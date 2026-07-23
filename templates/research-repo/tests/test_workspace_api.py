import hashlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from one_c_autoresearch.workspace_api import create_app
from one_c_autoresearch.contracts import external_id
from one_c_autoresearch.sources import draft_fingerprint


REPO = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("counts", "reason", "absent_roles", "tools_complete"),
    [
        ({"managed": 1, "ordinary": 0, "inconclusive": 0}, "managed_only", [], True),
        ({"managed": 0, "ordinary": 1, "inconclusive": 0}, "ordinary_form_present", [], True),
        ({"managed": 1, "ordinary": 1, "inconclusive": 0}, "ordinary_form_present", [], True),
        ({"managed": 0, "ordinary": 0, "inconclusive": 1}, "inconclusive_form_payload", [], True),
        ({"managed": 1, "ordinary": 0, "inconclusive": 0}, "managed_only", ["next_vendor"], True),
        ({"managed": 0, "ordinary": 1, "inconclusive": 0}, "ordinary_form_present", [], False),
    ],
)
def test_source_tool_and_routing_preview_api_matrix(tmp_path: Path, monkeypatch, counts, reason, absent_roles, tools_complete):
    connections = {role: {"platform_path": "/opt/1cv8/x86_64/8.3.27.1989"} for role in ("vendor_baseline", "target_cf", "next_vendor")}
    monkeypatch.setattr("one_c_autoresearch.user_state.load_connections", lambda *_args: connections)
    monkeypatch.setattr("one_c_autoresearch.source_tools.discover_tools", lambda _roots: {"schema_version": "1", "complete": tools_complete, "checked_at": "2026-01-01T00:00:00Z", "inventory_fingerprint": "sha256:" + "a" * 64, "tools": [], "diagnostics": []})
    preview_value = {
        "schema_version": "1",
        "routing_plan_fingerprint": "sha256:" + "b" * 64,
        "bindings": {"workflow": "sha256:" + "c" * 64, "workflow_state": "sha256:" + "d" * 64, "infobases": "sha256:" + "e" * 64, "external_artifacts": "sha256:" + "f" * 64, "connections": "sha256:" + "1" * 64, "upload_draft": "sha256:" + "2" * 64, "probe_contract": "form-probe/v1"},
        "probe_results": [],
        "required_tools": ["ibcmd"],
        "routing_manifest": {"schema_version": "2", "routing_contract_version": "form-routing/v1", "groups": [{"routing_group_id": "configuration", "members": [], "absent_roles": absent_roles, "form_counts": counts, "routing_reason": reason, "exporter": "ibcmd", "representation_schema": "v8unpack/v1" if counts["ordinary"] or counts["inconclusive"] else "xml-hierarchical/v1", "probe_contract_version": "form-probe/v1", "probe_fingerprints": [], "exporter_version": "8.3.27.1989", "converter_version": "0.9.7" if counts["ordinary"] or counts["inconclusive"] else ""}], "routing_manifest_fingerprint": "sha256:" + "3" * 64},
    }
    monkeypatch.setattr("one_c_autoresearch.sources.build_routing_preview", lambda *_args, **_kwargs: preview_value)
    app = create_app(tmp_path / "state", [REPO], testing=True)
    headers = {"Origin": "http://testserver", "Idempotency-Key": "bookmark"}
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "test", "root": str(REPO)}, headers=headers).json()
        assert client.get(f"/api/v1/projects/{project['id']}/source-tools").json()["complete"] is tools_complete
        created = client.post(f"/api/v1/projects/{project['id']}/source-routing-previews", headers=headers | {"Idempotency-Key": "preview"})
        assert created.status_code == 202, created.text
        preview_id = created.json()["preview_id"]
        for _ in range(100):
            current = client.get(f"/api/v1/projects/{project['id']}/source-routing-previews/{preview_id}").json()
            if current["status"] == "ready":
                break
            time.sleep(0.01)
        assert current["status"] == "ready" and current["routing_plan_fingerprint"] == preview_value["routing_plan_fingerprint"]
        assert current["routing_manifest"]["groups"][0]["form_counts"] == counts
        assert current["routing_manifest"]["groups"][0]["absent_roles"] == absent_roles
        assert str(REPO) not in str(current)
        events = client.get(f"/api/v1/projects/{project['id']}/events").json()["events"]
        assert any(item["run_id"] == preview_id and item["type"] == "run.finished" for item in events)


def test_routing_preview_can_be_cancelled_and_retried(tmp_path: Path, monkeypatch):
    connections = {role: {"platform_path": "/opt/1cv8/x86_64/8.3.27.1989"} for role in ("vendor_baseline", "target_cf", "next_vendor")}
    monkeypatch.setattr("one_c_autoresearch.user_state.load_connections", lambda *_args: connections)
    mode = {"block": True}

    def preview(*_args, cancelled, **_kwargs):
        while mode["block"]:
            if cancelled():
                raise InterruptedError("cancelled")
            time.sleep(0.005)
        return {"schema_version": "1", "routing_plan_fingerprint": "sha256:" + "a" * 64, "bindings": {}, "probe_results": [], "required_tools": [], "routing_manifest": {"schema_version": "2", "routing_contract_version": "form-routing/v1", "groups": [], "routing_manifest_fingerprint": "sha256:" + "b" * 64}}

    monkeypatch.setattr("one_c_autoresearch.sources.build_routing_preview", preview)
    app = create_app(tmp_path / "state", [REPO], testing=True)
    headers = {"Origin": "http://testserver", "Idempotency-Key": "bookmark"}
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "test", "root": str(REPO)}, headers=headers).json()
        first = client.post(f"/api/v1/projects/{project['id']}/source-routing-previews", headers=headers | {"Idempotency-Key": "preview-1"}).json()
        cancelled = client.delete(f"/api/v1/projects/{project['id']}/source-routing-previews/{first['preview_id']}", headers=headers | {"Idempotency-Key": "cancel"})
        assert cancelled.json()["status"] == "cancelled"
        mode["block"] = False
        second = client.post(f"/api/v1/projects/{project['id']}/source-routing-previews", headers=headers | {"Idempotency-Key": "preview-2"}).json()
        for _ in range(100):
            current = client.get(f"/api/v1/projects/{project['id']}/source-routing-previews/{second['preview_id']}").json()
            if current["status"] == "ready":
                break
            time.sleep(0.01)
        assert current["status"] == "ready"


def test_routing_preview_reports_inaccessible_required_tool(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("one_c_autoresearch.user_state.load_connections", lambda *_args: {"p": {"platform_path": "/opt/1cv8/x86_64/8.3.27.1989"}})
    monkeypatch.setattr("one_c_autoresearch.sources.build_routing_preview", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("source_tool_unavailable:v8unpack")))
    app = create_app(tmp_path / "state", [REPO], testing=True)
    headers = {"Origin": "http://testserver", "Idempotency-Key": "bookmark"}
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "test", "root": str(REPO)}, headers=headers).json()
        created = client.post(f"/api/v1/projects/{project['id']}/source-routing-previews", headers=headers | {"Idempotency-Key": "preview"}).json()
        for _ in range(100):
            current = client.get(f"/api/v1/projects/{project['id']}/source-routing-previews/{created['preview_id']}").json()
            if current["status"] == "failed":
                break
            time.sleep(0.01)
        assert current["status"] == "failed"
        assert current["error"] == "routing preview failed (RuntimeError)"


def test_run_next_rejects_stale_routing_confirmation(tmp_path: Path, monkeypatch):
    app = create_app(tmp_path / "state", [REPO], testing=True)
    headers = {"Origin": "http://testserver", "Idempotency-Key": "bookmark"}
    monkeypatch.setattr("one_c_autoresearch.runner.run_next", lambda _repo, invoke, _store, **kwargs: invoke("sources.acquire", kwargs["source_routing_preview"], lambda: False))
    monkeypatch.setattr("one_c_autoresearch.service.ApplicationService.apply", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("routing_preview_stale")))
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "test", "root": str(REPO)}, headers=headers).json()
        snapshot = client.get(f"/api/v1/projects/{project['id']}/workflow").json()
        response = client.post(
            f"/api/v1/projects/{project['id']}/workflow/run-next",
            json={"expected_fingerprint": snapshot["workflow_fingerprint"], "approved_operations": ["sources.acquire"], "source_routing_preview_id": "preview", "routing_plan_fingerprint": "sha256:" + "0" * 64},
            headers=headers | {"Idempotency-Key": "run"},
        )
        assert response.status_code == 409
        assert "routing_preview_stale" in response.text


def test_api_uses_repository_snapshot_and_typed_actions(tmp_path: Path, monkeypatch):
    tester = lambda profile_id, _platform, profile: {"profile_id": profile_id, "tested": True, "extensions": [], "tool_version": "8.3.27.1989", "tested_fingerprint": "sha256:" + "a" * 64}
    app = create_app(tmp_path / "state", [REPO], testing=True, connection_tester=tester)
    headers = {"Origin": "http://testserver", "Idempotency-Key": "bookmark-1"}
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "example", "root": str(REPO)}, headers=headers).json()
        snapshot = client.get(f"/api/v1/projects/{project['id']}/workflow").json()
        assert len(snapshot["gates"]) == 7
        configuration = client.get(f"/api/v1/projects/{project['id']}/workflow/configuration").json()
        assert [job["id"] for job in configuration["jobs"]] == ["configure", "acquire-sources", "build-diffs", "index-sources", "discover-mrq", "decide-mrq", "publish"]
        assert len(configuration["steps"]) == 8
        registry = client.get(f"/api/v1/projects/{project['id']}/registries/diff-inventory?offset=0&limit=1").json()
        assert len(registry["items"]) <= 1 and registry["limit"] == 1
        rejected = client.post(f"/api/v1/projects/{project['id']}/actions", json={"operation": "shell", "payload": {"command": "rm"}, "expected_fingerprint": snapshot["workflow_fingerprint"]}, headers=headers | {"Idempotency-Key": "run-1"})
        assert rejected.status_code == 422
        assert client.post(f"/api/v1/projects/{project['id']}/actions", json={"operation": "shell", "payload": {"command": "rm"}, "expected_fingerprint": snapshot["workflow_fingerprint"]}, headers=headers | {"Idempotency-Key": "run-1"}).status_code == 409
        events = client.get(f"/api/v1/projects/{project['id']}/events").json()["events"]
        assert [item["type"] for item in events] == ["run.created", "run.finished"]
        verified = client.post(f"/api/v1/projects/{project['id']}/actions", json={"operation": "workflow.verify", "payload": {}, "expected_fingerprint": snapshot["workflow_fingerprint"]}, headers=headers | {"Idempotency-Key": "verify-1"})
        assert verified.status_code == 200
        (tmp_path / "state/projects" / project["id"] / "events.jsonl").unlink()
        repeated = client.post(f"/api/v1/projects/{project['id']}/actions", json={"operation": "workflow.verify", "payload": {}, "expected_fingerprint": snapshot["workflow_fingerprint"]}, headers=headers | {"Idempotency-Key": "verify-1"})
        assert repeated.status_code == 200 and repeated.json() == verified.json()
        assert client.get("/api/v1/health", headers={"Host": "evil.example"}).status_code == 400
        assert client.post(f"/api/v1/projects/{project['id']}/actions", json={"operation": "workflow.verify", "payload": {}, "expected_fingerprint": snapshot["workflow_fingerprint"]}, headers={"Idempotency-Key": "forged"}).status_code == 403
        assert "default-src 'self'" in client.get("/api/v1/health").headers["content-security-policy"]
        assert client.get("/api/v1/" + "stages").status_code == 404
        assert client.get(f"/api/v1/projects/{project['id']}/" + "preferences").status_code == 404
        monkeypatch.setattr("one_c_autoresearch.runner.run_until_blocked", lambda *_args, **_kwargs: {"result": "blocked", "runs": []})
        until = client.post(f"/api/v1/projects/{project['id']}/workflow/run-until-blocked", json={"expected_fingerprint": snapshot["workflow_fingerprint"], "max_units": 3}, headers=headers | {"Idempotency-Key": "until-1"})
        assert until.status_code == 200 and until.json()["result"] == "blocked"
        cancelled = client.post(f"/api/v1/projects/{project['id']}/runs/run-1/cancel", json={"actor": "local-user"}, headers=headers | {"Idempotency-Key": "cancel-1"})
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancellation_requested"
        artifact = tmp_path / "state/projects" / project["id"] / "artifacts/result.json"
        artifact.parent.mkdir(parents=True); artifact.write_text("{}", encoding="utf-8")
        downloaded = client.get(f"/api/v1/projects/{project['id']}/operational-artifacts/artifacts/result.json")
        assert downloaded.status_code == 200 and downloaded.headers["content-security-policy"] == "sandbox"
        profile = {"kind": "server", "server": "localhost", "reference": "baseline", "profile_id": "ibcmd+form-aware/v1", "tested": True, "platform_path": "/opt/1cv8/x86_64/8.3.27.1989", "dbms": "PostgreSQL", "db_server": "localhost port=5432", "db_name": "baseline", "db_user": "postgres", "db_password": "private-db", "infobase_user": "chatgpt", "infobase_password": "private-ib", "extensions": []}
        saved = client.put(f"/api/v1/projects/{project['id']}/connection-profiles/local-baseline", json={"profile": profile}, headers=headers | {"Idempotency-Key": "profile-1"})
        assert saved.status_code == 200
        setup = client.get(f"/api/v1/projects/{project['id']}/source-setup").json()
        assert setup["profiles"] == ["designer+form-aware/v1", "ibcmd+form-aware/v1"]
        assert setup["connection_profiles"]["local-baseline"]["available"] is True
        assert "private-db" not in str(setup) and "private-ib" not in str(setup)
        stored = tmp_path / "state/projects" / project["id"] / "connections.json"
        assert stored.stat().st_mode & 0o777 == 0o600
        agent = {"provider": "codex-cli", "model": "gpt-5", "reasoning_effort": "high", "instructions_version": "1"}
        configured = client.put(f"/api/v1/projects/{project['id']}/agent-profiles/local", json={"profile": agent}, headers=headers | {"Idempotency-Key": "agent-profile-1"})
        assert configured.status_code == 200 and client.get(f"/api/v1/projects/{project['id']}/agent-profiles").json()["items"] == {"local": agent}
        assert (tmp_path / "state/projects" / project["id"] / "agent-profiles.json").stat().st_mode & 0o777 == 0o600


def test_external_upload_uses_declared_size_bytes(tmp_path: Path):
    repo = tmp_path / "repo"
    research = repo / "research"
    research.mkdir(parents=True)
    (repo / "project.toml").write_bytes((REPO / "project.toml").read_bytes())
    (research / "workflow.toml").write_bytes((REPO / "research/workflow.toml").read_bytes())
    (research / "infobases.toml").write_text('schema_version = "1"\n', encoding="utf-8")
    payload = b"external-artifact"
    digest = hashlib.sha256(payload).hexdigest()
    (research / "external-artifacts.toml").write_text(
        'schema_version = "1"\n[[artifacts]]\nrole = "target_cf"\nkind = "epf"\n'
        f'semantic_key = "report"\nfilename = "report.epf"\ndeclared_size_bytes = {len(payload)}\nsha256 = "{digest}"\n',
        encoding="utf-8",
    )
    app = create_app(tmp_path / "state", [repo], testing=True)
    headers = {"Origin": "http://testserver", "Idempotency-Key": "bookmark"}
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "test", "root": str(repo)}, headers=headers).json()
        draft_root = tmp_path / "state/projects" / project["id"] / "upload-drafts"
        identifier = external_id("epf", "report")
        response = client.put(
            f"/api/v1/projects/{project['id']}/external-uploads/target_cf/{identifier}",
            params={"filename": "report.epf", "declared_length": len(payload), "declared_sha256": digest, "expected_draft_fingerprint": draft_fingerprint(draft_root)},
            content=payload,
            headers=headers | {"Idempotency-Key": "upload"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["size_bytes"] == len(payload)


def test_external_folder_http_flow_is_paged_and_stages_selected_bytes(tmp_path: Path):
    repo = tmp_path / "repo"; research = repo / "research"; research.mkdir(parents=True)
    (repo / "project.toml").write_bytes((REPO / "project.toml").read_bytes())
    (research / "workflow.toml").write_bytes((REPO / "research/workflow.toml").read_bytes())
    (research / "infobases.toml").write_text('schema_version = "1"\n', encoding="utf-8")
    (research / "external-artifacts.toml").write_text('schema_version = "1"\n', encoding="utf-8")
    app = create_app(tmp_path / "state", [repo], testing=True); base_headers = {"Origin": "http://testserver", "Idempotency-Key": "bookmark"}
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "test", "root": str(repo)}, headers=base_headers).json()
        fingerprint = client.get(f"/api/v1/projects/{project['id']}/workflow").json()["workflow_fingerprint"]
        payload = b"folder artifact"
        created = client.post(f"/api/v1/projects/{project['id']}/external-folder-previews", json={"entries": [{"relative_path": "selected/nested/report.EPF", "size_bytes": len(payload)}, {"relative_path": "selected/readme.txt", "size_bytes": 1}], "expected_fingerprint": fingerprint, "role": "target_cf"}, headers=base_headers | {"Idempotency-Key": "create"})
        assert created.status_code == 201, created.text
        preview = created.json(); entry = preview["entries"][0]
        assert client.post(f"/api/v1/projects/{project['id']}/external-folder-previews", json={"entries": [], "expected_fingerprint": fingerprint, "role": "target_cf"}, headers=base_headers | {"Idempotency-Key": "create"}).json()["preview_id"] == preview["preview_id"]
        uploaded = client.put(f"/api/v1/projects/{project['id']}/external-folder-previews/{preview['preview_id']}/entries/{entry['entry_id']}", content=payload, headers=base_headers | {"Idempotency-Key": "upload"})
        assert uploaded.status_code == 200, uploaded.text
        finalized = client.post(f"/api/v1/projects/{project['id']}/external-folder-previews/{preview['preview_id']}/finalize", headers=base_headers | {"Idempotency-Key": "finalize"})
        assert finalized.status_code == 200, finalized.text
        assert client.post(f"/api/v1/projects/{project['id']}/external-folder-previews/{preview['preview_id']}/finalize", headers=base_headers | {"Idempotency-Key": "finalize"}).json() == finalized.json()
        value = finalized.json(); assert value["ignored_unsupported_count"] == 1 and value["entries"][0]["filename"] == "nested/report.EPF"
        page = client.get(f"/api/v1/projects/{project['id']}/external-folder-previews/{preview['preview_id']}/entries?limit=1").json(); assert len(page["items"]) == 1
        assert client.get(f"/api/v1/projects/{project['id']}/external-folder-previews/{preview['preview_id']}/entries?limit=201").status_code == 422
        selection = [{"entry_id": entry["entry_id"], "semantic_key": "reviewed-report"}]
        reviewed = client.post(f"/api/v1/projects/{project['id']}/external-folder-previews/{preview['preview_id']}/declaration-diff-preview", json={"selected_entries": selection, "expected_declaration_fingerprint": value["declaration_fingerprint"]}, headers=base_headers | {"Idempotency-Key": "review-diff"})
        assert reviewed.status_code == 200 and 'semantic_key = "reviewed-report"' in reviewed.json()["diff"]
        confirmed = client.post(f"/api/v1/projects/{project['id']}/external-folder-previews/{preview['preview_id']}/confirm", json={"selected_entries": selection, "expected_fingerprint": fingerprint, "expected_declaration_fingerprint": value["declaration_fingerprint"], "expected_draft_fingerprint": value["draft_fingerprint"], "confirm": True}, headers=base_headers | {"Idempotency-Key": "confirm"})
        assert confirmed.status_code == 200, confirmed.text
        assert client.post(f"/api/v1/projects/{project['id']}/external-folder-previews/{preview['preview_id']}/confirm", json={"selected_entries": [], "expected_fingerprint": fingerprint, "expected_declaration_fingerprint": "stale", "expected_draft_fingerprint": "stale", "confirm": True}, headers=base_headers | {"Idempotency-Key": "confirm"}).json() == confirmed.json()
        assert confirmed.json()["status"] == "complete" and list((tmp_path / "state/projects" / project["id"] / "upload-drafts").rglob("report.EPF"))[0].read_bytes() == payload
        assert 'semantic_key = "reviewed-report"' in (research / "external-artifacts.toml").read_text(encoding="utf-8")
        events = str(client.get(f"/api/v1/projects/{project['id']}/events").json())
        assert preview["preview_id"] not in events and "selected/nested/report.EPF" not in events and payload.decode() not in events
        assert "preview_digest" in events and "status_counts" in events


def test_old_repository_bookmark_has_no_compatibility_reader(tmp_path: Path):
    old = tmp_path / "old"; old.mkdir(); (old / "project.toml").write_text('[project]\nid="old"\n', encoding="utf-8")
    app = create_app(tmp_path / "state", [tmp_path], testing=True)
    headers = {"Origin": "http://testserver", "Idempotency-Key": "bookmark"}
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "old", "root": str(old)}, headers=headers)
        assert project.status_code == 201
        opened = client.get(f"/api/v1/projects/{project.json()['id']}/workflow")
        assert opened.status_code == 409
        assert "recreate" in opened.json()["detail"]


def test_security_boundary_rejects_traversal_and_redacts_conflicts(tmp_path: Path, monkeypatch):
    app = create_app(tmp_path / "state", [REPO], testing=True)
    headers = {"Origin": "http://testserver", "Idempotency-Key": "bookmark"}
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "example", "root": str(REPO)}, headers=headers).json()
        assert client.get(f"/api/v1/projects/{project['id']}/artifacts/../../project.toml").status_code == 404
        artifact = client.get(f"/api/v1/projects/{project['id']}/artifacts/outputs/projections.json")
        assert artifact.status_code == 200 and artifact.headers["content-security-policy"] == "sandbox" and artifact.headers["content-disposition"].startswith("attachment")
        monkeypatch.setattr("one_c_autoresearch.service.ApplicationService.apply", lambda *_: (_ for _ in ()).throw(RuntimeError("--db-pwd=plain-secret")))
        snapshot = client.get(f"/api/v1/projects/{project['id']}/workflow").json()
        response = client.post(f"/api/v1/projects/{project['id']}/actions", json={"operation": "workflow.verify", "payload": {}, "expected_fingerprint": snapshot["workflow_fingerprint"]}, headers=headers | {"Idempotency-Key": "conflict"})
        assert response.status_code == 409 and "plain-secret" not in response.text
