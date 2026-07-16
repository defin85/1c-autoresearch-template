from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .common import repo_path

COMPLETED_SUBJECT_STATUSES = {"ready_for_review", "reviewed"}
SUBJECT_REGISTRY_PATH = "analysis/subject-cards/registry.csv"
SECOND_PASS_SOURCE_ARTIFACTS = [
    SUBJECT_REGISTRY_PATH,
    "analysis/indexes/diff-inventory.csv",
    "analysis/indexes/v8unpack-diff-trace.csv",
]


@dataclass(frozen=True)
class WorkCandidate:
    semantic_key: str
    source_layer: str
    work_kind: str
    feature_id: str
    subject_card_slug: str = ""
    status: str = ""
    completed: bool = False
    source_artifacts: list[str] = field(default_factory=list)
    selection_reasons: list[str] = field(default_factory=list)
    queue_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlannerResult:
    candidates: list[WorkCandidate]
    completed_candidates: list[WorkCandidate] = field(default_factory=list)


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh) if any((value or "").strip() for value in row.values())]


def _to_int(value: str) -> int:
    try:
        return int(str(value or "0").strip() or 0)
    except ValueError:
        return 0


def _is_completed_subject_card(row: dict[str, str]) -> bool:
    status = str(row.get("status") or "").strip()
    card_path = str(row.get("card_path") or "").strip()
    return status in COMPLETED_SUBJECT_STATUSES and bool(card_path)


def _is_bf_container(row: dict[str, str]) -> bool:
    origin_layer = str(row.get("origin_layer") or "").strip().upper()
    owner_feature = str(row.get("owner_feature") or "").strip().upper()
    slug = str(row.get("slug") or "").strip().lower()
    linked_features = str(row.get("linked_features") or "").strip().upper()
    return origin_layer == "BF" or owner_feature.startswith("BF-") or slug.startswith("bf-") or linked_features.startswith("BF-")


def plan_second_pass_work(root: Path, *, default_quality_gates: list[str], default_priority: int) -> PlannerResult:
    candidates: list[WorkCandidate] = []
    completed_candidates: list[WorkCandidate] = []
    for row in _read_csv_rows(repo_path(root, SUBJECT_REGISTRY_PATH)):
        slug = str(row.get("slug") or "").strip()
        if not slug:
            continue
        title = str(row.get("title") or slug).strip()
        status = str(row.get("status") or "").strip()
        confidence = str(row.get("confidence") or "").strip()
        owner_feature = str(row.get("owner_feature") or slug).strip() or slug
        semantic_key = f"card:{slug}"
        evidence_count = _to_int(row.get("evidence_count", ""))
        gap_count = _to_int(row.get("gap_count", ""))
        priority = default_priority + min(gap_count * 5, 25) + (10 if evidence_count < 3 else 0)
        completed = _is_completed_subject_card(row)
        bf_container = _is_bf_container(row)
        if completed:
            completed_candidates.append(
                WorkCandidate(
                    semantic_key=semantic_key,
                    source_layer="subject-card-registry",
                    work_kind="completed_subject_card",
                    feature_id=owner_feature,
                    subject_card_slug=slug,
                    status=status,
                    completed=True,
                    source_artifacts=[SUBJECT_REGISTRY_PATH],
                    selection_reasons=[
                        f"Completed subject card is not queued: status={status}; card_path={row.get('card_path', '')}",
                    ],
                    queue_fields={},
                )
            )
            continue

        if bf_container:
            work_kind = "container_refinement"
            queue_title = f"Разобрать BF-контейнер и выделить сценарии: {title}"
            expected_outputs = [
                "analysis/queue/tasks.jsonl",
                f"analysis/features/{owner_feature}/open-questions.md",
            ]
            open_questions = [
                "Разделить BF-контейнер на бизнес-сценарии, пользовательскую работу, регламентные процессы или интеграции.",
                "Если бизнес-смысл не восстановлен, завершить задачу как needs_followup, не принимая BF как границу карточки.",
            ]
            selection_reasons = [
                "BF-derived subject-card registry row is a planning container, not a finished card boundary.",
                "Контейнер нужно разложить на бизнес-сценарии, работу пользователя, регламентные или интеграционные процессы.",
                f"evidence_count={evidence_count}; gap_count={gap_count}; status={status}; confidence={confidence}",
            ]
        else:
            work_kind = "subject_card_refinement"
            queue_title = f"Уточнить предметную карточку: {title}"
            expected_outputs = [str(row.get("card_path") or f"analysis/subject-cards/cards/{slug}/subject-card.json").strip()]
            open_questions = []
            selection_reasons = [
                "Subject-card registry row requires second-pass analyst refinement.",
                "Предметная карточка должна описывать бизнес-сценарий, работу пользователя, регламентный или интеграционный процесс.",
                f"evidence_count={evidence_count}; gap_count={gap_count}; status={status}; confidence={confidence}",
            ]

        candidates.append(
            WorkCandidate(
                semantic_key=semantic_key,
                source_layer="subject-card-registry",
                work_kind=work_kind,
                feature_id=owner_feature,
                subject_card_slug=slug,
                status=status,
                completed=False,
                source_artifacts=SECOND_PASS_SOURCE_ARTIFACTS,
                selection_reasons=selection_reasons,
                queue_fields={
                    "title": queue_title,
                    "task_type": "review",
                    "priority": priority,
                    "scope": [
                        f"work-kind:{work_kind}",
                        f"subject-card:{slug}",
                        f"status:{status}",
                        f"confidence:{confidence}",
                    ],
                    "markers": [slug, owner_feature, work_kind],
                    "search_terms": [title],
                    "quality_gates": default_quality_gates,
                    "expected_outputs": expected_outputs,
                    "evidence_sources": SECOND_PASS_SOURCE_ARTIFACTS,
                    "open_questions": open_questions,
                },
            )
        )
    return PlannerResult(candidates=candidates, completed_candidates=completed_candidates)
