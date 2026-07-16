from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

import one_c_autoresearch.parallel_research as parallel_research
from one_c_autoresearch.common import read_jsonl
from one_c_autoresearch.parallel_research import (
    APPLY_LOCK,
    COORDINATOR_LOCK,
    DEFAULT_MODEL,
    LEDGER,
    ParallelResearchError,
    ProcessLock,
    RUNS,
    TRACE_ROOT,
    _file_sha,
    _fake_decision,
    _apply_cus,
    _codex_command,
    _codex_failure_reason,
    _process_start,
    _prepare_worker_workspace,
    _read_json,
    _set_state,
    _state_path,
    _track_worker,
    _vendor_drift_candidate,
    _verify_fingerprints,
    allocate_canonical_id,
    apply_ready,
    cleanup_traces,
    compact_run,
    coordinator_signal_guard,
    decision_schema,
    plan_run,
    inspect_run,
    run_coordinator,
    run_unit,
    stable_unit_id,
    summarize_trace,
    task_context,
    terminate_active_workers,
    terminate_process_group,
    validate_decision,
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    write(path, "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows))


def seed_review(root: Path, count: int = 3) -> list[dict]:
    write(root / "project.toml", "[project]\nid='fixture'\n")
    tasks = []
    for index in range(count):
        tasks.append({"id": f"Q-{index}", "type": "review", "status": "pending", "title": f"Review {index}", "feature_id": f"F-{index}", "source_artifacts": ["project.toml"], "expected_outputs": [f"analysis/features/F-{index}/review.md"], "quality_gates": ["source_lines"]})
    tasks.append({"id": "Q-X", "type": "manual_markup", "status": "pending", "title": "Unsupported"})
    write_jsonl(root / "analysis/queue/tasks.jsonl", tasks)
    write_jsonl(root / "analysis/parallel-research/apply-ledger.jsonl", [])
    write_jsonl(root / "analysis/parallel-research/cus-proposals.jsonl", [])
    return tasks


def seed_cus(root: Path, adapter: str) -> None:
    write(root / "project.toml", "[project]\nid='fixture'\n")
    task = {"id": "Q-CUS", "type": "review", "status": "pending", "title": "CUS", "feature_id": "MRQ", "parallel_adapter": adapter, "parallel_entities": ["CUS-1"], "source_artifacts": ["project.toml"]}
    write_jsonl(root / "analysis/queue/tasks.jsonl", [task])
    write_jsonl(root / "analysis/customization-registry/customization-items.jsonl", [{"customization_id": "CUS-1", "title": "CUS", "scope_status": "included", "status": "ready_for_review"}])
    write_jsonl(root / "analysis/customization-registry/customization-evidence.jsonl", [{"customization_id": "CUS-1", "evidence_type": "source", "source_path": "project.toml"}])
    write_jsonl(root / "analysis/customization-registry/customization-links.jsonl", [])
    write_jsonl(root / "analysis/migration-requirements/requirements.jsonl", [{"requirement_id": "MRQ-1", "title": "MRQ", "status": "draft", "target_solution": "adapt"}])
    write_jsonl(root / "analysis/migration-requirements/requirement-links.jsonl", [])
    write_jsonl(root / "analysis/parallel-research/apply-ledger.jsonl", [])
    write_jsonl(root / "analysis/parallel-research/cus-proposals.jsonl", [])


def test_plan_is_deterministic_disjoint_and_preserves_unsupported() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root)
        first = plan_run(root, limit=3)
        second = plan_run(root, limit=3)
        assert first["run_id"] == second["run_id"]
        assert len(first["units"]) == 3
        assert len({entity for unit in first["units"] for entity in unit["entity_ids"]}) == 3
        assert first["unsupported"] == [{"task_id": "Q-X", "reason": "unsupported_adapter"}]
        assert not first["truncated"]
        assert stable_unit_id("Q", "review_evidence", ["E"], {"x": "y"}) == stable_unit_id("Q", "review_evidence", ["E"], {"x": "y"})
        complete = plan_run(root)
        assert complete["unsupported"] == [{"task_id": "Q-X", "reason": "unsupported_adapter"}]


def test_plan_rejects_expected_output_overlap() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); tasks = seed_review(root, 2)
        tasks[1]["expected_outputs"] = tasks[0]["expected_outputs"]
        write_jsonl(root / "analysis/queue/tasks.jsonl", tasks)
        with pytest.raises(ParallelResearchError, match="output overlap"):
            plan_run(root)


def test_truncated_diagnostic_plan_cannot_be_applied() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 4)
        plan = plan_run(root, limit=3, write=True)
        assert plan["truncated"]
        run_coordinator(root, plan["run_id"], workers=3, fake=True)
        unit = plan["units"][0]
        schema = _read_json(root / RUNS / plan["run_id"] / "schemas" / f"{unit['unit_id']}.json")
        assert schema["properties"]["unit_id"]["const"] == unit["unit_id"]
        with pytest.raises(ParallelResearchError, match="truncated diagnostic run"):
            apply_ready(root, plan["run_id"])


def test_context_is_bounded_and_rejects_escape() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1)
        plan = plan_run(root, write=True)
        unit = plan["units"][0]
        context = task_context(root, plan["run_id"], unit["unit_id"])
        assert context["policy"] == {"read_only": True, "mcp_allowed": False, "web_allowed": False, "static_sources_first": True}
        manifest_path = root / RUNS / plan["run_id"] / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["units"][0]["allowed_sources"] = ["../outside"]
        manifest_path.write_text(json.dumps(manifest))
        with pytest.raises(ParallelResearchError, match="integrity check"):
            task_context(root, plan["run_id"], unit["unit_id"])


def test_plan_rejects_source_directory_containing_global_registry() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); tasks = seed_review(root, 1)
        tasks[0]["source_artifacts"] = ["analysis"]
        write_jsonl(root / "analysis/queue/tasks.jsonl", tasks)
        with pytest.raises(ParallelResearchError, match="forbidden global registry"):
            plan_run(root)


def test_coordinator_loads_shared_cus_indexes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        task = dict(read_jsonl(root / "analysis/queue/tasks.jsonl")[0][1])
        task["parallel_entities"] = ["CUS-1", "CUS-2", "CUS-3"]
        write_jsonl(root / "analysis/queue/tasks.jsonl", [task])
        write_jsonl(root / "analysis/customization-registry/customization-items.jsonl", [
            {"customization_id": f"CUS-{index}", "title": f"CUS {index}", "scope_status": "included", "status": "ready_for_review"}
            for index in range(1, 4)
        ])
        write_jsonl(root / "analysis/customization-registry/customization-evidence.jsonl", [
            {"customization_id": f"CUS-{index}", "evidence_type": "source", "source_path": "project.toml"}
            for index in range(1, 4)
        ])
        plan = plan_run(root, unit_size=1, write=True)
        original_registry = parallel_research.load_registry_index
        original_mrq = parallel_research.load_mrq_index
        calls = {"registry": 0, "mrq": 0}

        def registry(root: Path) -> dict:
            calls["registry"] += 1
            return original_registry(root)

        def mrq(root: Path) -> dict:
            calls["mrq"] += 1
            return original_mrq(root)

        monkeypatch.setattr(parallel_research, "load_registry_index", registry)
        monkeypatch.setattr(parallel_research, "load_mrq_index", mrq)
        run_coordinator(root, plan["run_id"], workers=3, fake=True)
        assert calls == {"registry": 1, "mrq": 1}


def test_plan_excludes_false_positive_cus_from_backlog() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        items = read_jsonl(root / "analysis/customization-registry/customization-items.jsonl")
        items[0][1]["scope_status"] = "excluded_false_positive"
        write_jsonl(root / "analysis/customization-registry/customization-items.jsonl", [row for _, row in items])
        plan = plan_run(root)
        assert plan["units"] == []


def test_phase1_conflicts_ignore_draft_bp30_decisions_but_detect_multiple_owners() -> None:
    links = [
        {"requirement_id": "MRQ-1", "customization_id": "CUS-1", "role": "primary", "effort_owner": True},
        {"requirement_id": "MRQ-2", "customization_id": "CUS-1", "role": "shared", "effort_owner": False},
    ]
    assert parallel_research._phase1_conflicting_cus(links) == []
    links[1].update(role="primary", effort_owner=True)
    assert parallel_research._phase1_conflicting_cus(links) == ["CUS-1"]


def test_phase1_completion_ignores_phase2_warnings() -> None:
    coverage = {"status": "fail", "errors": [], "warnings": ["Conflicting target decisions"]}
    assert parallel_research._phase1_goal_complete(coverage, {"uncovered": 0, "conflicts": 0})


def test_plan_retries_follow_up_after_evidence_or_runner_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        unit_id = "PRU-FOLLOW-UP"
        write_jsonl(root / "analysis/parallel-research/cus-proposals.jsonl", [{
            "unit_id": unit_id, "customization_id": "CUS-1", "adapter": "uncovered_cus",
            "proposal": {"mutation_type": "follow_up"},
        }])
        write(root / "analysis/parallel-research/runs/R/manifest.json", json.dumps({"units": [{
            "unit_id": unit_id, "runner_version": parallel_research.RUNNER_VERSION, "source_fingerprints": {
                "project.toml": parallel_research._path_sha(root / "project.toml"),
                parallel_research.MRQ_BACKLOG: parallel_research._path_sha(root / parallel_research.MRQ_BACKLOG),
            },
        }]}))
        assert plan_run(root)["units"] == []
        current_version = parallel_research.RUNNER_VERSION
        monkeypatch.setattr(parallel_research, "RUNNER_VERSION", "next")
        assert len(plan_run(root)["units"]) == 1
        monkeypatch.setattr(parallel_research, "RUNNER_VERSION", current_version)
        write(root / "project.toml", (root / "project.toml").read_text() + "\n# changed\n")
        assert len(plan_run(root)["units"]) == 1


def test_source_comparison_falls_back_to_materialized_v8unpack_git_pair() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        write(root / "project.toml", "[paths]\nvendor_baseline='vendor'\ntarget_cf='customer'\n[parallel_research]\nvendor_drift_markers=['/Reports/RegulatedReport']\n")
        repo = root / parallel_research.V8UNPACK_REPO
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "v8unpack-refinement"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        source = repo / "CommonForm/Demo/CommonForm.json"
        write(source, "{\"name\":\"Demo\"}\n")
        write(repo / "Document/Demo/Template/Demo/Template.mxl", "template\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "vendor"], cwd=repo, check=True)
        subprocess.run(["git", "tag", "vendor-baseline"], cwd=repo, check=True)
        write(repo / "Document/Demo/Document.obj.bsl", 'ПолучитьМакет("Demo")\n')
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "customer"], cwd=repo, check=True)
        pairs = parallel_research._configuration_source_pairs(root, {
            "metadata_object": "CommonForm.Demo", "part_kind": "form", "part_name": "Demo", "part_path": "Forms/Demo",
        })
        assert len(pairs) == 1
        assert (root / pairs[0]["customer"]).read_text() == (root / pairs[0]["vendor"]).read_text()
        assert parallel_research._v8unpack_usage_files(str(repo), "Demo") == ("Document/Demo/Document.obj.bsl",)
        write(root / "customer/Documents/Demo/Templates/Demo/Ext/Template.xml", "<Template/>\n")
        template_pairs = parallel_research._configuration_source_pairs(root, {
            "metadata_object": "Template.Demo", "source_path": "customer/Documents/Demo/Templates/Demo/Ext/Template.xml",
        })
        assert any(pair.get("customer", "").endswith("Template.mxl") and pair.get("vendor", "").endswith("Template.mxl") for pair in template_pairs)


def test_standard_attributes_only_xml_difference_is_technical_noise() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        write(root / "project.toml", "[paths]\nvendor_baseline='vendor'\ntarget_cf='customer'\n")
        relative = "Enums/Demo.xml"
        write(root / "vendor" / relative, "<Meta><Properties><Name>Demo</Name></Properties></Meta>\n")
        write(root / "customer" / relative, "<Meta><Properties><Name>Demo</Name><StandardAttributes><Item name='Ref'/></StandardAttributes></Properties></Meta>\n")
        write_jsonl(root / "analysis/customization-registry/customization-evidence.jsonl", [{
            "customization_id": "CUS-1", "item_id": "CMI-1", "metadata_object": "Enum.Demo",
            "part_kind": "object", "part_path": relative, "change_type": "modified",
        }])
        plan = plan_run(root, write=True); unit = plan["units"][0]
        entity = task_context(root, plan["run_id"], unit["unit_id"])["entities"][0]
        assert entity["comparison_assessment"] == "no_semantic_diff"
        assert entity["source_comparisons"][0]["comparison_status"] == "technical_noise"


def test_diff_only_cus_maps_predefined_binary_to_designer_xml() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        write(root / "project.toml", "[paths]\nvendor_baseline='vendor'\ntarget_cf='customer'\n")
        items = [row for _, row in read_jsonl(root / "analysis/customization-registry/customization-items.jsonl")]
        items[0]["source_bp20_objects"] = ["ChartOfCharacteristicType.Demo"]
        write_jsonl(root / "analysis/customization-registry/customization-items.jsonl", items)
        write_jsonl(root / "analysis/customization-registry/customization-evidence.jsonl", [{
            "customization_id": "CUS-1", "source_path": "ChartOfCharacteristicType/Demo/Предустановленные данные.bin",
        }])
        relative = "ChartsOfCharacteristicTypes/Demo/Ext/Predefined.xml"
        write(root / "vendor" / relative, "<Items/>\n"); write(root / "customer" / relative, "<Items><Item/></Items>\n")
        plan = plan_run(root, write=True); unit = plan["units"][0]
        entity = task_context(root, plan["run_id"], unit["unit_id"])["entities"][0]
        assert entity["source_comparisons"][0]["comparison_status"] == "changed"
        assert {"vendor/" + relative, "customer/" + relative} <= set(unit["allowed_sources"])


def test_changed_v8_json_is_noise_when_designer_xml_is_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for path, content in (("c.json", "customer"), ("v.json", "vendor"), ("c.xml", "<Meta/>"), ("v.xml", "<Meta/>")):
            write(root / path, content)
        pairs = iter([{"customer": "c.json", "vendor": "v.json"}, {"customer": "c.xml", "vendor": "v.xml"}])
        monkeypatch.setattr(parallel_research, "_configuration_source_pairs", lambda root, evidence: [next(pairs)])
        rows = parallel_research._source_comparisons(root, [
            {"metadata_object": "CommonModule.Demo"}, {"metadata_object": "CommonModule.Demo"},
        ])
        assert [row["comparison_status"] for row in rows] == ["technical_noise", "identical"]


def test_reordered_attributes_with_shifted_uuids_are_xml_noise() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        vendor = root / "vendor.xml"; customer = root / "customer.xml"
        write(vendor, "<Meta><Attributes><Attribute uuid='1'><Properties><Name>B</Name></Properties></Attribute><Attribute uuid='2'><Properties><Name>A</Name></Properties></Attribute></Attributes></Meta>")
        write(customer, "<Meta><Attributes><Attribute uuid='3'><Properties><Name>A</Name></Properties></Attribute><Attribute uuid='4'><Properties><Name>B</Name></Properties></Attribute></Attributes></Meta>")
        assert parallel_research._technical_xml_serialization_difference(customer, vendor)


def test_cus_context_resolves_v8unpack_relative_evidence() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        evidence = root / "analysis/customization-registry/customization-evidence.jsonl"
        write_jsonl(evidence, [{"customization_id": "CUS-1", "evidence_type": "source", "source_path": "Catalog/X/Object.json"}])
        write(root / "analysis/cache/noise/publishable-clean-v8unpack/repo/Catalog/X/Object.json", "{}\n")
        plan = plan_run(root, write=True)
        context = task_context(root, plan["run_id"], plan["units"][0]["unit_id"])
        assert "analysis/cache/noise/publishable-clean-v8unpack/repo/Catalog/X/Object.json" in context["allowed_sources"]


def test_cus_context_compares_vendor_and_customer_sources_and_gates_proposal() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        write(root / "project.toml", "[paths]\nvendor_baseline='vendor'\ntarget_cf='customer'\n")
        relative = "CommonPictures/Demo/Ext/Picture.xml"
        write(root / "vendor" / relative, "same\n")
        write(root / "customer" / relative, "same\n")
        write_jsonl(root / "analysis/customization-registry/customization-evidence.jsonl", [{
            "customization_id": "CUS-1", "item_id": "CMI-1", "metadata_object": "CommonPicture.Demo",
            "part_kind": "unknown", "part_name": "Picture", "part_path": relative, "change_type": "unknown",
        }])
        plan = plan_run(root, write=True); unit = plan["units"][0]
        context = task_context(root, plan["run_id"], unit["unit_id"])
        entity = context["entities"][0]
        assert entity["comparison_assessment"] == "no_semantic_diff"
        assert entity["source_comparisons"][0]["comparison_status"] == "identical"
        assert {"vendor/" + relative, "customer/" + relative} <= set(unit["allowed_sources"])
        assert {"vendor/" + relative, "customer/" + relative} <= set(unit["source_fingerprints"])

        decision = _fake_decision(context)
        row = decision["entities"][0]
        row["outcome"] = "confirmed"
        row["evidence"] = [
            {"path": "vendor/" + relative, "line_start": 1, "line_end": 1, "summary": "vendor"},
            {"path": "customer/" + relative, "line_start": 1, "line_end": 1, "summary": "customer"},
        ]
        row["proposal"].update({
            "mutation_type": "new_requirement_proposal", "semantic_key": "demo", "title": "Demo",
            "source_scenario": "Demo", "migration_boundary": "Demo", "target_solution": "adapt",
            "acceptance_criteria": "Demo", "risk": "Demo", "specification_text": "Demo",
        })
        errors = validate_decision(root, plan["run_id"], unit["unit_id"], decision, context)
        assert any("lacks comparative semantic evidence" in error for error in errors)

        row["confidence"] = "high"
        row["proposal"]["mutation_type"] = "exclude_false_customization"
        row["proposal"]["rationale"] = "Типовая и клиентская части идентичны."
        exclusion_errors = validate_decision(root, plan["run_id"], unit["unit_id"], decision, context)
        assert not exclusion_errors

        write(root / "customer" / relative, "changed\n")
        changed_plan = plan_run(root, write=True); changed_unit = changed_plan["units"][0]
        changed_context = task_context(root, changed_plan["run_id"], changed_unit["unit_id"])
        assert changed_context["entities"][0]["comparison_assessment"] == "semantic_candidate"
        changed_decision = _fake_decision(changed_context)
        changed_row = changed_decision["entities"][0]
        changed_row.update({"outcome": "confirmed", "confidence": "high", "evidence": row["evidence"]})
        changed_row["proposal"].update(row["proposal"])
        changed_row["proposal"]["mutation_type"] = "new_requirement_proposal"
        changed_row["proposal"]["target_solution"] = None
        changed_errors = validate_decision(root, changed_plan["run_id"], changed_unit["unit_id"], changed_decision, changed_context)
        assert not any("migration proposal" in error or "source pair" in error for error in changed_errors)
        assert not any("lacks fields" in error for error in changed_errors)

        changed_row["proposal"]["mutation_type"] = "exclude_false_customization"
        changed_errors = validate_decision(root, changed_plan["run_id"], changed_unit["unit_id"], changed_decision, changed_context)
        assert any("lacks identical or technical evidence" in error for error in changed_errors)


def test_worker_prompt_keeps_target_release_out_of_phase_one() -> None:
    prompt = parallel_research._worker_prompt({"run_id": "R", "unit_id": "U"})
    assert "Не проверяй целевой релиз" in prompt
    assert "exclude_false_customization" in prompt


def test_retry_prompt_targets_previous_validation_errors() -> None:
    prompt = parallel_research._retry_prompt("base", ["invalid requirement relation role: None"])
    assert prompt.startswith("base")
    assert "invalid requirement relation role: None" in prompt
    assert "migration_boundary" in prompt
    assert "exclude_false_customization" in prompt


def test_false_customization_is_removed_from_migration_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parallel_research, "validate_registry", lambda root: {"status": "ok"})
    monkeypatch.setattr(parallel_research, "validate_mrq_graph", lambda root, requirements, links: {"status": "ok"})
    monkeypatch.setattr(parallel_research, "write_cus_summary", lambda *args: None)
    monkeypatch.setattr(parallel_research, "export_registry", lambda root: {"status": "ok"})
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        decision = {"entities": [{
            "entity_id": "CUS-1", "conclusion": "Исходники идентичны.",
            "proposal": {"mutation_type": "exclude_false_customization", "rationale": "Нет смыслового изменения."},
        }]}
        links = [{"requirement_id": "MRQ-1", "customization_id": "CUS-1", "role": "primary"}]
        affected, changed, _, result_links = _apply_cus(
            root, {"unit_id": "U", "task_id": "Q-CUS", "adapter": "uncovered_cus"},
            decision, [{"requirement_id": "MRQ-1"}], links,
        )
        item = [row for _, row in read_jsonl(root / "analysis/customization-registry/customization-items.jsonl")][0]
        assert changed and "analysis/customization-registry" in affected
        assert item["scope_status"] == "excluded_false_positive"
        assert item["status"] == "technical_noise_removed"
        assert item["migration_decision"] == "drop"
        assert result_links == []


def test_vendor_drift_candidate_cannot_create_migration_proposal() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        write(root / "project.toml", "[paths]\nvendor_baseline='vendor'\ntarget_cf='customer'\n[parallel_research]\nvendor_drift_markers=['/Reports/RegulatedReport']\n")
        relative = "Reports/RegulatedReport/Templates/Form2026/Ext/Template.xml"
        write(root / "customer" / relative, "custom-only\n")
        write_jsonl(root / "analysis/customization-registry/customization-evidence.jsonl", [{
            "customization_id": "CUS-1", "item_id": "CMI-1", "metadata_object": "Template.Форма2026",
            "part_kind": "unknown", "part_name": "Template", "part_path": relative, "change_type": "added",
        }])
        plan = plan_run(root, write=True); unit = plan["units"][0]
        context = task_context(root, plan["run_id"], unit["unit_id"])
        assert context["entities"][0]["vendor_drift_candidate"]
        decision = _fake_decision(context); row = decision["entities"][0]
        row.update({"outcome": "confirmed", "confidence": "high"})
        row["proposal"].update({
            "mutation_type": "new_requirement_proposal", "semantic_key": "demo", "title": "Demo",
            "source_scenario": "Demo", "migration_boundary": "Demo", "target_solution": "adapt",
            "acceptance_criteria": "Demo", "risk": "Demo", "specification_text": "Demo",
        })
        errors = validate_decision(root, plan["run_id"], unit["unit_id"], decision, context)
        assert any("vendor release drift candidate" in error for error in errors)


def test_three_fake_workers_and_single_writer_are_idempotent() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root)
        plan = plan_run(root, limit=3, write=True)
        run = run_coordinator(root, plan["run_id"], workers=3, fake=True)
        assert run["status"] == "ok"
        assert run["peak_concurrency"] == 3
        assert run["batch_feedback"]["recommended_unit_size"] >= plan["unit_size"]
        result = apply_ready(root, plan["run_id"])
        assert result["status"] == "ok" and len(result["applied"]) == 3
        summary_path = root / RUNS / plan["run_id"] / "apply-summary.json"
        first_summary = summary_path.read_bytes()
        assert apply_ready(root, plan["run_id"])["applied"] == []
        assert summary_path.read_bytes() == first_summary
        tasks = [row for _, row in read_jsonl(root / "analysis/queue/tasks.jsonl")]
        assert all(row["status"] == "needs_review" for row in tasks[:3])
        ledger = [row for _, row in read_jsonl(root / LEDGER)]
        assert len([row for row in ledger if row["event"] == "applied"]) == 3
        assert all(row.get("resulting_hashes") for row in ledger if row["event"] == "applied")


def test_compaction_preserves_audit_and_makes_run_read_only() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1)
        plan = plan_run(root, write=True)
        run_coordinator(root, plan["run_id"], fake=True)
        apply_ready(root, plan["run_id"])
        preview = compact_run(root, plan["run_id"])
        assert preview["eligible"] and not preview["written"]
        result = compact_run(root, plan["run_id"], write=True)
        run_dir = root / RUNS / plan["run_id"]
        assert result["compacted"]
        assert (run_dir / "manifest.json").is_file()
        assert (run_dir / "decisions" / f"{plan['units'][0]['unit_id']}.json").is_file()
        assert (run_dir / "apply-events.jsonl").is_file()
        assert not (run_dir / "states").exists()
        assert result["compaction"]["counters"]["applied_events"] == 1
        files = [path for path in run_dir.rglob("*") if path.is_file()]
        assert result["compaction"]["after"] == {"files": len(files), "bytes": sum(path.stat().st_size for path in files)}
        assert inspect_run(root, plan["run_id"])["compacted"]
        with pytest.raises(ParallelResearchError, match="read-only"):
            apply_ready(root, plan["run_id"])
        with pytest.raises(ParallelResearchError, match="read-only"):
            run_coordinator(root, plan["run_id"], fake=True)
        (run_dir / "apply-summary.json").write_text("{}\n", encoding="utf-8")
        assert inspect_run(root, plan["run_id"])["status"] == "fail"


def test_compacted_audit_recomputes_marker_claims() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1)
        plan = plan_run(root, write=True)
        run_coordinator(root, plan["run_id"], fake=True)
        apply_ready(root, plan["run_id"])
        compact_run(root, plan["run_id"], write=True)
        marker_path = root / RUNS / plan["run_id"] / "compaction.json"
        marker = _read_json(marker_path)
        marker["counters"]["units"] = 999
        marker["decision_hashes"][plan["units"][0]["unit_id"]] = "forged"
        write(marker_path, json.dumps(marker))
        result = inspect_run(root, plan["run_id"])
        assert result["status"] == "fail"
        assert "counters_mismatch" in result["integrity_errors"]
        assert "decision_hashes_mismatch" in result["integrity_errors"]


def test_compaction_rejects_incomplete_and_corrupt_runs() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1)
        plan = plan_run(root, write=True)
        with pytest.raises(ParallelResearchError, match="not eligible"):
            compact_run(root, plan["run_id"], write=True)
        unit_id = plan["units"][0]["unit_id"]
        _set_state(root, plan["run_id"], unit_id, "rejected", reason="test")
        assert f"{unit_id}:state=rejected" in inspect_run(root, plan["run_id"])["reasons"]
        with ProcessLock(root / COORDINATOR_LOCK, plan["run_id"]):
            assert "run_is_active" in inspect_run(root, plan["run_id"])["reasons"]
        write(root / RUNS / plan["run_id"] / "unknown.txt", "keep me")
        assert any(reason.startswith("unknown_files:") for reason in inspect_run(root, plan["run_id"])["reasons"])
        (root / RUNS / plan["run_id"] / "unknown.txt").unlink()
        (root / RUNS / plan["run_id"] / "summary.json").unlink(missing_ok=True)
        assert "run_summary_missing" in inspect_run(root, plan["run_id"])["reasons"]
        _set_state(root, plan["run_id"], unit_id, "planned")
        run_coordinator(root, plan["run_id"], fake=True)
        apply_ready(root, plan["run_id"])
        decision = root / RUNS / plan["run_id"] / "decisions" / f"{plan['units'][0]['unit_id']}.json"
        decision.write_text("{}\n", encoding="utf-8")
        with pytest.raises(ParallelResearchError, match="decision_hash_mismatch"):
            compact_run(root, plan["run_id"], write=True)


def test_compaction_and_direct_unit_respect_run_locks() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1)
        plan = plan_run(root, write=True); unit_id = plan["units"][0]["unit_id"]
        run_coordinator(root, plan["run_id"], fake=True)
        apply_ready(root, plan["run_id"])
        with ProcessLock(root / APPLY_LOCK, plan["run_id"]):
            with pytest.raises(ParallelResearchError, match="run_is_active|lock is held"):
                compact_run(root, plan["run_id"], write=True)
        with ProcessLock(root / COORDINATOR_LOCK, plan["run_id"]):
            with pytest.raises(ParallelResearchError, match="lock is held"):
                run_unit(root, plan["run_id"], unit_id, "fake-model", 30, fake=True)


def test_run_summaries_are_written_before_locks_are_released(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1)
        plan = plan_run(root, write=True)
        observed: dict[str, bool] = {}
        original = parallel_research._write_json

        def guarded_write(path: Path, payload: object, mode: int | None = None) -> None:
            if path.name == "summary.json":
                observed["coordinator"] = (root / COORDINATOR_LOCK).exists()
            elif path.name == "apply-summary.json":
                observed["apply"] = (root / APPLY_LOCK).exists()
            original(path, payload, mode)

        monkeypatch.setattr(parallel_research, "_write_json", guarded_write)
        run_coordinator(root, plan["run_id"], fake=True)
        apply_ready(root, plan["run_id"])
        assert observed == {"coordinator": True, "apply": True}


def test_dependency_rejection_keeps_unit_ready_for_next_apply_pass() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        tasks = [row for _, row in read_jsonl(root / "analysis/queue/tasks.jsonl")]
        tasks[0]["parallel_entities"] = ["CUS-1", "CUS-2"]
        write_jsonl(root / "analysis/queue/tasks.jsonl", tasks)
        write_jsonl(root / "analysis/customization-registry/customization-items.jsonl", [
            {"customization_id": cid, "title": cid, "scope_status": "included", "status": "ready_for_review"}
            for cid in ("CUS-1", "CUS-2")
        ])
        write_jsonl(root / "analysis/customization-registry/customization-evidence.jsonl", [
            {"customization_id": cid, "evidence_type": "source", "source_path": "project.toml"}
            for cid in ("CUS-1", "CUS-2")
        ])
        plan = plan_run(root, unit_size=1, write=True)
        run_coordinator(root, plan["run_id"], fake=True)
        first, second = plan["units"]
        assert second["apply_after"] == [first["unit_id"]]
        _set_state(root, plan["run_id"], first["unit_id"], "rejected", reason="test")
        result = apply_ready(root, plan["run_id"])
        assert {row["unit_id"]: row["reason"] for row in result["rejected"]}[second["unit_id"]] == "dependency_not_applied"
        assert _read_json(_state_path(root, plan["run_id"], second["unit_id"]))["state"] == "follow_up"


def test_applied_dependency_allows_both_mrq_graph_fingerprints_to_change() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        links = root / "analysis/migration-requirements/requirement-links.jsonl"
        requirements = root / "analysis/migration-requirements/requirements.jsonl"
        queue = root / "analysis/queue/tasks.jsonl"
        unrelated = root / "project.toml"
        for path in (links, requirements, queue, unrelated): write(path, "before\n")
        unit = {
            "apply_after": ["U-1"],
            "source_fingerprints": {
                str(links.relative_to(root)): _file_sha(links),
                str(requirements.relative_to(root)): _file_sha(requirements),
                str(queue.relative_to(root)): _file_sha(queue),
                str(unrelated.relative_to(root)): _file_sha(unrelated),
            },
        }
        write(links, "after\n"); write(requirements, "after\n"); write(queue, "after\n")
        assert _verify_fingerprints(root, unit, {"U-1"})
        write(unrelated, "after\n")
        assert not _verify_fingerprints(root, unit, {"U-1"})


def test_set_relation_primary_replaces_previous_primary_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("one_c_autoresearch.parallel_research.validate_mrq_graph", lambda root, requirements, links: {"status": "ok", "errors": []})
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "conflicting_cus")
        unit = {"unit_id": "U", "task_id": "Q-CUS", "adapter": "conflicting_cus"}
        decision = {"entities": [{
            "entity_id": "CUS-1", "conclusion": "Move primary",
            "proposal": {"mutation_type": "set_relation", "requirement_id": "MRQ-2", "role": "primary", "rationale": "Better owner", "acceptance_refs": []},
        }]}
        requirements = [
            {"requirement_id": "MRQ-1", "title": "Old", "status": "draft", "target_solution": "adapt"},
            {"requirement_id": "MRQ-2", "title": "New", "status": "draft", "target_solution": "adapt"},
            {"requirement_id": "MRQ-3", "title": "Shared", "status": "draft", "target_solution": "adapt"},
        ]
        links = [
            {"schema_version": "migration-requirements/v1", "requirement_id": "MRQ-1", "customization_id": "CUS-1", "role": "primary", "rationale": "Old", "required": True, "acceptance_refs": [], "effort_owner": True},
            {"schema_version": "migration-requirements/v1", "requirement_id": "MRQ-3", "customization_id": "CUS-1", "role": "shared", "rationale": "Keep", "required": True, "acceptance_refs": [], "effort_owner": False},
        ]
        _, changed, _, result_links = _apply_cus(root, unit, decision, requirements, links)
        assert changed
        assert sorted((link["requirement_id"], link["role"]) for link in result_links if link["customization_id"] == "CUS-1") == [("MRQ-2", "primary"), ("MRQ-3", "shared")]


def test_coordinator_uses_project_model_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1)
        (root / "project.toml").write_text('[project]\nid="fixture"\n[parallel_research]\nmodel="fixture-model"\n', encoding="utf-8")
        plan = plan_run(root, write=True)
        monkeypatch.delenv("CODEX_RESEARCH_MODEL", raising=False)
        monkeypatch.setattr("one_c_autoresearch.parallel_research._run_unit", lambda *_: {"status": "completed", "unit_id": "U"})
        result = run_coordinator(root, plan["run_id"])
        assert result["model"] == "fixture-model"
        assert DEFAULT_MODEL == ""


def test_coordinator_stops_feeding_units_after_usage_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 6); plan = plan_run(root, limit=6, write=True)
        calls = []
        def reject(*args: object) -> dict[str, str]:
            calls.append(args[2])
            return {"status": "rejected", "unit_id": str(args[2]), "reason": "usage_limit"}
        monkeypatch.setattr("one_c_autoresearch.parallel_research._run_unit", reject)
        result = run_coordinator(root, plan["run_id"], workers=3)
        assert result["halt_reason"] == "usage_limit"
        assert len(calls) == 3


def test_coordinator_normalizes_stale_running_state_before_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1); plan = plan_run(root, write=True); unit = plan["units"][0]
        _set_state(root, plan["run_id"], unit["unit_id"], "running")
        seen = []
        def complete(*args: object) -> dict[str, str]:
            seen.append(_read_json(_state_path(root, plan["run_id"], unit["unit_id"]))["state"])
            return {"status": "completed", "unit_id": str(args[2])}
        monkeypatch.setattr("one_c_autoresearch.parallel_research._run_unit", complete)
        run_coordinator(root, plan["run_id"])
        assert seen == ["planned"]


def test_cus_adapter_stages_graph_without_publishing(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "conflicting_cus")
        original = (root / "analysis/migration-requirements/requirement-links.jsonl").read_bytes()
        monkeypatch.setattr("one_c_autoresearch.parallel_research.validate_mrq_graph", lambda *_: {"status": "ok"})
        unit = {"unit_id": "U", "task_id": "Q-CUS", "adapter": "conflicting_cus"}
        decision = {"entities": [{"entity_id": "CUS-1", "conclusion": "Связать", "proposal": {"mutation_type": "set_relation", "requirement_id": "MRQ-1", "role": "supporting", "rationale": "Проверено"}}]}
        affected, changed, _, links = _apply_cus(root, unit, decision, [{"requirement_id": "MRQ-1"}], [])
        assert changed and affected == ["analysis/migration-requirements"]
        assert links[0]["customization_id"] == "CUS-1"
        assert (root / "analysis/migration-requirements/requirement-links.jsonl").read_bytes() == original


def test_cus_follow_up_proposal_is_idempotent_after_interrupted_apply() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        unit = {"unit_id": "U", "task_id": "Q-CUS", "adapter": "uncovered_cus"}
        decision = {"entities": [{"entity_id": "CUS-1", "conclusion": "Уточнить", "proposal": {"mutation_type": "follow_up"}}]}
        _apply_cus(root, unit, decision, [], [])
        _apply_cus(root, unit, decision, [], [])
        proposals = [row for _, row in read_jsonl(root / "analysis/parallel-research/cus-proposals.jsonl")]
        assert [(row["unit_id"], row["customization_id"]) for row in proposals] == [("U", "CUS-1")]


def test_cus_follow_up_keeps_task_pending_and_goal_incomplete() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        plan = plan_run(root, write=True)
        run_coordinator(root, plan["run_id"], fake=True)
        result = apply_ready(root, plan["run_id"])
        task = next(row for _, row in read_jsonl(root / "analysis/queue/tasks.jsonl"))
        assert task["status"] == "pending"
        assert result["status"] == "ok"
        assert result["goal_complete"] is False
        assert result["coverage"] == {"uncovered": 1, "conflicts": 0}


def test_apply_rejects_changed_source_but_keeps_other_units() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 2)
        plan = plan_run(root, write=True)
        run_coordinator(root, plan["run_id"], workers=2, fake=True)
        write(root / "project.toml", "[project]\nid='changed'\n")
        result = apply_ready(root, plan["run_id"])
        assert result["status"] == "ok"
        assert not result["applied"]
        assert {row["reason"] for row in result["rejected"]} == {"source_changed"}


def test_apply_rejects_changed_file_inside_source_directory() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); tasks = seed_review(root, 1)
        write(root / "source/a.txt", "before\n")
        tasks[0]["source_artifacts"] = ["project.toml", "source"]
        write_jsonl(root / "analysis/queue/tasks.jsonl", tasks)
        plan = plan_run(root, write=True)
        assert "source/a.txt" in plan["units"][0]["allowed_sources"]
        assert "source" not in plan["units"][0]["allowed_sources"]
        run_coordinator(root, plan["run_id"], fake=True)
        write(root / "source/a.txt", "after\n")
        result = apply_ready(root, plan["run_id"])
        assert result["applied"] == []
        assert result["rejected"][0]["reason"] == "source_changed"


def test_batch_failure_restores_queue_and_review_artifact() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); original = seed_review(root, 1)
        plan = plan_run(root, write=True); run_coordinator(root, plan["run_id"], fake=True)
        result = apply_ready(root, plan["run_id"], [["sh", "-c", "exit 7"]])
        assert result["status"] == "fail" and result["rolled_back"]
        assert [row for _, row in read_jsonl(root / "analysis/queue/tasks.jsonl")] == original
        feature_artifact = root / "analysis/features/F-0/artifacts" / f"parallel-research-{plan['units'][0]['unit_id']}.json"
        assert not feature_artifact.exists()


def test_adapter_validation_requires_semantic_keys_and_known_mutations() -> None:
    for adapter in ("uncovered_cus", "conflicting_cus"):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); seed_cus(root, adapter)
            plan = plan_run(root, write=True); unit = plan["units"][0]; context = task_context(root, plan["run_id"], unit["unit_id"])
            proposal_type = "new_requirement_proposal" if adapter == "uncovered_cus" else "split_customization_proposal"
            decision = {"schema_version": "parallel-research/v1", "run_id": plan["run_id"], "unit_id": unit["unit_id"], "adapter": adapter, "context_hash": context["context_hash"], "entities": [{"entity_id": "CUS-1", "outcome": "needs_followup", "confidence": "medium", "conclusion": "Split", "evidence": [{"path": "project.toml", "summary": "source"}], "proposal": {"mutation_type": proposal_type}}]}
            assert any("semantic_key" in error for error in validate_decision(root, plan["run_id"], unit["unit_id"], decision, context))


def test_relation_mutation_requires_requirement_id() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "conflicting_cus")
        plan = plan_run(root, write=True); unit = plan["units"][0]; context = task_context(root, plan["run_id"], unit["unit_id"])
        decision = _fake_decision(context)
        decision["entities"][0]["proposal"]["mutation_type"] = "set_relation"
        errors = validate_decision(root, plan["run_id"], unit["unit_id"], decision, context)
        assert any("requirement_id is required" in error for error in errors)


def test_requirement_mutation_rejects_business_persona_as_relation_role() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_cus(root, "uncovered_cus")
        plan = plan_run(root, write=True); unit = plan["units"][0]; context = task_context(root, plan["run_id"], unit["unit_id"])
        decision = _fake_decision(context)
        decision["entities"][0]["proposal"].update({"mutation_type": "new_requirement_proposal", "role": "Бухгалтер"})
        errors = validate_decision(root, plan["run_id"], unit["unit_id"], decision, context)
        assert any("invalid requirement relation role" in error for error in errors)


def test_decision_rejects_sibling_and_invalid_line_evidence() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1)
        write(root / "docs/allowed.md", "one\ntwo\n")
        write(root / "docs/sibling.md", "secret\n")
        tasks = [row for _, row in read_jsonl(root / "analysis/queue/tasks.jsonl")]
        tasks[0]["source_artifacts"] = ["docs/allowed.md"]
        write_jsonl(root / "analysis/queue/tasks.jsonl", tasks)
        plan = plan_run(root, write=True); unit = plan["units"][0]
        context = task_context(root, plan["run_id"], unit["unit_id"])
        decision = {
            "schema_version": plan["schema_version"], "run_id": plan["run_id"], "unit_id": unit["unit_id"],
            "adapter": unit["adapter"], "context_hash": context["context_hash"],
            "entities": [{"entity_id": unit["entity_ids"][0], "outcome": "confirmed", "confidence": "high", "conclusion": "x",
                          "evidence": [{"path": "docs/sibling.md", "line_start": 1, "line_end": 1, "summary": "x"}],
                          "proposal": {"mutation_type": "record_evidence"}}],
        }
        assert any("outside unit sources" in error for error in validate_decision(root, plan["run_id"], unit["unit_id"], decision, context))
        decision["entities"][0]["evidence"][0] = {"path": "docs/allowed.md", "line_start": 1, "line_end": 9, "summary": "x"}
        assert any("exceeds file" in error for error in validate_decision(root, plan["run_id"], unit["unit_id"], decision, context))


def test_decision_rejects_shape_that_bypasses_output_schema() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1)
        plan = plan_run(root, write=True); unit = plan["units"][0]
        context = task_context(root, plan["run_id"], unit["unit_id"])
        decision = {
            "schema_version": plan["schema_version"], "run_id": plan["run_id"], "unit_id": unit["unit_id"],
            "adapter": unit["adapter"], "context_hash": context["context_hash"],
            "entities": [{"entity_id": unit["entity_ids"][0], "outcome": "confirmed", "confidence": "high", "conclusion": "x",
                          "evidence": [{"path": "project.toml", "line_start": 1, "line_end": 1, "summary": "x"}],
                          "proposal": {"mutation_type": "record_evidence"}}],
        }
        errors = validate_decision(root, plan["run_id"], unit["unit_id"], decision, context)
        assert any("proposal does not match schema" in error for error in errors)
        decision["entities"] = ["not-an-object"]
        assert "decision entity is not an object" in validate_decision(root, plan["run_id"], unit["unit_id"], decision, context)


def test_worker_limit_lock_identity_and_trace_cleanup() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1); plan = plan_run(root, write=True)
        with pytest.raises(ParallelResearchError, match="1..20"):
            run_coordinator(root, plan["run_id"], workers=21, fake=True)
        lock = root / COORDINATOR_LOCK
        write(lock, json.dumps({"run_id": "old", "pid": os.getpid(), "process_start": "not-current"}))
        with ProcessLock(lock, "new"):
            assert _read_json(lock)["run_id"] == "new"
        trace = root / TRACE_ROOT / "old" / "x.jsonl"
        write(trace, "{}\n"); os.utime(trace, (time.time() - 20 * 86400,) * 2)
        assert cleanup_traces(root, 14)["deleted"]
        active = root / TRACE_ROOT / plan["run_id"] / "active.jsonl"
        write(active, "{}\n"); os.utime(active, (time.time() - 20 * 86400,) * 2)
        write(lock, json.dumps({"run_id": plan["run_id"], "pid": os.getpid(), "process_start": _process_start(os.getpid())}))
        assert cleanup_traces(root, 14)["deleted"] == []
        assert active.exists()


def test_codex_command_ignores_config_and_disables_ambient_tools() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        write(root / ".codex/config.toml", "[mcp_servers.demo]\ncommand='demo'\n")
        decision = root / "output/decision.json"
        decision.parent.mkdir()
        command = _codex_command(root, "model", decision, "prompt")
        joined = " ".join(str(value) for value in command)
        assert command[0] == "bwrap"
        assert "--ro-bind" in command and "/workspace" in command
        assert str(decision.parent) in command
        assert str(root / "output") in command and str(root / "decisions") not in command
        assert "--dangerously-bypass-approvals-and-sandbox" in command
        assert "--ignore-user-config" in command
        assert "mcp_servers." not in joined
        for feature in ("apps", "browser_use", "computer_use", "image_generation", "standalone_web_search"):
            assert feature in command


def test_decision_schema_requires_one_row_per_entity() -> None:
    schema = decision_schema("uncovered_cus", 10)
    entities = schema["properties"]["entities"]
    assert entities["minItems"] == entities["maxItems"] == 10


def test_worker_workspace_contains_only_assigned_sources() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        write(root / "allowed/source.txt", "ok")
        write(root / "forbidden/secret.txt", "no")
        context = {"allowed_sources": ["allowed/source.txt"], "run_id": "R", "unit_id": "U"}
        workspace = _prepare_worker_workspace(root, "R", "U", context, {"type": "object"})
        assert (workspace / "allowed/source.txt").read_text() == "ok"
        assert not (workspace / "forbidden").exists()
        assert (workspace / "scripts/research_task_context.py").is_file()


def test_worker_workspace_does_not_dereference_source_symlinks() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        write(root / "outside/secret.txt", "secret")
        (root / "allowed").mkdir()
        (root / "allowed/link").symlink_to(root / "outside", target_is_directory=True)
        context = {"allowed_sources": ["allowed"], "run_id": "R", "unit_id": "U"}
        workspace = _prepare_worker_workspace(root, "R", "U", context, {"type": "object"})
        assert (workspace / "allowed/link").is_symlink()


def test_trace_rejects_forbidden_global_read_and_out_of_scope_path() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); decision = root / "decision.json"; write(decision, "{}")
        trace = root / "trace.jsonl"
        write(trace, json.dumps({"command": "python3 scripts/research_task_context.py --unit-id U; sed -n 1p analysis/queue/tasks.jsonl; rg x src/other.py"}) + "\n")
        summary = summarize_trace(trace, decision, {"unit_id": "U", "allowed_sources": ["docs/allowed.md"]})
        assert "forbidden_command" in summary["errors"]
        assert "out_of_scope_read" in summary["errors"]
        write(trace, json.dumps({"command": "apply_patch < change.diff"}) + "\n")
        assert "forbidden_command" in summarize_trace(trace, decision, {"unit_id": "U", "allowed_sources": []})["errors"]


def test_trace_does_not_treat_command_output_as_a_command() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); decision = root / "decision.json"; write(decision, "{}")
        trace = root / "trace.jsonl"
        events = [
            {"type": "item.completed", "item": {"type": "command_execution", "command": "python3 scripts/research_task_context.py --unit-id U", "aggregated_output": "analysis/queue/tasks.jsonl"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "apply_patch analysis/queue/tasks.jsonl"}},
        ]
        write(trace, "\n".join(json.dumps(event) for event in events) + "\n")
        summary = summarize_trace(trace, decision, {"unit_id": "U", "runner_version": "3", "allowed_sources": []})
        assert summary["commands"] == ["python3 scripts/research_task_context.py --unit-id U"]
        assert summary["errors"] == []


def test_trace_keeps_unicode_line_separators_inside_json_event() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); decision = root / "decision.json"; write(decision, "{}")
        trace = root / "trace.jsonl"
        events = [
            {"command": "python3 scripts/research_task_context.py --unit-id U"},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "nl allowed.bin", "aggregated_output": "binary\u0085payload"}},
        ]
        write(trace, "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n")
        summary = summarize_trace(trace, decision, {"unit_id": "U", "runner_version": "5", "allowed_sources": ["allowed.bin"]})
        assert summary["invalid_events"] == 0
        assert summary["errors"] == []


def test_process_group_termination_and_canonical_allocators() -> None:
    process = subprocess.Popen(["sh", "-c", "sleep 30 & wait"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    terminate_process_group(process, 0.1)
    assert process.poll() is not None
    assert allocate_canonical_id("requirement", "demo").startswith("MRQ-")
    assert allocate_canonical_id("customization", "demo").startswith("CUS-")
    assert allocate_canonical_id("task", "demo").startswith("Q-PR-")
    assert allocate_canonical_id("lineage", "demo").startswith("LIN-")


def test_codex_command_keeps_worker_in_parent_process_group() -> None:
    command = _codex_command(Path("/workspace"), "test-model", Path("/output/decision.json"), "prompt")
    assert "--new-session" not in command


def test_codex_failure_reason_distinguishes_usage_limit_and_capacity() -> None:
    with tempfile.TemporaryDirectory() as temp:
        trace = Path(temp) / "trace.jsonl"
        write(trace, '{"type":"error","message":"You have hit your usage limit"}\n')
        assert _codex_failure_reason(trace, 1) == "usage_limit"
        write(trace, '{"type":"error","message":"Selected model is at capacity"}\n')
        assert _codex_failure_reason(trace, 1) == "model_capacity"


def test_vendor_drift_candidate_is_limited_to_customer_only_regulatory_sources() -> None:
    regulatory = [{"comparison_status": "customer_only", "customer_path": "configuration/Reports/РегламентированныйОтчетНДС/Templates/Форма2026/Ext/Template.xml"}]
    custom = [{"comparison_status": "customer_only", "customer_path": "configuration/Reports/ПользовательскийОтчет/Ext/ObjectModule.bsl"}]
    changed = [{"comparison_status": "changed", "customer_path": "configuration/Reports/РегламентированныйОтчетНДС/Ext/ObjectModule.bsl"}]
    markers = ["/Reports/RegulatedReport"]
    regulatory[0]["customer_path"] = "sources/customer/Reports/RegulatedReport/file.xml"
    assert _vendor_drift_candidate(regulatory, markers)
    assert not _vendor_drift_candidate(custom, markers)
    assert not _vendor_drift_candidate(changed, markers)


def test_active_worker_registry_terminates_process_groups() -> None:
    process = subprocess.Popen(["sh", "-c", "sleep 30 & wait"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    _track_worker(process)
    assert process.pid in terminate_active_workers()
    assert process.poll() is not None


def test_coordinator_signal_guard_restores_handler_and_reports_interrupt() -> None:
    previous = signal.getsignal(signal.SIGTERM)
    with pytest.raises(ParallelResearchError, match="interrupted by signal"):
        with coordinator_signal_guard():
            os.kill(os.getpid(), signal.SIGTERM)
    assert signal.getsignal(signal.SIGTERM) == previous


def test_resume_rejects_changed_context() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1); plan = plan_run(root, write=True); unit = plan["units"][0]
        run_coordinator(root, plan["run_id"], fake=True)
        decision = root / RUNS / plan["run_id"] / "decisions" / f"{unit['unit_id']}.json"
        data = json.loads(decision.read_text()); data["context_hash"] = "stale"; decision.write_text(json.dumps(data))
        _set_state(root, plan["run_id"], unit["unit_id"], "planned")
        result = run_coordinator(root, plan["run_id"], fake=True)
        assert result["status"] == "fail"


def test_resume_revalidates_completed_decision_and_model() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1); plan = plan_run(root, write=True)
        run_coordinator(root, plan["run_id"], model="model-a", fake=True)
        resumed = run_coordinator(root, plan["run_id"], model="model-a", fake=True)
        assert resumed["status"] == "ok"
        assert resumed["unit_counts"] == {"reused": 1}
        changed = run_coordinator(root, plan["run_id"], model="model-b", fake=True)
        assert changed["status"] == "fail"


def test_resume_reuses_attested_decision_without_rebuilding_context(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1); plan = plan_run(root, write=True)
        run_coordinator(root, plan["run_id"], model="model-a", fake=True)

        def fail(*args: object, **kwargs: object) -> dict:
            raise AssertionError("completed context was rebuilt")

        monkeypatch.setattr(parallel_research, "task_context", fail)
        resumed = run_coordinator(root, plan["run_id"], model="model-a", fake=True)
        assert resumed["status"] == "ok"
        assert resumed["unit_counts"] == {"reused": 1}


def test_resume_replaces_invalid_rejected_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); seed_review(root, 1); plan = plan_run(root, write=True)
        original = parallel_research._fake_decision

        def invalid(context: dict) -> dict:
            decision = original(context)
            del decision["entities"][0]["conclusion"]
            return decision

        monkeypatch.setattr(parallel_research, "_fake_decision", invalid)
        first = run_coordinator(root, plan["run_id"], fake=True)
        assert first["status"] == "fail"
        assert _read_json(_state_path(root, plan["run_id"], plan["units"][0]["unit_id"]))["state"] == "rejected"

        monkeypatch.setattr(parallel_research, "_fake_decision", original)
        retried = run_coordinator(root, plan["run_id"], fake=True)
        assert retried["status"] == "ok"
        assert retried["unit_counts"] == {"completed": 1}
