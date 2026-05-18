from __future__ import annotations

import argparse
import csv
import json
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .autopilot import DIFF_INVENTORY_HEADER
from .common import read_jsonl, repo_path, utc_now_iso, write_jsonl


REVERSE_MAP_COVERAGE_HEADER = (
    "diff_id,source,change_type,path,object_kind,object_name,area,source_feature_id,"
    "scenario_id,workitem_id,status,confidence,evidence_ref,decision_ref,notes"
)
REVERSE_MAP_DECISIONS_HEADER = (
    "decision_id,workitem_id,diff_id,scenario_id,decision,confidence,rationale,"
    "evidence_ref,decided_by,decided_at"
)
REVERSE_MAP_UNRESOLVED_HEADER = (
    "item_id,workitem_id,diff_id,scenario_id,status,reason,needed_input,impact,owner,notes"
)

REVERSE_MAP_STATUSES = {
    "unreviewed",
    "assigned",
    "confirmed_in_scenario",
    "supporting_shared",
    "belongs_to_other_scenario",
    "technical_platform",
    "technical_noise",
    "needs_infobase_data",
    "needs_manual_review",
    "out_of_scope",
}

REVERSE_MAP_WORKITEM_STATUSES = {
    "pending",
    "claimed",
    "in_progress",
    "evidence_pack",
    "drafted",
    "needs_review",
    "needs_followup",
    "blocked",
    "done",
    "skipped",
}


@contextmanager
def reverse_map_lock(path: Path, timeout_seconds: int = 10) -> Iterator[None]:
    lock_path = path.with_name(f"{path.name}.lock")
    deadline = time.monotonic() + timeout_seconds
    handle = None
    while handle is None:
        try:
            handle = lock_path.open("x")
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for reverse-map lock: {lock_path}")
            time.sleep(0.1)
    try:
        yield
    finally:
        handle.close()
        lock_path.unlink(missing_ok=True)


def _write_text(path: Path, content: str, force: bool) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def _write_csv_header(path: Path, header: str, force: bool) -> bool:
    return _write_text(path, header + "\n", force)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_csv(path: Path, header: str, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = header.split(",")
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _load_workitems(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [row for _, row in read_jsonl(path)]


def _next_workitem_id(workitems: list[dict[str, Any]]) -> str:
    last = 0
    for item in workitems:
        match = re.fullmatch(r"RM-(\d+)", str(item.get("id", "")))
        if match:
            last = max(last, int(match.group(1)))
    return f"RM-{last + 1:04d}"


def _scenario_slug(value: str, fallback: str) -> str:
    raw = value.strip().lower()
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if ascii_slug:
        return ascii_slug[:48]
    digest = abs(hash(raw or fallback)) % 100000
    return f"{fallback}-{digest:05d}"


def reverse_map_paths(root: Path) -> dict[str, Path]:
    base = repo_path(root, "analysis/reverse-map")
    return {
        "base": base,
        "readme": base / "README.md",
        "state": base / "state.md",
        "coverage": base / "coverage.csv",
        "workitems": base / "workitems.jsonl",
        "decisions": base / "decisions.csv",
        "unresolved": base / "unresolved.csv",
        "scenarios_readme": base / "scenarios" / "README.md",
        "outputs_readme": base / "outputs" / "README.md",
    }


def scaffold_reverse_map_files(root: Path, force: bool = False) -> tuple[list[str], list[str]]:
    paths = reverse_map_paths(root)
    created: list[str] = []
    skipped: list[str] = []
    files = {
        "analysis/reverse-map/README.md": (
            "# Reverse Functional Map\n\n"
            "Persistent state for evidence-guided reverse functional mapping.\n\n"
            "Use this when the user triggers `/goal Исследование` or asks to continue the reverse engineering run.\n"
            "The agent must read `state.md`, run `python -m one_c_autoresearch reverse-map claim`, process exactly one workitem, update coverage and evidence, then run doctor checks.\n"
        ),
        "analysis/reverse-map/state.md": (
            "# Reverse Map State\n\n"
            "Status: initialized\n\n"
            "Continuation command: `python -m one_c_autoresearch reverse-map claim`\n\n"
            "If no pending workitem exists, the reverse-map command seeds new workitems from uncovered rows in `analysis/indexes/diff-inventory.csv`.\n"
        ),
        "analysis/reverse-map/scenarios/README.md": (
            "# Scenario Details\n\n"
            "Store one folder per reconstructed scenario. Each scenario should contain an evidence-backed summary and optional detail workbook.\n"
        ),
        "analysis/reverse-map/outputs/README.md": (
            "# Reverse Map Outputs\n\n"
            "Generated review workbooks and intermediate human-facing reverse-map outputs can be stored here before promotion to `outputs/`.\n"
        ),
    }
    for relative, content in files.items():
        path = repo_path(root, relative)
        if _write_text(path, content, force):
            created.append(relative)
        else:
            skipped.append(relative)

    csv_files = {
        "analysis/reverse-map/coverage.csv": REVERSE_MAP_COVERAGE_HEADER,
        "analysis/reverse-map/decisions.csv": REVERSE_MAP_DECISIONS_HEADER,
        "analysis/reverse-map/unresolved.csv": REVERSE_MAP_UNRESOLVED_HEADER,
    }
    for relative, header in csv_files.items():
        path = repo_path(root, relative)
        if _write_csv_header(path, header, force):
            created.append(relative)
        else:
            skipped.append(relative)

    workitems = paths["workitems"]
    if _write_text(workitems, "", force):
        created.append("analysis/reverse-map/workitems.jsonl")
    else:
        skipped.append("analysis/reverse-map/workitems.jsonl")
    return created, skipped


def scaffold_reverse_map(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    created, skipped = scaffold_reverse_map_files(root, force=args.force)
    print(f"Reverse-map scaffolded at: {root}")
    if created:
        print("Created or updated:")
        for item in created:
            print(f"- {item}")
    if skipped:
        print("Skipped existing files without --force:")
        for item in skipped:
            print(f"- {item}")
    return 0


def _read_diff_inventory(root: Path) -> list[dict[str, str]]:
    path = repo_path(root, "analysis/indexes/diff-inventory.csv")
    if not path.exists():
        return []
    first = path.read_text(encoding="utf-8-sig").splitlines()[:1]
    if not first or first[0] != DIFF_INVENTORY_HEADER:
        return []
    return _read_csv(path)


def _coverage_row_from_diff(row: dict[str, str]) -> dict[str, str]:
    return {
        "diff_id": row.get("diff_id", ""),
        "source": row.get("source", ""),
        "change_type": row.get("change_type", ""),
        "path": row.get("path", ""),
        "object_kind": row.get("object_kind", ""),
        "object_name": row.get("object_name", ""),
        "area": row.get("area", ""),
        "source_feature_id": row.get("feature_id", ""),
        "scenario_id": "",
        "workitem_id": "",
        "status": "unreviewed",
        "confidence": "",
        "evidence_ref": "",
        "decision_ref": "",
        "notes": "Seeded from analysis/indexes/diff-inventory.csv.",
    }


def _group_key(row: dict[str, str]) -> tuple[str, str]:
    feature = row.get("source_feature_id", "").strip()
    if feature:
        return ("feature", feature)
    area = row.get("area", "").strip()
    if area:
        return ("area", area)
    kind = row.get("object_kind", "").strip()
    if kind:
        return ("object_kind", kind)
    source = row.get("source", "").strip() or "unmapped"
    return ("source", source)


def seed_reverse_map_workitems(root: Path) -> int:
    root = root.resolve()
    scaffold_reverse_map_files(root, force=False)
    paths = reverse_map_paths(root)

    diff_rows = _read_diff_inventory(root)
    coverage_rows = _read_csv(paths["coverage"])
    by_diff_id = {row.get("diff_id", ""): row for row in coverage_rows if row.get("diff_id")}
    changed = False
    for diff_row in diff_rows:
        diff_id = diff_row.get("diff_id", "")
        if diff_id and diff_id not in by_diff_id:
            coverage_row = _coverage_row_from_diff(diff_row)
            coverage_rows.append(coverage_row)
            by_diff_id[diff_id] = coverage_row
            changed = True

    workitems = _load_workitems(paths["workitems"])
    open_workitem_ids = {
        str(item.get("id", ""))
        for item in workitems
        if item.get("status") in {"pending", "claimed", "in_progress", "needs_review", "needs_followup", "blocked"}
    }
    rows_to_assign = [
        row
        for row in coverage_rows
        if row.get("status", "") == "unreviewed"
        and (not row.get("workitem_id") or row.get("workitem_id") not in open_workitem_ids)
    ]
    if not rows_to_assign:
        if changed:
            _write_csv(paths["coverage"], REVERSE_MAP_COVERAGE_HEADER, coverage_rows)
        return 0

    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows_to_assign:
        groups.setdefault(_group_key(row), []).append(row)

    now = utc_now_iso()
    created = 0
    for (key_kind, key_value), rows in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1])):
        workitem_id = _next_workitem_id(workitems)
        scenario_id = rows[0].get("source_feature_id", "").strip() or _scenario_slug(key_value, "scenario")
        title = f"Reverse-map: {key_value}"
        workitem = {
            "id": workitem_id,
            "type": "reverse_map",
            "status": "pending",
            "priority": 100,
            "title": title,
            "scenario_id": scenario_id,
            "scope": [f"{key_kind}:{key_value}"],
            "coverage_filter": {key_kind: key_value},
            "source_diff_ids": [row.get("diff_id", "") for row in rows if row.get("diff_id")],
            "quality_gates": [
                "fact_coverage",
                "source_evidence",
                "scenario_summary",
                "manual_decisions_or_needs_review",
                "needs_infobase_data_marked",
            ],
            "expected_outputs": [
                f"analysis/reverse-map/scenarios/{scenario_id}/summary.md",
                f"analysis/reverse-map/scenarios/{scenario_id}/evidence.csv",
                f"analysis/reverse-map/scenarios/{scenario_id}/decisions.csv",
            ],
            "created_at": now,
            "updated_at": now,
        }
        workitems.append(workitem)
        for row in rows:
            row["status"] = "assigned"
            row["workitem_id"] = workitem_id
            row["scenario_id"] = scenario_id
            row["notes"] = f"Assigned to {workitem_id} by reverse-map seed."
        created += 1

    _write_csv(paths["coverage"], REVERSE_MAP_COVERAGE_HEADER, coverage_rows)
    write_jsonl(paths["workitems"], workitems)
    return created


def _sort_workitems(workitems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(workitems, key=lambda item: (-int(item.get("priority", 0)), str(item.get("id", ""))))


def get_next_reverse_map_workitem(root: Path) -> dict[str, Any]:
    seed_reverse_map_workitems(root)
    workitems = _load_workitems(reverse_map_paths(root)["workitems"])
    pending = [item for item in workitems if item.get("status") == "pending"]
    ordered = _sort_workitems(pending)
    return ordered[0] if ordered else {}


def claim_reverse_map_workitem(root: Path, claimed_by: str = "codex", lock_timeout_seconds: int = 10) -> dict[str, Any]:
    seed_reverse_map_workitems(root)
    path = reverse_map_paths(root)["workitems"]
    with reverse_map_lock(path, lock_timeout_seconds):
        workitems = _load_workitems(path)
        pending = _sort_workitems([item for item in workitems if item.get("status") == "pending"])
        if not pending:
            return {}
        claimed = pending[0]
        now = utc_now_iso()
        claimed["status"] = "claimed"
        claimed["claimed_by"] = claimed_by
        claimed["claimed_at"] = now
        claimed["updated_at"] = now
        write_jsonl(path, workitems)
        return claimed


def set_reverse_map_workitem_status(
    root: Path,
    workitem_id: str,
    status: str,
    result_summary: str | None = None,
    expected_status: str | None = None,
    lock_timeout_seconds: int = 10,
) -> dict[str, Any]:
    if status not in REVERSE_MAP_WORKITEM_STATUSES:
        raise ValueError(f"Invalid reverse-map workitem status: {status}")
    path = reverse_map_paths(root)["workitems"]
    with reverse_map_lock(path, lock_timeout_seconds):
        workitems = _load_workitems(path)
        now = utc_now_iso()
        updated: dict[str, Any] | None = None
        for item in workitems:
            if str(item.get("id", "")) != workitem_id:
                continue
            if expected_status and item.get("status") != expected_status:
                raise RuntimeError(f"Workitem {workitem_id} expected status '{expected_status}' but found '{item.get('status')}'")
            item["status"] = status
            item["updated_at"] = now
            if status in {"done", "skipped"}:
                item["completed_at"] = now
            if result_summary:
                item["result_summary"] = result_summary
            updated = item
            break
        if updated is None:
            raise RuntimeError(f"Reverse-map workitem not found: {workitem_id}")
        write_jsonl(path, workitems)
        return updated


def reverse_map_status(root: Path) -> dict[str, Any]:
    paths = reverse_map_paths(root)
    coverage_rows = _read_csv(paths["coverage"])
    workitems = _load_workitems(paths["workitems"])
    by_coverage_status: dict[str, int] = {}
    for row in coverage_rows:
        status = row.get("status", "") or "<empty>"
        by_coverage_status[status] = by_coverage_status.get(status, 0) + 1
    by_workitem_status: dict[str, int] = {}
    for item in workitems:
        status = str(item.get("status", "") or "<empty>")
        by_workitem_status[status] = by_workitem_status.get(status, 0) + 1
    return {
        "coverage_rows": len(coverage_rows),
        "coverage_by_status": by_coverage_status,
        "workitems": len(workitems),
        "workitems_by_status": by_workitem_status,
        "next": get_next_reverse_map_workitem(root),
    }


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def seed_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    created = seed_reverse_map_workitems(root)
    _print_json({"created_workitems": created, "status": reverse_map_status(root)})
    return 0


def next_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    _print_json(get_next_reverse_map_workitem(root))
    return 0


def claim_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    _print_json(claim_reverse_map_workitem(root, claimed_by=args.claimed_by, lock_timeout_seconds=args.lock_timeout_seconds))
    return 0


def set_status_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    _print_json(
        set_reverse_map_workitem_status(
            root,
            args.id,
            args.status,
            result_summary=args.result_summary,
            expected_status=args.expected_status,
            lock_timeout_seconds=args.lock_timeout_seconds,
        )
    )
    return 0


def status_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    _print_json(reverse_map_status(root))
    return 0
