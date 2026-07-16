from __future__ import annotations

import csv
import hashlib
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

from .common import repo_path


DIFF_ID_MAP_HEADER = (
    "stable_diff_id,current_diff_id,source,change_type,path,object_kind,object_name,area,"
    "active,content_fingerprint,first_seen_ref,last_seen_ref,removed_by,notes"
)
DIFF_ID_MAP_PATH = "analysis/indexes/diff-id-map.csv"
STABLE_DIFF_ID_VERSION = "diff-stable-v1"


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv_rows(path: Path, header: str, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = header.split(",")
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def normalize_diff_path(path: str) -> str:
    return unicodedata.normalize("NFC", str(path or "").replace("\\", "/").lstrip("./"))


def stable_diff_id(source: str, change_type: str, path: str) -> str:
    payload = "\0".join([STABLE_DIFF_ID_VERSION, source or "", change_type or "", normalize_diff_path(path)])
    return "DIF-" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16].upper()


def stable_diff_id_for_row(row: dict[str, str]) -> str:
    return stable_diff_id(row.get("source", ""), row.get("change_type", ""), row.get("path", ""))


def _git_blob(repo: Path | None, ref: str, path: str) -> str:
    if repo is None or not path:
        return ""
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{ref}:{path}"],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def content_fingerprint(row: dict[str, str], nested_repo: Path | None = None, vendor_ref: str = "", target_ref: str = "") -> str:
    path = normalize_diff_path(row.get("path", ""))
    vendor_blob = _git_blob(nested_repo, vendor_ref, path) if vendor_ref else ""
    target_blob = _git_blob(nested_repo, target_ref, path) if target_ref else ""
    return f"vendor_blob={vendor_blob};target_blob={target_blob}"


def build_diff_id_map_rows(
    inventory_rows: list[dict[str, str]],
    *,
    previous_rows: list[dict[str, str]] | None = None,
    nested_repo: Path | None = None,
    vendor_ref: str = "",
    target_ref: str = "",
    first_seen_ref: str = "",
    last_seen_ref: str = "",
) -> list[dict[str, str]]:
    previous_by_id = {row.get("stable_diff_id", ""): row for row in previous_rows or [] if row.get("stable_diff_id")}
    rows: list[dict[str, str]] = []
    active_ids: set[str] = set()
    for row in inventory_rows:
        sid = stable_diff_id_for_row(row)
        previous = previous_by_id.get(sid, {})
        active_ids.add(sid)
        rows.append(
            {
                "stable_diff_id": sid,
                "current_diff_id": row.get("diff_id", ""),
                "source": row.get("source", ""),
                "change_type": row.get("change_type", ""),
                "path": normalize_diff_path(row.get("path", "")),
                "object_kind": row.get("object_kind", ""),
                "object_name": row.get("object_name", ""),
                "area": row.get("area", ""),
                "active": "true",
                "content_fingerprint": content_fingerprint(row, nested_repo, vendor_ref, target_ref),
                "first_seen_ref": previous.get("first_seen_ref") or first_seen_ref,
                "last_seen_ref": last_seen_ref or previous.get("last_seen_ref", ""),
                "removed_by": "",
                "notes": previous.get("notes", ""),
            }
        )
    for sid, previous in sorted(previous_by_id.items()):
        if sid in active_ids:
            continue
        inactive = dict(previous)
        inactive["active"] = "false"
        inactive["current_diff_id"] = ""
        inactive.setdefault("removed_by", "")
        rows.append(inactive)
    return rows


def validate_diff_id_map_rows(rows: list[dict[str, str]]) -> list[str]:
    seen: dict[str, str] = {}
    errors: list[str] = []
    for row in rows:
        if row.get("active") != "true":
            continue
        sid = row.get("stable_diff_id", "")
        path = row.get("path", "")
        if not sid:
            errors.append(f"active diff-id-map row has empty stable_diff_id: {path}")
        elif sid in seen:
            errors.append(f"duplicate active stable_diff_id {sid}: {seen[sid]} ; {path}")
        else:
            seen[sid] = path
    return errors


def write_diff_id_map(
    root: Path,
    inventory_rows: list[dict[str, str]],
    *,
    nested_repo: Path | None = None,
    vendor_ref: str = "",
    target_ref: str = "",
    first_seen_ref: str = "",
    last_seen_ref: str = "",
) -> list[dict[str, str]]:
    path = repo_path(root, DIFF_ID_MAP_PATH)
    rows = build_diff_id_map_rows(
        inventory_rows,
        previous_rows=read_csv_rows(path),
        nested_repo=nested_repo,
        vendor_ref=vendor_ref,
        target_ref=target_ref,
        first_seen_ref=first_seen_ref,
        last_seen_ref=last_seen_ref,
    )
    errors = validate_diff_id_map_rows(rows)
    if errors:
        raise RuntimeError("\n".join(errors))
    write_csv_rows(path, DIFF_ID_MAP_HEADER, rows)
    return rows


def load_active_stable_diff_ids(root: Path) -> dict[str, dict[str, str]]:
    rows = read_csv_rows(repo_path(root, DIFF_ID_MAP_PATH))
    return {row.get("stable_diff_id", ""): row for row in rows if row.get("stable_diff_id") and row.get("active") == "true"}


def current_to_stable_diff_ids(root: Path) -> dict[str, str]:
    rows = read_csv_rows(repo_path(root, DIFF_ID_MAP_PATH))
    return {
        row.get("current_diff_id", ""): row.get("stable_diff_id", "")
        for row in rows
        if row.get("active") == "true" and row.get("current_diff_id") and row.get("stable_diff_id")
    }
