import json
from pathlib import Path

import pytest

from one_c_autoresearch.stage_recompute import (
    INTENT,
    POINTERS,
    active_pointers,
    build_plan,
    compatibility_fingerprint,
    execute_plan,
    fingerprint,
    prepare_resume,
    publish_pointers,
    recover_publication,
    safe_profile_fingerprint,
)


def _repo(tmp_path: Path) -> Path:
    research = tmp_path / "research"
    research.mkdir()
    for kind, name in POINTERS.items():
        (research / name).write_text(json.dumps({"kind": kind, "generation_id": f"old-{kind}"}), encoding="utf-8")
    return tmp_path


def test_plan_is_deterministic_and_profiles_are_secret_independent(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr("one_c_autoresearch.stage_recompute.state_fingerprint", lambda _repo: "sha256:workflow")
    route = {"preview_id": "route", "routing_plan_fingerprint": "sha256:route", "expires_at": "2099-01-01T00:00:00Z", "required_tools": [{"name": "ibcmd", "version": "1"}]}
    profiles = {"target": {"server": "db", "password": "first", "nested": {"token": "secret"}}}
    first = build_plan(repo, "sources", "sha256:workflow", routing_preview=route, profiles=profiles)
    profiles["target"]["password"] = "second"
    second = build_plan(repo, "sources", "sha256:workflow", routing_preview=route, profiles=profiles)
    assert first == second
    assert first["steps"][-1]["operation"] == "indexes.build"
    assert first["steps"][-1]["conditional"] is True
    assert first["required_confirmations"] == ["confirm_recompute", "confirm_sources_acquire"]
    assert "secret" not in json.dumps(first)
    assert safe_profile_fingerprint(profiles).startswith("sha256:")


def test_plan_rejects_stale_state_and_unknown_boundary(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr("one_c_autoresearch.stage_recompute.state_fingerprint", lambda _repo: "current")
    with pytest.raises(RuntimeError, match="stale"):
        build_plan(repo, "diffs", "old")
    with pytest.raises(ValueError, match="unsupported"):
        build_plan(repo, "mrq", "current")


def test_plan_rejects_missing_active_generation(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "research" / POINTERS["mrq"]).unlink()
    monkeypatch.setattr("one_c_autoresearch.stage_recompute.state_fingerprint", lambda _repo: "current")
    with pytest.raises(RuntimeError, match="requires active"):
        build_plan(repo, "diffs", "current")


def test_compatibility_fingerprint_covers_supporting_facts_coverage_and_approval():
    mrq = {"mrq_id": "MRQ-1", "source_generation_id": "old", "migration_decision": {"decision": "adapt", "diff_generation_id": "old"}}
    dispositions = [
        {"mrq_id": "MRQ-1", "stable_diff_id": "DIF-1", "primary": True},
        {"mrq_id": "MRQ-1", "stable_diff_id": "DIF-2", "primary": False},
    ]
    facts = {"DIF-1": {"content_fingerprint": "a"}, "DIF-2": {"content_fingerprint": "b"}}
    coverage = {"DIF-1": {"coverage_status": "still_required"}, "DIF-2": {"coverage_status": "changed_in_target"}}
    approvals = [{"target_id": "MRQ-1", "fingerprint": "approved"}]
    initial = compatibility_fingerprint(mrq, dispositions, facts, coverage, approvals)
    mrq["source_generation_id"] = "new"
    mrq["migration_decision"]["diff_generation_id"] = "new"
    assert compatibility_fingerprint(mrq, dispositions, facts, coverage, approvals) == initial
    facts["DIF-2"]["content_fingerprint"] = "changed"
    assert compatibility_fingerprint(mrq, dispositions, facts, coverage, approvals) != initial


@pytest.mark.parametrize("part", ["primary", "supporting", "source_evidence", "target_evidence", "coverage", "decision", "approval"])
def test_compatibility_fingerprint_rejects_every_semantic_closure_change(part: str):
    import copy
    mrq = {
        "mrq_id": "MRQ-1",
        "source_customization": {"evidence": [{"stable_diff_id": "DIF-1", "fingerprint": "source"}]},
        "migration_decision": {"decision": "adapt", "target_evidence": [{"fingerprint": "target"}]},
    }
    relations = [{"mrq_id": "MRQ-1", "stable_diff_id": "DIF-1", "primary": True}, {"mrq_id": "MRQ-1", "stable_diff_id": "DIF-2", "primary": False}]
    facts = {"DIF-1": {"content_fingerprint": "primary"}, "DIF-2": {"content_fingerprint": "supporting"}}
    coverage = {"DIF-1": {"coverage_status": "still_required"}, "DIF-2": {"coverage_status": "still_required"}}
    approvals = [{"target_id": "MRQ-1", "fingerprint": "approval"}]
    baseline = compatibility_fingerprint(mrq, relations, facts, coverage, approvals)
    changed = tuple(copy.deepcopy(value) for value in (mrq, relations, facts, coverage, approvals))
    changed_mrq, changed_relations, changed_facts, changed_coverage, changed_approvals = changed
    if part in {"primary", "supporting"}:
        changed_facts["DIF-1" if part == "primary" else "DIF-2"]["content_fingerprint"] = "changed"
    elif part == "source_evidence":
        changed_mrq["source_customization"]["evidence"][0]["fingerprint"] = "changed"
    elif part == "target_evidence":
        changed_mrq["migration_decision"]["target_evidence"][0]["fingerprint"] = "changed"
    elif part == "coverage":
        changed_coverage["DIF-1"]["coverage_status"] = "covered_by_vendor"
    elif part == "decision":
        changed_mrq["migration_decision"]["decision"] = "retain_custom"
    else:
        changed_approvals[0]["fingerprint"] = "changed"
    assert compatibility_fingerprint(changed_mrq, changed_relations, changed_facts, changed_coverage, changed_approvals) != baseline


def test_pointer_set_uses_complete_cas_and_lease_check(tmp_path: Path):
    repo = _repo(tmp_path)
    old = active_pointers(repo)
    new = {kind: {"kind": kind, "generation_id": f"new-{kind}"} for kind in POINTERS}
    published = publish_pointers(repo, old, new, lease_check=lambda: True, validate=lambda values: None)
    assert published == new
    assert active_pointers(repo) == new
    assert not (repo / "research" / INTENT).exists()
    with pytest.raises(RuntimeError, match="stale complete"):
        publish_pointers(repo, old, new, lease_check=lambda: True, validate=lambda values: None)


@pytest.mark.parametrize("phase,expected", [("prepared", "rolled_back"), ("committing", "committed")])
def test_publication_recovery_has_deterministic_side(phase: str, expected: str, tmp_path: Path):
    repo = _repo(tmp_path)
    old = active_pointers(repo)
    new = {kind: {"kind": kind, "generation_id": f"new-{kind}"} for kind in POINTERS}
    transaction = {"schema_version": "1", "phase": phase, "kinds": list(POINTERS), "old": old, "new": new, "old_fingerprint": fingerprint(old), "new_fingerprint": fingerprint(new)}
    (repo / "research" / INTENT).write_text(json.dumps(transaction), encoding="utf-8")
    assert recover_publication(repo, validate=lambda values: None) == expected
    assert active_pointers(repo) == (old if phase == "prepared" else new)


def test_publication_keeps_recoverable_prepared_intent_after_lease_loss(tmp_path: Path):
    repo = _repo(tmp_path)
    old = active_pointers(repo)
    checks = iter((True, False))
    with pytest.raises(RuntimeError, match="lease lost"):
        publish_pointers(
            repo,
            old,
            {"diff": {"kind": "diff", "generation_id": "new"}},
            lease_check=lambda: next(checks),
            validate=lambda values: None,
        )
    assert json.loads((repo / "research" / INTENT).read_text())["phase"] == "prepared"
    assert recover_publication(repo, validate=lambda values: None) == "rolled_back"


@pytest.mark.parametrize("crash_at,expected", [("prepared", "rolled_back"), ("committing", "committed"), ("source", "committed"), ("diff", "committed"), ("mrq", "committed"), ("delete_intent", "committed")])
def test_publication_recovers_each_crash_point(crash_at: str, expected: str, tmp_path: Path):
    repo = _repo(tmp_path)
    old = active_pointers(repo)
    new = {kind: {"kind": kind, "generation_id": f"new-{kind}"} for kind in POINTERS}

    def crash(point: str):
        if point == crash_at:
            raise OSError("crash")

    with pytest.raises(OSError, match="crash"):
        publish_pointers(repo, old, new, lease_check=lambda: True, validate=lambda values: None, before_write=crash)
    assert recover_publication(repo, validate=lambda values: None) == expected
    assert active_pointers(repo) == (old if expected == "rolled_back" else new)


def test_corrupted_committing_transaction_fails_closed(tmp_path: Path):
    repo = _repo(tmp_path)
    old = active_pointers(repo)
    new = {kind: {"kind": kind, "generation_id": f"new-{kind}"} for kind in POINTERS}
    transaction = {"schema_version": "1", "phase": "committing", "kinds": list(POINTERS), "old": old, "new": new, "old_fingerprint": fingerprint(old), "new_fingerprint": "corrupt"}
    (repo / "research" / INTENT).write_text(json.dumps(transaction), encoding="utf-8")
    with pytest.raises(RuntimeError, match="critical corrupted"):
        recover_publication(repo, validate=lambda values: None)


def test_first_pointer_reader_recovers_committing_transaction(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    old = active_pointers(repo)
    new = {kind: {"kind": kind, "generation_id": f"new-{kind}"} for kind in POINTERS}
    transaction = {"schema_version": "1", "phase": "committing", "kinds": list(POINTERS), "old": old, "new": new, "old_fingerprint": fingerprint(old), "new_fingerprint": fingerprint(new)}
    (repo / "research" / INTENT).write_text(json.dumps(transaction), encoding="utf-8")
    monkeypatch.setattr("one_c_autoresearch.stage_recompute._validate_pointer_set", lambda _repo, _values: None)
    assert active_pointers(repo) == new
    assert not (repo / "research" / INTENT).exists()


def test_pointer_publication_rejects_non_pointer_state_change(tmp_path: Path, monkeypatch):
    repo = _repo(tmp_path)
    old = active_pointers(repo)
    monkeypatch.setattr("one_c_autoresearch.stage_recompute.state_fingerprint", lambda _repo: "changed")
    with pytest.raises(RuntimeError, match="stale complete workflow"):
        publish_pointers(
            repo,
            old,
            {"diff": {"kind": "diff", "generation_id": "new"}},
            lease_check=lambda: True,
            validate=lambda values: None,
            expected_workflow_fingerprint="expected",
        )


def test_executor_checks_signed_plan_and_lease_between_steps():
    unsigned = {
        "schema_version": "1",
        "boundary": "projections",
        "workflow_fingerprint": "wf",
        "steps": [
            {"step_id": "1:one", "operation": "one", "input_fingerprint": "a"},
            {"step_id": "2:two", "operation": "two", "input_fingerprint": "b"},
        ],
    }
    plan = {**unsigned, "plan_fingerprint": fingerprint(unsigned)}
    calls = []

    def apply(operation, payload, expected, cancelled):
        calls.append((operation, expected))
        return {"operation": operation, "workflow_fingerprint": expected + operation}

    results = execute_plan(plan, apply, lease_check=lambda: True)
    assert calls == [("one", "wf"), ("two", "wfone")]
    assert len(results) == 2
    assert results[0]["next_input_fingerprint"] == "b"
    with pytest.raises(RuntimeError, match="stale recompute plan"):
        execute_plan({**plan, "boundary": "diffs"}, apply, lease_check=lambda: True)


def test_executor_stops_after_unchanged_diff_and_skips_empty_conditional_step():
    unsigned = {
        "schema_version": "1",
        "boundary": "sources",
        "workflow_fingerprint": "wf",
        "steps": [
            {"step_id": "1:diff", "operation": "diff.build", "input_fingerprint": "a"},
            {"step_id": "2:mrq", "operation": "dif.classification-reset", "input_fingerprint": "b"},
            {"step_id": "3:indexes", "operation": "indexes.build", "input_fingerprint": "c", "conditional": True},
        ],
    }
    plan = {**unsigned, "plan_fingerprint": fingerprint(unsigned)}
    calls = []

    def apply(operation, _payload, _expected, _cancelled):
        calls.append(operation)
        return {"status": "unchanged", "_stop": True}

    result = execute_plan(plan, apply, lease_check=lambda: True, payloads={"indexes.build": {"component_ids": []}})
    assert calls == ["diff.build"]
    assert [item["operation"] for item in result] == ["diff.build"]


def test_executor_fences_output_when_lease_is_lost_during_step():
    unsigned = {
        "schema_version": "1",
        "boundary": "projections",
        "workflow_fingerprint": "wf",
        "steps": [{"step_id": "1:build", "operation": "projections.build", "input_fingerprint": "a"}],
    }
    plan = {**unsigned, "plan_fingerprint": fingerprint(unsigned)}
    checks = iter((True, False))
    events = []
    with pytest.raises(RuntimeError, match="lease lost"):
        execute_plan(
            plan,
            lambda *_args: {"status": "completed"},
            lease_check=lambda: next(checks),
            emit=lambda kind, _value: events.append(kind),
        )
    assert events == ["step.started"]


def test_application_service_stages_real_writer_operations_until_pointer_transaction(tmp_path: Path, monkeypatch):
    from one_c_autoresearch.service import ApplicationService
    from one_c_autoresearch.user_state import workspace_id

    repo = _repo(tmp_path)
    old = active_pointers(repo)
    (repo / "research" / "active-generation.json").write_text(json.dumps({"canonical_generation_id": "old-mrq"}), encoding="utf-8")
    previews = tmp_path / "previews"
    previews.mkdir()
    (previews / "route.json").write_text(json.dumps({"project_id": workspace_id(repo), "status": "ready", "routing_plan_fingerprint": "route", "expires_at": "2099-01-01T00:00:00+00:00"}), encoding="utf-8")
    service = object.__new__(ApplicationService)
    service.repo = repo
    service.connections = {"role": {"platform_path": str(tmp_path)}}
    service.upload_drafts = None
    service.routing_previews = previews
    service.rlm_executable = None
    monkeypatch.setattr("one_c_autoresearch.workflow.state_fingerprint", lambda _repo: "wf")
    monkeypatch.setattr("one_c_autoresearch.stage_recompute.state_fingerprint", lambda _repo: "wf")
    monkeypatch.setattr("one_c_autoresearch.sources.routing_preview", lambda value: value)
    source = {"generation_id": "new-source"}
    diff = {"generation_id": "new-diff", "source_generation_id": "new-source"}
    monkeypatch.setattr("one_c_autoresearch.sources.acquire", lambda *args, activate, **kwargs: source if activate is False else pytest.fail("source activated early"))
    monkeypatch.setattr("one_c_autoresearch.diffs.build", lambda *args, activate, **kwargs: diff if activate is False else pytest.fail("diff activated early"))
    staged = {}
    preview_path = previews / "route.json"
    expired = json.loads(preview_path.read_text(encoding="utf-8"))
    expired["expires_at"] = "2000-01-01T00:00:00+00:00"
    preview_path.write_text(json.dumps(expired), encoding="utf-8")
    with pytest.raises(RuntimeError, match="routing_preview_stale"):
        service.apply("sources.acquire", {"source_routing_preview_id": "route", "routing_plan_fingerprint": "route"}, "wf", staged=staged)
    expired["expires_at"] = "2099-01-01T00:00:00+00:00"
    preview_path.write_text(json.dumps(expired), encoding="utf-8")
    service.apply("sources.acquire", {"source_routing_preview_id": "route", "routing_plan_fingerprint": "route"}, "wf", staged=staged)
    service.apply("diff.build", {}, "wf", staged=staged)
    from one_c_autoresearch.consolidation import sentinel
    staged["mrq"] = sentinel()
    assert active_pointers(repo) == old
    publish_pointers(repo, active_pointers(repo), staged, lease_check=lambda: True, validate=lambda values: None, expected_workflow_fingerprint="wf")
    assert active_pointers(repo) == {"source": source, "diff": diff, "mrq": sentinel()}


def test_resume_skips_only_validated_linked_prefix_and_never_unpublished_sources():
    steps = [
        {"step_id": "1:diff", "operation": "diff.build", "input_fingerprint": "in-diff"},
        {"step_id": "2:mrq", "operation": "dif.classification-reset", "input_fingerprint": "in-mrq"},
    ]
    plan = {"boundary": "diffs", "steps": steps}
    predecessor = {
        "boundary": "diffs",
        "status": "failed",
        "steps": [{**steps[0], "output_fingerprint": "out-diff", "next_input_fingerprint": "in-mrq"}],
    }
    assert prepare_resume(plan, predecessor, {"diff.build": "out-diff"}) == [steps[0]]
    with pytest.raises(RuntimeError, match="next step input"):
        prepare_resume(plan, {**predecessor, "steps": [{**predecessor["steps"][0], "next_input_fingerprint": "other"}]}, {"diff.build": "out-diff"})
    sources = {"boundary": "sources", "steps": [{"operation": "sources.acquire", "input_fingerprint": "in"}]}
    assert prepare_resume(sources, {"boundary": "sources", "status": "failed", "steps": [{"operation": "sources.acquire", "input_fingerprint": "in", "output_fingerprint": "candidate"}]}, {"sources.acquire": "candidate"}) == []
