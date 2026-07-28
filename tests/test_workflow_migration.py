from __future__ import annotations

import json
from pathlib import Path

import pytest

from one_c_autoresearch.contracts import canonical_json, sha256
from one_c_autoresearch.sqlite_state import DispatcherStore
from one_c_autoresearch.workflow_migration import (
    PHASES,
    advance,
    guard_mutation,
    import_compatible_analyzer_results,
    journal_path,
    recover,
    rollback,
    start_migration,
)


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    base = tmp_path / "state"
    (repo / "research").mkdir(parents=True)
    (repo / "research/workflow.toml").write_text("schema_version = \"3\"\n")
    (repo / "research/active-source-generation.json").write_text('{"old":true}\n')
    return repo, base


def _start(repo: Path, base: Path) -> dict:
    return start_migration(
        repo,
        repository_fingerprint="sha256:" + "1" * 64,
        workflow_fingerprint="sha256:" + "2" * 64,
        owner="test",
        base=base,
    )


def test_atomic_handoff_interrupts_only_legacy_and_blocks_other_leases(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    with DispatcherStore(repo, base) as store:
        token = store.acquire_lease(
            "discover-mrq", "legacy-thread", "work", "owner", None,
            run_id="legacy-run",
        )
        assert token
        with store.conn:
            store.conn.execute(
                "INSERT INTO dispatcher_invocations "
                "(invocation_id, job_id, run_id, phase_id, role_id, work_unit_id, "
                "slot_id, status, created_at, updated_at) "
                "VALUES ('inv-1', 'discover-mrq', 'legacy-run', 'analyze-dif', "
                "'analyzer', 'DIF-1', 'slot', 'running', 'now', 'now')"
            )
            store.conn.execute(
                "INSERT INTO dispatcher_phase_work "
                "(job_id, run_id, phase_id, role_id, work_unit_id, status, "
                "invocation_id, updated_at) VALUES "
                "('discover-mrq', 'legacy-run', 'analyze-dif', 'analyzer', "
                "'DIF-1', 'running', 'inv-1', 'now')"
            )
            store.conn.execute(
                "INSERT INTO dispatcher_proposals "
                "(key, job_id, thread_id, kind, payload, created_at, consumed_at) "
                "VALUES ('approval-1', 'discover-mrq', 'legacy-thread', 'approval', "
                "'{}', 'now', NULL)"
            )

    journal = _start(repo, base)
    assert journal["legacy_lease_preimage"]["run_id"] == "legacy-run"
    with DispatcherStore(repo, base) as store:
        assert store.lease("discover-mrq") is None
        assert store.lease("workflow-migration")["lease_token"] == journal["lease_token"]
        assert store.acquire_lease("analyze-dif", "new", "work", "owner", None) is None
        assert store.conn.execute(
            "SELECT status FROM dispatcher_invocations WHERE invocation_id = 'inv-1'"
        ).fetchone()[0] == "interrupted"
        assert store.conn.execute(
            "SELECT status FROM dispatcher_phase_work WHERE invocation_id = 'inv-1'"
        ).fetchone()[0] == "interrupted"
        assert store.conn.execute(
            "SELECT consumed_at FROM dispatcher_proposals WHERE key = 'approval-1'"
        ).fetchone()[0] is not None


@pytest.mark.parametrize("conflict", ["stage-recompute", "analyze-dif", "consolidate-mrq"])
def test_migration_rejects_every_nonlegacy_conflict(tmp_path: Path, conflict: str) -> None:
    repo, base = _repo(tmp_path)
    with DispatcherStore(repo, base) as store:
        assert store.acquire_lease(conflict, "thread", "work", "owner", None)
    with pytest.raises(RuntimeError, match="conflicts"):
        _start(repo, base)


def test_missing_journal_is_reconstructed_idempotently_after_sqlite_handoff(
    tmp_path: Path,
) -> None:
    repo, base = _repo(tmp_path)
    journal = _start(repo, base)
    path = journal_path(repo, base)
    payload = path.read_bytes()
    path.unlink()

    first = recover(repo, journal["migration_id"], base=base)
    second = recover(repo, journal["migration_id"], base=base)

    assert first["phase"] == second["phase"] == "prepared"
    assert path.read_bytes() == payload


@pytest.mark.parametrize("phase", PHASES[:-1])
def test_uncommitted_journal_guards_mutation_at_every_boundary(
    tmp_path: Path, phase: str
) -> None:
    repo, base = _repo(tmp_path)
    journal = _start(repo, base)
    current = journal["phase"]
    while current != phase:
        current = PHASES[PHASES.index(current) + 1]
        journal = advance(
            repo, journal["migration_id"], journal["lease_token"], current,
            base=base,
        )
    with pytest.raises(RuntimeError, match="in progress"):
        guard_mutation(repo, base=base)


def test_every_phase_resumes_idempotently_and_commit_releases_fence(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    journal = _start(repo, base)
    for phase in PHASES[1:]:
        journal = advance(
            repo, journal["migration_id"], journal["lease_token"], phase,
            effects={phase: True}, result={"done": phase == "committed"},
            base=base,
        )
        repeated = advance(
            repo, journal["migration_id"], journal["lease_token"], phase,
            base=base,
        ) if phase != "committed" else journal
        assert repeated["phase"] == phase
    guard_mutation(repo, base=base)
    assert _start(repo, base) == {"done": True}
    with DispatcherStore(repo, base) as store:
        assert store.lease("workflow-migration") is None


def test_fencing_loss_rejects_recovery_and_phase_write(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    journal = _start(repo, base)
    with DispatcherStore(repo, base) as store, store.conn:
        store.conn.execute("DELETE FROM dispatcher_leases WHERE job_id = 'workflow-migration'")
    with pytest.raises(RuntimeError, match="fenced"):
        recover(repo, journal["migration_id"], base=base)
    with pytest.raises(RuntimeError, match="fenced"):
        advance(
            repo, journal["migration_id"], journal["lease_token"],
            "legacy-interrupted", base=base,
        )


def test_recovery_rolls_back_when_prepared_inputs_changed(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    journal = _start(repo, base)
    (repo / "research/workflow.toml").write_text("unexpected\n")

    result = recover(repo, journal["migration_id"], base=base)

    assert result["phase"] == "rolled_back"
    assert (repo / "research/workflow.toml").read_text() == 'schema_version = "3"\n'


def test_rollback_restores_exact_files_and_terminalizes_restored_legacy_work(
    tmp_path: Path,
) -> None:
    repo, base = _repo(tmp_path)
    with DispatcherStore(repo, base) as store:
        token = store.acquire_lease(
            "discover-mrq", "legacy", "work", "owner", None, run_id="legacy-run"
        )
        assert token
        with store.conn:
            store.conn.execute(
                "INSERT INTO dispatcher_invocations "
                "(invocation_id, job_id, run_id, phase_id, role_id, work_unit_id, "
                "slot_id, status, created_at, updated_at) VALUES "
                "('legacy-inv', 'discover-mrq', 'legacy-run', 'analyze-dif', "
                "'analyzer', 'DIF-1', 'slot', 'running', 'now', 'now')"
            )
    workflow_before = (repo / "research/workflow.toml").read_bytes()
    pointer_before = (repo / "research/active-source-generation.json").read_bytes()
    journal = _start(repo, base)
    (repo / "research/workflow.toml").write_text("schema_version = \"4\"\n")
    (repo / "research/active-source-generation.json").write_text("{}\n")
    (repo / "research/active-consolidation-generation.json").write_text("{}\n")

    result = rollback(repo, journal["migration_id"], base=base)

    assert result["phase"] == "rolled_back"
    assert (repo / "research/workflow.toml").read_bytes() == workflow_before
    assert (repo / "research/active-source-generation.json").read_bytes() == pointer_before
    assert not (repo / "research/active-consolidation-generation.json").exists()
    with DispatcherStore(repo, base) as store:
        assert store.lease("workflow-migration") is None
        assert store.lease("discover-mrq") is None
        assert store.conn.execute(
            "SELECT status FROM dispatcher_invocations WHERE invocation_id = 'legacy-inv'"
        ).fetchone()[0] == "interrupted"


def test_only_exact_analyzer_envelopes_are_imported(tmp_path: Path, monkeypatch) -> None:
    repo, _base = _repo(tmp_path)
    pointer = repo / "research/active-dif-classification-generation.json"
    published: list[dict] = []
    monkeypatch.setattr(
        "one_c_autoresearch.workflow_migration.publish_empty",
        lambda _repo: pointer.write_text('{"generation_id":"empty"}'),
    )
    monkeypatch.setattr(
        "one_c_autoresearch.workflow_migration.publish_window",
        lambda _repo, rows, **_kwargs: published.extend(rows),
    )
    monkeypatch.setattr(
        "one_c_autoresearch.workflow_migration.coverage",
        lambda _repo: {"remaining": 2},
    )
    fingerprint = "sha256:" + "a" * 64
    result = {
        "stable_diff_id": "DIF-1",
        "kind": "meaning",
        "semantic_hints": ["sales"],
        "evidence": [{"path": "x", "fingerprint": fingerprint}],
        "rationale": "business change",
    }
    expected = {
        "DIF-1": {
            "compatibility_fingerprint": "sha256:" + "b" * 64,
            "evidence_fingerprint": fingerprint,
            "result_schema_fingerprint": fingerprint,
            "profile_fingerprint": fingerprint,
            "instruction_fingerprint": fingerprint,
            "context_fingerprint": fingerprint,
        }
    }
    valid = {
        "compatibility_fingerprint": expected["DIF-1"]["compatibility_fingerprint"],
        "result_fingerprint": "sha256:" + sha256(canonical_json(result)),
        "result": result,
    }
    grouping = {
        "compatibility_fingerprint": valid["compatibility_fingerprint"],
        "result_fingerprint": "sha256:" + sha256(canonical_json({"groups": []})),
        "result": {"groups": []},
    }

    counts = import_compatible_analyzer_results(
        repo, [valid, grouping, {**valid, "compatibility_fingerprint": "stale"}],
        expected,
    )

    assert counts == {"imported": 1, "rejected": 2, "remaining": 2}
    assert [row["stable_diff_id"] for row in published] == ["DIF-1"]


def test_committed_external_journal_releases_lease_after_crash(
    tmp_path: Path, monkeypatch,
) -> None:
    repo, base = _repo(tmp_path)
    journal = _start(repo, base)
    token = journal["lease_token"]
    for phase in ("legacy-interrupted", "classification-published", "catalog-switched"):
        journal = advance(repo, journal["migration_id"], token, phase, base=base)
    original = DispatcherStore.update_workflow_migration
    monkeypatch.setattr(
        DispatcherStore,
        "update_workflow_migration",
        lambda *args, **kwargs: False,
    )
    with pytest.raises(RuntimeError, match="fenced"):
        advance(
            repo, journal["migration_id"], token, "committed",
            result={"migration_id": journal["migration_id"], "phase": "committed"},
            base=base,
        )
    monkeypatch.setattr(DispatcherStore, "update_workflow_migration", original)

    guard_mutation(repo, base=base)

    with DispatcherStore(repo, base) as store:
        assert store.lease("workflow-migration") is None
        assert store.workflow_migration(journal["migration_id"])["status"] == "committed"
