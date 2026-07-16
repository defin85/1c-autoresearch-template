#!/usr/bin/env python3
"""Build a reproducible candidate list for manual-cleanup Codex Exec passes."""

from __future__ import annotations

import argparse
import csv
import importlib
import json
from pathlib import Path


DEFAULT_OUTPUT = Path("analysis/detailed-register-reverse-review/manual-agent-review-candidates.csv")
FIELDS = [
    "row_id",
    "task_id",
    "strategy",
    "suggested_decision",
    "reason",
    "key_objects",
    "stable_diff_ids",
    "old_diff_ids",
    "paths",
    "semantic_diff_paths",
    "context_diff_paths",
    "clean_candidate_paths",
]


def load_probe():
    return importlib.import_module("one_c_autoresearch.manual_cleanup_commands.manual_cleanup_probe_row")


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        rows.append(json.loads(obj) if isinstance(obj, str) else obj)
    return rows


def classify(probe, row: dict) -> dict | None:
    paths = row.get("paths", [])
    strategy = probe.detect_strategy(paths)
    nested = probe.diff_name_only(probe.NESTED_REPO, paths)
    strategy_result = probe.template_mxl_probe(paths) if strategy == "template_mxl" else probe.simple_clean_probe(paths)
    semantic = strategy_result["semantic_diff_paths"]
    context = strategy_result["context_diff_paths"]
    if not nested:
        decision = "manual_review"
        reason = "no nested diff remains"
    elif not strategy_result["clean_candidate_paths"]:
        decision = "manual_review"
        reason = f"no normalized candidate paths for strategy: {strategy}"
    elif strategy != "template_mxl":
        decision = "manual_review"
        reason = f"non-template strategy requires source review: {strategy}"
    elif semantic:
        decision = "keep_customization"
        reason = "strategy semantic representation differs"
    else:
        return None
    return {
        "row_id": row.get("row_id", ""),
        "task_id": row.get("central_task_id") or row.get("task_id", ""),
        "strategy": strategy,
        "suggested_decision": decision,
        "reason": reason,
        "key_objects": row.get("key_objects", ""),
        "stable_diff_ids": ";".join(row.get("stable_diff_ids", [])),
        "old_diff_ids": ";".join(row.get("old_diff_ids", [])),
        "paths": ";".join(paths),
        "semantic_diff_paths": ";".join(semantic),
        "context_diff_paths": ";".join(context),
        "clean_candidate_paths": ";".join(strategy_result["clean_candidate_paths"]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", default="")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def self_test() -> None:
    assert FIELDS[0] == "row_id"
    assert DEFAULT_OUTPUT.name == "manual-agent-review-candidates.csv"


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        print("self-test: ok")
        return 0
    probe = load_probe()
    rows = []
    for row in read_jsonl(probe.QUEUE):
        if row.get("status") != "pending":
            continue
        task_id = row.get("central_task_id") or row.get("task_id", "")
        if args.task_id and task_id != args.task_id:
            continue
        candidate = classify(probe, row)
        if candidate:
            rows.append(candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"output": str(args.output), "rows": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
