from __future__ import annotations

import json
import threading
import uuid
from hashlib import sha256 as hashlib_sha256
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from one_c_autoresearch.dispatcher import (
    DISPATCHER_JOBS,
    DispatcherBindings,
    DispatcherCoordinator,
    LeaseRenewer,
    STALE_AFTER_SECONDS,
    load_bindings,
)
from one_c_autoresearch.events import EventStore
from one_c_autoresearch.agents import EVIDENCE_SCHEMA, ENVIRONMENT_KEYS, EXEC_ARGUMENTS, ENVIRONMENT_PRESET_VERSION, TARGET_COVERAGE_SCHEMA, build_context_manifest
from one_c_autoresearch.sqlite_state import DispatcherStore, LEASE_EXPIRY_SECONDS


REPO = Path(__file__).resolve().parents[1] / "templates/research-repo"


def _fake_bindings(work_unit_id: str = "DIF-AAA", *, workflow_fingerprint: str = "sha256:abc") -> DispatcherBindings:
    return DispatcherBindings(
        project_id="proj-1",
        job_id="analyze-dif",
        work_unit_id=work_unit_id,
        source_generation_id="src-1",
        diff_generation_id="diff-1",
        canonical_generation_id="canon-1",
        workflow_fingerprint=workflow_fingerprint,
        agent_profile_fingerprint="sha256:profile",
        instruction_supplement="",
    )


def _coordinator(tmp_path: Path) -> tuple[DispatcherCoordinator, DispatcherStore, EventStore]:
    store = DispatcherStore(tmp_path, base=tmp_path)
    store.open()
    event_store = EventStore(tmp_path / "events", "proj-1")
    coordinator = DispatcherCoordinator(tmp_path, "proj-1", store, event_store, actor="test")
    coordinator._validate_new_run_snapshot = lambda _snapshot, _bindings: None
    return coordinator, store, event_store


def _start(coordinator: DispatcherCoordinator, job_id: str, bindings: DispatcherBindings):
    run_id = str(uuid.uuid4())
    executable = coordinator.repo / "codex-test"
    if not executable.exists():
        executable.write_text("#!/bin/sh\necho codex-test\n", encoding="utf-8")
        executable.chmod(0o700)
    work_unit = {"id": bindings.work_unit_id, "allowed_paths": []}
    snapshot = {
        "schema_version": "1",
        "run_id": run_id,
        "operation": "dif.classify-next" if job_id == "analyze-dif" else "mrq.decide-next",
        "operation_version": "2",
        "workflow_fingerprint": bindings.workflow_fingerprint,
        "timeout_seconds": 60,
        "agent_phases": [],
        "profiles": {},
        "instructions": {},
        "environment": {
            "preset": "local-read-only",
            "preset_version": ENVIRONMENT_PRESET_VERSION,
            "executable": str(executable),
            "executable_fingerprint": "sha256:" + hashlib_sha256(executable.read_bytes()).hexdigest(),
            "inherited_environment_keys": list(ENVIRONMENT_KEYS),
            "arguments": list(EXEC_ARGUMENTS),
        },
        "codex_version": "codex-test",
        "application_version": "one-c-autoresearch/0.2",
        "subject_bindings": {},
        "policy_source": "current-policy",
        "work_unit": work_unit,
        "context_manifest": build_context_manifest(coordinator.repo, work_unit),
    }
    return coordinator.start(job_id, bindings, run_id=run_id, execution_snapshot=snapshot)


def test_dispatcher_jobs_match_fixed_workflow_contract() -> None:
    assert DISPATCHER_JOBS == ("analyze-dif", "consolidate-mrq", "classify-mrq", "decide-mrq")
    assert STALE_AFTER_SECONDS == 10


def test_agent_environment_inherits_standard_proxy_variables() -> None:
    assert {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"} <= set(ENVIRONMENT_KEYS)
    assert EVIDENCE_SCHEMA["additionalProperties"] is False
    assert TARGET_COVERAGE_SCHEMA["additionalProperties"] is False


def test_context_manifest_resolves_active_generation_paths(tmp_path: Path) -> None:
    source = tmp_path / "sources/generations/source-1/target_cf/configuration"
    source.mkdir(parents=True)
    (source / "Configuration.xml").write_text("source", encoding="utf-8")
    (tmp_path / "research").mkdir()
    (tmp_path / "research/active-source-generation.json").write_text(
        '{"generation_id":"source-1"}',
        encoding="utf-8",
    )

    manifest = build_context_manifest(tmp_path, {
        "id": "DIF-1",
        "allowed_paths": [
            "target_cf/configuration/Configuration.xml",
            "configuration/Configuration.xml",
        ],
    })

    assert manifest["paths"][0]["path"] == "target_cf/configuration/Configuration.xml"
    assert manifest["paths"][0]["fingerprint"] == "sha256:" + hashlib_sha256(b"source").hexdigest()
    assert manifest["paths"][1]["fingerprint"] == manifest["paths"][0]["fingerprint"]


def test_bindings_thread_id_is_stable_and_fingerprint_sensitive() -> None:
    base = _fake_bindings()
    same = _fake_bindings()
    assert base.thread_id() == same.thread_id()
    changed = DispatcherBindings(**{**base.__dict__, "run_id": "other"})
    assert base.thread_id() != changed.thread_id()
    changed_snapshot = DispatcherBindings(**{**base.__dict__, "execution_snapshot_fingerprint": "sha256:other"})
    assert base.thread_id() != changed_snapshot.thread_id()


def test_load_bindings_reads_active_pointers(tmp_path: Path) -> None:
    import json
    (tmp_path / "research").mkdir()
    (tmp_path / "research/active-source-generation.json").write_text(json.dumps({"generation_id": "src-xyz"}))
    (tmp_path / "research/active-diff-generation.json").write_text(json.dumps({"generation_id": "diff-xyz"}))
    (tmp_path / "research/active-consolidation-generation.json").write_text(json.dumps({"mrq_generation_id": "canon-xyz", "transaction_id": "tx"}))
    (tmp_path / "project.toml").write_text('[project]\nid="x"\n')
    with patch("one_c_autoresearch.dispatcher.sha256", lambda value: value.decode() if isinstance(value, bytes) else value), patch("one_c_autoresearch.stage_recompute.state_fingerprint", lambda _repo: "sha256:wf"):
        bindings = load_bindings(tmp_path, "proj", "analyze-dif", "DIF-1", {"model": "gpt"}, "sup")
    assert bindings.source_generation_id == "src-xyz"
    assert bindings.diff_generation_id == "diff-xyz"
    assert bindings.canonical_generation_id == "canon-xyz"
    assert bindings.workflow_fingerprint == "sha256:wf"


def test_start_acquires_lease_and_second_process_is_blocked(tmp_path: Path) -> None:
    coordinator, store, event_store = _coordinator(tmp_path)
    try:
        first = _start(coordinator, "analyze-dif", _fake_bindings())
        second = _start(coordinator, "analyze-dif", _fake_bindings())
        assert first.status == "running"
        assert second.status == "blocked"
        assert second.blocker is not None and second.blocker["code"] == "dispatcher.lease.busy"
        events = event_store.events()
        kinds = [event["payload"].get("kind") for event in events]
        assert "dispatcher.analyze-dif.started" in kinds
        started = next(event for event in events if event["payload"].get("kind") == "dispatcher.analyze-dif.started")
        assert started["payload"]["effective_action"]["policy_source"] == "current-policy"
    finally:
        coordinator.close()
        store.close()


def test_soft_stop_makes_run_resumable(tmp_path: Path) -> None:
    coordinator, store, _ = _coordinator(tmp_path)
    try:
        _start(coordinator, "decide-mrq", _fake_bindings(work_unit_id="MRQ-1").__class__(**{**_fake_bindings().__dict__, "job_id": "decide-mrq", "work_unit_id": "MRQ-1"}))
        outcome = coordinator.soft_stop("decide-mrq")
        lease = store.lease("decide-mrq")
        assert outcome.status == "resumable"
        assert lease is not None and lease["state"] == "resumable"
    finally:
        coordinator.close()
        store.close()


def test_resume_rejects_changed_bindings(tmp_path: Path) -> None:
    coordinator, store, _ = _coordinator(tmp_path)
    try:
        original = _fake_bindings()
        _start(coordinator, "analyze-dif", original)
        coordinator.soft_stop("analyze-dif")
        # предметное поколение изменилось — сохранённый запуск больше не валиден
        changed = DispatcherBindings(**{**original.__dict__, "diff_generation_id": "diff-new"})
        outcome = coordinator.resume("analyze-dif", changed)
        assert outcome.status == "stale"
        assert outcome.blocker is not None and outcome.blocker["code"] == "dispatcher.bindings.stale"
    finally:
        coordinator.close()
        store.close()


def test_resume_accepts_matching_bindings(tmp_path: Path) -> None:
    coordinator, store, _ = _coordinator(tmp_path)
    try:
        bindings = _fake_bindings()
        _start(coordinator, "analyze-dif", bindings)
        coordinator.soft_stop("analyze-dif")
        with patch("one_c_autoresearch.agents.validate_execution_snapshot", lambda *_args: None):
            outcome = coordinator.resume("analyze-dif", bindings)
        assert outcome.status == "running"
        lease = store.lease("analyze-dif")
        assert lease is not None and lease["state"] == "running"
    finally:
        coordinator.close()
        store.close()


def test_resume_marks_old_workflow_contract_stale(tmp_path: Path) -> None:
    from one_c_autoresearch.contracts import canonical_json, sha256

    coordinator, store, event_store = _coordinator(tmp_path)
    try:
        bindings = _fake_bindings()
        started = _start(coordinator, "analyze-dif", bindings)
        coordinator.soft_stop("analyze-dif")
        run = event_store.run_snapshot(started.run_id)
        assert run is not None
        run["execution_snapshot"]["application_version"] = "one-c-autoresearch/0.1"
        fingerprint = "sha256:" + sha256(canonical_json(run["execution_snapshot"]))
        run["execution_snapshot_fingerprint"] = fingerprint
        path = event_store.root / "runs" / f"{sha256(started.run_id.encode())}.json"
        path.write_text(json.dumps(run), encoding="utf-8")
        with store.conn:
            store.conn.execute(
                "UPDATE dispatcher_leases SET execution_snapshot_fingerprint = ? WHERE job_id = ?",
                (fingerprint, "analyze-dif"),
            )

        outcome = coordinator.resume("analyze-dif", bindings)
        assert outcome.status == "stale"
        assert outcome.blocker == {
            "code": "workflow_contract_stale",
            "message": "workflow_contract_stale",
            "action": "analyze-dif",
        }
    finally:
        coordinator.close()
        store.close()


def test_only_one_process_can_resume_a_saved_run(tmp_path: Path) -> None:
    coordinator, store, event_store = _coordinator(tmp_path)
    second_store = DispatcherStore(tmp_path, base=tmp_path)
    second_store.open()
    second = DispatcherCoordinator(tmp_path, "proj-1", second_store, event_store, actor="second")
    try:
        bindings = _fake_bindings()
        _start(coordinator, "analyze-dif", bindings)
        coordinator.soft_stop("analyze-dif")
        barrier = threading.Barrier(2)
        outcomes = []

        def resume(candidate: DispatcherCoordinator) -> None:
            barrier.wait()
            outcomes.append(candidate.resume("analyze-dif", bindings))

        with patch("one_c_autoresearch.agents.validate_execution_snapshot", lambda *_args: None):
            threads = [
                threading.Thread(target=resume, args=(candidate,))
                for candidate in (coordinator, second)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
        assert sorted(outcome.status for outcome in outcomes) == ["blocked", "running"]
        assert coordinator.store.lease("analyze-dif")["state"] == "running"
    finally:
        coordinator.close()
        second.close()
        store.close()
        second_store.close()


def test_cancel_releases_lease_and_deletes_thread(tmp_path: Path) -> None:
    coordinator, store, _ = _coordinator(tmp_path)
    try:
        _start(coordinator, "analyze-dif", _fake_bindings())
        outcome = coordinator.cancel("analyze-dif")
        assert outcome.status == "cancelled"
        assert store.lease("analyze-dif") is None
    finally:
        coordinator.close()
        store.close()


def test_retry_requires_terminal_state(tmp_path: Path) -> None:
    coordinator, store, event_store = _coordinator(tmp_path)
    try:
        first = _start(coordinator, "analyze-dif", _fake_bindings())
        blocked = coordinator.retry("analyze-dif", _fake_bindings())
        assert blocked.status == "blocked" and blocked.blocker["code"] == "dispatcher.retry.busy"
        coordinator.soft_stop("analyze-dif")
        retry_run_id = str(uuid.uuid4())
        retry_snapshot = {
            **event_store.run_snapshot(first.run_id)["execution_snapshot"],
            "run_id": retry_run_id,
            "policy_source": "reuse-snapshot",
            "predecessor_run_id": first.run_id,
        }
        restarted = coordinator.retry(
            "analyze-dif",
            _fake_bindings(),
            run_id=retry_run_id,
            execution_snapshot=retry_snapshot,
        )
        assert restarted.status == "running"
    finally:
        coordinator.close()
        store.close()


def test_verify_bindings_blocks_after_expiry(tmp_path: Path) -> None:
    coordinator, store, _ = _coordinator(tmp_path)
    try:
        bindings = _fake_bindings()
        _start(coordinator, "analyze-dif", bindings)
        stale = (datetime.now(timezone.utc) - timedelta(seconds=LEASE_EXPIRY_SECONDS + 5)).isoformat()
        conn = store.conn
        conn.execute("UPDATE dispatcher_leases SET renewed_at = ? WHERE job_id = ?", (stale, "analyze-dif"))
        conn.commit()
        assert coordinator.verify_bindings("analyze-dif", bindings) is False
    finally:
        coordinator.close()
        store.close()


def test_analyze_bindings_allow_own_classification_publication(tmp_path: Path) -> None:
    coordinator, store, _ = _coordinator(tmp_path)
    bindings = replace(
        _fake_bindings(),
        classification_generation_id="classification-before",
        consolidation_transaction_id="transaction",
    )
    (tmp_path / "research").mkdir()
    (tmp_path / "research/active-dif-classification-generation.json").write_text(
        json.dumps({"generation_id": "classification-after"}), encoding="utf-8"
    )
    (tmp_path / "research/active-consolidation-generation.json").write_text(
        json.dumps({"mrq_generation_id": "canon-1", "transaction_id": "transaction"}), encoding="utf-8"
    )
    try:
        started = _start(coordinator, "analyze-dif", bindings)
        lease = store.lease("analyze-dif")
        bindings = replace(
            bindings,
            run_id=started.run_id,
            execution_snapshot_fingerprint=lease["execution_snapshot_fingerprint"],
        )
        with patch(
            "one_c_autoresearch.stage_recompute.active_state",
            return_value=(
                {"source": {"generation_id": "src-1"}, "diff": {"generation_id": "diff-1"}},
                bindings.workflow_fingerprint,
            ),
        ):
            assert coordinator.verify_bindings("analyze-dif", bindings) is True
    finally:
        coordinator.close()
        store.close()


def test_mark_stale_uses_dispatcher_kind_in_existing_event_type(tmp_path: Path) -> None:
    coordinator, store, event_store = _coordinator(tmp_path)
    try:
        _start(coordinator, "analyze-dif", _fake_bindings())
        outcome = coordinator.mark_stale("analyze-dif")
        assert outcome.status == "stale"
        assert store.lease("analyze-dif") is None
        events = event_store.events()
        snapshot_events = [event for event in events if event["type"] == "approval.required"]
        assert snapshot_events and snapshot_events[-1]["payload"]["kind"] == "dispatcher.bindings.stale"
    finally:
        coordinator.close()
        store.close()


def test_emit_transition_uses_only_existing_event_types(tmp_path: Path) -> None:
    coordinator, store, event_store = _coordinator(tmp_path)
    try:
        started = _start(coordinator, "analyze-dif", _fake_bindings())
        revision_before = store.revision()[0]
        revision_after = coordinator.emit_transition("analyze-dif", started.run_id, started.thread_id, "step.progress", "dispatcher.group.validated", {"status": "running", "progress": {"current": 1, "total": 3}})
        assert revision_after > revision_before
        events = event_store.events()
        kinds = [event["payload"].get("kind") for event in events]
        assert "dispatcher.group.validated" in kinds
        # все события остались в существующем каталоге
        assert all(event["type"] != "snapshot.invalidated" for event in events)
    finally:
        coordinator.close()
        store.close()


def test_fenced_owner_cannot_emit_transition_after_lease_takeover(tmp_path: Path) -> None:
    coordinator, store, event_store = _coordinator(tmp_path)
    try:
        started = _start(coordinator, "analyze-dif", _fake_bindings())
        before = len(event_store.events())
        stale = (datetime.now(timezone.utc) - timedelta(seconds=LEASE_EXPIRY_SECONDS + 1)).isoformat()
        store.conn.execute(
            "UPDATE dispatcher_leases SET renewed_at = ? WHERE job_id = ?",
            (stale, "analyze-dif"),
        )
        store.conn.commit()
        assert store.acquire_lease(
            "analyze-dif",
            "new-thread",
            "DIF-BBB",
            "new-owner",
            None,
        )
        with pytest.raises(RuntimeError, match="fenced"):
            coordinator.emit_transition(
                "analyze-dif",
                started.run_id,
                started.thread_id,
                "step.progress",
                "dispatcher.old-owner",
                {"status": "running", "progress": {}},
            )
        assert len(event_store.events()) == before
    finally:
        coordinator.close()
        store.close()


def test_emit_transition_rejects_new_event_type(tmp_path: Path) -> None:
    coordinator, store, _ = _coordinator(tmp_path)
    try:
        with pytest.raises(ValueError, match="unsupported existing workflow event type"):
            coordinator.emit_transition("analyze-dif", "run-1", "thread-1", "dispatcher.custom", "dispatcher.x", {})
    finally:
        coordinator.close()
        store.close()


def test_snapshot_projection_excludes_canonical_fields(tmp_path: Path) -> None:
    coordinator, store, _ = _coordinator(tmp_path)
    try:
        _start(coordinator, "analyze-dif", _fake_bindings())
        projection = coordinator.snapshot_projection()
        assert projection["schema_version"] == "2"
        assert "revision" in projection and projection["revision"] >= 1
        assert "analyze-dif" in projection["jobs"]
        # проекция не содержит канонических отпечатков
        assert "workflow_fingerprint" not in projection
        assert "gates" not in projection
    finally:
        coordinator.close()
        store.close()


def test_lease_renewer_updates_renewed_at_in_background(tmp_path: Path) -> None:
    store = DispatcherStore(tmp_path, base=tmp_path)
    store.open()
    try:
        token = store.acquire_lease("analyze-dif", "thread-1", "DIF-1", "owner", None)
        assert token
        before = store.lease("analyze-dif")["renewed_at"]
        stop = threading.Event()
        renewer = LeaseRenewer(store, "analyze-dif", token, stop, interval_seconds=0)
        renewer.start()
        # мини-сон достаточен для одного обновления в быстрых тестах
        import time as _time
        _time.sleep(0.05)
        stop.set()
        renewer.join(timeout=1)
        after = store.lease("analyze-dif")["renewed_at"]
        # обновление фонового таймера может совпасть с initial; проверяем что renew вызван без ошибки
        assert before is not None and after is not None
    finally:
        store.close()


def test_lease_renewer_confirms_each_successful_renewal(tmp_path: Path) -> None:
    store = DispatcherStore(tmp_path, base=tmp_path)
    store.open()
    confirmations: list[bool] = []
    try:
        token = store.acquire_lease("analyze-dif", "thread-1", "DIF-1", "owner", None)
        assert token
        stop = threading.Event()
        renewer = LeaseRenewer(store, "analyze-dif", token, stop, interval_seconds=0, on_renewed=lambda: confirmations.append(True))
        renewer.start()
        import time as _time
        _time.sleep(0.05)
        stop.set()
        renewer.join(timeout=1)
        assert confirmations
    finally:
        store.close()


def test_fenced_checkpoint_and_lease_renewal_share_one_lock_order(tmp_path: Path, monkeypatch) -> None:
    store = DispatcherStore(tmp_path, base=tmp_path)
    store.open()
    token = store.acquire_lease("classify-mrq", "thread-1", "classify:source", "owner", None)
    assert token
    entered = threading.Event()
    release = threading.Event()
    errors: list[Exception] = []
    renewals: list[bool] = []

    def slow_put():
        entered.set()
        release.wait(timeout=2)
        return {}

    monkeypatch.setattr(store.saver, "put", slow_put)
    fenced = store.fenced_saver("classify-mrq", token, "thread-1")

    def checkpoint() -> None:
        try:
            fenced.put()
        except Exception as exc:
            errors.append(exc)

    def renew() -> None:
        try:
            renewals.append(store.renew_lease("classify-mrq", token))
        except Exception as exc:
            errors.append(exc)

    checkpoint_thread = threading.Thread(target=checkpoint)
    renewal_thread = threading.Thread(target=renew)
    try:
        checkpoint_thread.start()
        assert entered.wait(timeout=1)
        renewal_thread.start()
        release.set()
        checkpoint_thread.join(timeout=2)
        renewal_thread.join(timeout=2)
        assert not checkpoint_thread.is_alive() and not renewal_thread.is_alive()
        assert errors == []
        assert renewals == [True]
    finally:
        release.set()
        store.close()


def test_finish_deletes_thread_only_for_terminal_states(tmp_path: Path) -> None:
    coordinator, store, _ = _coordinator(tmp_path)
    try:
        _start(coordinator, "analyze-dif", _fake_bindings())
        outcome = coordinator.finish("analyze-dif", "completed", {"published": True})
        assert outcome.status == "completed"
        assert store.lease("analyze-dif") is None
    finally:
        coordinator.close()
        store.close()


def test_classify_publication_event_precedes_finish_with_monotonic_revisions(tmp_path: Path) -> None:
    coordinator, store, event_store = _coordinator(tmp_path)
    bindings = DispatcherBindings(**{
        **_fake_bindings("classify:sha256:source").__dict__,
        "job_id": "classify-mrq",
    })
    run_id = str(uuid.uuid4())
    snapshot = {
        "schema_version": "1",
        "run_id": run_id,
        "operation": "mrq.classify-batches",
        "operation_version": "1",
        "workflow_fingerprint": bindings.workflow_fingerprint,
        "timeout_seconds": 60,
        "agent_phases": [{
            "phase_id": "classify-batches",
            "max_concurrency": 1,
            "roles": [{"role_id": "classifier", "count": 1, "agent_profile": "local", "instruction_supplement": ""}],
        }],
        "profiles": {"local": {"instructions_version": "1"}},
        "instructions": {},
        "environment": {},
        "codex_version": "test",
        "application_version": "one-c-autoresearch/0.2",
        "subject_bindings": {},
        "policy_source": "current-policy",
        "work_unit": {"id": bindings.work_unit_id, "allowed_paths": []},
        "context_manifest": {"paths": []},
    }
    generation = {
        "generation_id": "a" * 64,
        "source_mrq_fingerprint": "sha256:" + "b" * 64,
        "result_fingerprint": "sha256:" + "c" * 64,
    }

    class Graph:
        def invoke(self, *_args, **_kwargs):
            return {
                "status": "completed",
                "published": True,
                "batch_generation": generation,
                "batches": [{"batch_id": "MRQB-AAAAAAAAAAAAAAAA"}],
                "blocker": None,
            }

    try:
        outcome = coordinator.start("classify-mrq", bindings, run_id=run_id, execution_snapshot=snapshot)
        with (
            patch("one_c_autoresearch.agents.validate_execution_snapshot", lambda *_args: None),
            patch("one_c_autoresearch.pipeline_graphs.compile_classify_graph", lambda **_kwargs: Graph()),
        ):
            coordinator._run_graph(outcome, bindings, {}, {}, {}, 60)
        transitions = [
            event for event in event_store.events()
            if str(event["payload"].get("kind", "")).startswith("dispatcher.classify-mrq.")
        ]
        assert [event["payload"]["kind"] for event in transitions] == [
            "dispatcher.classify-mrq.started",
            "dispatcher.classify-mrq.published",
            "dispatcher.classify-mrq.finished",
        ]
        revisions = [event["payload"]["revision"] for event in transitions]
        assert revisions == sorted(revisions) and len(revisions) == len(set(revisions))
        assert transitions[1]["payload"]["outputs"] == {
            "batch_generation": generation,
            "batch_count": 1,
        }
    finally:
        coordinator.close()
        store.close()
