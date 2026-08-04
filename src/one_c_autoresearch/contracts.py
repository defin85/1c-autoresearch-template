from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
import unicodedata
from collections.abc import Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, TypeAlias, TypeGuard, runtime_checkable

from .platform_support import lock_fd, unlock_fd


SCHEMA_VERSION = "1"
ROLES = ("vendor_baseline", "target_cf", "next_vendor")
DECISIONS = ("adopt_vendor", "adapt", "retain_custom", "out_of_scope")
MRQ_STATES = ("draft", "ready_for_review", "approved", "superseded")
SECRET_KEYS = re.compile(r"(?:password|passwd|pwd|token|secret|private[_-]?key)", re.I)
SAFE_SECRET_LIKE_KEYS = {"input_context_tokens"}
_PROCESS_REPOSITORY_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_REPOSITORY_LOCKS_GUARD = threading.Lock()
class _HeldRepositoryLocks(threading.local):
    def __init__(self) -> None:
        self.identities: set[str] = set()


_HELD_REPOSITORY_LOCKS = _HeldRepositoryLocks()
JsonValue: TypeAlias = None | bool | int | float | str | Sequence["JsonValue"] | Mapping[str, "JsonValue"]


def _is_tuple(value: object) -> TypeGuard[tuple[object, ...]]:
    return isinstance(value, tuple)


def _owned_value(module_name: str, name: str) -> object:
    module = importlib.import_module(module_name, __package__)
    namespace: dict[str, object] = module.__dict__
    return namespace.get(name)


def _owned_call(module_name: str, name: str, *args: object, **kwargs: object) -> object:
    value = _owned_value(module_name, name)
    if not callable(value):
        raise RuntimeError(f"{module_name}.{name} is unavailable")
    return value(*args, **kwargs)


def workflow_state_fingerprint(repo: Path) -> str:
    value = _owned_call(".workflow", "state_fingerprint", repo)
    if not isinstance(value, str):
        raise RuntimeError("workflow state fingerprint is invalid")
    return value


def recover_stage_publication(repo: Path) -> None:
    value = _owned_call(".stage_recompute", "recover_active_publication", repo)
    if value is not None and not isinstance(value, str):
        raise RuntimeError("stage publication recovery result is invalid")


def stage_active_pointers(repo: Path) -> dict[str, JsonValue]:
    return json_object(_owned_call(".stage_recompute", "active_pointers", repo))


def stage_active_state(repo: Path) -> tuple[dict[str, JsonValue], str]:
    value = _owned_call(".stage_recompute", "active_state", repo)
    if not _is_tuple(value):
        raise RuntimeError("stage recompute active state is invalid")
    items: tuple[object, ...] = value
    if len(items) != 2 or not isinstance(items[1], str):
        raise RuntimeError("stage recompute active state is invalid")
    return json_object(items[0]), items[1]


def stage_compatibility_fingerprint(
    mrq: Mapping[str, object],
    dispositions: Iterable[Mapping[str, object]],
    diff_facts: Mapping[str, Mapping[str, object]],
    coverage: Mapping[str, Mapping[str, object]],
    approvals: Iterable[Mapping[str, object]],
) -> str:
    value = _owned_call(
        ".stage_recompute", "compatibility_fingerprint",
        mrq, dispositions, diff_facts, coverage, approvals,
    )
    if not isinstance(value, str):
        raise RuntimeError("compatibility fingerprint is invalid")
    return value


def validate_source_search_profile(value: object) -> None:
    result = _owned_call(".source_search", "validate_profile_policy", value)
    if not isinstance(result, dict):
        raise RuntimeError("source-search profile policy is invalid")


def source_search_reserve_bytes(policy: Mapping[str, object]) -> int:
    value = _owned_call(".source_search", "dynamic_reserve_bytes", policy)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError("source-search reserve is invalid")
    return value


def source_search_policy_version() -> str:
    value = _owned_value(".source_search", "POLICY_VERSION")
    if not isinstance(value, str):
        raise RuntimeError("source-search policy version is unavailable")
    return value


def resolve_source_search_policy(
    repo: Path,
    profile: Mapping[str, object],
    role_id: str,
    work_unit: Mapping[str, object],
) -> dict[str, JsonValue] | None:
    value = _owned_call(".source_search", "resolve_policy", repo, profile, role_id, work_unit)
    return None if value is None else json_object(value)


def validate_decision_generation(repo: Path, generation_id: str, allowed_mrq_ids: set[str]) -> None:
    value = _owned_call(
        ".decision_generations", "validate_generation", repo, generation_id,
        allowed_mrq_ids=allowed_mrq_ids,
    )
    if not isinstance(value, dict):
        raise RuntimeError("decision generation validation result is invalid")


def decision_consolidation_fingerprint(pointer: Mapping[str, object]) -> str:
    value = _owned_call(".decision_generations", "consolidation_input_fingerprint", pointer)
    if not isinstance(value, str):
        raise RuntimeError("decision input fingerprint is invalid")
    return value


def attach_workflow_dispatcher(snapshot: Mapping[str, object], repo: Path, base: Path) -> dict[str, object]:
    value = _owned_call(".workflow", "attach_dispatcher", snapshot, repo, base)
    if not _is_dict(value):
        raise RuntimeError("workflow dispatcher projection is invalid")
    if not all(isinstance(key, str) for key in value):
        raise RuntimeError("workflow dispatcher projection keys are invalid")
    return {str(key): item for key, item in value.items()}


@runtime_checkable
class _JsonDecoder(Protocol):
    def decode(self, text: str) -> object: ...


def _decode(decoder: object, text: str) -> object:
    if not isinstance(decoder, _JsonDecoder):
        raise RuntimeError("invalid JSON decoder")
    return decoder.decode(text)


def _opaque(value: object) -> object:
    return value


def _is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def parse_json(text: str) -> JsonValue:
    return _json_value(_decode(_opaque(json.JSONDecoder()), text))


def parse_json_object(text: str) -> dict[str, JsonValue]:
    return json_object(parse_json(text))


def json_object(value: object) -> dict[str, JsonValue]:
    value = _json_value(value)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def json_array(value: object) -> list[JsonValue]:
    value = _json_value(value)
    if not isinstance(value, list):
        raise ValueError("expected a JSON array")
    return value


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if _is_list(value):
        return [_json_value(item) for item in value]
    if _is_dict(value):
        items = list(value.items())
        if not all(isinstance(key, str) for key, _child in items):
            raise ValueError("JSON object keys must be strings")
        return {str(key): _json_value(child) for key, child in items}
    raise ValueError(f"unsupported JSON value: {type(value).__name__}")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def content_id(prefix: str, preimage: Mapping[str, object]) -> str:
    return prefix + sha256(canonical_json(preimage))[:16].upper()


def external_id(kind: str, semantic_key: str, schema_version: str = SCHEMA_VERSION) -> str:
    return content_id("EXT-", {"kind": kind, "schema_version": schema_version, "semantic_key": _nfc(semantic_key)})


def mrq_id(semantic_key: str, schema_version: str = SCHEMA_VERSION) -> str:
    return content_id("MRQ-", {"schema_version": schema_version, "semantic_key": _nfc(semantic_key)})


def comparison_id(kind: str, before: str, after: str, profile: str, representation: str, normalizer: str, schema_version: str = SCHEMA_VERSION) -> str:
    return content_id("CMP-", {
        "acquisition_profile_id": profile,
        "after_role": after,
        "before_role": before,
        "comparison_kind": kind,
        "normalizer_version": normalizer,
        "representation_schema": representation,
        "schema_version": schema_version,
    })


def diff_id(comparison: str, path: str, change_type: str, schema_version: str = SCHEMA_VERSION) -> str:
    return content_id("DIF-", {"change_type": change_type, "comparison_id": comparison, "path": normalize_relative(path), "schema_version": schema_version})


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def normalize_relative(value: str) -> str:
    path = _nfc(value.replace("\\", "/"))
    if not path or path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError(f"unsafe relative path: {value!r}")
    return path


def confined(root: Path, value: str | Path) -> Path:
    root = root.resolve()
    candidate = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes repository: {value}")
    return candidate


def file_manifest(root: Path) -> list[dict[str, str | int]]:
    root = root.resolve()
    rows: list[dict[str, str | int]] = []
    inode_seen: set[tuple[int, int]] = set()
    for path in sorted(root.rglob("*"), key=lambda item: _nfc(item.relative_to(root).as_posix())):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode) or path.is_symlink():
            raise ValueError(f"unsupported source file type: {path}")
        inode = (path.stat().st_dev, path.stat().st_ino)
        if inode in inode_seen or path.stat().st_nlink != 1:
            raise ValueError(f"hardlink is forbidden: {path}")
        inode_seen.add(inode)
        relative = normalize_relative(path.relative_to(root).as_posix())
        payload = path.read_bytes()
        rows.append({"path": relative, "sha256": sha256(payload), "size_bytes": len(payload)})
    return rows


def tree_fingerprint(root: Path) -> str:
    return "sha256:" + sha256(canonical_json(file_manifest(root)))


def require_tracked_clean(repo: Path, paths: list[Path]) -> None:
    repo = repo.resolve()
    required: set[str] = set()
    selectors: list[str] = []
    for path in paths:
        path = confined(repo, path)
        selectors.append(path.relative_to(repo).as_posix())
        candidates = [path] if path.is_file() or path.is_symlink() else list(path.rglob("*"))
        for candidate in candidates:
            if candidate.is_dir():
                continue
            mode = candidate.lstat().st_mode
            if not stat.S_ISREG(mode) or candidate.is_symlink() or candidate.stat().st_nlink != 1:
                raise ValueError(f"canonical path has unsupported file type: {candidate.relative_to(repo)}")
            required.add(candidate.relative_to(repo).as_posix())
    selected = sorted(required)
    path_input = ("\0".join(selected) + "\0").encode()
    tracked = set(subprocess.run(["git", "ls-files", "-z"], cwd=repo, stdout=subprocess.PIPE, check=True).stdout.decode().split("\0"))
    if not required <= tracked:
        raise ValueError(f"canonical path is not tracked: {sorted(required - tracked)[0]}")
    ignored = subprocess.run(["git", "check-ignore", "--no-index", "--stdin", "-z"], cwd=repo, input=path_input, stdout=subprocess.PIPE, check=False).stdout.decode().split("\0")
    if any(ignored):
        raise ValueError(f"canonical path is ignored: {next(item for item in ignored if item)}")
    dirty = subprocess.run(["git", "status", "--porcelain=v1", "--", *selectors], cwd=repo, stdout=subprocess.PIPE, check=True, text=True).stdout
    if dirty:
        raise ValueError("canonical paths are dirty relative to HEAD")


def reject_secrets(value: JsonValue, location: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() not in SAFE_SECRET_LIKE_KEYS and SECRET_KEYS.search(str(key)) and child not in (None, "", [], {}):
                raise ValueError(f"secret value is forbidden at {location}.{key}")
            reject_secrets(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_secrets(child, f"{location}[{index}]")


def validate_unique_ids(items: list[dict[str, JsonValue]], id_key: str, preimage_key: str) -> None:
    by_id: dict[str, bytes] = {}
    by_preimage: dict[bytes, str] = {}
    for item in items:
        identifier = str(item[id_key])
        preimage = canonical_json(item[preimage_key])
        if identifier in by_id and by_id[identifier] != preimage:
            raise ValueError(f"truncated hash collision: {identifier}")
        if preimage in by_preimage and by_preimage[preimage] != identifier:
            raise ValueError(f"one preimage has multiple IDs: {identifier}")
        by_id[identifier] = preimage
        by_preimage[preimage] = identifier


@contextmanager
def repository_lock(repo: Path, timeout_seconds: float = 0) -> Generator[None, None, None]:
    identity = sha256(str(repo.resolve()).encode())
    with _PROCESS_REPOSITORY_LOCKS_GUARD:
        process_lock = _PROCESS_REPOSITORY_LOCKS.setdefault(identity, threading.RLock())
    with process_lock:
        held = _HELD_REPOSITORY_LOCKS.identities
        if identity in held:
            raise RuntimeError("repository writer is busy")
        lock_dir = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())) / "one-c-autoresearch-locks"
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(lock_dir / f"{identity}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                lock_fd(fd, nonblocking=timeout_seconds == 0)
            except BlockingIOError as exc:
                raise RuntimeError("repository writer is busy") from exc
            _HELD_REPOSITORY_LOCKS.identities = {*held, identity}
            try:
                yield
            finally:
                _HELD_REPOSITORY_LOCKS.identities = held
        finally:
            unlock_fd(fd)
            os.close(fd)


def atomic_json(path: Path, value: object) -> None:
    atomic_bytes(path, canonical_json(value) + b"\n")


def atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            _ = stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
