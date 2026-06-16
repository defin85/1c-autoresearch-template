from __future__ import annotations

import json
import re
import sys
import csv
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .autopilot import DIFF_INVENTORY_HEADER, FEATURE_MAP_HEADER, FINAL_DIFF_INVENTORY_HEADER, INFOBASE_QUESTIONS_HEADER, OPEN_QUESTIONS_HEADER
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
from .detail_maps import DETAIL_MAP_GENERATION_MODES, DETAIL_MAP_INDEX_HEADER
from .reverse_map import (
    REVERSE_MAP_COVERAGE_HEADER,
    REVERSE_MAP_DECISIONS_HEADER,
    REVERSE_MAP_INFOBASE_CHECKS_HEADER,
    REVERSE_MAP_STATUSES,
    REVERSE_MAP_UNRESOLVED_HEADER,
    REVERSE_MAP_WORKITEM_STATUSES,
)
from .final_gate import FINAL_FEATURE_MAP_HEADER
from .functional_gaps import validate_functional_gaps

CANONICAL_EVIDENCE_HEADER = "feature_id,claim_id,source_kind,source_path,line_start,line_end,evidence_type,confidence,summary,notes"
CANONICAL_FEATURE_CANDIDATES_HEADER = "feature_id,title,source_bucket,classification,confidence,summary,next_step"
SUBJECT_CARD_CANDIDATES_HEADER = "candidate_id,title,proposed_slug,source,discovery_basis,linked_features,linked_detail_maps,primary_objects,subject_type,confidence,proposed_action,status,notes"
SUBJECT_CARD_CLASSIFICATION_HEADER = "candidate_id,proposed_slug,decision,subject_type,registry_slug,merge_into,split_from,why_separate_card,status,confidence,notes"
SUBJECT_CARD_REGISTRY_HEADER = "slug,title,subject_type,status,confidence,origin_layer,owner_feature,linked_features,linked_detail_maps,primary_objects,coverage_scope,why_separate_card,merge_into,split_from,card_path,evidence_count,gap_count,review_notes"
SUBJECT_CARD_COVERAGE_HEADER = "source_kind,source_id,feature_id,detail_map_slug,subject_card_slug,relation,confidence,notes"
SUBJECT_CARD_EVIDENCE_HEADER = "evidence_id,section,claim,source_type,source_path,line,linked_diff_id,linked_feature_id,confidence,notes"
SUBJECT_CARD_GAPS_HEADER = "gap_id,section,question,needed_source,status,blocking,notes"
SUBJECT_CARD_TYPES = {
    "business_process",
    "business_document",
    "reference_model",
    "integration",
    "access_model",
    "ui_surface",
    "background_automation",
    "technical_support",
}
VALID_STATUSES = {"pending", "claimed", "evidence_pack", "drafted", "needs_review", "needs_followup", "blocked", "done", "skipped"}
VALID_TYPES = {"discovery", "deep_dive", "review", "migration_map", "packaging", "needs_infobase_data"}
AUTOPILOT_DIFF_STATUSES = {"mapped_to_feature", "technical_noise_removed", "requires_1c_review", "blocked_by_infobase_data"}
AUTOPILOT_FEATURE_STATUSES = {
    "complete",
    "blocked_by_infobase_data",
    "requires_1c_review",
    "requires_runtime_verification",
    "needs_reclassification",
    "out_of_scope",
}
INFOBASE_CHECK_METHODS = {
    "1c_mcp_run_select_query",
    "1c_mcp_debug_execute_bsl",
    "playwright_1c_web_ui",
    "direct_postgresql_query",
    "manual_1c_scenario",
}
INFOBASE_CHECK_RESULTS = {
    "custom_only",
    "same_as_vendor",
    "vendor_differs",
    "runtime_only",
    "manual_scenario_required",
    "inconclusive",
}
INFOBASE_CHECK_FINAL_STATUSES = {
    "closed",
    "needs_infobase_data",
    "needs_runtime_verification",
    "needs_manual_review",
    "blocked_by_infobase_data",
}
INFOBASE_QUESTION_STATUSES = {"open", "closed", "blocked_by_infobase_data"}
DETAIL_MAP_TYPES = {"document", "catalog", "route", "scheduled_job", "rights", "integration", "report", "ui", "other"}
DETAIL_MAP_STATUSES = {
    "draft",
    "complete",
    "needs_review",
    "requires_1c_review",
    "needs_infobase_data",
    "requires_runtime_verification",
    "blocked_by_infobase_data",
}
DETAIL_MAP_SECTIONS = {
    "attributes",
    "form_rules",
    "validations",
    "lifecycle",
    "rights",
    "scheduled_jobs",
    "ui",
    "integrations",
    "sources",
    "open_questions",
}
SUBJECT_CARD_STATUSES = {
    "candidate",
    "accepted",
    "draft",
    "needs_static_analysis",
    "needs_runtime_data",
    "needs_ui_check",
    "needs_review",
    "ready_for_review",
    "reviewed",
    "rejected",
    "merged_into_other",
    "supporting",
    "unclassified",
}
SUBJECT_CARD_SECTIONS = DETAIL_MAP_SECTIONS


def spreadsheet_reference(value: str) -> bool:
    lowered = value.lower()
    return any(suffix in lowered for suffix in (".xlsx", ".xlsm", ".xlsb", ".xls#"))

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
    "docs/agent/index.md",
    "docs/agent/repo-map.md",
    "docs/agent/verification.md",
    "docs/method/1c-autoresearch-process.md",
    "docs/method/evidence-pack-schema.md",
    "docs/method/autopilot-customization-map.md",
    "docs/method/physical-clean-comparison.md",
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
    "templates/research-repo/docs/method/physical-clean-comparison.md",
    "templates/research-repo/docs/method/reverse-functional-map.md",
    "templates/research-repo/analysis/indexes/README.md",
    "templates/research-repo/analysis/indexes/diff-inventory.csv",
    "templates/research-repo/analysis/indexes/feature-map.csv",
    "templates/research-repo/analysis/indexes/final-diff-inventory.csv",
    "templates/research-repo/analysis/indexes/final-feature-map.csv",
    "templates/research-repo/analysis/clean-comparison/README.md",
    "templates/research-repo/analysis/detail-maps/README.md",
    "templates/research-repo/analysis/detail-maps/index.csv",
    "templates/research-repo/analysis/detail-maps/_templates/detail-map.json",
    "templates/research-repo/analysis/subject-cards/README.md",
    "templates/research-repo/analysis/subject-cards/registry.csv",
    "templates/research-repo/analysis/subject-cards/candidates.csv",
    "templates/research-repo/analysis/subject-cards/classification.csv",
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
    "src/one_c_autoresearch/detail_maps.py",
    "src/one_c_autoresearch/subject_cards.py",
    "src/one_c_autoresearch/functional_gaps.py",
    "src/one_c_autoresearch/functional_gap_dashboard.py",
    "src/one_c_autoresearch/functional_gap_probes.py",
    "src/one_c_autoresearch/final_gate.py",
    "src/one_c_autoresearch/review_dashboard.py",
    "src/one_c_autoresearch/reverse_map.py",
    "src/one_c_autoresearch/cli.py",
    "docs/agent/index.md",
    "docs/agent/repo-map.md",
    "docs/agent/verification.md",
    "docs/method/1c-autoresearch-process.md",
    "docs/method/evidence-pack-schema.md",
    "docs/method/autopilot-customization-map.md",
    "docs/method/physical-clean-comparison.md",
    "docs/method/reverse-functional-map.md",
    "analysis/indexes/README.md",
    "analysis/indexes/diff-inventory.csv",
    "analysis/indexes/feature-map.csv",
    "analysis/indexes/final-diff-inventory.csv",
    "analysis/indexes/final-feature-map.csv",
    "analysis/clean-comparison/README.md",
    "analysis/detail-maps/README.md",
    "analysis/detail-maps/index.csv",
    "analysis/detail-maps/_templates/detail-map.json",
    "analysis/subject-cards/README.md",
    "analysis/subject-cards/registry.csv",
    "analysis/subject-cards/candidates.csv",
    "analysis/subject-cards/classification.csv",
    "analysis/subject-cards/coverage.csv",
    "analysis/subject-cards/_templates/subject-card.json",
    "analysis/functional-gaps/README.md",
    "analysis/functional-gaps/index.csv",
    "analysis/functional-gaps/coverage.csv",
    "analysis/functional-gaps/open-questions.csv",
    "analysis/functional-gaps/_templates/gap-card.json",
    "analysis/functional-gaps/_templates/target-profile.toml",
    "analysis/functional-gaps/profiles/.gitkeep",
    "analysis/reverse-map/README.md",
    "analysis/reverse-map/state.md",
    "analysis/reverse-map/coverage.csv",
    "analysis/reverse-map/workitems.jsonl",
    "analysis/reverse-map/decisions.csv",
    "analysis/reverse-map/unresolved.csv",
    "analysis/reverse-map/infobase-checks.csv",
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
    "outputs/review/README.md",
    "outputs/open-questions.csv",
    "outputs/infobase-questions.csv",
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
            self.checks.add(f"manifest.rlm.{key}", "ok", f"RLM project is {'set' if value else 'not configured'}: {key}" + ("" if value else " (optional)"))

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

    def test_autopilot_subject_card_gate(self, final_feature_rows: list[dict[str, str]]) -> None:
        registry_rows = [
            row
            for row in self.read_csv_rows("analysis/subject-cards/registry.csv", SUBJECT_CARD_REGISTRY_HEADER, "autopilot.subject_cards.registry")
            if any((value or "").strip() for value in row.values())
        ]
        coverage_rows = [
            row
            for row in self.read_csv_rows("analysis/subject-cards/coverage.csv", SUBJECT_CARD_COVERAGE_HEADER, "autopilot.subject_cards.coverage")
            if any((value or "").strip() for value in row.values())
        ]
        if not registry_rows:
            self.checks.add("autopilot.subject_cards.registry_rows", "fail", "Autopilot map has no subject-card registry rows; BF containers are not analyst-ready cards")
        else:
            self.checks.add("autopilot.subject_cards.registry_rows", "ok", f"Subject-card registry has {len(registry_rows)} row(s)")

        registry_by_slug = {row.get("slug", "").strip(): row for row in registry_rows if row.get("slug", "").strip()}
        card_paths = [row.get("card_path", "").strip() for row in registry_rows if row.get("card_path", "").strip()]
        if not card_paths:
            self.checks.add("autopilot.subject_cards.card_paths", "fail", "Subject-card registry has no concrete card_path rows; dashboard would show only BF containers")
        else:
            self.checks.add("autopilot.subject_cards.card_paths", "ok", f"Subject-card registry points to {len(card_paths)} card bundle(s)")

        card_root = repo_path(self.root, "analysis/subject-cards/cards")
        card_files = sorted(card_root.glob("*/subject-card.json")) if card_root.exists() else []
        if not card_files:
            self.checks.add("autopilot.subject_cards.cards", "fail", "No subject-card bundles found under analysis/subject-cards/cards")
        else:
            self.checks.add("autopilot.subject_cards.cards", "ok", f"Found {len(card_files)} subject-card bundle(s)")

        ready_cards: list[str] = []
        for path in card_files:
            relative = path.relative_to(self.root).as_posix()
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.checks.add("autopilot.subject_cards.parse", "fail", f"Could not parse {relative}: {exc}")
                continue
            status = str(payload.get("status") or "").strip()
            slug = str(payload.get("slug") or path.parent.name).strip()
            if status in {"ready_for_review", "reviewed"}:
                ready_cards.append(slug)
            if slug and slug not in registry_by_slug:
                self.checks.add("autopilot.subject_cards.registry_link", "fail", f"Subject-card {slug} is not present in analysis/subject-cards/registry.csv")
        if ready_cards:
            self.checks.add("autopilot.subject_cards.ready", "ok", f"Subject-card layer has {len(ready_cards)} ready card(s)")
        else:
            self.checks.add("autopilot.subject_cards.ready", "fail", "Autopilot map has no subject cards in ready_for_review or reviewed status")

        publishable_feature_ids = {
            row.get("feature_id", "").strip()
            for row in final_feature_rows
            if row.get("feature_id", "").strip() and row.get("status", "").strip() not in {"out_of_scope", "needs_reclassification"}
        }
        coverage_by_feature: dict[str, list[dict[str, str]]] = {}
        for row in coverage_rows:
            if row.get("source_kind", "").strip() != "BF":
                continue
            feature_id = row.get("feature_id", "").strip()
            if feature_id:
                coverage_by_feature.setdefault(feature_id, []).append(row)
        for feature_id in sorted(publishable_feature_ids):
            rows = coverage_by_feature.get(feature_id, [])
            if not rows:
                self.checks.add("autopilot.subject_cards.bf_coverage", "fail", f"Publishable BF {feature_id} is absent from subject-card coverage")
                continue
            useful_rows = [row for row in rows if row.get("relation", "").strip() in {"covered_by_subject_card", "supporting", "shared", "rejected", "technical_support"}]
            if not useful_rows:
                self.checks.add("autopilot.subject_cards.bf_unclassified", "fail", f"Publishable BF {feature_id} is still unclassified in subject-card coverage")
                continue
            for row in useful_rows:
                relation = row.get("relation", "").strip()
                subject_slug = row.get("subject_card_slug", "").strip()
                if relation == "covered_by_subject_card" and (not subject_slug or not registry_by_slug.get(subject_slug, {}).get("card_path", "").strip()):
                    self.checks.add("autopilot.subject_cards.bf_card_link", "fail", f"Publishable BF {feature_id} claims card coverage but has no concrete subject-card path")
                if relation != "covered_by_subject_card" and not row.get("notes", "").strip():
                    self.checks.add("autopilot.subject_cards.bf_decision_notes", "fail", f"Publishable BF {feature_id} has non-card relation {relation} without rationale notes")
        if publishable_feature_ids and not any(check["id"] in {"autopilot.subject_cards.bf_coverage", "autopilot.subject_cards.bf_unclassified", "autopilot.subject_cards.bf_card_link", "autopilot.subject_cards.bf_decision_notes"} for check in self.checks.checks):
            self.checks.add("autopilot.subject_cards.bf_coverage_complete", "ok", f"Subject-card coverage classifies {len(publishable_feature_ids)} publishable BF container(s)")

        dashboard_data_path = repo_path(self.root, "outputs/review/data.json")
        if not dashboard_data_path.exists():
            self.checks.add("autopilot.subject_cards.dashboard_data", "fail", "Missing analyst dashboard data: outputs/review/data.json")
            return
        try:
            dashboard_data = json.loads(dashboard_data_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.checks.add("autopilot.subject_cards.dashboard_data", "fail", f"Could not parse outputs/review/data.json: {exc}")
            return
        dashboard_cards = dashboard_data.get("subject_cards") if isinstance(dashboard_data, dict) else []
        dashboard_registry = dashboard_data.get("subject_registry") if isinstance(dashboard_data, dict) else []
        if not isinstance(dashboard_cards, list) or not dashboard_cards:
            self.checks.add("autopilot.subject_cards.dashboard_cards", "fail", "Review dashboard data has no subject_cards; first analyst screen would be empty")
        else:
            self.checks.add("autopilot.subject_cards.dashboard_cards", "ok", f"Review dashboard exposes {len(dashboard_cards)} subject card(s)")
        if not isinstance(dashboard_registry, list) or not dashboard_registry:
            self.checks.add("autopilot.subject_cards.dashboard_registry", "fail", "Review dashboard data has no subject_registry rows")
        else:
            self.checks.add("autopilot.subject_cards.dashboard_registry", "ok", f"Review dashboard exposes {len(dashboard_registry)} subject registry row(s)")

    def test_autopilot_contract(self, manifest: dict[str, Any] | None) -> None:
        if not manifest or not toml_enabled(manifest, "autopilot"):
            self.checks.add("autopilot.enabled", "ok", "Autopilot final gate is disabled")
            return
        self.checks.add("autopilot.enabled", "ok", "Autopilot final gate is enabled")
        for relative in (
            "analysis/indexes/diff-inventory.csv",
            "analysis/indexes/feature-map.csv",
            "analysis/indexes/final-diff-inventory.csv",
            "analysis/indexes/final-feature-map.csv",
            "outputs/customization-map.md",
            "outputs/customization-map.xlsx",
            "outputs/open-questions.csv",
            "outputs/infobase-questions.csv",
            "outputs/open-questions.xlsx",
            "outputs/review/index.html",
            "outputs/review/data.json",
            "analysis/final-audit.md",
        ):
            self.require_path(relative, "autopilot")

        diff_rows = self.read_csv_rows("analysis/indexes/diff-inventory.csv", DIFF_INVENTORY_HEADER, "autopilot.diff_inventory")
        feature_rows = self.read_csv_rows("analysis/indexes/feature-map.csv", FEATURE_MAP_HEADER, "autopilot.feature_map")
        final_diff_rows = self.read_csv_rows("analysis/indexes/final-diff-inventory.csv", FINAL_DIFF_INVENTORY_HEADER, "final_gate.diff_inventory")
        final_feature_rows = self.read_csv_rows("analysis/indexes/final-feature-map.csv", FINAL_FEATURE_MAP_HEADER, "final_gate.feature_map")
        open_question_rows = self.read_csv_rows("outputs/open-questions.csv", OPEN_QUESTIONS_HEADER, "autopilot.open_questions")
        infobase_question_rows = self.read_csv_rows("outputs/infobase-questions.csv", INFOBASE_QUESTIONS_HEADER, "autopilot.infobase_questions")
        infobase_check_rows = self.read_csv_rows("analysis/reverse-map/infobase-checks.csv", REVERSE_MAP_INFOBASE_CHECKS_HEADER, "autopilot.infobase_checks")
        coverage_rows = self.read_csv_rows("analysis/reverse-map/coverage.csv", REVERSE_MAP_COVERAGE_HEADER, "final_gate.coverage")
        unresolved_rows = self.read_csv_rows("analysis/reverse-map/unresolved.csv", REVERSE_MAP_UNRESOLVED_HEADER, "final_gate.unresolved")
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

        final_diff_ids = {row.get("diff_id", "").strip() for row in final_diff_rows if row.get("diff_id", "").strip()}
        primary_diff_ids = {row.get("diff_id", "").strip() for row in diff_rows if row.get("diff_id", "").strip()}
        if primary_diff_ids and final_diff_ids != primary_diff_ids:
            missing = sorted(primary_diff_ids - final_diff_ids)
            extra = sorted(final_diff_ids - primary_diff_ids)
            self.checks.add("final_gate.diff_inventory.coverage", "fail", f"Final-gate diff inventory must cover every primary diff row; missing={len(missing)} extra={len(extra)}")
        elif primary_diff_ids:
            self.checks.add("final_gate.diff_inventory.coverage", "ok", "Final-gate diff inventory covers every primary diff row")

        coverage_by_diff = {row.get("diff_id", "").strip(): row for row in coverage_rows if row.get("diff_id", "").strip()}
        stale_rows = []
        for row in final_diff_rows:
            diff_id = row.get("diff_id", "").strip()
            coverage = coverage_by_diff.get(diff_id, {})
            if coverage and row.get("reverse_status", "").strip() != coverage.get("status", "").strip():
                stale_rows.append(diff_id)
        if stale_rows:
            self.checks.add("final_gate.diff_inventory.stale", "fail", f"Final-gate diff inventory is stale for {len(stale_rows)} reverse-map row(s); rerun `python -m one_c_autoresearch final-gate build`")
        elif final_diff_rows:
            self.checks.add("final_gate.diff_inventory.fresh", "ok", "Final-gate diff inventory matches current reverse-map coverage statuses")

        final_feature_by_id = {row.get("feature_id", "").strip(): row for row in final_feature_rows if row.get("feature_id", "").strip()}
        for row in final_feature_rows:
            feature_id = row.get("feature_id", "").strip()
            status = row.get("status", "").strip()
            if status not in AUTOPILOT_FEATURE_STATUSES:
                self.checks.add("final_gate.feature_map.status", "fail", f"Final feature {feature_id or '<empty feature_id>'} has invalid status: {status or '<empty>'}")
        for row in final_diff_rows:
            action = row.get("final_action", "").strip()
            feature_id = row.get("feature_id", "").strip()
            final_status = row.get("final_status", "").strip()
            feature = final_feature_by_id.get(feature_id)
            if action == "block" and feature and feature.get("status", "").strip() == "complete":
                self.checks.add("final_gate.feature_blocked", "fail", f"Feature {feature_id} is complete but diff row {row.get('diff_id', '<empty diff_id>')} is blocked by reverse-map status {row.get('reverse_status', '<empty>')}")
            if action == "block" and final_status not in {"requires_1c_review", "blocked_by_infobase_data", "requires_runtime_verification", "needs_reclassification"}:
                self.checks.add("final_gate.diff_block_status", "fail", f"Blocked diff row {row.get('diff_id', '<empty diff_id>')} has invalid final_status: {final_status or '<empty>'}")
        blocking_final_rows = [row for row in final_diff_rows if row.get("final_action", "").strip() == "block"]
        if blocking_final_rows:
            self.checks.add("final_gate.blocking_rows", "fail", f"Final publication is blocked by {len(blocking_final_rows)} reverse-map row(s)")
        if final_diff_rows and not any(check["id"] == "final_gate.feature_blocked" for check in self.checks.checks):
            self.checks.add("final_gate.feature_blocking", "ok", "Final feature statuses account for reverse-map blockers")

        self.test_autopilot_subject_card_gate(final_feature_rows)

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

        self.test_infobase_question_gate(infobase_question_rows, infobase_check_rows, open_question_rows)

        unresolved_ids = {row.get("item_id", "").strip() for row in unresolved_rows if row.get("item_id", "").strip()}
        if unresolved_ids:
            open_question_refs = {
                row.get("question_id", "").strip()
                for row in open_question_rows
                if row.get("question_id", "").strip()
            }
            open_question_refs.update(
                ref.rsplit("#", 1)[-1]
                for ref in (row.get("source_ref", "").strip() for row in open_question_rows)
                if "#" in ref
            )
            missing = sorted(unresolved_ids - open_question_refs)
            if missing:
                self.checks.add("final_gate.open_questions.unresolved_coverage", "fail", f"Open questions do not cover {len(missing)} reverse-map unresolved item(s)")
            else:
                self.checks.add("final_gate.open_questions.unresolved_coverage", "ok", "Open questions cover every reverse-map unresolved item")

        audit = repo_path(self.root, "analysis/final-audit.md")
        if audit.exists():
            text = audit.read_text(encoding="utf-8", errors="ignore")
            audit_declares_complete = bool(re.search(r"(?im)^Coverage status:\s*complete\s*$", text))
            if blocking_final_rows and audit_declares_complete:
                self.checks.add("final_gate.final_audit.blocked_complete", "fail", "Final audit declares complete coverage while final-gate has blocking rows")
            elif blocking_final_rows:
                self.checks.add("final_gate.final_audit.blocked_status", "ok", "Final audit does not claim complete coverage while final-gate has blocking rows")
            elif not audit_declares_complete:
                self.checks.add("autopilot.final_audit.coverage_status", "fail", "Final audit must contain 'Coverage status: complete'")
            else:
                self.checks.add("autopilot.final_audit.coverage_status", "ok", "Final audit declares complete coverage")
            if not re.search(r"(?im)^Unclassified diff entries:\s*0\s*$", text):
                self.checks.add("autopilot.final_audit.unclassified_zero", "fail", "Final audit must contain 'Unclassified diff entries: 0'")
            else:
                self.checks.add("autopilot.final_audit.unclassified_zero", "ok", "Final audit declares zero unclassified diff entries")

    def test_infobase_question_gate(
        self,
        infobase_question_rows: list[dict[str, str]],
        infobase_check_rows: list[dict[str, str]],
        open_question_rows: list[dict[str, str]],
    ) -> None:
        closed_question_ids: set[str] = set()
        for row in infobase_check_rows:
            if row.get("status_after_pass", "").strip() != "closed":
                continue
            item_id = row.get("item_id", "").strip()
            if item_id:
                closed_question_ids.add(item_id)
            question_ref = row.get("question_ref", "").strip()
            if "#" in question_ref:
                closed_question_ids.add(question_ref.rsplit("#", 1)[-1])

        final_open_refs: set[str] = set()
        for row in open_question_rows:
            question_id = row.get("question_id", "").strip()
            if question_id:
                final_open_refs.add(question_id)
            source_ref = row.get("source_ref", "").strip()
            if "#" in source_ref:
                final_open_refs.add(source_ref.rsplit("#", 1)[-1])

        question_ids: set[str] = set()
        unresolved: list[str] = []
        closed_count = 0
        for index, row in enumerate(infobase_question_rows, 1):
            if not any((value or "").strip() for value in row.values()):
                continue
            question_id = row.get("question_id", "").strip() or f"<row {index}>"
            if question_id in question_ids:
                self.checks.add("autopilot.infobase_questions.duplicate_id", "fail", f"Duplicate infobase question id: {question_id}")
            question_ids.add(question_id)
            status = row.get("status", "").strip()
            if status not in INFOBASE_QUESTION_STATUSES:
                self.checks.add("autopilot.infobase_questions.status", "fail", f"Infobase question {question_id} has invalid status: {status or '<empty>'}")
            for field in ("feature_id", "reason", "closing_result", "risk_if_open", "source_ref"):
                if not row.get(field, "").strip():
                    self.checks.add("autopilot.infobase_questions.required_fields", "fail", f"Infobase question {question_id} is missing {field}")
            if question_id in closed_question_ids:
                closed_count += 1
            elif status == "closed":
                self.checks.add("autopilot.infobase_questions.closed_without_check", "fail", f"Infobase question {question_id} is closed without a closed infobase-check row")
            elif question_id not in final_open_refs:
                unresolved.append(question_id)

        if question_ids:
            self.checks.add("autopilot.infobase_questions.rows", "ok", f"Infobase question registry has {len(question_ids)} row(s), closed_by_checks={closed_count}")
        else:
            self.checks.add("autopilot.infobase_questions.empty", "ok", "No infobase questions recorded")
        if unresolved:
            self.checks.add("autopilot.infobase_questions.unresolved", "fail", f"{len(unresolved)} open infobase question(s) are neither closed by infobase-checks.csv nor carried into outputs/open-questions.csv")
        elif question_ids:
            self.checks.add("autopilot.infobase_questions.coverage", "ok", "Every infobase question is closed by a live check or carried into final open questions")

    def test_reverse_map_contract(self) -> None:
        for relative in (
            "analysis/reverse-map/README.md",
            "analysis/reverse-map/state.md",
            "analysis/reverse-map/coverage.csv",
            "analysis/reverse-map/workitems.jsonl",
            "analysis/reverse-map/decisions.csv",
            "analysis/reverse-map/unresolved.csv",
            "analysis/reverse-map/infobase-checks.csv",
            "analysis/reverse-map/scenarios/README.md",
            "analysis/reverse-map/outputs/README.md",
        ):
            self.require_path(relative, "reverse_map")

        coverage_rows = self.read_csv_rows("analysis/reverse-map/coverage.csv", REVERSE_MAP_COVERAGE_HEADER, "reverse_map.coverage")
        self.read_csv_rows("analysis/reverse-map/decisions.csv", REVERSE_MAP_DECISIONS_HEADER, "reverse_map.decisions")
        unresolved_rows = self.read_csv_rows("analysis/reverse-map/unresolved.csv", REVERSE_MAP_UNRESOLVED_HEADER, "reverse_map.unresolved")
        infobase_check_rows = self.read_csv_rows("analysis/reverse-map/infobase-checks.csv", REVERSE_MAP_INFOBASE_CHECKS_HEADER, "reverse_map.infobase_checks")

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

        self.test_infobase_checks_contract(unresolved_rows, infobase_check_rows)

        diff_path = repo_path(self.root, "analysis/indexes/diff-inventory.csv")
        if diff_path.exists() and diff_path.read_text(encoding="utf-8-sig").splitlines()[:1] == [DIFF_INVENTORY_HEADER]:
            with diff_path.open("r", encoding="utf-8-sig", newline="") as fh:
                diff_ids = {row.get("diff_id", "").strip() for row in csv.DictReader(fh) if row.get("diff_id", "").strip()}
            missing = sorted(diff_ids - coverage_ids)
            if missing:
                self.checks.add("reverse_map.coverage.missing_diff", "fail", f"Reverse-map coverage is missing {len(missing)} diff row(s); run `python -m one_c_autoresearch reverse-map seed`")
            else:
                self.checks.add("reverse_map.coverage.diff_complete", "ok", "Reverse-map coverage contains every diff inventory row")

    def test_infobase_checks_contract(self, unresolved_rows: list[dict[str, str]], infobase_check_rows: list[dict[str, str]]) -> None:
        check_ids: set[str] = set()
        covered_item_ids: set[str] = set()
        closed_count = 0
        for row in infobase_check_rows:
            if not any((value or "").strip() for value in row.values()):
                continue
            check_id = row.get("check_id", "").strip()
            item_id = row.get("item_id", "").strip()
            if not check_id:
                self.checks.add("reverse_map.infobase_checks.check_id", "fail", "Infobase check row has empty check_id")
            elif check_id in check_ids:
                self.checks.add("reverse_map.infobase_checks.duplicate_id", "fail", f"Duplicate infobase check id: {check_id}")
            else:
                check_ids.add(check_id)
            if item_id:
                covered_item_ids.add(item_id)
            else:
                self.checks.add("reverse_map.infobase_checks.item_id", "fail", f"Infobase check {check_id or '<empty>'} has empty item_id")

            method = row.get("check_method", "").strip()
            if method and method not in INFOBASE_CHECK_METHODS:
                self.checks.add("reverse_map.infobase_checks.method", "fail", f"Infobase check {check_id or '<empty>'} has invalid method: {method}")
            elif not method:
                self.checks.add("reverse_map.infobase_checks.method", "fail", f"Infobase check {check_id or '<empty>'} has empty method")

            result = row.get("result", "").strip()
            if result and result not in INFOBASE_CHECK_RESULTS:
                self.checks.add("reverse_map.infobase_checks.result", "fail", f"Infobase check {check_id or '<empty>'} has invalid result: {result}")

            status_after = row.get("status_after_pass", "").strip()
            if status_after and status_after not in INFOBASE_CHECK_FINAL_STATUSES:
                self.checks.add("reverse_map.infobase_checks.status_after_pass", "fail", f"Infobase check {check_id or '<empty>'} has invalid status_after_pass: {status_after}")
            elif not status_after:
                self.checks.add("reverse_map.infobase_checks.status_after_pass", "fail", f"Infobase check {check_id or '<empty>'} has empty status_after_pass")
            elif status_after == "closed":
                closed_count += 1
                for field in ("result", "evidence_ref"):
                    if not row.get(field, "").strip():
                        self.checks.add("reverse_map.infobase_checks.closed_required_fields", "fail", f"Closed infobase check {check_id or '<empty>'} is missing {field}")

            if method.startswith("1c_mcp") and not row.get("custom_target", "").strip():
                self.checks.add("reverse_map.infobase_checks.custom_target", "fail", f"1C MCP infobase check {check_id or '<empty>'} lacks custom_target")
            if result in {"same_as_vendor", "vendor_differs"} and not row.get("vendor_target", "").strip():
                self.checks.add("reverse_map.infobase_checks.vendor_target", "fail", f"Vendor-comparison infobase check {check_id or '<empty>'} lacks vendor_target")

        if check_ids:
            self.checks.add("reverse_map.infobase_checks.rows", "ok", f"Reverse-map infobase checks contain {len(check_ids)} row(s), closed={closed_count}")
        else:
            self.checks.add("reverse_map.infobase_checks.empty", "ok", "No reverse-map infobase checks recorded yet")

        unresolved_infobase_ids = {
            row.get("item_id", "").strip()
            for row in unresolved_rows
            if row.get("item_id", "").strip() and row.get("status", "").strip() == "needs_infobase_data"
        }
        missing_checks = sorted(unresolved_infobase_ids - covered_item_ids)
        if missing_checks:
            self.checks.add("reverse_map.infobase_checks.missing_for_unresolved", "warn", f"{len(missing_checks)} needs_infobase_data unresolved item(s) have no infobase-check row")
        elif unresolved_infobase_ids:
            self.checks.add("reverse_map.infobase_checks.unresolved_coverage", "ok", "Every needs_infobase_data unresolved item has an infobase-check row")

    def test_detail_maps_contract(self) -> None:
        self.require_path("analysis/detail-maps/README.md", "detail_maps")
        index_path = repo_path(self.root, "analysis/detail-maps/index.csv")
        if index_path.exists():
            header = index_path.read_text(encoding="utf-8-sig").splitlines()[:1]
            if header == [DETAIL_MAP_INDEX_HEADER]:
                self.checks.add("detail_maps.index.header", "ok", "Detail-map index header matches the contract")
            else:
                self.checks.add("detail_maps.index.header", "fail", "Detail-map index header does not match the contract")
        self.require_path("analysis/detail-maps/_templates/detail-map.json", "detail_maps")
        root = repo_path(self.root, "analysis/detail-maps")
        if not root.exists():
            return
        maps = [
            path
            for path in sorted(root.rglob("detail-map.json"))
            if "_templates" not in path.relative_to(root).parts
        ]
        if not maps:
            self.checks.add("detail_maps.empty", "ok", "No detail maps defined yet")
            return
        slugs: set[str] = set()
        for path in maps:
            relative = path.relative_to(self.root).as_posix()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.checks.add("detail_maps.parse", "fail", f"Could not parse {relative}: {exc}")
                continue
            for field in ("id", "slug", "title", "type", "generation_mode", "completeness", "status", "confidence", "owner_feature", "linked_features", "summary"):
                value = data.get(field)
                if value in (None, "", []):
                    self.checks.add("detail_maps.required_field", "fail", f"{relative} is missing required field: {field}")
            slug = str(data.get("slug") or path.parent.name).strip()
            if slug in slugs:
                self.checks.add("detail_maps.duplicate_slug", "fail", f"Duplicate detail-map slug: {slug}")
            elif slug:
                slugs.add(slug)
            map_type = str(data.get("type", "")).strip()
            if map_type and map_type not in DETAIL_MAP_TYPES:
                self.checks.add("detail_maps.type", "fail", f"{relative} has invalid type: {map_type}")
            status = str(data.get("status", "")).strip()
            if status and status not in DETAIL_MAP_STATUSES:
                self.checks.add("detail_maps.status", "fail", f"{relative} has invalid status: {status}")
            generation_mode = str(data.get("generation_mode", "")).strip()
            if generation_mode and generation_mode not in DETAIL_MAP_GENERATION_MODES:
                self.checks.add("detail_maps.generation_mode", "fail", f"{relative} has invalid generation_mode: {generation_mode}")
            if data.get("linked_features") is not None and not isinstance(data.get("linked_features"), list):
                self.checks.add("detail_maps.linked_features", "fail", f"{relative} linked_features must be a list")
            for section in DETAIL_MAP_SECTIONS:
                if section in data and not isinstance(data[section], list):
                    self.checks.add("detail_maps.section_type", "fail", f"{relative} section {section} must be a list")
        if slugs:
            self.checks.add("detail_maps.rows", "ok", f"Detail-map contract has {len(slugs)} map(s)")

    def test_subject_cards_contract(self) -> None:
        self.require_path("analysis/subject-cards/README.md", "subject_cards")
        self.require_path("analysis/subject-cards/_templates/subject-card.json", "subject_cards")
        candidates_path = repo_path(self.root, "analysis/subject-cards/candidates.csv")
        if candidates_path.exists():
            header = candidates_path.read_text(encoding="utf-8-sig").splitlines()[:1]
            if header == [SUBJECT_CARD_CANDIDATES_HEADER]:
                self.checks.add("subject_cards.candidates.header", "ok", "Subject-card candidates header matches the contract")
            else:
                self.checks.add("subject_cards.candidates.header", "fail", "Subject-card candidates header does not match the contract")
        classification_path = repo_path(self.root, "analysis/subject-cards/classification.csv")
        if classification_path.exists():
            header = classification_path.read_text(encoding="utf-8-sig").splitlines()[:1]
            if header == [SUBJECT_CARD_CLASSIFICATION_HEADER]:
                self.checks.add("subject_cards.classification.header", "ok", "Subject-card classification header matches the contract")
            else:
                self.checks.add("subject_cards.classification.header", "fail", "Subject-card classification header does not match the contract")
        registry_path = repo_path(self.root, "analysis/subject-cards/registry.csv")
        registry_rows: list[dict[str, str]] = []
        if registry_path.exists():
            header = registry_path.read_text(encoding="utf-8-sig").splitlines()[:1]
            if header == [SUBJECT_CARD_REGISTRY_HEADER]:
                self.checks.add("subject_cards.registry.header", "ok", "Subject-card registry header matches the contract")
                with registry_path.open("r", encoding="utf-8-sig", newline="") as fh:
                    registry_rows = [row for row in csv.DictReader(fh) if any((value or "").strip() for value in row.values())]
                invalid_types = [row for row in registry_rows if row.get("subject_type") not in SUBJECT_CARD_TYPES]
                if invalid_types:
                    self.checks.add("subject_cards.registry.subject_type", "fail", f"Registry contains invalid subject_type in {len(invalid_types)} row(s)")
                else:
                    self.checks.add("subject_cards.registry.subject_type", "ok", "Registry subject_type values are controlled")
            else:
                self.checks.add("subject_cards.registry.header", "fail", "Subject-card registry header does not match the contract")
        coverage_path = repo_path(self.root, "analysis/subject-cards/coverage.csv")
        coverage_rows: list[dict[str, str]] = []
        if coverage_path.exists():
            header = coverage_path.read_text(encoding="utf-8-sig").splitlines()[:1]
            if header == [SUBJECT_CARD_COVERAGE_HEADER]:
                self.checks.add("subject_cards.coverage.header", "ok", "Subject-card coverage header matches the contract")
                with coverage_path.open("r", encoding="utf-8-sig", newline="") as fh:
                    coverage_rows = [row for row in csv.DictReader(fh) if any((value or "").strip() for value in row.values())]
            else:
                self.checks.add("subject_cards.coverage.header", "fail", "Subject-card coverage header does not match the contract")
        root = repo_path(self.root, "analysis/subject-cards/cards")
        if not root.exists():
            self.checks.add("subject_cards.cards_dir", "ok", "No subject-card cards directory yet; run `python -m one_c_autoresearch subject-card seed` after discovery")
            return
        cards = sorted(root.glob("*/subject-card.json"))
        if not cards:
            self.checks.add("subject_cards.empty", "ok", "No subject cards defined yet")
            return
        slugs: set[str] = set()
        ready_count = 0
        for path in cards:
            relative = path.relative_to(self.root).as_posix()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.checks.add("subject_cards.parse", "fail", f"Could not parse {relative}: {exc}")
                continue
            if data.get("schema_version") != "subject-card/v1":
                self.checks.add("subject_cards.schema_version", "fail", f"{relative} must declare schema_version=subject-card/v1")
            for field in ("slug", "title", "status", "confidence", "subject_type", "origin_layer", "coverage_scope", "why_separate_card", "summary", "identification", "key_conclusion", "upgrade_risk", "linked_features", "sections"):
                value = data.get(field)
                if value in (None, "", []):
                    self.checks.add("subject_cards.required_field", "fail", f"{relative} is missing required field: {field}")
            slug = str(data.get("slug") or path.parent.name).strip()
            if slug in slugs:
                self.checks.add("subject_cards.duplicate_slug", "fail", f"Duplicate subject-card slug: {slug}")
            elif slug:
                slugs.add(slug)
            status = str(data.get("status", "")).strip()
            if status and status not in SUBJECT_CARD_STATUSES:
                self.checks.add("subject_cards.status", "fail", f"{relative} has invalid status: {status}")
            if status == "ready_for_review":
                ready_count += 1
            subject_type = str(data.get("subject_type", "")).strip()
            if subject_type and subject_type not in SUBJECT_CARD_TYPES:
                self.checks.add("subject_cards.subject_type", "fail", f"{relative} has invalid subject_type: {subject_type}")
            if data.get("linked_features") is not None and not isinstance(data.get("linked_features"), list):
                self.checks.add("subject_cards.linked_features", "fail", f"{relative} linked_features must be a list")
            for source in as_list(data.get("source_artifacts")):
                source_text = str(source or "").strip()
                if spreadsheet_reference(source_text):
                    self.checks.add("subject_cards.source_artifact", "fail", f"{relative} uses Excel as source artifact: {source_text}")
            sections = data.get("sections")
            if not isinstance(sections, dict):
                self.checks.add("subject_cards.sections", "fail", f"{relative} sections must be an object")
                sections = {}
            for section in SUBJECT_CARD_SECTIONS:
                if section in sections and not isinstance(sections[section], list):
                    self.checks.add("subject_cards.section_type", "fail", f"{relative} section {section} must be a list")
                    continue
                for index, row in enumerate(as_list(sections.get(section)), start=1):
                    if not isinstance(row, dict):
                        continue
                    if section == "open_questions":
                        if not str(row.get("question") or "").strip():
                            self.checks.add("subject_cards.section_question", "fail", f"{relative} section {section} row {index} must contain question")
                    elif not str(row.get("claim") or "").strip():
                        self.checks.add("subject_cards.section_claim", "fail", f"{relative} section {section} row {index} must contain claim; technical rows belong in linked detail-map")
                    for key in ("source", "source_path"):
                        source_text = str(row.get(key) or "").strip()
                        if spreadsheet_reference(source_text):
                            self.checks.add("subject_cards.section_source", "fail", f"{relative} section {section} row {index} uses Excel as source: {source_text}")
            evidence_path = path.parent / "evidence.csv"
            gaps_path = path.parent / "gaps.csv"
            review_path = path.parent / "review.md"
            for child_path in (evidence_path, gaps_path, review_path):
                if not child_path.exists():
                    self.checks.add("subject_cards.required_artifact", "fail", f"Missing subject-card artifact: {child_path.relative_to(self.root).as_posix()}")
            if evidence_path.exists():
                header = evidence_path.read_text(encoding="utf-8-sig").splitlines()[:1]
                if header != [SUBJECT_CARD_EVIDENCE_HEADER]:
                    self.checks.add("subject_cards.evidence.header", "fail", f"{evidence_path.relative_to(self.root).as_posix()} header does not match the contract")
                with evidence_path.open("r", encoding="utf-8-sig", newline="") as fh:
                    for row in csv.DictReader(fh):
                        source_text = str(row.get("source_path") or "").strip()
                        if spreadsheet_reference(source_text):
                            self.checks.add("subject_cards.evidence.source", "fail", f"{evidence_path.relative_to(self.root).as_posix()} uses Excel as source in {row.get('evidence_id')}: {source_text}")
            if gaps_path.exists():
                header = gaps_path.read_text(encoding="utf-8-sig").splitlines()[:1]
                if header != [SUBJECT_CARD_GAPS_HEADER]:
                    self.checks.add("subject_cards.gaps.header", "fail", f"{gaps_path.relative_to(self.root).as_posix()} header does not match the contract")
        if slugs:
            self.checks.add("subject_cards.rows", "ok", f"Subject-card contract has {len(slugs)} card(s)")
        if ready_count:
            self.checks.add("subject_cards.ready_for_review", "ok", f"Subject-card workflow has {ready_count} ready-for-review card(s)")
        else:
            self.checks.add("subject_cards.ready_for_review", "ok", "No ready_for_review subject cards yet")
        registry_by_slug = {row.get("slug", ""): row for row in registry_rows}
        missing_registry = [slug for slug in slugs if slug not in registry_by_slug or not registry_by_slug[slug].get("card_path")]
        if missing_registry:
            self.checks.add("subject_cards.registry.coverage", "fail", f"Subject-card registry does not point to card_path for: {', '.join(sorted(missing_registry))}")
        elif registry_rows:
            self.checks.add("subject_cards.registry.coverage", "ok", "Subject-card registry covers every card and can also contain candidates")
        final_feature_path = repo_path(self.root, "analysis/indexes/final-feature-map.csv")
        final_feature_rows: list[dict[str, str]] = []
        if final_feature_path.exists():
            with final_feature_path.open("r", encoding="utf-8-sig", newline="") as fh:
                final_feature_rows = [row for row in csv.DictReader(fh) if any((value or "").strip() for value in row.values())]
        feature_ids = {row.get("feature_id", "") for row in final_feature_rows if row.get("feature_id")}
        covered_feature_ids = {row.get("feature_id", "") for row in coverage_rows if row.get("source_kind") == "BF" and row.get("feature_id")}
        missing_features = sorted(feature_ids - covered_feature_ids)
        if missing_features:
            self.checks.add("subject_cards.coverage.bf", "fail", f"Subject-card coverage misses BF containers: {', '.join(missing_features)}")
        elif feature_ids:
            self.checks.add("subject_cards.coverage.bf", "ok", f"Subject-card coverage classifies {len(feature_ids)} BF container(s)")

    def test_functional_gaps_contract(self) -> None:
        root = repo_path(self.root, "analysis/functional-gaps")
        if not root.exists():
            self.checks.add("functional_gaps.not_enabled", "ok", "Functional-gap layer is not enabled yet")
            return
        cards_root = root / "cards"
        if not cards_root.exists() or not list(cards_root.glob("*/gap-card.json")):
            self.checks.add("functional_gaps.not_seeded", "ok", "No functional-gap cards built yet")
            return
        result = validate_functional_gaps(self.root)
        if result["status"] == "ok":
            self.checks.add("functional_gaps.contract", "ok", f"Functional-gap contract has {result.get('cards', 0)} card(s)")
            return
        for error in result.get("errors", []):
            self.checks.add("functional_gaps.contract", "fail", error)

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
        self.test_detail_maps_contract()
        self.test_subject_cards_contract()
        self.test_functional_gaps_contract()
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
