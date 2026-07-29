from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from one_c_autoresearch.events import EventStore
from one_c_autoresearch.dispatcher import DispatcherCoordinator
from one_c_autoresearch.service import ApplicationService
from one_c_autoresearch.sqlite_state import DispatcherStore, dispatcher_db_path
from one_c_autoresearch.workspace_api import (
    _event_cursor,
    _encode_dispatcher_cursor,
    _history_cursor,
    create_app,
)
from one_c_autoresearch.workflow import _dispatcher_items


REPO = Path(__file__).resolve().parents[1] / "templates/research-repo"


def _bookmark(client: TestClient) -> dict:
    return client.post(
        "/api/v1/projects",
        json={"name": "example", "root": str(REPO)},
        headers={"Origin": "http://testserver", "Idempotency-Key": "bookmark"},
    ).json()


def _run(event_store: EventStore, run_id: str) -> str:
    execution = {
        "schema_version": "1",
        "operation": "mrq.discover-next",
        "workflow_fingerprint": "sha256:test",
        "policy_source": "current-policy",
        "profiles": {"local": {"model": "test", "reasoning_effort": "low", "environment_preset": "local-read-only"}},
        "subject_bindings": {
            "source_generation_id": "source",
            "diff_generation_id": "diff",
            "canonical_generation_id": "canonical",
        },
        "agent_phases": [{
            "phase_id": "analyze-dif",
            "roles": [{"role_id": "analyzer", "agent_profile": "local", "count": 1}],
        }],
    }
    fingerprint = event_store.prepare_run(run_id, execution)
    event_store.emit(
        "run.created",
        run_id,
        {
            "status": "running",
            "actor": "test",
            "process_identity": None,
            "workflow_fingerprint": "sha256:test",
            "operation": "mrq.discover-next",
            "execution_snapshot_fingerprint": fingerprint,
            "policy_source": "current-policy",
            "tool_versions": {"codex": "test"},
        },
    )
    return fingerprint


def test_invocation_outbox_is_idempotent_and_preserves_run_status(tmp_path: Path) -> None:
    event_store = EventStore(tmp_path / "events", "project")
    fingerprint = _run(event_store, "run-1")
    with DispatcherStore(tmp_path, tmp_path) as store:
        token = store.acquire_lease(
            "discover-mrq", "thread", "DIF-1", "test", None, run_id="run-1"
        )
        invocation = store.start_invocation(
            "discover-mrq",
            "run-1",
            "analyze-dif",
            "analyzer",
            "DIF-1",
            1,
            token,
            execution_snapshot_fingerprint=fingerprint,
            profile_id="local",
            context_manifest_fingerprint="sha256:context",
        )
        assert invocation is not None
        assert store.reconcile_invocation_outbox(event_store) == 1
        assert store.reconcile_invocation_outbox(event_store) == 0
        assert store.terminalize_invocation(
            invocation["invocation_id"],
            "failed",
            token,
            error_code="provider_blocked",
            error_summary="<script>blocked</script>",
        )
        assert store.reconcile_invocation_outbox(event_store) == 1
        assert not store.terminalize_invocation(
            invocation["invocation_id"], "failed", token
        )
    snapshot = event_store.run_snapshot("run-1")
    assert snapshot is not None and snapshot["status"] == "running"
    lifecycle = [
        event for event in snapshot["events"]
        if event["type"].startswith("invocation.")
    ]
    assert [event["type"] for event in lifecycle] == [
        "invocation.started",
        "invocation.finished",
    ]
    assert lifecycle[-1]["payload"]["invocation_status"] == "failed"


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        ("completed", ""),
        ("failed", "internal_error"),
        ("cancelled", "cancelled"),
        ("interrupted", "interrupted"),
    ],
)
def test_every_invocation_terminal_state_is_idempotent_and_bounded(
    tmp_path: Path, status: str, error_code: str
) -> None:
    event_store = EventStore(tmp_path / "events", "project")
    fingerprint = _run(event_store, f"run-{status}")
    with DispatcherStore(tmp_path, tmp_path) as store:
        token = store.acquire_lease(
            "discover-mrq", "thread", "DIF-1", "test", None,
            run_id=f"run-{status}",
        )
        invocation = store.start_invocation(
            "discover-mrq", f"run-{status}", "analyze-dif", "analyzer",
            "DIF-1", 1, token,
            execution_snapshot_fingerprint=fingerprint,
            profile_id="local",
            context_manifest_fingerprint="sha256:context",
        )
        assert store.terminalize_invocation(
            invocation["invocation_id"],
            status,
            token,
            error_code=error_code,
            error_summary="password=secret " + "я" * 5000,
        )
        assert not store.terminalize_invocation(
            invocation["invocation_id"], status, token
        )
        row = store.invocation(invocation["invocation_id"])
        assert row["status"] == status
        assert "secret" not in (row["error_summary"] or "")
        assert len((row["error_summary"] or "").encode("utf-8")) <= 4096


def test_legacy_migration_creates_consistent_private_backup_once(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    path = dispatcher_db_path(repo, tmp_path / "state")
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE dispatcher_invocations ("
        "invocation_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, run_id TEXT NOT NULL, "
        "phase_id TEXT NOT NULL, role_id TEXT NOT NULL, work_unit_id TEXT NOT NULL, "
        "slot_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO dispatcher_invocations "
        "(invocation_id, job_id, run_id, phase_id, role_id, work_unit_id, "
        "slot_id, status, created_at, updated_at) "
        "VALUES ('legacy', 'job', 'run', 'phase', 'role', 'work', 'phase:role:1', "
        "'completed', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:01+00:00')"
    )
    connection.commit()
    connection.close()

    with DispatcherStore(repo, tmp_path / "state") as store:
        columns = {
            row[1]
            for row in store.conn.execute(
                "PRAGMA table_info(dispatcher_invocations)"
            )
        }
        assert {"finished_at", "result_ref", "context_manifest_fingerprint"} <= columns
        assert store.invocation("legacy")["status"] == "completed"
    backup = path.with_suffix(".pre-inspector.sqlite")
    assert backup.is_file() and backup.stat().st_mode & 0o777 == 0o600
    context_backup = path.with_suffix(".pre-context-envelope.sqlite")
    assert context_backup.is_file()
    assert context_backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as saved:
        assert saved.execute(
            "SELECT status FROM dispatcher_invocations WHERE invocation_id='legacy'"
        ).fetchone() == ("completed",)
        assert "finished_at" not in {
            row[1] for row in saved.execute("PRAGMA table_info(dispatcher_invocations)")
        }
    backup_bytes = backup.read_bytes()
    context_backup_bytes = context_backup.read_bytes()
    with DispatcherStore(repo, tmp_path / "state") as store:
        assert store.invocation("legacy")["status"] == "completed"
    assert backup.read_bytes() == backup_bytes
    assert context_backup.read_bytes() == context_backup_bytes
    rollback = Path(
        "docs/operator/dispatcher-inspector-rollback.md"
    ).read_text(encoding="utf-8")
    assert "older frontend" in rollback
    assert "older backend" in rollback
    assert "dispatcher.pre-context-envelope.sqlite" in rollback
    assert "Operational history created after the backup is lost" in rollback.replace(
        "\n", " "
    )


def test_inspection_supports_idle_slots_and_scoped_history_cursor(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with TestClient(create_app(state, [REPO], testing=True)) as client:
        project = _bookmark(client)
        projection = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher"
        ).json()
        role = projection["agent_phases"][0]["roles"][0]
        slot = role["slots"][0]
        idle = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={
                "kind": "slot",
                "phase_id": projection["agent_phases"][0]["phase_id"],
                "role_id": role["role_id"],
                "slot_id": slot["slot_id"],
            },
        )
        assert idle.status_code == 200
        assert idle.json()["slot"]["idle_reason_code"] == "work_not_requested"
        assert idle.json()["history"]["available"] is False
        assert slot["display_label"] == "1"
        assert idle.json()["observed_at"]
        invalid_idle_cursor = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={
                "kind": "slot",
                "phase_id": projection["agent_phases"][0]["phase_id"],
                "role_id": role["role_id"],
                "slot_id": slot["slot_id"],
                "event_cursor": "not-a-cursor",
            },
        )
        assert invalid_idle_cursor.status_code == 422

        event_store = EventStore(state / "projects", project["id"])
        fingerprint = _run(event_store, "run-history")
        with DispatcherStore(REPO, state) as store:
            token = store.acquire_lease(
                "discover-mrq",
                "thread",
                "DIF-1",
                "test",
                None,
                run_id="run-history",
            )
            identifiers = []
            for index in range(2):
                invocation = store.start_invocation(
                    "discover-mrq",
                    "run-history",
                    "analyze-dif",
                    "analyzer",
                    f"DIF-{index}",
                    1,
                    token,
                    execution_snapshot_fingerprint=fingerprint,
                    profile_id="local",
                    context_manifest_fingerprint=f"sha256:context-{index}",
                )
                identifiers.append(invocation["invocation_id"])
                store.terminalize_invocation(
                    invocation["invocation_id"], "completed", token
                )
            store.reconcile_invocation_outbox(event_store)

        first = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={
                "kind": "invocation",
                "invocation_id": identifiers[-1],
                "history_limit": 1,
                "event_limit": 1,
            },
        )
        assert first.status_code == 200
        payload = first.json()
        assert payload["execution_identity_available"] is True
        assert payload["execution_identity"] == {
            "profile_id": "local",
            "model": "test",
            "reasoning_effort": "low",
            "environment_preset": "local-read-only",
            "subject_bindings": {
                "source_generation_id": "source",
                "diff_generation_id": "diff",
                "canonical_generation_id": "canonical",
            },
        }
        assert payload["observed_at"]
        assert payload["history"]["truncated"] is True
        second = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={
                "kind": "invocation",
                "invocation_id": identifiers[-1],
                "history_limit": 1,
                "history_cursor": payload["history"]["next_cursor"],
            },
        )
        assert second.status_code == 200
        wrong = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={
                "kind": "invocation",
                "invocation_id": identifiers[0],
                "history_cursor": "not-a-cursor",
            },
        )
        assert wrong.status_code == 422


def test_empty_retained_events_are_truncated_without_next_cursor(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with TestClient(create_app(state, [REPO], testing=True)) as client:
        project = _bookmark(client)
        event_store = EventStore(state / "projects", project["id"])
        fingerprint = _run(event_store, "run-empty-events")
        with DispatcherStore(REPO, state) as store:
            token = store.acquire_lease(
                "discover-mrq", "thread", "DIF-1", "test", None,
                run_id="run-empty-events",
            )
            invocation = store.start_invocation(
                "discover-mrq", "run-empty-events", "analyze-dif", "analyzer",
                "DIF-1", 1, token,
                execution_snapshot_fingerprint=fingerprint,
                profile_id="local",
                context_manifest_fingerprint="sha256:context",
            )
            store.terminalize_invocation(
                invocation["invocation_id"],
                "completed",
                token,
                result_ref="opaque-result",
            )
            store.conn.execute(
                "UPDATE dispatcher_invocation_outbox SET delivered_at='lost'"
            )
            store.conn.commit()
        response = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={
                "kind": "invocation",
                "invocation_id": invocation["invocation_id"],
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["events"] == {
            "available": True,
            "items": [],
            "truncated": True,
            "next_cursor": None,
        }
        assert payload["result"]["reference"] == {
            "kind": "node-result",
            "id": "opaque-result",
        }
        assert payload["invocation"]["result_ref"] == {
            "kind": "node-result",
            "id": "opaque-result",
        }


def test_context_diagnostics_are_bounded_paged_and_legacy_safe(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    provenance = [
        {
            "item_key": f"fact:{index}",
            "item_kind": "fact",
            "selection_reason": "stage_policy",
            "origin_kind": "work_unit",
            "origin_ref": "DIF-1",
            "fingerprint": f"sha256:{index}",
        }
        for index in range(3)
    ]
    with TestClient(create_app(state, [REPO], testing=True)) as client:
        project = _bookmark(client)
        event_store = EventStore(state / "projects", project["id"])
        fingerprint = _run(event_store, "run-context")
        with DispatcherStore(REPO, state) as store:
            token = store.acquire_lease(
                "discover-mrq", "thread", "DIF-1", "test", None,
                run_id="run-context",
            )
            invocation = store.start_invocation(
                "discover-mrq", "run-context", "analyze-dif", "analyzer",
                "DIF-1", 1, token,
                execution_snapshot_fingerprint=fingerprint,
                profile_id="local",
                context_manifest_fingerprint="sha256:manifest",
                context_envelope_fingerprint="sha256:envelope",
                prepared_input_fingerprint="sha256:input",
                context_provenance=provenance,
                context_diagnostics={
                    "contract_version": "context-envelope/v1",
                    "prepared_input_bytes": 1200,
                    "headroom_bytes": 400,
                },
            )
        response = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={
                "kind": "invocation",
                "invocation_id": invocation["invocation_id"],
                "context_limit": 2,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["context"]["available"] is True
        assert payload["context"]["summary"]["prepared_input_bytes"] == 1200
        assert len(payload["context"]["provenance"]["items"]) == 2
        assert payload["context"]["provenance"]["truncated"] is True
        assert "context_provenance" not in payload["invocation"]
        cursor = payload["context"]["provenance"]["next_cursor"]
        page = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={
                "kind": "invocation",
                "invocation_id": invocation["invocation_id"],
                "context_limit": 2,
                "context_cursor": cursor,
            },
        )
        assert [row["item_key"] for row in page.json()["context"]["provenance"]["items"]] == ["fact:2"]


def test_inspection_drains_outbox_and_rejects_typed_cursor_mismatch(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    with TestClient(create_app(state, [REPO], testing=True)) as client:
        project = _bookmark(client)
        event_store = EventStore(state / "projects", project["id"])
        fingerprint = _run(event_store, "run-reconcile")
        with DispatcherStore(REPO, state) as store:
            token = store.acquire_lease(
                "discover-mrq", "thread", "DIF-1", "test", None,
                run_id="run-reconcile",
            )
            invocation = store.start_invocation(
                "discover-mrq", "run-reconcile", "analyze-dif", "analyzer",
                "DIF-1", 1, token,
                execution_snapshot_fingerprint=fingerprint,
                profile_id="local",
                context_manifest_fingerprint="sha256:context",
            )
        assert not any(
            event["type"] == "invocation.started"
            for event in event_store.events()
        )
        response = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={
                "kind": "invocation",
                "invocation_id": invocation["invocation_id"],
            },
        )
        assert response.status_code == 200
        assert any(
            event["type"] == "invocation.started"
            for event in event_store.events()
        )
        with DispatcherStore(REPO, state) as store:
            assert store.terminalize_invocation(
                invocation["invocation_id"], "completed", token
            )
        projection = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher"
        )
        assert projection.status_code == 200
        assert any(
            event["type"] == "invocation.finished"
            for event in event_store.events()
        )
        scope = {
            "project_id": project["id"],
            "run_id": "run-reconcile",
            "invocation_id": invocation["invocation_id"],
        }
        for sequence in (True, -1, "1"):
            invalid = client.get(
                f"/api/v1/projects/{project['id']}/dispatcher/inspect",
                params={
                    "kind": "invocation",
                    "invocation_id": invocation["invocation_id"],
                    "event_cursor": _encode_dispatcher_cursor({
                        "scope": scope,
                        "last_sequence": sequence,
                    }),
                },
            )
            assert invalid.status_code == 422


def test_dispatcher_database_errors_are_generic_503(
    tmp_path: Path, monkeypatch
) -> None:
    with TestClient(create_app(tmp_path / "state", [REPO], testing=True)) as client:
        project = _bookmark(client)
        monkeypatch.setattr(
            DispatcherStore,
            "reconcile_invocation_outbox",
            lambda *_args: (_ for _ in ()).throw(
                sqlite3.DatabaseError("private database detail")
            ),
        )
        projection = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher"
        )
        assert projection.status_code == 503
        assert projection.json()["detail"] == "dispatcher operational store unavailable"
        assert "private database detail" not in projection.text


def test_registry_exact_item_filter(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    generation = "a" * 64
    (repo / "research").mkdir(parents=True)
    (repo / "research/active-diff-generation.json").write_text(
        json.dumps({"schema_version": "1", "generation_id": generation}),
        encoding="utf-8",
    )
    registry = repo / "analysis/indexes/generations" / generation / "diff-inventory.csv"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        "stable_diff_id,path\nDIF-ONE,one\nDIF-TWO,two\n",
        encoding="utf-8",
    )
    service = object.__new__(ApplicationService)
    service.repo = repo
    exact = service.registry("diff-inventory", item_id="DIF-TWO")
    assert [row["stable_diff_id"] for row in exact["items"]] == ["DIF-TWO"]


def test_slot_assignment_is_authoritative_beyond_visible_window(
    tmp_path: Path,
) -> None:
    with DispatcherStore(tmp_path, tmp_path) as store:
        columns = (
            "invocation_id, job_id, run_id, phase_id, role_id, work_unit_id, "
            "slot_id, status, created_at, updated_at"
        )
        store.conn.execute(
            f"INSERT INTO dispatcher_invocations ({columns}) "
            "VALUES ('old-running', 'job', 'run', 'phase', 'role', 'work', "
            "'phase:role:1', 'running', '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:00+00:00')"
        )
        for index in range(20):
            store.conn.execute(
                f"INSERT INTO dispatcher_invocations ({columns}) "
                "VALUES (?, 'job', 'run', 'phase', 'role', ?, "
                "'phase:role:1', 'completed', ?, ?)",
                (
                    f"completed-{index:02d}",
                    f"work-{index}",
                    f"2026-01-02T00:00:{index:02d}+00:00",
                    f"2026-01-02T00:00:{index:02d}+00:00",
                ),
            )
        store.conn.commit()
        visible = store.invocations(
            "run", 16, phase_id="phase", role_id="role"
        )
        assert all(row["invocation_id"] != "old-running" for row in visible)
        assignment = store.slot_assignments("run", "phase", "role")[
            "phase:role:1"
        ]
        assert assignment["current"]["invocation_id"] == "old-running"
        assert assignment["latest"]["invocation_id"] == "completed-19"


def test_queue_aggregates_are_exact_before_bounded_slice(
    tmp_path: Path, monkeypatch
) -> None:
    diffs = [
        {
            "stable_diff_id": f"DIF-{index:03d}",
            "before_role": "vendor_baseline",
            "after_role": "target_cf",
            "path": f"path-{index}",
            "object_kind": "metadata",
        }
        for index in range(40)
    ]
    mrqs = [
        {
            "mrq_id": f"MRQ-{index:03d}",
            "state": "superseded" if index < 2 else "draft",
        }
        for index in range(40)
    ]
    dispositions = [
        {"stable_diff_id": f"DIF-{index:03d}", "primary": True}
        for index in range(5)
    ]
    monkeypatch.setattr(
        "one_c_autoresearch.workflow._active_rows",
        lambda _repo: (diffs, mrqs, dispositions, []),
    )
    monkeypatch.setattr(
        "one_c_autoresearch.workflow._pointer",
        lambda _repo, _name: {},
    )
    store = type("Store", (), {"proposals": lambda _self: []})()

    items = _dispatcher_items(tmp_path, store)

    totals = items.pop("_queue_aggregates")
    assert len(items["dif_queue"]) == 32
    assert totals["dif-queue"] == {"total": 35, "visible": 32, "omitted": 3}
    assert len(items["mrqs"]) == 32
    assert totals["mrq-queue"] == {"total": 38, "visible": 32, "omitted": 6}


def test_dispatcher_shows_current_analyzer_results_before_publication(
    tmp_path: Path, monkeypatch
) -> None:
    diffs = [
        {
            "stable_diff_id": identifier,
            "before_role": "vendor_baseline",
            "after_role": "target_cf",
            "path": f"{identifier}.bsl",
            "object_kind": "metadata",
        }
        for identifier in ("DIF-MEANING", "DIF-NOISE", "DIF-PENDING")
    ]
    monkeypatch.setattr(
        "one_c_autoresearch.workflow._active_rows",
        lambda _repo: (diffs, [], [], []),
    )
    monkeypatch.setattr(
        "one_c_autoresearch.workflow._pointer",
        lambda _repo, _name: {},
    )
    results = [{
        "job_id": "analyze-dif",
        "thread_id": "current",
        "kind": "node-result",
        "payload": {
            "name": "analyze-dif:DIF-NOISE",
            "envelope": {
                "result": {"stable_diff_id": "DIF-NOISE", "kind": "noise"},
            },
        },
    }]
    store = type("Store", (), {"proposals": lambda _self: results})()

    items = _dispatcher_items(tmp_path, store, {"DIF-MEANING"})

    assert [item["id"] for item in items["meaning_diffs"]] == ["DIF-MEANING"]
    assert [item["id"] for item in items["noise_diffs"]] == ["DIF-NOISE"]
    assert [item["id"] for item in items["dif_queue"]] == ["DIF-PENDING"]
    assert items["_queue_aggregates"]["meaning-diffs"]["total"] == 1
    assert items["_queue_aggregates"]["noise-diffs"]["total"] == 1


def test_duplicate_timestamps_and_concurrent_slot_insertion_are_stable(
    tmp_path: Path,
) -> None:
    first = DispatcherStore(tmp_path, tmp_path)
    second = DispatcherStore(tmp_path, tmp_path)
    first.open()
    second.open()
    token = first.acquire_lease(
        "discover-mrq", "thread", "work", "test", None, run_id="run"
    )
    barrier = threading.Barrier(2)
    rows: list[dict] = []

    def create(store: DispatcherStore, work: str) -> None:
        barrier.wait()
        row = store.start_invocation(
            "discover-mrq", "run", "phase", "role", work, 2, token
        )
        rows.append(row)

    threads = [
        threading.Thread(target=create, args=(store, f"work-{index}"))
        for index, store in enumerate((first, second))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert len(rows) == 2
    assert len({row["slot_id"] for row in rows}) == 2
    with first.conn:
        first.conn.execute(
            "UPDATE dispatcher_invocations SET created_at = "
            "'2026-01-01T00:00:00+00:00' WHERE run_id = 'run'"
        )
    history = first.invocation_history(
        "run", "phase", "role", rows[0]["slot_id"], 10
    )
    assert history == sorted(
        history,
        key=lambda row: (row["created_at"], row["invocation_id"]),
        reverse=True,
    )
    first.close()
    second.close()


def test_valid_cursors_are_rejected_across_slot_and_invocation_scope() -> None:
    history_scope = {
        "project_id": "p",
        "run_id": "r",
        "phase_id": "phase",
        "role_id": "role",
        "slot_id": "phase:role:1",
    }
    history = _encode_dispatcher_cursor({
        "scope": history_scope,
        "created_at": "2026-01-01T00:00:00+00:00",
        "invocation_id": "i1",
    })
    with pytest.raises(ValueError):
        _history_cursor(history, {**history_scope, "slot_id": "phase:role:2"})
    event_scope = {"project_id": "p", "run_id": "r", "invocation_id": "i1"}
    event = _encode_dispatcher_cursor({
        "scope": event_scope, "last_sequence": 1
    })
    with pytest.raises(ValueError):
        _event_cursor(event, {**event_scope, "invocation_id": "i2"})


def test_event_pagination_over_limit_is_exclusive(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with TestClient(create_app(state, [REPO], testing=True)) as client:
        project = _bookmark(client)
        event_store = EventStore(state / "projects", project["id"])
        fingerprint = _run(event_store, "run-events")
        with DispatcherStore(REPO, state) as store:
            token = store.acquire_lease(
                "discover-mrq", "thread", "work", "test", None,
                run_id="run-events",
            )
            invocation = store.start_invocation(
                "discover-mrq", "run-events", "analyze-dif", "analyzer",
                "work", 1, token,
                execution_snapshot_fingerprint=fingerprint,
                profile_id="local",
                context_manifest_fingerprint="sha256:context",
            )
            store.reconcile_invocation_outbox(event_store)
        for index in range(4):
            event_store.emit(
                "invocation.started",
                "run-events",
                {
                    "transition_key": f"extra:{index}",
                    "invocation_id": invocation["invocation_id"],
                    "run_id": "run-events",
                    "phase_id": "analyze-dif",
                    "role_id": "analyzer",
                    "slot_id": "analyze-dif:analyzer:1",
                    "work_unit_id": "work",
                    "invocation_status": "running",
                    "timestamp": f"2026-01-01T00:00:0{index}+00:00",
                },
            )
        first = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={
                "kind": "invocation",
                "invocation_id": invocation["invocation_id"],
                "event_limit": 2,
            },
        ).json()
        assert len(first["events"]["items"]) == 2
        assert first["events"]["truncated"] is True
        assert first["events"]["next_cursor"]
        second = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={
                "kind": "invocation",
                "invocation_id": invocation["invocation_id"],
                "event_limit": 2,
                "event_cursor": first["events"]["next_cursor"],
            },
        ).json()
        assert {
            event["sequence"] for event in first["events"]["items"]
        }.isdisjoint(event["sequence"] for event in second["events"]["items"])


def test_same_invocation_uuid_is_confined_to_each_project_store(
    tmp_path: Path,
) -> None:
    stores = []
    for index in range(2):
        repo = tmp_path / f"repo-{index}"
        repo.mkdir()
        store = DispatcherStore(repo, tmp_path / "state")
        store.open()
        store.conn.execute(
            "INSERT INTO dispatcher_invocations "
            "(invocation_id, job_id, run_id, phase_id, role_id, work_unit_id, "
            "slot_id, status, created_at, updated_at) "
            "VALUES ('same-id', 'job', ?, 'phase', 'role', ?, 'phase:role:1', "
            "'completed', '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:01+00:00')",
            (f"run-{index}", f"work-{index}"),
        )
        store.conn.commit()
        stores.append(store)
    assert stores[0].invocation("same-id")["work_unit_id"] == "work-0"
    assert stores[1].invocation("same-id")["work_unit_id"] == "work-1"
    assert stores[0].invocation("foreign-id") is None
    for store in stores:
        store.close()

    with TestClient(create_app(tmp_path / "api-state", [REPO], testing=True)) as client:
        project = _bookmark(client)
        unknown = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={"kind": "invocation", "invocation_id": "unknown"},
        )
        foreign = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={"kind": "invocation", "invocation_id": "same-id"},
        )
        assert unknown.status_code == foreign.status_code == 404
        assert unknown.json() == foreign.json()


@pytest.mark.parametrize("snapshot_kind", ["missing", "mismatched", "legacy"])
def test_snapshot_and_legacy_identity_are_explicitly_unavailable(
    tmp_path: Path, snapshot_kind: str
) -> None:
    state = tmp_path / "state"
    with TestClient(create_app(state, [REPO], testing=True)) as client:
        project = _bookmark(client)
        with DispatcherStore(REPO, state) as store:
            store.conn.execute(
                "INSERT INTO dispatcher_invocations "
                "(invocation_id, job_id, run_id, phase_id, role_id, work_unit_id, "
                "slot_id, status, created_at, updated_at, "
                "execution_snapshot_fingerprint, profile_id, "
                "context_manifest_fingerprint) VALUES "
                "(?, 'job', ?, 'analyze-dif', 'analyzer', 'work', "
                "'analyze-dif:analyzer:1', 'completed', "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:01+00:00', "
                "?, ?, ?)",
                (
                    f"inv-{snapshot_kind}",
                    f"run-{snapshot_kind}",
                    None if snapshot_kind == "legacy" else "sha256:wrong",
                    None if snapshot_kind == "legacy" else "local",
                    None if snapshot_kind == "legacy" else "sha256:context",
                ),
            )
            store.conn.commit()
        if snapshot_kind == "mismatched":
            _run(
                EventStore(state / "projects", project["id"]),
                "run-mismatched",
            )
        response = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={
                "kind": "invocation",
                "invocation_id": f"inv-{snapshot_kind}",
            },
        )
        assert response.status_code == 200
        assert response.json()["execution_identity_available"] is False
        assert response.json()["result"]["available"] is False


def test_published_missing_and_orphan_results_are_safe(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with TestClient(create_app(state, [REPO], testing=True)) as client:
        project = _bookmark(client)
        event_store = EventStore(state / "projects", project["id"])
        fingerprint = _run(event_store, "run-results")
        with DispatcherStore(REPO, state) as store:
            token = store.acquire_lease(
                "discover-mrq", "thread", "work", "test", None,
                run_id="run-results",
            )
            ids = {}
            for name in ("published", "missing", "orphan"):
                invocation = store.start_invocation(
                    "discover-mrq", "run-results", "analyze-dif", "analyzer",
                    name, 1, token,
                    execution_snapshot_fingerprint=fingerprint,
                    profile_id="local",
                    context_manifest_fingerprint=f"sha256:{name}",
                )
                reference = f"result-{name}"
                store.terminalize_invocation(
                    invocation["invocation_id"], "completed", token,
                    result_ref=reference,
                )
                ids[name] = invocation["invocation_id"]
            store.save_proposal(
                "result-published", "discover-mrq", "thread", "node-result",
                {
                    "name": "analyze-dif:published",
                    "envelope": {
                        "classification": "meaningful",
                        "confidence": "high",
                        "stable_diff_ids": ["DIF-1", "DIF-2"],
                        "response": "<script>private provider output</script>"
                    },
                },
                token,
            )
            store.save_proposal(
                "result-orphan", "discover-mrq", "thread", "approval",
                {"name": "unrelated-approval", "private": "must not leak"},
                token,
            )
        published = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher/inspect",
            params={"kind": "invocation", "invocation_id": ids["published"]},
        ).json()["result"]
        assert published == {
            "available": True,
            "reference": {"kind": "node-result", "id": "result-published"},
            "summary": {
                "name": "analyze-dif:published",
                "classification": "meaningful",
                "confidence": "high",
                "stable_diff_count": 2,
            },
        }
        for name in ("missing", "orphan"):
            result = client.get(
                f"/api/v1/projects/{project['id']}/dispatcher/inspect",
                params={"kind": "invocation", "invocation_id": ids[name]},
            ).json()["result"]
            assert result["available"] is False
            assert "summary" not in result


def test_first_of_two_concurrent_finishes_keeps_run_running(tmp_path: Path) -> None:
    event_store = EventStore(tmp_path / "events", "project")
    fingerprint = _run(event_store, "run-concurrent")
    with DispatcherStore(tmp_path, tmp_path) as store:
        token = store.acquire_lease(
            "discover-mrq", "thread", "work", "test", None,
            run_id="run-concurrent",
        )
        invocations = [
            store.start_invocation(
                "discover-mrq", "run-concurrent", "phase", "role", f"work-{i}",
                2, token, execution_snapshot_fingerprint=fingerprint,
                profile_id="local",
                context_manifest_fingerprint=f"sha256:{i}",
            )
            for i in range(2)
        ]
        store.reconcile_invocation_outbox(event_store)
        store.terminalize_invocation(
            invocations[0]["invocation_id"], "completed", token
        )
        store.reconcile_invocation_outbox(event_store)
        assert store.invocation(invocations[1]["invocation_id"])["status"] == "running"
    assert event_store.run_snapshot("run-concurrent")["status"] == "running"


def test_crash_after_emit_before_outbox_mark_deduplicates(tmp_path: Path) -> None:
    event_store = EventStore(tmp_path / "events", "project")
    _run(event_store, "run-crash")
    with DispatcherStore(tmp_path, tmp_path) as store:
        token = store.acquire_lease(
            "discover-mrq", "thread", "work", "test", None,
            run_id="run-crash",
        )
        invocation = store.start_invocation(
            "discover-mrq", "run-crash", "phase", "role", "work", 1, token
        )
        payload = store.conn.execute(
            "SELECT payload FROM dispatcher_invocation_outbox "
            "WHERE invocation_id = ?",
            (invocation["invocation_id"],),
        ).fetchone()[0]
        import json
        event_store.emit("invocation.started", "run-crash", json.loads(payload))
        assert store.reconcile_invocation_outbox(event_store) == 1
    lifecycle = [
        event for event in event_store.run_snapshot("run-crash")["events"]
        if event["type"] == "invocation.started"
    ]
    assert len(lifecycle) == 1


@pytest.mark.parametrize("path", ["soft_stop", "branch_cancel"])
def test_active_invocation_is_cancelled_by_stop_paths(
    tmp_path: Path, path: str
) -> None:
    event_store = EventStore(tmp_path / "events", "project")
    fingerprint = _run(event_store, f"run-{path}")
    store = DispatcherStore(tmp_path, tmp_path)
    store.open()
    token = store.acquire_lease(
        "discover-mrq", "thread", "work", "test", None,
        run_id=f"run-{path}", summary={"run_id": f"run-{path}"},
        execution_snapshot_fingerprint=fingerprint,
    )
    invocation = store.start_invocation(
        "discover-mrq", f"run-{path}", "phase", "role", "work", 1, token
    )
    coordinator = DispatcherCoordinator(
        tmp_path, "project", store, event_store, actor="test"
    )
    coordinator._lease_tokens["discover-mrq"] = token
    if path == "soft_stop":
        outcome = coordinator.soft_stop("discover-mrq")
        assert outcome.status == "resumable"
    else:
        outcome = coordinator.cancel("discover-mrq")
        assert outcome.status == "cancelled"
    assert store.invocation(invocation["invocation_id"])["status"] == "cancelled"
    coordinator.close()
    store.close()


def test_lease_expiry_interrupts_orphan_before_takeover(tmp_path: Path) -> None:
    store = DispatcherStore(tmp_path, tmp_path)
    store.open()
    token = store.acquire_lease(
        "discover-mrq", "thread", "work", "owner", None, run_id="run-expired"
    )
    invocation = store.start_invocation(
        "discover-mrq", "run-expired", "phase", "role", "work", 1, token
    )
    expired = (
        datetime.now(timezone.utc) - timedelta(seconds=60)
    ).isoformat()
    with store.conn:
        store.conn.execute(
            "UPDATE dispatcher_leases SET renewed_at = ? "
            "WHERE job_id = 'discover-mrq'",
            (expired,),
        )
    assert store.acquire_lease(
        "discover-mrq", "new-thread", "new-work", "new-owner", None,
        run_id="new-run",
    )
    assert store.invocation(invocation["invocation_id"])["status"] == "interrupted"
    store.close()


@pytest.mark.parametrize("reason", ["process_mismatch", "orphan_restart"])
def test_recovery_interrupts_running_invocation_and_restart_delivers(
    tmp_path: Path, reason: str
) -> None:
    event_store = EventStore(tmp_path / "events", "project")
    _run(event_store, f"run-{reason}")
    store = DispatcherStore(tmp_path, tmp_path)
    store.open()
    token = store.acquire_lease(
        "discover-mrq", "thread", "work", "owner", None,
        run_id=f"run-{reason}",
    )
    invocation = store.start_invocation(
        "discover-mrq", f"run-{reason}", "phase", "role", "work", 1, token
    )
    assert store.interrupt_running_work(
        "discover-mrq", f"run-{reason}", token
    ) == 1
    coordinator = DispatcherCoordinator(
        tmp_path, "project", store, event_store, actor="restart"
    )
    assert store.invocation(invocation["invocation_id"])["status"] == "interrupted"
    assert any(
        event["type"] == "invocation.finished"
        and event["payload"]["error_code"] == "interrupted"
        for event in event_store.run_snapshot(f"run-{reason}")["events"]
    )
    coordinator.close()
    store.close()
