from __future__ import annotations

import argparse
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
from .queue import claim_next_task, set_task_status
from .reverse_map import (
    REVERSE_MAP_COVERAGE_HEADER,
    get_next_reverse_map_workitem,
    scaffold_reverse_map,
    seed_reverse_map_workitems,
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
    require_text(root, "templates/research-repo/analysis/reverse-map/coverage.csv", rf"^{re.escape(REVERSE_MAP_COVERAGE_HEADER)}$", "Reverse-map coverage template should expose the canonical header.", errors)
    require_text(root, "templates/research-repo/project.toml", r"(?m)^\[autopilot\]$", "Generated manifest should include the autopilot final-gate section.", errors)
    require_text(root, "templates/research-repo/docs/agent/index.md", r"reverse-map claim", "Agent router should document the reverse-map continuation command.", errors)
    require_text(root, "templates/research-repo/AGENTS.md", r"analysis/reverse-map", "Generated AGENTS should identify reverse-map state as source of truth.", errors)
    require_text(root, "templates/research-repo/README.md", r"reverse-map", "Generated README should expose reverse-map continuation commands.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"reverse-map", "CLI should expose a reverse-map command group.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"final-gate", "CLI should expose a final-gate command group.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"review-dashboard", "CLI should expose a review-dashboard command group.", errors)
    require_text(root, "src/one_c_autoresearch/cli.py", r"detail-map", "CLI should expose a detail-map command group.", errors)
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
    doctor = run_doctor(root, mode="research")
    if doctor["status"] == "fail":
        print(json.dumps(doctor, ensure_ascii=False, indent=2))
        return 1
    print(f"Research repo validation passed: {root}")
    return 0


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
            require("Собственно доработки" in html, "Review dashboard should put the business customization catalog before technical details", errors)
            require("renderCustomizationGroups" in html, "Review dashboard should render grouped business customizations", errors)
            require("Ключевые объекты" in html, "Review dashboard should expose key changed objects for each customization", errors)
            require("Карты доработок" in html, "Review dashboard should expose the detail-map section", errors)
            require("Пример документа" in html, "Review dashboard should render detail-map titles", errors)
            require("Реквизиты" in html, "Review dashboard should render detail-map attribute tables", errors)
            require("DETAIL_MAP_PAGE_SIZE" in html, "Review dashboard should page large detail-map lists instead of rendering every map at boot", errors)
            require("renderDetailMapDetails" in html, "Review dashboard should lazy-render detail-map tables on demand", errors)
            require("buildDetailSearchText" in html, "Review dashboard should precompute detail-map search text instead of JSON.stringify on every filter pass", errors)
            require("JSON.stringify(map)" not in html, "Review dashboard should not stringify every detail map during filtering", errors)
            require("Что важно для перехода на ДО 3.0" in html, "Review dashboard should expose the migration-impact section", errors)
        if dashboard_data.exists():
            data = json.loads(dashboard_data.read_text(encoding="utf-8"))
            require(data.get("summary", {}).get("feature_count") == 1, "Review dashboard data should include the feature count", errors)
            require(data.get("summary", {}).get("detail_map_count") == 4, "Review dashboard data should include manual and generated detail-map count", errors)
            require(data.get("summary", {}).get("detail_map_by_type", {}).get("document") == 1, "Review dashboard data should count document detail maps", errors)
            require(data.get("summary", {}).get("detail_map_by_type", {}).get("route") == 1, "Review dashboard data should count route detail maps", errors)
            require(data.get("summary", {}).get("detail_map_by_generation_mode", {}).get("generated") == 2, "Review dashboard data should count generated detail maps", errors)
            require({item.get("slug") for item in data.get("detail_maps", [])} == {"example-document", "example-route", "catalog-example", "scheduled-job-examplejob"}, "Review dashboard data should include manual and generated detail maps", errors)
            require(data.get("features", [{}])[0].get("evidence_count") == 2, "Review dashboard data should include evidence counts", errors)
            require(data.get("features", [{}])[0].get("detail_maps_count") == 4, "Feature data should include linked detail-map counts", errors)
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
