#!/usr/bin/env python3
"""Apply structured manual-cleanup decisions to queue, pass files, and nested repo."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from one_c_autoresearch.common import run_git
from one_c_autoresearch.manual_cleanup_commands.config import path as configured_path, ref as configured_ref


QUEUE = Path("analysis/detailed-register-reverse-review/manual-markup-queue.jsonl")
STATE = Path("analysis/detailed-register-reverse-review/manual-markup-state.json")
OUT_DIR = Path("analysis/detailed-register-reverse-review")
NESTED_REPO = configured_path("comparison_repo", "analysis/cache/noise/clean-rebase-v8unpack/repo")
VENDOR_REF = configured_ref("vendor_ref", "HEAD^")
STATE_ROW_PREVIEW_LIMIT = 30
ALLOWED = {"remove_noise", "keep_customization", "manual_review"}
ALLOWED_BASIS = {"probe_only", "source_context", "free_read"}
FINAL_CLEANUP_PREFIX = "manual-final-cleanup-"
FIELDS = [
    "row_id",
    "task_id",
    "status",
    "decision",
    "old_diff_id",
    "current_diff_id",
    "stable_diff_id",
    "review_basis",
    "source_paths",
    "removed_paths",
    "cleanup_commit",
    "evidence_refs",
    "notes",
]


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


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def git(args: list[str]) -> str:
    return run_git(NESTED_REPO, args, check=True).stdout


def ensure_nested_repo_clean() -> None:
    dirty = git(["status", "--porcelain"]).strip()
    if dirty:
        raise SystemExit(f"nested cleanup repo has uncommitted changes; refuse to apply cleanup:\n{dirty}")


def load_decisions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    decisions = data.get("decisions", data if isinstance(data, list) else [])
    if not isinstance(decisions, list):
        raise SystemExit("decisions must be a list or an object with decisions")
    row_ids = [item.get("row_id", "") for item in decisions]
    missing = [idx + 1 for idx, row_id in enumerate(row_ids) if not row_id]
    duplicate = sorted({row_id for row_id in row_ids if row_ids.count(row_id) > 1})
    if missing or duplicate:
        raise SystemExit(f"invalid decision row_id values: missing_indexes={missing}, duplicate={duplicate}")
    for item in decisions:
        if item.get("decision") not in ALLOWED:
            raise SystemExit(f"invalid decision for {item.get('row_id')}: {item.get('decision')}")
        if item.get("review_basis") not in ALLOWED_BASIS:
            raise SystemExit(f"invalid review_basis for {item.get('row_id')}: {item.get('review_basis')}")
    return decisions


def is_final_cleanup_pass(pass_id: str) -> bool:
    return pass_id.startswith(FINAL_CLEANUP_PREFIX)


def validate_final_cleanup_decisions(decisions: list[dict], pass_id: str) -> None:
    if not is_final_cleanup_pass(pass_id):
        return
    manual = [item.get("row_id", "") for item in decisions if item.get("decision") == "manual_review"]
    if manual:
        raise SystemExit(f"final cleanup pass cannot apply manual_review rows: {', '.join(manual)}")


def is_allowed_cleanup_path(path: str) -> bool:
    return (
        (
            "/Template/" in path
            and (
                path.endswith("/Template.mxl")
                or path.endswith("/Template.bin")
                or path.endswith("/Template.json")
                or path.endswith("/Template.id.json")
                or path.endswith("/Template.html")
            )
        )
        or (
            path.startswith("CommonTemplate/")
            and (path.endswith("/CommonTemplate.mxl") or path.endswith("/CommonTemplate.json"))
        )
    )


def commit_removed_paths(paths: list[str], pass_id: str) -> str:
    if not paths:
        return ""
    ensure_nested_repo_clean()
    git(["checkout", VENDOR_REF, "--", *paths])
    git(["add", "--", *paths])
    changed = git(["diff", "--cached", "--name-only", "--", *paths]).splitlines()
    if not changed:
        return ""
    git(["commit", "-m", f"Remove template payload noise for {pass_id}"])
    return git(["rev-parse", "--short", "HEAD"]).strip()


def ensure_output_files_absent(paths: list[Path]) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise SystemExit(f"pass output files already exist; choose a unique --pass-id: {existing}")


def update_state(pass_id: str, task_id: str, pass_file: Path, summary_file: Path, rows: list[dict], counters: Counter) -> None:
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    row_preview = [row["row_id"] for row in rows[-STATE_ROW_PREVIEW_LIMIT:]]
    state["last_pass"] = pass_id
    state["last_pass_id"] = pass_id
    state["last_pass_markup"] = str(pass_file)
    state["last_pass_summary"] = str(summary_file)
    state["last_task_id"] = task_id
    state["last_row_id"] = rows[-1]["row_id"] if rows else state.get("last_row_id", "")
    state["last_rows"] = row_preview
    state["last_processed_rows"] = row_preview
    state["last_pass_counters"] = dict(counters)
    state["last_updated"] = utc_now()
    state["last_updated_at"] = state["last_updated"]
    state.setdefault("batch_counters", {}).setdefault(pass_id.rsplit("-pass-", 1)[0], {})
    for key, value in counters.items():
        state["batch_counters"][pass_id.rsplit("-pass-", 1)[0]][key] = (
            state["batch_counters"][pass_id.rsplit("-pass-", 1)[0]].get(key, 0) + value
        )
    for counter_key in ("counters", "global_counters"):
        state.setdefault(counter_key, {})
        state[counter_key]["done"] = state[counter_key].get("done", 0) + len(rows)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--pass-id", required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pass_file = OUT_DIR / f"{args.pass_id}.csv"
    summary_file = OUT_DIR / f"summary-{args.pass_id}.md"
    ensure_output_files_absent([pass_file, summary_file])
    decisions = load_decisions(args.decisions)
    validate_final_cleanup_decisions(decisions, args.pass_id)
    by_id = {item["row_id"]: item for item in decisions}
    queue_rows = read_jsonl(QUEUE)
    rows_by_id = {row.get("row_id"): row for row in queue_rows}
    missing = [row_id for row_id in by_id if row_id not in rows_by_id]
    if missing:
        raise SystemExit(f"decisions reference missing queue rows: {missing}")

    remove_paths = []
    for row_id, decision in by_id.items():
        if decision["decision"] == "remove_noise":
            remove_paths.extend(rows_by_id[row_id].get("paths", []))
    if is_final_cleanup_pass(args.pass_id):
        bad_paths = sorted(path for path in remove_paths if not is_allowed_cleanup_path(path))
        if bad_paths:
            raise SystemExit(f"final cleanup pass refuses non-template cleanup paths: {bad_paths}")
    cleanup_commit = commit_removed_paths(remove_paths, args.pass_id)
    if remove_paths and not cleanup_commit:
        raise SystemExit("remove_noise decisions produced no nested cleanup commit")

    now = utc_now()
    pass_rows = []
    counters: Counter = Counter()
    for row_id, decision in by_id.items():
        row = rows_by_id[row_id]
        verdict = decision["decision"]
        notes = decision.get("notes", "")
        if verdict == "remove_noise" and "техничес" not in notes.lower() and "шум" not in notes.lower():
            notes = "Технический шум: " + notes
        counters[verdict] += 1
        row["attempts"] = int(row.get("attempts", 0)) + 1
        row["decision"] = verdict
        row["last_pass"] = args.pass_id
        row["last_updated"] = now
        row["status"] = "done" if verdict != "manual_review" else "needs_evidence_after_source_search"
        row["notes"] = notes
        row["review_basis"] = decision.get("review_basis", "")
        if verdict == "remove_noise":
            row["cleanup_commit"] = cleanup_commit
            row["removed_paths"] = row.get("paths", [])
        pass_rows.append(
            {
                "row_id": row_id,
                "task_id": args.task_id,
                "status": row["status"],
                "decision": verdict,
                "old_diff_id": ";".join(row.get("old_diff_ids", [])),
                "current_diff_id": "" if verdict == "remove_noise" else ";".join(row.get("current_diff_ids", [])),
                "stable_diff_id": ";".join(row.get("stable_diff_ids", [])),
                "review_basis": decision.get("review_basis", ""),
                "source_paths": ";".join(decision.get("source_paths") or row.get("paths", [])),
                "removed_paths": ";".join(row.get("paths", [])) if verdict == "remove_noise" else "",
                "cleanup_commit": cleanup_commit if verdict == "remove_noise" else "",
                "evidence_refs": ";".join(decision.get("evidence_refs", [])),
                "notes": notes,
            }
        )

    write_jsonl(QUEUE, queue_rows)
    with pass_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(pass_rows)
    summary_file.write_text(
        "# Ручная разметка: " + args.pass_id + "\n\n"
        + f"Обработано строк: {len(pass_rows)}. "
        + ", ".join(f"{key}: {value}" for key, value in sorted(counters.items()))
        + ".\n\n"
        + f"`{args.task_id}` остается `pending`: это малый пробный проход.\n\n"
        + (f"Cleanup commit во вложенном repo: `{cleanup_commit}`.\n" if cleanup_commit else "Cleanup commit не создавался.\n"),
        encoding="utf-8",
    )
    update_state(args.pass_id, args.task_id, pass_file, summary_file, pass_rows, counters)
    print(json.dumps({"pass_file": str(pass_file), "summary_file": str(summary_file), "cleanup_commit": cleanup_commit}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
