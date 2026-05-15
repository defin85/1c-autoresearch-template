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

from .bootstrap import create_research_repo
from .common import current_module_command, git_check_ignored, read_jsonl, repo_path
from .doctor import run_doctor
from .queue import claim_next_task, set_task_status


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
    require_same_content(root, "docs/method/1c-autoresearch-process.md", "templates/research-repo/docs/method/1c-autoresearch-process.md", "Generated research methodology must match the template system-of-record document.", errors)
    require_same_content(root, "docs/method/evidence-pack-schema.md", "templates/research-repo/docs/method/evidence-pack-schema.md", "Generated evidence pack schema must match the template system-of-record document.", errors)
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
            "analysis/cache/AGENTS.md",
            "analysis/features/AGENTS.md",
            "analysis/features/_templates/evidence.csv",
            "analysis/queue/runs/README.md",
            "outputs/AGENTS.md",
            "scripts/queue/claim_next_analysis_task.py",
        ):
            require_path(temp_repo, relative, errors)
        generated_method = repo_path(temp_repo, "docs/method/1c-autoresearch-process.md").read_text(encoding="utf-8")
        require("## Evidence Levels" in generated_method, "Generated method docs should include evidence levels", errors)
        research = run_doctor(temp_repo)
        require(research["repo_kind"] == "research", "Embedded doctor should detect repo_kind=research", errors)
        require(research["status"] == "ok", "Embedded doctor should report status=ok without --deep", errors)
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
        toml_path.write_text(re.sub(r"(?m)^enabled = false", "enabled = true", toml_path.read_text(encoding="utf-8")), encoding="utf-8")
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
