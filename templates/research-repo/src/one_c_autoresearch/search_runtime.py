from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Generator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Protocol, TypedDict

from .platform_support import clone_file, current_uid, process_group, terminate_process_identity


SCHEMA_VERSION = "search-runtime/v1"
PROJECT_RESIDENT_LIMIT = 4
SERVICE_RESIDENT_LIMIT = 8
PROJECT_CALL_LIMIT = 4
SERVICE_CALL_LIMIT = 8
MAX_IDLE_TTL_SECONDS = 300
ORPHAN_GRACE_SECONDS = 30
BROKER_START_GRACE_SECONDS = 0.5
ADDRESS_SPACE_BYTES = 16 * 1024**3
TERM_GRACE_SECONDS = 2

class ProcessIdentity(TypedDict):
    pid: int
    process_group: int
    start_time: str
    boot_id: str


class DaemonSpec(TypedDict):
    command: list[str]
    environment: dict[str, str]
    start_new_session: bool
    address_space_bytes: int


class ProxySpec(TypedDict):
    command: list[str]
    environment: dict[str, str]
    auto_launch: bool
    stdio_fallback: bool
    expected_backend_pid: int


class BrokerSpecs(TypedDict):
    daemon: DaemonSpec
    proxy: ProxySpec


class Closable(Protocol):
    def close(self) -> object: ...


class BackendEntry(TypedDict):
    process: subprocess.Popen[bytes]
    identity: ProcessIdentity
    project: str
    target: str
    state: str
    admission_open: bool
    last_traffic_at: float
    specs: BrokerSpecs
    promoted_source: str
    promoted_digest: str
    serving_root: str
    sessions: int
    owned_resource: Closable | None


_BACKENDS: dict[str, BackendEntry] = {}
_BACKENDS_LOCK = threading.Lock()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("search_runtime.symlink_forbidden")
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative)
        if path.is_file():
            digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def private_environment(
    serving: Path, runtime: Path, inherited: Mapping[str, str] | None = None
) -> dict[str, str]:
    serving = serving.resolve()
    runtime = runtime.resolve()
    for path in (serving, runtime):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    source = inherited or os.environ
    env = {
        key: source[key]
        for key in (
            "PATH", "LANG", "LC_ALL", "EMBEDDING_URL", "EMBEDDING_API_KEY",
            "EMBEDDING_MODEL", "EMBEDDING_DIM", "EMBEDDING_DIMENSION",
            "ONE_C_EMBEDDING_IDENTITY",
        )
        if key in source
    }
    env.update(
        {
            "HOME": str(serving),
            "XDG_CONFIG_HOME": str(serving / "config"),
            "XDG_DATA_HOME": str(serving / "data"),
            "XDG_CACHE_HOME": str(serving / "cache"),
            "XDG_STATE_HOME": str(serving / "state"),
            "XDG_RUNTIME_DIR": str(runtime),
        }
    )
    for path in (serving / name for name in ("config", "data", "cache", "state")):
        path.mkdir(mode=0o700, exist_ok=True)
    return env


def broker_specs(
    executable: Path,
    source_dir: Path,
    backend_pid: int,
    environment: Mapping[str, str],
    *,
    idle_ttl_seconds: int = MAX_IDLE_TTL_SECONDS,
) -> BrokerSpecs:
    if not 0 < idle_ttl_seconds <= MAX_IDLE_TTL_SECONDS or backend_pid <= 0:
        raise ValueError("search_runtime.invalid_broker_limits")
    common = [
        str(executable),
        "mcp",
        "serve",
        "--profile",
        "workspace",
        "--source-dir",
        str(source_dir),
    ]
    daemon_env = dict(environment)
    daemon_env["BSL_MCP_IDLE_TTL_SECS"] = str(idle_ttl_seconds)
    daemon_env["BSL_MCP_ORPHAN_GRACE_SECS"] = str(ORPHAN_GRACE_SECONDS)
    return {
        "daemon": {
            "command": common + ["--mode", "daemon"],
            "environment": daemon_env,
            "start_new_session": True,
            "address_space_bytes": ADDRESS_SPACE_BYTES,
        },
        "proxy": {
            "command": common
            + ["--mode", "broker-required", "--backend-pid", str(backend_pid)],
            "environment": dict(environment),
            "auto_launch": False,
            "stdio_fallback": False,
            "expected_backend_pid": backend_pid,
        },
    }


def create_serving_workspace(
    promoted: Path, serving_root: Path, serve_identity: str
) -> tuple[Path, str]:
    if promoted.is_symlink() or not promoted.is_dir():
        raise ValueError("search_runtime.invalid_promoted_instance")
    before = _tree_digest(promoted)
    serving = serving_root / serve_identity
    if serving.exists():
        raise ValueError("search_runtime.serving_identity_exists")
    serving_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    serving.mkdir(mode=0o700)
    try:
        for source in sorted(promoted.rglob("*")):
            relative = source.relative_to(promoted)
            target = serving / relative
            if source.is_dir():
                target.mkdir(mode=0o700)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with source.open("rb") as input_stream, target.open("xb") as output_stream:
                if not clone_file(input_stream, output_stream):
                    # ponytail: full private copy is the safe fallback on filesystems
                    # without FICLONE; expose storage pressure through the project quota.
                    _ = input_stream.seek(0)
                    shutil.copyfileobj(input_stream, output_stream)
            target.chmod(0o600)
    except BaseException:
        shutil.rmtree(serving)
        raise
    for directory in [serving, *[path for path in serving.rglob("*") if path.is_dir()]]:
        directory.chmod(0o700)
    for file in (path for path in serving.rglob("*") if path.is_file()):
        file.chmod(0o600)
    if _tree_digest(promoted) != before:
        shutil.rmtree(serving)
        raise RuntimeError("search_runtime.promoted_instance_changed")
    return serving, before


def verify_promoted_unchanged(promoted: Path, expected_digest: str) -> None:
    if _tree_digest(promoted) != expected_digest:
        raise RuntimeError("search_runtime.promoted_instance_changed")


def process_identity(pid: int) -> ProcessIdentity:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
        return {
            "pid": pid,
            "process_group": process_group(pid),
            "start_time": fields[19],
            "boot_id": Path("/proc/sys/kernel/random/boot_id")
            .read_text(encoding="utf-8")
            .strip(),
        }
    except (OSError, IndexError) as exc:
        raise RuntimeError("search_runtime.process_identity_unavailable") from exc


def process_identity_alive(identity: ProcessIdentity) -> bool:
    try:
        return process_identity(int(identity["pid"])) == dict(identity)
    except (KeyError, TypeError, ValueError, RuntimeError):
        return False


def launch_backend(spec: DaemonSpec) -> tuple[subprocess.Popen[bytes], ProcessIdentity]:
    ceiling = spec["address_space_bytes"]
    limiter = shutil.which("prlimit")
    if not limiter:
        raise RuntimeError("search_runtime.prlimit_unavailable")
    process = subprocess.Popen(
        [limiter, f"--as={ceiling}", "--", *list(spec["command"])],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=dict(spec["environment"]),
        start_new_session=True,
    )
    return process, process_identity(process.pid)


def _backend_key(
    executable: Path, source_dir: Path, environment: Mapping[str, str]
) -> str:
    identity = {
        "executable": str(executable.resolve()),
        "source_dir": str(source_dir.resolve()),
        "embedding": {
            key: environment.get(key, "")
            for key in (
                "ONE_C_EMBEDDING_IDENTITY", "EMBEDDING_MODEL", "EMBEDDING_DIM",
            )
        },
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@contextmanager
def supervised_workspace_proxy(
    executable: Path,
    promoted_source: Path,
    environment: Mapping[str, str],
    *,
    timeout_seconds: float,
    serving_copy: bool = True,
    owned_resource: Closable | None = None,
) -> Generator[ProxySpec, None, None]:
    """Return one required-broker proxy spec backed by a supervised shared daemon."""
    if not promoted_source.is_dir() or promoted_source.is_symlink():
        raise ValueError("search_runtime.invalid_promoted_instance")
    key = _backend_key(executable, promoted_source, environment)
    if not serving_copy:
        key += "-" + uuid.uuid4().hex
    target_root = (
        promoted_source.parents[2]
        if promoted_source.parent.parent.name == "instances"
        else promoted_source.parent
    )
    runtime_root = target_root / "runtime" / key
    socket_base = Path(
        os.environ.get("XDG_RUNTIME_DIR", f"/tmp/one-c-autoresearch-{current_uid() or 0}")
    )
    socket_root = socket_base / "one-c-autoresearch" / key[:16]
    deadline = time.monotonic() + timeout_seconds
    project = target_root.parent.parent.name
    with ExitStack() as admission:
        admission.enter_context(_ADMISSION.resident(project, key, deadline))
        admission.enter_context(_ADMISSION.call(project, deadline))
        with _BACKENDS_LOCK:
            for other_key, other in _BACKENDS.items():
                if (
                    other_key != key
                    and other.get("project") == project
                    and other.get("target") == str(promoted_source.resolve())
                ):
                    other["state"] = "superseded"
                    other["admission_open"] = False
            entry: BackendEntry | None = _BACKENDS.get(key)
            if entry and not process_identity_alive(entry["identity"]):
                resource = entry["owned_resource"]
                if resource is not None:
                    _ = resource.close()
                verify_promoted_unchanged(
                    Path(entry["promoted_source"]), entry["promoted_digest"]
                )
                shutil.rmtree(Path(entry["serving_root"]), ignore_errors=True)
                _ = _BACKENDS.pop(key, None)
                entry = None
            if entry is None:
                if serving_copy:
                    serving_root = runtime_root / "serving"
                    promoted_instance = promoted_source.parent
                    serving_instance, promoted_digest = create_serving_workspace(
                        promoted_instance, serving_root, "instance"
                    )
                    serving_source = serving_instance / "source"
                    home = serving_instance / "home"
                else:
                    serving_source = promoted_source
                    promoted_digest = _tree_digest(promoted_source)
                    home = runtime_root / "home"
                daemon_environment = private_environment(
                    home, socket_root, inherited=environment
                )
                provisional = broker_specs(
                    executable, serving_source, os.getpid(), daemon_environment
                )
                try:
                    process, identity = launch_backend(provisional["daemon"])
                    # The installed analyzer does not retry broker-required while
                    # the freshly launched daemon is still creating its socket.
                    time.sleep(BROKER_START_GRACE_SECONDS)
                    if process.poll() is not None:
                        raise RuntimeError("search_runtime.backend_start_failed")
                except BaseException:
                    if owned_resource is not None:
                        _ = owned_resource.close()
                    raise
                specs = broker_specs(
                    executable, serving_source, process.pid, daemon_environment
                )
                entry = BackendEntry(
                    process=process,
                    identity=identity,
                    project=project,
                    target=str(promoted_source.resolve()),
                    state="warm",
                    admission_open=True,
                    last_traffic_at=time.monotonic(),
                    specs=specs,
                    promoted_source=str(
                        promoted_source.parent if serving_copy else promoted_source
                    ),
                    promoted_digest=promoted_digest,
                    serving_root=str(runtime_root),
                    sessions=0,
                    owned_resource=owned_resource,
                )
                _BACKENDS[key] = entry
            elif owned_resource is not None:
                _ = owned_resource.close()
            entry["sessions"] += 1
            entry["state"] = "warm"
            entry["last_traffic_at"] = time.monotonic()
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError("search_runtime.backend_start_deadline")
            yield entry["specs"]["proxy"]
        finally:
            drained: BackendEntry | None = None
            with _BACKENDS_LOCK:
                current = _BACKENDS.get(key)
                if current:
                    current["sessions"] = max(0, int(current["sessions"]) - 1)
                    current["last_traffic_at"] = time.monotonic()
                    if current["sessions"] == 0:
                        current["state"] = (
                            "superseded"
                            if not current.get("admission_open", True)
                            else "idle"
                        )
                    if current["sessions"] == 0 and (
                        not serving_copy or not current.get("admission_open", True)
                    ):
                        _ = _BACKENDS.pop(key, None)
                        drained = current
            if drained:
                _ = terminate_process_group(drained["identity"])
                resource = drained["owned_resource"]
                if resource is not None:
                    _ = resource.close()
                shutil.rmtree(runtime_root, ignore_errors=True)
                shutil.rmtree(socket_root, ignore_errors=True)


def terminate_process_group(
    identity: ProcessIdentity, *, grace_seconds: float = TERM_GRACE_SECONDS
) -> str:
    if not process_identity_alive(identity):
        return "identity_mismatch"
    group = int(identity["process_group"])
    terminate_process_identity(int(identity["pid"]), group)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not process_identity_alive(identity):
            return "terminated"
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))
    if process_identity_alive(identity):
        terminate_process_identity(int(identity["pid"]), group, force=True)
    return "killed"


class Admission:
    def __init__(self) -> None:
        self._condition: threading.Condition = threading.Condition()
        self._residents: dict[tuple[str, str], int] = {}
        self._calls: dict[str, int] = {}

    def _wait(self, allowed: Callable[[], bool], deadline: float) -> None:
        while not allowed():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("search_runtime.admission_deadline")
            _ = self._condition.wait(remaining)

    @contextmanager
    def resident(self, project: str, target: str, deadline: float) -> Generator[None, None, None]:
        key = (project, target)
        with self._condition:
            if any(existing_target == target and existing_project != project for existing_project, existing_target in self._residents):
                raise ValueError("search_runtime.cross_project_reuse_forbidden")
            self._wait(
                lambda: key in self._residents
                or (
                    sum(project_id == project for project_id, _ in self._residents)
                    < PROJECT_RESIDENT_LIMIT
                    and len(self._residents) < SERVICE_RESIDENT_LIMIT
                ),
                deadline,
            )
            self._residents[key] = self._residents.get(key, 0) + 1
        try:
            yield
        finally:
            with self._condition:
                remaining = self._residents[key] - 1
                if remaining:
                    self._residents[key] = remaining
                else:
                    del self._residents[key]
                _ = self._condition.notify_all()

    @contextmanager
    def call(self, project: str, deadline: float) -> Generator[None, None, None]:
        with self._condition:
            self._wait(
                lambda: self._calls.get(project, 0) < PROJECT_CALL_LIMIT
                and sum(self._calls.values()) < SERVICE_CALL_LIMIT,
                deadline,
            )
            self._calls[project] = self._calls.get(project, 0) + 1
        try:
            yield
        finally:
            with self._condition:
                self._calls[project] -= 1
                if not self._calls[project]:
                    del self._calls[project]
                _ = self._condition.notify_all()


_ADMISSION = Admission()


class Session:
    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self.state: str = "active"
        self.result: object | None = None

    def settle(self, result: object) -> bool:
        with self._lock:
            if self.state != "active":
                return False
            self.state = "settled"
            self.result = result
            return True

    def cancel(self) -> bool:
        with self._lock:
            if self.state != "active":
                return False
            self.state = "cancelled"
            return True


class BackendLifecycle:
    def __init__(
        self,
        target_fingerprint: str,
        *,
        started_at: float,
        idle_ttl_seconds: int = MAX_IDLE_TTL_SECONDS,
    ) -> None:
        if not 0 < idle_ttl_seconds <= MAX_IDLE_TTL_SECONDS:
            raise ValueError("search_runtime.invalid_idle_ttl")
        self.target_fingerprint: str = target_fingerprint
        self.started_at: float = started_at
        self.last_traffic_at: float | None = None
        self.idle_ttl_seconds: int = idle_ttl_seconds
        self.active_sessions: int = 0
        self.state: str = "warm"
        self.admission_open: bool = True
        self.terminal_cause: str | None = None

    def traffic(self, now: float) -> None:
        if not self.admission_open:
            raise RuntimeError("search_runtime.target_admission_closed")
        self.last_traffic_at = now
        self.state = "warm"

    def session_opened(self) -> None:
        if not self.admission_open:
            raise RuntimeError("search_runtime.target_admission_closed")
        self.active_sessions += 1

    def session_closed(self) -> None:
        if self.active_sessions <= 0:
            raise RuntimeError("search_runtime.session_underflow")
        self.active_sessions -= 1

    def observe(self, now: float) -> str:
        if not self.admission_open:
            return self.state
        if self.last_traffic_at is None:
            if now - self.started_at >= ORPHAN_GRACE_SECONDS:
                self.state = "orphan_expired"
                self.admission_open = False
                self.terminal_cause = "orphan_grace"
        elif self.active_sessions == 0:
            if now - self.last_traffic_at >= self.idle_ttl_seconds:
                self.state = "idle_expired"
                self.admission_open = False
                self.terminal_cause = "idle_ttl"
            else:
                self.state = "idle"
        return self.state

    def supersede(self) -> None:
        self.admission_open = False
        self.state = "superseded"
        self.terminal_cause = "target_superseded"

    def diagnostic(self) -> dict[str, object]:
        return {
            "target_fingerprint": self.target_fingerprint,
            "state": self.state,
            "admission_open": self.admission_open,
            "active_sessions": self.active_sessions,
            "terminal_cause": self.terminal_cause,
        }


def quarantine_path(path: Path, quarantine_root: Path, cause: str) -> Path | None:
    if not path.exists():
        return None
    quarantine_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = quarantine_root / f"{path.name}-{cause}-{uuid.uuid4().hex}"
    _ = path.replace(destination)
    return destination


def reconcile_restart(
    target_root: Path, recorded_identity: ProcessIdentity | None
) -> dict[str, object]:
    terminal = "not_running"
    if recorded_identity:
        terminal = terminate_process_group(recorded_identity)
    quarantined: list[str] = []
    quarantine = target_root / "quarantine"
    for parent_name in ("staging", "serving"):
        parent = target_root / parent_name
        if not parent.is_dir():
            continue
        for child in list(parent.iterdir()):
            moved = quarantine_path(child, quarantine, "restart")
            if moved:
                quarantined.append(str(moved))
    return {
        "schema_version": SCHEMA_VERSION,
        "terminal_cause": terminal,
        "quarantined": quarantined,
        "replayed_requests": 0,
        "promoted_instances": 0,
    }


def runtime_diagnostics() -> list[dict[str, object]]:
    with _BACKENDS_LOCK:
        return [
            {
                "target_identity_prefix": key[:16],
                "state": (
                    (
                        "idle_expired"
                        if entry.get("state") == "idle"
                        and time.monotonic() - float(entry["last_traffic_at"])
                        >= MAX_IDLE_TTL_SECONDS
                        else entry.get("state", "warm")
                    )
                    if process_identity_alive(entry["identity"])
                    else "failed"
                ),
                "backend_pid": entry["identity"]["pid"],
                "active_sessions": entry["sessions"],
                "transport": "broker-required",
                "auto_launch": False,
                "stdio_fallback": False,
            }
            for key, entry in sorted(_BACKENDS.items())
        ]


def shutdown_project_backends(project: str) -> int:
    with _BACKENDS_LOCK:
        selected = [
            (key, entry)
            for key, entry in _BACKENDS.items()
            if entry.get("project") == project
        ]
        for key, _entry in selected:
            _ = _BACKENDS.pop(key, None)
    for _key, entry in selected:
        _ = terminate_process_group(entry["identity"])
        resource = entry["owned_resource"]
        if resource is not None:
            _ = resource.close()
        shutil.rmtree(Path(entry["serving_root"]), ignore_errors=True)
    return len(selected)
