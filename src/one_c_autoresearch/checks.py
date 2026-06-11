from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .autopilot import (
    DIFF_INVENTORY_HEADER,
    FEATURE_MAP_HEADER,
    FINAL_DIFF_INVENTORY_HEADER,
    OPEN_QUESTIONS_HEADER,
    scaffold_autopilot,
    write_minimal_xlsx,
)
from .bootstrap import create_research_repo
from .common import current_module_command, git_check_ignored, read_jsonl, repo_path
from .doctor import run_doctor
from .final_gate import build_final_gate
from .functional_gap_dashboard import build_functional_gap_dashboard
from .functional_gaps import build_functional_gap_card, build_functional_gap_map, inspect_target_for_functional_gap, refresh_functional_gap_card, validate_functional_gaps
from .queue import claim_next_task, set_task_status
from .reverse_map import (
    REVERSE_MAP_COVERAGE_HEADER,
    get_next_reverse_map_workitem,
    scaffold_reverse_map,
    seed_reverse_map_workitems,
)
from .subject_cards import (
    SUBJECT_CARD_REGISTRY_HEADER,
    build_subject_card_registry,
    classify_subject_cards,
    discover_subject_cards,
    read_csv_rows as read_subject_csv_rows,
    seed_subject_cards,
    write_csv_rows as write_subject_csv_rows,
)


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
        "src/one_c_autoresearch/queue.py",
        "src/one_c_autoresearch/checks.py",
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
        "templates/research-repo/docs/method/reverse-functional-map.md",
        "templates/research-repo/analysis/indexes/README.md",
        "templates/research-repo/analysis/indexes/diff-inventory.csv",
        "templates/research-repo/analysis/indexes/feature-map.csv",
        "templates/research-repo/analysis/indexes/final-diff-inventory.csv",
        "templates/research-repo/analysis/indexes/final-feature-map.csv",
        "templates/research-repo/analysis/detail-maps/README.md",
        "templates/research-repo/analysis/detail-maps/index.csv",
        "templates/research-repo/analysis/detail-maps/_templates/detail-map.json",
        "templates/research-repo/analysis/subject-cards/README.md",
        "templates/research-repo/analysis/subject-cards/candidates.csv",
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
        "templates/research-repo/scripts/queue/claim_next_analysis_task.py",
        "templates/research-repo/scripts/queue/get_next_analysis_task.py",
        "templates/research-repo/scripts/queue/set_analysis_task_status.py",
        "templates/research-repo/scripts/checks/test_research_repo.py",
        "templates/research-repo/.agents/skills/1c-autoresearch-queue-worker/SKILL.md",
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
    require_text(root, "templates/research-repo/outputs/open-questions.csv", r"^question_id,feature_id,status,reason,closure_method,impact,source_ref,owner,notes$", "Open questions template should expose the canonical autopilot header.", errors)
    require_text(root, "templates/research-repo/analysis/detail-maps/README.md", r"detail-map\.json", "Detail-map README should document the detail-map.json contract.", errors)
    require_text(root, "templates/research-repo/analysis/detail-maps/README.md", r"detail-map build", "Detail-map README should document the reproducible builder command.", errors)
    require_text(root, "templates/research-repo/analysis/detail-maps/index.csv", r"^slug,title,type,status,confidence,owner_feature,linked_features,source_rows,detail_map_path,generation_mode,completeness,notes$", "Detail-map index template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/analysis/detail-maps/_templates/detail-map.json", r'"linked_features"', "Detail-map template should expose linked_features.", errors)
    require_text(root, "templates/research-repo/analysis/detail-maps/_templates/detail-map.json", r'"generation_mode"', "Detail-map template should expose generation_mode.", errors)
    require_text(root, "templates/research-repo/analysis/subject-cards/candidates.csv", r"^candidate_id,title,proposed_slug,source,discovery_basis,linked_features,linked_detail_maps,primary_objects,subject_type,confidence,proposed_action,status,notes$", "Subject-card candidates template should expose the canonical header.", errors)
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
    require_same_content(root, "docs/method/1c-autoresearch-process.md", "templates/research-repo/docs/method/1c-autoresearch-process.md", "Generated research methodology must match the template system-of-record document.", errors)
    require_same_content(root, "docs/method/evidence-pack-schema.md", "templates/research-repo/docs/method/evidence-pack-schema.md", "Generated evidence pack schema must match the template system-of-record document.", errors)
    require_same_content(root, "docs/method/autopilot-customization-map.md", "templates/research-repo/docs/method/autopilot-customization-map.md", "Generated autopilot runbook must match the template system-of-record document.", errors)
    require_same_content(root, "docs/method/reverse-functional-map.md", "templates/research-repo/docs/method/reverse-functional-map.md", "Generated reverse-map runbook must match the template system-of-record document.", errors)
    try:
        read_jsonl(repo_path(root, "templates/research-repo/analysis/queue/tasks.jsonl"))
    except Exception as exc:
        errors.append(f"Queue JSONL should parse: {exc}")
    if errors:
        raise CheckFailure("\n".join(f"- {error}" for error in errors))
    print(f"Template validation passed: {root}")
    return 0


def test_research_repo(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    errors: list[str] = []
    test_subject_card_registry_seed_contract(errors)
    test_functional_gap_one_card_contract(errors)
    test_review_dashboard_subject_bf_metrics(root, errors)
    test_review_dashboard_language(root, errors)
    doctor = run_doctor(root, mode="research")
    if doctor["status"] == "fail":
        print(json.dumps(doctor, ensure_ascii=False, indent=2))
        return 1
    if errors:
        raise CheckFailure("\n".join(f"- {error}" for error in errors))
    print(f"Research repo validation passed: {root}")
    return 0


def test_subject_card_registry_seed_contract(errors: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="1c-subject-registry-seed-") as temp_dir:
        root = Path(temp_dir)
        repo_path(root, "analysis/indexes").mkdir(parents=True)
        repo_path(root, "analysis/features/BF-002").mkdir(parents=True)
        repo_path(root, "analysis/indexes/final-feature-map.csv").write_text(
            "feature_id,title,classification,confidence,source_bucket,evidence_pack_path,status\n"
            "BF-002,Проверочный кандидат,confirmed business feature,high,clean-rebase:1,analysis/features/BF-002,complete\n",
            encoding="utf-8",
        )
        repo_path(root, "analysis/features/BF-002/evidence.csv").write_text(
            "claim_id,source_kind,source_path,line_start,line_end,evidence_type,confidence,summary,notes\n"
            "E-1,target_cf,path,1,2,static,high,Проверочное доказательство,\n",
            encoding="utf-8",
        )
        discover_subject_cards(root)
        classify_subject_cards(root)
        build_subject_card_registry(root)
        registry_path = repo_path(root, "analysis/subject-cards/registry.csv")
        registry_rows = read_subject_csv_rows(registry_path)
        if not registry_rows:
            errors.append("Subject-card registry seed smoke should create a registry candidate row")
            return
        registry_rows[0]["status"] = "accepted"
        registry_rows[0]["coverage_scope"] = "covered"
        registry_rows[0]["why_separate_card"] = "Проверочная строка registry принята аналитиком."
        write_subject_csv_rows(registry_path, SUBJECT_CARD_REGISTRY_HEADER, registry_rows)
        result = seed_subject_cards(root, from_registry=True)
        card_path = repo_path(root, "analysis/subject-cards/cards/bf-002-proverochnyy-kandidat/subject-card.json")
        require(card_path.exists(), f"subject-card seed --from-registry should create a card from accepted registry rows; result={result}", errors)


def test_functional_gap_one_card_contract(errors: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="1c-functional-gap-card-") as temp_dir:
        root = Path(temp_dir)
        repo_path(root, "analysis/subject-cards/cards/example-document").mkdir(parents=True)
        repo_path(root, "project.toml").write_text(
            "[project]\n"
            "project_id = \"test\"\n"
            "product = \"1C:Документооборот\"\n"
            "next_vendor_version = \"ДО 3.0\"\n\n"
            "[paths]\n"
            "next_vendor = \"sources/next_vendor\"\n\n"
            "[rlm]\n"
            "next_vendor = \"do_next_vendor\"\n",
            encoding="utf-8",
        )
        repo_path(root, "analysis/subject-cards/cards/example-document/subject-card.json").write_text(
            json.dumps(
                {
                    "schema_version": "subject-card/v1",
                    "slug": "example-document",
                    "title": "Проверочная предметная карточка",
                    "subject_type": "business_process",
                    "status": "ready_for_review",
                    "confidence": "medium",
                    "linked_features": ["BF-000"],
                    "linked_detail_maps": ["analysis/detail-maps/cards/example/detail-map.json"],
                    "primary_objects": ["Документ.ПроверочныйДокумент"],
                    "source_artifacts": ["analysis/subject-cards/cards/example-document/subject-card.json"],
                    "runtime_data_needed": "Проверить наличие исторических документов.",
                    "summary": "Проверочная доработка внутреннего документа.",
                    "key_conclusion": "Нужна проверка, закрывает ли новый релиз типовой сценарий.",
                    "upgrade_risk": "Может потребоваться адаптация маршрута.",
                    "sections": {
                        "business_purpose": [
                            {
                                "text": "Поддерживает внутренний документ.",
                                "source": "analysis/features/BF-000/findings.md",
                            }
                        ],
                        "implementation": [],
                        "checks": [],
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        repo_path(root, "analysis/subject-cards/cards/example-document/evidence.csv").write_text(
            "evidence_id,section,claim,source_type,source_path,line,linked_diff_id,linked_feature_id,confidence,notes\n"
            "E-1,business_purpose,Поддерживает внутренний документ.,static,analysis/features/BF-000/findings.md,1,,BF-000,medium,\n",
            encoding="utf-8",
        )
        repo_path(root, "analysis/subject-cards/cards/example-document/gaps.csv").write_text(
            "gap_id,section,question,needed_source,status,blocking,notes\n",
            encoding="utf-8",
        )
        repo_path(root, "sources/next_vendor/Documents").mkdir(parents=True)
        repo_path(root, "sources/next_vendor/Documents/ПроверочныйДокумент.xml").write_text(
            "<Meta>Документ.ПроверочныйДокумент</Meta>\n",
            encoding="utf-8",
        )

        result = build_functional_gap_card(root, "example-document")
        require(result["status"] == "ok", f"functional-gap build should succeed for one card; result={result}", errors)
        refresh_result = refresh_functional_gap_card(root, "example-document")
        require(refresh_result["status"] == "ok", f"functional-gap refresh should preserve and update one card; result={refresh_result}", errors)
        inspect_result = inspect_target_for_functional_gap(root, "example-document")
        require(inspect_result["status"] == "ok", f"functional-gap inspect-target should succeed; result={inspect_result}", errors)

        gap_dir = repo_path(root, "analysis/functional-gaps/cards/example-document")
        gap_path = repo_path(root, "analysis/functional-gaps/cards/example-document/gap-card.json")
        require(gap_path.exists(), "functional-gap build should create gap-card.json", errors)
        require(repo_path(root, "analysis/functional-gaps/cards/example-document/hypotheses.csv").exists(), "functional-gap build should create hypotheses.csv", errors)
        require(repo_path(root, "analysis/functional-gaps/cards/example-document/checks.csv").exists(), "functional-gap build should create checks.csv", errors)
        require(repo_path(root, "analysis/functional-gaps/cards/example-document/target-findings.csv").exists(), "functional-gap build should create target-findings.csv", errors)
        require(repo_path(root, "analysis/functional-gaps/cards/example-document/object-mapping.csv").exists(), "functional-gap build should create object-mapping.csv", errors)
        require(repo_path(root, "analysis/functional-gaps/cards/example-document/behavior-probes.csv").exists(), "functional-gap build should create behavior-probes.csv", errors)
        require(repo_path(root, "analysis/functional-gaps/cards/example-document/review.md").exists(), "functional-gap build should create review.md", errors)
        if not gap_path.exists():
            return
        payload = json.loads(gap_path.read_text(encoding="utf-8"))
        require(payload.get("schema_version") == "functional-gap-card/v2", "functional-gap card should expose schema_version", errors)
        require(payload.get("subject_card_slug") == "example-document", "functional-gap card should point to exactly one subject card", errors)
        require(payload.get("gap_readiness") == "needs_target_release_check", "ready subject cards should move to target-release checks", errors)
        require(payload.get("inputs", {}).get("next_vendor_path") == "sources/next_vendor", "functional-gap card should expose target source path", errors)
        require(any(item.get("gap_type") == "replace_by_standard" for item in payload.get("hypotheses", [])), "functional-gap card should include replacement-by-standard hypothesis", errors)
        require(any(item.get("check_type") == "target_release_static" for item in payload.get("required_checks", [])), "functional-gap card should include target release static check", errors)
        require(payload.get("counts", {}).get("target_findings") == 1, "functional-gap inspect-target should count target findings", errors)
        require(not repo_path(root, "analysis/functional-gaps/cards/other-card").exists(), "functional-gap build must not create unrelated card directories", errors)

        validation = validate_functional_gaps(root, card="example-document")
        require(validation["status"] == "ok", f"functional-gap validation should pass; validation={validation}; gap_dir={gap_dir}", errors)
        map_result = build_functional_gap_map(root)
        require(map_result["status"] == "ok", f"functional-gap map-build should succeed; result={map_result}", errors)
        require(repo_path(root, "outputs/functional-gap-map.md").exists(), "functional-gap map-build should create markdown output", errors)
        require(repo_path(root, "outputs/functional-gap-map.json").exists(), "functional-gap map-build should create json output", errors)
        dashboard_result = build_functional_gap_dashboard(root)
        require(dashboard_result["status"] == "ok", f"functional-gap dashboard-build should succeed; result={dashboard_result}", errors)
        require(repo_path(root, "outputs/functional-gap-dashboard/data.json").exists(), "functional-gap dashboard-build should create data.json", errors)
        require(repo_path(root, "outputs/functional-gap-dashboard/index.html").exists(), "functional-gap dashboard-build should create index.html", errors)


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


def copy_smoke_repo(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def test_doctor(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    errors: list[str] = []
    template = run_doctor(root)
    require(template["repo_kind"] == "template", "Template doctor should detect repo_kind=template", errors)
    require(template["status"] == "ok", "Template doctor should report status=ok", errors)
    with tempfile.TemporaryDirectory(prefix="1c-autoresearch-doctor-") as temp_dir:
        base = Path(temp_dir)
        temp_repo = base / "smoke"
        create_research_repo(
            argparse.Namespace(
                template_root=str(root),
                target_path=str(temp_repo),
                project_id="smoke",
                product="1C Smoke",
                baseline_version="2.1",
                target_version="2.1",
                next_vendor_version="3.0",
                vendor_baseline="E:/Projects/vendor",
                target_cf="E:/Projects/customer/cf",
                target_cfe="E:/Projects/customer/cfe",
                next_vendor="E:/Projects/vendor30",
                rlm_vendor_baseline="vendor",
                rlm_target_cf="customer_cf",
                rlm_target_cfe="customer_cfe",
                rlm_next_vendor="vendor30",
                init_git=False,
                force=False,
            )
        )
        for relative in (
            "scripts/doctor.py",
            ".codex/1c-mcp.example.toml",
            "analysis/runs/README.md",
            "analysis/indexes/README.md",
            "analysis/indexes/diff-inventory.csv",
            "analysis/indexes/feature-map.csv",
            "analysis/reverse-map/README.md",
            "analysis/reverse-map/state.md",
            "analysis/reverse-map/coverage.csv",
            "analysis/reverse-map/workitems.jsonl",
            "analysis/reverse-map/decisions.csv",
            "analysis/reverse-map/unresolved.csv",
            "analysis/reverse-map/scenarios/README.md",
            "analysis/reverse-map/outputs/README.md",
            "analysis/cache/AGENTS.md",
            "analysis/features/AGENTS.md",
            "analysis/features/_templates/evidence.csv",
            "analysis/queue/runs/README.md",
            "outputs/open-questions.csv",
            "outputs/AGENTS.md",
            "scripts/queue/claim_next_analysis_task.py",
        ):
            require_path(temp_repo, relative, errors)
        generated_method = repo_path(temp_repo, "docs/method/1c-autoresearch-process.md").read_text(encoding="utf-8")
        require("## Evidence Levels" in generated_method, "Generated method docs should include evidence levels", errors)
        research = run_doctor(temp_repo)
        require(research["repo_kind"] == "research", "Embedded doctor should detect repo_kind=research", errors)
        require(research["status"] == "ok", "Embedded doctor should report status=ok without --deep", errors)
        scaffold_repo = base / "scaffold-autopilot"
        copy_smoke_repo(temp_repo, scaffold_repo)
        scaffold_autopilot(argparse.Namespace(repo_path=str(scaffold_repo), force=True, enable_gate=True))
        scaffolded = run_doctor(scaffold_repo)
        require(scaffolded["status"] == "fail", "Autopilot gate should fail on scaffold-only artifacts", errors)
        require(any(check["id"] == "autopilot.diff_inventory.rows" and check["status"] == "fail" for check in scaffolded["checks"]), "Autopilot gate should require classified diff rows", errors)

        complete_repo = base / "complete-autopilot"
        copy_smoke_repo(scaffold_repo, complete_repo)
        repo_path(complete_repo, "analysis/indexes/diff-inventory.csv").write_text(
            DIFF_INVENTORY_HEADER
            + "\nD-0001,target_cf,M,cf/Catalogs/Example.xml,Catalogs,Catalogs.Example,Documents,feature-a,covered_by_existing_customization,high,mapped_to_feature,Example catalog metadata changed,analysis/features/feature-a/evidence.csv#E-001,\n"
            + "D-0002,target_cf,M,cf/ScheduledJobs/ExampleJob.xml,ScheduledJobs,ScheduledJobs.ExampleJob,Регламентные задания,feature-a,platform automation,medium,mapped_to_feature,Example scheduled job changed,analysis/features/feature-a/evidence.csv#E-002,\n",
            encoding="utf-8",
        )
        repo_path(complete_repo, "analysis/indexes/feature-map.csv").write_text(
            FEATURE_MAP_HEADER
            + "\nfeature-a,Example feature,Documents,target_cf,covered_by_existing_customization,high,complete,codex,Example customization,analysis/features/feature-a,analysis/features/feature-a/open-questions.md,outputs/customization-map.md,\n",
            encoding="utf-8",
        )
        feature = repo_path(complete_repo, "analysis/features/feature-a")
        feature.mkdir(parents=True, exist_ok=True)
        (feature / "brief.md").write_text("# Brief\n\nExample feature.\n", encoding="utf-8")
        (feature / "findings.md").write_text("# Findings\n\n- F-001: Example behavior changed.\n", encoding="utf-8")
        (feature / "evidence.csv").write_text(
            "feature_id,claim_id,source_kind,source_path,line_start,line_end,evidence_type,confidence,summary,notes\n"
            "feature-a,F-001,target_cf,cf/Catalogs/Example.xml,1,3,metadata,high,Example catalog metadata changed,\n"
            "feature-a,F-002,target_cf,cf/ScheduledJobs/ExampleJob.xml,1,3,metadata,medium,Example scheduled job changed,\n",
            encoding="utf-8",
        )
        (feature / "open-questions.md").write_text("# Open Questions\n\nNo open questions.\n", encoding="utf-8")
        (feature / "review.md").write_text("# Review\n\nPassed.\n", encoding="utf-8")
        repo_path(complete_repo, "outputs/customization-map.md").write_text("# Customization Map\n\nCoverage status: complete\n\nFeature A is mapped.\n", encoding="utf-8")
        repo_path(complete_repo, "outputs/open-questions.csv").write_text(OPEN_QUESTIONS_HEADER + "\n", encoding="utf-8")
        write_minimal_xlsx(repo_path(complete_repo, "outputs/customization-map.xlsx"), [FEATURE_MAP_HEADER.split(","), ["feature-a", "Example feature"]], force=True)
        write_minimal_xlsx(repo_path(complete_repo, "outputs/open-questions.xlsx"), [OPEN_QUESTIONS_HEADER.split(",")], force=True)
        repo_path(complete_repo, "analysis/final-audit.md").write_text(
            "# Final Audit\n\nCoverage status: complete\n\nUnclassified diff entries: 0\n\nStarting diff entries: 1\nFinal diff entries: 1\nOpen questions: 0\n",
            encoding="utf-8",
        )
        document_map = repo_path(complete_repo, "analysis/detail-maps/example-document/detail-map.json")
        document_map.parent.mkdir(parents=True, exist_ok=True)
        document_map.write_text(
            json.dumps(
                {
                    "schema_version": "detail-map/v1",
                    "id": "example-document",
                    "slug": "example-document",
                    "title": "Пример документа",
                    "type": "document",
                    "generation_mode": "enriched",
                    "completeness": "high",
                    "status": "complete",
                    "confidence": "high",
                    "owner_feature": "feature-a",
                    "linked_features": ["feature-a"],
                    "summary": "Детальная карта документа для smoke-проверки.",
                    "identification": "Через признак тестовой предметной карты.",
                    "key_conclusion": "Предметная карта документа показывает реквизит, правило формы и источник доказательства.",
                    "upgrade_risk": "При переходе нужно сверить реквизит и правило формы с целевым релизом.",
                    "runtime_data_needed": "Нужны значения тестового справочника в ИБ.",
                    "review_status": "Готово к ревью аналитиком.",
                    "migration_notes": ["Проверить перенос реквизитов и правил формы на целевой релиз."],
                    "attributes": [
                        {
                            "object": "Catalog.Example",
                            "kind": "Attribute",
                            "name": "ExampleAttribute",
                            "synonym": "Пример реквизита",
                            "data_type": "xs:string",
                            "vendor_status": "добавлен",
                            "relation": "Ключевой реквизит документа",
                            "confidence": "high",
                            "source": "cf/Catalogs/Example.xml",
                            "line": "10",
                            "comment": "",
                        }
                    ],
                    "form_rules": [
                        {
                            "id": "FR01",
                            "rule": "Настройка формы",
                            "description": "Форма меняет доступность реквизита.",
                            "mechanism": "ПриОткрытии",
                            "confidence": "high",
                            "source": "cf/Catalogs/Example/Forms/ФормаЭлемента/Ext/Form/Module.bsl",
                            "line": "20",
                        }
                    ],
                    "validations": [],
                    "lifecycle": [],
                    "rights": [],
                    "scheduled_jobs": [],
                    "ui": [],
                    "integrations": [],
                    "sources": [],
                    "open_questions": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        route_map = repo_path(complete_repo, "analysis/detail-maps/example-route/detail-map.json")
        route_map.parent.mkdir(parents=True, exist_ok=True)
        route_map.write_text(
            json.dumps(
                {
                    "schema_version": "detail-map/v1",
                    "id": "example-route",
                    "slug": "example-route",
                    "title": "Пример маршрута",
                    "type": "route",
                    "generation_mode": "manual",
                    "completeness": "medium",
                    "status": "requires_1c_review",
                    "confidence": "medium",
                    "owner_feature": "feature-a",
                    "linked_features": ["feature-a"],
                    "summary": "Детальная карта маршрута для проверки нескольких типов.",
                    "identification": "Через тестовый маршрут согласования.",
                    "key_conclusion": "Предметная карта маршрута фиксирует условия согласования.",
                    "upgrade_risk": "При переходе нужно сверить условия маршрута в ИБ.",
                    "runtime_data_needed": "Нужны настройки маршрута из ИБ.",
                    "review_status": "Требует проверки ИБ.",
                    "migration_notes": [],
                    "attributes": [],
                    "form_rules": [],
                    "validations": [],
                    "lifecycle": [],
                    "rights": [],
                    "scheduled_jobs": [],
                    "ui": [],
                    "integrations": [],
                    "sources": [],
                    "open_questions": [
                        {
                            "id": "OQ01",
                            "question": "Какие условия маршрута включены в ИБ?",
                            "why_open": "Нужны данные ИБ.",
                            "needed": "Выгрузить настройки маршрута.",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        seed_reverse_map_workitems(complete_repo)
        repo_path(complete_repo, "analysis/reverse-map/coverage.csv").write_text(
            REVERSE_MAP_COVERAGE_HEADER
            + "\nD-0001,target_cf,M,cf/Catalogs/Example.xml,Catalogs,Catalogs.Example,Documents,feature-a,feature-a,RM-0001,confirmed_in_scenario,high,analysis/features/feature-a/evidence.csv,analysis/reverse-map/decisions.csv#RM-0001,Confirmed by smoke test\n"
            + "D-0002,target_cf,M,cf/ScheduledJobs/ExampleJob.xml,ScheduledJobs,ScheduledJobs.ExampleJob,Регламентные задания,feature-a,feature-a,RM-0001,confirmed_in_scenario,medium,analysis/features/feature-a/evidence.csv,analysis/reverse-map/decisions.csv#RM-0001,Confirmed by smoke test\n",
            encoding="utf-8",
        )
        build_final_gate(complete_repo)
        detail_builder = subprocess.run(
            current_module_command("detail-map", "build", "--repo-path", str(complete_repo)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        require(detail_builder.returncode == 0, f"Detail-map builder should pass: stdout={detail_builder.stdout} stderr={detail_builder.stderr}", errors)
        detail_index = repo_path(complete_repo, "analysis/detail-maps/index.csv")
        generated_maps = sorted(repo_path(complete_repo, "analysis/detail-maps/generated").glob("*/detail-map.json"))
        require(detail_index.exists(), "Detail-map builder should write analysis/detail-maps/index.csv", errors)
        require(len(generated_maps) == 2, "Detail-map builder should create generated maps for each concrete object group", errors)
        if detail_index.exists():
            detail_index_text = detail_index.read_text(encoding="utf-8-sig")
            require("example-document/detail-map.json" in detail_index_text, "Detail-map index should include enriched manual maps", errors)
            require("generated/catalog-example/detail-map.json" in detail_index_text, "Detail-map index should include the generated catalog map", errors)
            require("generated/scheduled-job-examplejob/detail-map.json" in detail_index_text, "Detail-map index should include the generated scheduled job map", errors)
        if generated_maps:
            generated_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in generated_maps]
            require({payload.get("generation_mode") for payload in generated_payloads} == {"generated"}, "Generated detail maps should declare generation_mode=generated", errors)
            require({payload.get("type") for payload in generated_payloads} == {"catalog", "scheduled_job"}, "Generated detail maps should infer concrete map types", errors)
        manual_payload = json.loads(document_map.read_text(encoding="utf-8"))
        require(manual_payload.get("generation_mode") == "enriched", "Detail-map builder should not overwrite enriched manual maps", errors)
        subject_discover = subprocess.run(
            current_module_command("subject-card", "discover", "--repo-path", str(complete_repo)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        require(subject_discover.returncode == 0, f"Subject-card discover should pass: stdout={subject_discover.stdout} stderr={subject_discover.stderr}", errors)
        subject_classify = subprocess.run(
            current_module_command("subject-card", "classify", "--repo-path", str(complete_repo)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        require(subject_classify.returncode == 0, f"Subject-card classify should pass: stdout={subject_classify.stdout} stderr={subject_classify.stderr}", errors)
        subject_registry_build = subprocess.run(
            current_module_command("subject-card", "registry-build", "--repo-path", str(complete_repo)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        require(subject_registry_build.returncode == 0, f"Subject-card registry-build should pass: stdout={subject_registry_build.stdout} stderr={subject_registry_build.stderr}", errors)
        subject_seed = subprocess.run(
            current_module_command("subject-card", "seed", "--repo-path", str(complete_repo), "--from-registry"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        require(subject_seed.returncode == 0, f"Subject-card seed should pass: stdout={subject_seed.stdout} stderr={subject_seed.stderr}", errors)
        subject_refine = subprocess.run(
            current_module_command("subject-card", "refine", "--repo-path", str(complete_repo), "--card", "example-document"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        require(subject_refine.returncode == 0, f"Subject-card refine should pass: stdout={subject_refine.stdout} stderr={subject_refine.stderr}", errors)
        subject_validate = subprocess.run(
            current_module_command("subject-card", "validate", "--repo-path", str(complete_repo)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        require(subject_validate.returncode == 0, f"Subject-card validate should pass: stdout={subject_validate.stdout} stderr={subject_validate.stderr}", errors)
        subject_registry = repo_path(complete_repo, "analysis/subject-cards/registry.csv")
        subject_candidates = repo_path(complete_repo, "analysis/subject-cards/candidates.csv")
        subject_classification = repo_path(complete_repo, "analysis/subject-cards/classification.csv")
        subject_coverage = repo_path(complete_repo, "analysis/subject-cards/coverage.csv")
        subject_template = repo_path(complete_repo, "analysis/subject-cards/_templates/subject-card.json")
        subject_document_card = repo_path(complete_repo, "analysis/subject-cards/cards/example-document/subject-card.json")
        subject_document_evidence = repo_path(complete_repo, "analysis/subject-cards/cards/example-document/evidence.csv")
        subject_document_gaps = repo_path(complete_repo, "analysis/subject-cards/cards/example-document/gaps.csv")
        for subject_path in (
            subject_registry,
            subject_candidates,
            subject_classification,
            subject_coverage,
            subject_template,
            subject_document_card,
            subject_document_evidence,
            subject_document_gaps,
        ):
            require(subject_path.exists(), f"Subject-card workflow should write {subject_path.relative_to(complete_repo).as_posix()}", errors)
        if subject_document_card.exists():
            subject_payload = json.loads(subject_document_card.read_text(encoding="utf-8"))
            require(subject_payload.get("schema_version") == "subject-card/v1", "Subject-card payload should declare schema_version=subject-card/v1", errors)
            require(subject_payload.get("slug") == "example-document", "Subject-card seed should preserve the source card slug", errors)
            require(bool(subject_payload.get("key_conclusion")), "Subject-card seed should carry key_conclusion from source artifacts", errors)
            require(bool(subject_payload.get("upgrade_risk")), "Subject-card seed should carry upgrade_risk from source artifacts", errors)
            require(subject_payload.get("subject_type") == "business_document", "Subject-card seed should classify document maps as business_document", errors)
            require(bool(subject_payload.get("why_separate_card")), "Subject-card seed should explain why the card exists as a separate subject", errors)
            require(subject_payload.get("source_mode") != "hardcoded", "Subject-card seed should not mark cards as hardcoded", errors)
        dashboard = subprocess.run(
            current_module_command("review-dashboard", "build", "--repo-path", str(complete_repo)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        require(dashboard.returncode == 0, f"Review dashboard build should pass: stdout={dashboard.stdout} stderr={dashboard.stderr}", errors)
        dashboard_html = repo_path(complete_repo, "outputs/review/index.html")
        dashboard_data = repo_path(complete_repo, "outputs/review/data.json")
        require(dashboard_html.exists(), "Review dashboard should generate outputs/review/index.html", errors)
        require(dashboard_data.exists(), "Review dashboard should generate outputs/review/data.json", errors)
        if dashboard_html.exists():
            html = dashboard_html.read_text(encoding="utf-8")
            require("Карта доработок" in html, "Review dashboard should contain a Russian customization-map heading", errors)
            require("Example feature" in html, "Review dashboard should contain feature titles", errors)
            require("Реестр предметных доработок" in html, "Review dashboard should put the subject registry first", errors)
            require("subject-registry-list" in html, "Review dashboard should render the subject-card registry list", errors)
            require("coverage-summary" in html, "Review dashboard should expose BF coverage for subject cards", errors)
            require("renderSubjectRegistry" in html, "Review dashboard should render subject registry as the primary unit", errors)
            require("data-view=\"cards\"" in html, "Review dashboard should have a separate subject-card list screen", errors)
            require("function routeDashboard" in html, "Review dashboard should route between dashboard screens without a long landing page", errors)
            require("#card/" in html, "Review dashboard should open subject cards through drill-down hash routes", errors)
            require("Назад к списку" in html, "Review dashboard should provide an explicit return from card details to the list", errors)
            require("renderSubjectCardList" in html, "Review dashboard should render a compact subject-card list before details", errors)
            require("renderSubjectCardScreen" in html, "Review dashboard should render subject-card details as a separate screen", errors)
            require("data-view=\"runtime\"" in html, "Review dashboard should expose runtime/infobase checks as a separate screen", errors)
            require("runtime-check-list" in html, "Review dashboard should include a runtime checks table target", errors)
            require("renderRuntimeChecks" in html, "Review dashboard should render runtime/infobase check rows", errors)
            require("Проверки в ИБ" in html, "Review dashboard should label infobase checks in analyst-friendly Russian", errors)
            require("Ключевой вывод" in html, "Review dashboard should expose the subject-map key conclusion", errors)
            require("Риск перехода на ДО 3.0" in html, "Review dashboard should expose the subject-map upgrade risk", errors)
            require("Идентификация" in html, "Review dashboard should expose subject-map identification", errors)
            require("Группировка по BF" in html, "Review dashboard should keep BF as secondary grouping", errors)
            require("Техническая подложка" in html, "Review dashboard should move generated maps into a technical foundation section", errors)
            require(html.find("Реестр предметных доработок") < html.find("Группировка по BF") < html.find("Техническая подложка"), "Review dashboard should order subject registry before BF grouping and technical foundation", errors)
            require("Пример документа" in html, "Review dashboard should render subject-map titles", errors)
            require("Пример документа" in html, "Review dashboard should render detail-map titles", errors)
            require("Реквизиты" in html, "Review dashboard should render detail-map attribute tables", errors)
            require("DETAIL_MAP_PAGE_SIZE" in html, "Review dashboard should page large detail-map lists instead of rendering every map at boot", errors)
            require("renderDetailMapDetails" in html, "Review dashboard should lazy-render detail-map tables on demand", errors)
            require("buildDetailSearchText" in html, "Review dashboard should precompute detail-map search text instead of JSON.stringify on every filter pass", errors)
            require("JSON.stringify(map)" not in html, "Review dashboard should not stringify every detail map during filtering", errors)
            require("min-width: 0" in html, "Review dashboard should allow grid children to shrink instead of clipping wide content", errors)
            require("overflow-wrap: anywhere" in html, "Review dashboard should wrap long 1C identifiers and artifact paths", errors)
            require("Что важно для перехода на ДО 3.0" in html, "Review dashboard should expose the migration-impact section", errors)
        if dashboard_data.exists():
            data = json.loads(dashboard_data.read_text(encoding="utf-8"))
            require(data.get("summary", {}).get("feature_count") == 1, "Review dashboard data should include the feature count", errors)
            require(data.get("summary", {}).get("detail_map_count") == 4, "Review dashboard data should include manual and generated detail-map count", errors)
            require(data.get("summary", {}).get("detail_map_by_type", {}).get("document") == 1, "Review dashboard data should count document detail maps", errors)
            require(data.get("summary", {}).get("detail_map_by_type", {}).get("route") == 1, "Review dashboard data should count route detail maps", errors)
            require(data.get("summary", {}).get("detail_map_by_generation_mode", {}).get("generated") == 2, "Review dashboard data should count generated detail maps", errors)
            require({item.get("slug") for item in data.get("detail_maps", [])} == {"example-document", "example-route", "catalog-example", "scheduled-job-examplejob"}, "Review dashboard data should include manual and generated detail maps", errors)
            require(data.get("summary", {}).get("subject_map_count") == 2, "Review dashboard data should count manual/enriched subject maps", errors)
            require(data.get("summary", {}).get("technical_map_count") == 2, "Review dashboard data should count generated technical maps", errors)
            require(data.get("summary", {}).get("subject_registry_count") == 2, "Review dashboard data should count subject registry rows", errors)
            require(data.get("summary", {}).get("subject_registry_ready_count") == 2, "Review dashboard data should count ready subject-card registry rows", errors)
            require(data.get("summary", {}).get("subject_bf_covered_count") >= 1, "Review dashboard data should include BF coverage by subject cards", errors)
            subject_slugs = {item.get("slug") for item in data.get("subject_maps", [])}
            technical_slugs = {item.get("slug") for item in data.get("detail_maps", []) if item.get("generation_mode") == "generated"}
            require(subject_slugs == {"example-document", "example-route"}, "Review dashboard should expose manual/enriched maps as subject maps", errors)
            require({item.get("slug") for item in data.get("subject_cards", [])} == {"example-document", "example-route"}, "Review dashboard should expose subject-card artifacts as the analyst-facing subject cards", errors)
            require({item.get("slug") for item in data.get("subject_registry", [])} == {"example-document", "example-route"}, "Review dashboard should expose the canonical subject registry", errors)
            require(bool(data.get("subject_card_coverage")), "Review dashboard should expose subject-card coverage rows", errors)
            require(technical_slugs == {"catalog-example", "scheduled-job-examplejob"}, "Review dashboard should expose generated maps only as technical maps", errors)
            document_subject = next((item for item in data.get("subject_maps", []) if item.get("slug") == "example-document"), {})
            require(bool(document_subject.get("key_conclusion")), "Subject maps should include key_conclusion", errors)
            require(bool(document_subject.get("upgrade_risk")), "Subject maps should include upgrade_risk", errors)
            require(data.get("features", [{}])[0].get("evidence_count") == 2, "Review dashboard data should include evidence counts", errors)
            require(data.get("features", [{}])[0].get("detail_maps_count") == 4, "Feature data should include linked detail-map counts", errors)
        scoped_output_dir = base / "subject-card-dashboard"
        scoped_dashboard = subprocess.run(
            current_module_command(
                "review-dashboard",
                "build",
                "--repo-path",
                str(complete_repo),
                "--output-dir",
                str(scoped_output_dir),
                "--subject-card",
                "example-document",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        require(scoped_dashboard.returncode == 0, f"Scoped review dashboard build should pass: stdout={scoped_dashboard.stdout} stderr={scoped_dashboard.stderr}", errors)
        scoped_data_path = scoped_output_dir / "data.json"
        if scoped_data_path.exists():
            scoped_data = json.loads(scoped_data_path.read_text(encoding="utf-8"))
            require(scoped_data.get("dashboard_scope", {}).get("mode") == "subject_card", "Scoped dashboard should declare subject_card mode", errors)
            require(scoped_data.get("dashboard_scope", {}).get("subject_card") == "example-document", "Scoped dashboard should declare selected subject card", errors)
            require([item.get("slug") for item in scoped_data.get("subject_cards", [])] == ["example-document"], "Scoped dashboard should include only selected subject card", errors)
            require([item.get("slug") for item in scoped_data.get("subject_maps", [])] == ["example-document"], "Scoped dashboard should include only selected subject map", errors)
            require(scoped_data.get("detail_maps") == [], "Scoped dashboard should not include generated technical maps", errors)
            require(scoped_data.get("features") == [], "Scoped dashboard should not include global BF sections", errors)
            require(scoped_data.get("summary", {}).get("technical_map_count") == 0, "Scoped dashboard technical map count should be zero", errors)
        complete = run_doctor(complete_repo)
        require(complete["status"] == "ok", "Autopilot gate should pass on a complete customization map", errors)

        reverse_blocked_final_repo = base / "reverse-blocked-final"
        copy_smoke_repo(complete_repo, reverse_blocked_final_repo)
        repo_path(reverse_blocked_final_repo, "analysis/reverse-map/coverage.csv").write_text(
            REVERSE_MAP_COVERAGE_HEADER
            + "\nD-0001,target_cf,M,cf/CommonModules/Example/Ext/Module.bsl,CommonModule,Example,bsl,feature-a,feature-a,RM-0001,needs_manual_review,high,analysis/features/feature-a/evidence.csv,analysis/reverse-map/decisions.csv#RM-0001,Manual review required\n",
            encoding="utf-8",
        )
        reverse_blocked_final = run_doctor(reverse_blocked_final_repo)
        require(reverse_blocked_final["status"] == "fail", "Autopilot final gate should fail when final outputs claim complete but reverse-map blocks a diff row", errors)
        require(
            any(check["id"] == "final_gate.diff_inventory.stale" for check in reverse_blocked_final["checks"]),
            "Doctor should report final_gate.diff_inventory.stale for changed reverse-map blockers",
            errors,
        )

        bad_question_repo = base / "bad-open-question"
        copy_smoke_repo(complete_repo, bad_question_repo)
        repo_path(bad_question_repo, "outputs/open-questions.csv").write_text(
            OPEN_QUESTIONS_HEADER + "\nOQ-1,feature-a,open_question,,,,source,codex,\n",
            encoding="utf-8",
        )
        bad_question = run_doctor(bad_question_repo)
        require(bad_question["status"] == "fail", "Autopilot gate should fail when open questions lack reason, closure method, or impact", errors)
        require(any(check["id"] == "autopilot.open_questions.required_fields" for check in bad_question["checks"]), "Autopilot gate should report incomplete open questions", errors)

        reverse_repo = base / "reverse-map"
        copy_smoke_repo(temp_repo, reverse_repo)
        repo_path(reverse_repo, "analysis/indexes/diff-inventory.csv").write_text(
            DIFF_INVENTORY_HEADER
            + "\nD-0001,clean-rebase,A,Catalogs/Example.xml,Catalogs,Catalogs.Example,Documents,feature-a,confirmed business feature,high,mapped_to_feature,Example object,analysis/features/feature-a/evidence.csv,\n"
            + "D-0002,clean-rebase,M,Catalogs/Example/Ext/ObjectModule.bsl,Catalogs,Catalogs.Example,Documents,feature-a,confirmed business feature,high,mapped_to_feature,Example logic,analysis/features/feature-a/evidence.csv,\n",
            encoding="utf-8",
        )
        scaffold_reverse_map(argparse.Namespace(repo_path=str(reverse_repo), force=True))
        created = seed_reverse_map_workitems(reverse_repo)
        require(created == 1, "Reverse-map seed should create one workitem for the uncovered feature group", errors)
        coverage_lines = repo_path(reverse_repo, "analysis/reverse-map/coverage.csv").read_text(encoding="utf-8-sig").splitlines()
        require(coverage_lines[0] == REVERSE_MAP_COVERAGE_HEADER, "Reverse-map coverage should keep the canonical header", errors)
        require(len(coverage_lines) == 3, "Reverse-map coverage should contain every diff row plus header", errors)
        next_workitem = get_next_reverse_map_workitem(reverse_repo)
        require(next_workitem.get("id") == "RM-0001", "Reverse-map next should return the first generated workitem", errors)
        require(next_workitem.get("status") == "pending", "Reverse-map generated workitem should be pending", errors)
        reverse_doctor = run_doctor(reverse_repo, mode="research")
        require(reverse_doctor["status"] != "fail", "Doctor should not fail on initialized reverse-map state with pending work", errors)

        queue_path = repo_path(temp_repo, "analysis/queue/tasks.jsonl")
        claimed = claim_next_task(queue_path, claimed_by="worker-a")
        require(claimed.get("id") == "Q-0001", "claim_next_task should return Q-0001", errors)
        require(claimed.get("status") == "claimed", "claim_next_task should mark Q-0001 as claimed", errors)
        require(claim_next_task(queue_path, claimed_by="worker-b") == {}, "claim_next_task should not claim the same task twice", errors)
        updated = set_task_status(queue_path, "Q-0001", "done", expected_status="claimed", result_summary="smoke complete")
        require(updated.get("status") == "done", "set_task_status should allow guarded transitions from expected status", errors)

        unresolved = []
        for path in temp_repo.rglob("*"):
            if path.is_file() and path.suffix in {".md", ".toml", ".jsonl", ".py"}:
                relative = path.relative_to(temp_repo).as_posix()
                if relative.startswith("src/one_c_autoresearch/") or relative.startswith("scripts/"):
                    continue
                if re.search(r"__[A-Z][A-Z0-9_]*__", path.read_text(encoding="utf-8", errors="ignore")):
                    unresolved.append(path)
        require(not unresolved, "Bootstrap should leave no unresolved template placeholders", errors)

        placeholder_repo = base / "placeholder"
        copy_smoke_repo(temp_repo, placeholder_repo)
        repo_path(placeholder_repo, "analysis/features/placeholder-test.md").write_text("__UNRESOLVED_PLACEHOLDER__", encoding="utf-8")
        placeholder = run_doctor(placeholder_repo)
        require(placeholder["status"] == "fail", "Doctor should fail when a research repo contains unresolved placeholders", errors)
        require(any(check["id"] == "research.unresolved_placeholder" for check in placeholder["checks"]), "Doctor should report research.unresolved_placeholder", errors)

        invalid_queue_repo = base / "invalid-queue"
        copy_smoke_repo(temp_repo, invalid_queue_repo)
        invalid_queue = repo_path(invalid_queue_repo, "analysis/queue/tasks.jsonl")
        tasks = [task for _, task in read_jsonl(invalid_queue)]
        tasks[0]["status"] = "not-a-status"
        from .common import write_jsonl

        write_jsonl(invalid_queue, tasks)
        require(run_doctor(invalid_queue_repo, mode="research")["status"] == "fail", "Doctor should fail when queue status is invalid", errors)

        invalid_evidence_repo = base / "invalid-evidence"
        copy_smoke_repo(temp_repo, invalid_evidence_repo)
        invalid_evidence_queue = repo_path(invalid_evidence_repo, "analysis/queue/tasks.jsonl")
        tasks = [task for _, task in read_jsonl(invalid_evidence_queue)]
        tasks[0]["status"] = "evidence_pack"
        write_jsonl(invalid_evidence_queue, tasks)
        feature = repo_path(invalid_evidence_repo, "analysis/features/initial-discovery")
        feature.mkdir(parents=True, exist_ok=True)
        (feature / "findings.md").write_text("# Findings", encoding="utf-8")
        (feature / "feature-candidates.csv").write_text("feature_id,title,source_bucket,classification,confidence,summary,next_step", encoding="utf-8")
        (feature / "evidence.csv").write_text("bad,header", encoding="utf-8")
        invalid_evidence = run_doctor(invalid_evidence_repo)
        require(invalid_evidence["status"] == "warn", "Doctor should warn when evidence pack CSV headers do not match the schema", errors)
        require(any(check["id"] == "evidence_pack.invalid_evidence_header" for check in invalid_evidence["checks"]), "Doctor should report evidence_pack.invalid_evidence_header", errors)

        missing_evidence_repo = base / "missing-evidence"
        copy_smoke_repo(temp_repo, missing_evidence_repo)
        missing_queue = repo_path(missing_evidence_repo, "analysis/queue/tasks.jsonl")
        tasks = [task for _, task in read_jsonl(missing_queue)]
        tasks[0]["status"] = "evidence_pack"
        tasks[0].pop("expected_outputs", None)
        write_jsonl(missing_queue, tasks)
        missing_feature = repo_path(missing_evidence_repo, "analysis/features/initial-discovery")
        missing_feature.mkdir(parents=True, exist_ok=True)
        (missing_feature / "evidence.csv").write_text("feature_id,claim_id,source_kind,source_path,line_start,line_end,evidence_type,confidence,summary,notes", encoding="utf-8")
        missing = run_doctor(missing_evidence_repo)
        require(missing["status"] == "warn", "Doctor should warn when a completed evidence pack is missing required markdown files", errors)
        require(any(check["id"] == "evidence_pack.missing_required_file" for check in missing["checks"]), "Doctor should report evidence_pack.missing_required_file", errors)

        mcp_warn_repo = base / "mcp-warn"
        copy_smoke_repo(temp_repo, mcp_warn_repo)
        toml_path = repo_path(mcp_warn_repo, "project.toml")
        toml_path.write_text(re.sub(r"(?m)^enabled = false", "enabled = true", toml_path.read_text(encoding="utf-8"), count=2), encoding="utf-8")
        mcp_warn = run_doctor(mcp_warn_repo)
        require(mcp_warn["status"] == "warn", "Doctor should warn when MCP or web access is enabled but incomplete", errors)
        require(any(check["id"].startswith("manifest.mcp.") and check["status"] == "warn" for check in mcp_warn["checks"]), "Doctor should emit MCP configuration warnings", errors)
        require(any(check["id"].startswith("manifest.web.") and check["status"] == "warn" for check in mcp_warn["checks"]), "Doctor should emit web configuration warnings", errors)

        mcp_mismatch_repo = base / "mcp-mismatch"
        copy_smoke_repo(temp_repo, mcp_mismatch_repo)
        project_toml = repo_path(mcp_mismatch_repo, "project.toml")
        project_toml.write_text(project_toml.read_text(encoding="utf-8").replace('[mcp]\nenabled = false\nserver = ""\nurl = ""\nservice_root = "mcp"', '[mcp]\nenabled = true\nserver = "1c-project"\nurl = "http://localhost/project"\nservice_root = "mcp"'), encoding="utf-8")
        local_mcp = repo_path(mcp_mismatch_repo, ".codex/1c-mcp.toml")
        local_mcp.write_text('infobase = "project"\nmcp_server = "1c-other"\nurl = "http://localhost/other"\nservice_root = "mcp"\nrlm_project = "project"\n', encoding="utf-8")
        mismatch = run_doctor(mcp_mismatch_repo)
        require(mismatch["status"] == "fail", "Doctor should fail when .codex/1c-mcp.toml conflicts with project.toml", errors)
        require(any(check["id"] == "manifest.mcp.local_mismatch" for check in mismatch["checks"]), "Doctor should report manifest.mcp.local_mismatch", errors)

        rlm_mismatch_repo = base / "rlm-mismatch"
        copy_smoke_repo(temp_repo, rlm_mismatch_repo)
        project_toml = repo_path(rlm_mismatch_repo, "project.toml")
        project_toml.write_text(project_toml.read_text(encoding="utf-8").replace('[mcp]\nenabled = false\nserver = ""\nurl = ""\nservice_root = "mcp"', '[mcp]\nenabled = true\nserver = "1c-project"\nurl = "http://localhost/project"\nservice_root = "mcp"'), encoding="utf-8")
        local_mcp = repo_path(rlm_mismatch_repo, ".codex/1c-mcp.toml")
        local_mcp.write_text('infobase = "project"\nmcp_server = "1c-project"\nurl = "http://localhost/project"\nservice_root = "mcp"\nrlm_project = "other_project"\n', encoding="utf-8")
        rlm_mismatch = run_doctor(rlm_mismatch_repo)
        require(rlm_mismatch["status"] == "fail", "Doctor should fail when .codex/1c-mcp.toml rlm_project conflicts with project.toml rlm.target_cf", errors)
        require(any(check["id"] == "manifest.local_mcp.rlm_mismatch" for check in rlm_mismatch["checks"]), "Doctor should report manifest.local_mcp.rlm_mismatch", errors)

    if errors:
        raise CheckFailure("\n".join(f"- {error}" for error in errors))
    print(f"Doctor smoke tests passed: {root}")
    return 0


def run_check(fn, args: argparse.Namespace) -> int:
    try:
        return fn(args)
    except CheckFailure as exc:
        print(str(exc), file=sys.stderr)
        return 1
