from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from one_c_autoresearch.workspace import WorkspacePaths
from one_c_autoresearch.workspace_api import create_app, discover_1c_infobases


def test_ibases_discovery_ignores_credentials(tmp_path: Path) -> None:
    source = tmp_path / "ibases.v8i"
    source.write_text('[Demo]\nConnect=Srvr="localhost";Ref="demo";\nFolder=/Local\nDefaultVersion=8.3.27.1989\nAdditionalParameters=/N"user" /P"password"\n\n[Folder]\nFolder=/\n', encoding="utf-8-sig")
    result = discover_1c_infobases(source)
    assert result == [{"name": "Demo", "connection_string": 'Srvr="localhost";Ref="demo";', "connection_kind": "server", "server": "localhost", "reference": "demo", "file": "", "folder": "/Local", "platform_version": "8.3.27.1989", "credentials_ignored": True}]
    assert "password" not in str(result)


def test_local_browser_flow_and_security(tmp_path: Path) -> None:
    root = tmp_path / "research"; root.mkdir(); (root / "project.toml").write_text('[project]\nid="p"\nname="P"\n', encoding="utf-8")
    app = create_app(WorkspacePaths(tmp_path / "state"), [tmp_path], testing=True)
    with TestClient(app) as client:
        assert client.get("/api/v1/projects").status_code == 200
        headers = {"Origin": "http://testserver"}
        listing = client.get("/api/v1/filesystem/directories", params={"path": str(tmp_path)}).json()
        assert listing["current"] == str(tmp_path) and any(item["name"] == "research" for item in listing["directories"])
        assert client.get("/api/v1/filesystem/directories", params={"path": str(tmp_path.parent)}).status_code == 422
        assert client.post("/api/v1/projects", json={"name": "P", "root": str(root)}).status_code == 403
        created = client.post("/api/v1/projects", json={"name": "P", "root": str(root)}, headers=headers)
        assert created.status_code == 201
        generated = tmp_path / "generated"
        assert client.post("/api/v1/projects", json={"name": "Generated", "root": str(generated)}, headers=headers).status_code == 201
        assert (generated / ".git").is_dir()
        connection = client.post("/api/v1/connections", json={"project_id": "p", "data": {"name": "Demo", "role": "customer", "channel": "onec", "connection_string": 'Srvr="localhost";Ref="demo";', "access_mode": "read"}}, headers=headers).json()
        credentials = client.put(f"/api/v1/connections/{connection['id']}/credentials", json={"username": "user", "secret": "password"}, headers=headers).json()
        assert credentials["user"] == "user" and credentials["secret_present"] is True and "password" not in str(credentials)
        assert app.state.store.secret(credentials["secret_ref"]) == "password"
        setup = {"product": "ERP", "step": 1}
        assert client.put("/api/v1/projects/p/setup", json=setup, headers=headers).json()["setup"] == setup
        workflow = client.get("/api/v1/projects/p/workflow").json()
        assert workflow["stages"][0]["ready"] is True and workflow["stages"][1]["ready"] is False
        dispatched = client.post("/api/v1/runs", json={"project_id": "p", "stage_id": "intake", "payload": {"fake": True}}, headers=headers | {"Idempotency-Key": "run-1"})
        assert dispatched.status_code == 202
        assert client.post("/api/v1/runs", json={"project_id": "p", "stage_id": "intake", "payload": {"fake": True}}, headers=headers | {"Idempotency-Key": "run-1"}).json()["id"] == dispatched.json()["id"]
        assert client.get("/api/v1/projects", headers={"Host": "evil.example"}).status_code == 400
        assert "default-src 'self'" in client.get("/api/v1/health").headers["content-security-policy"]
        assert client.get("/").headers["cache-control"] == "no-store"


def test_artifact_confinement_and_sandbox_headers(tmp_path: Path) -> None:
    root = tmp_path / "research"; dashboard = root / "outputs" / "review"; dashboard.mkdir(parents=True)
    (root / "project.toml").write_text('[project]\nid="p"\nname="P"\n', encoding="utf-8")
    (dashboard / "index.html").write_text("<script>parent.location='https://evil'</script>", encoding="utf-8")
    app = create_app(WorkspacePaths(tmp_path / "state"), [tmp_path], testing=True)
    with TestClient(app) as client:
        headers = {"Origin": "http://testserver"}
        client.post("/api/v1/projects", json={"name": "P", "root": str(root)}, headers=headers)
        embedded = client.get("/api/v1/artifacts/p/outputs/review/index.html?embed=true")
        assert embedded.status_code == 200 and "connect-src 'none'" in embedded.headers["content-security-policy"]
        assert client.get("/api/v1/artifacts/p/%2e%2e/%2e%2e/project.toml").status_code == 404
