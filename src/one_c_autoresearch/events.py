from __future__ import annotations

import json
import os
import re
import fcntl
import itertools
from datetime import datetime, timezone
from pathlib import Path
from .contracts import JsonValue, atomic_bytes, atomic_json, canonical_json, parse_json_object, reject_secrets, sha256


EVENT_LIMIT = 10_000
PAYLOAD_LIMIT = 64 * 1024
LOG_LIMIT = 50 * 1024 * 1024
EVENT_TYPES = {"run.created", "run.finished", "job.started", "job.finished", "step.started", "step.progress", "step.action", "step.output", "step.validation", "step.finished", "log.append", "approval.required", "invocation.started", "invocation.finished"}
TERMINAL_STATUSES = {"completed", "blocked", "failed", "cancelled", "interrupted"}
RETRYABLE_RUN_STATUSES = frozenset((TERMINAL_STATUSES - {"blocked"}) | {"stale"})
REQUIRED_PAYLOAD = {
    "run.created": {"status", "actor", "process_identity", "workflow_fingerprint", "operation"},
    "run.finished": {"status", "duration_seconds"},
    "job.started": {"status", "actor", "operation"},
    "job.finished": {"status", "actor", "operation", "duration_seconds"},
    "step.started": {"status", "actor", "operation", "inputs", "input_fingerprint"},
    "step.progress": {"status", "progress"},
    "step.action": {"status", "effective_action"},
    "step.output": {"status", "outputs", "output_fingerprint"},
    "step.validation": {"status", "validation"},
    "step.finished": {"status", "actor", "operation", "duration_seconds"},
    "log.append": {"status", "log"},
    "approval.required": {"status", "blocker"},
    "invocation.started": {
        "transition_key", "invocation_id", "run_id", "phase_id", "role_id",
        "slot_id", "work_unit_id", "invocation_status", "timestamp",
    },
    "invocation.finished": {
        "transition_key", "invocation_id", "run_id", "phase_id", "role_id",
        "slot_id", "work_unit_id", "invocation_status", "timestamp",
        "duration_seconds",
    },
}


def process_identity(pid: int | None = None) -> dict[str, JsonValue] | None:
    pid = pid or os.getpid()
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        return {"pid": pid, "start_time": fields[19], "boot_id": boot_id}
    except (OSError, IndexError):
        return None


def process_identity_alive(identity: dict[str, JsonValue] | None) -> bool:
    if not isinstance(identity, dict) or set(identity) != {"pid", "start_time", "boot_id"}:
        return False
    try:
        return process_identity(int(str(identity["pid"]))) == identity
    except (TypeError, ValueError):
        return False


def redact(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: ("[not retained]" if key.lower() in {"reasoning", "private_reasoning", "chain_of_thought"} else "***" if any(word in key.lower() for word in ("password", "token", "secret", "private_key")) else redact(child)) for key, child in value.items()}
    if isinstance(value, list):
        return [redact(child) for child in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(--(?:db-pwd|password|pwd|token|secret)=)[^\s]+", r"\1***", value)
        value = re.sub(r"(?i)\b((?:db[_-]?)?(?:password|pwd)|token|secret)=\S+", r"\1=***", value)
        value = re.sub(r"(?i)(/(?:P|password))[^\s]+", r"\1***", value)
        return value
    return value


def _object(value: JsonValue, message: str = "expected object") -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(message)
    return value


def _objects(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("expected object array")
    return [item for item in value if isinstance(item, dict)]


def _sequence(value: JsonValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("invalid event sequence")
    return value


class EventStore:
    def __init__(self, root: Path, project_id: str):
        self.root: Path = root / project_id
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.events_path: Path = self.root / "events.jsonl"
        self.lock_path: Path = self.root / ".events.lock"

    def prepare_run(self, run_id: str, execution_snapshot: dict[str, JsonValue]) -> str:
        """Атомарно создаёт неизменяемую безопасную часть файла запуска."""

        reject_secrets(execution_snapshot, "execution snapshot")
        encoded = canonical_json(execution_snapshot)
        fingerprint = "sha256:" + sha256(encoded)
        path = self.root / "runs" / f"{sha256(run_id.encode())}.json"
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if path.is_file():
                prior = parse_json_object(path.read_text(encoding="utf-8"))
                if prior.get("run_id") != run_id or prior.get("execution_snapshot_fingerprint") != fingerprint or canonical_json(prior.get("execution_snapshot")) != encoded:
                    raise RuntimeError("run execution snapshot is immutable")
                return fingerprint
            atomic_json(path, {"schema_version": "2", "run_id": run_id, "last_sequence": 0, "status": "preparing", "process_identity": None, "execution_snapshot": execution_snapshot, "execution_snapshot_fingerprint": fingerprint, "events": []})
            path.chmod(0o600)
        return fingerprint

    def remove_prepared_run(self, run_id: str) -> bool:
        path = self.root / "runs" / f"{sha256(run_id.encode())}.json"
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if not path.is_file():
                return False
            value = parse_json_object(path.read_text(encoding="utf-8"))
            if value.get("events"):
                return False
            path.unlink()
            return True

    def run_snapshot(self, run_id: str) -> dict[str, JsonValue] | None:
        """Читает файл запуска и проверяет неизменяемый снимок."""

        path = self.root / "runs" / f"{sha256(run_id.encode())}.json"
        try:
            value = parse_json_object(path.read_text(encoding="utf-8"))
            snapshot = value["execution_snapshot"]
            fingerprint = "sha256:" + sha256(canonical_json(snapshot))
        except (FileNotFoundError, OSError, KeyError, json.JSONDecodeError, TypeError):
            return None
        if value.get("run_id") != run_id or value.get("execution_snapshot_fingerprint") != fingerprint:
            return None
        return value

    def emit(self, event_type: str, run_id: str, payload: dict[str, JsonValue], **hierarchy: JsonValue) -> dict[str, JsonValue]:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported workflow event type: {event_type}")
        if not run_id:
            raise ValueError("workflow event requires run ID and object payload")
        if event_type.startswith("job.") and not hierarchy.get("job_id"):
            raise ValueError("job event requires job ID")
        attempt = hierarchy.get("attempt")
        if (event_type.startswith("step.") or event_type in {"log.append", "approval.required"}) and (not hierarchy.get("job_id") or not hierarchy.get("step_id") or not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1):
            raise ValueError("step event requires job, step, and positive attempt")
        if event_type in {"run.finished", "job.finished", "step.finished"} and payload.get("status") not in TERMINAL_STATUSES:
            raise ValueError("terminal workflow event requires a terminal status")
        if event_type == "invocation.started" and payload.get("invocation_status") != "running":
            raise ValueError("invocation started event requires running invocation status")
        if event_type == "invocation.finished" and payload.get("invocation_status") not in {"completed", "failed", "cancelled", "interrupted"}:
            raise ValueError("invocation finished event requires terminal invocation status")
        if event_type in {"run.created", "run.finished"}:
            prepared = self.run_snapshot(run_id)
            if prepared is not None and prepared.get("schema_version") == "2":
                required_snapshot_fields = {
                    "execution_snapshot_fingerprint",
                    "policy_source",
                    "tool_versions",
                }
                if missing_snapshot_fields := required_snapshot_fields - set(payload):
                    raise ValueError(f"dispatcher run event is missing execution metadata: {sorted(missing_snapshot_fields)}")
        missing = REQUIRED_PAYLOAD[event_type] - set(payload)
        if missing:
            raise ValueError(f"workflow event {event_type} is missing payload fields: {sorted(missing)}")
        if (event_type.endswith(".created") or event_type.endswith(".started") or event_type in {"step.progress", "step.action", "log.append"}) and not event_type.startswith("invocation."):
            if payload.get("status") != "running":
                raise ValueError(f"workflow event {event_type} requires running status")
        if "duration_seconds" in payload and (not isinstance(payload["duration_seconds"], (int, float)) or payload["duration_seconds"] < 0):
            raise ValueError("workflow event duration must be non-negative")
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return self._emit_locked(event_type, run_id, payload, **hierarchy)

    def _emit_locked(self, event_type: str, run_id: str, payload: dict[str, JsonValue], **hierarchy: JsonValue) -> dict[str, JsonValue]:
        events = self.events()
        cleaned = _object(redact(payload))
        transition_key = cleaned.get("transition_key")
        if transition_key:
            snapshot_path = self.root / "runs" / f"{sha256(run_id.encode())}.json"
            try:
                run_snapshot = parse_json_object(
                    snapshot_path.read_text(encoding="utf-8")
                )
                run_events = _objects(run_snapshot.get("events", []))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                run_events = []
            duplicate = next(
                (
                    event for event in [*events, *run_events]
                    if _object(event.get("payload", {})).get("transition_key") == transition_key
                ),
                None,
            )
            if duplicate is not None:
                return duplicate
        encoded = canonical_json(cleaned)
        if len(encoded) > PAYLOAD_LIMIT:
            artifacts = self.root / "artifacts"; artifacts.mkdir(exist_ok=True)
            fingerprint = sha256(encoded); artifact = artifacts / f"{fingerprint}.json"
            atomic_bytes(artifact, encoded)
            reference = {"truncated": True, "artifact": str(artifact.relative_to(self.root))}
            summary: dict[str, JsonValue] = {}
            for key in REQUIRED_PAYLOAD[event_type]:
                value = cleaned[key]
                summary[key] = value if len(canonical_json(value)) <= 1024 else reference
            cleaned = {**summary, **reference, "original_size": len(encoded)}
        sequence_path = self.root / "sequence.json"
        try:
            sequence = int(str(parse_json_object(sequence_path.read_text(encoding="utf-8"))["sequence"])) + 1
        except (FileNotFoundError, OSError, ValueError, KeyError, json.JSONDecodeError):
            sequence = _sequence(events[-1]["sequence"]) + 1 if events else 1
        event: dict[str, JsonValue] = {"schema_version": "workflow-event/v1", "sequence": sequence, "timestamp": datetime.now(timezone.utc).isoformat(), "type": event_type, "run_id": run_id, "job_id": hierarchy.get("job_id"), "step_id": hierarchy.get("step_id"), "attempt": hierarchy.get("attempt"), "payload": cleaned}
        events.append(event); events = events[-EVENT_LIMIT:]
        temporary = self.events_path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            for item in events: _ = stream.write(canonical_json(item) + b"\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, self.events_path)
        atomic_json(sequence_path, {"schema_version": "1", "sequence": sequence})
        snapshot_path = self.root / "runs" / f"{sha256(run_id.encode())}.json"
        try:
            prior_snapshot = parse_json_object(snapshot_path.read_text(encoding="utf-8")); history = _objects(prior_snapshot["events"])
        except (FileNotFoundError, OSError, KeyError, json.JSONDecodeError):
            prior_snapshot = {}; history = []
        history = [*history, event][-1000:]
        identity = cleaned.get("process_identity") if event_type == "run.created" else prior_snapshot.get("process_identity")
        snapshot: dict[str, JsonValue] = {"schema_version": "2" if "execution_snapshot" in prior_snapshot else "1", "run_id": run_id, "last_sequence": sequence, "status": prior_snapshot.get("status", "running") if event_type.startswith("invocation.") else cleaned.get("status", "running"), "process_identity": identity, "events": history}
        if "execution_snapshot" in prior_snapshot:
            snapshot["execution_snapshot"] = prior_snapshot["execution_snapshot"]
            snapshot["execution_snapshot_fingerprint"] = prior_snapshot["execution_snapshot_fingerprint"]
        atomic_json(snapshot_path, snapshot)
        snapshot_path.chmod(0o600)
        return event

    def events(self) -> list[dict[str, JsonValue]]:
        if not self.events_path.is_file():
            return []
        return [parse_json_object(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line]

    def accepted(self, key: str) -> dict[str, JsonValue] | None:
        path = self.root / "accepted" / f"{sha256(key.encode())}.json"
        return parse_json_object(path.read_text(encoding="utf-8")) if path.is_file() else None

    def accept(self, key: str, result: dict[str, JsonValue]) -> None:
        atomic_json(self.root / "accepted" / f"{sha256(key.encode())}.json", {"schema_version": "1", "key": key, "result": result})

    def proposal(self, key: str) -> dict[str, JsonValue] | None:
        path = self.root / "proposals" / f"{sha256(key.encode())}.json"
        return parse_json_object(path.read_text(encoding="utf-8")) if path.is_file() else None

    def save_proposal(self, key: str, operation: str, profile: str, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        reject_secrets(payload, "agent proposal")
        value = {"schema_version": "1", "key": key, "operation": operation, "agent_profile": profile, "payload": redact(payload)}
        atomic_json(self.root / "proposals" / f"{sha256(key.encode())}.json", value)
        return value

    def cancel(self, run_id: str, actor: str) -> None:
        atomic_json(self.root / "cancellations" / f"{sha256(run_id.encode())}.json", {"schema_version": "1", "run_id": run_id, "actor": actor, "timestamp": datetime.now(timezone.utc).isoformat()})

    def cancellation(self, run_id: str) -> dict[str, JsonValue] | None:
        path = self.root / "cancellations" / f"{sha256(run_id.encode())}.json"
        return parse_json_object(path.read_text(encoding="utf-8")) if path.is_file() else None

    def reconcile(self) -> list[str]:
        interrupted: list[str] = []
        runs = self.root / "runs"
        for path in sorted(runs.glob("*.json")) if runs.is_dir() else []:
            snapshot = parse_json_object(path.read_text(encoding="utf-8"))
            if snapshot.get("status") != "running":
                continue
            identity = snapshot.get("process_identity")
            identity_object = _object(identity) if identity else None
            pid = identity_object.get("pid") if identity_object else None
            if identity_object and isinstance(pid, int) and not isinstance(pid, bool) and process_identity(pid) == identity_object:
                continue
            run_id = snapshot["run_id"]
            if not isinstance(run_id, str):
                raise ValueError("invalid run ID")
            payload: dict[str, JsonValue] = {
                "status": "interrupted",
                "error_class": "process_identity_lost",
                "message": "run owner is no longer active",
                "duration_seconds": 0.0,
            }
            if execution := snapshot.get("execution_snapshot"):
                execution_object = _object(execution)
                payload.update(
                    {
                        "execution_snapshot_fingerprint": snapshot.get("execution_snapshot_fingerprint", ""),
                        "policy_source": execution_object.get("policy_source", "current-policy"),
                        "tool_versions": {"codex": execution_object.get("codex_version", "")},
                    }
                )
            _ = self.emit("run.finished", run_id, payload)
            interrupted.append(run_id)
        return interrupted

    def replay(self, cursor: int, limit: int = 500, *, tail: bool = False) -> dict[str, JsonValue]:
        if not 1 <= limit <= 500:
            raise ValueError("event page limit must be 1..500")
        events = self.events(); earliest = _sequence(events[0]["sequence"]) if events else cursor + 1
        if events and cursor and cursor < earliest - 1:
            snapshots = [parse_json_object(path.read_text(encoding="utf-8")) for path in sorted((self.root / "runs").glob("*.json"))] if (self.root / "runs").is_dir() else []
            remaining = 500; bounded: list[dict[str, JsonValue]] = []
            for snapshot in reversed(snapshots):
                history = _objects(snapshot.get("events", []))[-remaining:]
                bounded.append({**snapshot, "events": history})
                remaining -= len(history)
                if remaining <= 0: break
            next_cursor = max((_sequence(event["sequence"]) for snapshot in bounded for event in _objects(snapshot.get("events", []))), default=earliest - 1)
            return {"resync_required": True, "earliest_sequence": earliest, "events": [], "snapshot": list(reversed(bounded)), "next_cursor": next_cursor}
        page = events[-limit:] if tail and cursor == 0 else [item for item in events if _sequence(item["sequence"]) > cursor][:limit]
        return {"resync_required": False, "earliest_sequence": earliest, "events": page, "next_cursor": _sequence(page[-1]["sequence"]) if page else cursor}

    def append_log(self, run_id: str, attempt: int, data: bytes) -> dict[str, JsonValue]:
        root = self.root / "logs" / sha256(run_id.encode()); root.mkdir(parents=True, exist_ok=True)
        data = str(redact(data.decode("utf-8", errors="replace"))).encode()
        path = root / f"{attempt}.log"; current = path.stat().st_size if path.exists() else 0
        accepted = max(0, min(len(data), LOG_LIMIT - current))
        with path.open("ab") as stream: _ = stream.write(data[:accepted])
        discarded = len(data) - accepted
        metadata_path = path.with_suffix(".json")
        try:
            previous = int(str(parse_json_object(metadata_path.read_text(encoding="utf-8"))["discarded_bytes"]))
        except (FileNotFoundError, OSError, ValueError, KeyError, json.JSONDecodeError):
            previous = 0
        atomic_json(metadata_path, {"schema_version": "1", "discarded_bytes": previous + discarded})
        return {"path": str(path.relative_to(self.root)), "accepted_bytes": accepted, "discarded_bytes": previous + discarded}

    def read_log(self, run_id: str, attempt: int, offset: int = 0, limit: int = 500) -> dict[str, JsonValue]:
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("log page must use non-negative offset and limit 1..500")
        path = self.root / "logs" / sha256(run_id.encode()) / f"{attempt}.log"
        if not path.is_file():
            return {"offset": offset, "lines": [], "has_more": False}
        metadata = path.with_suffix(".json")
        try:
            discarded = int(str(parse_json_object(metadata.read_text(encoding="utf-8"))["discarded_bytes"]))
        except (FileNotFoundError, OSError, ValueError, KeyError, json.JSONDecodeError):
            discarded = 0
        with path.open(encoding="utf-8", errors="replace") as stream:
            skipped = sum(1 for _ in itertools.islice(stream, offset))
            lines = list(itertools.islice(stream, limit + 1)) if skipped == offset else []
        if discarded and len(lines) <= limit:
            lines.append(f"[log truncated: {discarded} bytes discarded]\n")
        return {"offset": offset, "lines": lines[:limit], "has_more": len(lines) > limit}
