import shutil
from pathlib import Path

import pytest

from one_c_autoresearch.workflow import AGENT_PHASE_CATALOG, OPERATION_CATALOG, _agent_circuit_state, next_work, preview_step_patch, projection_value, semantic_diff_context, status, validate_project_contract, validate_workflow, workflow_fingerprint
from one_c_autoresearch.mrq import active


REPO = Path(__file__).parents[1] / "templates/research-repo"
HAS_ACTIVE_GENERATION = (REPO / "research/active-generation.json").is_file()


def test_active_agent_job_overrides_ready_gate_state():
    gate = {"state": "ready"}
    assert _agent_circuit_state("analyze-dif", {"analyze-dif"}, gate) == "active"
    assert _agent_circuit_state("analyze-dif", set(), gate) == "ready"


def test_fixed_graph_and_next_unit_matches_first_ready_gate():
    manifest = validate_workflow(REPO)
    assert manifest["schema_version"] == "4"
    assert manifest["tool_version"] == "0.3.0"
    assert len(manifest["gates"]) == 8
    assert len(manifest["jobs"]) == 9
    assert len(OPERATION_CATALOG) == 10
    assert [job["id"] for job in manifest["jobs"]][-5:] == ["analyze-dif", "consolidate-mrq", "classify-mrq", "decide-mrq", "publish"]
    snapshot = status(REPO)
    assert len(snapshot["gates"]) == 8
    ready = [gate for gate in snapshot["gates"] if gate["state"] == "ready"]
    assert next_work(REPO) is None if not ready else next_work(REPO)["action"] == ready[0]["blockers"][0]["action"]


def test_shallow_status_does_not_hash_source_payloads(monkeypatch):
    monkeypatch.setattr("one_c_autoresearch.sources.file_manifest", lambda _path: (_ for _ in ()).throw(AssertionError("payload was hashed")))
    assert status(REPO, deep=False)["workflow_fingerprint"].startswith("sha256:")


def test_graph_edits_fail_closed(tmp_path: Path):
    (tmp_path / "research").mkdir()
    text = (REPO / "research/workflow.toml").read_text(encoding="utf-8")
    (tmp_path / "research/workflow.toml").write_text(text.replace('id = "configure"', 'id = "shell"', 1), encoding="utf-8")
    with pytest.raises(ValueError, match="fixed"):
        validate_workflow(tmp_path)
    (tmp_path / "research/workflow.toml").write_text(text.replace('needs = ["configure"]', 'needs = ["configure"]\ndisabled = true', 1), encoding="utf-8")
    with pytest.raises(ValueError, match="fixed jobs"):
        validate_workflow(tmp_path)
    (tmp_path / "research/workflow.toml").write_text(text.replace('tool_version = "0.3.0"', 'tool_version = "0.3.0"\nremote_action = true'), encoding="utf-8")
    with pytest.raises(ValueError, match="top-level"):
        validate_workflow(tmp_path)
    unsafe = text.replace('operation_version = "1", timeout_seconds = 1800 }]', 'operation_version = "2", timeout_seconds = 1800, shell = "true", executable_path = "/bin/sh" }]', 1)
    (tmp_path / "research/workflow.toml").write_text(unsafe, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        validate_workflow(tmp_path)
    assert all(value in str(caught.value) for value in ("shell", "executable_path", "requires operation version 1"))


def test_unknown_or_research_owned_project_authority_is_rejected(tmp_path: Path):
    project = tmp_path / "project.toml"
    project.write_text('[project]\nid="p"\nproduct="p"\nbaseline_version="1"\ntarget_version="1"\nnext_vendor_version="2"\n[unknown]\nvalue=true\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"unknown.*\[unknown\]"):
        validate_project_contract(tmp_path)
    project.write_text('[project]\nid="p"\nproduct="p"\nbaseline_version="1"\ntarget_version="1"\nnext_vendor_version="2"\nacquisition_profile="forbidden"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="acquisition_profile.*research/infobases.toml"):
        validate_project_contract(tmp_path)


def test_later_gates_are_blocked_by_predecessor():
    snapshot = status(REPO)
    incomplete = [index for index, gate in enumerate(snapshot["gates"]) if gate["state"] != "complete"]
    assert not incomplete or all(gate["state"] == "blocked" for gate in snapshot["gates"][incomplete[0] + 1:])


def test_exact_operation_catalog_and_typed_patch_preview():
    assert set(OPERATION_CATALOG) == {operation for _job, _needs, steps in __import__("one_c_autoresearch.workflow", fromlist=["JOBS"]).JOBS for _step, operation, _version in steps}
    assert all({"version", "executor", "effect", "parameters", "paths", "artifacts", "validator", "retryable", "approval_required"} <= set(item) for item in OPERATION_CATALOG.values())
    assert OPERATION_CATALOG["indexes.build"]["fixed_inputs"] == {"mode": "ensure", "selector": "all"}
    assert OPERATION_CATALOG["indexes.build"]["executor"] == "source-index-adapters"
    assert OPERATION_CATALOG["indexes.build"]["paths"] == []
    assert OPERATION_CATALOG["indexes.build"]["run_inputs"]["mode"] == ["ensure", "validate", "rebuild"]
    preview = preview_step_patch(REPO, "build-diffs", {"timeout_seconds": 60, "max_retries": 1}, workflow_fingerprint(REPO))
    assert preview["before"]["timeout_seconds"] == 1800
    assert preview["after"]["timeout_seconds"] == 60
    assert preview["catalog"]["effect"] == "write"
    with pytest.raises(ValueError, match="unsupported"):
        preview_step_patch(REPO, "build-diffs", {"shell": "true"}, workflow_fingerprint(REPO))
    phases = preview_step_patch(REPO, "analyze-dif", {}, workflow_fingerprint(REPO))["after"]["agent_phases"]
    consolidate = preview_step_patch(REPO, "consolidate-mrq", {}, workflow_fingerprint(REPO))["after"]["agent_phases"]
    assert [item["phase_id"] for item in phases] == ["analyze-dif"]
    assert [item["phase_id"] for item in consolidate] == ["form-mrq"]
    assert AGENT_PHASE_CATALOG["mrq.consolidate"][0]["roles"] == ("coordinator", "grouper")
    classifier = preview_step_patch(REPO, "classify-mrq", {}, workflow_fingerprint(REPO))["after"]["agent_phases"]
    assert classifier == [{"phase_id": "classify-batches", "mode": "sequential", "max_concurrency": 1, "roles": [{"role_id": "classifier", "agent_profile": "local", "count": 1, "instruction_supplement": ""}]}]
    with pytest.raises(ValueError, match="phase"):
        preview_step_patch(REPO, "analyze-dif", {"agent_phases": []}, workflow_fingerprint(REPO))


def test_empty_analyze_window_has_no_agent_call(monkeypatch):
    monkeypatch.setattr("one_c_autoresearch.dif_classifications.coverage", lambda _repo: {"remaining": 0})
    preview = preview_step_patch(
        REPO,
        "analyze-dif",
        {},
        workflow_fingerprint(REPO),
    )
    phases = {item["phase_id"]: item for item in preview["agent_phase_preview"]}
    assert phases["analyze-dif"]["effective_max_concurrency"] == 0


def test_legacy_agent_step_and_unsafe_phase_values_are_rejected(tmp_path: Path):
    (tmp_path / "research").mkdir()
    current = (REPO / "research/workflow.toml").read_text(encoding="utf-8")
    legacy = current.replace('operation_version = "2", timeout_seconds = 1800, agent_phases =', 'operation_version = "1", timeout_seconds = 1800, agent_profile = "local", legacy_phases =', 1)
    (tmp_path / "research/workflow.toml").write_text(legacy, encoding="utf-8")
    with pytest.raises(ValueError, match="agent_profile|legacy_phases|version"):
        validate_workflow(tmp_path)
    too_large = current.replace("max_concurrency = 4", "max_concurrency = 9007199254740992", 1)
    (tmp_path / "research/workflow.toml").write_text(too_large, encoding="utf-8")
    with pytest.raises(ValueError, match="safe integer"):
        validate_workflow(tmp_path)


@pytest.mark.skipif(not HAS_ACTIVE_GENERATION or not (REPO / "outputs/projections.json").is_file(), reason="clean template has no generated outputs")
def test_repository_truth_survives_user_state_deletion(tmp_path: Path):
    before = status(REPO), active(REPO), (REPO / "outputs/projections.json").read_bytes()
    operational = tmp_path / "user-state"
    (operational / "indexes").mkdir(parents=True)
    (operational / "indexes/state.json").write_text('{"status":"ready"}', encoding="utf-8")
    (operational / "events.jsonl").write_text("{}\n", encoding="utf-8")
    shutil.rmtree(operational)
    after = status(REPO), active(REPO), (REPO / "outputs/projections.json").read_bytes()
    assert after == before


@pytest.mark.skipif(not HAS_ACTIVE_GENERATION, reason="clean template has no active generation")
def test_all_projections_are_deterministic_and_fingerprinted_from_mrq():
    state = active(REPO)
    first = projection_value(state)
    assert first == projection_value(state)
    assert set(first["views"]) == {"subject_cards", "functional_gaps", "dashboard", "customer_register", "specifications"}
    changed = {**state, "mrq.jsonl": [*state["mrq.jsonl"], {"mrq_id": "MRQ-TEST"}]}
    assert projection_value(changed)["input_fingerprint"] != first["input_fingerprint"]


def test_large_registry_produces_one_bounded_agent_work_unit(tmp_path: Path, monkeypatch):
    import csv
    source, diff = "a" * 64, "b" * 64
    (tmp_path / "research").mkdir(); (tmp_path / "research/active-source-generation.json").write_text('{"generation_id":"' + source + '"}', encoding="utf-8"); (tmp_path / "research/active-diff-generation.json").write_text('{"generation_id":"' + diff + '","source_generation_id":"' + source + '"}', encoding="utf-8")
    coverage = tmp_path / "analysis/indexes/generations" / diff / "target-coverage.csv"; coverage.parent.mkdir(parents=True)
    fields = ("customer_diff_id", "source_generation", "target_diff_ids", "coverage_status", "evidence_ref", "notes")
    with coverage.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for index in range(2000): writer.writerow({"customer_diff_id": f"DIF-{index:016X}", "source_generation": source, "target_diff_ids": "", "coverage_status": "still_required", "evidence_ref": "{}", "notes": ""})
    diffs = [{"stable_diff_id": f"DIF-{index:016X}", "path": f"configuration/{index}.bsl", "before_role": "vendor_baseline", "after_role": "target_cf"} for index in range(2000)]
    monkeypatch.setattr("one_c_autoresearch.workflow.status", lambda _repo: {"workflow_fingerprint": "sha256:x", "gates": [{"id": "diffs-classified", "state": "ready", "blockers": [{"action": "mrq.discover-next", "code": "missing", "message": "missing"}]}]})
    monkeypatch.setattr("one_c_autoresearch.workflow._active_rows", lambda _repo: (diffs, [], [], []))
    work = next_work(tmp_path)
    assert work["work_unit"]["id"] == "DIF-0000000000000000"
    assert "registry" not in work["work_unit"] and len(work["work_unit"]["allowed_paths"]) == 1


def test_semantic_extension_context_is_complete_and_raw_audit_ids_are_rejected(tmp_path: Path):
    import csv
    import json
    generation = "b" * 64
    (tmp_path / "research").mkdir()
    (tmp_path / "research/active-diff-generation.json").write_text(json.dumps({"schema_version": "2", "generation_id": generation}), encoding="utf-8")
    root = tmp_path / "analysis/indexes/generations" / generation
    root.mkdir(parents=True)
    fields = ("stable_diff_id", "object_kind", "path")
    with (root / "diff-inventory.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        writer.writerow({"stable_diff_id": "DIF-AAAAAAAAAAAAAAAA", "object_kind": "extension_intervention", "path": "extensions/x/first.xml"})
    detail = {
        "stable_diff_id": "DIF-AAAAAAAAAAAAAAAA", "extension_uuid": "x", "intervention_kind": "form_change",
        "object_scope": "adopted", "affected_base_identity": "catalog.invoice",
        "evidence": [{"role": "target_cf", "side": "after", "path": "forms/a.xml", "fingerprint": "sha256:" + "a" * 64}, {"role": "vendor_baseline", "side": "before", "path": "forms/b.xml", "fingerprint": "sha256:" + "b" * 64}],
        "dependency_ids": ["DEP-1"], "diagnostic_codes": ["changed_in_target"],
    }
    dependency = {"dependency_id": "DEP-1", "stable_diff_id": detail["stable_diff_id"], "outcome": "resolved"}
    (root / "extension-diff.jsonl").write_text(json.dumps(detail) + "\n", encoding="utf-8")
    (root / "extension-dependencies.jsonl").write_text(json.dumps(dependency) + "\n", encoding="utf-8")
    with (root / "target-coverage.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("customer_diff_id", "coverage_status")); writer.writeheader()
        writer.writerow({"customer_diff_id": detail["stable_diff_id"], "coverage_status": "needs_semantic_review"})
    with (root / "extension-physical-diff.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        writer.writerow({"stable_diff_id": "DIF-BBBBBBBBBBBBBBBB", "object_kind": "file", "path": "extensions/x/raw.xml"})
    context = semantic_diff_context(tmp_path, detail["stable_diff_id"])
    assert context["allowed_paths"] == ["target_cf/extensions/x/forms/a.xml", "vendor_baseline/extensions/x/forms/b.xml"]
    assert context["dependencies"] == [dependency]
    assert context["target_coverage"]["coverage_status"] == "needs_semantic_review"
    assert context["compatibility_summary"]["dependency_outcomes"] == ["resolved"]
    with pytest.raises(ValueError, match="raw extension audit DIF"):
        semantic_diff_context(tmp_path, "DIF-BBBBBBBBBBBBBBBB")
