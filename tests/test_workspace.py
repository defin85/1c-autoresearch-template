from __future__ import annotations

import json
import os
import stat
import sqlite3
import time
from pathlib import Path

import pytest

from one_c_autoresearch.common import file_sha256
from one_c_autoresearch.workspace import (
    RunManager,
    WorkspacePaths,
    WorkspaceStore,
    canonical_under,
    agent_command,
    agent_probe,
    normalize_agent_result,
    update_project_manifest,
    validate_agent_profile,
    workflow_snapshot,
    STAGE_BY_ID,
)


def project(tmp_path: Path) -> Path:
    root = tmp_path / "research"
    root.mkdir()
    (root / "project.toml").write_text('[project]\nid = "fixture"\nname = "Fixture"\n\n[paths]\ntarget_cf = "sources/cf"\n', encoding="utf-8")
    (root / "sources" / "cf").mkdir(parents=True)
    return root


@pytest.fixture
def store(tmp_path: Path) -> WorkspaceStore:
    value = WorkspaceStore(WorkspacePaths(tmp_path / "state"))
    yield value
    value.close()


def test_state_migration_secrets_paths_and_removal(store: WorkspaceStore, tmp_path: Path) -> None:
    root = project(tmp_path)
    registered = store.register_project("Fixture", root, [tmp_path])
    assert registered["id"] == "fixture"
    assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert store.db.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    store.write_secret("mcp-token", "password=hunter2")
    mode = stat.S_IMODE((store.paths.credentials / "mcp-token").stat().st_mode)
    assert mode == 0o600
    assert "hunter2" not in json.dumps(store.resources("connection"))
    connection = store.save_resource("connection", {"name": "secured", "secret_ref": "mcp-token"})
    store.delete_resource("connection", connection["id"])
    assert not (store.paths.credentials / "mcp-token").exists()
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    with pytest.raises(ValueError):
        canonical_under(outside, [root])
    link = root / "escape"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        canonical_under(link / "file", [root])
    state_root = store.paths.root
    store.close()
    import shutil
    shutil.rmtree(state_root)
    assert (root / "project.toml").is_file()


def test_existing_database_is_migrated_in_place(tmp_path: Path) -> None:
    paths = WorkspacePaths(tmp_path / "old-state"); paths.root.mkdir()
    db = sqlite3.connect(paths.database)
    db.executescript("CREATE TABLE schema_version(version INTEGER NOT NULL); INSERT INTO schema_version VALUES(1); CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,root TEXT NOT NULL UNIQUE,manifest_hash TEXT NOT NULL DEFAULT '',setup_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);")
    db.close()
    store = WorkspaceStore(paths)
    try:
        assert "approved_roots_json" in {row[1] for row in store.db.execute("PRAGMA table_info(projects)")}
        assert store.db.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    finally:
        store.close()


def test_manifest_compare_and_swap_and_external_workflow_reconciliation(store: WorkspaceStore, tmp_path: Path) -> None:
    root = project(tmp_path)
    store.register_project("Fixture", root, [tmp_path])
    fingerprint = file_sha256(root / "project.toml")
    changed = update_project_manifest(root, {"project": {"name": "Updated"}}, fingerprint)
    assert changed != fingerprint and 'name = "Updated"' in (root / "project.toml").read_text(encoding="utf-8")
    with pytest.raises(RuntimeError, match="stale"):
        update_project_manifest(root, {"project": {"name": "Bad"}}, fingerprint)
    (root / "outputs" / "review").mkdir(parents=True)
    (root / "outputs" / "review" / "index.html").write_text("result", encoding="utf-8")
    dashboards = next(stage for stage in workflow_snapshot(store, "fixture")["stages"] if stage["id"] == "dashboards")
    assert dashboards["artifact"] == "outputs/review/index.html"


def test_prompt_versions_fallback_graph_and_normalization(store: WorkspaceStore, tmp_path: Path) -> None:
    root = project(tmp_path); store.register_project("Fixture", root, [tmp_path])
    first = store.save_prompt("fixture", "feature-research", "Study {{product}}", "Focus A", {"product": "ERP"})
    second = store.save_prompt("fixture", "feature-research", "Study {{product}}", "Focus B", {"product": "ERP"})
    assert first["version"] == 1 and second["version"] == 2 and "Focus B" in second["diff"]
    assert second["assembled"].startswith("Follow AGENTS.md")
    a = store.save_resource("agent", {"provider": "codex", "model": "m", "workers": 1}, None)
    b = store.save_resource("agent", {"provider": "codex", "model": "m", "workers": 1, "fallback_profile": a["id"]}, None)
    with pytest.raises(ValueError, match="cycle"):
        validate_agent_profile(store, {"provider": "codex", "model": "m", "fallback_profile": b["id"]}, a["id"])
    normalized = normalize_agent_result("codex", '{"decisions":[],"evidence":[{"path":"x"}]}', {"x": "abc"})
    assert normalized["source_fingerprints"] == {"x": "abc"}
    with pytest.raises(ValueError, match="schema"):
        normalize_agent_result("claude", '{"result":"{}"}', {})


def test_fixed_agent_adapters_with_fake_executables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "bin"; binary.mkdir()
    for name in ("codex", "claude"):
        path = binary / name
        path.write_text("#!/bin/sh\nprintf '%s\\n' 'fake 1.0'\n", encoding="utf-8")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    assert agent_probe("codex")["available"] and agent_probe("claude")["available"]
    codex = agent_command("codex", "model", "prompt", tmp_path)
    claude = agent_command("claude", "model", "prompt", tmp_path)
    assert codex[:3] == ["codex", "exec", "--ephemeral"] and "read-only" in codex
    assert claude[:3] == ["claude", "-p", "--output-format"] and "Read,Glob,Grep" in claude
    assert all("shell" not in argument.lower() for argument in codex + claude)


def test_discovered_1c_connection_requires_installed_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    from one_c_autoresearch.workspace import test_connection
    monkeypatch.setattr(Path, "is_file", lambda self: str(self).endswith("/8.3.27/1cv8"))
    result = test_connection({"channel": "onec", "connection_string": 'Srvr="localhost";Ref="demo";', "platform_version": "8.3.27"})
    assert result["ok"] and result["capabilities"] == ["designer"]


def test_runs_are_async_idempotent_bounded_and_reconciled(store: WorkspaceStore, tmp_path: Path) -> None:
    root = project(tmp_path); store.register_project("Fixture", root, [tmp_path])
    manager = RunManager(store, allow_fake=True)
    run = manager.start("fixture", "intake", {"fake": True}, "same")
    assert run["status"] in {"queued", "running"}
    assert manager.start("fixture", "intake", {"fake": True}, "same")["id"] == run["id"]
    with pytest.raises(RuntimeError, match="different payload"):
        manager.start("fixture", "intake", {"fake": False}, "same")
    deadline = time.time() + 5
    while store.run(run["id"])["status"] not in {"completed", "failed"} and time.time() < deadline:
        time.sleep(0.05)
    assert store.run(run["id"])["status"] == "completed"
    log = store.paths.runs / run["id"] / "run.log"
    log.write_text("\n".join(str(i) for i in range(700)), encoding="utf-8")
    assert len(store.run(run["id"])["log_tail"]) == 500
    assert store.log_page(run["id"], 0, 100)["next_offset"] == 100
    rows, reset = store.events("fixture", 0)
    assert not reset and [row["id"] for row in rows] == sorted(row["id"] for row in rows)


def test_reconciliation_does_not_interrupt_a_locally_owned_process(store: WorkspaceStore, tmp_path: Path) -> None:
    root = project(tmp_path); store.register_project("Fixture", root, [tmp_path])
    manager = RunManager(store, allow_fake=True)
    run = manager.start("fixture", "intake", {"fake": True, "fake_seconds": 0.5}, "owned")
    manager.reconcile()
    assert store.run(run["id"])["status"] in {"queued", "running"}
    deadline = time.time() + 3
    while store.run(run["id"])["status"] not in {"completed", "failed"} and time.time() < deadline:
        time.sleep(0.05)
    assert store.run(run["id"])["status"] == "completed"


def test_event_retention_gap_and_payload_bound(store: WorkspaceStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path); store.register_project("Fixture", root, [tmp_path])
    monkeypatch.setattr("one_c_autoresearch.workspace.EVENT_RETENTION", 2)
    ids = [store.append_event("fixture", "stage.progress", {"value": value}) for value in range(4)]
    rows, reset = store.events("fixture", ids[0])
    assert reset is True and [row["id"] for row in rows] == ids[-2:]
    with pytest.raises(ValueError, match="too large"):
        store.append_event("fixture", "log.append", {"text": "x" * (70 * 1024)})


def test_decision_queue_is_idempotent_and_typed(store: WorkspaceStore, tmp_path: Path) -> None:
    root = project(tmp_path); store.register_project("Fixture", root, [tmp_path])
    data = {"evidence": ["analysis/x"], "impact": "publication", "choices": ["accept", "reject"]}
    approval = store.request_approval("fixture", "final-gate", data, "decision-1")
    assert store.request_approval("fixture", "final-gate", data, "decision-1")["id"] == approval["id"]
    decided = store.decide(approval["id"], "accept", "local")
    assert decided["status"] == "accepted" and decided["data"]["verified"] is True
    assert (root / decided["data"]["canonical_record"]).is_file()
    assert store.decide(approval["id"], "accept", "local")["status"] == "accepted"


def test_cancel_is_idempotent(store: WorkspaceStore, tmp_path: Path) -> None:
    root = project(tmp_path); store.register_project("Fixture", root, [tmp_path])
    manager = RunManager(store, allow_fake=True)
    run = manager.start("fixture", "intake", {"fake": True}, "cancel")
    cancelled = manager.cancel(run["id"])
    assert cancelled["status"] in {"cancelled", "completed"}
    assert manager.cancel(run["id"])["status"] == cancelled["status"]


def test_mutating_dispatch_is_serialized_and_command_fields_are_rejected(store: WorkspaceStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path); store.register_project("Fixture", root, [tmp_path])
    manager = RunManager(store, allow_fake=True)
    monkeypatch.setitem(STAGE_BY_ID["intake"], "mutation", "write")
    first = manager.start("fixture", "intake", {"fake": True}, "first")
    with pytest.raises(RuntimeError, match="active incompatible"):
        manager.start("fixture", "intake", {"fake": True}, "second")
    with pytest.raises(ValueError, match="unsupported run fields"):
        manager.start("fixture", "intake", {"command": "echo unsafe"}, "unsafe")
    manager.cancel(first["id"])


def test_process_start_failure_is_terminal(store: WorkspaceStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path); store.register_project("Fixture", root, [tmp_path])
    manager = RunManager(store, allow_fake=True)
    monkeypatch.setattr("one_c_autoresearch.workspace.subprocess.Popen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable")))
    with pytest.raises(RuntimeError, match="could not start"):
        manager.start("fixture", "intake", {"fake": True}, "launch-error")
    assert store.runs("fixture")[0]["status"] == "failed"


def test_fake_operations_are_unavailable_in_production(store: WorkspaceStore, tmp_path: Path) -> None:
    root = project(tmp_path); store.register_project("Fixture", root, [tmp_path])
    with pytest.raises(ValueError, match="unsupported run fields"):
        RunManager(store).start("fixture", "intake", {"fake": True}, "forbidden")
