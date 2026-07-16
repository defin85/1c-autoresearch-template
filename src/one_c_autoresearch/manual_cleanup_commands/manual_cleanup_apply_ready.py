#!/usr/bin/env python3
"""Apply ready manual-cleanup decision files with a single-writer lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


OUT_DIR = Path("analysis/detailed-register-reverse-review")
TRACE_DIR = Path("analysis/cache/manual-markup-traces")
QUEUE = OUT_DIR / "manual-markup-queue.jsonl"
LOCK = OUT_DIR / ".manual-cleanup-apply.lock"


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        rows.append(json.loads(obj) if isinstance(obj, str) else obj)
    return rows


def acquire_lock() -> None:
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            pid = int(LOCK.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
        except ProcessLookupError:
            LOCK.unlink(missing_ok=True)
            return acquire_lock()
        except (OSError, ValueError):
            pass
        raise SystemExit(f"apply lock exists: {LOCK}")
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()) + "\n")


def release_lock() -> None:
    LOCK.unlink(missing_ok=True)


def pass_id_from_decisions(path: Path) -> str:
    suffix = "-decisions.json"
    if not path.name.endswith(suffix):
        raise SystemExit(f"not a decisions file: {path}")
    return path.name[: -len(suffix)]


def decision_row_ids(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    decisions = data.get("decisions", data if isinstance(data, list) else [])
    return [item["row_id"] for item in decisions]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def task_id_for_rows(row_ids: list[str]) -> str:
    rows_by_id = {row.get("row_id"): row for row in read_jsonl(QUEUE)}
    task_ids = {
        rows_by_id[row_id].get("central_task_id") or rows_by_id[row_id].get("task_id")
        for row_id in row_ids
        if row_id in rows_by_id
    }
    if len(task_ids) != 1:
        raise SystemExit(f"cannot derive one task_id for rows: {row_ids[:5]}")
    return next(iter(task_ids))


def summary_skip_reason(pass_id: str, decisions: Path) -> str:
    path = TRACE_DIR / f"trace-{pass_id}.summary.json"
    if not path.exists():
        return "trace_summary_missing"
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("missing_probe_rows", "missing_source_context_rows", "suspicious_direct_reads", "errors"):
        if data.get(key):
            return key
    if data.get("decisions_sha256") != file_sha256(decisions):
        return "decisions_hash_mismatch"
    return ""


def run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--glob", default="manual-final-cleanup-*-decisions.json")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    acquire_lock()
    applied = []
    skipped = []
    try:
        for decisions in sorted(OUT_DIR.glob(args.glob)):
            pass_id = pass_id_from_decisions(decisions)
            pass_file = OUT_DIR / f"{pass_id}.csv"
            if pass_file.exists():
                skipped.append({"pass_id": pass_id, "reason": "pass_file_exists"})
                continue
            reason = summary_skip_reason(pass_id, decisions)
            if reason:
                skipped.append({"pass_id": pass_id, "reason": reason})
                continue
            task_id = task_id_for_rows(decision_row_ids(decisions))
            if args.dry_run:
                applied.append({"pass_id": pass_id, "task_id": task_id, "dry_run": True})
            else:
                run([
                    "python3",
                    "-m", "one_c_autoresearch", "manual-cleanup", "apply-decisions",
                    "--task-id",
                    task_id,
                    "--pass-id",
                    pass_id,
                    "--decisions",
                    str(decisions),
                ])
                run([
                    "python3",
                    "scripts/check_manual_cleanup_pass.py",
                    "--task-id",
                    task_id,
                    "--pass-file",
                    str(pass_file),
                    "--check-worktree",
                ])
                applied.append({"pass_id": pass_id, "task_id": task_id, "dry_run": False})
            if args.limit and len(applied) >= args.limit:
                break
    finally:
        release_lock()
    print(json.dumps({"applied": applied, "skipped": skipped}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
