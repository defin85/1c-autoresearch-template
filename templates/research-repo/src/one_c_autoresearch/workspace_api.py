from __future__ import annotations

import json
import os
import fcntl
import time
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from .contracts import confined, sha256
from .events import EventStore
from .service import ApplicationService


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


def create_app(state_root: Path | None = None, approved_roots: list[Path] | None = None, testing: bool = False, connection_tester=None):
    operational = (state_root or (Path.home() / ".local/state/one-c-autoresearch")).resolve()
    operational.mkdir(parents=True, exist_ok=True, mode=0o700)
    roots = [item.resolve() for item in (approved_roots or [Path.cwd()])]
    bookmarks_path = operational / "bookmarks.json"
    dispatcher_sessions: dict[tuple[str, str], tuple[Any, Any]] = {}
    dispatcher_sessions_lock = threading.RLock()
    folder_streams: dict[str, threading.BoundedSemaphore] = {}

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
        if not idempotency_key: raise HTTPException(400, "Idempotency-Key is required")

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
        from .workflow import attach_dispatcher
        return attach_dispatcher(ApplicationService(repo(project_id)).snapshot(), repo(project_id), operational)

    @app.get("/api/v1/projects/{project_id}/workflow/next")
    def next_work(project_id: str): return ApplicationService(repo(project_id)).next()

    @app.get("/api/v1/projects/{project_id}/workflow/configuration")
    def workflow_configuration(project_id: str): return ApplicationService(repo(project_id)).workflow_configuration()

    @app.post("/api/v1/projects/{project_id}/workflow/patch-preview")
    def workflow_patch_preview(project_id: str, body: StepPatchBody):
        return ApplicationService(repo(project_id)).preview_step_patch(body.model_dump())

    @app.post("/api/v1/projects/{project_id}/workflow/run-next")
    def run_next_step(project_id: str, body: RunBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); project = repo(project_id)
        from .user_state import load_agent_profiles, load_connections
        service = ApplicationService(project, connections=load_connections(project, operational), upload_drafts=operational / "projects" / project_id / "upload-drafts")
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
            if set(body.approved_operations) - {"sources.acquire", "mrq.discover-next", "mrq.decide-next"}: raise ValueError("unsupported run approval")
            result = run_next(service.repo, invoke, store, select=service.next, approved_operations=set(body.approved_operations), agent_profiles=load_agent_profiles(project, operational))
            from .contracts import atomic_json
            atomic_json(result_path, result)
            return result

    @app.post("/api/v1/projects/{project_id}/workflow/run-until-blocked")
    def run_until(project_id: str, body: RunBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); project = repo(project_id)
        from .user_state import load_agent_profiles, load_connections
        service = ApplicationService(project, connections=load_connections(project, operational), upload_drafts=operational / "projects" / project_id / "upload-drafts")
        if set(body.approved_operations) - {"sources.acquire", "mrq.discover-next", "mrq.decide-next"}: raise ValueError("unsupported run approval")
        from .runner import run_until_blocked
        store = EventStore(operational / "projects", project_id)
        result_path = store.root / ("idempotency-until-" + sha256(idempotency_key.encode()) + ".json")
        with result_path.with_suffix(".lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8")); result["snapshot"] = service.snapshot(); return result
            if body.expected_fingerprint != service.snapshot()["workflow_fingerprint"]: raise RuntimeError("stale workflow fingerprint")
            invoke = lambda operation, payload, cancelled: service.apply(operation, payload, service.snapshot()["workflow_fingerprint"], cancelled)
            result = run_until_blocked(service.repo, invoke, store, max_units=body.max_units, select=service.next, approved_operations=set(body.approved_operations), agent_profiles=load_agent_profiles(project, operational))
            from .contracts import atomic_json
            atomic_json(result_path, result)
            return result

    @app.post("/api/v1/projects/{project_id}/runs/{run_id}/cancel")
    def cancel_run(project_id: str, run_id: str, body: CancelBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); repo(project_id)
        EventStore(operational / "projects", project_id).cancel(run_id, body.actor)
        return {"run_id": run_id, "status": "cancellation_requested"}

    @app.get("/api/v1/projects/{project_id}/dispatcher")
    def dispatcher_projection(project_id: str):
        from .workflow import attach_dispatcher
        snapshot = attach_dispatcher(ApplicationService(repo(project_id)).snapshot(), repo(project_id), operational)
        return snapshot.get("dispatcher", {"schema_version": "1", "revision": 0, "fresh_at": "", "circuits": [], "jobs": {}})

    @app.post("/api/v1/projects/{project_id}/dispatcher/{job_id}/{action}")
    def dispatcher_action(project_id: str, job_id: str, action: str, request: Request, body: dict[str, Any] | None = Body(default=None), idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        if job_id not in {"discover-mrq", "decide-mrq"}:
            raise ValueError("unsupported dispatcher job")
        if action not in {"start", "stop", "resume", "cancel", "retry", "approve-batch", "approve-decision"}:
            raise ValueError("unsupported dispatcher action")
        mutation(request, idempotency_key)
        project = repo(project_id)
        from .dispatcher import DispatcherCoordinator, DispatcherOutcome, load_bindings
        from .sqlite_state import DispatcherStore
        from .user_state import load_agent_profiles
        from .workflow import step_configurations
        event_store = EventStore(operational / "projects", project_id)
        action_result_path = event_store.root / ("idempotency-dispatcher-" + sha256(idempotency_key.encode()) + ".json")
        action_lock = None
        if action not in {"approve-batch", "approve-decision"}:
            action_lock = action_result_path.with_suffix(".lock").open("a+b")
            fcntl.flock(action_lock.fileno(), fcntl.LOCK_EX)
            if action_result_path.is_file():
                cached = json.loads(action_result_path.read_text(encoding="utf-8"))
                action_lock.close()
                return cached
        session_key = (project_id, job_id)
        with dispatcher_sessions_lock:
            session = dispatcher_sessions.get(session_key)
            if session is None:
                store = DispatcherStore(project, operational); store.open()
                coordinator = DispatcherCoordinator(project, project_id, store, event_store, actor=str((body or {}).get("actor", "local-user")))
                dispatcher_sessions[session_key] = (coordinator, store)
            else:
                coordinator, store = session
        try:
            agent_profile = None
            if job_id in {"discover-mrq", "decide-mrq"}:
                profiles = load_agent_profiles(project, operational)
                step_id = "discover-mrq" if job_id == "discover-mrq" else "decide-mrq"
                configured = next((item for item in step_configurations(project) if item["step"]["id"] == step_id), None)
                if configured:
                    profile_name = configured["step"].get("agent_profile")
                    agent_profile = profiles.get(profile_name) if profile_name else None
            supplement = (body or {}).get("instruction_supplement", "")
            work_unit_id = (body or {}).get("work_unit_id")
            if not work_unit_id and job_id == "decide-mrq":
                from .mrq import active
                rows = active(project)["mrq.jsonl"]
                pending = sorted(item["mrq_id"] for item in rows if item.get("state") != "superseded" and not item.get("migration_decision", {}).get("decision"))
                work_unit_id = pending[0] if pending else job_id
            work_unit_id = work_unit_id or job_id
            bindings = load_bindings(project, project_id, job_id, work_unit_id, agent_profile, supplement)
            if action == "start":
                outcome = coordinator.start(job_id, bindings)
            elif action == "stop":
                outcome = coordinator.soft_stop(job_id, timeout_seconds=int((body or {}).get("timeout_seconds", 0)))
            elif action == "resume":
                outcome = coordinator.resume(job_id, bindings)
            elif action == "cancel":
                outcome = coordinator.cancel(job_id)
            elif action == "retry":
                outcome = coordinator.retry(job_id, bindings)
            elif action in {"approve-batch", "approve-decision"}:
                request_body = body or {}
                proposal_key = str(request_body.get("proposal_key", ""))
                expected_fingerprint = str(request_body.get("expected_fingerprint", ""))
                if not proposal_key or not expected_fingerprint:
                    raise ValueError("proposal_key and expected_fingerprint are required for approval")
                proposal = store.proposal(proposal_key)
                if proposal is None or proposal["job_id"] != job_id or proposal.get("consumed_at") is not None:
                    raise RuntimeError("dispatcher proposal is missing, stale, or already consumed")
                result_path = action_result_path
                with result_path.with_suffix(".lock").open("a+b") as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                    if result_path.is_file():
                        return json.loads(result_path.read_text(encoding="utf-8"))
                    saved = proposal["payload"]
                    if action == "approve-batch":
                        groups = saved.get("batch_proposals", [])
                        approved_noise = request_body.get("payload", {}).get("approved_noise", [])
                        expected_noise_ids = set(saved.get("approved_noise_ids", []))
                        supplied_noise_ids = {item.get("stable_diff_id") for item in approved_noise if isinstance(item, dict)}
                        if supplied_noise_ids != expected_noise_ids:
                            raise ValueError("approved noise must exactly match the reviewed dispatcher proposal")
                        canonical_payload = {
                            "approved_noise": approved_noise,
                            "group_proposals": [
                                {key: group[key] for key in ("semantic_key", "title", "stable_diff_ids", "supporting_diff_ids", "evidence", "business_meaning", "scope", "confidence", "rationale")}
                                for group in groups
                            ],
                        }
                        from .mrq import active
                        canonical = active(project)
                        active_by_key = {item.get("semantic_key"): item for item in canonical["mrq.jsonl"] if item.get("state") != "superseded"}
                        recovered_ids: list[str] = []
                        for group in canonical_payload["group_proposals"]:
                            row = active_by_key.get(group["semantic_key"])
                            if row is None:
                                recovered_ids = []
                                break
                            source = row.get("source_customization", {})
                            primary = sorted(item.get("stable_diff_id") for item in canonical["dispositions.jsonl"] if item.get("mrq_id") == row["mrq_id"] and item.get("primary"))
                            if primary != sorted(group["stable_diff_ids"]) or any(source.get(key) != group[key] for key in ("business_meaning", "scope", "confidence", "rationale")):
                                recovered_ids = []
                                break
                            recovered_ids.append(row["mrq_id"])
                        applied_noise = {item.get("stable_diff_id") for item in canonical["dispositions.jsonl"] if item.get("approved_noise")}
                        expected_noise = {item["stable_diff_id"] for item in approved_noise}
                        if recovered_ids and len(recovered_ids) == len(groups) and expected_noise <= applied_noise:
                            result = {"mrq_ids": recovered_ids, "recovered": True}
                        else:
                            result = ApplicationService(project).apply("mrq.publish-source-batch", canonical_payload, expected_fingerprint)
                        canonical_snapshot = ApplicationService(project).snapshot()
                        summary = {"published_mrq_ids": result.get("mrq_ids", []), "workflow_fingerprint": canonical_snapshot["workflow_fingerprint"]}
                    else:
                        decision = saved.get("decision_proposal")
                        if not isinstance(decision, dict):
                            raise ValueError("dispatcher decision proposal is missing")
                        from .mrq import active
                        row = next((item for item in active(project)["mrq.jsonl"] if item.get("mrq_id") == decision.get("mrq_id") and item.get("state") != "superseded"), None)
                        decision_fields = ("decision", "target_evidence", "target_coverage", "residual_gap", "target_solution", "rationale", "acceptance_criteria", "risk", "open_questions")
                        if row is not None and all(row.get("migration_decision", {}).get(key) == decision.get(key) for key in decision_fields):
                            result = {"generation_id": active(project)["pointer"].get("canonical_generation_id"), "recovered": True}
                        else:
                            result = ApplicationService(project).apply("mrq.decide", decision, expected_fingerprint)
                        canonical_snapshot = ApplicationService(project).snapshot()
                        summary = {"approved_mrq_id": decision.get("mrq_id"), "generation_id": result.get("generation_id"), "workflow_fingerprint": canonical_snapshot["workflow_fingerprint"]}
                    store.consume_proposal(proposal_key)
                    remaining = [item for item in store.proposals() if item["job_id"] == job_id and item["thread_id"] == proposal["thread_id"] and item["kind"] == "approval" and item.get("consumed_at") is None]
                    if action == "approve-decision" and remaining:
                        lease = store.lease(job_id) or {}
                        run_id = lease.get("summary", {}).get("run_id") or ""
                        store.renew_lease(job_id, state="blocked", summary={"phase": "approval_required", "run_id": run_id, "remaining_approvals": len(remaining)})
                        revision = coordinator.emit_transition(job_id, run_id, proposal["thread_id"], "step.progress", "dispatcher.decide-mrq.decision_approved", {"status": "blocked", "progress": {"approved_mrq_id": summary["approved_mrq_id"], "remaining_approvals": len(remaining)}})
                        outcome = DispatcherOutcome(job_id, run_id, "blocked", proposal["thread_id"], revision, {**summary, "remaining_approvals": len(remaining)})
                    else:
                        outcome = coordinator.finish(job_id, "completed", summary)
                    response = {"job_id": job_id, "action": action, "outcome": {"status": outcome.status, "run_id": outcome.run_id, "thread_id": outcome.thread_id, "revision": outcome.revision, "summary": outcome.summary, "blocker": outcome.blocker}}
                    from .contracts import atomic_json
                    atomic_json(result_path, response)
                    return response
            if action in {"start", "resume", "retry"} and outcome.status == "running":
                if agent_profile is None:
                    outcome = coordinator.finish(job_id, "blocked", {"phase": "executor_unavailable", "blocker": {"code": "executor.agent_profile_missing", "message": "configured local agent profile is unavailable", "action": job_id}})
                    outcome.blocker = outcome.summary["blocker"]
                else:
                    timeout_seconds = int(configured["step"].get("timeout_seconds", 1800)) if configured else 1800
                    coordinator.launch_graph(outcome, bindings, agent_profile, timeout_seconds=timeout_seconds)
            response = {"job_id": job_id, "action": action, "outcome": {"status": outcome.status, "run_id": outcome.run_id, "thread_id": outcome.thread_id, "revision": outcome.revision, "summary": outcome.summary, "blocker": outcome.blocker}}
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
        from .sources import PROFILES, current_profile_test, draft_fingerprint, load_contract
        from .contracts import external_id
        infobases, artifacts = load_contract(repo(project_id))
        from .user_state import load_connections
        available = load_connections(repo(project_id), operational)
        summaries = {name: {"available": True, "kind": value.get("kind"), "tested": current_profile_test(value, infobases["acquisition_profile"]), "profile_id": value.get("profile_id"), "extensions": value.get("extensions", []), "extension_count": len(value.get("extensions", [])), "tool_versions": value.get("tool_versions", {})} for name, value in available.items()}
        drafts = operational / "projects" / project_id / "upload-drafts"
        declared = [{**item, "external_artifact_id": external_id(item["kind"], item["semantic_key"]), "uploaded": (drafts / item["role"] / external_id(item["kind"], item["semantic_key"]) / item["filename"]).is_file()} for item in artifacts.get("artifacts", [])]
        project = repo(project_id)
        pointer = lambda name: json.loads((project / "research" / name).read_text(encoding="utf-8")) if (project / "research" / name).is_file() else {}
        return {"profiles": sorted(PROFILES), "infobases": infobases, "infobases_fingerprint": "sha256:" + sha256((project / "research/infobases.toml").read_bytes()), "external_artifacts": {"schema_version": artifacts.get("schema_version"), "artifacts": declared}, "upload_draft_fingerprint": draft_fingerprint(drafts), "connection_profiles": summaries, "active_source": pointer("active-source-generation.json"), "active_diff": pointer("active-diff-generation.json")}

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
        tester = connection_tester or preflight_connection
        tested = tester(infobases["acquisition_profile"], Path(str(profile.get("platform_path", ""))), profile)
        profile = {**profile, **tested}
        from .user_state import load_connections, save_connections
        values = load_connections(project, operational); values[profile_id] = profile; save_connections(project, values, operational)
        return {"profile_id": profile_id, "available": True, "kind": profile["kind"], "tested": True, "acquisition_profile": profile.get("profile_id"), "extension_count": len(profile.get("extensions", []))}

    @app.get("/api/v1/projects/{project_id}/agent-profiles")
    def agent_profiles(project_id: str):
        from .user_state import load_agent_profiles
        return {"items": load_agent_profiles(repo(project_id), operational)}

    @app.put("/api/v1/projects/{project_id}/agent-profiles/{profile_id}")
    def put_agent_profile(project_id: str, profile_id: str, body: AgentProfileBody, request: Request, idempotency_key: str | None = web["Header"](default=None, alias="Idempotency-Key")):
        mutation(request, idempotency_key); project = repo(project_id)
        if not profile_id or len(profile_id) > 100 or not profile_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("invalid agent profile ID")
        from .user_state import load_agent_profiles, save_agent_profiles
        values = load_agent_profiles(project, operational); values[profile_id] = body.profile; save_agent_profiles(project, values, operational)
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
    def registry(project_id: str, name: str, offset: int = 0, limit: int = 100):
        if name not in {"diff-inventory", "target-coverage", "mrq"} or not 1 <= limit <= 500 or offset < 0: raise ValueError("invalid registry page")
        project = repo(project_id)
        if name == "mrq":
            from .mrq import active
            rows = active(project)["mrq.jsonl"]
        else:
            pointer = json.loads((project / "research/active-diff-generation.json").read_text(encoding="utf-8"))
            import csv
            if not pointer.get("generation_id"):
                return {"offset": offset, "limit": limit, "items": [], "has_more": False}
            path = project / "analysis/indexes/generations" / pointer["generation_id"] / f"{name}.csv"
            with path.open(encoding="utf-8", newline="") as stream: rows = list(csv.DictReader(stream))
        return {"offset": offset, "limit": limit, "items": rows[offset:offset + limit], "has_more": offset + limit < len(rows)}

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
