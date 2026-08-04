"""Тесты API диспетчера конвейера.

Проверяем 6 действий (start/stop/resume/cancel/retry/approve-batch), проекцию
в снимке и обработку невалидных job/action.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from one_c_autoresearch.contracts import canonical_json, sha256
from one_c_autoresearch.dispatcher import DispatcherCoordinator
from one_c_autoresearch.events import EventStore
from one_c_autoresearch.sqlite_state import DispatcherStore
from one_c_autoresearch.user_state import save_agent_profiles
from one_c_autoresearch.workflow import _latest_operation_zone
from one_c_autoresearch.workspace_api import create_app


REPO = Path(__file__).resolve().parents[1]


def _test_execution_snapshot(_repo, run_id, operation, step, profiles, work_unit):
    from one_c_autoresearch.stage_recompute import active_state
    pointers, _ = active_state(_repo)
    return {
        "schema_version": "1", "run_id": run_id, "operation": operation,
        "operation_version": step["operation_version"], "workflow_fingerprint": "sha256:" + "0" * 64,
        "timeout_seconds": step["timeout_seconds"], "agent_phases": step.get("agent_phases", []),
        "profiles": profiles, "instructions": {}, "environment": {}, "codex_version": "test",
        "application_version": "one-c-autoresearch/0.2", "subject_bindings": {
            "source_generation_id": str((pointers.get("source") or {}).get("generation_id", "")),
            "diff_generation_id": str((pointers.get("diff") or {}).get("generation_id", "")),
            "canonical_generation_id": str(
                (pointers.get("mrq") or {}).get("mrq_generation_id") or ""
            ),
        }, "policy_source": "current-policy", "work_unit": work_unit, "context_manifest": {
            "schema_version": "1", "work_unit_id": work_unit["id"],
            "work_unit_fingerprint": "sha256:" + "0" * 64, "paths": [],
        },
    }


def _client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "state", [REPO], testing=True)
    return TestClient(app)


def _bookmark(client: TestClient, tmp_path: Path) -> dict:
    headers = {"Origin": "http://testserver", "Idempotency-Key": "bookmark-1"}
    return client.post("/api/v1/projects", json={"name": "example", "root": str(REPO)}, headers=headers).json()


def test_snapshot_includes_dispatcher_section(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        snapshot = client.get(f"/api/v1/projects/{project['id']}/workflow").json()
        assert "dispatcher" in snapshot
        assert snapshot["dispatcher"]["schema_version"] == "2"
        circuits = snapshot["dispatcher"]["circuits"]
        assert [c["id"] for c in circuits] == ["prepare-diffs", "analyze-dif", "form-mrq", "classify-mrq", "decide-target"]
        assert [zone["id"] for zone in circuits[0]["zones"]] == [
            "vendor-baseline",
            "target-cf",
            "next-vendor",
            "sources-acquire",
            "diffs-build",
            "indexes-build",
        ]
        assert {zone["state"] for zone in circuits[0]["zones"]} <= {
            "unknown",
            "waiting",
            "active",
            "error",
            "complete",
        }
        assert circuits[2]["publication"]["id"] == "publication"
        assert circuits[2]["publication"]["state"] in {"unknown", "waiting", "complete"}
        items = snapshot["dispatcher"]["items"]
        totals = snapshot["dispatcher"]["queue_aggregates"]
        assert {"dif_queue", "meaning_diffs", "noise_diffs", "proposals", "mrqs", "batches", "decisions"} <= set(items)
        assert totals["dif-queue"]["total"] == len(items["dif_queue"]) + totals["dif-queue"]["omitted"]
        assert totals["mrq-queue"]["total"] == len(items["mrqs"]) + totals["mrq-queue"]["omitted"]
        assert all(item["id"].startswith("DIF-") for item in items["dif_queue"])
        assert all(item["id"].startswith("MRQ-") for item in items["mrqs"])
        event_store = EventStore(tmp_path / "state/projects", project["id"])
        execution = {
            "schema_version": "1",
            "operation": "dif.classify-next",
            "workflow_fingerprint": snapshot["workflow_fingerprint"],
            "policy_source": "current-policy",
            "subject_bindings": {
                "source_generation_id": "source",
                "diff_generation_id": "diff",
                "canonical_generation_id": "canonical",
            },
            "work_unit": {"id": "DIF-001"},
        }
        fingerprint = event_store.prepare_run("completed-run", execution)
        metadata = {
            "execution_snapshot_fingerprint": fingerprint,
            "policy_source": "current-policy",
            "tool_versions": {"codex": "test"},
        }
        event_store.emit(
            "run.created",
            "completed-run",
            {
                "status": "running",
                "actor": "local-user",
                "process_identity": None,
                "workflow_fingerprint": snapshot["workflow_fingerprint"],
                "operation": "dif.classify-next",
                **metadata,
            },
        )
        event_store.emit(
            "run.finished",
            "completed-run",
            {"status": "completed", "duration_seconds": 0.0, **metadata},
        )
        blocked_fingerprint = event_store.prepare_run("blocked-run", {**execution, "run_id": "blocked-run"})
        blocked_metadata = {**metadata, "execution_snapshot_fingerprint": blocked_fingerprint}
        event_store.emit(
            "run.created",
            "blocked-run",
            {
                "status": "running",
                "actor": "local-user",
                "process_identity": None,
                "workflow_fingerprint": snapshot["workflow_fingerprint"],
                "operation": "dif.classify-next",
                **blocked_metadata,
            },
        )
        event_store.emit(
            "run.finished",
            "blocked-run",
            {"status": "blocked", "duration_seconds": 0.0, **blocked_metadata},
        )
        updated = client.get(f"/api/v1/projects/{project['id']}/workflow").json()
        assert updated["dispatcher"]["retry_candidates"][0]["run_id"] == "completed-run"
        assert all(item["run_id"] != "blocked-run" for item in updated["dispatcher"]["retry_candidates"])


def test_dispatcher_endpoint_returns_projection(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        response = client.get(f"/api/v1/projects/{project['id']}/dispatcher")
        assert response.status_code == 200
        projection = response.json()
        assert projection["schema_version"] == "2"
        assert "revision" in projection
        assert "circuits" in projection


def test_prepare_zone_uses_latest_verified_operation_run(tmp_path: Path) -> None:
    execution = {"operation": "diff.build", "workflow_fingerprint": "sha256:current"}
    (tmp_path / "run.json").write_text(
        json.dumps(
            {
                "execution_snapshot": execution,
                "execution_snapshot_fingerprint": "sha256:" + sha256(canonical_json(execution)),
                "status": "failed",
                "events": [{"timestamp": "2026-07-24T12:00:00+00:00"}],
            }
        ),
        encoding="utf-8",
    )

    assert _latest_operation_zone(tmp_path, "diff.build", "sha256:current") == {
        "state": "error",
        "updated_at": "2026-07-24T12:00:00+00:00",
    }
    assert _latest_operation_zone(tmp_path, "diff.build", "sha256:new-epoch") is None


def test_dispatcher_projection_probes_codex_once_for_all_roles(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "state"
    save_agent_profiles(
        REPO,
        {"local": {"provider": "codex-cli", "model": "test-model", "reasoning_effort": "low", "instructions_version": "1", "environment_preset": "local-read-only"}},
        state,
    )
    calls = 0

    def probe() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {
            "executable": "/usr/bin/codex",
            "executable_fingerprint": "sha256:" + "0" * 64,
            "codex_version": "codex test",
        }

    monkeypatch.setattr("one_c_autoresearch.agents.probe_codex_environment", probe)
    with TestClient(create_app(state, [REPO], testing=True)) as client:
        project = _bookmark(client, tmp_path)
        calls = 0
        response = client.get(f"/api/v1/projects/{project['id']}/dispatcher")
        assert response.status_code == 200
    assert calls == 1


def test_dispatcher_projection_reports_bounded_invocation_window(tmp_path: Path, monkeypatch) -> None:
    rows = [
        {
            "invocation_id": f"invocation-{index}",
            "job_id": "analyze-dif",
            "run_id": "run-window",
            "phase_id": "analyze-dif",
            "role_id": "analyzer",
            "work_unit_id": f"DIF-{index:016X}",
            "slot_id": f"analyze-dif:analyzer:{index % 4 + 1}",
            "status": "completed",
            "created_at": f"2026-07-24T00:00:{index:02d}+00:00",
            "updated_at": f"2026-07-24T00:01:{index:02d}+00:00",
        }
        for index in range(20)
    ]
    monkeypatch.setattr(
        DispatcherStore,
        "latest_phase_run",
        lambda _self, job_id: "run-window" if job_id == "analyze-dif" else "",
    )
    monkeypatch.setattr(
        DispatcherStore,
        "invocations",
        lambda _self, run_id=None, limit=100, phase_id=None, role_id=None: [
            row for row in rows
            if run_id == "run-window"
            and (phase_id is None or row["phase_id"] == phase_id)
            and (role_id is None or row["role_id"] == role_id)
        ][:limit],
    )
    monkeypatch.setattr(
        DispatcherStore,
        "invocation_count",
        lambda _self, run_id, phase_id, role_id: sum(
            row["run_id"] == run_id
            and row["phase_id"] == phase_id
            and row["role_id"] == role_id
            for row in rows
        ),
    )
    monkeypatch.setattr(
        DispatcherStore,
        "phase_work",
        lambda _self, run_id: [
            {
                "phase_id": row["phase_id"],
                "role_id": row["role_id"],
                "status": row["status"],
            }
            for row in rows
        ]
        if run_id == "run-window"
        else [],
    )

    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        projection = client.get(
            f"/api/v1/projects/{project['id']}/dispatcher",
        ).json()

    phase = next(
        item
        for item in projection["agent_phases"]
        if item["phase_id"] == "analyze-dif"
    )
    role = phase["roles"][0]
    assert role["invocation_total"] == 20
    assert role["invocation_omitted"] == 4
    assert len(role["invocations"]) == 16
    assert role["invocations"][0]["updated_at"] == "2026-07-24T00:01:00+00:00"


def test_dispatcher_start_reports_missing_configured_executor(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        headers = {"Origin": "http://testserver", "Idempotency-Key": "start-1"}
        response = client.post(f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/start", json={"actor": "local-user"}, headers=headers)
        assert response.status_code == 200
        outcome = response.json()["outcome"]
        assert outcome["status"] == "blocked"
        assert outcome["thread_id"] == ""
        assert outcome["blocker"]["code"] == "executor.agent_profile_missing"
        # повторный start тем же idempotency-key — идемпотентный
        repeat = client.post(f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/start", json={"actor": "local-user"}, headers=headers)
        assert repeat.status_code == 200


def test_classify_dispatcher_uses_existing_action_family_without_approval(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        headers = {"Origin": "http://testserver", "Idempotency-Key": "classify-start"}
        response = client.post(f"/api/v1/projects/{project['id']}/dispatcher/classify-mrq/start", json={"actor": "local-user"}, headers=headers)
        assert response.status_code == 200
        assert response.json()["outcome"]["blocker"]["code"] == "executor.agent_profile_missing"
        approval = client.post(
            f"/api/v1/projects/{project['id']}/dispatcher/classify-mrq/approve-batch",
            json={"actor": "local-user"},
            headers={"Origin": "http://testserver", "Idempotency-Key": "classify-approval"},
        )
        assert approval.status_code == 422


def test_dispatcher_cancel_releases_lease(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        headers = {"Origin": "http://testserver", "Idempotency-Key": "cancel-1"}
        client.post(f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/start", json={"actor": "local-user"}, headers=headers)
        cancel = client.post(f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/cancel", json={"actor": "local-user"}, headers={"Origin": "http://testserver", "Idempotency-Key": "cancel-2"})
        assert cancel.status_code == 200
        assert cancel.json()["outcome"]["status"] == "cancelled"


def test_dispatcher_soft_stop_without_started_run_is_blocked(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        client.post(f"/api/v1/projects/{project['id']}/dispatcher/decide-mrq/start", json={"actor": "local-user"}, headers={"Origin": "http://testserver", "Idempotency-Key": "stop-1"})
        stop = client.post(f"/api/v1/projects/{project['id']}/dispatcher/decide-mrq/stop", json={"timeout_seconds": 1}, headers={"Origin": "http://testserver", "Idempotency-Key": "stop-2"})
        assert stop.status_code == 200
        assert stop.json()["outcome"]["status"] == "blocked"


def test_dispatcher_rejects_invalid_job(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        response = client.post(f"/api/v1/projects/{project['id']}/dispatcher/invalid-job/start", json={}, headers={"Origin": "http://testserver", "Idempotency-Key": "invalid-1"})
        assert response.status_code == 422


def test_dispatcher_rejects_invalid_action(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        response = client.post(f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/invalid-action", json={}, headers={"Origin": "http://testserver", "Idempotency-Key": "invalid-2"})
        assert response.status_code == 422


def test_dispatcher_requires_origin_and_idempotency(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        # без Origin и Idempotency-Key запрос отклоняется безопасности
        response = client.post(f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/start", json={"actor": "local-user"})
        assert response.status_code in {400, 403}
        # с Origin, но без Idempotency-Key — 400
        response_no_key = client.post(f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/start", json={"actor": "local-user"}, headers={"Origin": "http://testserver"})
        assert response_no_key.status_code == 400


def test_dispatcher_retry_body_is_action_specific_and_closed(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        response = client.post(
            f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/retry",
            json={
                "actor": "not-allowed-for-retry",
                "policy_source": "reuse-snapshot",
                "predecessor_run_id": "run-1",
                "expected_workflow_fingerprint": "sha256:workflow",
                "expected_input_fingerprints": {},
            },
            headers={"Origin": "http://testserver", "Idempotency-Key": "retry-closed"},
        )
        assert response.status_code == 422
        assert "unsupported fields for dispatcher retry" in response.text


def test_dispatcher_section_is_not_part_of_canonical_fingerprint(tmp_path: Path) -> None:
    """Секция dispatcher не входит в ``workflow_fingerprint`` снимка."""

    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        snapshot_before = client.get(f"/api/v1/projects/{project['id']}/workflow").json()
        # запускаем и останавливаем диспетчер (изменяется только SQLite)
        client.post(f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/start", json={"actor": "local-user"}, headers={"Origin": "http://testserver", "Idempotency-Key": "fp-1"})
        snapshot_after = client.get(f"/api/v1/projects/{project['id']}/workflow").json()
        assert snapshot_before["workflow_fingerprint"] == snapshot_after["workflow_fingerprint"]
        # но projection revision вырос
        assert snapshot_after["dispatcher"]["revision"] >= snapshot_before["dispatcher"]["revision"]


def test_retry_recovers_same_run_after_owner_dies_post_lease(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setattr("one_c_autoresearch.agents.resolve_execution_snapshot", _test_execution_snapshot)
    monkeypatch.setattr(
        "one_c_autoresearch.agents.build_context_manifest",
        lambda _repo, work_unit: {
            "schema_version": "1",
            "work_unit_id": work_unit["id"],
            "work_unit_fingerprint": "sha256:" + "0" * 64,
            "paths": [],
        },
    )
    save_agent_profiles(
        REPO,
        {"local": {"provider": "codex-cli", "model": "test-model", "reasoning_effort": "low", "instructions_version": "1", "environment_preset": "local-read-only"}},
        state,
    )
    monkeypatch.setattr(DispatcherCoordinator, "_validate_new_run_snapshot", lambda *_args: None)
    monkeypatch.setattr(DispatcherCoordinator, "launch_graph", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "one_c_autoresearch.workflow.next_work",
        lambda _repo: {
            "action": "dif.classify-next",
            "work_unit": {"id": "DIF-RECOVERY", "kind": "customer-diff", "allowed_paths": []},
        },
    )
    headers = {"Origin": "http://testserver", "Idempotency-Key": "bookmark-recovery"}
    app = create_app(state, [REPO], testing=True)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "example", "root": str(REPO)}, headers=headers).json()
        start = client.post(
            f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/start",
            json={"actor": "local-user"},
            headers={"Origin": "http://testserver", "Idempotency-Key": "recovery-start"},
        ).json()["outcome"]
        assert start["status"] == "running", json.dumps(start, ensure_ascii=False)
        stop_response = client.post(
            f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/stop",
            json={"timeout_seconds": 1},
            headers={"Origin": "http://testserver", "Idempotency-Key": "recovery-stop"},
        )
        assert stop_response.status_code == 200, stop_response.text
        stopped = stop_response.json()["outcome"]
        assert stopped["status"] == "resumable"
        with DispatcherStore(REPO, state) as store:
            predecessor = store.lease("analyze-dif")
            assert predecessor is not None and predecessor["state"] == "resumable"
        workflow = client.get(f"/api/v1/projects/{project['id']}/workflow").json()
        execution = EventStore(state / "projects", project["id"]).run_snapshot(start["run_id"])["execution_snapshot"]
        retry_body = {
            "policy_source": "current-policy",
            "predecessor_run_id": start["run_id"],
            "expected_workflow_fingerprint": workflow["workflow_fingerprint"],
            "expected_input_fingerprints": {
                **execution["subject_bindings"],
                "work_unit_id": execution["work_unit"]["id"],
            },
        }
        retry_headers = {"Origin": "http://testserver", "Idempotency-Key": "recovery-retry"}
        first_response = client.post(
            f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/retry",
            json=retry_body,
            headers=retry_headers,
        )
        assert first_response.status_code == 200, first_response.text
        first = first_response.json()["outcome"]
        assert first["status"] == "running"

    with DispatcherStore(REPO, state) as store:
        lease = store.lease("analyze-dif")
        assert lease is not None and lease["run_id"] == first["run_id"]
        old_token = lease["lease_token"]
        with store.conn:
            store.conn.execute(
                "UPDATE dispatcher_leases SET process_identity = ? WHERE job_id = ?",
                (json.dumps({"pid": 999_999_999, "start_time": "0", "boot_id": "dead"}), "analyze-dif"),
            )

    with TestClient(create_app(state, [REPO], testing=True)) as client:
        recovered_response = client.post(
            f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/retry",
            json=retry_body,
            headers=retry_headers,
        )
        assert recovered_response.status_code == 200, recovered_response.text
        recovered = recovered_response.json()["outcome"]
        assert recovered["run_id"] == first["run_id"]
        assert recovered["status"] == "running"
    with DispatcherStore(REPO, state) as store:
        assert store.lease("analyze-dif")["lease_token"] != old_token


def test_analyze_dif_has_no_approval_action(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        project = _bookmark(client, tmp_path)
        response = client.post(
            f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/approve-noise",
            json={},
            headers={"Origin": "http://testserver", "Idempotency-Key": "noise-forbidden"},
        )
        assert response.status_code == 422
        assert "unsupported dispatcher action" in response.text
