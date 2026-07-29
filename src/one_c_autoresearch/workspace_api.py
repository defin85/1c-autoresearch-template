from __future__ import annotations

import base64
import json
import os
import fcntl
import re
import shutil
import sqlite3
import subprocess
import time
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from .contracts import canonical_json, confined, repository_lock, sha256
from .events import EventStore, RETRYABLE_RUN_STATUSES
from .service import ApplicationService


def _encode_dispatcher_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(canonical_json(payload)).decode().rstrip("=")


def _decode_dispatcher_cursor(value: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(value + padding).decode("utf-8")
        )
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid dispatcher cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid dispatcher cursor")
    return payload


def _history_cursor(value: str, scope: dict[str, str]) -> tuple[str, str]:
    cursor = _decode_dispatcher_cursor(value)
    if (
        set(cursor) != {"scope", "created_at", "invocation_id"}
        or cursor.get("scope") != scope
        or not isinstance(cursor.get("created_at"), str)
        or not isinstance(cursor.get("invocation_id"), str)
        or not cursor["invocation_id"]
    ):
        raise ValueError("dispatcher history cursor scope mismatch")
    try:
        datetime.fromisoformat(cursor["created_at"])
    except ValueError as exc:
        raise ValueError("invalid dispatcher history cursor") from exc
    return cursor["created_at"], cursor["invocation_id"]


def _event_cursor(value: str, scope: dict[str, str]) -> int:
    cursor = _decode_dispatcher_cursor(value)
    sequence = cursor.get("last_sequence")
    if (
        set(cursor) != {"scope", "last_sequence"}
        or cursor.get("scope") != scope
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
    ):
        raise ValueError("dispatcher event cursor scope mismatch")
    return sequence


def _context_cursor(value: str, scope: dict[str, str]) -> int:
    cursor = _decode_dispatcher_cursor(value)
    offset = cursor.get("offset")
    if (
        set(cursor) != {"scope", "offset"}
        or cursor.get("scope") != scope
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
    ):
        raise ValueError("dispatcher context cursor scope mismatch")
    return offset


def _public_invocation(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    provenance = result.pop("context_provenance", [])
    result.pop("context_diagnostics", None)
    result["context_provenance_count"] = (
        len(provenance) if isinstance(provenance, list) else 0
    )
    if result.get("result_ref"):
        result["result_ref"] = {
            "kind": "node-result",
            "id": result["result_ref"],
        }
    return result


def _safe_result_summary(payload: dict[str, Any]) -> dict[str, Any]:
    from .events import redact
    envelope = payload.get("envelope")
    if isinstance(envelope, dict):
        payload = {**envelope, **({"name": payload["name"]} if isinstance(payload.get("name"), str) else {})}
    allowed = {
        "classification", "confidence", "semantic_key", "title", "decision",
        "risk", "group_count", "batch_count", "stable_diff_count",
        "supporting_diff_count", "evidence_count", "target_evidence_count",
        "target_coverage_count", "open_question_count", "name",
    }
    summary = {
        key: (str(redact(value))[:256] if isinstance(value, str) else value)
        for key, value in payload.items()
        if key in allowed and isinstance(value, (str, int, float, bool))
    }
    for source, target in (
        ("stable_diff_ids", "stable_diff_count"),
        ("supporting_diff_ids", "supporting_diff_count"),
        ("evidence", "evidence_count"),
        ("groups", "group_count"),
        ("batches", "batch_count"),
        ("target_evidence", "target_evidence_count"),
        ("target_coverage", "target_coverage_count"),
        ("open_questions", "open_question_count"),
    ):
        if isinstance(payload.get(source), list):
            summary[target] = len(payload[source])
    return summary


def codex_capabilities() -> dict[str, Any]:
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("codex executable is unavailable")
    result = subprocess.run(
        [executable, "debug", "models"],
        capture_output=True,
        check=True,
        text=True,
        timeout=15,
    )
    catalog = json.loads(result.stdout)
    from .user_state import codex_model_capabilities
    context = codex_model_capabilities()
    models = [
        {
            "id": item["slug"],
            "name": item.get("display_name") or item["slug"],
            "default_reasoning_effort": item["default_reasoning_level"],
            "reasoning_efforts": [
                level["effort"] for level in item.get("supported_reasoning_levels", [])
            ],
            **context.get(item["slug"], {}),
        }
        for item in catalog.get("models", [])
        if item.get("visibility") == "list" and item.get("supported_in_api", True)
    ]
    if not models:
        raise RuntimeError("codex returned no available models")
    return {"provider": "codex-cli", "models": models}


def _reviewed_noise_is_active(canonical: dict[str, Any], reviewed: list[dict[str, Any]]) -> bool:
    for expected in reviewed:
        noise = {key: expected[key] for key in ("rationale", "actor", "evidence")}
        disposition = next(
            (
                item
                for item in canonical["dispositions.jsonl"]
                if item.get("stable_diff_id") == expected["stable_diff_id"]
                and item.get("primary") is True
                and item.get("approved_noise") == noise
            ),
            None,
        )
        if disposition is None or not any(
            item.get("event") == "approve"
            and item.get("target_id") == expected["stable_diff_id"]
            and item.get("actor") == expected["actor"]
            and item.get("rationale") == expected["rationale"]
            and item.get("evidence") == expected["evidence"]
            and item.get("timestamp") == expected["timestamp"]
            and item.get("fingerprint") == sha256(canonical_json(noise))
            and item.get("source_generation_id") == disposition.get("source_generation_id")
            and item.get("diff_generation_id") == disposition.get("diff_generation_id")
            for item in canonical["approvals.jsonl"]
        ):
            return False
    return True


def _imports() -> dict[str, Any]:
    try:
        from fastapi import Body, FastAPI, Header, HTTPException, Request
        from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
        from fastapi.staticfiles import StaticFiles
        from pydantic import BaseModel, Field
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install one-c-autoresearch[workspace]") from exc
    return locals()


web = _imports(); FastAPI = web["FastAPI"]; HTTPException = web["HTTPException"]; Request = web["Request"]; JSONResponse = web["JSONResponse"]; FileResponse = web["FileResponse"]; StreamingResponse = web["StreamingResponse"]; BaseModel = web["BaseModel"]; Field = web["Field"]; StaticFiles = web["StaticFiles"]; Body = web["Body"]


class ActionBody(BaseModel):
    operation: str
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_fingerprint: str


class RunBody(BaseModel):
    expected_fingerprint: str
    approved_operations: list[str] = Field(default_factory=list)
    source_routing_preview_id: str | None = None
    routing_plan_fingerprint: str | None = None
    max_units: int = Field(default=100, ge=1, le=1000)


class CancelBody(BaseModel):
    actor: str = Field(min_length=1, max_length=200)


class StepPatchBody(BaseModel):
    step_id: str
    parameters: dict[str, Any]
    expected_manifest_fingerprint: str


class BookmarkBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    root: str


class ConnectionBody(BaseModel):
    profile: dict[str, Any]


class AgentProfileBody(BaseModel):
    profile: dict[str, Any]


class StagePreviewBody(BaseModel):
    model_config = {"extra": "forbid"}
    boundary: str = Field(max_length=32)
    expected_workflow_fingerprint: str = Field(min_length=1, max_length=200)
    source_routing_preview_id: str | None = Field(default=None, max_length=200)


class StageRunBody(BaseModel):
    model_config = {"extra": "forbid"}
    boundary: str = Field(max_length=32)
    workflow_fingerprint: str = Field(min_length=1, max_length=200)
    plan_fingerprint: str = Field(min_length=1, max_length=200)
    confirmations: list[str] = Field(max_length=8)
    predecessor_run_id: str | None = Field(default=None, max_length=200)


class DispatcherActionBody(BaseModel):
    model_config = {"extra": "forbid"}
    actor: str = Field(default="local-user", min_length=1, max_length=200)
    timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)
    proposal_key: str | None = Field(default=None, max_length=200)
    expected_fingerprint: str | None = Field(default=None, max_length=200)
    payload: dict[str, Any] | None = None
    policy_source: Literal["reuse-snapshot", "current-policy"] | None = None
    predecessor_run_id: str | None = Field(default=None, max_length=200)
    expected_workflow_fingerprint: str | None = Field(default=None, max_length=200)
    expected_input_fingerprints: dict[str, str] | None = None


def create_app(state_root: Path | None = None, approved_roots: list[Path] | None = None, testing: bool = False, connection_tester=None):
    operational = (state_root or (Path.home() / ".local/state/one-c-autoresearch")).resolve()
    operational.mkdir(parents=True, exist_ok=True, mode=0o700)
    roots = [item.resolve() for item in (approved_roots or [Path.cwd()])]
    bookmarks_path = operational / "bookmarks.json"
    dispatcher_sessions: dict[tuple[str, str], tuple[Any, Any]] = {}
    dispatcher_sessions_lock = threading.RLock()
    folder_streams: dict[str, threading.BoundedSemaphore] = {}
    stage_cancellations: dict[str, threading.Event] = {}
    stage_cancellations_lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        with dispatcher_sessions_lock:
            sessions = list(dispatcher_sessions.values())
            dispatcher_sessions.clear()
        for coordinator, store in sessions:
            coordinator.close()
            store.close()

    app = FastAPI(title="1C Autoresearch Workspace", version="1", lifespan=lifespan)

    def bookmarks() -> list[dict[str, str]]:
        if not bookmarks_path.is_file(): return []
        return json.loads(bookmarks_path.read_text(encoding="utf-8"))

    def save_bookmarks(values: list[dict[str, str]]) -> None:
        temporary = bookmarks_path.with_suffix(".tmp"); temporary.write_text(json.dumps(values, ensure_ascii=False, sort_keys=True), encoding="utf-8"); os.replace(temporary, bookmarks_path)

    def repo(project_id: str) -> Path:
        item = next((value for value in bookmarks() if value["id"] == project_id), None)
        if not item: raise HTTPException(404, "project bookmark not found")
        root = Path(item["root"]).resolve()
        if not any(root == allowed or allowed in root.parents for allowed in roots):
            raise HTTPException(409, "stale or unauthorized project bookmark")
        try:
            from .workflow import validate_project_contract, validate_workflow
            validate_project_contract(root); validate_workflow(root)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(409, "unsupported repository contract; recreate the repository instead of migrating it") from exc
        return confined(root, ".")

    def mutation(request: Request, idempotency_key: str | None) -> None:
        expected = f"{request.url.scheme}://{request.headers.get('host')}"
        if request.headers.get("origin") != expected: raise HTTPException(403, "origin validation failed")
        if not idempotency_key or len(idempotency_key) > 200: raise HTTPException(400, "valid Idempotency-Key is required")

    def same_origin(request: Request) -> None:
        expected = f"{request.url.scheme}://{request.headers.get('host')}"
        if request.headers.get("origin") != expected:
            raise HTTPException(403, "origin validation failed")

    @app.middleware("http")
    async def security(request: Request, call_next):
        host = request.headers.get("host", "").split(":", 1)[0]
        if host not in {"127.0.0.1", "localhost", "testserver"}: return JSONResponse({"detail": "invalid host"}, status_code=400)
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        response.headers["X-Content-Type-Options"] = "nosniff"; response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(ValueError)
    async def invalid(_request: Request, exc: ValueError):
        from .events import redact
        return JSONResponse({"detail": redact(str(exc)), "code": "validation_error"}, status_code=422)
    @app.exception_handler(RuntimeError)
    async def conflict(_request: Request, exc: RuntimeError):
        from .events import redact
        return JSONResponse({"detail": redact(str(exc)), "code": "conflict"}, status_code=409)

    @app.get("/api/v1/health")
    def health(): return {"status": "ok", "schema_version": "1"}

    @app.get("/api/v1/projects")
    def projects(): return bookmarks()

    @app.post("/api/v1/projects", status_code=201)
    def add_project(body: BookmarkBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); root = confined(Path(body.root).resolve(), ".")
        if not any(root == allowed or allowed in root.parents for allowed in roots): raise ValueError("unsupported repository path")
        identifier = sha256(str(root).encode())[:16]; values = [item for item in bookmarks() if item["id"] != identifier]; item = {"id": identifier, "name": body.name, "root": str(root)}; values.append(item); save_bookmarks(values); return item

    @app.get("/api/v1/projects/{project_id}/workflow")
    def snapshot(project_id: str):
        from .user_state import load_connections
        from .workflow import attach_dispatcher
        project = repo(project_id)
        return attach_dispatcher(
            ApplicationService(project, connections=load_connections(project, operational)).snapshot(deep=False),
            project,
            operational,
        )

    @app.get("/api/v1/projects/{project_id}/workflow/next")
    def next_work(project_id: str):
        from .user_state import load_connections
        project = repo(project_id)
        return ApplicationService(project, connections=load_connections(project, operational)).next()

    @app.get("/api/v1/projects/{project_id}/workflow/configuration")
    def workflow_configuration(project_id: str): return ApplicationService(repo(project_id)).workflow_configuration()

    @app.post("/api/v1/projects/{project_id}/workflow/patch-preview")
    def workflow_patch_preview(project_id: str, body: StepPatchBody):
        project = repo(project_id)
        from .workflow_migration import guard_mutation
        guard_mutation(project, base=operational)
        return ApplicationService(project).preview_step_patch(body.model_dump(), operational)

    @app.post("/api/v1/projects/{project_id}/workflow/run-next")
    def run_next_step(project_id: str, body: RunBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); project = repo(project_id)
        from .workflow_migration import guard_mutation
        guard_mutation(project, base=operational)
        from .user_state import load_agent_profiles, load_connections
        preview_root = operational / "projects" / project_id / "source-routing-previews"
        service = ApplicationService(project, connections=load_connections(project, operational), upload_drafts=operational / "projects" / project_id / "upload-drafts", routing_previews=preview_root)
        from .runner import run_next
        store = EventStore(operational / "projects", project_id)
        result_path = store.root / ("idempotency-" + sha256(idempotency_key.encode()) + ".json")
        lock_path = result_path.with_suffix(".lock")
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8")); result["snapshot"] = service.snapshot(); return result
            if body.expected_fingerprint != service.snapshot()["workflow_fingerprint"]: raise RuntimeError("stale workflow fingerprint")
            invoke = lambda operation, payload, cancelled: service.apply(operation, payload, service.snapshot()["workflow_fingerprint"], cancelled)
            if set(body.approved_operations) - {"sources.acquire", "dif.classify-next", "mrq.consolidate", "mrq.decide-next"}: raise ValueError("unsupported run approval")
            source_preview = {"source_routing_preview_id": body.source_routing_preview_id, "routing_plan_fingerprint": body.routing_plan_fingerprint} if body.source_routing_preview_id and body.routing_plan_fingerprint else None
            result = run_next(service.repo, invoke, store, select=service.next, approved_operations=set(body.approved_operations), agent_profiles=load_agent_profiles(project, operational), source_routing_preview=source_preview)
            from .contracts import atomic_json
            atomic_json(result_path, result)
            return result

    @app.post("/api/v1/projects/{project_id}/source-acquisition-runs", status_code=202)
    def start_source_acquisition(project_id: str, body: RunBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); project = repo(project_id)
        from .workflow_migration import guard_mutation
        guard_mutation(project, base=operational)
        if set(body.approved_operations) != {"sources.acquire"} or not body.source_routing_preview_id or not body.routing_plan_fingerprint:
            raise ValueError("source acquisition approval and routing preview are required")
        from .user_state import load_agent_profiles, load_connections
        from .runner import run_next, run_until_blocked
        run_id = str(uuid.uuid4())
        store = EventStore(operational / "projects", project_id)
        preview_root = operational / "projects" / project_id / "source-routing-previews"
        def report(value: dict[str, Any]):
            store.emit("step.progress", run_id, {"status": "running", "progress": value}, job_id="acquire-sources", step_id="acquire-sources", attempt=1)
        service = ApplicationService(project, connections=load_connections(project, operational), upload_drafts=operational / "projects" / project_id / "upload-drafts", routing_previews=preview_root, progress=report)
        expected_fingerprint = service.snapshot(deep=False)["workflow_fingerprint"]
        if body.expected_fingerprint != expected_fingerprint:
            raise RuntimeError("stale workflow fingerprint")
        source_preview = {"source_routing_preview_id": body.source_routing_preview_id, "routing_plan_fingerprint": body.routing_plan_fingerprint}
        source_work = {"action": "sources.acquire", "job_id": "acquire-sources", "gate_id": "sources-acquired", "blocker": {"code": "sources.refresh_requested", "message": "source refresh requested by local user", "action": "sources.acquire"}}
        from .events import process_identity
        store.emit("run.created", run_id, {"status": "running", "actor": "local-user", "process_identity": process_identity(), "workflow_fingerprint": expected_fingerprint, "operation": "sources.acquire", "work_unit": source_work})
        report({"phase": "queued", "completed": 0, "total": 0, "subject": ""})
        def worker():
            completed = False
            def invoke(operation, payload, cancelled):
                nonlocal completed
                result = service.apply(operation, payload, expected_fingerprint, cancelled)
                completed = True
                return result
            try:
                result = run_next(service.repo, invoke, store, select=lambda: None if completed else source_work, approved_operations={"sources.acquire"}, agent_profiles=load_agent_profiles(project, operational), source_routing_preview=source_preview, run_id=run_id, precreated=True)
                if result["result"] == "progressed":
                    run_until_blocked(
                        service.repo,
                        lambda operation, payload, cancelled: service.apply(
                            operation,
                            payload,
                            service.snapshot(deep=False)["workflow_fingerprint"],
                            cancelled,
                        ),
                        store,
                        max_units=3,
                        select=service.next,
                        agent_profiles=load_agent_profiles(project, operational),
                    )
            except Exception:
                pass
        threading.Thread(target=worker, name=f"source-acquisition-{run_id}", daemon=True).start()
        return {"run_id": run_id, "status": "running"}

    @app.get("/api/v1/projects/{project_id}/source-acquisition-runs/{run_id}")
    def source_acquisition_status(project_id: str, run_id: str):
        repo(project_id)
        if not re.fullmatch(r"[0-9a-f-]{36}", run_id):
            raise HTTPException(404, "source acquisition run not found")
        events = [event for event in EventStore(operational / "projects", project_id).events() if event["run_id"] == run_id]
        if not events:
            raise HTTPException(404, "source acquisition run not found")
        finished = next((event for event in reversed(events) if event["type"] == "run.finished"), None)
        progress = next((event["payload"]["progress"] for event in reversed(events) if event["type"] == "step.progress"), {"phase": "queued", "completed": 0, "total": 0, "subject": ""})
        payload = finished["payload"] if finished else {}
        return {"run_id": run_id, "status": payload.get("status", "running"), "progress": progress, "error": payload.get("message", "")}

    @app.post("/api/v1/projects/{project_id}/workflow/run-until-blocked")
    def run_until(project_id: str, body: RunBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); project = repo(project_id)
        from .workflow_migration import guard_mutation
        guard_mutation(project, base=operational)
        from .user_state import load_agent_profiles, load_connections
        preview_root = operational / "projects" / project_id / "source-routing-previews"
        service = ApplicationService(project, connections=load_connections(project, operational), upload_drafts=operational / "projects" / project_id / "upload-drafts", routing_previews=preview_root)
        if set(body.approved_operations) - {"sources.acquire", "dif.classify-next", "mrq.consolidate", "mrq.decide-next"}: raise ValueError("unsupported run approval")
        from .runner import run_until_blocked
        store = EventStore(operational / "projects", project_id)
        result_path = store.root / ("idempotency-until-" + sha256(idempotency_key.encode()) + ".json")
        with result_path.with_suffix(".lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8")); result["snapshot"] = service.snapshot(); return result
            if body.expected_fingerprint != service.snapshot()["workflow_fingerprint"]: raise RuntimeError("stale workflow fingerprint")
            invoke = lambda operation, payload, cancelled: service.apply(operation, payload, service.snapshot()["workflow_fingerprint"], cancelled)
            source_preview = {"source_routing_preview_id": body.source_routing_preview_id, "routing_plan_fingerprint": body.routing_plan_fingerprint} if body.source_routing_preview_id and body.routing_plan_fingerprint else None
            result = run_until_blocked(service.repo, invoke, store, max_units=body.max_units, select=service.next, approved_operations=set(body.approved_operations), agent_profiles=load_agent_profiles(project, operational), source_routing_preview=source_preview)
            from .contracts import atomic_json
            atomic_json(result_path, result)
            return result

    @app.post("/api/v1/projects/{project_id}/runs/{run_id}/cancel")
    def cancel_run(project_id: str, run_id: str, body: CancelBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); project = repo(project_id)
        from .workflow_migration import guard_mutation
        guard_mutation(project, base=operational)
        EventStore(operational / "projects", project_id).cancel(run_id, body.actor)
        return {"run_id": run_id, "status": "cancellation_requested"}

    @app.get("/api/v1/projects/{project_id}/dispatcher")
    def dispatcher_projection(project_id: str):
        from .user_state import load_connections
        project = repo(project_id)
        from .sqlite_state import DispatcherStore
        try:
            with DispatcherStore(project, operational) as store:
                store.reconcile_invocation_outbox(
                    EventStore(operational / "projects", project_id)
                )
        except (OSError, RuntimeError, sqlite3.DatabaseError):
            raise HTTPException(503, "dispatcher operational store unavailable")
        from .workflow import attach_dispatcher
        try:
            snapshot = attach_dispatcher(
                ApplicationService(project, connections=load_connections(project, operational)).snapshot(deep=False),
                project,
                operational,
            )
        except sqlite3.DatabaseError:
            raise HTTPException(503, "dispatcher operational store unavailable")
        return snapshot.get("dispatcher", {"schema_version": "1", "revision": 0, "fresh_at": "", "circuits": [], "jobs": {}})

    @app.get("/api/v1/projects/{project_id}/dispatcher/inspect")
    def dispatcher_inspect(
        project_id: str,
        kind: str,
        invocation_id: str = "",
        phase_id: str = "",
        role_id: str = "",
        slot_id: str = "",
        run_id: str = "",
        history_limit: int = 20,
        event_limit: int = 50,
        context_limit: int = 50,
        history_cursor: str = "",
        event_cursor: str = "",
        context_cursor: str = "",
    ):
        if kind not in {"invocation", "slot"}:
            raise ValueError("invalid dispatcher inspection kind")
        if (
            not 1 <= history_limit <= 100
            or not 1 <= event_limit <= 200
            or not 1 <= context_limit <= 100
        ):
            raise ValueError("invalid dispatcher inspection limit")
        if history_cursor:
            _decode_dispatcher_cursor(history_cursor)
        if event_cursor:
            _decode_dispatcher_cursor(event_cursor)
        if context_cursor:
            _decode_dispatcher_cursor(context_cursor)
        if kind == "invocation":
            if not invocation_id or any((phase_id, role_id, slot_id, run_id)):
                raise ValueError("invocation inspection requires only invocation_id")
        elif invocation_id or not all((phase_id, role_id, slot_id)):
            raise ValueError("slot inspection requires phase_id, role_id and slot_id")
        project = repo(project_id)
        from .sqlite_state import DispatcherStore
        try:
            store = DispatcherStore(project, operational)
            store.open()
            store.reconcile_invocation_outbox(
                EventStore(operational / "projects", project_id)
            )
        except (OSError, RuntimeError, sqlite3.DatabaseError):
            raise HTTPException(503, "dispatcher operational store unavailable")
        try:
            projection = dispatcher_projection(project_id)
            selected_slot = next(
                (
                    slot
                    for phase in projection.get("agent_phases", [])
                    if phase.get("phase_id") == phase_id
                    for role in phase.get("roles", [])
                    if role.get("role_id") == role_id
                    for slot in role.get("slots", [])
                    if slot.get("slot_id") == slot_id
                ),
                None,
            )
            invocation = store.invocation(invocation_id) if invocation_id else None
            if kind == "invocation":
                if invocation is None:
                    raise HTTPException(404, "dispatcher invocation not found")
                phase_id = str(invocation["phase_id"])
                role_id = str(invocation["role_id"])
                slot_id = str(invocation["slot_id"])
                run_id = str(invocation["run_id"])
            elif selected_slot is None:
                raise HTTPException(404, "dispatcher slot not found")
            elif not run_id:
                run_id = str(selected_slot.get("run_id") or "")

            history_scope = {
                "project_id": project_id,
                "run_id": run_id,
                "phase_id": phase_id,
                "role_id": role_id,
                "slot_id": slot_id,
            }
            before = None
            if history_cursor:
                before = _history_cursor(history_cursor, history_scope)
            history = (
                store.invocation_history(
                    run_id, phase_id, role_id, slot_id, history_limit + 1, before
                )
                if run_id
                else []
            )
            history_truncated = len(history) > history_limit
            history = history[:history_limit]
            next_history_cursor = None
            if history_truncated:
                last = history[-1]
                next_history_cursor = _encode_dispatcher_cursor({
                    "scope": history_scope,
                    "created_at": last["created_at"],
                    "invocation_id": last["invocation_id"],
                })
            if kind == "slot" and invocation is None:
                invocation = next(
                    (item for item in history if item["status"] == "running"),
                    history[0] if history else None,
                )
            history = [_public_invocation(item) for item in history]

            event_items: list[dict[str, Any]] = []
            events_available = bool(invocation and run_id)
            events_truncated = False
            next_event_cursor = None
            if event_cursor and not invocation:
                raise ValueError("event cursor requires an invocation")
            if events_available:
                event_scope = {
                    "project_id": project_id,
                    "run_id": run_id,
                    "invocation_id": invocation["invocation_id"],
                }
                last_sequence = 0
                if event_cursor:
                    last_sequence = _event_cursor(event_cursor, event_scope)
                snapshot = EventStore(
                    operational / "projects", project_id
                ).run_snapshot(run_id)
                events_available = snapshot is not None
                if snapshot is not None:
                    correlated = [
                        event for event in snapshot.get("events", [])
                        if event.get("payload", {}).get("invocation_id")
                        == invocation["invocation_id"]
                    ]
                    retained = [
                        event for event in correlated
                        if event.get("sequence", 0) > last_sequence
                    ]
                    retained.sort(key=lambda event: event["sequence"])
                    has_started = any(
                        event["type"] == "invocation.started"
                        for event in correlated
                    )
                    page_truncated = len(retained) > event_limit
                    events_truncated = page_truncated or not has_started
                    event_items = retained[:event_limit]
                    if page_truncated:
                        next_event_cursor = _encode_dispatcher_cursor({
                            "scope": event_scope,
                            "last_sequence": event_items[-1]["sequence"],
                        })

            execution_available = False
            execution_identity = None
            result = {"available": False}
            duration = None
            if invocation:
                snapshot = EventStore(
                    operational / "projects", project_id
                ).run_snapshot(str(invocation["run_id"]))
                execution = (snapshot or {}).get("execution_snapshot", {})
                profile = (execution.get("profiles") or {}).get(
                    invocation.get("profile_id")
                )
                matching_role = next(
                    (
                        role
                        for phase in execution.get("agent_phases", [])
                        if phase.get("phase_id") == invocation.get("phase_id")
                        for role in phase.get("roles", [])
                        if role.get("role_id") == invocation.get("role_id")
                        and role.get("agent_profile") == invocation.get("profile_id")
                    ),
                    None,
                )
                execution_available = bool(
                    snapshot
                    and snapshot.get("execution_snapshot_fingerprint")
                    == invocation.get("execution_snapshot_fingerprint")
                    and isinstance(profile, dict)
                    and matching_role is not None
                )
                if execution_available:
                    execution_identity = {
                        "profile_id": invocation.get("profile_id"),
                        "model": profile.get("model"),
                        "reasoning_effort": profile.get("reasoning_effort"),
                        "environment_preset": profile.get("environment_preset"),
                        "subject_bindings": {
                            key: (execution.get("subject_bindings") or {}).get(key, "")
                            for key in (
                                "source_generation_id",
                                "diff_generation_id",
                                "canonical_generation_id",
                            )
                        },
                    }
                if invocation.get("result_ref"):
                    proposal = store.proposal(str(invocation["result_ref"]))
                    if proposal is not None and proposal.get("kind") != "node-result":
                        proposal = None
                    result = {
                        "available": proposal is not None,
                        "reference": {
                            "kind": "node-result",
                            "id": invocation["result_ref"],
                        },
                        **(
                            {"summary": _safe_result_summary(proposal["payload"])}
                            if proposal is not None
                            else {}
                        ),
                    }
                try:
                    start = datetime.fromisoformat(str(invocation["created_at"]))
                    end = datetime.fromisoformat(
                        str(invocation.get("finished_at") or datetime.now(timezone.utc).isoformat())
                    )
                    duration = max(0.0, (end - start).total_seconds())
                except ValueError:
                    duration = None

            idle_reason = (
                selected_slot.get("idle_reason_code", "none")
                if selected_slot
                else "none"
            )
            public_invocation = dict(invocation) if invocation else None
            context = {
                "available": False,
                "summary": None,
                "prepared_input_fingerprint": None,
                "envelope_fingerprint": None,
                "provenance": {
                    "items": [],
                    "truncated": False,
                    "next_cursor": None,
                },
            }
            if invocation and invocation.get("context_envelope_fingerprint"):
                context_scope = {
                    "project_id": project_id,
                    "invocation_id": str(invocation["invocation_id"]),
                }
                offset = (
                    _context_cursor(context_cursor, context_scope)
                    if context_cursor
                    else 0
                )
                provenance = invocation.get("context_provenance") or []
                page = provenance[offset:offset + context_limit]
                truncated = offset + len(page) < len(provenance)
                context = {
                    "available": True,
                    "summary": invocation.get("context_diagnostics") or {},
                    "prepared_input_fingerprint": invocation.get(
                        "prepared_input_fingerprint"
                    ),
                    "envelope_fingerprint": invocation.get(
                        "context_envelope_fingerprint"
                    ),
                    "provenance": {
                        "items": page,
                        "truncated": truncated,
                        "next_cursor": (
                            _encode_dispatcher_cursor({
                                "scope": context_scope,
                                "offset": offset + len(page),
                            })
                            if truncated
                            else None
                        ),
                    },
                }
            public_invocation = (
                _public_invocation(invocation) if invocation else None
            )
            return {
                "kind": kind,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "slot": {
                    "phase_id": phase_id,
                    "role_id": role_id,
                    "slot_id": slot_id,
                    "run_id": run_id or None,
                    "idle_reason_code": idle_reason,
                    "assignment_state": "assigned" if invocation else "idle",
                },
                "invocation": public_invocation,
                "duration_seconds": duration,
                "execution_identity_available": execution_available,
                "execution_identity": execution_identity,
                "result": result,
                "context": context,
                "history": {
                    "available": bool(run_id),
                    "items": history,
                    "truncated": history_truncated,
                    "next_cursor": next_history_cursor,
                },
                "events": {
                    "available": events_available,
                    "items": event_items,
                    "truncated": events_truncated,
                    "next_cursor": next_event_cursor,
                },
            }
        except sqlite3.DatabaseError:
            raise HTTPException(503, "dispatcher operational store unavailable")
        finally:
            store.close()

    def stage_preview(project: Path, body: StagePreviewBody, *, require_source_preview: bool = True) -> dict[str, Any]:
        if body.boundary not in {"sources", "diffs", "projections"}:
            raise ValueError("unsupported stage recompute boundary")
        if require_source_preview and body.boundary == "sources" and not body.source_routing_preview_id:
            raise ValueError("source_routing_preview_id is required for sources")
        if body.boundary != "sources" and body.source_routing_preview_id is not None:
            raise ValueError("source_routing_preview_id is only valid for sources")
        from .stage_recompute import preview
        return preview(
            project,
            boundary=body.boundary,
            expected_workflow_fingerprint=body.expected_workflow_fingerprint,
            source_routing_preview_id=body.source_routing_preview_id,
            operational_root=operational,
        )

    @app.post("/api/v1/projects/{project_id}/stage-recompute/preview")
    def preview_stage_recompute(project_id: str, body: StagePreviewBody, request: Request):
        same_origin(request)
        return stage_preview(repo(project_id), body)

    @app.post("/api/v1/projects/{project_id}/stage-recompute/runs", status_code=202)
    def run_stage_recompute(project_id: str, body: StageRunBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key)
        project = repo(project_id)
        from .workflow_migration import guard_mutation
        guard_mutation(project, base=operational)
        candidates = [None]
        if body.boundary == "sources":
            preview_root = operational / "projects" / project_id / "source-routing-previews"
            candidates = [path.stem for path in sorted(preview_root.glob("*.json"))]
        plan = None
        for preview_id in candidates:
            try:
                candidate = stage_preview(
                    project,
                    StagePreviewBody(
                        boundary=body.boundary,
                        expected_workflow_fingerprint=body.workflow_fingerprint,
                        source_routing_preview_id=preview_id,
                    ),
                    require_source_preview=False,
                )
            except RuntimeError:
                continue
            if candidate.get("plan_fingerprint") == body.plan_fingerprint:
                plan = candidate
                break
        if plan is None:
            raise RuntimeError("stale stage recompute plan fingerprint")
        if plan.get("plan_fingerprint") != body.plan_fingerprint:
            raise RuntimeError("stale stage recompute plan fingerprint")
        required = plan["required_confirmations"]
        if sorted(body.confirmations) != sorted(required) or len(set(body.confirmations)) != len(body.confirmations):
            raise ValueError("confirmations must exactly match the stage recompute plan")
        from .contracts import canonical_json
        request_value = {
            "project_id": project_id,
            "boundary": body.boundary,
            "workflow_fingerprint": body.workflow_fingerprint,
            "plan_fingerprint": body.plan_fingerprint,
            "confirmations": sorted(body.confirmations),
            "predecessor_run_id": body.predecessor_run_id,
        }
        request_fingerprint = "sha256:" + sha256(canonical_json(request_value))
        from .sqlite_state import DispatcherStore
        with DispatcherStore(project, operational) as store:
            record, created = store.start_stage_recompute(
                str(idempotency_key),
                request_fingerprint,
                body.boundary,
                plan,
                body.predecessor_run_id,
            )
            predecessor = store.stage_run(body.predecessor_run_id) if body.predecessor_run_id else None
        public = {key: record[key] for key in ("run_id", "status", "boundary", "idempotency_key_fingerprint")}
        public["plan_fingerprint"] = plan["plan_fingerprint"]
        if not created:
            if record.get("result") is not None:
                public["result"] = record["result"]
            return public

        run_id = record["run_id"]
        lease_token = record["lease_token"]
        cancelled = threading.Event()
        with stage_cancellations_lock:
            stage_cancellations[run_id] = cancelled
        event_store = EventStore(operational / "projects", project_id)
        from .events import process_identity
        event_store.emit(
            "run.created",
            run_id,
            {
                "status": "running",
                "actor": "local-user",
                "process_identity": process_identity(),
                "workflow_fingerprint": body.workflow_fingerprint,
                "operation": "stage-recompute",
                "boundary": body.boundary,
                "plan_fingerprint": plan["plan_fingerprint"],
                "idempotency_key_fingerprint": record["idempotency_key_fingerprint"],
            },
        )

        def worker() -> None:
            started = time.monotonic()
            status = "failed"
            result: dict[str, Any]
            try:
                from .stage_recompute import execute
                import inspect
                def emit(event_type: str, value: dict[str, Any]):
                    step_id = str(value["step_id"])
                    operation = str(value["operation"])
                    common = {
                        "boundary": body.boundary,
                        "plan_fingerprint": plan["plan_fingerprint"],
                        "idempotency_key_fingerprint": record["idempotency_key_fingerprint"],
                    }
                    hierarchy = {"job_id": "stage-recompute", "step_id": step_id, "attempt": 1}
                    if event_type == "step.started":
                        with repository_lock(project), DispatcherStore(project, operational) as progress_store:
                            if not progress_store.renew_stage_lease(run_id, lease_token, {**common, "current_step": operation, "step_id": step_id}):
                                raise RuntimeError("stage recompute lease lost")
                            return event_store.emit(
                                "step.started",
                                run_id,
                                {
                                    **common,
                                    "status": "running",
                                    "actor": "local-user",
                                    "operation": operation,
                                    "inputs": {"boundary": body.boundary},
                                    "input_fingerprint": value["input_fingerprint"],
                                },
                                **hierarchy,
                            )
                    if lease_is_lost():
                        raise RuntimeError("stage recompute lease lost")
                    result = value.get("result", {})
                    def fenced_emit(kind: str, payload: dict[str, Any]):
                        with repository_lock(project), DispatcherStore(project, operational) as progress_store:
                            if not progress_store.renew_stage_lease(run_id, lease_token):
                                raise RuntimeError("stage recompute lease lost")
                            return event_store.emit(kind, run_id, payload, **hierarchy)

                    fenced_emit(
                        "step.output",
                        {**common, "status": "completed", "outputs": result, "output_fingerprint": value["output_fingerprint"]},
                    )
                    fenced_emit(
                        "step.validation",
                        {**common, "status": "completed", "validation": "passed"},
                    )
                    return fenced_emit(
                        "step.finished",
                        {**common, "status": "completed", "actor": "local-user", "operation": operation, "duration_seconds": 0},
                    )
                def lease_is_lost() -> bool:
                    if cancelled.is_set():
                        return True
                    with DispatcherStore(project, operational) as check_store:
                        return not check_store.renew_stage_lease(run_id, lease_token)
                arguments = {
                    "lease_token": lease_token,
                    "cancelled": lease_is_lost,
                    "emit": emit,
                    "operational_root": operational,
                }
                if predecessor is not None:
                    if "predecessor_run" not in inspect.signature(execute).parameters:
                        raise RuntimeError("stage recompute core does not support predecessor resume")
                    arguments["predecessor_run"] = predecessor
                result = execute(
                    project,
                    plan,
                    **arguments,
                )
                if lease_is_lost():
                    raise RuntimeError("stage recompute lease lost")
                if result.get("status") == "awaiting_manual_work":
                    event_store.emit(
                        "approval.required",
                        run_id,
                        {
                            "status": "blocked",
                            "boundary": body.boundary,
                            "plan_fingerprint": plan["plan_fingerprint"],
                            "idempotency_key_fingerprint": record["idempotency_key_fingerprint"],
                            "next": result.get("next"),
                        },
                        job_id="stage-recompute",
                        step_id="manual-stop",
                        attempt=1,
                    )
                status = str(result.get("status", "completed"))
                if status == "awaiting_manual_work":
                    status = "blocked"
                if status not in {"completed", "blocked", "failed", "cancelled", "interrupted"}:
                    status = "completed"
            except InterruptedError:
                status, result = "cancelled", {"status": "cancelled"}
            except Exception as exc:
                status, result = "failed", {
                    "status": "failed",
                    "error_class": type(exc).__name__,
                    "message": "stage recompute failed",
                    "boundary": body.boundary,
                    "steps": getattr(exc, "steps", []),
                }
            with DispatcherStore(project, operational) as store:
                current = store.finish_stage_recompute(run_id, lease_token, status, result)
            if current:
                event_store.emit(
                    "run.finished",
                    run_id,
                    {
                        "status": status,
                        "duration_seconds": time.monotonic() - started,
                        "operation": "stage-recompute",
                        "boundary": body.boundary,
                        "plan_fingerprint": plan["plan_fingerprint"],
                        "idempotency_key_fingerprint": record["idempotency_key_fingerprint"],
                        "result": result,
                    },
                )
            with stage_cancellations_lock:
                stage_cancellations.pop(run_id, None)

        threading.Thread(target=worker, name=f"stage-recompute-{run_id}", daemon=True).start()
        return public

    @app.post("/api/v1/projects/{project_id}/stage-recompute/runs/{run_id}/cancel")
    def cancel_stage_recompute(project_id: str, run_id: str, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key)
        project = repo(project_id)
        from .workflow_migration import guard_mutation
        guard_mutation(project, base=operational)
        from .sqlite_state import DispatcherStore
        with DispatcherStore(project, operational) as store:
            record = store.stage_run(run_id)
            if record is None:
                raise HTTPException(404, "stage recompute run not found")
            if record["status"] == "running" and not store.stage_token_current(run_id, record["lease_token"]):
                raise RuntimeError("stage recompute lease token is no longer current")
            new_cancellation = store.bind_stage_cancellation(str(idempotency_key), run_id)
            if not new_cancellation:
                return {"run_id": run_id, "status": "cancellation_requested"}
            if record["status"] == "running":
                with stage_cancellations_lock:
                    signal = stage_cancellations.get(run_id)
                    if signal is not None:
                        signal.set()
                EventStore(operational / "projects", project_id).cancel(run_id, "local-user")
                return {"run_id": run_id, "status": "cancellation_requested"}
        return {"run_id": run_id, "status": record["status"]}

    @app.post("/api/v1/projects/{project_id}/dispatcher/{job_id}/{action}")
    def dispatcher_action(project_id: str, job_id: str, action: str, request: Request, body: DispatcherActionBody | None = Body(default=None), idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        if job_id not in {"analyze-dif", "consolidate-mrq", "classify-mrq", "decide-mrq"}:
            raise ValueError("unsupported dispatcher job")
        if action not in {"start", "stop", "resume", "cancel", "retry", "approve-consolidation", "approve-decision"}:
            raise ValueError("unsupported dispatcher action")
        if action == "approve-consolidation" and job_id != "consolidate-mrq":
            raise ValueError("only consolidation plans require this approval")
        if action == "approve-decision" and job_id != "decide-mrq":
            raise ValueError("only target decisions require this approval")
        mutation(request, idempotency_key)
        request_body = body.model_dump(exclude_none=True, exclude_unset=True) if body is not None else {}
        allowed_body_fields = {
            "start": {"actor"},
            "stop": {"timeout_seconds"},
            "resume": {"actor"},
            "cancel": {"actor"},
            "retry": {"policy_source", "predecessor_run_id", "expected_workflow_fingerprint", "expected_input_fingerprints"},
            "approve-consolidation": {"actor", "proposal_key", "expected_fingerprint", "payload"},
            "approve-decision": {"actor", "proposal_key", "expected_fingerprint", "payload"},
        }[action]
        if unknown := set(request_body) - allowed_body_fields:
            raise ValueError(f"unsupported fields for dispatcher {action}: {sorted(unknown)}")
        project = repo(project_id)
        from .workflow_migration import guard_mutation
        guard_mutation(project, base=operational)
        from .dispatcher import DispatcherCoordinator, DispatcherOutcome, load_bindings
        from .sqlite_state import DispatcherStore
        from .user_state import load_agent_profiles
        from .workflow import step_configurations
        event_store = EventStore(operational / "projects", project_id)
        if action in {"start", "resume", "retry"}:
            from .sqlite_state import DispatcherStore
            with DispatcherStore(project, operational) as gate_store:
                if gate_store.lease("stage-recompute") is not None:
                    raise RuntimeError("stage recompute lease blocks dispatcher start and continuation")
        action_result_path = event_store.root / ("idempotency-dispatcher-" + sha256(idempotency_key.encode()) + ".json")
        action_lock = None
        if action not in {"approve-consolidation", "approve-decision"}:
            action_lock = action_result_path.with_suffix(".lock").open("a+b")
            fcntl.flock(action_lock.fileno(), fcntl.LOCK_EX)
            if action != "retry" and action_result_path.is_file():
                cached = json.loads(action_result_path.read_text(encoding="utf-8"))
                action_lock.close()
                return cached
        session_key = (project_id, job_id)
        with dispatcher_sessions_lock:
            session = dispatcher_sessions.get(session_key)
            if session is None:
                store = DispatcherStore(project, operational); store.open()
                coordinator = DispatcherCoordinator(project, project_id, store, event_store, actor=str(request_body.get("actor", "local-user")))
                dispatcher_sessions[session_key] = (coordinator, store)
            else:
                coordinator, store = session
        try:
            should_launch_graph = True
            agent_profile = None
            phase_policies: dict[str, dict[str, Any]] = {}
            profiles_by_role: dict[str, dict[str, Any]] = {}
            if job_id in {"analyze-dif", "consolidate-mrq", "classify-mrq", "decide-mrq"}:
                profiles = load_agent_profiles(project, operational)
                step_id = job_id
                configured = next((item for item in step_configurations(project) if item["step"]["id"] == step_id), None)
                if configured:
                    phase_policies = {phase["phase_id"]: phase for phase in configured["step"].get("agent_phases", [])}
                    primary_role = {"analyze-dif": "analyzer", "consolidate-mrq": "grouper", "classify-mrq": "classifier", "decide-mrq": "researcher"}[job_id]
                    profile_name = next((role["agent_profile"] for phase in phase_policies.values() for role in phase["roles"] if role["role_id"] == primary_role), None)
                    agent_profile = profiles.get(profile_name) if profile_name else None
                    profiles_by_role = {role["role_id"]: profiles[role["agent_profile"]] for phase in phase_policies.values() for role in phase["roles"] if role["agent_profile"] in profiles}
            supplement = next((role["instruction_supplement"] for phase in phase_policies.values() for role in phase["roles"] if role["role_id"] in {"analyzer", "classifier", "researcher"}), "")
            from .workflow import next_work
            current_work = next_work(project) or {}
            expected_action = {"analyze-dif": "dif.classify-next", "consolidate-mrq": "mrq.consolidate", "classify-mrq": "mrq.classify-batches", "decide-mrq": "mrq.decide-next"}[job_id]
            if action == "start" and job_id == "consolidate-mrq" and current_work.get("action") != expected_action:
                from .dif_classifications import coverage
                counts = coverage(project)
                if not counts["all_dif_classified"]:
                    outcome = DispatcherOutcome(
                        job_id, "", "blocked", "", store.revision()[0],
                        {"phase": "classification_incomplete", "remaining": counts["remaining"]},
                        {
                            "code": "consolidation.classification_incomplete",
                            "message": f"{counts['remaining']} customer DIF remain unclassified",
                            "action": "dif.classify-next",
                        },
                    )
                    should_launch_graph = False
            work_unit = current_work.get("work_unit") if current_work.get("action") == expected_action else None
            if not isinstance(work_unit, dict):
                work_unit = {"id": job_id, "kind": "dispatcher-root", "allowed_paths": []}
            work_unit_id = str(work_unit["id"])
            bindings = load_bindings(project, project_id, job_id, work_unit_id, agent_profile, supplement)
            if action == "start" and not should_launch_graph:
                return {
                    "job_id": job_id, "action": action,
                    "outcome": {
                        "status": outcome.status, "run_id": outcome.run_id,
                        "thread_id": outcome.thread_id, "revision": outcome.revision,
                        "summary": outcome.summary, "blocker": outcome.blocker,
                    },
                }
            if action == "start" and agent_profile is None:
                outcome = DispatcherOutcome(
                    job_id,
                    "",
                    "blocked",
                    "",
                    store.revision()[0],
                    {"phase": "executor_unavailable"},
                    {
                        "code": "executor.agent_profile_missing",
                        "message": "configured local agent profile is unavailable",
                        "action": job_id,
                    },
                )
                response = {"job_id": job_id, "action": action, "outcome": {"status": outcome.status, "run_id": outcome.run_id, "thread_id": outcome.thread_id, "revision": outcome.revision, "summary": outcome.summary, "blocker": outcome.blocker}}
                from .contracts import atomic_json
                atomic_json(action_result_path, response)
                return response
            candidate_run_id = str(uuid.uuid4())
            execution_snapshot = None
            if action in {"start", "retry"} and agent_profile is not None and configured is not None:
                from .agents import resolve_execution_snapshot
                execution_snapshot = resolve_execution_snapshot(project, candidate_run_id, configured["step"]["operation"], configured["step"], profiles, work_unit)
            if action == "start":
                outcome = coordinator.start(job_id, bindings, run_id=candidate_run_id, execution_snapshot=execution_snapshot)
            elif action == "stop":
                outcome = coordinator.soft_stop(job_id, timeout_seconds=int(request_body.get("timeout_seconds", 0)))
            elif action == "resume":
                outcome = coordinator.resume(job_id, bindings)
            elif action == "cancel":
                outcome = coordinator.cancel(job_id)
            elif action == "retry":
                policy_source = request_body.get("policy_source")
                predecessor_run_id = str(request_body.get("predecessor_run_id", ""))
                expected_workflow = str(request_body.get("expected_workflow_fingerprint", ""))
                expected_inputs = request_body.get("expected_input_fingerprints")
                if policy_source not in {"reuse-snapshot", "current-policy"} or not predecessor_run_id or not isinstance(expected_inputs, dict):
                    raise ValueError("retry requires policy_source, predecessor_run_id and expected input fingerprints")
                expected_keys = {"source_generation_id", "diff_generation_id", "canonical_generation_id", "work_unit_id"}
                if set(expected_inputs) != expected_keys or any(not isinstance(value, str) for value in expected_inputs.values()):
                    raise ValueError("retry expected input fingerprints have an invalid shape")
                actual_inputs = {
                    "source_generation_id": bindings.source_generation_id,
                    "diff_generation_id": bindings.diff_generation_id,
                    "canonical_generation_id": bindings.canonical_generation_id,
                    "work_unit_id": bindings.work_unit_id,
                }
                if expected_workflow != bindings.workflow_fingerprint or expected_inputs != actual_inputs:
                    raise RuntimeError("retry workflow or subject inputs are stale")
                predecessor = event_store.run_snapshot(predecessor_run_id)
                if predecessor is None:
                    raise RuntimeError("retry predecessor execution snapshot is missing or corrupt")
                predecessor_snapshot = predecessor["execution_snapshot"]
                predecessor_bindings = predecessor_snapshot.get("subject_bindings", {})
                if any(predecessor_bindings.get(key) != actual_inputs[key] for key in expected_keys - {"work_unit_id"}) or str(predecessor_snapshot.get("work_unit", {}).get("id", "")) != work_unit_id:
                    raise RuntimeError("retry predecessor subject inputs are incompatible")
                if policy_source == "reuse-snapshot":
                    from .agents import validate_execution_snapshot
                    validate_execution_snapshot(project, predecessor_snapshot)
                    execution_snapshot = {
                        **predecessor_snapshot,
                        "run_id": candidate_run_id,
                        "policy_source": policy_source,
                        "predecessor_run_id": predecessor_run_id,
                    }
                elif execution_snapshot is not None:
                    execution_snapshot = {
                        **execution_snapshot,
                        "policy_source": policy_source,
                        "predecessor_run_id": predecessor_run_id,
                    }
                if execution_snapshot is None:
                    raise RuntimeError("retry execution snapshot cannot be resolved")
                snapshot_fingerprint = "sha256:" + sha256(canonical_json(execution_snapshot))
                reservation, created = store.reserve_retry(
                    str(idempotency_key),
                    "sha256:" + sha256(canonical_json({
                        "job_id": job_id,
                        "policy_source": policy_source,
                        "predecessor_run_id": predecessor_run_id,
                        "expected_workflow_fingerprint": expected_workflow,
                        "expected_input_fingerprints": expected_inputs,
                    })),
                    candidate_run_id,
                    job_id,
                    predecessor_run_id,
                    policy_source,
                    execution_snapshot,
                    snapshot_fingerprint,
                )
                candidate_run_id = reservation["run_id"]
                execution_snapshot = reservation["execution_snapshot"]
                if not created and reservation["state"] == "terminal":
                    return reservation["result"]
                if created:
                    try:
                        predecessor_lease = store.lease(job_id)
                        matching_lease = (
                            predecessor_lease
                            if predecessor_lease and predecessor_lease.get("run_id") == predecessor_run_id
                            else None
                        )
                        if matching_lease:
                            if matching_lease.get("state") not in {"failed", "resumable", "stale"}:
                                raise RuntimeError("retry predecessor is still active; stop it before retry")
                        elif predecessor.get("status") not in RETRYABLE_RUN_STATUSES:
                            raise RuntimeError("retry predecessor status is not retryable")
                    except Exception:
                        store.abandon_retry(candidate_run_id, reservation["owner_token"])
                        raise
                event_store.prepare_run(candidate_run_id, execution_snapshot)
                existing_lease = store.lease(job_id)
                if (
                    not created
                    and existing_lease
                    and existing_lease.get("run_id") == candidate_run_id
                ):
                    from .sqlite_state import is_stale
                    if is_stale(str(existing_lease["renewed_at"])):
                        store.interrupt_running_work(job_id, candidate_run_id, str(existing_lease["lease_token"]))
                        store.reconcile_invocation_outbox(event_store)
                        store.release_lease(job_id, str(existing_lease["lease_token"]))
                        existing_lease = None
                if not created and existing_lease and existing_lease.get("run_id") == candidate_run_id:
                    from .events import process_identity_alive
                    if existing_lease["state"] == "running" and not process_identity_alive(existing_lease.get("process_identity")):
                        store.interrupt_running_work(job_id, candidate_run_id, str(existing_lease["lease_token"]))
                        store.reconcile_invocation_outbox(event_store)
                        store.release_lease(job_id, str(existing_lease["lease_token"]))
                        outcome = coordinator.retry(job_id, bindings, run_id=candidate_run_id, execution_snapshot=execution_snapshot)
                        store.update_retry(candidate_run_id, reservation["owner_token"], "started", {"status": outcome.status, "recovered": True})
                    else:
                        should_launch_graph = False
                        outcome = DispatcherOutcome(
                            job_id,
                            candidate_run_id,
                            existing_lease["state"],
                            existing_lease["thread_id"],
                            store.revision()[0],
                            existing_lease.get("summary", {}),
                        )
                else:
                    outcome = coordinator.retry(job_id, bindings, run_id=candidate_run_id, execution_snapshot=execution_snapshot)
                    store.update_retry(candidate_run_id, reservation["owner_token"], "started", {"status": outcome.status})
            elif action in {"approve-consolidation", "approve-decision"}:
                proposal_key = str(request_body.get("proposal_key", ""))
                expected_fingerprint = str(request_body.get("expected_fingerprint", ""))
                if not proposal_key or not expected_fingerprint:
                    raise ValueError("proposal_key and expected_fingerprint are required for approval")
                proposal = store.proposal(proposal_key)
                if proposal is None or proposal["job_id"] != job_id:
                    raise RuntimeError("dispatcher proposal is missing, stale, or already consumed")
                if action == "approve-consolidation" and proposal.get("consumed_at") is not None:
                    from .consolidation import load_active as load_consolidation
                    pointer = load_consolidation(project)["pointer"]
                    if pointer.get("plan_fingerprint") == proposal["payload"].get("plan_fingerprint"):
                        return {
                            "job_id": job_id, "action": action,
                            "outcome": {
                                "status": "completed", "run_id": "", "thread_id": proposal["thread_id"],
                                "revision": store.revision()[0],
                                "summary": {
                                    "mrq_generation_id": pointer["mrq_generation_id"],
                                    "plan_fingerprint": pointer["plan_fingerprint"],
                                    "already_applied": True,
                                },
                                "blocker": None,
                            },
                        }
                lease = store.lease(job_id)
                if lease is None or lease["thread_id"] != proposal["thread_id"]:
                    raise RuntimeError("dispatcher approval lease is missing or belongs to another run")
                if proposal.get("consumed_at") is not None:
                    if action == "approve-consolidation":
                        from .consolidation import load_active as load_consolidation
                        pointer = load_consolidation(project)["pointer"]
                        if pointer.get("plan_fingerprint") == proposal["payload"].get("plan_fingerprint"):
                            return {
                                "job_id": job_id,
                                "action": action,
                                "outcome": {
                                    "status": "completed",
                                    "run_id": str(lease.get("run_id", "")),
                                    "thread_id": proposal["thread_id"],
                                    "revision": store.revision()[0],
                                    "summary": {
                                        "mrq_generation_id": pointer["mrq_generation_id"],
                                        "plan_fingerprint": pointer["plan_fingerprint"],
                                        "already_applied": True,
                                    },
                                    "blocker": None,
                                },
                            }
                    recovered_noise = (
                        action == "approve-noise"
                        and lease["state"] == "resumable"
                        and any(
                            item["job_id"] == job_id
                            and item["thread_id"] == proposal["thread_id"]
                            and item["kind"] == "noise-approval"
                            and item.get("consumed_at") is None
                            for item in store.proposals()
                        )
                    )
                    if not recovered_noise:
                        raise RuntimeError("dispatcher proposal is missing, stale, or already consumed")
                    summary = lease.get("summary", {})
                    if summary.get("workflow_fingerprint") != expected_fingerprint:
                        raise RuntimeError("dispatcher noise approval is stale")
                    response = {
                        "job_id": job_id,
                        "action": action,
                        "outcome": {
                            "status": "resumable",
                            "run_id": str(lease.get("run_id", "")),
                            "thread_id": proposal["thread_id"],
                            "revision": store.revision()[0],
                            "summary": summary,
                            "blocker": None,
                        },
                    }
                    from .contracts import atomic_json
                    atomic_json(action_result_path, response)
                    return response
                lease_token = str(lease["lease_token"])
                def approval_fence() -> None:
                    if not store.owns_lease(job_id, lease_token, proposal["thread_id"]):
                        raise RuntimeError("dispatcher lease was fenced before the canonical effect")
                result_path = action_result_path
                with result_path.with_suffix(".lock").open("a+b") as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                    if result_path.is_file():
                        return json.loads(result_path.read_text(encoding="utf-8"))
                    saved = proposal["payload"]
                    if action == "approve-noise":
                        proposals = saved.get("noise_proposals", [])
                        if saved.get("approval_stage") != "noise" or not proposals:
                            raise ValueError("dispatcher noise proposal is missing")
                        if ApplicationService(project).snapshot()["workflow_fingerprint"] != expected_fingerprint:
                            raise RuntimeError("dispatcher noise approval is stale")
                        timestamp = datetime.now(timezone.utc).isoformat()
                        reviewed = {
                            "items": [
                                {
                                    "stable_diff_id": item["stable_diff_id"],
                                    "actor": str(request_body.get("actor", "local-user")),
                                    "rationale": item["rationale"],
                                    "evidence": item["evidence"],
                                    "timestamp": timestamp,
                                }
                                for item in proposals
                            ]
                        }
                        approval_key = sha256(
                            canonical_json(
                                {
                                    "thread_id": proposal["thread_id"],
                                    "kind": "approved-noise",
                                }
                            )
                        )
                        summary = {
                            "approved_noise_ids": [item["stable_diff_id"] for item in reviewed["items"]],
                            "workflow_fingerprint": expected_fingerprint,
                            "next_action": "resume",
                        }
                        if not store.approve_noise_review(
                            proposal_key,
                            approval_key,
                            job_id,
                            proposal["thread_id"],
                            lease_token,
                            reviewed,
                            summary,
                        ):
                            raise RuntimeError("dispatcher lease was fenced before noise approval persistence")
                    elif action == "approve-consolidation":
                        from .consolidation import approve_plan, fingerprint
                        relative = str(saved.get("plan_path", ""))
                        plan_path = (operational / "projects" / project_id / relative).resolve()
                        plan_root = (operational / "projects" / project_id / "consolidation-plans").resolve()
                        if plan_root not in plan_path.parents or not plan_path.is_file():
                            raise ValueError("dispatcher consolidation plan is missing")
                        plan = json.loads(plan_path.read_text(encoding="utf-8"))
                        if fingerprint(plan) != saved.get("plan_fingerprint"):
                            raise RuntimeError("dispatcher consolidation plan fingerprint is stale")
                        approval_fence()
                        already_applied = saved["expected_pointer"].get("plan_fingerprint") == saved["plan_fingerprint"]
                        approval = {
                            "schema_version": "2",
                            "kind": "consolidation",
                            "actor": str(request_body.get("actor", "local-user")),
                            "rationale": str(request_body.get("rationale", "explicit dispatcher approval")),
                            "evidence": list(request_body.get("evidence", [])),
                            **{
                                key: plan["bindings"][key]
                                for key in (
                                    "source_fingerprint", "diff_fingerprint",
                                    "classification_fingerprint", "prior_mrq_fingerprint",
                                )
                            },
                            "plan_fingerprint": saved["plan_fingerprint"],
                        }
                        pointer = approve_plan(project, plan, saved["expected_pointer"], approval)
                        summary = {
                            "mrq_generation_id": pointer["mrq_generation_id"],
                            "plan_fingerprint": pointer["plan_fingerprint"],
                            "already_applied": already_applied,
                        }
                    else:
                        decision = saved.get("decision_proposal")
                        if not isinstance(decision, dict):
                            raise ValueError("dispatcher decision proposal is missing")
                        from .consolidation import load_active as load_consolidation
                        from .decision_generations import (
                            consolidation_input_fingerprint,
                            fingerprint as decision_fingerprint,
                            publish as publish_decisions,
                            validate_generation as validate_decisions,
                        )
                        pointer = load_consolidation(project)["pointer"]
                        input_fingerprint = consolidation_input_fingerprint(pointer)
                        rows = []
                        if pointer.get("decision_input_fingerprint") == input_fingerprint:
                            rows = validate_decisions(project, pointer["decision_generation_id"])["decisions.jsonl"]
                        approved = {**decision, "agreement_status": "approved"}
                        actor = str(request_body.get("actor", "local-user"))
                        rationale = str(request_body.get("rationale", "explicit dispatcher approval"))
                        evidence = list(request_body.get("evidence", []))
                        decision_row = {
                            "schema_version": "1",
                            "mrq_id": decision["mrq_id"],
                            "decision": approved,
                            "approval_fingerprint": decision_fingerprint({
                                "actor": actor,
                                "rationale": rationale,
                                "evidence": evidence,
                                "input_fingerprint": input_fingerprint,
                                "proposal_key": proposal_key,
                                "decision": approved,
                            }),
                            "provenance": {"kind": "stage-5"},
                        }
                        rows = sorted(
                            [row for row in rows if row["mrq_id"] != decision["mrq_id"]] + [decision_row],
                            key=canonical_json,
                        )
                        rows_fingerprint = decision_fingerprint(rows)
                        approval = {
                            "schema_version": "1",
                            "kind": "target-decisions",
                            "actor": actor,
                            "rationale": rationale,
                            "evidence": evidence,
                            "input_fingerprint": input_fingerprint,
                            "decision_fingerprint": rows_fingerprint,
                            "row_approval_fingerprints": {
                                row["mrq_id"]: row["approval_fingerprint"]
                                for row in rows
                            },
                        }
                        approval_fence()
                        result = publish_decisions(
                            project, rows, approval,
                            expected_transaction_id=pointer["transaction_id"],
                        )
                        summary = {
                            "approved_mrq_id": decision.get("mrq_id"),
                            "generation_id": result["generation"]["manifest"]["generation_id"],
                            "workflow_fingerprint": expected_fingerprint,
                            "already_applied": result["idempotent"],
                        }
                    if action != "approve-noise" and not store.consume_proposal(proposal_key, lease_token):
                        raise RuntimeError("dispatcher lease was fenced before proposal consumption")
                    remaining = [item for item in store.proposals() if item["job_id"] == job_id and item["thread_id"] == proposal["thread_id"] and item["kind"] == "approval" and item.get("consumed_at") is None]
                    if action == "approve-noise":
                        revision = coordinator.emit_transition(
                            job_id,
                            str(lease.get("run_id", "")),
                            proposal["thread_id"],
                            "step.progress",
                            "dispatcher.discover-mrq.noise_approved",
                            {"status": "running", "progress": {**summary, "resumable": True}},
                        )
                        outcome = DispatcherOutcome(
                            job_id,
                            str(lease.get("run_id", "")),
                            "resumable",
                            proposal["thread_id"],
                            revision,
                            summary,
                        )
                    elif action == "approve-decision" and remaining:
                        lease = store.lease(job_id) or {}
                        run_id = lease.get("summary", {}).get("run_id") or ""
                        store.renew_lease(job_id, str(lease["lease_token"]), state="blocked", summary={"phase": "approval_required", "run_id": run_id, "remaining_approvals": len(remaining)})
                        revision = coordinator.emit_transition(job_id, run_id, proposal["thread_id"], "step.progress", "dispatcher.decide-mrq.decision_approved", {"status": "blocked", "progress": {"approved_mrq_id": summary["approved_mrq_id"], "remaining_approvals": len(remaining)}})
                        outcome = DispatcherOutcome(job_id, run_id, "blocked", proposal["thread_id"], revision, {**summary, "remaining_approvals": len(remaining)})
                    else:
                        outcome = coordinator.finish(job_id, "completed", summary)
                    response = {"job_id": job_id, "action": action, "outcome": {"status": outcome.status, "run_id": outcome.run_id, "thread_id": outcome.thread_id, "revision": outcome.revision, "summary": outcome.summary, "blocker": outcome.blocker}}
                    from .contracts import atomic_json
                    atomic_json(result_path, response)
                    return response
            if should_launch_graph and action in {"start", "resume", "retry"} and outcome.status == "running":
                saved_run = event_store.run_snapshot(outcome.run_id)
                saved_execution = (saved_run or {}).get("execution_snapshot", {})
                saved_phases = saved_execution.get("agent_phases", [])
                saved_profiles = saved_execution.get("profiles", {})
                saved_primary_role = {
                    "analyze-dif": "analyzer",
                    "consolidate-mrq": "grouper",
                    "classify-mrq": "classifier",
                    "decide-mrq": "researcher",
                }[job_id]
                saved_profile_name = next(
                    (
                        role["agent_profile"]
                        for phase in saved_phases
                        for role in phase.get("roles", [])
                        if role.get("role_id") == saved_primary_role
                    ),
                    None,
                )
                if saved_profile_name in saved_profiles:
                    agent_profile = saved_profiles[saved_profile_name]
                if agent_profile is None:
                    outcome = coordinator.finish(job_id, "blocked", {"phase": "executor_unavailable", "blocker": {"code": "executor.agent_profile_missing", "message": "configured local agent profile is unavailable", "action": job_id}})
                    outcome.blocker = outcome.summary["blocker"]
                else:
                    timeout_seconds = int(configured["step"].get("timeout_seconds", 1800)) if configured else 1800
                    coordinator.launch_graph(outcome, bindings, agent_profile, phase_policies=phase_policies, profiles_by_role=profiles_by_role, timeout_seconds=timeout_seconds)
            response = {"job_id": job_id, "action": action, "outcome": {"status": outcome.status, "run_id": outcome.run_id, "thread_id": outcome.thread_id, "revision": outcome.revision, "summary": outcome.summary, "blocker": outcome.blocker}}
            if action == "retry":
                store.update_retry(
                    outcome.run_id,
                    reservation["owner_token"],
                    "terminal" if outcome.status in {"completed", "failed", "cancelled", "stale", "blocked"} else "started",
                    response,
                )
            from .contracts import atomic_json
            atomic_json(action_result_path, response)
            return response
        except Exception:
            # Активная сессия остаётся доступной для явной остановки/повтора.
            raise
        finally:
            if action_lock is not None:
                action_lock.close()

    @app.get("/api/v1/projects/{project_id}/source-setup")
    def source_setup(project_id: str):
        from .sources import PROFILES, current_profile_test, draft_fingerprint, extension_scope_status, load_contract
        from .contracts import external_id
        infobases, artifacts = load_contract(repo(project_id))
        from .user_state import load_connections
        available = load_connections(repo(project_id), operational)
        visible = ("server", "reference", "platform_path", "dbms", "db_server", "db_name", "db_user", "infobase_user", "client_connection")
        summaries = {
            name: {
                "available": True,
                "kind": value.get("kind"),
                "tested": current_profile_test(value, infobases["acquisition_profile"]),
                "profile_id": value.get("profile_id"),
                **{field: value.get(field, "") for field in visible},
                "db_password_set": bool(value.get("db_password")),
                "infobase_password_set": bool(value.get("infobase_password")),
                "extensions": value.get("extensions", []),
                "extension_count": len(value.get("extensions", [])),
                "tool_versions": value.get("tool_versions", {}),
            }
            for name, value in available.items()
        }
        drafts = operational / "projects" / project_id / "upload-drafts"
        declared = [{**item, "external_artifact_id": external_id(item["kind"], item["semantic_key"]), "uploaded": (drafts / item["role"] / external_id(item["kind"], item["semantic_key"]) / item["filename"]).is_file()} for item in artifacts.get("artifacts", [])]
        project = repo(project_id)
        pointer = lambda name: json.loads((project / "research" / name).read_text(encoding="utf-8")) if (project / "research" / name).is_file() else {}
        scope_status = extension_scope_status(project, available)
        return {"profiles": sorted(PROFILES), "infobases": infobases, "infobases_fingerprint": scope_status["infobases_fingerprint"], "extension_scope": scope_status["extension_scope"], "extension_scope_ready": scope_status["ready"], "extension_scope_blockers": scope_status["blockers"], "external_artifacts": {"schema_version": artifacts.get("schema_version"), "artifacts": declared}, "upload_draft_fingerprint": draft_fingerprint(drafts), "connection_profiles": summaries, "active_source": pointer("active-source-generation.json"), "active_diff": pointer("active-diff-generation.json")}

    @app.get("/api/v1/projects/{project_id}/source-tools")
    def source_tools(project_id: str):
        project = repo(project_id)
        from .source_tools import discover_tools
        from .user_state import load_connections
        roots = [str(item.get("platform_path", "")) for item in load_connections(project, operational).values()]
        return discover_tools(roots)

    @app.post("/api/v1/projects/{project_id}/source-routing-previews", status_code=202)
    def create_source_routing_preview(project_id: str, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key)
        project = repo(project_id)
        from .contracts import atomic_json
        from .user_state import load_connections, workspace_id
        connections = load_connections(project, operational)
        roots = {str(item.get("platform_path", "")) for item in connections.values()}
        if len(roots) != 1 or not next(iter(roots)):
            raise ValueError("one generation-wide platform path is required")
        preview_root = operational / "projects" / project_id / "source-routing-previews"
        preview_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        store = EventStore(operational / "projects", project_id)
        with (preview_root / ".lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            for old_path in preview_root.glob("*.json"):
                previous = json.loads(old_path.read_text(encoding="utf-8"))
                if previous.get("status") in {"pending", "running"}:
                    store.cancel(previous["preview_id"], "replacement")
                    previous.update({"status": "cancelled", "bindings": {}, "probe_results": [], "routing_manifest": {}, "required_tools": []}); atomic_json(old_path, previous)
                    store.emit("run.finished", previous["preview_id"], {"status": "cancelled", "duration_seconds": 0, "operation": "sources.routing-preview"})
            import uuid
            preview_id = str(uuid.uuid4())
            path = preview_root / f"{preview_id}.json"
            atomic_json(path, {"schema_version": "1", "preview_id": preview_id, "project_id": workspace_id(project), "status": "pending", "progress": {"phase": "queued", "completed": 0, "total": 0, "subject": ""}, "routing_plan_fingerprint": "", "bindings": {}, "probe_results": [], "routing_manifest": {}, "required_tools": []})
            from .events import process_identity
            from .workflow import state_fingerprint
            preview_started = time.monotonic()
            store.emit("run.created", preview_id, {"status": "running", "actor": "local-user", "process_identity": process_identity(), "workflow_fingerprint": state_fingerprint(project), "operation": "sources.routing-preview"})

        def worker():
            with (preview_root / ".lock").open("a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["status"] == "cancelled" or store.cancellation(preview_id):
                    return
                record["status"] = "running"; atomic_json(path, record)
            cancelled = lambda: bool(store.cancellation(preview_id)) or json.loads(path.read_text(encoding="utf-8")).get("status") == "cancelled"
            def update_progress(progress):
                with (preview_root / ".lock").open("a+b") as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                    current = json.loads(path.read_text(encoding="utf-8"))
                    if current.get("status") in {"pending", "running"}:
                        current["progress"] = progress
                        atomic_json(path, current)
            def finish(value):
                with (preview_root / ".lock").open("a+b") as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                    current = json.loads(path.read_text(encoding="utf-8"))
                    if current.get("status") == "cancelled":
                        return
                    if value["status"] == "ready":
                        decisions = [{"routing_group_id": group["routing_group_id"], "form_counts": group["form_counts"], "representation_schema": group["representation_schema"], "routing_reason": group["routing_reason"]} for group in value["routing_manifest"]["groups"][:1000]]
                        outputs = {"group_count": len(value["routing_manifest"]["groups"]), "required_tools": value["required_tools"], "decisions": decisions}
                        store.emit("step.output", preview_id, {"status": "completed", "outputs": outputs, "output_fingerprint": value["routing_plan_fingerprint"]}, job_id="acquire-sources", step_id="routing-preview", attempt=1)
                    store.emit("run.finished", preview_id, {"status": "completed" if value["status"] == "ready" else value["status"], "duration_seconds": time.monotonic() - preview_started, "operation": "sources.routing-preview"})
                    atomic_json(path, value)
            try:
                from .sources import build_routing_preview
                value = build_routing_preview(project, Path(next(iter(roots))), connections, upload_drafts=operational / "projects" / project_id / "upload-drafts", cancelled=cancelled, progress=update_progress)
                if cancelled():
                    return
                finish({
                    **record,
                    **value,
                    "status": "blocked" if value.get("blockers") else "ready",
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
                })
            except InterruptedError:
                current = json.loads(path.read_text(encoding="utf-8"))
                current["status"] = "cancelled"; atomic_json(path, current)
            except Exception as exc:
                finish({**record, "status": "failed", "error_code": "routing_preview_failed", "error": f"routing preview failed ({type(exc).__name__})"})

        threading.Thread(target=worker, name=f"source-routing-preview-{preview_id}", daemon=True).start()
        return {"preview_id": preview_id, "status": "pending"}

    @app.get("/api/v1/projects/{project_id}/source-routing-previews/{preview_id}")
    def get_source_routing_preview(project_id: str, preview_id: str):
        repo(project_id)
        if not re.fullmatch(r"[0-9a-f-]{36}", preview_id):
            raise HTTPException(404, "routing preview not found")
        path = operational / "projects" / project_id / "source-routing-previews" / f"{preview_id}.json"
        if not path.is_file():
            raise HTTPException(404, "routing preview not found")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.delete("/api/v1/projects/{project_id}/source-routing-previews/{preview_id}")
    def cancel_source_routing_preview(project_id: str, preview_id: str, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); repo(project_id)
        path = operational / "projects" / project_id / "source-routing-previews" / f"{preview_id}.json"
        if not path.is_file():
            raise HTTPException(404, "routing preview not found")
        from .contracts import atomic_json
        with (path.parent / ".lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("status") in {"pending", "running"}:
                store = EventStore(operational / "projects", project_id)
                store.cancel(preview_id, "local-user")
                record["status"] = "cancelled"; atomic_json(path, record)
                store.emit("run.finished", preview_id, {"status": "cancelled", "duration_seconds": 0, "operation": "sources.routing-preview"})
        return {"preview_id": preview_id, "status": record["status"]}

    @app.put("/api/v1/projects/{project_id}/connection-profiles/{profile_id}")
    def put_connection(project_id: str, profile_id: str, body: ConnectionBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); project = repo(project_id)
        from .sources import load_contract, preflight_connection
        infobases = load_contract(project)[0]
        declared = {item["connection_profile"] for item in infobases["roles"].values()}
        if profile_id not in declared or not profile_id or "/" in profile_id:
            raise ValueError("connection profile is not declared by this repository")
        profile = body.profile
        allowed = {"kind", "server", "reference", "path", "profile_id", "tested", "platform_path", "dbms", "db_server", "db_name", "db_user", "db_password", "infobase_user", "infobase_password", "client_connection", "extensions"}
        if set(profile) - allowed or profile.get("kind") not in {"server", "file"}:
            raise ValueError("invalid current connection profile")
        from .sources import normalize_connection_identity
        normalize_connection_identity(profile)
        from .user_state import load_connections, save_connections
        values = load_connections(project, operational)
        previous = values.get(profile_id, {})
        for password in ("db_password", "infobase_password"):
            if not profile.get(password) and previous.get(password):
                profile[password] = previous[password]
        tester = connection_tester or preflight_connection
        tested = tester(infobases["acquisition_profile"], Path(str(profile.get("platform_path", ""))), profile)
        profile = {**profile, **tested}
        configuration = tested.get("configuration")
        if configuration:
            for role, binding in infobases["roles"].items():
                if binding["connection_profile"] != profile_id:
                    continue
                if configuration["version"] != binding["version"]:
                    raise ValueError(f"infobase version does not match declared role: {role}")
                binding.update(configuration_name=configuration["name"], root_uuid=configuration["uuid"])
            from .sources import serialize_infobases
            from .contracts import atomic_bytes
            atomic_bytes(project / "research/infobases.toml", serialize_infobases(infobases))
        values[profile_id] = profile; save_connections(project, values, operational)
        return {"profile_id": profile_id, "available": True, "kind": profile["kind"], "tested": True, "acquisition_profile": profile.get("profile_id"), "extension_count": len(profile.get("extensions", []))}

    @app.get("/api/v1/projects/{project_id}/agent-profiles")
    def agent_profiles(project_id: str):
        from .user_state import load_agent_profiles
        return {"items": load_agent_profiles(repo(project_id), operational)}

    @app.get("/api/v1/projects/{project_id}/agent-capabilities")
    def agent_capabilities(project_id: str):
        repo(project_id)
        return codex_capabilities()

    @app.put("/api/v1/projects/{project_id}/agent-profiles/{profile_id}")
    def put_agent_profile(project_id: str, profile_id: str, body: AgentProfileBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); project = repo(project_id)
        if not profile_id or len(profile_id) > 100 or not profile_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("invalid agent profile ID")
        from .user_state import replace_agent_profile
        values = replace_agent_profile(project, profile_id, body.profile, operational)
        return {"profile_id": profile_id, "profile": values[profile_id]}

    @app.put("/api/v1/projects/{project_id}/external-uploads/{role}/{external_id}")
    async def put_external_upload(project_id: str, role: str, external_id: str, request: Request, filename: str, declared_length: int, declared_sha256: str, expected_draft_fingerprint: str, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); project = repo(project_id)
        from .sources import draft_fingerprint, load_contract
        from .contracts import external_id as make_external_id, normalize_relative
        declaration = next((item for item in load_contract(project)[1].get("artifacts", []) if item.get("role") == role and make_external_id(item.get("kind", ""), item.get("semantic_key", "")) == external_id), None)
        if not declaration or normalize_relative(filename) != normalize_relative(str(declaration.get("filename", ""))) or declared_length != int(declaration.get("declared_size_bytes", -1)):
            raise ValueError("upload does not match a tracked external artifact declaration")
        expected_hash = str(declaration.get("sha256") or declared_sha256).removeprefix("sha256:").lower()
        if declared_sha256.removeprefix("sha256:").lower() != expected_hash:
            raise ValueError("upload hash does not match declaration")
        root = operational / "projects" / project_id / "upload-drafts"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if draft_fingerprint(root) != expected_draft_fingerprint:
            raise RuntimeError("stale upload draft fingerprint")
        import shutil, hashlib
        if shutil.disk_usage(root).free < declared_length + 1024**3:
            raise OSError("external upload requires declared bytes plus 1 GiB free space")
        target = confined(root, f"{role}/{external_id}/{normalize_relative(filename)}"); target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.partial"); checksum = hashlib.sha256(); written = 0
        try:
            with temporary.open("xb") as output:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > declared_length: raise ValueError("upload exceeds declared length")
                    checksum.update(chunk); output.write(chunk)
                output.flush(); os.fsync(output.fileno())
            if written != declared_length or checksum.hexdigest() != expected_hash:
                raise ValueError("external upload length or SHA-256 mismatch")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"role": role, "external_id": external_id, "filename": filename, "size_bytes": written, "sha256": checksum.hexdigest(), "draft_fingerprint": draft_fingerprint(root)}

    def folder_store(project_id: str):
        from .external_folder import PreviewStore
        project_root = operational / "projects" / project_id
        return PreviewStore(repo(project_id), project_root / "external-folder-previews", project_root / "upload-drafts")

    def folder_event(project_id: str, preview_id: str, operation: str, value: dict[str, Any]) -> None:
        from .events import process_identity
        digest = sha256(preview_id.encode()); run_id = f"external-folder-{digest[:16]}"; store = EventStore(operational / "projects", project_id)
        store.emit("run.created", run_id, {"status": "running", "actor": "local-user", "process_identity": process_identity(), "workflow_fingerprint": str(value.get("workflow_fingerprint", "")), "operation": operation, "preview_digest": "sha256:" + digest})
        store.emit("run.finished", run_id, {"status": "completed", "duration_seconds": 0.0, "operation": operation, "entry_count": len(value.get("entries", [])), "ignored_unsupported_count": int(value.get("ignored_unsupported_count", 0)), "status_counts": value.get("status_counts", {}), "total_bytes": int(value.get("total_bytes", 0)), "candidate_declaration_fingerprint": value.get("candidate_declaration_sha256"), "draft_result": value.get("status"), "validation": "passed"})

    def folder_idempotent(project_id: str, key: str, operation: str, invoke) -> dict[str, Any]:
        from .contracts import atomic_json
        path = operational / "projects" / project_id / "folder-idempotency" / f"{sha256((operation + ':' + key).encode())}.json"; path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.with_suffix(".lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if path.is_file(): return json.loads(path.read_text(encoding="utf-8"))
            value = invoke(); atomic_json(path, value); return value

    @app.post("/api/v1/projects/{project_id}/external-folder-previews", status_code=201)
    def create_external_folder_preview(project_id: str, request: Request, body: dict[str, Any] = Body(), idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key)
        from .contracts import canonical_json
        if len(canonical_json(body)) > 16 * 1024**2 or set(body) != {"entries", "expected_fingerprint", "role"}: raise ValueError("invalid folder preview request")
        entries = body["entries"]
        if not isinstance(entries, list) or any(not isinstance(item, dict) or set(item) != {"relative_path", "size_bytes"} for item in entries): raise ValueError("invalid folder preview entries")
        if body["expected_fingerprint"] != ApplicationService(repo(project_id)).snapshot()["workflow_fingerprint"]: raise RuntimeError("stale workflow fingerprint")
        def invoke():
            value = folder_store(project_id).create_browser([item["relative_path"] for item in entries], [item["size_bytes"] for item in entries], body["expected_fingerprint"], body["role"]); folder_event(project_id, value["preview_id"], "external-artifacts.folder-preview.create/v1", value); return value
        return folder_idempotent(project_id, idempotency_key, "create", invoke)

    @app.put("/api/v1/projects/{project_id}/external-folder-previews/{preview_id}/entries/{entry_id}")
    async def put_external_folder_entry(project_id: str, preview_id: str, entry_id: str, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key)
        from .contracts import atomic_json
        cached = operational / "projects" / project_id / "folder-idempotency" / f"{sha256(('upload:' + str(idempotency_key)).encode())}.json"; cached.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        cache_lock = cached.with_suffix(".lock").open("a+b"); fcntl.flock(cache_lock.fileno(), fcntl.LOCK_EX)
        if cached.is_file():
            value = json.loads(cached.read_text(encoding="utf-8")); cache_lock.close(); return value
        store = folder_store(project_id); preview = store.load(preview_id)
        entry = next((item for item in preview.get("entries", []) if item.get("entry_id") == entry_id), None)
        if entry is None: cache_lock.close(); raise ValueError("unknown preview entry")
        semaphore = folder_streams.setdefault(project_id, threading.BoundedSemaphore(2))
        if not semaphore.acquire(blocking=False): cache_lock.close(); raise RuntimeError("folder upload stream limit reached")
        target = store._path(preview_id) / "bytes" / entry["stored_name"]; target.parent.mkdir(mode=0o700, exist_ok=True)
        temporary = target.with_name(f".{target.name}.partial"); written = 0
        import hashlib
        digest = hashlib.sha256()
        try:
            with temporary.open("xb") as output:
                async for chunk in request.stream():
                    written += len(chunk)
                    if written > entry["size_bytes"]: raise ValueError("upload exceeds declared length")
                    digest.update(chunk); output.write(chunk)
                output.flush(); os.fsync(output.fileno())
            if written != entry["size_bytes"]: raise ValueError("upload length mismatch")
            os.replace(temporary, target)
            value = store.uploaded(preview_id, entry_id, written, digest.hexdigest()); atomic_json(cached, value); return value
        finally:
            temporary.unlink(missing_ok=True); semaphore.release(); cache_lock.close()

    @app.post("/api/v1/projects/{project_id}/external-folder-previews/{preview_id}/finalize")
    def finalize_external_folder_preview(project_id: str, preview_id: str, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key)
        def invoke():
            value = folder_store(project_id).finalize_browser(preview_id); folder_event(project_id, preview_id, "external-artifacts.folder-preview.finalize/v1", value); return value
        return folder_idempotent(project_id, idempotency_key, "finalize", invoke)

    @app.get("/api/v1/projects/{project_id}/external-folder-previews/{preview_id}/entries")
    def external_folder_entries(project_id: str, preview_id: str, cursor: int = 0, limit: int = 100):
        if cursor < 0 or limit < 1 or limit > 200: raise ValueError("preview page limit must be 1..200")
        entries = folder_store(project_id).load(preview_id).get("entries", []); page = entries[cursor:cursor + limit]
        return {"items": page, "next_cursor": cursor + len(page) if cursor + len(page) < len(entries) else None}

    @app.get("/api/v1/projects/{project_id}/external-folder-previews/{preview_id}/declaration-diff")
    def external_folder_diff(project_id: str, preview_id: str):
        preview = folder_store(project_id).load(preview_id); return {"candidate_declaration_sha256": preview.get("candidate_declaration_sha256"), "diff": preview.get("declaration_diff", "")}

    @app.post("/api/v1/projects/{project_id}/external-folder-previews/{preview_id}/declaration-diff-preview")
    def preview_external_folder_diff(project_id: str, preview_id: str, request: Request, body: dict[str, Any] = Body(), idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key)
        from .contracts import canonical_json
        if len(canonical_json(body)) > 16 * 1024**2 or set(body) != {"selected_entries", "expected_declaration_fingerprint"}: raise ValueError("invalid declaration diff preview request")
        return folder_idempotent(project_id, idempotency_key, "declaration-diff-preview", lambda: folder_store(project_id).declaration_diff(preview_id, body["selected_entries"], body["expected_declaration_fingerprint"]))

    @app.post("/api/v1/projects/{project_id}/external-folder-previews/{preview_id}/confirm")
    def confirm_external_folder_preview(project_id: str, preview_id: str, request: Request, body: dict[str, Any] = Body(), idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key)
        from .contracts import canonical_json
        if len(canonical_json(body)) > 16 * 1024**2 or set(body) != {"selected_entries", "expected_fingerprint", "expected_declaration_fingerprint", "expected_draft_fingerprint", "confirm"}: raise ValueError("invalid folder confirmation request")
        project = repo(project_id); service = ApplicationService(project, upload_drafts=operational / "projects" / project_id / "upload-drafts")
        def invoke():
            value = service.apply("sources.configure", {"external_artifact_preview_id": preview_id, "selected_entries": body["selected_entries"], "expected_declaration_fingerprint": body["expected_declaration_fingerprint"], "expected_draft_fingerprint": body["expected_draft_fingerprint"], "confirm": body["confirm"]}, body["expected_fingerprint"]); folder_event(project_id, preview_id, "sources.configure.external-artifacts/v1", value); return value
        return folder_idempotent(project_id, idempotency_key, "confirm", invoke)

    @app.delete("/api/v1/projects/{project_id}/external-folder-previews/{preview_id}")
    def cancel_external_folder_preview(project_id: str, preview_id: str, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); return folder_idempotent(project_id, idempotency_key, "cancel", lambda: folder_store(project_id).cancel(preview_id))

    @app.get("/api/v1/projects/{project_id}/indexes")
    def index_status(project_id: str): return {"items": ApplicationService(repo(project_id)).index_statuses(), "disposable": True}

    @app.post("/api/v1/projects/{project_id}/actions")
    def action(project_id: str, body: ActionBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); store = EventStore(operational / "projects", project_id); started = time.monotonic()
        result_path = store.root / ("idempotency-action-" + sha256(idempotency_key.encode()) + ".json")
        lock_path = result_path.with_suffix(".lock")
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if result_path.is_file():
                prior = json.loads(result_path.read_text(encoding="utf-8"))
                if prior["status"] == "completed": return prior["result"]
                raise HTTPException(409, "idempotency key belongs to a failed action")
            project = repo(project_id); workflow_fingerprint = ApplicationService(project).snapshot()["workflow_fingerprint"]
            from .events import process_identity
            event_context = {"actor": "local-user", "operation": body.operation}
            store.emit("run.created", idempotency_key, {**event_context, "status": "running", "process_identity": process_identity(), "workflow_fingerprint": workflow_fingerprint, "idempotency_key": idempotency_key})
            try:
                from .user_state import load_connections
                result = ApplicationService(project, connections=load_connections(project, operational), upload_drafts=operational / "projects" / project_id / "upload-drafts").apply(body.operation, body.payload, body.expected_fingerprint)
            except Exception as exc:
                from .contracts import atomic_json
                atomic_json(result_path, {"schema_version": "1", "status": "failed"})
                store.emit("run.finished", idempotency_key, {**event_context, "status": "failed", "error_class": type(exc).__name__, "message": str(exc), "idempotency_key": idempotency_key, "duration_seconds": time.monotonic() - started}); raise
            from .contracts import atomic_json
            atomic_json(result_path, {"schema_version": "1", "status": "completed", "result": result})
            store.emit("run.finished", idempotency_key, {**event_context, "status": "completed", "result": result, "idempotency_key": idempotency_key, "duration_seconds": time.monotonic() - started}); return result

    @app.get("/api/v1/projects/{project_id}/events")
    def events(project_id: str, cursor: int = 0, limit: int = 500): return EventStore(operational / "projects", project_id).replay(cursor, limit)

    @app.get("/api/v1/projects/{project_id}/events/stream")
    async def event_stream(project_id: str, request: Request, cursor: int = 0):
        repo(project_id); store = EventStore(operational / "projects", project_id)
        async def delivery():
            nonlocal cursor
            import asyncio
            while not await request.is_disconnected():
                page = store.replay(cursor, 500)
                if page.get("resync_required"):
                    yield "event: resync\ndata: " + json.dumps(page, ensure_ascii=False) + "\n\n"; cursor = page["next_cursor"]
                for event in page.get("events", []):
                    cursor = event["sequence"]; yield "id: " + str(cursor) + "\nevent: workflow\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                yield ": keepalive\n\n"; await asyncio.sleep(1)
        return StreamingResponse(delivery(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/v1/projects/{project_id}/runs")
    def runs(project_id: str, offset: int = 0, limit: int = 100):
        if offset < 0 or not 1 <= limit <= 500: raise ValueError("invalid run page")
        store = EventStore(operational / "projects", project_id); repo(project_id)
        store.reconcile()
        root = store.root / "runs"; items = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)] if root.is_dir() else []
        return {"offset": offset, "limit": limit, "items": items[offset:offset + limit], "has_more": offset + limit < len(items)}

    @app.get("/api/v1/projects/{project_id}/runs/{run_id}/attempts/{attempt}/log")
    def attempt_log(project_id: str, run_id: str, attempt: int, offset: int = 0, limit: int = 500):
        repo(project_id)
        return EventStore(operational / "projects", project_id).read_log(run_id, attempt, offset, limit)

    @app.get("/api/v1/projects/{project_id}/operational-artifacts/{artifact_path:path}")
    def operational_artifact(project_id: str, artifact_path: str):
        repo(project_id); root = EventStore(operational / "projects", project_id).root
        path = confined(root, artifact_path); relative = path.relative_to(root).as_posix()
        if not relative.startswith("artifacts/") or not path.is_file() or path.suffix.lower() not in {".json", ".txt", ".log"}:
            raise HTTPException(404, "artifact not found")
        return FileResponse(path, media_type="application/octet-stream", headers={"Content-Disposition": "attachment", "Content-Security-Policy": "sandbox"})

    @app.get("/api/v1/projects/{project_id}/registries/{name}")
    def registry(
        project_id: str,
        name: str,
        offset: int = 0,
        limit: int = 100,
        expected_generation: str = "",
        item_id: str = "",
    ):
        return ApplicationService(repo(project_id)).registry(
            name,
            offset,
            limit,
            expected_generation,
            item_id,
        )

    @app.get("/api/v1/projects/{project_id}/artifacts/{artifact_path:path}")
    def artifact(project_id: str, artifact_path: str):
        project = repo(project_id); path = confined(project, artifact_path)
        relative = path.relative_to(project).as_posix()
        if not any(relative == prefix.rstrip("/") or relative.startswith(prefix) for prefix in ("outputs/", "analysis/indexes/generations/", "analysis/migration-requirements/generations/")):
            raise HTTPException(404, "artifact not found")
        if not path.is_file() or path.suffix.lower() not in {".json", ".csv", ".txt", ".log", ".md"}: raise HTTPException(404, "artifact not found")
        return FileResponse(path, media_type="text/plain", headers={"Content-Disposition": "attachment", "Content-Security-Policy": "sandbox"})
    static = Path(__file__).with_name("workspace_static")
    if static.is_dir(): app.mount("/", StaticFiles(directory=static, html=True), name="workspace")
    return app


def main() -> int:
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8765)
    return 0
