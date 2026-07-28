from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from .contracts import canonical_json, sha256


ALGORITHM_VERSION = "whole-component-meaning/v1"


def _fingerprint(value: Any) -> str:
    return "sha256:" + sha256(canonical_json(value))


def derive(
    repo: Path,
    rows: Iterable[dict[str, Any]] | None = None,
    *,
    validate: bool = True,
) -> list[dict[str, Any]]:
    if validate:
        from .sources import validate_active as validate_source
        source = validate_source(repo)
    else:
        source = json.loads((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    source_root = repo / "sources/generations" / source["generation_id"]
    routing = json.loads((source_root / source["routing_manifest_path"]).read_text(encoding="utf-8"))
    if rows is None:
        if validate:
            from .diffs import validate_active as validate_diff
            diff = validate_diff(repo)
        else:
            diff = json.loads((repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
        diff_root = repo / "analysis/indexes/generations" / diff["generation_id"]
        with (diff_root / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
            rows = [row for row in csv.DictReader(stream) if row["after_role"] == "target_cf"]
        with (diff_root / "target-coverage.csv").open(encoding="utf-8", newline="") as stream:
            coverage = {row["customer_diff_id"]: row for row in csv.DictReader(stream)}
    else:
        coverage = {}
    inventory = list(rows)
    groups: list[dict[str, Any]] = []
    for group in routing["groups"]:
        group_id = str(group["routing_group_id"])
        if group_id == "configuration":
            continue
        roles = {member["role"] for member in group["members"]}
        if "vendor_baseline" not in roles and "target_cf" in roles:
            direction, evidence_role = "added", "target_cf"
        elif "vendor_baseline" in roles and "target_cf" not in roles:
            direction, evidence_role = "deleted", "vendor_baseline"
        else:
            direction, evidence_role = "modified", ""
        prefix = group_id.replace("extension:", "extensions/", 1).replace("external:", "external/", 1)
        members = sorted(
            (row for row in inventory if str(row.get("path", "")).startswith(prefix + "/")),
            key=lambda row: row["stable_diff_id"],
        )
        if not members:
            continue
        groups.append({
            "component_kind": "extension" if group_id.startswith("extension:") else "external_artifact",
            "component_key": group_id.split(":", 1)[1],
            "direction": direction,
            "evidence_role": evidence_role,
            "stable_diff_ids": [row["stable_diff_id"] for row in members],
            "evidence": {
                row["stable_diff_id"]: [{
                    "path": f"{evidence_role}/{row['path']}",
                    "fingerprint": row["after_fingerprint"] if direction == "added" else row["before_fingerprint"],
                }]
                for row in members
                if evidence_role
            },
            "evidence_fingerprints": {
                row["stable_diff_id"]: _fingerprint({
                    "path": row["path"],
                    "kind": row["object_kind"],
                    "before": row["before_fingerprint"],
                    "after": row["after_fingerprint"],
                })
                for row in members
            },
            "target_coverage": {
                row["stable_diff_id"]: coverage.get(row["stable_diff_id"])
                for row in members
                if coverage.get(row["stable_diff_id"])
            },
        })
    return sorted(groups, key=lambda item: (item["component_kind"], item["component_key"]))


def deterministic_results(
    repo: Path,
    identifiers: Iterable[str],
    *,
    validate: bool = True,
) -> dict[str, dict[str, Any]]:
    selected = set(identifiers)
    results: dict[str, dict[str, Any]] = {}
    for group in derive(repo, validate=validate):
        if group["direction"] not in {"added", "deleted"}:
            continue
        for identifier in group["stable_diff_ids"]:
            if identifier not in selected:
                continue
            evidence = group["evidence"][identifier]
            results[identifier] = {
                "result": {
                    "kind": "meaning",
                    "semantic_hints": [
                        f"component:{group['component_kind']}:{group['component_key']}",
                        f"whole-component:{group['direction']}",
                    ],
                    "evidence": evidence,
                    "rationale": f"{group['component_kind']} {group['component_key']} is wholly {group['direction']}",
                },
                "evidence_fingerprint": group["evidence_fingerprints"][identifier],
                "result_schema_fingerprint": _fingerprint({"schema": "dif-classification/v1", "algorithm": ALGORITHM_VERSION}),
                "profile_fingerprint": _fingerprint({"coordinator": ALGORITHM_VERSION}),
                "instruction_fingerprint": _fingerprint(ALGORITHM_VERSION),
                "context_fingerprint": _fingerprint({
                    "algorithm": ALGORITHM_VERSION,
                    "component_kind": group["component_kind"],
                    "component_key": group["component_key"],
                    "direction": group["direction"],
                    "stable_diff_id": identifier,
                    "evidence": evidence,
                }),
            }
    return results
