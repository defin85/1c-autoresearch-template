#!/usr/bin/env python3
"""Return pending manual-cleanup rows as JSON for agent input."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


QUEUE = Path("analysis/detailed-register-reverse-review/manual-markup-queue.jsonl")
DEFAULT_CANDIDATES = Path("analysis/detailed-register-reverse-review/manual-agent-review-candidates.csv")


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        rows.append(json.loads(obj) if isinstance(obj, str) else obj)
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--row-id-prefix", default="")
    parser.add_argument("--candidate-file", type=Path, default=None)
    parser.add_argument("--suggested-decision", choices=["manual_review", "keep_customization"], default="")
    return parser.parse_args()


def candidate_row_ids(path: Path, task_id: str, suggested_decision: str) -> set[str]:
    if not path.exists():
        raise SystemExit(f"candidate file not found: {path}")
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        return {
            row.get("row_id", "")
            for row in reader
            if row.get("task_id") == task_id
            and (not suggested_decision or row.get("suggested_decision") == suggested_decision)
        }


def main() -> int:
    args = parse_args()
    allowed_rows = None
    if args.candidate_file or args.suggested_decision:
        allowed_rows = candidate_row_ids(args.candidate_file or DEFAULT_CANDIDATES, args.task_id, args.suggested_decision)
    rows = []
    for row in read_jsonl(QUEUE):
        if row.get("central_task_id") != args.task_id and row.get("task_id") != args.task_id:
            continue
        if row.get("status") != "pending":
            continue
        if allowed_rows is not None and row.get("row_id", "") not in allowed_rows:
            continue
        if args.row_id_prefix and not row.get("row_id", "").startswith(args.row_id_prefix):
            continue
        rows.append(
            {
                "row_id": row.get("row_id", ""),
                "task_id": args.task_id,
                "stable_diff_ids": row.get("stable_diff_ids", []),
                "old_diff_ids": row.get("old_diff_ids", []),
                "paths": row.get("paths", []),
                "key_objects": row.get("key_objects", ""),
            }
        )
        if len(rows) >= args.limit:
            break
    print(json.dumps({"task_id": args.task_id, "rows": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
