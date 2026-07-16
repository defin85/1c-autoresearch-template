from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from one_c_autoresearch.common import write_jsonl
from one_c_autoresearch.research_review import (
    CHECKS_HEADER,
    DECISIONS_HEADER,
    QUEUE_PLAN_SCHEMA_VERSION,
    REVIEW_PREPARATION_GENERATION,
    TARGETS_HEADER,
    validate_research_review,
)


def write_csv(path: Path, header: str, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = header.split(",")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class ResearchReviewValidationTest(unittest.TestCase):
    def test_validate_allows_completed_materialized_review_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "analysis/source.txt"
            source.parent.mkdir(parents=True)
            source.write_text("evidence\n", encoding="utf-8")
            target = {
                "target_id": "RR-1",
                "target_type": "external_source_review",
                "status": "prepared",
                "priority": "100",
                "feature_id": "BF-001",
                "subject_card_slug": "bf-001",
                "title": "Review",
                "source_artifacts": "analysis/source.txt",
                "selection_reasons": "test",
                "expected_outputs": "analysis/research-review/decisions.csv",
                "infobase_required": "false",
                "notes": "",
            }
            check = {
                "check_id": "CHK-1",
                "target_id": "RR-1",
                "question": "Check",
                "evidence_required": "analysis/source.txt",
                "positive_search": "Review",
                "negative_search": "runtime",
                "expected_outputs": "analysis/research-review/decisions.csv",
                "quality_gates": "source_lines;review_passed",
                "infobase_required": "false",
                "status": "prepared",
                "blocking": "false",
                "notes": "",
            }
            task = {
                "id": "Q-RR-1",
                "type": "review",
                "status": "done",
                "review_preparation_generation": REVIEW_PREPARATION_GENERATION,
                "review_target_id": "RR-1",
                "review_check_ids": ["CHK-1"],
                "source_artifacts": ["analysis/source.txt"],
                "expected_outputs": ["analysis/research-review/decisions.csv"],
                "quality_gates": ["source_lines", "review_passed"],
            }
            queue_plan = {
                "schema_version": QUEUE_PLAN_SCHEMA_VERSION,
                "mode": "apply",
                "actions": [{"task_id": "Q-RR-1", "target_id": "RR-1", "action": "create"}],
                "tasks": [dict(task, status="pending")],
            }
            write_csv(root / "analysis/research-review/targets.csv", TARGETS_HEADER, [target])
            write_csv(root / "analysis/research-review/checks.csv", CHECKS_HEADER, [check])
            write_csv(root / "analysis/research-review/decisions.csv", DECISIONS_HEADER, [])
            (root / "analysis/research-review/queue-plan.json").write_text(json.dumps(queue_plan), encoding="utf-8")
            (root / "analysis/queue").mkdir(parents=True)
            write_jsonl(root / "analysis/queue/tasks.jsonl", [task])

            result = validate_research_review(root)

            self.assertEqual(result["errors"], [])
            self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
