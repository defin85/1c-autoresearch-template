from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from one_c_autoresearch.events import EventStore
from one_c_autoresearch.sqlite_state import DispatcherStore
from one_c_autoresearch.workspace_api import create_app


REPO = Path(__file__).resolve().parents[1]
ORIGIN = {"Origin": "http://testserver"}


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    state = tmp_path / "state"
    return TestClient(create_app(state, [REPO], testing=True)), state


def _bookmark(client: TestClient) -> dict:
    return client.post(
        "/api/v1/projects",
        json={"name": "example", "root": str(REPO)},
        headers={**ORIGIN, "Idempotency-Key": "bookmark"},
    ).json()


def _plan(fingerprint: str) -> dict:
    return {
        "boundary": "diffs",
        "workflow_fingerprint": fingerprint,
        "plan_fingerprint": "sha256:plan",
        "required_confirmations": ["confirm_recompute"],
        "steps": [],
    }


def test_preview_is_exact_and_does_not_create_dispatcher_state(tmp_path: Path, monkeypatch) -> None:
    client, state = _client(tmp_path)
    with client:
        project = _bookmark(client)
        fingerprint = client.get(f"/api/v1/projects/{project['id']}/workflow").json()["workflow_fingerprint"]
        db = state / "projects" / project["id"] / "dispatcher.sqlite"
        before = db.read_bytes()
        monkeypatch.setattr("one_c_autoresearch.stage_recompute.preview", lambda *_args, **_kwargs: _plan(fingerprint))
        response = client.post(
            f"/api/v1/projects/{project['id']}/stage-recompute/preview",
            json={"boundary": "diffs", "expected_workflow_fingerprint": fingerprint},
            headers=ORIGIN,
        )
        assert response.status_code == 200
        assert response.json()["plan_fingerprint"] == "sha256:plan"
        assert db.read_bytes() == before
        assert not (state / "projects" / project["id"] / "events.jsonl").exists()
        invalid = client.post(
            f"/api/v1/projects/{project['id']}/stage-recompute/preview",
            json={"boundary": "diffs", "expected_workflow_fingerprint": fingerprint, "unknown": True},
            headers=ORIGIN,
        )
        assert invalid.status_code == 422


def test_async_run_is_idempotent_fenced_and_events_hide_raw_key(tmp_path: Path, monkeypatch) -> None:
    client, state = _client(tmp_path)
    release = threading.Event()
    started = threading.Event()

    def execute(*_args, cancelled, **_kwargs):
        started.set()
        while not release.wait(0.01):
            if cancelled():
                raise InterruptedError("cancelled")
        return {"status": "unchanged"}

    with client:
        project = _bookmark(client)
        fingerprint = client.get(f"/api/v1/projects/{project['id']}/workflow").json()["workflow_fingerprint"]
        monkeypatch.setattr("one_c_autoresearch.stage_recompute.preview", lambda *_args, **_kwargs: _plan(fingerprint))
        monkeypatch.setattr("one_c_autoresearch.stage_recompute.execute", execute)
        body = {
            "boundary": "diffs",
            "workflow_fingerprint": fingerprint,
            "plan_fingerprint": "sha256:plan",
            "confirmations": ["confirm_recompute"],
        }
        headers = {**ORIGIN, "Idempotency-Key": "raw-run-key"}
        accepted = client.post(f"/api/v1/projects/{project['id']}/stage-recompute/runs", json=body, headers=headers)
        assert accepted.status_code == 202
        assert started.wait(1)
        repeated = client.post(f"/api/v1/projects/{project['id']}/stage-recompute/runs", json=body, headers=headers)
        assert repeated.status_code == 202
        assert repeated.json()["run_id"] == accepted.json()["run_id"]
        conflict = client.post(
            f"/api/v1/projects/{project['id']}/stage-recompute/runs",
            json={**body, "predecessor_run_id": "other"},
            headers=headers,
        )
        assert conflict.status_code == 409
        with DispatcherStore(REPO, state) as store:
            run = store.stage_run(accepted.json()["run_id"])
            assert run and store.stage_token_current(run["run_id"], run["lease_token"])
            assert not store.stage_token_current(run["run_id"], "stale-token")
        event_text = (state / "projects" / project["id"] / "events.jsonl").read_text(encoding="utf-8")
        assert "raw-run-key" not in event_text
        release.set()


def test_cancel_is_idempotent_and_stage_lease_excludes_mrq(tmp_path: Path, monkeypatch) -> None:
    client, state = _client(tmp_path)
    started = threading.Event()

    def execute(*_args, cancelled, **_kwargs):
        started.set()
        while not cancelled():
            time.sleep(0.01)
        raise InterruptedError("cancelled")

    with client:
        project = _bookmark(client)
        fingerprint = client.get(f"/api/v1/projects/{project['id']}/workflow").json()["workflow_fingerprint"]
        monkeypatch.setattr("one_c_autoresearch.stage_recompute.preview", lambda *_args, **_kwargs: _plan(fingerprint))
        monkeypatch.setattr("one_c_autoresearch.stage_recompute.execute", execute)
        response = client.post(
            f"/api/v1/projects/{project['id']}/stage-recompute/runs",
            json={"boundary": "diffs", "workflow_fingerprint": fingerprint, "plan_fingerprint": "sha256:plan", "confirmations": ["confirm_recompute"]},
            headers={**ORIGIN, "Idempotency-Key": "run"},
        )
        run_id = response.json()["run_id"]
        assert started.wait(1)
        blocked = client.post(
                f"/api/v1/projects/{project['id']}/dispatcher/analyze-dif/start",
            json={"actor": "local-user"},
            headers={**ORIGIN, "Idempotency-Key": "mrq"},
        )
        assert blocked.status_code == 409
        cancel_headers = {**ORIGIN, "Idempotency-Key": "cancel"}
        first = client.post(f"/api/v1/projects/{project['id']}/stage-recompute/runs/{run_id}/cancel", headers=cancel_headers)
        second = client.post(f"/api/v1/projects/{project['id']}/stage-recompute/runs/{run_id}/cancel", headers=cancel_headers)
        assert first.json() == second.json() == {"run_id": run_id, "status": "cancellation_requested"}
        for _ in range(100):
            events = EventStore(state / "projects", project["id"]).events()
            if any(item["type"] == "run.finished" and item["payload"]["status"] == "cancelled" for item in events):
                break
            time.sleep(0.01)
        events = EventStore(state / "projects", project["id"]).events()
        assert any(item["type"] == "run.finished" and item["payload"]["status"] == "cancelled" for item in events)


def test_saved_mrq_lease_blocks_stage_recompute_even_when_expired(tmp_path: Path) -> None:
    with DispatcherStore(REPO, tmp_path) as store:
        assert store.acquire_lease("analyze-dif", "thread", "DIF-1", "owner", None)
        with store.conn:
            store.conn.execute("UPDATE dispatcher_leases SET renewed_at = '2000-01-01T00:00:00+00:00'")
        try:
            store.start_stage_recompute("key", "sha256:request", "diffs", {"plan_fingerprint": "sha256:plan"})
        except RuntimeError as exc:
            assert "dispatcher lease is busy" in str(exc)
        else:
            raise AssertionError("saved MRQ lease must block stage recompute")


def test_predecessor_record_is_passed_to_core(tmp_path: Path, monkeypatch) -> None:
    client, state = _client(tmp_path)
    received = threading.Event()
    captured = {}
    with client:
        project = _bookmark(client)
        fingerprint = client.get(f"/api/v1/projects/{project['id']}/workflow").json()["workflow_fingerprint"]
        monkeypatch.setattr("one_c_autoresearch.stage_recompute.preview", lambda *_args, **_kwargs: _plan(fingerprint))
        with DispatcherStore(REPO, state) as store:
            predecessor, _ = store.start_stage_recompute("old-key", "sha256:old", "diffs", _plan(fingerprint))
            assert store.finish_stage_recompute(predecessor["run_id"], predecessor["lease_token"], "failed", {"steps": []})

        def execute(*_args, predecessor_run, **_kwargs):
            captured.update(predecessor_run)
            received.set()
            return {"status": "unchanged"}

        monkeypatch.setattr("one_c_autoresearch.stage_recompute.execute", execute)
        response = client.post(
            f"/api/v1/projects/{project['id']}/stage-recompute/runs",
            json={
                "boundary": "diffs",
                "workflow_fingerprint": fingerprint,
                "plan_fingerprint": "sha256:plan",
                "confirmations": ["confirm_recompute"],
                "predecessor_run_id": predecessor["run_id"],
            },
            headers={**ORIGIN, "Idempotency-Key": "new-key"},
        )
        assert response.status_code == 202
        assert received.wait(1)
        assert captured["run_id"] == predecessor["run_id"]
        assert captured["status"] == "failed"
