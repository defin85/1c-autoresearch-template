#!/usr/bin/env python3
"""Initialize manual cleanup queue rows from a task report CSV."""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path


TASKS = Path("analysis/queue/tasks.jsonl")
QUEUE = Path("analysis/detailed-register-reverse-review/manual-markup-queue.jsonl")
FINAL_DIFFS = Path("analysis/indexes/final-diff-inventory.csv")
DIFF_ID_MAP = Path("analysis/indexes/diff-id-map.csv")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        rows.append(json.loads(obj) if isinstance(obj, str) else obj)
    return rows


def task_suffix(task_id: str) -> str:
    match = re.search(r"V8UNPACK-(\d+[A-Z])$", task_id)
    if not match:
        raise SystemExit(f"Cannot derive queue suffix from task id: {task_id}")
    return f"Q{match.group(1)}"


def task_class(task: dict) -> str:
    for item in task.get("scope", []):
        if item.startswith(("template_report_class:", "binary_report_class:", "json_html_residual_class:", "schema_report_class:")):
            return item.split(":", 1)[1]
    raise SystemExit(f"Task has no supported *_class scope: {task.get('id')}")


def task_report(task: dict) -> Path:
    candidates = [
        Path(p)
        for key in ("source_artifacts", "evidence_sources")
        for p in task.get(key, [])
        if p.endswith(".csv") and "/reports/" in p
    ]
    if not candidates:
        raise SystemExit(f"Task has no report CSV in source_artifacts/evidence_sources: {task.get('id')}")
    if len(set(candidates)) > 1:
        raise SystemExit(f"Task has multiple report CSV candidates: {candidates}")
    return candidates[0]


def final_active_keys() -> tuple[set[str], set[str], dict[str, str]]:
    diff_ids: set[str] = set()
    paths: set[str] = set()
    diff_id_by_path: dict[str, str] = {}
    with FINAL_DIFFS.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("diff_id"):
                diff_ids.add(row["diff_id"])
            if row.get("path"):
                paths.add(row["path"])
                diff_id_by_path[row["path"]] = row.get("diff_id", "")
    return diff_ids, paths, diff_id_by_path


def stable_ids_by_current() -> dict[str, str]:
    if not DIFF_ID_MAP.exists():
        return {}
    with DIFF_ID_MAP.open(encoding="utf-8-sig", newline="") as f:
        return {
            row.get("current_diff_id", ""): row.get("stable_diff_id", "")
            for row in csv.DictReader(f)
            if row.get("current_diff_id") and row.get("stable_diff_id")
        }


def report_class(row: dict) -> str:
    return row.get("candidate_class") or row.get("residual_class") or ""


def existing_keys(task_id: str) -> tuple[set[str], int]:
    if not QUEUE.exists():
        return set(), 0
    keys: set[str] = set()
    max_num = 0
    for row in read_jsonl(QUEUE):
        if row.get("central_task_id") != task_id and row.get("task_id") != task_id:
            continue
        for stable_id in row.get("stable_diff_ids", []):
            keys.add(f"stable:{stable_id}")
        for old_id in row.get("old_diff_ids", []):
            keys.add(f"diff:{old_id}")
        match = re.search(r"-(\d+)$", row.get("row_id", ""))
        if match:
            max_num = max(max_num, int(match.group(1)))
    return keys, max_num


def build_rows(task: dict, limit: int) -> list[dict]:
    task_id = task["id"]
    wanted_class = task_class(task)
    report = task_report(task)
    final_ids, final_paths, final_id_by_path = final_active_keys()
    stable_by_current = stable_ids_by_current()
    seen, start_num = existing_keys(task_id)
    suffix = task_suffix(task_id)
    batch_id = f"manual-final-cleanup-{suffix[1:]}"
    now = utc_now()
    out = []
    with report.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if report_class(row) != wanted_class:
                continue
            if row.get("diff_id") not in final_ids and row.get("path") not in final_paths:
                continue
            current_diff_id = row.get("diff_id") if row.get("diff_id") in final_ids else final_id_by_path.get(row.get("path", ""))
            stable_diff_id = row.get("stable_diff_id") or stable_by_current.get(current_diff_id, "")
            key = f"stable:{stable_diff_id}" if stable_diff_id else f"diff:{row.get('diff_id')}"
            if key in seen:
                continue
            index = start_num + len(out) + 1
            out.append(
                {
                    "attempts": 0,
                    "batch_id": batch_id,
                    "central_task_id": task_id,
                    "current_diff_ids": [current_diff_id] if current_diff_id else [],
                    "decision": "",
                    "key_objects": row.get("object_name", ""),
                    "last_pass": "",
                    "last_updated": now,
                    "notes": "",
                    "old_diff_ids": [row["diff_id"]] if row.get("diff_id") else [],
                    "paths": [row["path"]] if row.get("path") else [],
                    "priority": task.get("priority", 0),
                    "row_id": f"DIFF-CLEANUP-V8UNPACK-{suffix}-{index:03d}",
                    "source_title": f"Physical diff cleanup: {wanted_class}",
                    "stable_diff_ids": [stable_diff_id] if stable_diff_id else [],
                    "status": "pending",
                    "task_id": task_id,
                }
            )
            seen.add(key)
            if len(out) >= limit:
                break
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--write", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tasks = read_jsonl(TASKS)
    task = next((t for t in tasks if t.get("id") == args.task_id), None)
    if not task:
        raise SystemExit(f"Task not found: {args.task_id}")
    if task.get("type") != "manual_markup":
        raise SystemExit(f"Task is not manual_markup: {args.task_id}")

    rows = build_rows(task, args.limit)
    result = {
        "task_id": args.task_id,
        "report_class": task_class(task),
        "rows": len(rows),
        "first_row_id": rows[0]["row_id"] if rows else "",
        "last_row_id": rows[-1]["row_id"] if rows else "",
        "write": bool(args.write),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.write and rows:
        with QUEUE.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
