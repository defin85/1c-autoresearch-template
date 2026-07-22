from __future__ import annotations

import argparse
import asyncio
import configparser
import json
import os
import re
import secrets
import socket
import sys
import urllib.parse
import webbrowser
import zipfile
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
    redact,
    render_project_manifest,
    test_connection,
    update_project_manifest,
    validate_agent_profile,
    workflow_snapshot,
)


def _web_imports() -> dict[str, Any]:
    try:
        from fastapi import FastAPI, Header, HTTPException, Request, Response
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
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
Header = web["Header"]
SOURCE_PATHS = {"vendor_baseline": "sources/vendor_baseline", "target_cf": "sources/target_cf", "target_cfe": "sources/target_cfe", "next_vendor": "sources/next_vendor"}


def discover_1c_infobases(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(path.read_text(encoding="utf-8-sig", errors="replace"))
    result = []
    for name in parser.sections():
        section = parser[name]
        connection = section.get("Connect", "").strip()
        if not connection:
            continue
        server = re.search(r'Srvr="([^"]+)"', connection, re.IGNORECASE)
        reference = re.search(r'Ref="([^"]+)"', connection, re.IGNORECASE)
        file_path = re.search(r'File="([^"]+)"', connection, re.IGNORECASE)
        if not ((server and reference) or file_path):
            continue
        result.append({
            "name": name,
            "connection_string": connection,
            "connection_kind": "server" if server else "file",
            "server": server.group(1) if server else "",
            "reference": reference.group(1) if reference else "",
            "file": file_path.group(1) if file_path else "",
            "folder": section.get("Folder", ""),
            "platform_version": section.get("DefaultVersion", section.get("Version", "")),
            "credentials_ignored": bool(re.search(r"/(?:N|P)(?:\"|\s)", section.get("AdditionalParameters", ""), re.IGNORECASE)),
        })
    return sorted(result, key=lambda item: (item["folder"].casefold(), item["name"].casefold()))


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    root: str
    product: str = ""
    baseline_version: str = ""
    target_version: str = ""


class CredentialBody(BaseModel):
    username: str = Field(default="", max_length=200)
    secret: str = Field(min_length=1, max_length=4096)


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
        content = path.read_text(encoding="utf-8", errors="replace")
        try:
            pid = int(content.partition("pid=")[2].splitlines()[0])
            os.kill(pid, 0)
        except (ValueError, OSError):
            path.unlink(missing_ok=True)
            return acquire_instance_lock(path)
        raise RuntimeError(f"managed workspace already active: {content}") from exc
    os.write(fd, f"pid={os.getpid()}\n".encode())
    return fd


def create_app(paths: WorkspacePaths | None = None, approved_roots: list[Path] | None = None, testing: bool = False):
    workspace_paths = paths or WorkspacePaths.default()
    roots = [path.resolve() for path in (approved_roots or [Path.cwd()])]
    store = WorkspaceStore(workspace_paths)
    manager = RunManager(store, allow_fake=testing)
    lock_fd: int | None = None

    @asynccontextmanager
    async def lifespan(_app) -> AsyncIterator[None]:
        nonlocal lock_fd
        if not testing:
            lock_fd = acquire_instance_lock(workspace_paths.lock)
        manager.start_monitor()
        yield
        manager.close()
        store.close()
        if lock_fd is not None:
            os.close(lock_fd)
            workspace_paths.lock.unlink(missing_ok=True)

    app = FastAPI(title="1C Autoresearch Workspace", version="1", lifespan=lifespan)
    app.state.store = store
    app.state.manager = manager
    app.state.approved_roots = roots

    @app.exception_handler(ValueError)
    async def value_error(_request: Request, exc: ValueError):
        return JSONResponse({"detail": redact(str(exc)), "code": "validation_error"}, status_code=422)

    @app.exception_handler(RuntimeError)
    async def runtime_error(_request: Request, exc: RuntimeError):
        return JSONResponse({"detail": redact(str(exc)), "code": "conflict"}, status_code=409)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        host_header = request.headers.get("host", "")
        host = host_header[1:].split("]", 1)[0] if host_header.startswith("[") else host_header.split(":", 1)[0]
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "invalid host"}, status_code=400)
        response = await call_next(request)
        if "Content-Security-Policy" not in response.headers:
            response.headers["Content-Security-Policy"] = "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def require_mutation(request: Request) -> dict[str, str]:
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host')}"
        if origin != expected_origin:
            raise HTTPException(status_code=403, detail="origin validation failed")
        return {"user": "local"}

    def _validate_manifest_updates(project: dict[str, Any], updates: dict[str, dict[str, Any]]) -> None:
        allowed = {"project": {"product", "baseline_version", "target_version", "next_vendor_version", "description"}, "paths": {"vendor_baseline", "target_cf", "target_cfe", "next_vendor", "compare_repo"}, "policy": {"static_sources_first", "allow_live_infobase_evidence", "mark_runtime_data_dependencies", "default_confidence_for_inference"}}
        for section, values in updates.items():
            if section not in allowed or set(values) - allowed[section]:
                raise HTTPException(status_code=422, detail=f"unsupported project.toml update: {section}")
        approved = [Path(item) for item in project.get("approved_roots") or roots]
        for value in updates.get("paths", {}).values():
            if str(value).strip():
                candidate = Path(str(value)); canonical_under(candidate if candidate.is_absolute() else Path(project["root"]) / candidate, approved)

    @app.get("/api/v1/health")
    def health():
        from .workspace import SCHEMA_VERSION
        return {"status": "ok", "schema_version": SCHEMA_VERSION}

    @app.get("/api/v1/projects")
    def list_projects(request: Request):
        return store.projects()

    @app.get("/api/v1/infobases/discover")
    def discover_infobases(request: Request):
        path = Path.home() / ".1C" / "1cestart" / "ibases.v8i"
        return {"source": str(path), "infobases": discover_1c_infobases(path)}

    @app.get("/api/v1/filesystem/directories")
    def list_directories(request: Request, path: str = ""):
        current = canonical_under(Path(path).expanduser() if path else roots[0], roots)
        if not current.is_dir():
            raise HTTPException(status_code=404, detail="directory not found")
        directories = []
        try:
            for child in sorted(current.iterdir(), key=lambda item: item.name.casefold()):
                if child.is_dir():
                    try:
                        directories.append({"name": child.name, "path": str(canonical_under(child, roots))})
                    except ValueError:
                        pass
        except PermissionError:
            raise HTTPException(status_code=403, detail="directory is not readable") from None
        parent = None
        try:
            candidate = canonical_under(current.parent, roots)
            if candidate != current:
                parent = str(candidate)
        except ValueError:
            pass
        return {"current": str(current), "parent": parent, "directories": directories}

    @app.post("/api/v1/projects", status_code=201)
    def create_project(body: ProjectCreate, request: Request):
        require_mutation(request)
        root = Path(body.root).expanduser()
        if not (root / "project.toml").is_file():
            canonical_under(root.parent, roots)
            from argparse import Namespace
            from .bootstrap import create_research_repo
            template_root = Path(__file__).resolve().parents[2]
            if not (template_root / "templates" / "research-repo").is_dir():
                archive = Path(__file__).with_name("workspace_assets") / "research-template.zip"
                template_root = workspace_paths.root / "template"
                if not archive.is_file():
                    raise HTTPException(status_code=409, detail="project does not exist and packaged research template is unavailable")
                if not (template_root / "templates" / "research-repo").is_dir():
                    with zipfile.ZipFile(archive) as source:
                        for member in source.infolist():
                            canonical_under(template_root / member.filename, [template_root])
                        source.extractall(template_root)
            slug = re.sub(r"[^a-z0-9-]+", "-", body.name.lower()).strip("-") or secrets.token_hex(4)
            create_research_repo(Namespace(template_root=str(template_root), target_path=str(root), force=False, project_id=slug, product=body.product or body.name, baseline_version=body.baseline_version, target_version=body.target_version, next_vendor_version="", vendor_baseline="", target_cf="", target_cfe="", next_vendor="", rlm_vendor_baseline="", rlm_target_cf="", rlm_target_cfe="", rlm_next_vendor="", init_git=True))
        return store.register_project(body.name, root, roots)

    @app.get("/api/v1/projects/{project_id}")
    def get_project(project_id: str, request: Request):
        try:
            return store.project(project_id)
        except KeyError:
            raise HTTPException(status_code=404) from None

    @app.put("/api/v1/projects/{project_id}/setup")
    async def save_setup(project_id: str, request: Request):
        require_mutation(request)
        setup = await request.json()
        errors: dict[str, str] = {}
        step = int(setup.get("step") or 0)
        if step >= 2:
            setup["source_roles"] = json.dumps(SOURCE_PATHS)
            if not setup.get("vendor_connection"):
                errors["vendor_connection"] = "select the vendor infobase"
            if not setup.get("customer_connection"):
                errors["customer_connection"] = "select the customer infobase"
            try:
                infobases = json.loads(str(setup.get("infobases") or "[]"))
                ids = list(infobases.values()) if isinstance(infobases, dict) else list(infobases)
                if not ids:
                    errors["infobases"] = "select at least one tested infobase connection"
                for connection_id in ids:
                    connection = store.resource("connection", str(connection_id))
                    if not connection.get("last_test_ok"):
                        errors[f"infobases.{connection_id}"] = "connection has not passed its fixed test"
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                errors["infobases"] = str(exc)
        if step >= 3:
            project_root = Path(store.project(project_id)["root"])
            for relative in SOURCE_PATHS.values():
                canonical_under(project_root / relative, [project_root]).mkdir(parents=True, exist_ok=True)
            try:
                source_roles = json.loads(str(setup.get("source_roles") or "{}"))
                if not isinstance(source_roles, dict) or not source_roles:
                    errors["source_roles"] = "specify at least one supported source role"
                for role, value in source_roles.items():
                    if role not in {"vendor_baseline", "target_cf", "target_cfe", "next_vendor"}:
                        errors["source_roles"] = f"unsupported source role: {role}"
                        continue
                    candidate = Path(str(value)); target = canonical_under(candidate if candidate.is_absolute() else Path(store.project(project_id)["root"]) / candidate, roots)
                    if not target.exists():
                        errors[f"source_roles.{role}"] = f"path does not exist: {target}"
            except (ValueError, json.JSONDecodeError) as exc:
                errors["source_roles"] = str(exc)
        if step >= 5 and not setup.get("checked"):
            errors["checked"] = "prerequisite checks must be confirmed"
        if setup.get("complete") and not str(setup.get("enabled_stages") or "").strip():
            errors["enabled_stages"] = "select at least one stage"
        if setup.get("complete"):
            from .workspace import STAGE_BY_ID
            enabled = {item.strip() for item in str(setup.get("enabled_stages") or "").split(",") if item.strip()}
            unknown = enabled - set(STAGE_BY_ID)
            if unknown:
                errors["enabled_stages"] = f"unsupported stages: {', '.join(sorted(unknown))}"
            agent_stages = {item for item in enabled if item in STAGE_BY_ID and STAGE_BY_ID[item]["kind"] == "agent"}
            try:
                assignments = json.loads(str(setup.get("agent_assignments") or "{}"))
            except json.JSONDecodeError as exc:
                assignments = {}; errors["agent_assignments"] = str(exc)
            for stage_id in agent_stages:
                profile_id = str(assignments.get(stage_id) or "") if isinstance(assignments, dict) else ""
                try:
                    profile = store.resource("agent", profile_id)
                    if not profile.get("enabled", True) or not profile.get("available", False):
                        raise KeyError(profile_id)
                except KeyError:
                    errors[f"agent_assignments.{stage_id}"] = "select an enabled compatible agent profile"
        if errors:
            raise HTTPException(status_code=422, detail={"code": "setup_invalid", "fields": errors})
        return store.save_setup(project_id, setup)

    @app.get("/api/v1/stages")
    def stages(request: Request):
        from .workspace import STAGES
        return list(STAGES)

    @app.get("/api/v1/projects/{project_id}/preferences")
    def preferences(project_id: str, request: Request):
        return store.preferences(project_id)

    @app.put("/api/v1/projects/{project_id}/preferences")
    async def save_preferences(project_id: str, request: Request):
        require_mutation(request)
        return store.save_preferences(project_id, await request.json())

    @app.get("/api/v1/projects/{project_id}/workflow")
    def workflow(project_id: str, request: Request):
        return workflow_snapshot(store, project_id)

    @app.post("/api/v1/projects/{project_id}/inspect")
    def inspect_project(project_id: str, request: Request):
        require_mutation(request)
        from .doctor import run_doctor
        project = store.project(project_id)
        result = run_doctor(Path(project["root"]), mode="research", deep=True)
        selected = json.loads(str(project.get("setup", {}).get("infobases") or "[]"))
        connections = []
        for item in selected:
            try:
                profile = store.resource("connection", str(item))
                if profile.get("last_test_ok"):
                    connections.append(profile)
            except KeyError:
                pass
        return {"ok": result["status"] != "fail" and bool(connections), "doctor": result, "tested_connections": len(connections)}

    @app.post("/api/v1/projects/{project_id}/verify")
    def verify_project(project_id: str, request: Request):
        require_mutation(request)
        from .doctor import run_doctor
        result = run_doctor(Path(store.project(project_id)["root"]), mode="research", deep=True)
        return {"ok": result["status"] == "ok", "doctor": result}

    if testing:
        @app.post("/api/v1/testing/projects/{project_id}/fixture-dashboard")
        def fixture_dashboard(project_id: str, request: Request):
            require_mutation(request)
            root = Path(store.project(project_id)["root"])
            target = canonical_under(root / "outputs" / "review" / "index.html", [root])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("<!doctype html><html><body><h1>Проверочный результат</h1></body></html>", encoding="utf-8")
            return {"artifact": "outputs/review/index.html"}

    @app.put("/api/v1/projects/{project_id}/manifest")
    def manifest(project_id: str, body: ManifestUpdate, request: Request):
        require_mutation(request)
        project = store.project(project_id)
        _validate_manifest_updates(project, body.updates)
        new_hash = update_project_manifest(Path(project["root"]), body.updates, body.expected_hash)
        with store.db:
            store.db.execute("UPDATE projects SET manifest_hash=? WHERE id=?", (new_hash, project_id))
        return {"manifest_hash": new_hash}

    @app.post("/api/v1/projects/{project_id}/manifest/preview")
    def manifest_preview(project_id: str, body: ManifestUpdate, request: Request):
        project = store.project(project_id); path = Path(project["root"]) / "project.toml"
        _validate_manifest_updates(project, body.updates)
        current_hash = file_sha256(path)
        if current_hash != body.expected_hash:
            raise HTTPException(status_code=409, detail="stale project.toml fingerprint")
        proposed = render_project_manifest(path.read_text(encoding="utf-8"), body.updates)
        import difflib
        difference = "\n".join(difflib.unified_diff(path.read_text(encoding="utf-8").splitlines(), proposed.splitlines(), fromfile="project.toml", tofile="project.toml (proposed)", lineterm=""))
        return {"source_hash": current_hash, "proposed": proposed, "diff": difference}

    def resource_routes(kind: str):
        plural = kind + "s"

        @app.get(f"/api/v1/{plural}", name=f"list_{plural}")
        def list_items(request: Request, project_id: str | None = None):
            return store.resources(kind, project_id)

        @app.post(f"/api/v1/{plural}", status_code=201, name=f"create_{kind}")
        def create_item(body: ResourceBody, request: Request):
            require_mutation(request)
            data = dict(body.data)
            project_id = body.project_id or data.pop("project_id", None)
            if kind == "connection":
                channel = data.get("channel")
                if channel not in ALLOWED_CONNECTIONS:
                    raise HTTPException(status_code=422, detail="unsupported connection channel")
                if data.get("role") not in {"customer", "vendor"}:
                    raise HTTPException(status_code=422, detail="connection role must be customer or vendor")
                if data.get("access_mode", "read") not in {"read", "write"}:
                    raise HTTPException(status_code=422, detail="unsupported access mode")
                parsed_target = urllib.parse.urlsplit(str(data.get("url") or ""))
                if parsed_target.username or parsed_target.password:
                    raise HTTPException(status_code=422, detail="credentials must use the write-only secret field")
            if kind == "agent":
                try:
                    validate_agent_profile(store, data, body.id)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from None
                probe = agent_probe(str(data["provider"]))
                data.update({"available": probe["available"], "adapter_version": probe.get("version", ""), "authentication_ready": probe.get("authentication_ready", False), "capabilities": probe.get("capabilities", {})})
            item = store.save_resource(kind, ({"id": body.id} if body.id else {}) | data, project_id)
            if body.secret:
                secret_id = f"{kind}-{item['id']}"
                store.write_secret(secret_id, body.secret)
                data["secret_ref"] = secret_id
                data["secret_present"] = True
                item = store.save_resource(kind, {"id": item["id"]} | data, project_id)
            return item

        @app.get(f"/api/v1/{plural}/{{item_id}}", name=f"get_{kind}")
        def get_item(item_id: str, request: Request):
            try:
                return store.resource(kind, item_id)
            except KeyError:
                raise HTTPException(status_code=404) from None

        @app.put(f"/api/v1/{plural}/{{item_id}}", name=f"update_{kind}")
        def update_item(item_id: str, body: ResourceBody, request: Request):
            return create_item(body.model_copy(update={"id": item_id}), request)

        @app.delete(f"/api/v1/{plural}/{{item_id}}", status_code=204, name=f"delete_{kind}")
        def delete_item(item_id: str, request: Request):
            require_mutation(request)
            store.delete_resource(kind, item_id)
            return Response(status_code=204)

    for resource_kind in ("connection", "agent"):
        resource_routes(resource_kind)

    @app.put("/api/v1/connections/{item_id}/credentials")
    def save_connection_credentials(item_id: str, body: CredentialBody, request: Request):
        require_mutation(request)
        try:
            profile = store.resource("connection", item_id)
        except KeyError:
            raise HTTPException(status_code=404) from None
        secret_id = f"connection-{item_id}"
        store.write_secret(secret_id, body.secret)
        profile.update({"user": body.username, "secret_ref": secret_id, "secret_present": True})
        return store.save_resource("connection", profile, profile.get("project_id"))

    @app.get("/api/v1/artifacts")
    def artifacts(request: Request, project_id: str | None = None):
        return store.artifacts(project_id)

    @app.post("/api/v1/projects/{project_id}/artifacts/discover")
    def discover_artifacts(project_id: str, request: Request):
        require_mutation(request)
        items = store.discover_artifacts(project_id)
        for item in items:
            store.append_event(project_id, "artifact.ready", {"artifact_id": item["id"], "path": item["path"]}, item.get("run_id"))
        return items

    @app.get("/api/v1/prompts")
    def prompts(request: Request, project_id: str | None = None):
        return store.prompts(project_id)

    @app.post("/api/v1/prompts", status_code=201)
    def create_prompt(body: ResourceBody, request: Request):
        require_mutation(request)
        stage_id = str(body.data.get("stage_id") or "")
        from .workspace import STAGE_BY_ID
        template = str(body.data.get("template") or STAGE_BY_ID.get(stage_id, {}).get("prompt_default") or "")
        return store.save_prompt(str(body.project_id or ""), stage_id, template, str(body.data.get("supplement") or ""), body.data.get("variables") or {})

    @app.get("/api/v1/prompts/{prompt_id}")
    def prompt(prompt_id: str, request: Request):
        try:
            return store.prompt(prompt_id)
        except KeyError:
            raise HTTPException(status_code=404) from None

    @app.post("/api/v1/connections/{item_id}/test")
    def probe_connection(item_id: str, request: Request, confirm_write: str = Header(default="", alias="X-Confirm-Write")):
        require_mutation(request)
        profile = store.resource("connection", item_id)
        if profile.get("access_mode", "read") != "read" and confirm_write.lower() != "true":
            raise HTTPException(status_code=409, detail={"code": "write_confirmation_required", "target": profile.get("url"), "impact": "connection probe may write to the infobase"})
        secret = store.secret(profile["secret_ref"]) if profile.get("secret_ref") else ""
        result = test_connection(profile, secret)
        profile["last_test_ok"] = bool(result.get("ok"))
        profile["tested_capabilities"] = result.get("capabilities") or []
        profile["tested_target"] = result.get("target")
        store.save_resource("connection", profile, profile.get("project_id"))
        if profile.get("access_mode", "read") != "read" and profile.get("project_id"):
            store.append_event(str(profile["project_id"]), "connection.write_probe", {"connection_id": item_id, "target": result.get("target"), "confirmed": True})
        return result

    @app.get("/api/v1/agent-providers")
    def providers(request: Request):
        return [agent_probe(provider) for provider in sorted(ALLOWED_PROVIDERS)]

    @app.get("/api/v1/runs")
    def runs(request: Request, project_id: str | None = None):
        return store.runs(project_id)

    @app.get("/api/v1/runs/{run_id}")
    def run(run_id: str, request: Request):
        try:
            return store.run(run_id)
        except KeyError:
            raise HTTPException(status_code=404) from None

    @app.get("/api/v1/runs/{run_id}/logs")
    def run_logs(run_id: str, request: Request, offset: int = 0, limit: int = 200):
        return store.log_page(run_id, offset, limit)

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

    @app.post("/api/v1/runs/{run_id}/retry", status_code=202)
    def retry_run(run_id: str, request: Request, idempotency_key: str = Header(alias="Idempotency-Key")):
        require_mutation(request)
        run = store.run(run_id)
        if run["status"] not in {"failed", "interrupted", "cancelled"}:
            raise HTTPException(status_code=409, detail="only failed, interrupted, or cancelled runs can be retried")
        payload = {key: value for key, value in run["snapshot"]["payload"].items() if key in {"paths", "agent_profile_id", "timeout_seconds", "workers"}}
        return manager.start(run["project_id"], run["stage_id"], payload, idempotency_key)

    @app.get("/api/v1/approvals")
    def approvals(request: Request, project_id: str | None = None):
        return store.approvals(project_id)

    @app.post("/api/v1/approvals", status_code=201)
    async def request_approval(request: Request, idempotency_key: str = Header(alias="Idempotency-Key")):
        require_mutation(request)
        body = await request.json()
        return store.request_approval(body["project_id"], body["stage_id"], body.get("data") or {}, idempotency_key)

    @app.post("/api/v1/approvals/{approval_id}/decision")
    async def decide(approval_id: str, request: Request):
        require_mutation(request)
        body = await request.json()
        try:
            return store.decide(approval_id, str(body.get("choice")), "local")
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/api/v1/events")
    async def events(request: Request, project_id: str, run_id: str | None = None, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
        after = int(last_event_id or 0)

        async def stream():
            cursor = after
            while not await request.is_disconnected():
                rows, reset = store.events(project_id, cursor, run_id=run_id)
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
    def artifact(project_id: str, artifact_path: str, request: Request, download: bool = False, embed: bool = False):
        project = store.project(project_id)
        root = Path(project["root"])
        try:
            target = canonical_under(root / artifact_path, [root])
        except ValueError:
            raise HTTPException(status_code=404) from None
        if not target.is_file():
            raise HTTPException(status_code=404)
        media = "text/html" if target.suffix.lower() == ".html" else "application/octet-stream"
        safe_dashboard = artifact_path in {"outputs/clean-comparison-dashboard/index.html", "outputs/review/index.html", "outputs/functional-gap-dashboard/index.html"}
        disposition = "inline" if embed and safe_dashboard else "attachment" if download or target.suffix.lower() == ".html" else "inline"
        headers = {"Content-Disposition": f'{disposition}; filename="{target.name}"'}
        if embed and safe_dashboard:
            headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; script-src 'unsafe-inline'; connect-src 'none'; frame-ancestors 'self'; form-action 'none'; base-uri 'none'"
        return FileResponse(target, media_type=media, filename=target.name if disposition == "attachment" else None, headers=headers)

    static_dir = Path(__file__).with_name("workspace_static")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        target = static_dir / path if path else static_dir / "index.html"
        if target.is_file():
            headers = {"Cache-Control": "no-store"} if target.name == "index.html" else None
            return FileResponse(target, headers=headers)
        index = static_dir / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-store"})
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
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Workspace: {url}", flush=True)
    if not args.no_browser:
        threading = __import__("threading")
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    roots = [Path(value).resolve() for value in args.workspace_root] or [Path.cwd()]
    uvicorn.run(create_app(approved_roots=roots), host=args.host, port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
