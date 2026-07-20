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

from .common import file_sha256, path_sha256, read_toml, utc_now_iso, write_json


SCHEMA_VERSION = 3
MAX_LIVE_LOG_LINES = 500
MAX_EVENT_PAYLOAD = 64 * 1024
EVENT_RETENTION = 10_000
ALLOWED_CONNECTIONS = {"mcp", "web", "postgresql", "onec"}
ALLOWED_PROVIDERS = {"codex", "claude"}
SECRET_RE = re.compile(r"(?i)(token|password|secret|api[_-]?key)\s*[:=]\s*([^\s,;]+)")


STAGES: tuple[dict[str, Any], ...] = (
    {"id": "intake", "title": "Project intake", "kind": "deterministic", "dependencies": [], "operation": "doctor", "mutation": "read"},
    {"id": "connection-validation", "title": "Source and connection validation", "kind": "deterministic", "dependencies": ["intake"], "operation": "doctor-deep", "mutation": "read"},
    {"id": "source-normalization", "title": "Source normalization", "kind": "deterministic", "dependencies": ["connection-validation"], "operation": "configuration-source-validate", "mutation": "read"},
    {"id": "physical-cleanup", "title": "Physical clean comparison", "kind": "agent", "dependencies": ["source-normalization"], "operation": "manual-cleanup", "mutation": "write"},
    {"id": "diff-inventory", "title": "Diff inventory", "kind": "agent", "dependencies": ["physical-cleanup"], "operation": "parallel-research", "mutation": "write"},
    {"id": "feature-research", "title": "Feature and requirement research", "kind": "agent", "dependencies": ["diff-inventory"], "operation": "parallel-research", "mutation": "write"},
    {"id": "reverse-map", "title": "Reverse functional map", "kind": "agent", "dependencies": ["feature-research"], "operation": "reverse-map", "mutation": "write"},
    {"id": "final-gate", "title": "Final gate", "kind": "deterministic", "dependencies": ["reverse-map"], "operation": "final-gate", "mutation": "write"},
    {"id": "evidence-packs", "title": "Evidence packs", "kind": "agent", "dependencies": ["final-gate"], "operation": "parallel-research", "mutation": "write"},
    {"id": "subject-cards", "title": "Subject cards", "kind": "agent", "dependencies": ["evidence-packs"], "operation": "subject-cards", "mutation": "write"},
    {"id": "functional-gaps", "title": "Functional gaps", "kind": "agent", "dependencies": ["subject-cards"], "operation": "functional-gaps", "mutation": "write"},
    {"id": "review", "title": "Review preparation", "kind": "agent", "dependencies": ["functional-gaps"], "operation": "review", "mutation": "write"},
    {"id": "results", "title": "Result generation", "kind": "deterministic", "dependencies": ["review"], "operation": "final-gate", "mutation": "write"},
    {"id": "dashboards", "title": "Dashboards", "kind": "deterministic", "dependencies": ["results"], "operation": "results", "mutation": "write"},
    {"id": "verification", "title": "Strict verification", "kind": "deterministic", "dependencies": ["dashboards"], "operation": "doctor-strict", "mutation": "read"},
)
STAGE_BY_ID = {stage["id"]: stage for stage in STAGES}
_RESULT_VIEWS = {"physical-cleanup": "outputs/clean-comparison-dashboard/index.html", "functional-gaps": "outputs/functional-gap-dashboard/index.html", "dashboards": "outputs/review/index.html"}
for _stage in STAGES:
    _stage.update({
        "readiness_probe": "repository_fingerprint",
        "configuration_schema": {"timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 86400}},
        "approval_policy": "per_run" if _stage["mutation"] == "write" else "none",
        "resume_policy": "retry" if _stage["kind"] == "deterministic" else "documented_unit_resume",
        "allow_read_overlap": _stage["mutation"] == "read",
        "prompt_default": f"Complete the {_stage['title']} stage using repository evidence." if _stage["kind"] == "agent" else None,
        "result_view": _RESULT_VIEWS.get(_stage["id"]),
    })


def redact(value: str) -> str:
    return SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)


def redact_structure(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("<redacted>" if re.search(r"(?i)(password|secret|token|api[_-]?key)", str(key)) and key not in {"secret_ref", "secret_present"} else redact_structure(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_structure(item) for item in value]
    return redact(value) if isinstance(value, str) else value


def canonical_under(path: Path, roots: Iterable[Path]) -> Path:
    resolved = path.expanduser().resolve()
    for root in roots:
        try:
            resolved.relative_to(root.expanduser().resolve())
            return resolved
        except ValueError:
            pass
    raise ValueError(f"path is outside approved roots: {resolved}")


def process_start_marker(pid: int) -> str:
    if os.name != "nt":
        stat = Path(f"/proc/{pid}/stat")
        try:
            if stat.is_file():
                fields = stat.read_text(encoding="utf-8", errors="replace").split()
                return fields[21] if len(fields) > 21 else ""
        except OSError:
            pass
    return ""


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
                    manifest_hash TEXT NOT NULL DEFAULT '', setup_json TEXT NOT NULL DEFAULT '{}', approved_roots_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
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
                CREATE TABLE IF NOT EXISTS prompt_versions(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, stage_id TEXT NOT NULL,
                    version INTEGER NOT NULL, template TEXT NOT NULL, supplement TEXT NOT NULL,
                    assembled TEXT NOT NULL, diff TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(project_id,stage_id,version), FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS artifacts(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT, path TEXT NOT NULL,
                    media_type TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(project_id,path), FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS log_indexes(
                    run_id TEXT PRIMARY KEY, total_lines INTEGER NOT NULL DEFAULT 0,
                    total_bytes INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS preferences(
                    project_id TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL,
                    PRIMARY KEY(project_id,key), FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS events_project_id ON events(project_id,id);
                UPDATE schema_version SET version=3;
            """)
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(projects)")}
            if "approved_roots_json" not in columns:
                self.db.execute("ALTER TABLE projects ADD COLUMN approved_roots_json TEXT NOT NULL DEFAULT '[]'")

    def close(self) -> None:
        self.db.close()

    def project(self, project_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError(project_id)
        result = dict(row)
        result["setup"] = json.loads(result.pop("setup_json"))
        result["approved_roots"] = json.loads(result.pop("approved_roots_json"))
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
                "INSERT INTO projects(id,name,root,manifest_hash,approved_roots_json,created_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,root=excluded.root,manifest_hash=excluded.manifest_hash,approved_roots_json=excluded.approved_roots_json",
                (project_id, name, str(resolved), file_sha256(manifest), json.dumps([str(Path(item).resolve()) for item in approved_roots]), now),
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
        forbidden = [key for key in data if re.search(r"(?i)(password|secret|token|api[_-]?key)", key) and key not in {"secret_ref", "secret_present"}]
        if forbidden:
            raise ValueError(f"secret fields must use the write-only secret input: {', '.join(forbidden)}")
        payload = {key: value for key, value in data.items() if key not in {"id", "project_id", "created_at", "updated_at"}}
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO resources(kind,id,project_id,data_json,created_at,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(kind,id) DO UPDATE SET project_id=excluded.project_id,data_json=excluded.data_json,updated_at=excluded.updated_at",
                (kind, resource_id, project_id, json.dumps(payload), now, now),
            )
        return self.resource(kind, resource_id)

    def delete_resource(self, kind: str, resource_id: str) -> None:
        try:
            resource = self.resource(kind, resource_id)
        except KeyError:
            return
        with self.lock, self.db:
            self.db.execute("DELETE FROM resources WHERE kind=? AND id=?", (kind, resource_id))
        secret_ref = resource.get("secret_ref")
        if secret_ref:
            canonical_under(self.paths.credentials / str(secret_ref), [self.paths.credentials]).unlink(missing_ok=True)

    def write_secret(self, secret_id: str, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", secret_id):
            raise ValueError("invalid secret id")
        atomic_private_text(self.paths.credentials / secret_id, value)
        return secret_id

    def secret(self, secret_id: str) -> str:
        path = canonical_under(self.paths.credentials / secret_id, [self.paths.credentials])
        return path.read_text(encoding="utf-8")

    def append_event(self, project_id: str, event_type: str, payload: dict[str, Any], run_id: str | None = None) -> int:
        payload = redact_structure(payload)
        raw = json.dumps(payload, ensure_ascii=False)
        if len(raw.encode()) > MAX_EVENT_PAYLOAD:
            raise ValueError("event payload too large")
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT INTO events(project_id,run_id,type,payload_json,created_at) VALUES(?,?,?,?,?)",
                (project_id, run_id, event_type, raw, utc_now_iso()),
            )
            self.db.execute("DELETE FROM events WHERE project_id=? AND id NOT IN (SELECT id FROM events WHERE project_id=? ORDER BY id DESC LIMIT ?)", (project_id, project_id, EVENT_RETENTION))
        return int(cursor.lastrowid)

    def events(self, project_id: str, after: int = 0, limit: int = 500, run_id: str | None = None) -> tuple[list[dict[str, Any]], bool]:
        oldest = self.db.execute("SELECT MIN(id) AS id FROM events WHERE project_id=?", (project_id,)).fetchone()["id"]
        reset = bool(after and oldest and after < oldest - 1)
        sql = "SELECT * FROM events WHERE project_id=? AND id>?"
        params: list[Any] = [project_id, after]
        if run_id:
            sql += " AND run_id=?"
            params.append(run_id)
        rows = self.db.execute(sql + " ORDER BY id LIMIT ?", (*params, min(limit, 1000))).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows], reset

    def run(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        result = dict(row)
        result["snapshot"] = json.loads(result.pop("snapshot_json"))
        result["log_tail"] = self.log_tail(run_id)
        return result

    def runs(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT id FROM runs" + (" WHERE project_id=?" if project_id else "") + " ORDER BY rowid DESC", ((project_id,) if project_id else ())).fetchall()
            return [self.run(row["id"]) for row in rows]

    def log_tail(self, run_id: str) -> list[str]:
        path = self.paths.runs / run_id / "run.log"
        if not path.exists():
            return []
        return [redact(line.rstrip("\n")) for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_LIVE_LOG_LINES:]]

    def log_page(self, run_id: str, offset: int = 0, limit: int = 200) -> dict[str, Any]:
        path = canonical_under(self.paths.runs / run_id / "run.log", [self.paths.runs])
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
        start = max(0, min(offset, len(lines)))
        page = [redact(line) for line in lines[start:start + min(max(limit, 1), 500)]]
        with self.lock, self.db:
            self.db.execute("INSERT INTO log_indexes(run_id,total_lines,total_bytes,updated_at) VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET total_lines=excluded.total_lines,total_bytes=excluded.total_bytes,updated_at=excluded.updated_at", (run_id, len(lines), path.stat().st_size if path.is_file() else 0, utc_now_iso()))
        return {"data": page, "offset": start, "next_offset": start + len(page), "total": len(lines)}

    def save_prompt(self, project_id: str, stage_id: str, template: str, supplement: str, variables: dict[str, str]) -> dict[str, Any]:
        if stage_id not in STAGE_BY_ID or STAGE_BY_ID[stage_id]["kind"] != "agent":
            raise ValueError("prompt is allowed only for an agent stage")
        mandatory = "Follow AGENTS.md, repository safety rules, evidence requirements, and the coordinator-owned output schema."
        resolved = template
        for key, value in variables.items():
            resolved = resolved.replace("{{" + key + "}}", str(value))
        assembled = "\n\n".join((mandatory, resolved, supplement.strip())).strip()
        previous = self.db.execute("SELECT * FROM prompt_versions WHERE project_id=? AND stage_id=? ORDER BY version DESC LIMIT 1", (project_id, stage_id)).fetchone()
        version = int(previous["version"]) + 1 if previous else 1
        import difflib
        difference = "\n".join(difflib.unified_diff(previous["assembled"].splitlines() if previous else [], assembled.splitlines(), fromfile=f"v{version-1}", tofile=f"v{version}", lineterm=""))
        prompt_id = str(uuid.uuid4())
        with self.lock, self.db:
            self.db.execute("INSERT INTO prompt_versions VALUES(?,?,?,?,?,?,?,?,?)", (prompt_id, project_id, stage_id, version, template, supplement, assembled, difference, utc_now_iso()))
        return self.prompt(prompt_id)

    def prompt(self, prompt_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM prompt_versions WHERE id=?", (prompt_id,)).fetchone()
        if not row:
            raise KeyError(prompt_id)
        return dict(row)

    def prompts(self, project_id: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM prompt_versions", ()
        if project_id:
            sql, params = sql + " WHERE project_id=?", (project_id,)
        return [dict(row) for row in self.db.execute(sql + " ORDER BY created_at DESC", params)]

    def preferences(self, project_id: str) -> dict[str, Any]:
        return {row["key"]: json.loads(row["value_json"]) for row in self.db.execute("SELECT key,value_json FROM preferences WHERE project_id=?", (project_id,))}

    def save_preferences(self, project_id: str, values: dict[str, Any]) -> dict[str, Any]:
        with self.lock, self.db:
            for key, value in values.items():
                self.db.execute("INSERT INTO preferences(project_id,key,value_json) VALUES(?,?,?) ON CONFLICT(project_id,key) DO UPDATE SET value_json=excluded.value_json", (project_id, str(key), json.dumps(value)))
        return self.preferences(project_id)

    def discover_artifacts(self, project_id: str) -> list[dict[str, Any]]:
        project = self.project(project_id); root = Path(project["root"])
        known = {value for value in _RESULT_VIEWS.values() if value}
        now = utc_now_iso()
        with self.lock, self.db:
            for relative in known:
                path = canonical_under(root / relative, [root])
                if path.is_file():
                    artifact_id = hashlib.sha256(f"{project_id}:{relative}".encode()).hexdigest()[:24]
                    media = "text/html" if path.suffix.lower() == ".html" else "application/octet-stream"
                    self.db.execute("INSERT INTO artifacts(id,project_id,path,media_type,created_at) VALUES(?,?,?,?,?) ON CONFLICT(project_id,path) DO UPDATE SET media_type=excluded.media_type", (artifact_id, project_id, relative, media, now))
            self.db.execute("DELETE FROM artifacts WHERE project_id=? AND path NOT IN (%s)" % ",".join("?" for _ in known), (project_id, *known))
        return self.artifacts(project_id)

    def artifacts(self, project_id: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM artifacts", ()
        if project_id:
            sql, params = sql + " WHERE project_id=?", (project_id,)
        return [dict(row) for row in self.db.execute(sql + " ORDER BY created_at DESC", params)]

    def request_approval(self, project_id: str, stage_id: str, data: dict[str, Any], key: str) -> dict[str, Any]:
        existing = self.db.execute("SELECT id FROM approvals WHERE project_id=? AND idempotency_key=?", (project_id, key)).fetchone()
        if existing:
            return self.approval(existing["id"])
        approval_id, now = str(uuid.uuid4()), utc_now_iso()
        with self.lock, self.db:
            self.db.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?)", (approval_id, project_id, stage_id, "pending", key, json.dumps(data), now, now))
        self.append_event(project_id, "approval.required", {"approval_id": approval_id, "stage_id": stage_id, "impact": data.get("impact", "")})
        return self.approval(approval_id)

    def approval(self, approval_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            raise KeyError(approval_id)
        result = dict(row)
        result["data"] = json.loads(result.pop("data_json"))
        return result

    def approvals(self, project_id: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT id FROM approvals", ()
        if project_id:
            sql, params = sql + " WHERE project_id=?", (project_id,)
        return [self.approval(row["id"]) for row in self.db.execute(sql + " ORDER BY created_at DESC", params)]

    def decide(self, approval_id: str, choice: str, actor: str) -> dict[str, Any]:
        with self.lock:
            return self._decide(approval_id, choice, actor)

    def _decide(self, approval_id: str, choice: str, actor: str) -> dict[str, Any]:
        approval = self.approval(approval_id)
        if choice not in approval["data"].get("choices", ["accept", "reject"]):
            raise ValueError("decision is not allowed")
        if approval["status"] != "pending":
            return approval
        status = "accepted" if choice == "accept" else "rejected"
        project = self.project(approval["project_id"])
        mutation = approval["data"].get("mutation") or {"kind": "decision_record"}
        if mutation.get("kind") == "manifest_update" and choice == "accept":
            fingerprint = update_project_manifest(Path(project["root"]), mutation.get("updates") or {}, str(mutation.get("expected_hash") or ""))
            verified = fingerprint == file_sha256(Path(project["root"]) / "project.toml")
        elif mutation.get("kind") == "decision_record" or choice == "reject":
            verified = True
        else:
            raise ValueError("unsupported canonical decision mutation")
        decided_at = utc_now_iso()
        decision_path = Path(project["root"]) / "analysis" / "decisions" / "workspace-decisions.jsonl"
        existing = decision_path.read_text(encoding="utf-8") if decision_path.is_file() else ""
        record = {"approval_id": approval_id, "stage_id": approval["stage_id"], "choice": choice, "actor": actor, "decided_at": decided_at, "evidence": approval["data"].get("evidence", []), "impact": approval["data"].get("impact", ""), "mutation": mutation, "verified": verified}
        atomic_private_text(decision_path, existing + json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        decision_path.chmod(0o644)
        data = approval["data"] | {"choice": choice, "actor": actor, "decided_at": decided_at, "verified": verified, "canonical_record": decision_path.relative_to(project["root"]).as_posix()}
        with self.lock, self.db:
            self.db.execute("UPDATE approvals SET status=?,data_json=?,updated_at=? WHERE id=?", (status, json.dumps(data), utc_now_iso(), approval_id))
            run_id = str(data.get("run_id") or "")
            if run_id:
                run_status = "completed" if choice == "accept" and verified else "failed"
                self.db.execute(
                    "UPDATE runs SET status=?,finished_at=?,error=? WHERE id=? AND status='awaiting_approval'",
                    (run_status, utc_now_iso(), "" if run_status == "completed" else "agent result rejected", run_id),
                )
        self.append_event(approval["project_id"], "approval.decided", {"approval_id": approval_id, "status": status})
        if data.get("run_id"):
            self.append_event(approval["project_id"], f"run.{'completed' if choice == 'accept' and verified else 'failed'}", {"run_id": data["run_id"], "stage_id": approval["stage_id"]}, str(data["run_id"]))
        return self.approval(approval_id)


def source_roots(project: dict[str, Any]) -> list[Path]:
    manifest = read_toml(Path(project["root"]) / "project.toml")
    values = manifest.get("paths", {})
    roots = [Path(project["root"])]
    approved = [Path(item) for item in project.get("approved_roots") or [project["root"]]]
    for key in ("vendor_baseline", "target_cf", "target_cfe", "next_vendor", "compare_repo"):
        value = str(values.get(key) or "").strip()
        if value:
            candidate = Path(value)
            roots.append(canonical_under(candidate if candidate.is_absolute() else Path(project["root"]) / candidate, approved))
    return roots


def workflow_snapshot(store: WorkspaceStore, project_id: str) -> dict[str, Any]:
    project = store.project(project_id)
    root = Path(project["root"])
    runs = store.runs(project_id)
    pending_approvals = {item["stage_id"]: item for item in store.approvals(project_id) if item["status"] == "pending"}
    configured = {item.strip() for item in str(project.get("setup", {}).get("enabled_stages") or "").split(",") if item.strip()}
    try:
        assignments = json.loads(str(project.get("setup", {}).get("agent_assignments") or "{}"))
    except json.JSONDecodeError:
        assignments = {}
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        latest.setdefault(run["stage_id"], run)
    stages = []
    completed: set[str] = set()
    artifact_map = {
        "physical-cleanup": "outputs/clean-comparison-dashboard/index.html",
        "functional-gaps": "outputs/functional-gap-dashboard/index.html",
        "dashboards": "outputs/review/index.html",
    }
    for definition in STAGES:
        enabled = not configured or definition["id"] in configured
        run = latest.get(definition["id"])
        artifact = artifact_map.get(definition["id"])
        artifact_ready = bool(artifact and (root / artifact).is_file())
        status = run["status"] if run else "pending" if enabled else "disabled"
        if enabled and artifact_ready and not run:
            status = "completed"
        if status == "completed" and artifact and not artifact_ready:
            status = "stale"
        blockers = [dep for dep in definition["dependencies"] if dep not in completed]
        approval = pending_approvals.get(definition["id"])
        if approval:
            blockers.append(f"approval:{approval['id']}")
        resolved_profile = None
        if enabled and definition["kind"] == "agent":
            profile_id = str(assignments.get(definition["id"]) or "") if isinstance(assignments, dict) else ""
            try:
                resolved_profile = store.resource("agent", profile_id)
                if not resolved_profile.get("enabled", True) or not resolved_profile.get("available", False):
                    blockers.append("agent_profile_unavailable")
            except KeyError:
                blockers.append("agent_profile_missing")
        if status == "completed" and not approval:
            completed.add(definition["id"])
        stages.append(definition | {
            "status": status,
            "ready": enabled and not blockers,
            "enabled": enabled,
            "blockers": blockers,
            "run_id": run["id"] if run else None,
            "artifact": artifact if artifact_ready else None,
            "approval": approval,
            "agent_profile": resolved_profile,
        })
    return {"project": project, "stages": stages, "generated_at": utc_now_iso()}


def agent_probe(provider: str) -> dict[str, Any]:
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError("unsupported provider")
    executable = shutil.which(provider)
    if not executable:
        return {"provider": provider, "available": False, "reason": "executable_not_found"}
    version = subprocess.run([executable, "--version"], text=True, capture_output=True, timeout=10, check=False)
    auth_command = [executable, "login", "status"] if provider == "codex" else [executable, "auth", "status"]
    auth = subprocess.run(auth_command, text=True, capture_output=True, timeout=10, check=False)
    return {
        "provider": provider, "available": version.returncode == 0 and auth.returncode == 0,
        "version": redact((version.stdout or version.stderr).strip()), "authentication_ready": auth.returncode == 0,
        "capabilities": {"structured_output": True, "isolated_workspace": True, "tool_restriction": True, "cancellation": "process_group"},
        "reason": "" if version.returncode == 0 and auth.returncode == 0 else "version_or_authentication_probe_failed",
    }


def validate_agent_profile(store: WorkspaceStore, profile: dict[str, Any], profile_id: str | None = None) -> None:
    provider = profile.get("provider")
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError("unsupported provider")
    if not str(profile.get("model") or "").strip():
        raise ValueError("model is required")
    workers = int(profile.get("workers") or 1)
    timeout = int(profile.get("timeout_seconds") or 3600)
    if workers < 1 or workers > 32 or timeout < 30 or timeout > 86400:
        raise ValueError("agent limits are outside supported bounds")
    tools = set(profile.get("tools") or [])
    if not tools <= {"read", "search"}:
        raise ValueError("unsupported agent tool policy")
    reasoning = str(profile.get("reasoning") or "")
    if provider == "codex" and reasoning not in {"", "low", "medium", "high", "xhigh"}:
        raise ValueError("unsupported Codex reasoning setting")
    if provider == "claude" and reasoning:
        raise ValueError("Claude profile does not support a reasoning override")
    profiles = {item["id"]: item for item in store.resources("agent")}
    current_id = profile_id or str(profile.get("id") or "__new__")
    profiles[current_id] = profile | {"id": current_id}
    cursor, seen = current_id, set()
    while cursor:
        if cursor in seen:
            raise ValueError("fallback cycle")
        seen.add(cursor)
        candidate = profiles.get(cursor)
        if not candidate:
            raise ValueError("fallback profile does not exist")
        fallback = str(candidate.get("fallback_profile") or "")
        if fallback and profiles.get(fallback, {}).get("provider") != provider:
            raise ValueError("fallback profile is incompatible")
        cursor = fallback


def agent_command(provider: str, model: str, prompt: str, cwd: Path, output_path: Path | None = None, reasoning: str = "") -> list[str]:
    if provider == "codex":
        command = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "-m", model, "-C", str(cwd)]
        if reasoning:
            command.extend(["-c", f"model_reasoning_effort={reasoning}"])
        for feature in ("apps", "browser_use", "computer_use", "image_generation", "standalone_web_search"):
            command.extend(["--disable", feature])
        if output_path:
            command.extend(["--output-schema", str(cwd / "schema.json"), "-o", str(output_path)])
        return command + [prompt]
    if provider == "claude":
        return ["claude", "-p", "--output-format", "json", "--permission-mode", "dontAsk", "--allowedTools", "Read,Glob,Grep", "--model", model, prompt]
    raise ValueError("unsupported provider")


def normalize_agent_result(provider: str, raw: str, source_fingerprints: dict[str, str]) -> dict[str, Any]:
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError("unsupported provider")
    try:
        value = json.loads(raw)
        if provider == "claude" and isinstance(value, dict) and isinstance(value.get("result"), str):
            value = json.loads(value["result"])
    except json.JSONDecodeError as exc:
        raise ValueError("agent output is not structured JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("decisions"), list) or not isinstance(value.get("evidence"), list):
        raise ValueError("agent output violates coordinator schema")
    if not value["evidence"] or any(not isinstance(item, dict) or not item.get("path") for item in value["evidence"]):
        raise ValueError("agent evidence is missing")
    return {"provider": provider, "decisions": value["decisions"], "evidence": value["evidence"], "trace": value.get("trace", {}), "source_fingerprints": source_fingerprints}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "redirect rejected", headers, fp)


def test_connection(profile: dict[str, Any], secret: str = "") -> dict[str, Any]:
    channel = profile.get("channel")
    if channel not in ALLOWED_CONNECTIONS:
        raise ValueError("unsupported connection channel")
    target = str(profile.get("url") or "")
    if channel == "onec":
        connection = str(profile.get("connection_string") or "")
        server = re.search(r'Srvr="([^"]+)"', connection, re.IGNORECASE)
        reference = re.search(r'Ref="([^"]+)"', connection, re.IGNORECASE)
        file_path = re.search(r'File="([^"]+)"', connection, re.IGNORECASE)
        if not ((server and reference) or file_path):
            raise ValueError("invalid 1C infobase connection string")
        version = str(profile.get("platform_version") or "")
        executable = Path(f"/opt/1cv8/x86_64/{version}/1cv8") if version else Path()
        capabilities = ["designer"] if version and executable.is_file() else []
        return {"ok": bool(capabilities), "target": connection, "error": "" if capabilities else "configured 1C platform executable is unavailable", "capabilities": capabilities}
    if channel == "postgresql":
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise ValueError("invalid PostgreSQL URL")
        target_label = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 5432}/{parsed.path.lstrip('/')}"
        psql = shutil.which("psql")
        if not psql:
            return {"ok": False, "target": target_label, "error": "psql executable is unavailable", "capabilities": []}
        environment = os.environ.copy()
        environment.update({"PGHOST": parsed.hostname, "PGPORT": str(parsed.port or 5432), "PGDATABASE": parsed.path.lstrip("/") or "postgres", "PGUSER": str(profile.get("user") or "postgres"), "PGPASSWORD": secret, "PGCONNECT_TIMEOUT": "5"})
        try:
            result = subprocess.run([psql, "--no-psqlrc", "--tuples-only", "--command", "BEGIN READ ONLY; SELECT current_database(); ROLLBACK;"], env=environment, text=True, capture_output=True, timeout=8, check=False)
            return {"ok": result.returncode == 0, "target": target_label, "error": "" if result.returncode == 0 else redact(result.stderr[-1000:]), "capabilities": ["postgresql_read"] if result.returncode == 0 else []}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "target": target_label, "error": redact(str(exc)), "capabilities": []}
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid HTTP URL")
    headers = {"Accept": "application/json,text/html;q=0.5"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    request = urllib.request.Request(target, method="GET", headers=headers)
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPHandler(), urllib.request.HTTPSHandler())
    try:
        response = opener.open(request, timeout=5)
        if len(response.read(64 * 1024 + 1)) > 64 * 1024:
            raise ValueError("connection probe response is too large")
        final = urllib.parse.urlsplit(response.geturl())
        if (final.scheme, final.hostname, final.port) != (parsed.scheme, parsed.hostname, parsed.port):
            raise ValueError("cross-origin redirect rejected")
        return {"ok": response.status < 500, "status": response.status, "target": target}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "target": target, "error": "redirect rejected" if 300 <= exc.code < 400 else "probe rejected"}


def render_project_manifest(current: str, updates: dict[str, dict[str, Any]]) -> str:
    lines = current.splitlines()
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
    return "\n".join(output) + "\n"


def update_project_manifest(project_root: Path, updates: dict[str, dict[str, Any]], expected_hash: str) -> str:
    path = project_root / "project.toml"
    if file_sha256(path) != expected_hash:
        raise RuntimeError("stale project.toml fingerprint")
    rendered = render_project_manifest(path.read_text(encoding="utf-8"), updates)
    atomic_private_text(path, rendered)
    path.chmod(0o644)
    return file_sha256(path)


class RunManager:
    def __init__(self, store: WorkspaceStore, allow_fake: bool = False):
        self.store = store
        self.allow_fake = allow_fake
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.lock = threading.RLock()
        self.monitor_stop = threading.Event()
        self.monitor_thread: threading.Thread | None = None
        self.reconcile()

    def start_monitor(self) -> None:
        if self.monitor_thread and self.monitor_thread.is_alive():
            return
        self.monitor_stop.clear()
        self.monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self.monitor_thread.start()

    def _monitor(self) -> None:
        while not self.monitor_stop.wait(1):
            self.reconcile()

    def close(self) -> None:
        self.monitor_stop.set()
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)

    def _command(self, project: dict[str, Any], stage: dict[str, Any], payload: dict[str, Any]) -> list[str]:
        root = Path(project["root"])
        if payload.get("fake") and self.allow_fake:
            seconds = min(5.0, max(0.0, float(payload.get("fake_seconds", 0.15))))
            return [sys.executable, "-c", f"import time; print('started',flush=True); time.sleep({seconds!r}); print('completed',flush=True)"]
        operation = stage["operation"]
        commands = {
            "doctor": [sys.executable, "-m", "one_c_autoresearch", "doctor", "--repo-path", str(root), "--json"],
            "doctor-deep": [sys.executable, "-m", "one_c_autoresearch", "doctor", "--repo-path", str(root), "--json", "--deep"],
            "configuration-source-validate": [sys.executable, "-m", "one_c_autoresearch", "doctor", "--repo-path", str(root), "--json", "--deep"],
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
            return agent_command(provider, model, prompt, Path(payload.get("_workspace") or root), Path(payload["_output"]) if payload.get("_output") else None, str(payload.get("reasoning") or ""))
        raise ValueError(f"stage operation requires configured agent execution: {stage['id']}")

    def start(self, project_id: str, stage_id: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        # ponytail: one local dispatcher lock; use per-project locks only if multi-project launch latency becomes measurable.
        with self.lock:
            return self._start(project_id, stage_id, payload, idempotency_key)

    def _start(self, project_id: str, stage_id: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        project = self.store.project(project_id)
        stage = STAGE_BY_ID.get(stage_id)
        if not stage:
            raise ValueError("unknown stage")
        allowed_payload = {"paths", "expected_manifest_hash", "expected_input_fingerprints"}
        if self.allow_fake:
            allowed_payload |= {"fake", "fake_seconds"}
        if stage["kind"] == "agent":
            allowed_payload |= {"agent_profile_id", "timeout_seconds", "workers"}
        unknown_payload = set(payload) - allowed_payload
        if unknown_payload:
            raise ValueError(f"unsupported run fields: {', '.join(sorted(unknown_payload))}")
        snapshot_before = workflow_snapshot(self.store, project_id)
        stage_state = next(item for item in snapshot_before["stages"] if item["id"] == stage_id)
        if not stage_state["enabled"]:
            raise RuntimeError("stage is disabled by project setup")
        if not stage_state["ready"]:
            raise RuntimeError(f"stage prerequisites are incomplete: {', '.join(stage_state['blockers'])}")
        existing = self.store.db.execute("SELECT id,payload_hash FROM runs WHERE project_id=? AND idempotency_key=?", (project_id, idempotency_key)).fetchone()
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise RuntimeError("idempotency key reused with different payload")
            return self.store.run(existing["id"])
        active = self.store.db.execute("SELECT id,stage_id FROM runs WHERE project_id=? AND status IN ('queued','running')", (project_id,)).fetchone()
        if active:
            active_stage = STAGE_BY_ID[active["stage_id"]]
            compatible = stage["mutation"] == active_stage["mutation"] == "read" and stage.get("allow_read_overlap") and active_stage.get("allow_read_overlap")
            if not compatible:
                raise RuntimeError(f"project has active incompatible run: {active['id']}")
        roots = source_roots(project)
        resolved_inputs = [canonical_under(Path(candidate), roots) for candidate in payload.get("paths", [])]
        current_manifest_hash = file_sha256(Path(project["root"]) / "project.toml")
        expected = str(payload.get("expected_manifest_hash") or current_manifest_hash)
        if expected != current_manifest_hash:
            raise RuntimeError("stale input fingerprint")
        input_fingerprints = {"project.toml": current_manifest_hash} | {str(path): path_sha256(path) for path in resolved_inputs}
        expected_inputs = payload.get("expected_input_fingerprints")
        if expected_inputs is not None and expected_inputs != input_fingerprints:
            raise RuntimeError("stale input fingerprint")
        if stage["kind"] == "agent":
            profile_id = str(payload.get("agent_profile_id") or "")
            if payload.get("fake") and self.allow_fake:
                pass
            elif not profile_id:
                raise RuntimeError("agent profile is required")
            else:
                profile = self.store.resource("agent", profile_id)
                probe = agent_probe(str(profile.get("provider") or ""))
                selected_profile = profile
                while (not profile.get("enabled", True) or not probe["available"]) and profile.get("fallback_profile"):
                    profile = self.store.resource("agent", str(profile["fallback_profile"]))
                    probe = agent_probe(str(profile.get("provider") or ""))
                if not profile.get("enabled", True) or not probe["available"]:
                    raise RuntimeError("agent profile and its compatible fallback are unavailable")
                requested_workers = int(payload.get("workers") or profile.get("workers") or 1)
                requested_timeout = int(payload.get("timeout_seconds") or profile.get("timeout_seconds") or 3600)
                if requested_workers < 1 or requested_workers > int(profile.get("workers") or 1):
                    raise ValueError("stage worker override exceeds the profile ceiling")
                if requested_timeout < 30 or requested_timeout > int(profile.get("timeout_seconds") or 3600):
                    raise ValueError("stage timeout override exceeds the profile bounds")
                prompts = [item for item in self.store.prompts(project_id) if item["stage_id"] == stage_id]
                prompt = prompts[0] if prompts else None
                payload = payload | {"provider": profile["provider"], "model": profile["model"], "reasoning": profile.get("reasoning", ""), "workers": requested_workers, "timeout_seconds": requested_timeout, "prompt": prompt["assembled"] if prompt else stage.get("prompt_default", ""), "selected_agent_profile_snapshot": selected_profile, "agent_profile_snapshot": profile, "prompt_snapshot": prompt, "adapter_version": probe.get("version", "")}
        run_id = str(uuid.uuid4())
        run_token = secrets.token_urlsafe(32)
        run_dir = self.store.paths.runs / run_id
        run_dir.mkdir(parents=True, mode=0o700)
        command_payload = payload
        command_cwd = Path(project["root"])
        if stage["kind"] == "agent" and not payload.get("fake"):
            command_cwd = run_dir / "workspace"
            command_cwd.mkdir(mode=0o700)
            schema = {"type": "object", "required": ["decisions", "evidence"], "properties": {"decisions": {"type": "array"}, "evidence": {"type": "array", "items": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}}, "trace": {"type": "object"}}, "additionalProperties": False}
            write_json(command_cwd / "schema.json", schema, mode=0o600)
            write_json(command_cwd / "context.json", {"stage": stage_id, "project": project_id, "allowed_sources": [str(path) for path in resolved_inputs], "source_fingerprints": input_fingerprints}, mode=0o600)
            atomic_private_text(command_cwd / "AGENTS.md", "Work only on the assignment in context.json. Read only allowed_sources. Return JSON matching schema.json. Do not modify canonical files.\n")
            command_payload = payload | {"_workspace": str(command_cwd), "_output": str(run_dir / "agent-result.json")}
        command = self._command(project, stage, command_payload)
        spec = {
            "run_id": run_id, "run_token": run_token, "command": command,
            "cwd": str(command_cwd), "run_dir": str(run_dir), "agent_output": str(run_dir / "agent-result.json"),
            "agent_provider": payload.get("provider") if stage["kind"] == "agent" and not payload.get("fake") else "",
            "source_fingerprints": input_fingerprints,
            "fake": bool(payload.get("fake") and self.allow_fake),
            "timeout_seconds": int(payload.get("timeout_seconds") or payload.get("agent_profile_snapshot", {}).get("timeout_seconds") or 3600),
        }
        write_json(run_dir / "spec.json", spec, mode=0o600)
        snapshot = {"stage": stage, "payload": payload, "manifest_hash": current_manifest_hash, "input_fingerprints": input_fingerprints, "source_roots": [str(path) for path in roots]}
        with self.store.lock, self.store.db:
            self.store.db.execute(
                "INSERT INTO runs(id,project_id,stage_id,status,idempotency_key,payload_hash,snapshot_json,run_token) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, project_id, stage_id, "queued", idempotency_key, payload_hash, json.dumps(snapshot), run_token),
            )
        self.store.append_event(project_id, "run.queued", {"run_id": run_id, "stage_id": stage_id}, run_id)
        wrapper = [sys.executable, "-m", "one_c_autoresearch.workspace_runner", str(run_dir / "spec.json")]
        try:
            runner_env = os.environ.copy()
            source_root = str(Path(__file__).resolve().parents[1])
            runner_env["PYTHONPATH"] = source_root + (os.pathsep + runner_env["PYTHONPATH"] if runner_env.get("PYTHONPATH") else "")
            process = subprocess.Popen(wrapper, cwd=project["root"], text=True, env=runner_env, start_new_session=os.name != "nt")
        except OSError as exc:
            error = redact(str(exc))
            with self.store.lock, self.store.db:
                self.store.db.execute("UPDATE runs SET status='failed',finished_at=?,exit_code=-1,error=? WHERE id=?", (utc_now_iso(), error, run_id))
            self.store.append_event(project_id, "run.failed", {"run_id": run_id, "stage_id": stage_id, "error": error}, run_id)
            raise RuntimeError(f"could not start stage process: {error}") from exc
        marker = process_start_marker(process.pid)
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
        delivered = 0
        if process:
            while process.poll() is None:
                log_path = run_dir / "run.log"
                if log_path.is_file() and log_path.stat().st_size > delivered:
                    with log_path.open("rb") as stream:
                        stream.seek(delivered)
                        raw_chunk = stream.read(8192)
                    if raw_chunk:
                        chunk = redact(raw_chunk.decode("utf-8", errors="replace"))
                        delivered += len(raw_chunk)
                        self.store.append_event(run["project_id"], "log.append", {"run_id": run_id, "stage_id": run["stage_id"], "text": chunk}, run_id)
                self.store.append_event(run["project_id"], "stage.progress", {"run_id": run_id, "stage_id": run["stage_id"], "indeterminate": True}, run_id)
                time.sleep(0.25)
            process.wait()
        for _ in range(50):
            if result_path.exists():
                break
            time.sleep(0.05)
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {"exit_code": process.returncode if process else -1, "error": "result missing"}
        verified = result.get("exit_code") == 0 and self._terminal_verified(run, result)
        is_agent_result = verified and STAGE_BY_ID[run["stage_id"]]["kind"] == "agent" and not run["snapshot"].get("payload", {}).get("fake")
        status = "awaiting_approval" if is_agent_result else "completed" if verified else "failed"
        if result.get("exit_code") == 0 and status == "failed" and not result.get("error"):
            result["error"] = "terminal result verification failed"
        error = redact(str(result.get("error") or ""))
        with self.store.lock, self.store.db:
            changed = self.store.db.execute("UPDATE runs SET status=?,finished_at=?,exit_code=?,error=? WHERE id=? AND status IN ('queued','running')", (status, utc_now_iso(), result.get("exit_code"), error, run_id)).rowcount
        if changed:
            self.store.append_event(run["project_id"], f"run.{status}", {"run_id": run_id, "stage_id": run["stage_id"], "error": error}, run_id)
            if status == "awaiting_approval":
                normalized = result.get("normalized_result") or {}
                self.store.request_approval(run["project_id"], run["stage_id"], {
                    "run_id": run_id,
                    "impact": "Применить проверенный результат агентского этапа",
                    "evidence": normalized.get("evidence", []),
                    "decisions": normalized.get("decisions", []),
                    "choices": ["accept", "reject"],
                    "mutation": {"kind": "decision_record"},
                }, f"agent-result:{run_id}")
            if status == "completed":
                for artifact in self.store.discover_artifacts(run["project_id"]):
                    self.store.append_event(run["project_id"], "artifact.ready", {"artifact_id": artifact["id"], "path": artifact["path"]}, run_id)
        self.processes.pop(run_id, None)

    def _terminal_verified(self, run: dict[str, Any], result: dict[str, Any]) -> bool:
        if result.get("run_token") != run["run_token"]:
            return False
        snapshot = run["snapshot"]
        if result.get("source_fingerprints") != snapshot.get("input_fingerprints"):
            return False
        if snapshot.get("payload", {}).get("fake") and self.allow_fake:
            return True
        stage = STAGE_BY_ID[run["stage_id"]]
        if stage["kind"] == "agent" and not result.get("normalized_result"):
            return False
        artifact = stage.get("result_view")
        if run["stage_id"] == "dashboards" and artifact and not (Path(self.store.project(run["project_id"])["root"]) / artifact).is_file():
            return False
        return True

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
            changed = self.store.db.execute("UPDATE runs SET status='cancelled',finished_at=? WHERE id=? AND status IN ('queued','running')", (utc_now_iso(), run_id)).rowcount
        if changed:
            self.store.append_event(run["project_id"], "run.cancelled", {"run_id": run_id, "stage_id": run["stage_id"]}, run_id)
        return self.store.run(run_id)

    def reconcile(self) -> None:
        rows = self.store.db.execute("SELECT id,project_id,stage_id,run_token FROM runs WHERE status IN ('queued','running')").fetchall()
        for row in rows:
            if row["id"] in self.processes:
                continue
            run_dir = self.store.paths.runs / row["id"]
            result_path = run_dir / "result.json"
            heartbeat_path = run_dir / "heartbeat.json"
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                run = self.store.run(row["id"])
                if result.get("run_token") == row["run_token"] and (result.get("exit_code") != 0 or self._terminal_verified(run, result)):
                    is_agent_result = result.get("exit_code") == 0 and STAGE_BY_ID[row["stage_id"]]["kind"] == "agent" and not run["snapshot"].get("payload", {}).get("fake")
                    status = "awaiting_approval" if is_agent_result else "completed" if result.get("exit_code") == 0 else "failed"
                    with self.store.db:
                        changed = self.store.db.execute("UPDATE runs SET status=?,finished_at=?,exit_code=?,error=? WHERE id=? AND status IN ('queued','running')", (status, utc_now_iso(), result.get("exit_code"), redact(str(result.get("error") or "")), row["id"])).rowcount
                    if changed:
                        self.store.append_event(row["project_id"], f"run.{status}", {"run_id": row["id"], "stage_id": row["stage_id"], "reconciled": True}, row["id"])
                        if status == "awaiting_approval":
                            normalized = result.get("normalized_result") or {}
                            self.store.request_approval(row["project_id"], row["stage_id"], {"run_id": row["id"], "impact": "Применить проверенный результат агентского этапа", "evidence": normalized.get("evidence", []), "decisions": normalized.get("decisions", []), "choices": ["accept", "reject"], "mutation": {"kind": "decision_record"}}, f"agent-result:{row['id']}")
                    continue
            live = False
            if heartbeat_path.exists():
                heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
                pid = int(heartbeat.get("pid") or 0)
                try:
                    os.kill(pid, 0)
                    marker = process_start_marker(pid)
                    recorded = self.store.db.execute("SELECT process_marker FROM runs WHERE id=?", (row["id"],)).fetchone()["process_marker"]
                    live = heartbeat.get("run_token") == row["run_token"] and marker == recorded and time.time() - float(heartbeat.get("time") or 0) < 10
                except (OSError, ValueError):
                    live = False
            if not live:
                with self.store.db:
                    self.store.db.execute("UPDATE runs SET status='interrupted',finished_at=?,error='runner identity could not be proven' WHERE id=?", (utc_now_iso(), row["id"]))
                self.store.append_event(row["project_id"], "run.interrupted", {"run_id": row["id"], "stage_id": row["stage_id"]}, row["id"])
