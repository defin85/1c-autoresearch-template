from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .common import read_jsonl, repo_path, utc_now_iso, write_jsonl
from .queue import queue_lock
from .queue_seed import PROTECTED_STATUSES, REFRESHABLE_STATUSES, stable_task_id

INVENTORY_PATH = "analysis/external-processing/inventory.csv"
RECONCILIATION_PATH = "analysis/external-processing/external-tools-source-reconciliation.csv"
SOURCE_ONLY_PATH = "analysis/external-processing/source-files-not-in-external-tools.csv"
QUEUE_PLAN_PATH = "analysis/external-processing/queue-plan.json"
ROLLUP_PATH = "analysis/external-processing/review/rollup.md"
OPEN_QUESTIONS_PATH = "analysis/external-processing/review/open-questions.md"
REVIEW_INDEX_PATH = "analysis/external-processing/review/index.csv"
REVIEW_EVIDENCE_PATH = "analysis/external-processing/review/evidence.csv"
DEFAULT_QUEUE_PATH = "analysis/queue/tasks.jsonl"

QUEUE_PLAN_SCHEMA_VERSION = "external-processing-queue-plan/v1"
REVIEW_GENERATION = "external-processing-research/v1"
INDEX_HEADER = (
    "external_review_id,external_id,source_state,status,priority,title,source_kind,"
    "inventory_ref,reconciliation_ref,source_dir,original_name,business_purpose,"
    "technical_scope,migration_relevance,subject_card_links,functional_gap_links,"
    "open_questions,queue_task_id,notes"
)
EVIDENCE_HEADER = (
    "external_review_id,claim_id,source_path,line_start,line_end,evidence_type,"
    "confidence,summary,notes"
)


class ExternalProcessingError(RuntimeError):
    pass


@dataclass
class ExternalProcessingCandidate:
    candidate_key: str
    external_review_id: str
    external_id: str
    source_state: str
    title: str
    source_kind: str
    priority: int
    source_artifacts: list[str]
    source_dir: str = ""
    inventory_ref: str = ""
    reconciliation_ref: str = ""
    original_name: str = ""
    markers: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    selection_reasons: list[str] = field(default_factory=list)

    def expected_outputs(self) -> list[str]:
        review_dir = self.external_id if self.source_state == "found" else self.external_review_id
        return [
            f"analysis/external-processing/review/{review_dir}/summary.md",
            f"analysis/external-processing/review/{review_dir}/evidence.csv",
        ]

    def quality_gates(self) -> list[str]:
        if self.source_state == "found":
            return ["source_lines", "positive_search", "metadata_checked", "bsl_checked", "forms_checked", "review_passed"]
        if self.source_state == "failed_unpack":
            return ["positive_search", "review_passed"]
        return ["positive_search", "negative_search", "review_passed"]

    def task_payload(self, now: str) -> dict[str, Any]:
        task_id = stable_task_id("Q-EP", self.candidate_key)
        return {
            "id": task_id,
            "type": "review",
            "status": "pending",
            "priority": self.priority,
            "title": self.title,
            "feature_id": self.external_id or self.external_review_id,
            "dependencies": [],
            "scope": [
                self.external_review_id,
                self.source_state,
                self.source_kind,
                self.source_dir,
            ],
            "markers": [item for item in [self.external_id, self.original_name, *self.markers] if item],
            "search_terms": [item for item in [self.title, *self.search_terms] if item],
            "quality_gates": self.quality_gates(),
            "expected_outputs": self.expected_outputs(),
            "evidence_sources": self.source_artifacts,
            "open_questions": [],
            "source_artifacts": sorted(set(self.source_artifacts)),
            "selection_reasons": self.selection_reasons,
            "external_processing_generation": REVIEW_GENERATION,
            "external_review_id": self.external_review_id,
            "external_id": self.external_id,
            "external_processing_source_state": self.source_state,
            "created_at": now,
            "updated_at": now,
        }

    def index_row(self, task_id: str) -> dict[str, str]:
        return {
            "external_review_id": self.external_review_id,
            "external_id": self.external_id,
            "source_state": self.source_state,
            "status": "pending_review",
            "priority": str(self.priority),
            "title": self.title,
            "source_kind": self.source_kind,
            "inventory_ref": self.inventory_ref,
            "reconciliation_ref": self.reconciliation_ref,
            "source_dir": self.source_dir,
            "original_name": self.original_name,
            "business_purpose": "",
            "technical_scope": "",
            "migration_relevance": "",
            "subject_card_links": "",
            "functional_gap_links": "",
            "open_questions": "",
            "queue_task_id": task_id,
            "notes": "",
        }


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle) if any(str(value or "").strip() for value in row.values())]


def _write_csv(path: Path, header: str, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = header.split(",")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_fingerprint(root: Path, relative: str) -> dict[str, Any]:
    path = repo_path(root, relative)
    return {
        "path": relative,
        "exists": path.exists(),
        "sha256": _file_sha256(path),
        "size": path.stat().st_size if path.exists() and path.is_file() else 0,
    }


def _source_ref(path: str, row_number: int) -> str:
    return f"{path}:row:{row_number}"


def _priority(state: str, title: str, file_status: str = "") -> int:
    text = f"{title} {file_status}".lower()
    if state in {"not_found", "failed_unpack", "source_only"}:
        return 100
    if any(marker in text for marker in ("загрузка", "выгрузка", "обмен", "xml", "ндфл", "фсс", "зуп", "банк")):
        return 95
    if any(marker in text for marker in ("ндс", "налог", "счет-фактур", "декларац", "отчет")):
        return 90
    return 70


def _load_inventory(root: Path) -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    rows = _read_csv_rows(repo_path(root, INVENTORY_PATH))
    return {row.get("external_id", ""): row for row in rows if row.get("external_id")}, rows


def build_candidates(root: Path) -> list[ExternalProcessingCandidate]:
    inventory_by_id, inventory_rows = _load_inventory(root)
    candidates: list[ExternalProcessingCandidate] = []
    used_external_ids: set[str] = set()

    for row_number, row in enumerate(_read_csv_rows(repo_path(root, RECONCILIATION_PATH)), 2):
        state = (row.get("status") or "").strip()
        if state == "group":
            continue
        external_id = (row.get("external_id") or "").strip()
        title = (row.get("screen_name") or row.get("file_title") or external_id).strip()
        source_kind = (row.get("screen_kind") or row.get("file_kind") or "").strip()
        reconciliation_ref = _source_ref(RECONCILIATION_PATH, row_number)
        if state == "found":
            inventory = inventory_by_id.get(external_id, {})
            file_status = row.get("file_status") or inventory.get("status", "")
            source_dir = row.get("unpacked_dir") or inventory.get("unpacked_dir", "")
            source_state = "failed_unpack" if file_status == "failed" else "found"
            artifacts = [INVENTORY_PATH, RECONCILIATION_PATH]
            if source_state == "found" and source_dir:
                artifacts.append(source_dir)
            candidate = ExternalProcessingCandidate(
                candidate_key=f"{source_state}:{external_id}",
                external_review_id=f"EPR-{external_id}",
                external_id=external_id,
                source_state=source_state,
                title=f"Исследовать внешний источник: {title}",
                source_kind=source_kind,
                priority=_priority(source_state, title, file_status),
                source_artifacts=artifacts,
                source_dir=source_dir,
                inventory_ref=external_id,
                reconciliation_ref=reconciliation_ref,
                original_name=row.get("original_name") or inventory.get("original_name", ""),
                markers=[row.get("screen_code", ""), row.get("file_code", ""), row.get("original_name", "")],
                search_terms=[title, row.get("file_title", "")],
                selection_reasons=[f"external-tools row matched source by {row.get('match_method') or 'unknown'}"],
            )
            used_external_ids.add(external_id)
        elif state == "not_found":
            code = row.get("screen_code", "").strip()
            candidate = ExternalProcessingCandidate(
                candidate_key=f"not-found:{code}:{title}",
                external_review_id=stable_task_id("EPR-MISSING", f"{code}:{title}"),
                external_id="",
                source_state="not_found",
                title=f"Проверить непоставленный внешний источник: {title}",
                source_kind=source_kind,
                priority=_priority("not_found", title),
                source_artifacts=[RECONCILIATION_PATH],
                reconciliation_ref=reconciliation_ref,
                markers=[code, title],
                search_terms=[title],
                selection_reasons=["external-tools row has status=not_found after source reconciliation"],
            )
        else:
            continue
        candidates.append(candidate)

    for row_number, row in enumerate(_read_csv_rows(repo_path(root, SOURCE_ONLY_PATH)), 2):
        external_id = row.get("external_id", "").strip()
        if not external_id or external_id in used_external_ids:
            continue
        title = row.get("file_title") or row.get("original_name") or external_id
        candidates.append(
            ExternalProcessingCandidate(
                candidate_key=f"source-only:{external_id}",
                external_review_id=f"EPR-{external_id}",
                external_id=external_id,
                source_state="source_only",
                title=f"Триаж внешнего источника вне скриншотного списка: {title}",
                source_kind=row.get("kind", ""),
                priority=_priority("source_only", title, row.get("status", "")),
                source_artifacts=[INVENTORY_PATH, SOURCE_ONLY_PATH, row.get("unpacked_dir", "")],
                source_dir=row.get("unpacked_dir", ""),
                inventory_ref=external_id,
                original_name=row.get("original_name", ""),
                markers=[external_id, row.get("original_name", ""), row.get("file_code", "")],
                search_terms=[title],
                selection_reasons=["source file exists in inventory but is absent from external-tools.csv"],
            )
        )

    for row in inventory_rows:
        external_id = row.get("external_id", "").strip()
        if row.get("status") != "failed" or external_id in {item.external_id for item in candidates}:
            continue
        title = row.get("original_name") or external_id
        candidates.append(
            ExternalProcessingCandidate(
                candidate_key=f"failed-unpack:{external_id}",
                external_review_id=f"EPR-{external_id}",
                external_id=external_id,
                source_state="failed_unpack",
                title=f"Разобрать ошибку распаковки внешнего источника: {title}",
                source_kind=row.get("kind", ""),
                priority=_priority("failed_unpack", title, row.get("status", "")),
                source_artifacts=[INVENTORY_PATH, row.get("log_file", "")],
                inventory_ref=external_id,
                original_name=row.get("original_name", ""),
                markers=[external_id, row.get("original_name", "")],
                search_terms=[title],
                selection_reasons=["inventory row has failed unpack status"],
            )
        )

    seen: set[str] = set()
    unique: list[ExternalProcessingCandidate] = []
    for candidate in sorted(candidates, key=lambda item: (-item.priority, item.external_review_id, item.candidate_key)):
        if candidate.candidate_key in seen:
            raise ExternalProcessingError(f"Duplicate external-processing candidate: {candidate.candidate_key}")
        seen.add(candidate.candidate_key)
        candidate.source_artifacts = [item for item in candidate.source_artifacts if item]
        unique.append(candidate)
    return unique


def build_queue_plan(root: Path, queue_path: Path, apply: bool) -> dict[str, Any]:
    candidates = build_candidates(root)
    now = utc_now_iso()
    tasks = [candidate.task_payload(now) for candidate in candidates]
    existing = {str(task.get("id")): task for _, task in read_jsonl(queue_path)} if queue_path.exists() else {}
    existing_by_review = {str(task.get("external_review_id")): task for task in existing.values() if task.get("external_review_id")}
    candidates_by_review = {candidate.external_review_id: candidate for candidate in candidates}
    for task in tasks:
        current = existing.get(task["id"]) or existing_by_review.get(str(task.get("external_review_id")))
        if current:
            task["id"] = current["id"]
        enrichment = _summary_enrichment(root, candidates_by_review[task["external_review_id"]], current or {})
        task["result_summary"] = str(current.get("result_summary") or enrichment["business_purpose"]) if current else enrichment["business_purpose"]
        task["open_questions"] = list(current.get("open_questions") or enrichment["open_questions"]) if current else enrichment["open_questions"]
    generated_ids = {task["id"] for task in tasks}
    actions: list[dict[str, Any]] = []
    for task in tasks:
        current = existing.get(task["id"])
        if current is None:
            action = "create"
        elif current.get("status") in REFRESHABLE_STATUSES:
            action = "update"
        elif current.get("status") in PROTECTED_STATUSES:
            action = "protected"
        else:
            action = "error"
        actions.append({"task_id": task["id"], "external_review_id": task["external_review_id"], "action": action})
    for task in existing.values():
        if task.get("external_processing_generation") == REVIEW_GENERATION and str(task.get("id")) not in generated_ids:
            actions.append({"task_id": task.get("id"), "external_review_id": task.get("external_review_id", ""), "action": "stale"})
    counts: dict[str, int] = {}
    for action in actions:
        counts[action["action"]] = counts.get(action["action"], 0) + 1
    return {
        "schema_version": QUEUE_PLAN_SCHEMA_VERSION,
        "mode": "apply" if apply else "plan",
        "generated_at": now,
        "input_artifacts": [_artifact_fingerprint(root, relative) for relative in (INVENTORY_PATH, RECONCILIATION_PATH, SOURCE_ONLY_PATH)],
        "queue_path": queue_path.relative_to(root).as_posix(),
        "candidate_count": len(candidates),
        "task_count": len(tasks),
        "tasks": tasks,
        "actions": actions,
        "counts": counts,
        "errors": [action for action in actions if action["action"] == "error"],
    }


def _summary_path(root: Path, candidate: ExternalProcessingCandidate) -> Path:
    review_dir = candidate.external_id if candidate.source_state == "found" else candidate.external_review_id
    return repo_path(root, f"analysis/external-processing/review/{review_dir}/summary.md")


def _summary_enrichment(root: Path, candidate: ExternalProcessingCandidate, task: dict[str, Any]) -> dict[str, Any]:
    path = _summary_path(root, candidate)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections[current] = []
        elif current:
            sections[current].append(line)

    def section(*names: str) -> str:
        for name in names:
            for heading, lines in sections.items():
                if name in heading:
                    value = " ".join(line.strip(" -`") for line in lines if line.strip() and not line.startswith("#"))
                    if value:
                        return value[:4000]
        return ""

    questions = list(task.get("open_questions") or [])
    if not questions:
        question_text = section("открытые вопросы")
        questions = [line.strip(" -`") for line in sections.get("открытые вопросы", []) if line.lstrip().startswith("-")]
        if not questions and question_text:
            questions = [question_text]
    business = str(task.get("result_summary") or section("назначение", "итог", "вывод") or candidate.title)
    technical = section("технический контур", "ключевые наблюдения", "техническ")
    migration = section("оценка переноса", "вывод для миграции", "сопоставление с бп 3", "оценка миграции")
    return {
        "business_purpose": business[:4000],
        "technical_scope": technical or f"Подробный статический разбор: {path.relative_to(root).as_posix() if path.is_file() else candidate.source_dir}",
        "migration_relevance": migration or "Статический разбор завершен; оставшиеся решения зафиксированы в открытых вопросах.",
        "subject_card_links": ";".join(sorted(set(re.findall(r"(?:bf-\d{3}[a-z0-9-]*|[a-z0-9-]+(?:-i-|-s-)[a-z0-9-]+)", text.lower())))),
        "functional_gap_links": ";".join(sorted(set(re.findall(r"needs_gap_card:[a-z0-9_.;=-]+", text.lower())))),
        "open_questions": questions,
        "notes": f"См. {path.relative_to(root).as_posix()}" if path.is_file() else "",
    }


def write_review_artifacts(root: Path, queue_plan: dict[str, Any]) -> None:
    task_ids = {task["external_review_id"]: task["id"] for task in queue_plan.get("tasks", [])}
    tasks = {task["external_review_id"]: task for task in queue_plan.get("tasks", [])}
    queue_path = repo_path(root, queue_plan.get("queue_path") or DEFAULT_QUEUE_PATH)
    queue_status = {str(task.get("id")): str(task.get("status")) for _, task in read_jsonl(queue_path)} if queue_path.exists() else {}
    existing = {row.get("external_review_id", ""): row for row in _read_csv_rows(repo_path(root, REVIEW_INDEX_PATH))}
    index_rows = []
    enrichment_fields = ("business_purpose", "technical_scope", "migration_relevance", "subject_card_links", "functional_gap_links", "open_questions", "notes")
    for candidate in build_candidates(root):
        row = candidate.index_row(task_ids.get(candidate.external_review_id, ""))
        old = existing.get(candidate.external_review_id, {})
        enrichment = _summary_enrichment(root, candidate, tasks.get(candidate.external_review_id, {}))
        task_status = queue_status.get(row["queue_task_id"], "")
        row["status"] = task_status if task_status in {"done", "skipped"} else old.get("status") or "pending_review"
        for field in enrichment_fields:
            value = old.get(field) or enrichment[field]
            row[field] = "; ".join(value) if isinstance(value, list) else value
        index_rows.append(row)
    _write_csv(repo_path(root, REVIEW_INDEX_PATH), INDEX_HEADER, index_rows)
    if not repo_path(root, REVIEW_EVIDENCE_PATH).exists():
        _write_csv(repo_path(root, REVIEW_EVIDENCE_PATH), EVIDENCE_HEADER, [])
    if not repo_path(root, OPEN_QUESTIONS_PATH).exists():
        repo_path(root, OPEN_QUESTIONS_PATH).parent.mkdir(parents=True, exist_ok=True)
        repo_path(root, OPEN_QUESTIONS_PATH).write_text("# Open Questions\n\n", encoding="utf-8")
    _write_json(repo_path(root, QUEUE_PLAN_PATH), queue_plan)
    write_rollup(root, queue_plan)


def write_rollup(root: Path, queue_plan: dict[str, Any]) -> None:
    candidates = build_candidates(root)
    states: dict[str, int] = {}
    for candidate in candidates:
        states[candidate.source_state] = states.get(candidate.source_state, 0) + 1
    lines = [
        "# External Processing Research Rollup",
        "",
        f"- generated_at: {queue_plan.get('generated_at')}",
        f"- mode: {queue_plan.get('mode')}",
        f"- candidates: {len(candidates)}",
        f"- queue_actions: {json.dumps(queue_plan.get('counts', {}), ensure_ascii=False, sort_keys=True)}",
        "",
        "## Source States",
        "",
    ]
    for state, count in sorted(states.items()):
        lines.append(f"- {state}: {count}")
    lines.extend(["", "## Delivery Gaps", ""])
    for candidate in candidates:
        if candidate.source_state in {"not_found", "failed_unpack", "source_only"}:
            lines.append(f"- {candidate.source_state}: {candidate.title}")
    repo_path(root, ROLLUP_PATH).parent.mkdir(parents=True, exist_ok=True)
    repo_path(root, ROLLUP_PATH).write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_queue_plan(root: Path, queue_plan: dict[str, Any], queue_path: Path) -> dict[str, Any]:
    if queue_plan.get("errors"):
        raise ExternalProcessingError("External-processing queue plan contains merge errors; refusing to apply")
    with queue_lock(queue_path):
        current = [task for _, task in read_jsonl(queue_path)] if queue_path.exists() else []
        by_id = {str(task.get("id")): task for task in current}
        now = utc_now_iso()
        for action in queue_plan["actions"]:
            if action["action"] not in {"create", "update"}:
                continue
            task = next(item for item in queue_plan["tasks"] if item["id"] == action["task_id"])
            task = dict(task)
            task["updated_at"] = now
            if action["action"] == "create":
                task["created_at"] = now
                current.append(task)
                continue
            existing = by_id[action["task_id"]]
            preserved = {
                key: existing[key]
                for key in ("status", "claimed_by", "claimed_at", "completed_at", "result_summary", "open_questions", "created_at")
                if key in existing
            }
            existing.clear()
            existing.update(task)
            existing.update(preserved)
        write_jsonl(queue_path, current)
    queue_plan["mode"] = "apply"
    queue_plan["queue_hash_after"] = _file_sha256(queue_path)
    return queue_plan


def _split_artifacts(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


def validate_external_processing(root: Path, queue_path: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    queue_path = queue_path or repo_path(root, DEFAULT_QUEUE_PATH)
    required = [INVENTORY_PATH, RECONCILIATION_PATH, SOURCE_ONLY_PATH, REVIEW_INDEX_PATH, REVIEW_EVIDENCE_PATH, QUEUE_PLAN_PATH, ROLLUP_PATH]
    for relative in required:
        if not repo_path(root, relative).exists():
            errors.append(f"Missing external-processing artifact: {relative}")

    inventory_by_id, _ = _load_inventory(root)
    index_rows = _read_csv_rows(repo_path(root, REVIEW_INDEX_PATH))
    review_ids = {row.get("external_review_id", "") for row in index_rows}
    for row in index_rows:
        review_id = row.get("external_review_id", "")
        state = row.get("source_state", "")
        external_id = row.get("external_id", "")
        source_dir = row.get("source_dir", "")
        if state in {"found", "failed_unpack", "source_only"} and external_id not in inventory_by_id:
            errors.append(f"Review row {review_id} references missing inventory row: {external_id}")
        if state == "found" and (not source_dir or not repo_path(root, source_dir).is_dir()):
            errors.append(f"Review row {review_id} has missing source_dir: {source_dir}")

    queue_plan: dict[str, Any] = {}
    plan_path = repo_path(root, QUEUE_PLAN_PATH)
    if plan_path.exists():
        try:
            queue_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"Could not parse {QUEUE_PLAN_PATH}: {exc}")
        if queue_plan.get("schema_version") != QUEUE_PLAN_SCHEMA_VERSION:
            errors.append(f"{QUEUE_PLAN_PATH} has invalid schema_version")
        for task in queue_plan.get("tasks", []):
            task_id = task.get("id", "<unknown>")
            if task.get("type") == "manual_markup":
                errors.append(f"External-processing task {task_id} must not use type=manual_markup")
            if task.get("type") != "review":
                errors.append(f"External-processing task {task_id} has invalid type: {task.get('type')}")
            if task.get("external_review_id") not in review_ids:
                errors.append(f"External-processing task {task_id} references missing review row: {task.get('external_review_id')}")
            for field in ("source_artifacts", "expected_outputs", "quality_gates"):
                if not task.get(field):
                    errors.append(f"External-processing task {task_id} is missing {field}")
            for relative in _split_artifacts(task.get("source_artifacts")):
                path_part = relative.split(":", 1)[0]
                if not repo_path(root, path_part).exists():
                    errors.append(f"External-processing task {task_id} references missing source artifact: {relative}")
        if queue_plan.get("mode") == "apply":
            if not queue_path.exists():
                errors.append(f"Applied external-processing queue plan has no queue file: {queue_path.relative_to(root).as_posix()}")
            else:
                queue_tasks = {str(task.get("id")): task for _, task in read_jsonl(queue_path)}
                for action in queue_plan.get("actions", []):
                    if action.get("action") not in {"create", "update", "protected"}:
                        continue
                    task_id = str(action.get("task_id") or "")
                    actual = queue_tasks.get(task_id)
                    if actual is None:
                        errors.append(f"Applied external-processing queue task is missing: {task_id}")
                        continue
                    if actual.get("external_processing_generation") != REVIEW_GENERATION:
                        errors.append(f"Applied external-processing queue task {task_id} has invalid generation")
                    if actual.get("type") != "review":
                        errors.append(f"Applied external-processing queue task {task_id} has invalid type: {actual.get('type')}")
                    if actual.get("external_review_id") not in review_ids:
                        errors.append(f"Applied external-processing queue task {task_id} references missing review row")
    if not index_rows:
        warnings.append("External-processing review index is empty")
    return {
        "status": "fail" if errors else "ok",
        "review_rows": len(index_rows),
        "queue_tasks": len(queue_plan.get("tasks", [])) if queue_plan else 0,
        "errors": errors,
        "warnings": warnings,
    }


def prepare_external_processing(root: Path, queue_path: Path, apply: bool) -> dict[str, Any]:
    queue_plan = build_queue_plan(root, queue_path, apply)
    if not apply:
        return {
            "status": "fail" if queue_plan["errors"] else "ok",
            "mode": "plan",
            "candidate_count": queue_plan["candidate_count"],
            "task_count": queue_plan["task_count"],
            "counts": queue_plan["counts"],
            "queue_plan": queue_plan,
            "errors": queue_plan["errors"],
            "warnings": [],
        }
    queue_plan = apply_queue_plan(root, queue_plan, queue_path)
    write_review_artifacts(root, queue_plan)
    validation = validate_external_processing(root, queue_path)
    return {
        "status": validation["status"],
        "mode": queue_plan["mode"],
        "candidate_count": queue_plan["candidate_count"],
        "task_count": queue_plan["task_count"],
        "counts": queue_plan["counts"],
        "queue_plan": QUEUE_PLAN_PATH,
        "errors": validation["errors"],
        "warnings": validation["warnings"],
    }


def plan_command(args: argparse.Namespace) -> int:
    if args.plan == args.apply:
        raise ExternalProcessingError("Specify exactly one of --plan or --apply")
    root = Path(args.repo_path).resolve()
    result = prepare_external_processing(root, repo_path(root, args.queue_path), apply=bool(args.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] != "ok" else 0


def validate_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    result = validate_external_processing(root, repo_path(root, args.queue_path))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] != "ok" else 0
