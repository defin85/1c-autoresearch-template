from pathlib import Path
import threading

from one_c_autoresearch.events import EventStore
from one_c_autoresearch.runner import run_next, run_until_blocked
import pytest


def test_read_only_verification_stops_cleanly_when_repository_is_still_blocked(tmp_path: Path, monkeypatch):
    snapshot = {"workflow_fingerprint": "sha256:x", "manifest_fingerprint": "sha256:m", "state": "ready", "gates": []}
    work = {"action": "workflow.verify", "gate_id": "published", "blocker": {"code": "publication.strict", "message": "untracked", "action": "workflow.verify"}}
    monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: snapshot)
    monkeypatch.setattr("one_c_autoresearch.runner.step_configurations", lambda _repo: [{"step": {"id": "verify-workflow", "operation": "workflow.verify", "operation_version": "1", "timeout_seconds": 1800}}])
    result = run_next(tmp_path, lambda _operation, _payload, _cancelled: snapshot, EventStore(tmp_path / "events", "project"), select=lambda: work)
    assert result["result"] == "blocked"


def test_idempotent_index_result_is_reused(tmp_path: Path, monkeypatch):
    snapshot = {"workflow_fingerprint": "sha256:x", "manifest_fingerprint": "sha256:m", "state": "ready", "gates": []}
    work = {"action": "indexes.build", "gate_id": "indexes-ready", "work_unit": {"id": "component", "component_ids": ["b", "a"]}, "blocker": {"code": "indexes.missing", "message": "missing", "action": "indexes.build"}}
    monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: snapshot)
    monkeypatch.setattr("one_c_autoresearch.runner.step_configurations", lambda _repo: [{"step": {"id": "index-sources", "operation": "indexes.build", "operation_version": "1", "timeout_seconds": 1800}}])
    store = EventStore(tmp_path / "events", "project"); calls = []
    def invoke(_operation, payload, _cancelled):
        calls.append(payload["component_ids"][0]); return {"components": [{"component_id": payload["component_ids"][0], "status": "ready"}]}
    selections = iter((work, None))
    assert run_next(tmp_path, invoke, store, select=lambda: next(selections))["result"] == "progressed"
    repeated = iter((work, None))
    assert run_next(tmp_path, invoke, store, select=lambda: next(repeated))["result"] == "reused"
    assert calls == ["a", "b"]
    assert [event["payload"]["progress"]["component_id"] for event in store.events() if event["type"] == "step.progress"] == ["a", "b"]
    assert len([event for event in store.events() if event["type"] == "log.append"]) == 2
    terminal = [event for event in store.events() if event["type"].endswith(".finished")]
    assert terminal and all(event["payload"]["duration_seconds"] >= 0 for event in terminal)
    assert all(event["payload"].get("operation") == "indexes.build" for event in terminal[:-1])


def test_deleted_operational_result_is_rebuilt_instead_of_reused(tmp_path: Path, monkeypatch):
    before = {"workflow_fingerprint": "sha256:x", "manifest_fingerprint": "sha256:m", "state": "ready", "gates": []}
    after = {**before, "workflow_fingerprint": "sha256:y"}
    work = {"action": "indexes.build", "gate_id": "indexes-ready", "work_unit": {"id": "component", "component_ids": ["a"]}, "blocker": {"code": "indexes.missing", "message": "missing", "action": "indexes.build"}}
    monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: before)
    monkeypatch.setattr("one_c_autoresearch.runner.step_configurations", lambda _repo: [{"step": {"id": "index-sources", "operation": "indexes.build", "operation_version": "1", "timeout_seconds": 1800}}])
    store = EventStore(tmp_path / "events", "project"); calls = 0
    def invoke(_operation, _payload, _cancelled):
        nonlocal calls; calls += 1
        monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: after)
        return {"components": [{"component_id": "a", "status": "ready"}]}
    first = iter((work, None))
    assert run_next(tmp_path, invoke, store, select=lambda: next(first))["result"] == "progressed"
    monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: before)
    second = iter((work, work, None))
    assert run_next(tmp_path, invoke, store, select=lambda: next(second))["result"] == "progressed"
    assert calls == 2


def test_source_acquisition_emits_component_progress(tmp_path: Path, monkeypatch):
    before = {"workflow_fingerprint": "sha256:x", "manifest_fingerprint": "sha256:m", "state": "ready", "gates": []}
    after = {**before, "workflow_fingerprint": "sha256:y"}
    work = {"action": "sources.acquire", "gate_id": "sources-acquired", "blocker": {"code": "sources.missing", "message": "missing", "action": "sources.acquire"}}
    monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: before)
    monkeypatch.setattr("one_c_autoresearch.runner.step_configurations", lambda _repo: [{"step": {"id": "acquire-sources", "operation": "sources.acquire", "operation_version": "1", "timeout_seconds": 1800}}])
    def invoke(_operation, _payload, _cancelled):
        monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: after)
        return {"components": [{"component_id": "vendor_baseline:configuration"}, {"component_id": "target_cf:configuration"}]}
    selected = iter((work, None)); store = EventStore(tmp_path / "events", "project")
    assert run_next(tmp_path, invoke, store, select=lambda: next(selected), approved_operations={"sources.acquire"})["result"] == "progressed"
    assert [event["payload"]["progress"]["component_id"] for event in store.events() if event["type"] == "step.progress"] == ["vendor_baseline:configuration", "target_cf:configuration"]


def test_only_catalogued_transient_failure_is_retried(tmp_path: Path, monkeypatch):
    before = {"workflow_fingerprint": "sha256:x", "manifest_fingerprint": "sha256:m", "state": "ready", "gates": []}
    after = {**before, "workflow_fingerprint": "sha256:y"}
    statuses = iter((before, before, after))
    monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: next(statuses))
    monkeypatch.setattr("one_c_autoresearch.runner.step_configurations", lambda _repo: [{"step": {"id": "build-diffs", "operation": "diff.build", "operation_version": "1", "timeout_seconds": 1800, "max_retries": 1}}])
    work = {"action": "diff.build", "gate_id": "diffs-built", "blocker": {"code": "diff.missing", "message": "missing", "action": "diff.build"}}
    selections = iter((work, None)); calls = 0
    def invoke(_operation, _payload, _cancelled):
        nonlocal calls; calls += 1
        if calls == 1: raise OSError("temporary start failure")
        return {"generation_id": "g"}
    store = EventStore(tmp_path / "events", "project")
    assert run_next(tmp_path, invoke, store, select=lambda: next(selections))["result"] == "progressed"
    assert calls == 2 and [event["attempt"] for event in store.events() if event["type"] == "step.started"] == [1, 2]


def test_validation_failure_is_not_retried(tmp_path: Path, monkeypatch):
    snapshot = {"workflow_fingerprint": "sha256:x", "manifest_fingerprint": "sha256:m", "state": "ready", "gates": []}
    work = {"action": "diff.build", "gate_id": "diffs-built", "blocker": {"code": "diff.missing", "message": "missing", "action": "diff.build"}}
    monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: snapshot)
    monkeypatch.setattr("one_c_autoresearch.runner.step_configurations", lambda _repo: [{"step": {"id": "build-diffs", "operation": "diff.build", "operation_version": "1", "timeout_seconds": 1800, "max_retries": 1}}])
    calls = 0
    def invoke(*_args):
        nonlocal calls; calls += 1; raise ValueError("invalid candidate")
    with pytest.raises(ValueError, match="invalid candidate"):
        run_next(tmp_path, invoke, EventStore(tmp_path / "events", "project"), select=lambda: work)
    assert calls == 1


def test_read_only_runs_can_execute_in_parallel(tmp_path: Path, monkeypatch):
    snapshot = {"workflow_fingerprint": "sha256:x", "manifest_fingerprint": "sha256:m", "state": "ready", "gates": []}
    work = {"action": "workflow.verify", "gate_id": "published", "blocker": {"code": "publication.strict", "message": "blocked", "action": "workflow.verify"}}
    monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: snapshot)
    monkeypatch.setattr("one_c_autoresearch.runner.step_configurations", lambda _repo: [{"step": {"id": "verify-workflow", "operation": "workflow.verify", "operation_version": "1", "timeout_seconds": 1800}}])
    barrier = threading.Barrier(2); results = []
    def invoke(*_args):
        barrier.wait(timeout=2); return snapshot
    threads = [threading.Thread(target=lambda index=index: results.append(run_next(tmp_path, invoke, EventStore(tmp_path / "events", "project"), select=lambda: work)["result"])) for index in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(5)
    assert results == ["blocked", "blocked"]


def test_run_until_blocked_honours_stop_and_bound(monkeypatch, tmp_path: Path):
    results = iter(({"result": "progressed"}, {"result": "blocked"}))
    monkeypatch.setattr("one_c_autoresearch.runner.run_next", lambda *_args, **_kwargs: next(results))
    monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: {"state": "ready"})
    assert run_until_blocked(tmp_path, lambda *_: {}, EventStore(tmp_path / "events", "project"), max_units=3)["result"] == "blocked"
    monkeypatch.setattr("one_c_autoresearch.runner.run_next", lambda *_args, **_kwargs: {"result": "progressed"})
    assert run_until_blocked(tmp_path, lambda *_: {}, EventStore(tmp_path / "events", "project"), max_units=2)["result"] == "bounded"


def test_cancellation_stops_before_effect(tmp_path: Path, monkeypatch):
    snapshot = {"workflow_fingerprint": "sha256:x", "manifest_fingerprint": "sha256:m", "state": "ready", "gates": []}
    work = {"action": "diff.build", "gate_id": "diffs-built", "blocker": {"code": "diff.missing", "message": "missing", "action": "diff.build"}}
    monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: snapshot)
    monkeypatch.setattr("one_c_autoresearch.runner.step_configurations", lambda _repo: [{"step": {"id": "build-diffs", "operation": "diff.build", "operation_version": "1", "timeout_seconds": 1800, "max_retries": 0}}])
    store = EventStore(tmp_path / "events", "project")
    monkeypatch.setattr(store, "cancellation", lambda _run_id: {"actor": "user"})
    assert run_next(tmp_path, lambda *_: pytest.fail("effect must not start"), store, select=lambda: work)["result"] == "cancelled"


def test_agent_proposal_requires_a_separate_approved_run(tmp_path: Path, monkeypatch):
    before = {"workflow_fingerprint": "sha256:x", "manifest_fingerprint": "sha256:m", "state": "ready", "gates": []}
    after = {**before, "workflow_fingerprint": "sha256:y"}
    work = {"action": "dif.classify-next", "gate_id": "diffs-classified", "work_unit": {"id": "DIF-AAAAAAAAAAAAAAAA", "allowed_paths": ["configuration/a.bsl"]}, "blocker": {"code": "mrq.ownership", "message": "missing", "action": "dif.classify-next"}}
    monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: before)
    monkeypatch.setattr("one_c_autoresearch.runner.step_configurations", lambda _repo: [{"step": {"id": "analyze-dif", "operation": "dif.classify-next", "operation_version": "2", "timeout_seconds": 1800, "agent_phases": [{"phase_id": "analyze-dif", "mode": "parallel-pool", "max_concurrency": 4, "roles": [{"role_id": "analyzer", "agent_profile": "local", "count": 4, "instruction_supplement": ""}]}]}}])
    proposal = {"semantic_key": "requirement", "title": "Requirement", "stable_diff_ids": [work["work_unit"]["id"]], "supporting_diff_ids": [], "evidence": [{"path": "configuration/a.bsl", "fingerprint": "sha256:" + "a" * 64, "stable_diff_id": work["work_unit"]["id"]}], "business_meaning": "Meaning", "scope": "Scope", "confidence": "high", "rationale": "Evidence"}
    agent_calls = 0
    def agent(*_args):
        nonlocal agent_calls; agent_calls += 1; return proposal
    applied = []
    def invoke(operation, payload, _cancelled):
        applied.append((operation, payload)); monkeypatch.setattr("one_c_autoresearch.runner.status", lambda _repo: after); return {"mrq_id": "MRQ-X"}
    store = EventStore(tmp_path / "events", "project")
    profile = {"local": {"provider": "codex-cli", "model": "gpt-5", "reasoning_effort": "high", "instructions_version": "1", "environment_preset": "local-read-only"}}
    first = run_next(tmp_path, invoke, store, select=lambda: work, approved_operations={"dif.classify-next"}, agent_profiles=profile, agent_executor=agent)
    assert first["result"] == "blocked"
    assert first["blocker"]["code"] == "dispatcher.required"
    assert not applied and agent_calls == 0 and store.events() == []
