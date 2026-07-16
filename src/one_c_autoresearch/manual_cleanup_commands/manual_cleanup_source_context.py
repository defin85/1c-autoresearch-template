#!/usr/bin/env python3
"""Return bounded source context for one manual-cleanup row."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from one_c_autoresearch.common import run_git as common_run_git
from one_c_autoresearch.manual_cleanup_commands.config import path as configured_path, ref as configured_ref


QUEUE = Path("analysis/detailed-register-reverse-review/manual-markup-queue.jsonl")
NESTED_REPO = configured_path("comparison_repo", "analysis/cache/noise/clean-rebase-v8unpack/repo")
CLEAN_REPO = configured_path("normalized_repo", "analysis/cache/clean-rebase/repo")
VENDOR_REF = configured_ref("vendor_ref", "HEAD^")
CUSTOMER_REF = configured_ref("customer_ref", "HEAD")
MAX_DIFF_CHARS = 20000


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        rows.append(json.loads(obj) if isinstance(obj, str) else obj)
    return rows


def find_row(row_id: str) -> dict:
    for row in read_jsonl(QUEUE):
        if row.get("row_id") == row_id:
            return row
    raise SystemExit(f"row not found: {row_id}")


def run_git(repo: Path, args: list[str]) -> str:
    result = common_run_git(repo, args)
    return result.stdout if result.returncode == 0 else result.stderr


def probe_row(row_id: str) -> dict:
    result = subprocess.run(
        ["python3", "-m", "one_c_autoresearch", "manual-cleanup", "probe-row", "--row-id", row_id],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def bounded_diff(repo: Path, paths: list[str]) -> tuple[str, bool]:
    if not paths:
        return "", False
    diff = run_git(repo, ["diff", "--no-ext-diff", "--unified=40", f"{VENDOR_REF}..{CUSTOMER_REF}", "--", *paths])
    truncated = len(diff) > MAX_DIFF_CHARS
    if truncated:
        diff = diff[:MAX_DIFF_CHARS] + "\n[truncated]\n"
    return diff, truncated


def owner_prefix(path: str) -> str:
    parts = path.split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--row-id", required=True)
    args = parser.parse_args()

    row = find_row(args.row_id)
    paths = row.get("paths", [])
    probe = probe_row(args.row_id)
    owners = sorted({owner_prefix(path) for path in paths})
    related = []
    for owner in owners:
        related.extend(run_git(NESTED_REPO, ["ls-tree", "-r", "--name-only", CUSTOMER_REF, owner]).splitlines())
    related = sorted(set(path for path in related if path.endswith((".json", ".bsl", ".mxl", ".wsdl", ".xsd", ".bin", ".c1b64", ".c1brace"))))[:80]
    nested_diff, nested_truncated = bounded_diff(NESTED_REPO, paths)
    normalized_diff_paths = sorted(set(probe.get("semantic_diff_paths", []) + probe.get("context_diff_paths", [])))
    normalized_diff, normalized_truncated = bounded_diff(CLEAN_REPO, normalized_diff_paths)

    print(
        json.dumps(
            {
                "row_id": args.row_id,
                "paths": paths,
                "key_objects": row.get("key_objects", ""),
                "owner_prefixes": owners,
                "related_source_paths": related,
                "probe": {
                    "strategy": probe.get("strategy", ""),
                    "semantic_diff_paths": probe.get("semantic_diff_paths", []),
                    "context_diff_paths": probe.get("context_diff_paths", []),
                    "readable_peer_paths": probe.get("readable_peer_paths", []),
                    "has_readable_peer": probe.get("has_readable_peer", False),
                    "suggested_decision": probe.get("suggested_decision", ""),
                    "decision_reason": probe.get("decision_reason", ""),
                },
                "diff": nested_diff,
                "has_diff": bool(nested_diff.strip()),
                "diff_truncated": nested_truncated,
                "normalized_diff_paths": normalized_diff_paths,
                "normalized_diff": normalized_diff,
                "has_normalized_diff": bool(normalized_diff.strip()),
                "normalized_diff_truncated": normalized_truncated,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
