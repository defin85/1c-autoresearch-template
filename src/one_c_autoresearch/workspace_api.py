from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import sys
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from .common import file_sha256
from .workspace import (
    ALLOWED_CONNECTIONS,
    ALLOWED_PROVIDERS,
    RunManager,
    WorkspacePaths,
    WorkspaceStore,
    agent_probe,
    canonical_under,
    test_connection,
    update_project_manifest,
    workflow_snapshot,
)


def _web_imports() -> dict[str, Any]:
    try:
        from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
        from fastapi.staticfiles import StaticFiles
        from pydantic import BaseModel, Field
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install the optional workspace dependencies: pip install 'one-c-autoresearch[workspace]'") from exc
    return locals()


web = _web_imports()
FastAPI = web["FastAPI"]
HTTPException = web["HTTPException"]
Request = web["Request"]
Response = web["Response"]
JSONResponse = web["JSONResponse"]
StreamingResponse = web["StreamingResponse"]
FileResponse = web["FileResponse"]
BaseModel = web["BaseModel"]
Field = web["Field"]
Cookie = web["Cookie"]
Header = web["Header"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    root: str


class ResourceBody(BaseModel):
    id: str | None = None
    project_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = None


class RunBody(BaseModel):
    project_id: str
    stage_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ManifestUpdate(BaseModel):
    expected_hash: str
    updates: dict[str, dict[str, Any]]


def acquire_instance_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"managed workspace already active: {path.read_text(encoding='utf-8', errors='replace')}") from exc
    os.write(fd, f"pid={os.getpid()}\n".encode())
    return fd


def create_app(paths: WorkspacePaths | None = None, approved_roots: list[Path] | None = None, testing: bool = False):
    workspace_paths = paths or WorkspacePaths.default()
    roots = [path.resolve() for path in (approved_roots or [Path.cwd()])]
    store = WorkspaceStore(workspace_paths)
    manager = RunManager(store)
    bootstrap_tokens: dict[str, float] = {}
    sessions: dict[str, dict[str, str]] = {}
    lock_fd: int | None = None

    @asynccontextmanager
    async def lifespan(_app) -> AsyncIterator[None]:
        nonlocal lock_fd
        if not testing:
            lock_fd = acquire_instance_lock(workspace_paths.lock)
        yield
        store.close()
        if lock_fd is not None:
            os.close(lock_fd)
            workspace_paths.lock.unlink(missing_ok=True)

    app = FastAPI(title="1C Autoresearch Workspace", version="1", lifespan=lifespan)
    app.state.store = store
    app.state.manager = manager
    app.state.approved_roots = roots
    app.state.bootstrap_tokens = bootstrap_tokens

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        host = request.headers.get("host", "").split(":", 1)[0].strip("[]")
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "invalid host"}, status_code=400)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def require_session(request: Request) -> dict[str, str]:
        session_id = request.cookies.get("workspace_session")
        session = sessions.get(session_id or "")
        if not session:
            raise HTTPException(status_code=401, detail="authentication required")
        return session

    def require_mutation(request: Request) -> dict[str, str]:
        session = require_session(request)
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host')}"
        if origin != expected_origin or request.headers.get("x-csrf-token") != session["csrf"]:
            raise HTTPException(status_code=403, detail="CSRF validation failed")
        return session

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "schema_version": 1}

    @app.post("/api/v1/bootstrap-token")
    def issue_bootstrap(request: Request):
        if not testing:
            raise HTTPException(status_code=404)
        token = secrets.token_urlsafe(32)
        bootstrap_tokens[token] = time.time() + 60
        return {"token": token}

    @app.post("/api/v1/session")
    async def create_session(request: Request, response: Response):
        body = await request.json()
        token = str(body.get("token") or "")
        expires = bootstrap_tokens.pop(token, 0)
        if not token or expires < time.time():
            raise HTTPException(status_code=401, detail="invalid or expired bootstrap token")
        session_id = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        sessions[session_id] = {"csrf": csrf, "user": "local"}
        response.set_cookie("workspace_session", session_id, httponly=True, samesite="strict", secure=False, path="/")
        return {"csrf_token": csrf, "user": "local"}

    @app.get("/api/v1/projects")
    def list_projects(request: Request):
        require_session(request)
        return store.projects()

    @app.post("/api/v1/projects", status_code=201)
    def create_project(body: ProjectCreate, request: Request):
        require_mutation(request)
        return store.register_project(body.name, Path(body.root), roots)

    @app.get("/api/v1/projects/{project_id}")
    def get_project(project_id: str, request: Request):
        require_session(request)
        try:
            return store.project(project_id)
        except KeyError:
            raise HTTPException(status_code=404) from None

    @app.put("/api/v1/projects/{project_id}/setup")
    async def save_setup(project_id: str, request: Request):
        require_mutation(request)
        return store.save_setup(project_id, await request.json())

    @app.get("/api/v1/projects/{project_id}/workflow")
    def workflow(project_id: str, request: Request):
        require_session(request)
        return workflow_snapshot(store, project_id)

    @app.put("/api/v1/projects/{project_id}/manifest")
    def manifest(project_id: str, body: ManifestUpdate, request: Request):
        require_mutation(request)
        project = store.project(project_id)
        new_hash = update_project_manifest(Path(project["root"]), body.updates, body.expected_hash)
        with store.db:
            store.db.execute("UPDATE projects SET manifest_hash=? WHERE id=?", (new_hash, project_id))
        return {"manifest_hash": new_hash}

    def resource_routes(kind: str):
        plural = kind + "s"

        @app.get(f"/api/v1/{plural}", name=f"list_{plural}")
        def list_items(request: Request, project_id: str | None = None):
            require_session(request)
            return store.resources(kind, project_id)

        @app.post(f"/api/v1/{plural}", status_code=201, name=f"create_{kind}")
        def create_item(body: ResourceBody, request: Request):
            require_mutation(request)
            data = dict(body.data)
            if kind == "connection":
                channel = data.get("channel")
                if channel not in ALLOWED_CONNECTIONS:
                    raise HTTPException(status_code=422, detail="unsupported connection channel")
            if kind == "agent":
                if data.get("provider") not in ALLOWED_PROVIDERS:
                    raise HTTPException(status_code=422, detail="unsupported provider")
                fallback = data.get("fallback_profile")
                if fallback and fallback == body.id:
                    raise HTTPException(status_code=422, detail="fallback cycle")
            item = store.save_resource(kind, ({"id": body.id} if body.id else {}) | data, body.project_id)
            if body.secret:
                secret_id = f"{kind}-{item['id']}"
                store.write_secret(secret_id, body.secret)
                data["secret_ref"] = secret_id
                data["secret_present"] = True
                item = store.save_resource(kind, {"id": item["id"]} | data, body.project_id)
            return item

        @app.get(f"/api/v1/{plural}/{{item_id}}", name=f"get_{kind}")
        def get_item(item_id: str, request: Request):
            require_session(request)
            try:
                return store.resource(kind, item_id)
            except KeyError:
                raise HTTPException(status_code=404) from None

        @app.delete(f"/api/v1/{plural}/{{item_id}}", status_code=204, name=f"delete_{kind}")
        def delete_item(item_id: str, request: Request):
            require_mutation(request)
            store.delete_resource(kind, item_id)
            return Response(status_code=204)

    for resource_kind in ("connection", "agent", "prompt", "artifact"):
        resource_routes(resource_kind)

    @app.post("/api/v1/connections/{item_id}/test")
    def probe_connection(item_id: str, request: Request):
        require_mutation(request)
        profile = store.resource("connection", item_id)
        secret = store.secret(profile["secret_ref"]) if profile.get("secret_ref") else ""
        return test_connection(profile, secret)

    @app.get("/api/v1/agent-providers")
    def providers(request: Request):
        require_session(request)
        return [agent_probe(provider) for provider in sorted(ALLOWED_PROVIDERS)]

    @app.get("/api/v1/runs")
    def runs(request: Request, project_id: str | None = None):
        require_session(request)
        return store.runs(project_id)

    @app.get("/api/v1/runs/{run_id}")
    def run(run_id: str, request: Request):
        require_session(request)
        try:
            return store.run(run_id)
        except KeyError:
            raise HTTPException(status_code=404) from None

    @app.post("/api/v1/runs", status_code=202)
    def start_run(body: RunBody, request: Request, idempotency_key: str = Header(alias="Idempotency-Key")):
        require_mutation(request)
        try:
            return manager.start(body.project_id, body.stage_id, body.payload, idempotency_key)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel_run(run_id: str, request: Request):
        require_mutation(request)
        return manager.cancel(run_id)

    @app.get("/api/v1/events")
    async def events(request: Request, project_id: str, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
        require_session(request)
        after = int(last_event_id or 0)

        async def stream():
            cursor = after
            while not await request.is_disconnected():
                rows, reset = store.events(project_id, cursor)
                if reset:
                    yield "event: reset\ndata: {}\n\n"
                    return
                if rows:
                    for row in rows:
                        cursor = int(row["id"])
                        data = json.dumps(row["payload"], ensure_ascii=False)
                        yield f"id: {cursor}\nevent: {row['type']}\ndata: {data}\n\n"
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/v1/artifacts/{project_id}/{artifact_path:path}")
    def artifact(project_id: str, artifact_path: str, request: Request, download: bool = False):
        require_session(request)
        project = store.project(project_id)
        root = Path(project["root"])
        try:
            target = canonical_under(root / artifact_path, [root])
        except ValueError:
            raise HTTPException(status_code=404) from None
        if not target.is_file():
            raise HTTPException(status_code=404)
        media = "text/html" if target.suffix.lower() == ".html" else "application/octet-stream"
        disposition = "attachment" if download or target.suffix.lower() == ".html" else "inline"
        return FileResponse(target, media_type=media, filename=target.name if disposition == "attachment" else None, headers={"Content-Disposition": f'{disposition}; filename="{target.name}"'})

    static_dir = Path(__file__).with_name("workspace_static")

    @app.get("/{path:path}")
    def frontend(path: str):
        target = static_dir / path if path else static_dir / "index.html"
        if target.is_file():
            return FileResponse(target)
        index = static_dir / "index.html"
        if index.is_file():
            return FileResponse(index)
        return web["HTMLResponse"]("<h1>Workspace frontend is not built</h1>", status_code=503)

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the managed autoresearch workspace")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace-root", action="append", default=[])
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("non-loopback binding is not supported")
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install optional workspace dependencies") from exc
    token = secrets.token_urlsafe(32)
    os.environ["ONE_C_AUTORESEARCH_BOOTSTRAP_TOKEN"] = token
    url = f"http://127.0.0.1:{args.port}/#bootstrap={token}"
    if not args.no_browser:
        threading = __import__("threading")
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    roots = [Path(value).resolve() for value in args.workspace_root] or [Path.cwd()]
    uvicorn.run(create_app(approved_roots=roots), host=args.host, port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
