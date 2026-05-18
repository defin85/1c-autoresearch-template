from __future__ import annotations

import json
import re
import sys
import csv
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .autopilot import DIFF_INVENTORY_HEADER, FEATURE_MAP_HEADER, OPEN_QUESTIONS_HEADER
from .common import (
    CheckSet,
    as_list,
    command_exists,
    read_jsonl,
    read_toml,
    rel_id,
    repo_path,
    toml_enabled,
    toml_value,
    utc_now_iso,
)
from .reverse_map import (
    REVERSE_MAP_COVERAGE_HEADER,
    REVERSE_MAP_DECISIONS_HEADER,
    REVERSE_MAP_STATUSES,
    REVERSE_MAP_UNRESOLVED_HEADER,
    REVERSE_MAP_WORKITEM_STATUSES,
)

CANONICAL_EVIDENCE_HEADER = "feature_id,claim_id,source_kind,source_path,line_start,line_end,evidence_type,confidence,summary,notes"
CANONICAL_FEATURE_CANDIDATES_HEADER = "feature_id,title,source_bucket,classification,confidence,summary,next_step"
VALID_STATUSES = {"pending", "claimed", "evidence_pack", "drafted", "needs_review", "needs_followup", "blocked", "done", "skipped"}
VALID_TYPES = {"discovery", "deep_dive", "review", "migration_map", "packaging", "needs_infobase_data"}
AUTOPILOT_DIFF_STATUSES = {"mapped_to_feature", "technical_noise_removed", "requires_1c_review", "blocked_by_infobase_data"}
AUTOPILOT_FEATURE_STATUSES = {"complete", "blocked_by_infobase_data", "requires_1c_review", "out_of_scope"}

TEMPLATE_REQUIRED_PATHS = [
    ".github/workflows/verify.yml",
    "README.md",
    "AGENTS.md",
    "pyproject.toml",
    "one_c_autoresearch/__init__.py",
    "one_c_autoresearch/__main__.py",
    "project.example.toml",
    "src/one_c_autoresearch/__init__.py",
    "src/one_c_autoresearch/__main__.py",
    "src/one_c_autoresearch/cli.py",
    "src/one_c_autoresearch/autopilot.py",
    "src/one_c_autoresearch/reverse_map.py",
    "src/one_c_autoresearch/doctor.py",
    "src/one_c_autoresearch/bootstrap.py",
    "src/one_c_autoresearch/queue.py",
    "src/one_c_autoresearch/checks.py",
    "docs/agent/index.md",
    "docs/agent/repo-map.md",
    "docs/agent/verification.md",
    "docs/method/1c-autoresearch-process.md",
    "docs/method/evidence-pack-schema.md",
    "docs/method/autopilot-customization-map.md",
    "docs/method/reverse-functional-map.md",
    "docs/method/queue-design.md",
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
    "templates/research-repo/outputs/open-questions.csv",
    "templates/research-repo/.agents/skills/1c-autoresearch-queue-worker/SKILL.md",
    "scripts/doctor.py",
    "scripts/bootstrap/new_research_repo.py",
    "scripts/checks/test_template.py",
    "scripts/checks/test_doctor.py",
    "scripts/checks/test_research_repo.py",
]

RESEARCH_REQUIRED_PATHS = [
    "project.toml",
    "AGENTS.md",
    "README.md",
    ".gitignore",
    ".codex/1c-mcp.example.toml",
    "pyproject.toml",
    "one_c_autoresearch/__init__.py",
    "one_c_autoresearch/__main__.py",
    "src/one_c_autoresearch/__init__.py",
    "src/one_c_autoresearch/__main__.py",
    "src/one_c_autoresearch/autopilot.py",
    "src/one_c_autoresearch/reverse_map.py",
    "src/one_c_autoresearch/cli.py",
    "docs/agent/index.md",
    "docs/agent/repo-map.md",
    "docs/agent/verification.md",
    "docs/method/1c-autoresearch-process.md",
    "docs/method/evidence-pack-schema.md",
    "docs/method/autopilot-customization-map.md",
    "docs/method/reverse-functional-map.md",
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
    "analysis/runs/README.md",
    "analysis/queue/README.md",
    "analysis/queue/tasks.jsonl",
    "analysis/queue/task-schema.md",
    "analysis/queue/review-checklist.md",
    "analysis/queue/runs/README.md",
    "analysis/queue/worker-prompt.md",
    "analysis/cache/AGENTS.md",
    "analysis/cache/README.md",
    "analysis/features/AGENTS.md",
    "analysis/features/README.md",
    "analysis/features/_templates/brief.md",
    "analysis/features/_templates/evidence.csv",
    "analysis/features/_templates/feature-candidates.csv",
    "analysis/features/_templates/findings.md",
    "analysis/features/_templates/open-questions.md",
    "analysis/features/_templates/review.md",
    "outputs/AGENTS.md",
    "outputs/README.md",
    "outputs/open-questions.csv",
    ".agents/skills/1c-autoresearch-queue-worker/SKILL.md",
    "scripts/doctor.py",
    "scripts/queue/claim_next_analysis_task.py",
    "scripts/queue/get_next_analysis_task.py",
    "scripts/queue/set_analysis_task_status.py",
    "scripts/checks/test_research_repo.py",
]


class Doctor:
    def __init__(self, root: Path, mode: str = "auto", deep: bool = False, stale_claim_hours: int = 12) -> None:
        self.root = root.resolve()
        self.mode = mode
        self.deep = deep
        self.stale_claim_hours = stale_claim_hours
        self.checks = CheckSet()
        self.last_queue_tasks: list[dict[str, Any]] = []

    def exists(self, relative: str) -> bool:
        return repo_path(self.root, relative).exists()

    def require_path(self, relative: str, prefix: str) -> None:
        if self.exists(relative):
            self.checks.add(f"{prefix}.required_path.{rel_id(relative)}", "ok", f"Found {relative}")
        else:
            self.checks.add(f"{prefix}.required_path.{rel_id(relative)}", "fail", f"Missing required path: {relative}")

    def repo_kind(self) -> str:
        is_template = self.exists("templates/research-repo/project.toml")
        is_research = self.exists("project.toml") and self.exists("analysis/queue/tasks.jsonl")
        if self.mode != "auto":
            return self.mode
        if is_research:
            return "research"
        if is_template:
            return "template"
        return "unknown"

    def test_tool(self, name: str) -> None:
        if command_exists(name):
            self.checks.add(f"tool.{name}", "ok", f"Tool is available: {name}")
        else:
            self.checks.add(f"tool.{name}", "warn", f"Tool is not available on PATH: {name}")

    def test_project_toml(self, relative: str = "project.toml") -> dict[str, Any] | None:
        path = repo_path(self.root, relative)
        if not path.exists():
            self.checks.add("manifest.exists", "fail", f"Missing manifest: {relative}")
            return None
        try:
            manifest = read_toml(path)
            self.checks.add("manifest.parse", "ok", f"Parsed {relative}")
        except Exception as exc:
            self.checks.add("manifest.parse", "fail", f"Could not parse {relative}: {exc}")
            return None

        for section in ("project", "paths", "rlm", "mcp", "web", "policy", "autopilot"):
            status = "ok" if section in manifest else "fail"
            message = f"Found [{section}]" if section in manifest else f"Missing section [{section}]"
            self.checks.add(f"manifest.section.{section}", status, message)

        project = manifest.get("project", {})
        for key in ("id", "product"):
            value = str(project.get(key, "")).strip()
            self.checks.add(f"manifest.project.{key}", "ok" if value else "warn", f"Project {key} is {'set' if value else 'empty'}")

        paths = manifest.get("paths", {})
        for key in ("vendor_baseline", "target_cf"):
            value = str(paths.get(key, "")).strip()
            self.checks.add(f"manifest.paths.{key}", "ok" if value else "warn", f"Path {key} is {'set' if value else 'empty'}")
        for key in ("vendor_baseline", "target_cf", "target_cfe", "next_vendor"):
            value = str(paths.get(key, "")).strip()
            if self.deep and value:
                self.checks.add(
                    f"manifest.paths.exists.{key}",
                    "ok" if Path(value).exists() else "warn",
                    f"Path {'exists' if Path(value).exists() else 'does not exist'}: {key}" + ("" if Path(value).exists() else f" = {value}"),
                )

        rlm = manifest.get("rlm", {})
        for key in ("vendor_baseline", "target_cf", "target_cfe", "next_vendor"):
            value = str(rlm.get(key, "")).strip()
            self.checks.add(f"manifest.rlm.{key}", "ok" if value else "warn", f"RLM project is {'set' if value else 'empty'}: {key}")

        self.test_manifest_access_policy(manifest)
        self.test_local_mcp_manifest(manifest)
        return manifest

    def test_manifest_access_policy(self, manifest: dict[str, Any]) -> None:
        if "mcp" in manifest:
            if toml_enabled(manifest, "mcp"):
                for key in ("server", "url", "service_root"):
                    value = toml_value(manifest, "mcp", key).strip()
                    self.checks.add(f"manifest.mcp.{key}", "ok" if value else "warn", f"MCP {key} is {'set' if value else 'enabled but mcp.' + key + ' is empty'}")
            else:
                self.checks.add("manifest.mcp.enabled", "ok", "MCP access is disabled")
        if "web" in manifest:
            if toml_enabled(manifest, "web"):
                for key in ("url", "username"):
                    value = toml_value(manifest, "web", key).strip()
                    self.checks.add(f"manifest.web.{key}", "ok" if value else "warn", f"Web {key} is {'set' if value else 'enabled but web.' + key + ' is empty'}")
                credential = toml_value(manifest, "web", "credential_file").strip()
                self.checks.add(
                    "manifest.web.credential_file",
                    "ok" if credential else "warn",
                    "Web credential file is set" if credential else "Web access is enabled without a credential file; default codex/codex credentials are expected",
                )
            else:
                self.checks.add("manifest.web.enabled", "ok", "Web access is disabled")

    def test_local_mcp_manifest(self, manifest: dict[str, Any]) -> None:
        path = repo_path(self.root, ".codex/1c-mcp.toml")
        if not path.exists():
            self.checks.add("manifest.local_mcp.absent", "ok", "No repo-local .codex/1c-mcp.toml manifest found")
            return
        try:
            local = read_toml(path)
            self.checks.add("manifest.local_mcp.parse", "ok", "Parsed .codex/1c-mcp.toml")
        except Exception as exc:
            self.checks.add("manifest.local_mcp.parse", "fail", f"Could not parse .codex/1c-mcp.toml: {exc}")
            return
        if not toml_enabled(manifest, "mcp"):
            self.checks.add("manifest.mcp.local_with_disabled_project_mcp", "warn", "Repo-local .codex/1c-mcp.toml exists while project.toml has mcp.enabled=false")
            return
        comparisons = (
            ("server", "mcp_server", "MCP server"),
            ("url", "url", "MCP URL"),
            ("service_root", "service_root", "MCP service root"),
        )
        mismatch = missing = unchecked = 0
        for project_key, local_key, label in comparisons:
            project_value = toml_value(manifest, "mcp", project_key)
            local_value = str(local.get(local_key, "")).strip()
            if not local_value:
                missing += 1
                self.checks.add(f"manifest.local_mcp.{local_key}", "warn", f".codex/1c-mcp.toml is missing {local_key}")
            elif not project_value:
                unchecked += 1
            elif project_value != local_value:
                mismatch += 1
                self.checks.add("manifest.mcp.local_mismatch", "fail", f"{label} differs between project.toml and .codex/1c-mcp.toml: project.toml={project_value} local={local_value}")
        if mismatch == 0 and missing == 0 and unchecked == 0:
            self.checks.add("manifest.mcp.local_match", "ok", "Repo-local .codex/1c-mcp.toml matches project.toml MCP settings")
        local_rlm = str(local.get("rlm_project", "")).strip()
        target_rlm = toml_value(manifest, "rlm", "target_cf")
        if not local_rlm:
            self.checks.add("manifest.local_mcp.rlm_project", "warn", ".codex/1c-mcp.toml is missing rlm_project")
        elif target_rlm and local_rlm != target_rlm:
            self.checks.add("manifest.local_mcp.rlm_mismatch", "fail", f".codex/1c-mcp.toml rlm_project differs from project.toml rlm.target_cf: project.toml={target_rlm} local={local_rlm}")
        else:
            self.checks.add("manifest.local_mcp.rlm_match", "ok", "Repo-local .codex/1c-mcp.toml matches project.toml rlm.target_cf")
        if not str(local.get("infobase", "")).strip():
            self.checks.add("manifest.local_mcp.infobase", "warn", ".codex/1c-mcp.toml is missing infobase")
        if toml_enabled(manifest, "web"):
            project_web = toml_value(manifest, "web", "url")
            local_web = str(local.get("web_url", "")).strip()
            if not local_web:
                self.checks.add("manifest.local_mcp.web_url", "warn", ".codex/1c-mcp.toml is missing web_url while project.toml has web.enabled=true")
            elif project_web and local_web != project_web:
                self.checks.add("manifest.local_mcp.web_mismatch", "fail", f".codex/1c-mcp.toml web_url differs from project.toml web.url: project.toml={project_web} local={local_web}")

    def test_queue(self, relative: str = "analysis/queue/tasks.jsonl") -> None:
        path = repo_path(self.root, relative)
        if not path.exists():
            self.checks.add("queue.exists", "fail", f"Missing queue: {relative}")
            return
        tasks: list[dict[str, Any]] = []
        try:
            for line_number, task in read_jsonl(path):
                task["_line"] = line_number
                tasks.append(task)
        except Exception as exc:
            self.checks.add("queue.jsonl.parse", "fail", f"Invalid JSONL at {relative}: {exc}")
        if not tasks:
            self.checks.add("queue.not_empty", "warn", "Queue has no tasks")
            return
        self.checks.add("queue.not_empty", "ok", f"Queue contains {len(tasks)} task(s)")
        ids: dict[str, dict[str, Any]] = {}
        for task in tasks:
            for field in ("id", "type", "status", "priority", "title", "feature_id", "created_at", "updated_at"):
                if field not in task:
                    self.checks.add("queue.required_fields", "fail", f"Task at line {task.get('_line')} missing field: {field}")
            task_id = str(task.get("id", ""))
            if task_id:
                if task_id in ids:
                    self.checks.add("queue.duplicate_id", "fail", f"Duplicate task id: {task_id}")
                else:
                    ids[task_id] = task
            if "status" in task and task["status"] not in VALID_STATUSES:
                self.checks.add("queue.invalid_status", "fail", f"Task {task_id} has invalid status: {task['status']}")
            if "type" in task and task["type"] not in VALID_TYPES:
                self.checks.add("queue.invalid_type", "fail", f"Task {task_id} has invalid type: {task['type']}")
        for task in tasks:
            for dependency in as_list(task.get("dependencies")):
                dependency = str(dependency).strip()
                if dependency and dependency not in ids:
                    self.checks.add("queue.missing_dependency", "fail", f"Task {task.get('id')} depends on missing task: {dependency}")
        self.test_queue_cycles(ids)
        stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=self.stale_claim_hours)
        for task in tasks:
            if task.get("status") == "claimed" and task.get("claimed_at"):
                try:
                    claimed_at = datetime.fromisoformat(str(task["claimed_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
                    if claimed_at < stale_cutoff:
                        self.checks.add("queue.stale_claim", "warn", f"Task {task.get('id')} has stale claim from {task['claimed_at']}")
                except Exception:
                    self.checks.add("queue.claimed_at_parse", "warn", f"Task {task.get('id')} has unparsable claimed_at: {task['claimed_at']}")
            if task.get("status") in {"evidence_pack", "drafted", "needs_review", "done"}:
                for output in as_list(task.get("expected_outputs")):
                    output = str(output).strip()
                    if output and not repo_path(self.root, output).exists():
                        self.checks.add("queue.expected_output_missing", "warn", f"Task {task.get('id')} expected output is missing: {output}")
        if not any(check["id"] == "queue.duplicate_id" for check in self.checks.checks):
            self.checks.add("queue.unique_ids", "ok", "Task ids are unique")
        self.last_queue_tasks = tasks

    def test_queue_cycles(self, tasks_by_id: dict[str, dict[str, Any]]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()
        has_cycle = False

        def visit(task_id: str) -> None:
            nonlocal has_cycle
            if task_id in visited:
                return
            if task_id in visiting:
                has_cycle = True
                self.checks.add("queue.dependency_cycle", "fail", f"Dependency cycle includes {task_id}")
                return
            task = tasks_by_id.get(task_id)
            if task is None:
                return
            visiting.add(task_id)
            for dependency in as_list(task.get("dependencies")):
                dependency = str(dependency).strip()
                if dependency:
                    visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in tasks_by_id:
            visit(task_id)
        if not has_cycle:
            self.checks.add("queue.no_dependency_cycles", "ok", "No dependency cycles detected")

    def test_evidence_packs(self) -> None:
        for task in self.last_queue_tasks:
            if task.get("status") not in {"evidence_pack", "drafted", "needs_review", "done"} or not task.get("feature_id"):
                continue
            feature_id = str(task["feature_id"])
            feature_path = repo_path(self.root, f"analysis/features/{feature_id}")
            if not feature_path.exists():
                self.checks.add("evidence_pack.missing_folder", "warn", f"Task {task.get('id')} has status {task.get('status')} but feature folder is missing: analysis/features/{feature_id}")
                continue
            for required_file in ("brief.md", "findings.md", "evidence.csv", "open-questions.md", "review.md"):
                if not (feature_path / required_file).exists():
                    self.checks.add("evidence_pack.missing_required_file", "warn", f"Task {task.get('id')} feature pack is missing required file: analysis/features/{feature_id}/{required_file}")
            evidence = feature_path / "evidence.csv"
            if not evidence.exists():
                self.checks.add("evidence_pack.missing_evidence", "warn", f"Task {task.get('id')} has status {task.get('status')} but evidence.csv is missing")
            elif evidence.read_text(encoding="utf-8-sig").splitlines()[0] != CANONICAL_EVIDENCE_HEADER:
                self.checks.add("evidence_pack.invalid_evidence_header", "warn", f"Task {task.get('id')} evidence.csv header does not match docs/method/evidence-pack-schema.md")
            candidates = feature_path / "feature-candidates.csv"
            if candidates.exists() and candidates.read_text(encoding="utf-8-sig").splitlines()[0] != CANONICAL_FEATURE_CANDIDATES_HEADER:
                self.checks.add("evidence_pack.invalid_feature_candidates_header", "warn", f"Task {task.get('id')} feature-candidates.csv header does not match docs/method/evidence-pack-schema.md")

    def read_csv_rows(self, relative: str, expected_header: str, check_prefix: str) -> list[dict[str, str]]:
        path = repo_path(self.root, relative)
        if not path.exists():
            self.checks.add(f"{check_prefix}.exists", "fail", f"Missing required CSV: {relative}")
            return []
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        if not lines:
            self.checks.add(f"{check_prefix}.not_empty", "fail", f"CSV is empty: {relative}")
            return []
        if lines[0] != expected_header:
            self.checks.add(f"{check_prefix}.header", "fail", f"CSV header does not match contract: {relative}")
            return []
        self.checks.add(f"{check_prefix}.header", "ok", f"CSV header matches contract: {relative}")
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))

    def test_xlsx_file(self, relative: str, check_id: str) -> None:
        path = repo_path(self.root, relative)
        if not path.exists():
            self.checks.add(check_id, "fail", f"Missing required XLSX deliverable: {relative}")
            return
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
            required = {"[Content_Types].xml", "xl/workbook.xml"}
            if required.issubset(names):
                self.checks.add(check_id, "ok", f"XLSX deliverable is a readable workbook: {relative}")
            else:
                self.checks.add(check_id, "fail", f"XLSX deliverable is missing workbook parts: {relative}")
        except Exception as exc:
            self.checks.add(check_id, "fail", f"XLSX deliverable is not readable: {relative}: {exc}")

    def test_final_text_has_no_todos(self, relative: str) -> None:
        path = repo_path(self.root, relative)
        if not path.exists():
            self.checks.add("autopilot.final_text.exists", "fail", f"Missing final text artifact: {relative}")
            return
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"\b(TODO|FIXME)\b", text, re.IGNORECASE):
            self.checks.add("autopilot.final_text.todo", "fail", f"Final artifact contains TODO/FIXME marker: {relative}")
        else:
            self.checks.add(f"autopilot.final_text.no_todo.{rel_id(relative)}", "ok", f"Final artifact has no TODO/FIXME markers: {relative}")

    def test_feature_pack_from_map(self, feature_id: str, feature_path_text: str) -> None:
        feature_path = repo_path(self.root, feature_path_text or f"analysis/features/{feature_id}")
        if not feature_path.exists():
            self.checks.add("autopilot.feature_pack.exists", "fail", f"Feature map references missing evidence pack: {feature_path.relative_to(self.root).as_posix() if feature_path.is_relative_to(self.root) else feature_path}")
            return
        for required_file in ("brief.md", "findings.md", "evidence.csv", "open-questions.md", "review.md"):
            if not (feature_path / required_file).exists():
                self.checks.add("autopilot.feature_pack.required_file", "fail", f"Feature pack {feature_id} is missing {required_file}")
        evidence = feature_path / "evidence.csv"
        if not evidence.exists():
            return
        rows = self.read_csv_rows(evidence.relative_to(self.root).as_posix(), CANONICAL_EVIDENCE_HEADER, f"autopilot.feature_pack.evidence.{rel_id(feature_id)}")
        data_rows = 0
        for row in rows:
            if not any((value or "").strip() for value in row.values()):
                continue
            data_rows += 1
            if not (row.get("source_kind", "").strip() and row.get("source_path", "").strip() and row.get("summary", "").strip()):
                self.checks.add("autopilot.evidence.missing_source", "fail", f"Feature {feature_id} evidence row lacks source_kind, source_path, or summary")
        if data_rows:
            self.checks.add(f"autopilot.feature_pack.evidence_rows.{rel_id(feature_id)}", "ok", f"Feature {feature_id} has {data_rows} evidence row(s)")
        else:
            self.checks.add("autopilot.feature_pack.evidence_rows", "fail", f"Feature {feature_id} has no evidence rows")

    def test_autopilot_contract(self, manifest: dict[str, Any] | None) -> None:
        if not manifest or not toml_enabled(manifest, "autopilot"):
            self.checks.add("autopilot.enabled", "ok", "Autopilot final gate is disabled")
            return
        self.checks.add("autopilot.enabled", "ok", "Autopilot final gate is enabled")
        for relative in (
            "analysis/indexes/diff-inventory.csv",
            "analysis/indexes/feature-map.csv",
            "outputs/customization-map.md",
            "outputs/customization-map.xlsx",
            "outputs/open-questions.csv",
            "outputs/open-questions.xlsx",
            "analysis/final-audit.md",
        ):
            self.require_path(relative, "autopilot")

        diff_rows = self.read_csv_rows("analysis/indexes/diff-inventory.csv", DIFF_INVENTORY_HEADER, "autopilot.diff_inventory")
        feature_rows = self.read_csv_rows("analysis/indexes/feature-map.csv", FEATURE_MAP_HEADER, "autopilot.feature_map")
        open_question_rows = self.read_csv_rows("outputs/open-questions.csv", OPEN_QUESTIONS_HEADER, "autopilot.open_questions")
        self.test_xlsx_file("outputs/customization-map.xlsx", "autopilot.outputs.customization_map_xlsx")
        self.test_xlsx_file("outputs/open-questions.xlsx", "autopilot.outputs.open_questions_xlsx")
        self.test_final_text_has_no_todos("outputs/customization-map.md")
        self.test_final_text_has_no_todos("analysis/final-audit.md")

        if not diff_rows:
            self.checks.add("autopilot.diff_inventory.rows", "fail", "Diff inventory has no classified rows")
        else:
            self.checks.add("autopilot.diff_inventory.rows", "ok", f"Diff inventory has {len(diff_rows)} row(s)")
        unclassified = 0
        for row in diff_rows:
            status = row.get("status", "").strip()
            diff_id = row.get("diff_id", "").strip() or "<empty diff_id>"
            if status not in AUTOPILOT_DIFF_STATUSES:
                unclassified += 1
                self.checks.add("autopilot.diff_inventory.unclassified", "fail", f"Diff row {diff_id} has invalid or unclassified status: {status or '<empty>'}")
            if not row.get("classification", "").strip():
                self.checks.add("autopilot.diff_inventory.missing_classification", "fail", f"Diff row {diff_id} has empty classification")
            if status in {"mapped_to_feature", "blocked_by_infobase_data", "requires_1c_review"} and not row.get("feature_id", "").strip():
                self.checks.add("autopilot.diff_inventory.missing_feature", "fail", f"Diff row {diff_id} requires a feature_id for status {status}")
            if not row.get("summary", "").strip():
                self.checks.add("autopilot.diff_inventory.missing_summary", "fail", f"Diff row {diff_id} has empty summary")
        if diff_rows and unclassified == 0:
            self.checks.add("autopilot.diff_inventory.coverage", "ok", "Every diff inventory row has a classified autopilot status")

        if not feature_rows:
            self.checks.add("autopilot.feature_map.rows", "fail", "Feature map has no feature rows")
        else:
            self.checks.add("autopilot.feature_map.rows", "ok", f"Feature map has {len(feature_rows)} row(s)")
        feature_ids = {row.get("feature_id", "").strip() for row in feature_rows if row.get("feature_id", "").strip()}
        for row in feature_rows:
            feature_id = row.get("feature_id", "").strip()
            if not feature_id:
                self.checks.add("autopilot.feature_map.feature_id", "fail", "Feature map row has empty feature_id")
                continue
            status = row.get("status", "").strip()
            if status not in AUTOPILOT_FEATURE_STATUSES:
                self.checks.add("autopilot.feature_map.status", "fail", f"Feature {feature_id} has invalid status: {status or '<empty>'}")
            if not row.get("classification", "").strip():
                self.checks.add("autopilot.feature_map.classification", "fail", f"Feature {feature_id} has empty classification")
            if not row.get("summary", "").strip():
                self.checks.add("autopilot.feature_map.summary", "fail", f"Feature {feature_id} has empty summary")
            if status in {"complete", "blocked_by_infobase_data", "requires_1c_review"}:
                self.test_feature_pack_from_map(feature_id, row.get("evidence_pack_path", "").strip())
        for row in diff_rows:
            feature_id = row.get("feature_id", "").strip()
            status = row.get("status", "").strip()
            if feature_id and status != "technical_noise_removed" and feature_id not in feature_ids:
                self.checks.add("autopilot.coverage.unknown_feature", "fail", f"Diff row {row.get('diff_id', '<empty diff_id>')} references feature not present in feature-map.csv: {feature_id}")

        for row in open_question_rows:
            if not any((value or "").strip() for value in row.values()):
                continue
            question_id = row.get("question_id", "").strip() or "<empty question_id>"
            for field in ("reason", "closure_method", "impact"):
                if not row.get(field, "").strip():
                    self.checks.add("autopilot.open_questions.required_fields", "fail", f"Open question {question_id} is missing {field}")
            status = row.get("status", "").strip()
            if status not in {"open_question", "blocked_by_infobase_data", "closed"}:
                self.checks.add("autopilot.open_questions.status", "fail", f"Open question {question_id} has invalid status: {status or '<empty>'}")

        audit = repo_path(self.root, "analysis/final-audit.md")
        if audit.exists():
            text = audit.read_text(encoding="utf-8", errors="ignore")
            if not re.search(r"(?im)^Coverage status:\s*complete\s*$", text):
                self.checks.add("autopilot.final_audit.coverage_status", "fail", "Final audit must contain 'Coverage status: complete'")
            else:
                self.checks.add("autopilot.final_audit.coverage_status", "ok", "Final audit declares complete coverage")
            if not re.search(r"(?im)^Unclassified diff entries:\s*0\s*$", text):
                self.checks.add("autopilot.final_audit.unclassified_zero", "fail", "Final audit must contain 'Unclassified diff entries: 0'")
            else:
                self.checks.add("autopilot.final_audit.unclassified_zero", "ok", "Final audit declares zero unclassified diff entries")

    def test_reverse_map_contract(self) -> None:
        for relative in (
            "analysis/reverse-map/README.md",
            "analysis/reverse-map/state.md",
            "analysis/reverse-map/coverage.csv",
            "analysis/reverse-map/workitems.jsonl",
            "analysis/reverse-map/decisions.csv",
            "analysis/reverse-map/unresolved.csv",
            "analysis/reverse-map/scenarios/README.md",
            "analysis/reverse-map/outputs/README.md",
        ):
            self.require_path(relative, "reverse_map")

        coverage_rows = self.read_csv_rows("analysis/reverse-map/coverage.csv", REVERSE_MAP_COVERAGE_HEADER, "reverse_map.coverage")
        self.read_csv_rows("analysis/reverse-map/decisions.csv", REVERSE_MAP_DECISIONS_HEADER, "reverse_map.decisions")
        self.read_csv_rows("analysis/reverse-map/unresolved.csv", REVERSE_MAP_UNRESOLVED_HEADER, "reverse_map.unresolved")

        workitems_path = repo_path(self.root, "analysis/reverse-map/workitems.jsonl")
        workitems: list[dict[str, Any]] = []
        if workitems_path.exists():
            try:
                workitems = [item for _, item in read_jsonl(workitems_path)]
                self.checks.add("reverse_map.workitems.parse", "ok", "Reverse-map workitems JSONL parses")
            except Exception as exc:
                self.checks.add("reverse_map.workitems.parse", "fail", f"Invalid reverse-map workitems JSONL: {exc}")
        ids: set[str] = set()
        for item in workitems:
            workitem_id = str(item.get("id", "")).strip()
            if not workitem_id:
                self.checks.add("reverse_map.workitems.id", "fail", "Reverse-map workitem has empty id")
            elif workitem_id in ids:
                self.checks.add("reverse_map.workitems.duplicate_id", "fail", f"Duplicate reverse-map workitem id: {workitem_id}")
            else:
                ids.add(workitem_id)
            status = str(item.get("status", "")).strip()
            if status not in REVERSE_MAP_WORKITEM_STATUSES:
                self.checks.add("reverse_map.workitems.status", "fail", f"Reverse-map workitem {workitem_id or '<empty>'} has invalid status: {status or '<empty>'}")
            if not as_list(item.get("source_diff_ids")):
                self.checks.add("reverse_map.workitems.source_diff_ids", "warn", f"Reverse-map workitem {workitem_id or '<empty>'} has no source_diff_ids")
        if not workitems:
            self.checks.add("reverse_map.workitems.empty", "ok", "Reverse-map has no workitems yet")
        elif not any(check["id"] == "reverse_map.workitems.duplicate_id" for check in self.checks.checks):
            self.checks.add("reverse_map.workitems.unique_ids", "ok", "Reverse-map workitem ids are unique")

        coverage_ids: set[str] = set()
        coverage_by_status: dict[str, int] = {}
        for row in coverage_rows:
            if not any((value or "").strip() for value in row.values()):
                continue
            diff_id = row.get("diff_id", "").strip()
            if diff_id:
                coverage_ids.add(diff_id)
            status = row.get("status", "").strip()
            coverage_by_status[status or "<empty>"] = coverage_by_status.get(status or "<empty>", 0) + 1
            if status not in REVERSE_MAP_STATUSES:
                self.checks.add("reverse_map.coverage.status", "fail", f"Reverse-map coverage row {diff_id or '<empty>'} has invalid status: {status or '<empty>'}")
            if status in {"assigned", "confirmed_in_scenario", "supporting_shared", "needs_manual_review", "needs_infobase_data"} and not row.get("workitem_id", "").strip():
                self.checks.add("reverse_map.coverage.workitem_id", "fail", f"Reverse-map coverage row {diff_id or '<empty>'} with status {status} lacks workitem_id")
        self.checks.add("reverse_map.coverage.rows", "ok", f"Reverse-map coverage has {len(coverage_ids)} diff row(s)")
        if coverage_by_status:
            self.checks.add("reverse_map.coverage.status_counts", "ok", "Reverse-map coverage status counts", coverage_by_status)

        diff_path = repo_path(self.root, "analysis/indexes/diff-inventory.csv")
        if diff_path.exists() and diff_path.read_text(encoding="utf-8-sig").splitlines()[:1] == [DIFF_INVENTORY_HEADER]:
            with diff_path.open("r", encoding="utf-8-sig", newline="") as fh:
                diff_ids = {row.get("diff_id", "").strip() for row in csv.DictReader(fh) if row.get("diff_id", "").strip()}
            missing = sorted(diff_ids - coverage_ids)
            if missing:
                self.checks.add("reverse_map.coverage.missing_diff", "fail", f"Reverse-map coverage is missing {len(missing)} diff row(s); run `python -m one_c_autoresearch reverse-map seed`")
            else:
                self.checks.add("reverse_map.coverage.diff_complete", "ok", "Reverse-map coverage contains every diff inventory row")

    def test_unresolved_placeholders(self) -> None:
        found = False
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in {".md", ".toml", ".jsonl", ".py", ".txt", ".csv"} and path.name != ".gitignore":
                continue
            relative = path.relative_to(self.root).as_posix()
            if relative.startswith("src/one_c_autoresearch/") or relative.startswith("scripts/"):
                continue
            matches = list(re.finditer(r"__[A-Z][A-Z0-9_]*__", path.read_text(encoding="utf-8", errors="ignore")))
            if matches:
                found = True
                self.checks.add("research.unresolved_placeholder", "fail", f"Unresolved template placeholder in {relative}")
        if not found:
            self.checks.add("research.unresolved_placeholders", "ok", "No unresolved template placeholders found")

    def test_template_repo(self) -> None:
        for path in TEMPLATE_REQUIRED_PATHS:
            self.require_path(path, "template")
        self.test_queue("templates/research-repo/analysis/queue/tasks.jsonl")
        for customer_path in ("cf", "cfe", "analysis/cache", "outputs/do21-functional-customizations"):
            if self.exists(customer_path):
                self.checks.add("template.customer_artifact", "warn", f"Template contains project-specific looking path: {customer_path}")

    def test_research_repo(self) -> None:
        for path in RESEARCH_REQUIRED_PATHS:
            self.require_path(path, "research")
        manifest = self.test_project_toml("project.toml")
        self.test_queue("analysis/queue/tasks.jsonl")
        self.test_evidence_packs()
        self.test_reverse_map_contract()
        self.test_autopilot_contract(manifest)
        self.test_unresolved_placeholders()

    def run(self) -> dict[str, Any]:
        kind = self.repo_kind()
        if kind == "unknown":
            self.checks.add("repo.detect", "fail", "Could not detect repo kind. Expected template or research repo contract.")
        else:
            self.checks.add("repo.detect", "ok", f"Detected repo kind: {kind}")
        if self.mode != "auto" and kind != self.mode:
            self.checks.add("repo.mode", "fail", f"Requested mode {self.mode} but detected {kind}")
        if kind == "template":
            self.test_template_repo()
        elif kind == "research":
            self.test_research_repo()
        if self.deep:
            self.test_tool("git")
            self.test_tool("rg")
            self.checks.add("tool.python", "ok", f"Python runtime is available: {sys.executable}")
        ok, warn, fail = self.checks.counts()
        status = "fail" if fail else "warn" if warn else "ok"
        return {
            "status": status,
            "repo_kind": kind,
            "repo_path": str(self.root),
            "deep": self.deep,
            "generated_at": utc_now_iso(),
            "summary": {"ok": ok, "warn": warn, "fail": fail},
            "checks": self.checks.checks,
        }


def run_doctor(root: Path, mode: str = "auto", deep: bool = False, stale_claim_hours: int = 12) -> dict[str, Any]:
    return Doctor(root, mode=mode, deep=deep, stale_claim_hours=stale_claim_hours).run()


def print_doctor(result: dict[str, Any], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print("1C Autoresearch Doctor\n")
    print(f"Repo kind: {result['repo_kind']}")
    print(f"Repo path: {result['repo_path']}")
    print(f"Status: {result['status'].upper()}")
    summary = result["summary"]
    print(f"Checks: ok={summary['ok']} warn={summary['warn']} fail={summary['fail']}\n")
    for check in result["checks"]:
        print(f"[{check['status'].upper()}] {check['id']}: {check['message']}")
