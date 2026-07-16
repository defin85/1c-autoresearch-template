from __future__ import annotations

import argparse
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .common import as_list, read_jsonl, repo_path, utc_now_iso, write_jsonl

VALID_STATUSES = {"pending", "claimed", "evidence_pack", "drafted", "needs_review", "needs_followup", "blocked", "done", "skipped"}


@contextmanager
def queue_lock(path: Path, timeout_seconds: int = 10) -> Iterator[None]:
    lock_path = path.with_name(f"{path.name}.lock")
    deadline = time.monotonic() + timeout_seconds
    handle = None
    while handle is None:
        try:
            handle = lock_path.open("x")
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for queue lock: {lock_path}")
            time.sleep(0.1)
    try:
        yield
    finally:
        handle.close()
        lock_path.unlink(missing_ok=True)


def default_queue_path(script_path: Path | None = None, repo_root: Path | None = None) -> Path:
    if repo_root is not None:
        return repo_path(repo_root, "analysis/queue/tasks.jsonl")
    if script_path is not None:
        return script_path.resolve().parents[2] / "analysis" / "queue" / "tasks.jsonl"
    return Path("analysis/queue/tasks.jsonl")


def load_tasks(queue_path: Path) -> list[dict[str, Any]]:
    return [task for _, task in read_jsonl(queue_path)]


def dependencies_done(task: dict[str, Any], done_ids: set[str]) -> bool:
    for dependency in as_list(task.get("dependencies")):
        dependency = str(dependency).strip()
        if dependency and dependency not in done_ids:
            return False
    return True


def sort_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(tasks, key=lambda task: (-int(task.get("priority", 0)), str(task.get("id", ""))))


def is_claimable_task(task: dict[str, Any], task_type: str | None) -> bool:
    return not task.get("parallel_adapter") and (task.get("type") != "manual_markup" or task_type == "manual_markup")


def get_next_task(queue_path: Path, status: str = "pending", task_type: str | None = None, all_tasks: bool = False, include_blocked: bool = False) -> Any:
    tasks = load_tasks(queue_path)
    done_ids = {str(task.get("id")) for task in tasks if task.get("status") in {"done", "skipped"}}
    candidates = [
        task
        for task in tasks
        if task.get("status") == status
        and is_claimable_task(task, task_type)
        and (task_type is None or task.get("type") == task_type)
        and (include_blocked or dependencies_done(task, done_ids))
    ]
    ordered = sort_tasks(candidates)
    if all_tasks:
        return ordered
    return ordered[0] if ordered else {}


def claim_next_task(queue_path: Path, status: str = "pending", task_type: str | None = None, claimed_by: str = "codex", lock_timeout_seconds: int = 10) -> dict[str, Any]:
    with queue_lock(queue_path, lock_timeout_seconds):
        tasks = load_tasks(queue_path)
        done_ids = {str(task.get("id")) for task in tasks if task.get("status") in {"done", "skipped"}}
        candidates = [
            task
            for task in tasks
            if task.get("status") == status
            and is_claimable_task(task, task_type)
            and (task_type is None or task.get("type") == task_type)
            and dependencies_done(task, done_ids)
        ]
        ordered = sort_tasks(candidates)
        if not ordered:
            return {}
        claimed = ordered[0]
        now = utc_now_iso()
        claimed["status"] = "claimed"
        claimed["updated_at"] = now
        claimed["claimed_by"] = claimed_by
        claimed["claimed_at"] = now
        write_jsonl(queue_path, tasks)
        return claimed


def set_task_status(
    queue_path: Path,
    task_id: str,
    status: str,
    claimed_by: str = "codex",
    result_summary: str | None = None,
    expected_status: str | None = None,
    lock_timeout_seconds: int = 10,
) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    with queue_lock(queue_path, lock_timeout_seconds):
        tasks = load_tasks(queue_path)
        now = utc_now_iso()
        updated: dict[str, Any] | None = None
        for task in tasks:
            if str(task.get("id")) != task_id:
                continue
            if expected_status and task.get("status") != expected_status:
                raise RuntimeError(f"Task {task_id} expected status '{expected_status}' but found '{task.get('status')}'")
            task["status"] = status
            task["updated_at"] = now
            if status == "claimed":
                task["claimed_by"] = claimed_by
                task["claimed_at"] = now
            if status in {"done", "skipped"}:
                task["completed_at"] = now
            if result_summary:
                task["result_summary"] = result_summary
            updated = task
            break
        if updated is None:
            raise RuntimeError(f"Task not found: {task_id}")
        write_jsonl(queue_path, tasks)
        return updated


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def get_command(args: argparse.Namespace) -> int:
    result = get_next_task(Path(args.queue_path).resolve(), args.status, args.type, args.all, args.include_blocked_by_dependencies)
    print_json(result)
    return 0


def claim_command(args: argparse.Namespace) -> int:
    result = claim_next_task(Path(args.queue_path).resolve(), args.status, args.type, args.claimed_by, args.lock_timeout_seconds)
    print_json(result)
    return 0


def set_status_command(args: argparse.Namespace) -> int:
    result = set_task_status(
        Path(args.queue_path).resolve(),
        args.id,
        args.status,
        claimed_by=args.claimed_by,
        result_summary=args.result_summary,
        expected_status=args.expected_status,
        lock_timeout_seconds=args.lock_timeout_seconds,
    )
    print_json(result)
    return 0
