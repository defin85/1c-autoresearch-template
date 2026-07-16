#!/usr/bin/env python3
"""Validate a manual physical cleanup pass before accepting agent output."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from pathlib import Path


QUEUE = Path("analysis/detailed-register-reverse-review/manual-markup-queue.jsonl")
MARKUP = "analysis/detailed-register-reverse-review/markup.csv"
TASKS = "analysis/queue/tasks.jsonl"
NESTED_REPO = Path("analysis/cache/noise/clean-rebase-v8unpack/repo")
ALLOWED_DECISIONS = {"remove_noise", "keep_customization", "manual_review"}
KEEP_RISK_MARKERS = (
    "повед",
    "реквизит",
    "команд",
    "обработчик",
    "код",
    "bsl",
    ".obj.bsl",
    "форма",
    "интерфейс",
    "данн",
    "контракт",
    "xsd",
    "wsdl",
    "xdto",
    "xml",
    "правил",
    "обмен",
    "алгоритм",
    "печат",
    "макет измен",
    "содержимое измен",
    "смыслов",
    "бизнес",
)
REMOVE_NOISE_MARKERS = (
    "техничес",
    "служеб",
    "export",
    "экспорт",
    "одинаков",
    "normalized",
    "нормализован",
    "header",
    "digest",
    "шум",
    "одинаковый blob",
    "blob",
    "без смысл",
    "смысловое изменение не",
)


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
        raise SystemExit(f"Cannot derive task suffix from {task_id}")
    return f"Q{match.group(1)}"


def git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True)


def nested_git(args: list[str]) -> str:
    return subprocess.check_output(["git", "-C", str(NESTED_REPO), *args], text=True)


def changed_paths() -> set[str]:
    return set(filter(None, git(["diff", "--name-only"]).splitlines()))


def numstat(path: str) -> tuple[int, int] | None:
    out = git(["diff", "--numstat", "--", path]).strip()
    if not out:
        return None
    added, removed, _ = out.split("\t", 2)
    if added == "-" or removed == "-":
        return 999999, 999999
    return int(added), int(removed)


def split_cell(value: str) -> list[str]:
    return [part for part in (value or "").split(";") if part]


def read_task(task_id: str) -> dict | None:
    for row in read_jsonl(Path(TASKS)):
        if row.get("id") == task_id:
            return row
    return None


def current_count(task: dict | None) -> int | None:
    if not task:
        return None
    for item in task.get("scope", []):
        match = re.fullmatch(r"current_count:(\d+)", str(item))
        if match:
            return int(match.group(1))
    return None


def validate_queue(task_id: str, suffix: str, errors: list[str]) -> dict[str, dict]:
    rows = {}
    for row in read_jsonl(QUEUE):
        if row.get("central_task_id") != task_id and row.get("task_id") != task_id:
            continue
        row_id = row.get("row_id", "")
        if not row_id.startswith(f"DIFF-CLEANUP-V8UNPACK-{suffix}-"):
            errors.append(f"queue row has wrong row_id for {task_id}: {row_id}")
        if row_id.startswith("DCCR-"):
            errors.append(f"queue row uses detailed-register row id in cleanup task: {row_id}")
        rows[row_id] = row
    if not rows:
        errors.append(f"no queue rows found for {task_id}")
    return rows


def validate_pass(pass_file: Path, task_id: str, suffix: str, queue_rows: dict[str, dict], errors: list[str]) -> list[dict]:
    if not pass_file.exists():
        errors.append(f"pass file not found: {pass_file}")
        return []
    with pass_file.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        errors.append(f"pass file is empty: {pass_file}")
        return []
    for idx, row in enumerate(rows, 2):
        row_id = row.get("row_id", "")
        if not row_id.startswith(f"DIFF-CLEANUP-V8UNPACK-{suffix}-"):
            errors.append(f"{pass_file}:{idx}: wrong cleanup row_id: {row_id}")
        if row_id.startswith("DCCR-"):
            errors.append(f"{pass_file}:{idx}: DCCR row_id is not allowed for cleanup pass")
        if row_id not in queue_rows:
            errors.append(f"{pass_file}:{idx}: row_id is absent from manual-markup-queue: {row_id}")
        if row.get("task_id") and row["task_id"] != task_id:
            errors.append(f"{pass_file}:{idx}: task_id mismatch: {row['task_id']}")
        decision = row.get("decision", "")
        if decision not in ALLOWED_DECISIONS:
            errors.append(f"{pass_file}:{idx}: invalid decision: {decision}")
        if not split_cell(row.get("stable_diff_ids") or row.get("stable_diff_id", "")):
            errors.append(f"{pass_file}:{idx}: stable_diff_id is required")
        if not split_cell(row.get("paths") or row.get("source_paths", "")):
            errors.append(f"{pass_file}:{idx}: source path is required")
        validate_decision_evidence(pass_file, idx, row, errors)
    return rows


def validate_decision_evidence(pass_file: Path, idx: int, row: dict, errors: list[str]) -> None:
    decision = row.get("decision", "")
    notes = row.get("notes") or row.get("reason") or ""
    evidence = row.get("evidence_refs") or row.get("source_paths") or ""
    combined = f"{notes}\n{evidence}"
    lower = combined.lower()
    if decision == "keep_customization":
        if not any(marker in lower for marker in KEEP_RISK_MARKERS):
            errors.append(
                f"{pass_file}:{idx}: keep_customization requires a concrete semantic diff rationale"
            )
        if "шум не доказан" in lower or "не доказан" in lower or "нет безопасного" in lower:
            errors.append(
                f"{pass_file}:{idx}: use manual_review, not keep_customization, when semantic diff is not proven"
            )
    if decision == "remove_noise":
        removed_paths = split_cell(row.get("removed_paths", ""))
        cleanup_commit = row.get("cleanup_commit", "")
        if not removed_paths:
            errors.append(f"{pass_file}:{idx}: remove_noise requires removed_paths")
        if not cleanup_commit:
            errors.append(f"{pass_file}:{idx}: remove_noise requires cleanup_commit")
        if not any(marker in lower for marker in REMOVE_NOISE_MARKERS):
            errors.append(f"{pass_file}:{idx}: remove_noise requires explicit technical-noise rationale")
        for path in removed_paths:
            validate_removed_path(pass_file, idx, path, cleanup_commit, errors)


def validate_removed_path(pass_file: Path, idx: int, path: str, cleanup_commit: str, errors: list[str]) -> None:
    if not cleanup_commit:
        return
    try:
        diff_paths = nested_git(["diff", "--name-only", "vendor-baseline..HEAD", "--", path]).splitlines()
        touched = nested_git(["show", "--name-only", "--format=", cleanup_commit, "--", path]).splitlines()
    except subprocess.CalledProcessError as exc:
        errors.append(f"{pass_file}:{idx}: cannot verify cleanup_commit {cleanup_commit}: {exc}")
        return
    if path in diff_paths:
        errors.append(f"{pass_file}:{idx}: removed path still differs in nested repo: {path}")
    if path not in touched:
        errors.append(f"{pass_file}:{idx}: cleanup_commit {cleanup_commit} does not touch removed path: {path}")


def validate_task_status(task_id: str, queue_rows: dict[str, dict], pass_rows: list[dict], errors: list[str]) -> None:
    task = read_task(task_id)
    if not task:
        errors.append(f"{TASKS}: task not found: {task_id}")
        return
    total = current_count(task)
    done_rows = sum(1 for row in queue_rows.values() if row.get("status") == "done")
    pass_count = len(pass_rows)
    if task.get("status") == "done" and total and done_rows < total:
        errors.append(
            f"{TASKS}: {task_id} is done, but only {done_rows}/{total} row-level items are done"
        )
    if task.get("status") == "done" and total and pass_count and pass_count < total:
        errors.append(
            f"{TASKS}: {task_id} was closed by a partial pass ({pass_count}/{total}); keep task pending"
        )


def validate_worktree(task_id: str, errors: list[str]) -> None:
    paths = changed_paths()
    if MARKUP in paths:
        errors.append(f"{MARKUP} is modified; physical cleanup pass must not edit markup.csv")
    unexpected = [
        p
        for p in paths
        if not (
            p == TASKS
            or p == str(QUEUE)
            or p == "analysis/detailed-register-reverse-review/manual-markup-state.json"
            or p.startswith("analysis/detailed-register-reverse-review/manual-final-cleanup-")
            or p.startswith("analysis/detailed-register-reverse-review/summary-manual-final-cleanup-")
            or p.startswith("docs/method/")
            or p.startswith("scripts/")
        )
    ]
    for path in unexpected:
        errors.append(f"unexpected modified path for cleanup pass: {path}")
    stats = numstat(TASKS)
    if stats and max(stats) > 20:
        errors.append(f"{TASKS} diff is too large ({stats[0]} added, {stats[1]} removed); likely whole-file rewrite")
    state_stats = numstat("analysis/detailed-register-reverse-review/manual-markup-state.json")
    if state_stats and max(state_stats) > 250:
        errors.append(
            "analysis/detailed-register-reverse-review/manual-markup-state.json "
            f"diff is too large ({state_stats[0]} added, {state_stats[1]} removed); update only current pass state"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--pass-file", type=Path)
    parser.add_argument("--check-worktree", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors: list[str] = []
    suffix = task_suffix(args.task_id)
    queue_rows = validate_queue(args.task_id, suffix, errors)
    pass_rows: list[dict] = []
    if args.pass_file:
        pass_rows = validate_pass(args.pass_file, args.task_id, suffix, queue_rows, errors)
        validate_task_status(args.task_id, queue_rows, pass_rows, errors)
    if args.check_worktree:
        validate_worktree(args.task_id, errors)

    result = {
        "status": "error" if errors else "ok",
        "task_id": args.task_id,
        "queue_rows": len(queue_rows),
        "pass_file": str(args.pass_file) if args.pass_file else "",
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
