from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from pathlib import Path
from typing import Any

from .autopilot import (
    FINAL_DIFF_INVENTORY_HEADER,
    INFOBASE_QUESTIONS_HEADER,
)
from .common import git_check_ignored, read_jsonl, repo_path
from .configuration_source_parser import DEFAULT_SNAPSHOT_DIR, read_snapshot, validate_snapshot
from .custom_metadata_inventory import DEFAULT_INVENTORY_DIR, validate_inventory
from .customization_registry import validate_registry as validate_customization_registry
from .migration_requirements import canonical_active as migration_requirements_active, validate as validate_migration_requirements
from .customer_register import validate_customer_register
from .detailed_customer_register import DETAILED_REGISTER_CSV, validate_detailed_customer_register
from .detailed_register_reverse_review import MARKUP_CSV, validate_reverse_review
from .doctor import run_doctor
from .reverse_map import REVERSE_MAP_COVERAGE_HEADER
from .subject_cards import SUBJECT_CARD_CONTOURS_HEADER
from .v8unpack_refinement import validate_refinement_artifacts
from .v8unpack_autopilot import validate_source_alignment


class CheckFailure(Exception):
    pass


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def require_path(root: Path, relative: str, errors: list[str]) -> None:
    require(repo_path(root, relative).exists(), f"Missing required path: {relative}", errors)


def require_text(root: Path, relative: str, pattern: str, message: str, errors: list[str]) -> None:
    path = repo_path(root, relative)
    if not path.exists():
        errors.append(f"Missing required path: {relative}")
        return
    if not re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE):
        errors.append(message)


def require_same_content(root: Path, expected: str, actual: str, message: str, errors: list[str]) -> None:
    expected_path = repo_path(root, expected)
    actual_path = repo_path(root, actual)
    if not expected_path.exists() or not actual_path.exists():
        errors.append(f"Missing required path for content comparison: {expected} or {actual}")
        return
    if expected_path.read_text(encoding="utf-8") != actual_path.read_text(encoding="utf-8"):
        errors.append(message)


def test_template_portability(root: Path, errors: list[str]) -> None:
    roots = ("src", "scripts", "docs", ".agents", "templates/research-repo")
    forbidden = {
        "/run/" + "media/": "absolute workstation path",
        "Транс" + "неф": "customer name",
        "tn-" + "bp-20": "customer project id",
        "tn_" + "bp20": "customer MCP profile",
        "gpt-5.6-" + "sol": "fixed research model",
        "БП " + "2.0": "fixed source-product label",
        "БП " + "3.0": "fixed target-product label",
        "BP " + "2.0": "fixed source-product label",
        "BP " + "3.0": "fixed target-product label",
    }
    generated_parts = {"__pycache__", ".pytest_cache", "runs", "workspaces", "traces"}
    for base in roots:
        folder = repo_path(root, base)
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if not path.is_file() or generated_parts.intersection(path.parts):
                continue
            if path.suffix not in {".py", ".md", ".toml", ".json", ".jsonl", ".txt"} and path.name not in {"AGENTS.md", ".gitignore"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for marker, label in forbidden.items():
                if marker in text:
                    errors.append(f"Non-portable {label} in {path.relative_to(root).as_posix()}: {marker}")


def test_template(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    errors: list[str] = []
    required_paths = [
        ".github/workflows/verify.yml",
        "README.md",
        "AGENTS.md",
        "pyproject.toml",
        "one_c_autoresearch/__init__.py",
        "one_c_autoresearch/__main__.py",
        "project.example.toml",
        "src/one_c_autoresearch/cli.py",
        "src/one_c_autoresearch/autopilot.py",
        "src/one_c_autoresearch/detail_maps.py",
        "src/one_c_autoresearch/subject_cards.py",
        "src/one_c_autoresearch/functional_gaps.py",
        "src/one_c_autoresearch/functional_gap_dashboard.py",
        "src/one_c_autoresearch/functional_gap_probes.py",
        "src/one_c_autoresearch/final_gate.py",
        "src/one_c_autoresearch/review_dashboard.py",
        "src/one_c_autoresearch/reverse_map.py",
        "src/one_c_autoresearch/doctor.py",
        "src/one_c_autoresearch/bootstrap.py",
        "src/one_c_autoresearch/workspace.py",
        "src/one_c_autoresearch/workspace_api.py",
        "src/one_c_autoresearch/workspace_runner.py",
        "src/one_c_autoresearch/workspace_static/index.html",
        "src/one_c_autoresearch/workspace_assets/research-template.zip",
        "scripts/build_workspace_template.py",
        "web/workspace/package.json",
        "web/workspace/package-lock.json",
        "web/workspace/src/App.tsx",
        "src/one_c_autoresearch/queue.py",
        "src/one_c_autoresearch/checks.py",
        "src/one_c_autoresearch/configuration_source_parser.py",
        "src/one_c_autoresearch/custom_metadata_inventory.py",
        "src/one_c_autoresearch/customization_registry.py",
        "src/one_c_autoresearch/migration_requirements.py",
        "src/one_c_autoresearch/parallel_research.py",
        "src/one_c_autoresearch/manual_cleanup.py",
        "templates/research-repo/.gitignore",
        "templates/research-repo/AGENTS.md",
        "templates/research-repo/README.md",
        "templates/research-repo/project.toml",
        "templates/research-repo/.codex/1c-mcp.example.toml",
        "templates/research-repo/docs/agent/index.md",
        "templates/research-repo/docs/agent/repo-map.md",
        "templates/research-repo/docs/agent/verification.md",
        "templates/research-repo/docs/method/1c-autoresearch-process.md",
        "templates/research-repo/docs/method/evidence-pack-schema.md",
        "templates/research-repo/docs/method/autopilot-customization-map.md",
        "templates/research-repo/docs/method/physical-clean-comparison.md",
        "templates/research-repo/docs/method/reverse-functional-map.md",
        "templates/research-repo/docs/method/research-goal-router.md",
        "templates/research-repo/docs/method/research-review-preparation.md",
        "templates/research-repo/docs/method/manual-markup-goal.md",
        "templates/research-repo/docs/method/functional-gap-goal.md",
        "templates/research-repo/docs/method/parallel-research-goal.md",
        "templates/research-repo/analysis/indexes/README.md",
        "templates/research-repo/analysis/indexes/diff-inventory.csv",
        "templates/research-repo/analysis/indexes/feature-map.csv",
        "templates/research-repo/analysis/indexes/final-diff-inventory.csv",
        "templates/research-repo/analysis/indexes/final-feature-map.csv",
        "templates/research-repo/analysis/clean-comparison/README.md",
        "templates/research-repo/analysis/customization-registry/README.md",
        "templates/research-repo/analysis/customization-registry/customization-items.jsonl",
        "templates/research-repo/analysis/customization-registry/schemas/customization-item.schema.json",
        "templates/research-repo/analysis/migration-requirements/README.md",
        "templates/research-repo/analysis/migration-requirements/requirements.jsonl",
        "templates/research-repo/analysis/migration-requirements/schemas/migration-requirement.schema.json",
        "templates/research-repo/analysis/parallel-research/README.md",
        "templates/research-repo/analysis/detail-maps/README.md",
        "templates/research-repo/analysis/detail-maps/index.csv",
        "templates/research-repo/analysis/detail-maps/_templates/detail-map.json",
        "templates/research-repo/analysis/subject-cards/README.md",
        "templates/research-repo/analysis/subject-cards/candidates.csv",
        "templates/research-repo/analysis/subject-cards/contours.csv",
        "templates/research-repo/analysis/subject-cards/classification.csv",
        "templates/research-repo/analysis/subject-cards/registry.csv",
        "templates/research-repo/analysis/subject-cards/coverage.csv",
        "templates/research-repo/analysis/subject-cards/_templates/subject-card.json",
        "templates/research-repo/analysis/functional-gaps/README.md",
        "templates/research-repo/analysis/functional-gaps/index.csv",
        "templates/research-repo/analysis/functional-gaps/coverage.csv",
        "templates/research-repo/analysis/functional-gaps/open-questions.csv",
        "templates/research-repo/analysis/functional-gaps/_templates/gap-card.json",
        "templates/research-repo/analysis/functional-gaps/_templates/target-profile.toml",
        "templates/research-repo/analysis/functional-gaps/profiles/.gitkeep",
        "templates/research-repo/analysis/reverse-map/README.md",
        "templates/research-repo/analysis/reverse-map/state.md",
        "templates/research-repo/analysis/reverse-map/coverage.csv",
        "templates/research-repo/analysis/reverse-map/workitems.jsonl",
        "templates/research-repo/analysis/reverse-map/decisions.csv",
        "templates/research-repo/analysis/reverse-map/unresolved.csv",
        "templates/research-repo/analysis/reverse-map/infobase-checks.csv",
        "templates/research-repo/analysis/reverse-map/scenarios/README.md",
        "templates/research-repo/analysis/reverse-map/outputs/README.md",
        "templates/research-repo/analysis/runs/README.md",
        "templates/research-repo/analysis/cache/AGENTS.md",
        "templates/research-repo/analysis/cache/README.md",
        "templates/research-repo/analysis/features/AGENTS.md",
        "templates/research-repo/analysis/features/README.md",
        "templates/research-repo/analysis/features/_templates/brief.md",
        "templates/research-repo/analysis/features/_templates/evidence.csv",
        "templates/research-repo/analysis/features/_templates/feature-candidates.csv",
        "templates/research-repo/analysis/features/_templates/findings.md",
        "templates/research-repo/analysis/features/_templates/open-questions.md",
        "templates/research-repo/analysis/features/_templates/review.md",
        "templates/research-repo/analysis/queue/README.md",
        "templates/research-repo/analysis/queue/review-checklist.md",
        "templates/research-repo/analysis/queue/runs/README.md",
        "templates/research-repo/analysis/queue/task-schema.md",
        "templates/research-repo/analysis/queue/tasks.jsonl",
        "templates/research-repo/analysis/queue/worker-prompt.md",
        "templates/research-repo/outputs/AGENTS.md",
        "templates/research-repo/outputs/README.md",
        "templates/research-repo/outputs/review/README.md",
        "templates/research-repo/outputs/open-questions.csv",
        "templates/research-repo/outputs/infobase-questions.csv",
        "templates/research-repo/scripts/queue/claim_next_analysis_task.py",
        "templates/research-repo/scripts/queue/get_next_analysis_task.py",
        "templates/research-repo/scripts/queue/set_analysis_task_status.py",
        "templates/research-repo/scripts/csv_page.py",
        "templates/research-repo/scripts/parallel_research.py",
        "templates/research-repo/scripts/research_task_context.py",
        "templates/research-repo/scripts/manual_cleanup_probe_row.py",
        "templates/research-repo/scripts/manual_cleanup_apply_decisions.py",
        "templates/research-repo/scripts/checks/test_research_repo.py",
        "templates/research-repo/.agents/skills/1c-autoresearch-queue-worker/SKILL.md",
        "templates/research-repo/.agents/skills/1c-autoresearch-research-goal/SKILL.md",
        "templates/research-repo/.agents/skills/1c-autoresearch-review-preparation-goal/SKILL.md",
        "templates/research-repo/.agents/skills/1c-autoresearch-manual-markup-goal/SKILL.md",
        "templates/research-repo/.agents/skills/1c-autoresearch-functional-gap-goal/SKILL.md",
        "templates/research-repo/.agents/skills/1c-autoresearch-parallel-research-goal/SKILL.md",
        "scripts/doctor.py",
        "scripts/bootstrap/new_research_repo.py",
        "scripts/checks/test_template.py",
        "scripts/checks/test_doctor.py",
        "scripts/checks/test_research_repo.py",
    ]
    for relative in required_paths:
        require_path(root, relative, errors)
    require(not git_check_ignored(root, "templates/research-repo/analysis/runs/README.md"), "Path is unexpectedly git-ignored: templates/research-repo/analysis/runs/README.md", errors)
    require(not git_check_ignored(root, "templates/research-repo/analysis/queue/runs/README.md"), "Path is unexpectedly git-ignored: templates/research-repo/analysis/queue/runs/README.md", errors)
    require_text(root, "templates/research-repo/.gitignore", r"(?m)^\*\.jsonl\.lock$", "Generated research repo should ignore queue lock files.", errors)
    require_text(root, "templates/research-repo/analysis/queue/task-schema.md", "## Optional Fields", "Queue schema should document optional task fields.", errors)
    require_text(root, "templates/research-repo/analysis/queue/tasks.jsonl", r"analysis/features/initial-discovery/evidence\.csv", "Initial discovery task should expect the canonical evidence pack files.", errors)
    require_text(root, "templates/research-repo/docs/agent/verification.md", r"1c-mcp\.example\.toml", "Generated verification docs should explain how to promote the example MCP manifest.", errors)
    require_text(root, "docs/agent/verification.md", r"python -m one_c_autoresearch doctor --json --deep --strict", "Template verification runbook should expose the strict Python doctor gate used by CI.", errors)
    require_text(root, "examples/do-gap-analysis-minimal/README.md", "--rlm-target-cf", "Minimal example should produce a research repo without empty RLM warnings.", errors)
    require_text(root, "templates/research-repo/analysis/queue/worker-prompt.md", r"Run `python -m one_c_autoresearch doctor` before updating the task status", "Queue worker prompt should verify before marking a task complete.", errors)
    require_text(root, "templates/research-repo/.agents/skills/1c-autoresearch-queue-worker/SKILL.md", r"python -m one_c_autoresearch doctor", "Queue worker skill should verify before marking a task complete.", errors)
    require_text(root, "templates/research-repo/analysis/features/_templates/evidence.csv", r"^feature_id,claim_id,source_kind,source_path,line_start,line_end,evidence_type,confidence,summary,notes$", "Evidence CSV template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/features/_templates/feature-candidates.csv", r"^feature_id,title,source_bucket,classification,confidence,summary,next_step$", "Feature candidate CSV template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/indexes/diff-inventory.csv", r"^diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes$", "Diff inventory template should expose the canonical autopilot header.", errors)
    require_text(root, "templates/research-repo/analysis/indexes/feature-map.csv", r"^feature_id,title,domain,source_bucket,classification,confidence,status,owner,summary,evidence_pack_path,open_questions_path,outputs,notes$", "Feature map template should expose the canonical autopilot header.", errors)
    require_text(root, "templates/research-repo/analysis/indexes/final-diff-inventory.csv", rf"^{re.escape(FINAL_DIFF_INVENTORY_HEADER)}$", "Final diff inventory template should expose the canonical final-gate header.", errors)
    require_text(root, "templates/research-repo/analysis/indexes/final-feature-map.csv", r"^feature_id,title,domain,source_bucket,classification,confidence,status,owner,summary,evidence_pack_path,open_questions_path,outputs,notes$", "Final feature map template should expose the canonical final-gate header.", errors)
    require_text(root, "docs/method/autopilot-customization-map.md", r"physical-clean-comparison\.md", "Autopilot runbook should route physical cleanup through the clean-comparison contract.", errors)
    require_text(root, "docs/method/autopilot-customization-map.md", r"subject-card validate", "Autopilot runbook should require subject-card validation before the analyst dashboard.", errors)
    require_text(root, "docs/method/autopilot-customization-map.md", r"contour-draft", "Autopilot runbook should require subject-card contour drafting before classification.", errors)
    require_text(root, "docs/agent/verification.md", r"subject_cards", "Verification runbook should include subject-card dashboard readiness checks.", errors)
    require_text(root, "docs/method/physical-clean-comparison.md", r"outputs/clean-comparison-dashboard", "Physical clean-comparison runbook should require an intermediate analyst dashboard.", errors)
    require_text(root, "docs/method/physical-clean-comparison.md", r"self-contained HTML", "Physical clean-comparison dashboard should be self-contained.", errors)
    require_text(root, "templates/research-repo/analysis/clean-comparison/README.md", r"physical-clean-comparison\.md", "Generated clean-comparison README should point to the method contract.", errors)
    require_text(root, "templates/research-repo/outputs/open-questions.csv", r"^question_id,feature_id,status,reason,closure_method,impact,source_ref,owner,notes$", "Open questions template should expose the canonical autopilot header.", errors)
    require_text(root, "templates/research-repo/outputs/infobase-questions.csv", rf"^{re.escape(INFOBASE_QUESTIONS_HEADER)}$", "Infobase questions template should expose the canonical autopilot header.", errors)
    require_text(root, "templates/research-repo/analysis/reverse-map/infobase-checks.csv", r"^check_id,item_id,workitem_id,diff_id,scenario_id,feature_id,subject_card_slug,question_ref,check_method,custom_target,vendor_target,query_or_probe,custom_result,vendor_result,result,status_before_pass,status_after_pass,evidence_ref,checked_by,checked_at,notes$", "Reverse-map infobase checks template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/detail-maps/README.md", r"detail-map\.json", "Detail-map README should document the detail-map.json contract.", errors)
    require_text(root, "templates/research-repo/analysis/detail-maps/README.md", r"detail-map build", "Detail-map README should document the reproducible builder command.", errors)
    require_text(root, "templates/research-repo/analysis/detail-maps/index.csv", r"^slug,title,type,status,confidence,owner_feature,linked_features,source_rows,detail_map_path,generation_mode,completeness,notes$", "Detail-map index template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/detail-maps/_templates/detail-map.json", r'"linked_features"', "Detail-map template should expose linked_features.", errors)
    require_text(root, "templates/research-repo/analysis/detail-maps/_templates/detail-map.json", r'"generation_mode"', "Detail-map template should expose generation_mode.", errors)
    require_text(root, "templates/research-repo/analysis/subject-cards/candidates.csv", r"^candidate_id,title,proposed_slug,source,discovery_basis,linked_features,linked_detail_maps,primary_objects,subject_type,confidence,proposed_action,status,notes$", "Subject-card candidates template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/subject-cards/contours.csv", rf"^{re.escape(SUBJECT_CARD_CONTOURS_HEADER)}$", "Subject-card contours template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/subject-cards/classification.csv", r"^candidate_id,proposed_slug,decision,subject_type,registry_slug,merge_into,split_from,why_separate_card,status,confidence,notes$", "Subject-card classification template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/subject-cards/registry.csv", r"^slug,title,subject_type,status,confidence,origin_layer,owner_feature,linked_features,linked_detail_maps,primary_objects,coverage_scope,why_separate_card,merge_into,split_from,card_path,evidence_count,gap_count,review_notes$", "Subject-card registry template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/subject-cards/coverage.csv", r"^source_kind,source_id,feature_id,detail_map_slug,subject_card_slug,relation,confidence,notes$", "Subject-card coverage template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/subject-cards/_templates/subject-card.json", r'"schema_version": "subject-card/v1"', "Subject-card JSON template should expose schema_version.", errors)
    require_text(root, "templates/research-repo/analysis/functional-gaps/index.csv", r"^subject_card_slug,title,status,gap_readiness,hypotheses_count,open_checks_count,gap_card_path,selected_decision,review_notes$", "Functional-gap index template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/functional-gaps/coverage.csv", r"^subject_card_slug,gap_card_status,gap_readiness,gap_card_path,selected_decision,open_checks_count,notes$", "Functional-gap coverage template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/functional-gaps/open-questions.csv", r"^question_id,subject_card_slug,check_id,question,needed_source,blocking,status,notes$", "Functional-gap open questions template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/functional-gaps/_templates/gap-card.json", r'"schema_version": "functional-gap-card/v2"', "Functional-gap JSON template should expose schema_version.", errors)
    require_text(root, "templates/research-repo/analysis/functional-gaps/_templates/target-profile.toml", r"^profile_id = \"target\"", "Functional-gap target profile template should expose profile_id.", errors)
    require_text(root, "templates/research-repo/analysis/functional-gaps/README.md", r"target_profile", "Functional-gap README should document explicit target profiles.", errors)
    require_text(root, "templates/research-repo/analysis/reverse-map/coverage.csv", rf"^{re.escape(REVERSE_MAP_COVERAGE_HEADER)}$", "Reverse-map coverage template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/project.toml", r"(?m)^\[autopilot\]$", "Generated manifest should include the autopilot final-gate section.", errors)
    require_text(root, "templates/research-repo/docs/agent/index.md", r"reverse-map claim", "Agent router should document the reverse-map continuation command.", errors)
    require_text(root, "templates/research-repo/AGENTS.md", r"analysis/reverse-map", "Generated AGENTS should identify reverse-map state as source of truth.", errors)
    require_text(root, "templates/research-repo/README.md", r"reverse-map", "Generated README should expose reverse-map continuation commands.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"reverse-map", "CLI should expose a reverse-map command group.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"final-gate", "CLI should expose a final-gate command group.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"review-dashboard", "CLI should expose a review-dashboard command group.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"detail-map", "CLI should expose a detail-map command group.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"subject-card", "CLI should expose a subject-card command group.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"functional-gap", "CLI should expose a functional-gap command group.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"dashboard-build", "CLI should expose a functional-gap dashboard build command.", errors)
    require_text(root, "templates/research-repo/outputs/review/README.md", r"review-dashboard build", "Generated outputs/review README should document the dashboard build command.", errors)
    require_text(root, "docs/method/autopilot-customization-map.md", r"Coverage status: complete", "Autopilot runbook should document the final audit coverage marker.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"autopilot", "CLI should expose an autopilot command group.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"configuration-source", "CLI should expose a configuration-source parser command group.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"custom-metadata", "CLI should expose a custom metadata inventory command group.", errors)
    require_same_content(root, "docs/method/1c-autoresearch-process.md", "templates/research-repo/docs/method/1c-autoresearch-process.md", "Generated research methodology must match the template system-of-record document.", errors)
    require_same_content(root, "docs/method/evidence-pack-schema.md", "templates/research-repo/docs/method/evidence-pack-schema.md", "Generated evidence pack schema must match the template system-of-record document.", errors)
    require_same_content(root, "docs/method/autopilot-customization-map.md", "templates/research-repo/docs/method/autopilot-customization-map.md", "Generated autopilot runbook must match the template system-of-record document.", errors)
    require_same_content(root, "docs/method/physical-clean-comparison.md", "templates/research-repo/docs/method/physical-clean-comparison.md", "Generated physical clean-comparison runbook must match the template system-of-record document.", errors)
    require_same_content(root, "docs/method/reverse-functional-map.md", "templates/research-repo/docs/method/reverse-functional-map.md", "Generated reverse-map runbook must match the template system-of-record document.", errors)
    for method in ("research-goal-router.md", "research-review-preparation.md", "manual-markup-goal.md", "functional-gap-goal.md", "parallel-research-goal.md", "customization-registry-bootstrap.md", "migration-requirement-contour.md"):
        require_same_content(root, f"docs/method/{method}", f"templates/research-repo/docs/method/{method}", f"Generated method must match template source: {method}", errors)
    try:
        read_jsonl(repo_path(root, "templates/research-repo/analysis/queue/tasks.jsonl"))
    except Exception as exc:
        errors.append(f"Queue JSONL should parse: {exc}")
    test_template_portability(root, errors)
    if errors:
        raise CheckFailure("\n".join(f"- {error}" for error in errors))
    print(f"Template validation passed: {root}")
    return 0


def test_research_repo(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    errors: list[str] = []
    test_review_dashboard_subject_bf_metrics(root, errors)
    test_review_dashboard_language(root, errors)
    test_v8unpack_refinement_layer(root, errors)
    test_v8unpack_autopilot_alignment(root, errors)
    test_configuration_source_parser_snapshots(root, errors)
    test_custom_metadata_inventory(root, errors)
    test_customer_customization_register(root, errors)
    test_detailed_customer_customization_register(root, errors)
    test_detailed_register_reverse_review(root, errors)
    test_customization_registry_contract(root, errors)
    if migration_requirements_active(root):
        result = validate_migration_requirements(root)
        require(result["status"] == "ok", f"migration-requirement validation should pass: {result}", errors)
    doctor = run_doctor(root, mode="research")
    if doctor["status"] == "fail":
        print(json.dumps(doctor, ensure_ascii=False, indent=2))
        return 1
    if errors:
        raise CheckFailure("\n".join(f"- {error}" for error in errors))
    print(f"Research repo validation passed: {root}")
    return 0


def test_v8unpack_refinement_layer(root: Path, errors: list[str]) -> None:
    if not repo_path(root, "analysis/v8unpack-cleanup/object-queue.csv").exists():
        return
    errors.extend(validate_refinement_artifacts(root))


def test_v8unpack_autopilot_alignment(root: Path, errors: list[str]) -> None:
    if not repo_path(root, "analysis/v8unpack-refinement/summary.json").exists():
        return
    errors.extend(validate_source_alignment(root))
    data_path = repo_path(root, "outputs/review/data.json")
    if data_path.exists():
        data = json.loads(data_path.read_text(encoding="utf-8"))
        source_alignment = data.get("source_alignment") or {}
        summary = data.get("summary") or {}
        if source_alignment.get("canonical_source") != "v8unpack-refinement":
            errors.append("Review dashboard data lacks v8unpack-refinement source_alignment marker")
        for key in ("canonical_v8unpack_rows", "xml_support_rows", "form_template_mixed_rows", "manual_review_rows"):
            if key not in summary:
                errors.append(f"Review dashboard summary lacks source alignment count: {key}")


def test_configuration_source_parser_snapshots(root: Path, errors: list[str]) -> None:
    snapshot_dir = repo_path(root, DEFAULT_SNAPSHOT_DIR)
    if not snapshot_dir.exists():
        return
    snapshots = sorted(snapshot_dir.glob("*.snapshot.json"))
    if not snapshots:
        errors.append(f"{DEFAULT_SNAPSHOT_DIR} exists but contains no parser snapshots")
        return
    for snapshot_path in snapshots:
        try:
            snapshot = read_snapshot(snapshot_path)
        except Exception as exc:
            errors.append(f"Configuration source snapshot does not parse: {snapshot_path.relative_to(root).as_posix()}: {exc}")
            continue
        for error in validate_snapshot(snapshot):
            errors.append(f"{snapshot_path.relative_to(root).as_posix()}: {error}")


def test_custom_metadata_inventory(root: Path, errors: list[str]) -> None:
    inventory_dir = repo_path(root, DEFAULT_INVENTORY_DIR)
    if not inventory_dir.exists():
        return
    errors.extend(validate_inventory(root, inventory_dir, strict_reconciliation=True))


def test_detailed_customer_customization_register(root: Path, errors: list[str]) -> None:
    if not repo_path(root, DETAILED_REGISTER_CSV).exists():
        return
    result = validate_detailed_customer_register(root)
    errors.extend(result.get("errors") or [])


def test_customer_customization_register(root: Path, errors: list[str]) -> None:
    if not repo_path(root, "outputs/customer-customization-register.csv").exists():
        return
    result = validate_customer_register(root)
    if result["status"] != "ok":
        errors.extend(str(error) for error in result.get("errors", []))


def test_detailed_register_reverse_review(root: Path, errors: list[str]) -> None:
    if not repo_path(root, MARKUP_CSV).exists():
        return
    result = validate_reverse_review(root, mode="draft")
    errors.extend(result.get("errors") or [])


def test_customization_registry_contract(root: Path, errors: list[str]) -> None:
    if repo_path(root, "analysis/customization-registry/customization-items.jsonl").exists():
        result = validate_customization_registry(root)
        require(result["status"] == "ok", f"customization-registry validation should pass: {result}", errors)
    require_text(root, "docs/agent/verification.md", r"customization-registry validate", "Verification runbook should include customization-registry validation.", errors)
    require_text(root, "docs/method/customization-registry-bootstrap.md", r"customization-registry bootstrap", "Customization registry bootstrap runbook should document the bootstrap command.", errors)


def test_review_dashboard_subject_bf_metrics(root: Path, errors: list[str]) -> None:
    data_path = repo_path(root, "outputs/review/data.json")
    if not data_path.exists():
        return
    data = json.loads(data_path.read_text(encoding="utf-8"))
    summary = data.get("summary", {})
    required_keys = {
        "subject_bf_unique_count",
        "subject_bf_unique_covered_count",
        "subject_bf_unique_unclassified_count",
    }
    missing = sorted(key for key in required_keys if key not in summary)
    require(not missing, f"Review dashboard summary should expose unique BF subject-card coverage metrics: {', '.join(missing)}", errors)
    if not missing:
        unique_count = int(summary.get("subject_bf_unique_count") or 0)
        covered_count = int(summary.get("subject_bf_unique_covered_count") or 0)
        unclassified_count = int(summary.get("subject_bf_unique_unclassified_count") or 0)
        feature_count = int(summary.get("feature_count") or 0)
        require(unique_count <= feature_count, "Unique subject-card BF coverage count should not exceed feature_count", errors)
        require(covered_count + unclassified_count <= unique_count, "Covered and unclassified unique BF metrics should not double-count the same BF", errors)


DASHBOARD_LANGUAGE_FORBIDDEN = (
    "Runtime-проверки",
    "runtime-проверки",
    "runtime-проверок",
    "runtime-проверка",
    "runtime-вопрос",
    "runtime CSV",
    "UI-поверхность",
    "UI-поверхности",
    "UI-проверка",
    "UI-проверки",
    "UI/runtime",
    "web UI",
    "clean diff",
    "generated detail maps",
    "detail maps",
    "subject cards",
    "subject card",
    "candidate/unclassified/supporting/technical",
    "Merged / rejected / supporting",
    "split/merge",
    "Evidence",
    "evidence rows",
    "gaps",
    "Upgrade-риск",
    "final-gate",
    "smoke-test",
    "ServerCall",
    "startup/session",
    "read-only",
    "production-",
)

DASHBOARD_TECH_VALUE_KEYS = {
    "autopilot_enabled",
    "baseline_version",
    "card_href",
    "card_source_href",
    "check_method",
    "classification",
    "confidence",
    "dashboard_scope",
    "diff_id",
    "evidence_pack_href",
    "evidence_pack_path",
    "evidence_ref",
    "evidence_type",
    "feature_id",
    "generated_at",
    "href",
    "id",
    "item_id",
    "line",
    "line_end",
    "line_start",
    "mode",
    "next_vendor_version",
    "origin_layer",
    "owner_feature",
    "path",
    "scenario_id",
    "scenario_summary_href",
    "scenario_summary_path",
    "schema_version",
    "slug",
    "source",
    "source_artifacts",
    "source_bucket",
    "source_counts",
    "source_href",
    "source_kind",
    "source_mode",
    "source_path",
    "source_workbook",
    "source_workbook_href",
    "status",
    "status_after_pass",
    "subject_card",
    "subject_card_slug",
    "subject_type",
    "target_version",
    "type",
}


def dashboard_visible_text(html: str) -> str:
    body = re.sub(r"(?is)<script\b.*?</script>", " ", html)
    body = re.sub(r"(?is)<style\b.*?</style>", " ", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    return html_lib.unescape(re.sub(r"\s+", " ", body))


def has_artifact_path(value: str) -> bool:
    return bool(re.search(r"(?:analysis|outputs|sources|work|scenarios)/[^\s;,]+", value))


def collect_dashboard_language_hits(value: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in DASHBOARD_TECH_VALUE_KEYS:
                continue
            hits.extend(collect_dashboard_language_hits(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(collect_dashboard_language_hits(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        if has_artifact_path(value):
            return hits
        for term in DASHBOARD_LANGUAGE_FORBIDDEN:
            if term in value:
                hits.append(f"{path}: {term}")
    return hits


def test_review_dashboard_language(root: Path, errors: list[str]) -> None:
    html_path = repo_path(root, "outputs/review/index.html")
    data_path = repo_path(root, "outputs/review/data.json")
    visible_hits: list[str] = []
    data_hits: list[str] = []
    if html_path.exists():
        visible = dashboard_visible_text(html_path.read_text(encoding="utf-8"))
        visible_hits = [term for term in DASHBOARD_LANGUAGE_FORBIDDEN if term in visible]
    if data_path.exists():
        data = json.loads(data_path.read_text(encoding="utf-8"))
        data_hits = collect_dashboard_language_hits(data)
    require(not visible_hits, f"Review dashboard visible text should use Russian 1C-friendly terms; found: {', '.join(visible_hits[:12])}", errors)
    require(not data_hits, f"Review dashboard data should not expose English/internal terms in human fields; found: {', '.join(data_hits[:12])}", errors)


def test_doctor(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    result = run_doctor(root)
    if result["status"] == "fail":
        raise CheckFailure(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Doctor validation passed: {root}")
    return 0


def run_check(fn, args: argparse.Namespace) -> int:
    try:
        return fn(args)
    except CheckFailure as exc:
        print(str(exc), file=sys.stderr)
        return 1
