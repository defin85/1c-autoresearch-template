from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .common import file_sha256, read_toml, utc_now_iso, write_json


SCHEMA_VERSION = 1
MAX_LIVE_LOG_LINES = 500
MAX_EVENT_PAYLOAD = 64 * 1024
EVENT_RETENTION = 10_000
ALLOWED_CONNECTIONS = {"mcp", "web", "postgresql"}
ALLOWED_PROVIDERS = {"codex", "claude"}
SECRET_RE = re.compile(r"(?i)(token|password|secret|api[_-]?key)\s*[:=]\s*([^\s,;]+)")


STAGES: tuple[dict[str, Any], ...] = (
    {"id": "intake", "title": "Project intake", "kind": "deterministic", "dependencies": [], "operation": "doctor", "mutation": "read"},
    {"id": "source-normalization", "title": "Source normalization", "kind": "deterministic", "dependencies": ["intake"], "operation": "configuration-source-validate", "mutation": "read"},
    {"id": "physical-cleanup", "title": "Physical clean comparison", "kind": "agent", "dependencies": ["source-normalization"], "operation": "manual-cleanup", "mutation": "write"},
    {"id": "diff-inventory", "title": "Diff inventory", "kind": "agent", "dependencies": ["physical-cleanup"], "operation": "parallel-research", "mutation": "write"},
    {"id": "feature-research", "title": "Feature and requirement research", "kind": "agent", "dependencies": ["diff-inventory"], "operation": "parallel-research", "mutation": "write"},
    {"id": "reverse-map", "title": "Reverse functional map", "kind": "agent", "dependencies": ["feature-research"], "operation": "reverse-map", "mutation": "write"},
    {"id": "final-gate", "title": "Final gate", "kind": "deterministic", "dependencies": ["reverse-map"], "operation": "final-gate", "mutation": "write"},
    {"id": "subject-cards", "title": "Subject cards", "kind": "agent", "dependencies": ["final-gate"], "operation": "subject-cards", "mutation": "write"},
    {"id": "functional-gaps", "title": "Functional gaps", "kind": "agent", "dependencies": ["subject-cards"], "operation": "functional-gaps", "mutation": "write"},
    {"id": "review", "title": "Review preparation", "kind": "agent", "dependencies": ["functional-gaps"], "operation": "review", "mutation": "write"},
    {"id": "results", "title": "Results and dashboards", "kind": "deterministic", "dependencies": ["review"], "operation": "results", "mutation": "write"},
    {"id": "verification", "title": "Strict verification", "kind": "deterministic", "dependencies": ["results"], "operation": "doctor-strict", "mutation": "read"},
)
STAGE_BY_ID = {stage["id"]: stage for stage in STAGES}


def redact(value: str) -> str:
    return SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)


def canonical_under(path: Path, roots: Iterable[Path]) -> Path:
    resolved = path.expanduser().resolve()
    for root in roots:
        try:
            resolved.relative_to(root.expanduser().resolve())
            return resolved
        except ValueError:
            pass
    raise ValueError(f"path is outside approved roots: {resolved}")


def atomic_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        tmp.unlink(missing_ok=True)


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path

    @classmethod
    def default(cls) -> "WorkspacePaths":
        configured = os.environ.get("ONE_C_AUTORESEARCH_WORKSPACE_HOME")
        root = Path(configured).expanduser() if configured else Path.home() / ".local" / "share" / "one-c-autoresearch" / "workspace"
        return cls(root.resolve())

    @property
    def database(self) -> Path:
        return self.root / "workspace.sqlite3"

    @property
    def credentials(self) -> Path:
        return self.root / "credentials"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def lock(self) -> Path:
        return self.root / "service.lock"


class WorkspaceStore:
    def __init__(self, paths: WorkspacePaths):
        self.paths = paths
        paths.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        paths.root.chmod(0o700)
        self.db = sqlite3.connect(paths.database, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.RLock()
        self.migrate()

    def migrate(self) -> None:
        with self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
                INSERT INTO schema_version(version) SELECT 0 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
                CREATE TABLE IF NOT EXISTS projects(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL UNIQUE,
                    manifest_hash TEXT NOT NULL DEFAULT '', setup_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resources(
                    kind TEXT NOT NULL, id TEXT NOT NULL, project_id TEXT, data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(kind,id), FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, stage_id TEXT NOT NULL,
                    status TEXT NOT NULL, idempotency_key TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, pid INTEGER, process_marker TEXT, run_token TEXT NOT NULL,
                    started_at TEXT, finished_at TEXT, exit_code INTEGER, error TEXT NOT NULL DEFAULT '',
                    UNIQUE(project_id,idempotency_key), FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, run_id TEXT,
                    type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS approvals(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, stage_id TEXT NOT NULL,
                    status TEXT NOT NULL, idempotency_key TEXT NOT NULL, data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(project_id,idempotency_key), FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS events_project_id ON events(project_id,id);
                UPDATE schema_version SET version=1;
            """)

    def close(self) -> None:
        self.db.close()

    def project(self, project_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError(project_id)
        result = dict(row)
        result["setup"] = json.loads(result.pop("setup_json"))
        return result

    def projects(self) -> list[dict[str, Any]]:
        return [self.project(row["id"]) for row in self.db.execute("SELECT id FROM projects ORDER BY created_at")]

    def register_project(self, name: str, root: Path, approved_roots: Iterable[Path]) -> dict[str, Any]:
        resolved = canonical_under(root, approved_roots)
        manifest = resolved / "project.toml"
        if not manifest.is_file():
            raise ValueError(f"project.toml not found: {manifest}")
        project_id = str(read_toml(manifest).get("project", {}).get("id") or uuid.uuid4())
        now = utc_now_iso()
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO projects(id,name,root,manifest_hash,created_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,root=excluded.root,manifest_hash=excluded.manifest_hash",
                (project_id, name, str(resolved), file_sha256(manifest), now),
            )
        return self.project(project_id)

    def save_setup(self, project_id: str, setup: dict[str, Any]) -> dict[str, Any]:
        with self.lock, self.db:
            self.db.execute("UPDATE projects SET setup_json=? WHERE id=?", (json.dumps(setup), project_id))
        return self.project(project_id)

    def resources(self, kind: str, project_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM resources WHERE kind=?"
        params: list[Any] = [kind]
        if project_id:
            sql += " AND project_id=?"
            params.append(project_id)
        sql += " ORDER BY created_at"
        return [self._resource(row) for row in self.db.execute(sql, params)]

    def resource(self, kind: str, resource_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM resources WHERE kind=? AND id=?", (kind, resource_id)).fetchone()
        if not row:
            raise KeyError(resource_id)
        return self._resource(row)

    @staticmethod
    def _resource(row: sqlite3.Row) -> dict[str, Any]:
        value = json.loads(row["data_json"])
        value.update({"id": row["id"], "project_id": row["project_id"], "created_at": row["created_at"], "updated_at": row["updated_at"]})
        return value

    def save_resource(self, kind: str, data: dict[str, Any], project_id: str | None = None) -> dict[str, Any]:
        resource_id = str(data.get("id") or uuid.uuid4())
        now = utc_now_iso()
        payload = {key: value for key, value in data.items() if key not in {"id", "project_id", "created_at", "updated_at"}}
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO resources(kind,id,project_id,data_json,created_at,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(kind,id) DO UPDATE SET project_id=excluded.project_id,data_json=excluded.data_json,updated_at=excluded.updated_at",
                (kind, resource_id, project_id, json.dumps(payload), now, now),
            )
        return self.resource(kind, resource_id)

    def delete_resource(self, kind: str, resource_id: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM resources WHERE kind=? AND id=?", (kind, resource_id))

    def write_secret(self, secret_id: str, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", secret_id):
            raise ValueError("invalid secret id")
        atomic_private_text(self.paths.credentials / secret_id, value)
        return secret_id

    def secret(self, secret_id: str) -> str:
        path = canonical_under(self.paths.credentials / secret_id, [self.paths.credentials])
        return path.read_text(encoding="utf-8")

    def append_event(self, project_id: str, event_type: str, payload: dict[str, Any], run_id: str | None = None) -> int:
        raw = json.dumps(payload, ensure_ascii=False)
        if len(raw.encode()) > MAX_EVENT_PAYLOAD:
            raise ValueError("event payload too large")
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT INTO events(project_id,run_id,type,payload_json,created_at) VALUES(?,?,?,?,?)",
                (project_id, run_id, event_type, raw, utc_now_iso()),
            )
            cutoff = cursor.lastrowid - EVENT_RETENTION
            if cutoff > 0:
                self.db.execute("DELETE FROM events WHERE project_id=? AND id<=?", (project_id, cutoff))
        return int(cursor.lastrowid)

    def events(self, project_id: str, after: int = 0, limit: int = 500) -> tuple[list[dict[str, Any]], bool]:
        oldest = self.db.execute("SELECT MIN(id) AS id FROM events WHERE project_id=?", (project_id,)).fetchone()["id"]
        reset = bool(after and oldest and after < oldest - 1)
        rows = self.db.execute(
            "SELECT * FROM events WHERE project_id=? AND id>? ORDER BY id LIMIT ?", (project_id, after, min(limit, 1000))
        ).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows], reset

    def run(self, run_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        result = dict(row)
        result["snapshot"] = json.loads(result.pop("snapshot_json"))
        result["log_tail"] = self.log_tail(run_id)
        return result

    def runs(self, project_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT id FROM runs" + (" WHERE project_id=?" if project_id else "") + " ORDER BY rowid DESC", ((project_id,) if project_id else ()))
        return [self.run(row["id"]) for row in rows]

    def log_tail(self, run_id: str) -> list[str]:
        path = self.paths.runs / run_id / "run.log"
        if not path.exists():
            return []
        return [redact(line.rstrip("\n")) for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_LIVE_LOG_LINES:]]


def source_roots(project: dict[str, Any]) -> list[Path]:
    manifest = read_toml(Path(project["root"]) / "project.toml")
    values = manifest.get("paths", {})
    roots = [Path(project["root"])]
    for key in ("vendor_baseline", "target_cf", "target_cfe", "next_vendor", "compare_repo"):
        value = str(values.get(key) or "").strip()
        if value:
            candidate = Path(value)
            roots.append((candidate if candidate.is_absolute() else Path(project["root"]) / candidate).resolve())
    return roots


def workflow_snapshot(store: WorkspaceStore, project_id: str) -> dict[str, Any]:
    project = store.project(project_id)
    root = Path(project["root"])
    runs = store.runs(project_id)
    latest = {run["stage_id"]: run for run in runs if run["stage_id"] not in locals().get("latest", {})}
    stages = []
    completed: set[str] = set()
    artifact_map = {
        "physical-cleanup": "outputs/clean-comparison-dashboard/index.html",
        "functional-gaps": "outputs/functional-gap-dashboard/index.html",
        "results": "outputs/review/index.html",
    }
    for definition in STAGES:
        run = latest.get(definition["id"])
        status = run["status"] if run else "pending"
        if status == "completed":
            completed.add(definition["id"])
        blockers = [dep for dep in definition["dependencies"] if dep not in completed]
        artifact = artifact_map.get(definition["id"])
        stages.append(definition | {
            "status": status,
            "ready": not blockers,
            "blockers": blockers,
            "run_id": run["id"] if run else None,
            "artifact": artifact if artifact and (root / artifact).is_file() else None,
        })
    return {"project": project, "stages": stages, "generated_at": utc_now_iso()}


def agent_probe(provider: str) -> dict[str, Any]:
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError("unsupported provider")
    executable = shutil.which(provider)
    if not executable:
        return {"provider": provider, "available": False, "reason": "executable_not_found"}
    command = [executable, "--version"]
    result = subprocess.run(command, text=True, capture_output=True, timeout=10, check=False)
    return {"provider": provider, "available": result.returncode == 0, "version": redact((result.stdout or result.stderr).strip())}


def agent_command(provider: str, model: str, prompt: str, cwd: Path) -> list[str]:
    if provider == "codex":
        return ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "-m", model, "-C", str(cwd), prompt]
    if provider == "claude":
        return ["claude", "-p", "--output-format", "json", "--permission-mode", "plan", "--model", model, prompt]
    raise ValueError("unsupported provider")


def test_connection(profile: dict[str, Any], secret: str = "") -> dict[str, Any]:
    channel = profile.get("channel")
    if channel not in ALLOWED_CONNECTIONS:
        raise ValueError("unsupported connection channel")
    target = str(profile.get("url") or "")
    if channel == "postgresql":
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise ValueError("invalid PostgreSQL URL")
        return {"ok": True, "target": f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 5432}", "probe": "syntax_only"}
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid HTTP URL")
    headers = {"Accept": "application/json,text/html;q=0.5"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    request = urllib.request.Request(target, method="HEAD", headers=headers)
    opener = urllib.request.build_opener(urllib.request.HTTPHandler(), urllib.request.HTTPSHandler())
    try:
        response = opener.open(request, timeout=5)
        final = urllib.parse.urlsplit(response.geturl())
        if (final.scheme, final.hostname, final.port) != (parsed.scheme, parsed.hostname, parsed.port):
            raise ValueError("cross-origin redirect rejected")
        return {"ok": response.status < 500, "status": response.status, "target": target}
    except urllib.error.HTTPError as exc:
        return {"ok": exc.code < 500, "status": exc.code, "target": target}


def update_project_manifest(project_root: Path, updates: dict[str, dict[str, Any]], expected_hash: str) -> str:
    path = project_root / "project.toml"
    if file_sha256(path) != expected_hash:
        raise RuntimeError("stale project.toml fingerprint")
    lines = path.read_text(encoding="utf-8").splitlines()
    section = ""
    seen: set[tuple[str, str]] = set()
    output: list[str] = []
    for line in lines:
        match = re.fullmatch(r"\[([^]]+)\]\s*", line)
        if match:
            section = match.group(1)
        key_match = re.match(r"([A-Za-z0-9_-]+)\s*=", line)
        if key_match and section in updates and key_match.group(1) in updates[section]:
            key = key_match.group(1)
            value = updates[section][key]
            rendered = str(value).lower() if isinstance(value, bool) else json.dumps(value, ensure_ascii=False)
            output.append(f"{key} = {rendered}")
            seen.add((section, key))
        else:
            output.append(line)
    for section_name, values in updates.items():
        missing = [(key, value) for key, value in values.items() if (section_name, key) not in seen]
        if not missing:
            continue
        if f"[{section_name}]" not in output:
            output.extend(["", f"[{section_name}]"])
        for key, value in missing:
            rendered = str(value).lower() if isinstance(value, bool) else json.dumps(value, ensure_ascii=False)
            output.append(f"{key} = {rendered}")
    atomic_private_text(path, "\n".join(output) + "\n")
    path.chmod(0o644)
    return file_sha256(path)


class RunManager:
    def __init__(self, store: WorkspaceStore):
        self.store = store
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.lock = threading.RLock()
        self.reconcile()

    def _command(self, project: dict[str, Any], stage: dict[str, Any], payload: dict[str, Any]) -> list[str]:
        root = Path(project["root"])
        if payload.get("fake"):
            return [sys.executable, "-c", "import time; print('started',flush=True); time.sleep(.15); print('completed',flush=True)"]
        operation = stage["operation"]
        commands = {
            "doctor": [sys.executable, "-m", "one_c_autoresearch", "doctor", "--repo-path", str(root), "--json"],
            "configuration-source-validate": [sys.executable, "-m", "one_c_autoresearch", "configuration-source", "validate", "--repo-path", str(root)],
            "final-gate": [sys.executable, "-m", "one_c_autoresearch", "final-gate", "build", "--repo-path", str(root)],
            "results": [sys.executable, "-m", "one_c_autoresearch", "review-dashboard", "build", "--repo-path", str(root)],
            "doctor-strict": [sys.executable, "-m", "one_c_autoresearch", "doctor", "--repo-path", str(root), "--json", "--deep", "--strict"],
        }
        if operation in commands:
            return commands[operation]
        provider = str(payload.get("provider") or "")
        model = str(payload.get("model") or "")
        prompt = str(payload.get("prompt") or "")
        if stage["kind"] == "agent" and provider and model and prompt:
            return agent_command(provider, model, prompt, root)
        raise ValueError(f"stage operation requires configured agent execution: {stage['id']}")

    def start(self, project_id: str, stage_id: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        project = self.store.project(project_id)
        stage = STAGE_BY_ID.get(stage_id)
        if not stage:
            raise ValueError("unknown stage")
        existing = self.store.db.execute("SELECT id,payload_hash FROM runs WHERE project_id=? AND idempotency_key=?", (project_id, idempotency_key)).fetchone()
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise RuntimeError("idempotency key reused with different payload")
            return self.store.run(existing["id"])
        if stage["mutation"] == "write":
            active = self.store.db.execute("SELECT id FROM runs WHERE project_id=? AND status IN ('queued','running')", (project_id,)).fetchone()
            if active:
                raise RuntimeError(f"project has active run: {active['id']}")
        roots = source_roots(project)
        for candidate in payload.get("paths", []):
            canonical_under(Path(candidate), roots)
        command = self._command(project, stage, payload)
        run_id = str(uuid.uuid4())
        run_token = secrets.token_urlsafe(32)
        run_dir = self.store.paths.runs / run_id
        run_dir.mkdir(parents=True, mode=0o700)
        spec = {
            "run_id": run_id, "run_token": run_token, "command": command,
            "cwd": project["root"], "run_dir": str(run_dir),
        }
        write_json(run_dir / "spec.json", spec, mode=0o600)
        snapshot = {"stage": stage, "payload": payload, "manifest_hash": file_sha256(Path(project["root"]) / "project.toml")}
        with self.store.lock, self.store.db:
            self.store.db.execute(
                "INSERT INTO runs(id,project_id,stage_id,status,idempotency_key,payload_hash,snapshot_json,run_token) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, project_id, stage_id, "queued", idempotency_key, payload_hash, json.dumps(snapshot), run_token),
            )
        self.store.append_event(project_id, "run.queued", {"run_id": run_id, "stage_id": stage_id}, run_id)
        wrapper = [sys.executable, "-m", "one_c_autoresearch.workspace_runner", str(run_dir / "spec.json")]
        process = subprocess.Popen(wrapper, cwd=project["root"], text=True, start_new_session=os.name != "nt")
        marker = str(time.time_ns())
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE runs SET status='running',pid=?,process_marker=?,started_at=? WHERE id=?", (process.pid, marker, utc_now_iso(), run_id))
        self.processes[run_id] = process
        self.store.append_event(project_id, "run.started", {"run_id": run_id, "stage_id": stage_id}, run_id)
        threading.Thread(target=self._watch, args=(run_id,), daemon=True).start()
        return self.store.run(run_id)

    def _watch(self, run_id: str) -> None:
        run = self.store.run(run_id)
        run_dir = self.store.paths.runs / run_id
        result_path = run_dir / "result.json"
        process = self.processes.get(run_id)
        if process:
            process.wait()
        for _ in range(50):
            if result_path.exists():
                break
            time.sleep(0.05)
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {"exit_code": process.returncode if process else -1, "error": "result missing"}
        status = "completed" if result.get("exit_code") == 0 else "failed"
        error = redact(str(result.get("error") or ""))
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE runs SET status=?,finished_at=?,exit_code=?,error=? WHERE id=?", (status, utc_now_iso(), result.get("exit_code"), error, run_id))
        self.store.append_event(run["project_id"], f"run.{status}", {"run_id": run_id, "stage_id": run["stage_id"], "error": error}, run_id)
        self.processes.pop(run_id, None)

    def cancel(self, run_id: str) -> dict[str, Any]:
        run = self.store.run(run_id)
        process = self.processes.get(run_id)
        if run["status"] not in {"queued", "running"}:
            return run
        if process and process.poll() is None:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt": process.kill()
                else: os.killpg(process.pid, signal.SIGKILL)
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE runs SET status='cancelled',finished_at=? WHERE id=?", (utc_now_iso(), run_id))
        self.store.append_event(run["project_id"], "run.cancelled", {"run_id": run_id, "stage_id": run["stage_id"]}, run_id)
        return self.store.run(run_id)

    def reconcile(self) -> None:
        rows = self.store.db.execute("SELECT id,project_id,stage_id,run_token FROM runs WHERE status IN ('queued','running')").fetchall()
        for row in rows:
            run_dir = self.store.paths.runs / row["id"]
            result_path = run_dir / "result.json"
            heartbeat_path = run_dir / "heartbeat.json"
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if result.get("run_token") == row["run_token"]:
                    status = "completed" if result.get("exit_code") == 0 else "failed"
                    with self.store.db:
                        self.store.db.execute("UPDATE runs SET status=?,finished_at=?,exit_code=? WHERE id=?", (status, utc_now_iso(), result.get("exit_code"), row["id"]))
                    continue
            live = False
            if heartbeat_path.exists():
                heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
                pid = int(heartbeat.get("pid") or 0)
                try:
                    os.kill(pid, 0)
                    live = heartbeat.get("run_token") == row["run_token"] and time.time() - float(heartbeat.get("time") or 0) < 10
                except (OSError, ValueError):
                    live = False
            if not live:
                with self.store.db:
                    self.store.db.execute("UPDATE runs SET status='interrupted',finished_at=?,error='runner identity could not be proven' WHERE id=?", (utc_now_iso(), row["id"]))
                self.store.append_event(row["project_id"], "run.interrupted", {"run_id": row["id"], "stage_id": row["stage_id"]}, row["id"])
