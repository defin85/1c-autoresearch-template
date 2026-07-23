from __future__ import annotations

import csv
import os
import subprocess
import tempfile
import json
from pathlib import Path
from typing import Any

from .contracts import atomic_json, canonical_json, comparison_id, confined, content_id, diff_id, normalize_relative, repository_lock, require_tracked_clean, sha256


INVENTORY_HEADER = ("stable_diff_id", "comparison_id", "source_generation", "before_role", "after_role", "change_type", "path", "object_kind", "object_name", "area", "before_fingerprint", "after_fingerprint", "content_fingerprint")
ID_MAP_HEADER = ("stable_diff_id", "comparison_id", "change_type", "path", "active", "first_seen_generation", "last_seen_generation", "predecessor_diff_id", "successor_diff_id", "content_fingerprint", "notes")
COVERAGE_HEADER = ("customer_diff_id", "source_generation", "target_diff_ids", "coverage_status", "evidence_ref", "notes")
COMPARISONS = (("customer-customization", "vendor_baseline", "target_cf"), ("target-release", "vendor_baseline", "next_vendor"))
STATUSES = {"A": "added", "D": "deleted", "M": "modified", "T": "type_changed"}


def _fingerprint(path: Path) -> str:
    return "" if not path.is_file() else "sha256:" + sha256(path.read_bytes())


def _relative(raw: bytes, roots: tuple[Path, Path]) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Git returned a non-UTF-8 path") from exc
    path = Path(text)
    candidate = path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
    for root in roots:
        try:
            return normalize_relative(candidate.relative_to(root.resolve()).as_posix())
        except ValueError:
            pass
    raise ValueError(f"Git path escapes role roots: {text}")


def compare(before_root: Path, after_root: Path, metadata: dict[str, str]) -> list[dict[str, str]]:
    command = ["git", "-c", "core.quotepath=false", "-c", "core.fileMode=false", "diff", "--no-index", "--name-status", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", "--", str(before_root.resolve()), str(after_root.resolve())]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env={"PATH": os.environ.get("PATH", "")})
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"Git comparison failed with exit {result.returncode}")
    fields = result.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ValueError("invalid NUL-delimited Git output")
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, str]] = []
    cmp_id = metadata.get("comparison_id") or comparison_id(metadata["comparison_kind"], metadata["before_role"], metadata["after_role"], metadata["acquisition_profile_id"], metadata["representation_schema"], metadata["normalizer_version"])
    for status_raw, path_raw in zip(fields[::2], fields[1::2], strict=True):
        try:
            status = status_raw.decode("ascii")
            change_type = STATUSES[status]
        except (UnicodeDecodeError, KeyError) as exc:
            raise ValueError(f"unsupported Git status: {status_raw!r}") from exc
        payload_relative = _relative(path_raw, (before_root, after_root))
        if payload_relative == "component-manifest.json":
            continue
        relative = f"{metadata.get('path_prefix', '').rstrip('/')}/{payload_relative}".lstrip("/")
        key = (change_type, relative)
        if key in seen:
            raise ValueError(f"duplicate Git row: {key}")
        seen.add(key)
        before = before_root / payload_relative
        after = after_root / payload_relative
        before_fp, after_fp = _fingerprint(before), _fingerprint(after)
        if before_fp and before_fp == after_fp and before.is_file() == after.is_file():
            continue
        content = "sha256:" + sha256(canonical_json({"before": before_fp, "after": after_fp}))
        rows.append({
            "stable_diff_id": diff_id(cmp_id, relative, change_type, metadata.get("schema_version", "1")), "comparison_id": cmp_id,
            "source_generation": metadata["source_generation"], "before_role": metadata["before_role"],
            "after_role": metadata["after_role"], "change_type": change_type, "path": relative,
            "object_kind": "file", "object_name": Path(relative).name, "area": relative.partition("/")[0],
            "before_fingerprint": before_fp, "after_fingerprint": after_fp, "content_fingerprint": content,
        })
    return sorted(rows, key=lambda row: (row["comparison_id"], row["path"], row["change_type"]))


def _write_csv(path: Path, header: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=header, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
        stream.flush(); os.fsync(stream.fileno())


def validate_active(repo: Path, *, require_tracked_clean_state: bool = False) -> dict[str, Any]:
    pointer = json.loads((repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
    source = json.loads((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    generation = str(pointer.get("generation_id", "")); source_id = str(source.get("generation_id", ""))
    if pointer.get("source_generation_id") != source_id or not generation:
        raise ValueError("active diff generation is stale")
    root = confined(repo / "analysis/indexes/generations", generation)
    if require_tracked_clean_state:
        require_tracked_clean(repo, [root, repo / "research/active-diff-generation.json"])
    headers = {"diff-inventory.csv": INVENTORY_HEADER, "diff-id-map.csv": ID_MAP_HEADER, "target-coverage.csv": COVERAGE_HEADER}
    rows: dict[str, list[dict[str, str]]] = {}
    actual_hashes: dict[str, str] = {}
    for name, header in headers.items():
        path = root / name; actual_hashes[name] = sha256(path.read_bytes())
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != header: raise ValueError(f"invalid diff CSV schema: {name}")
            rows[name] = list(reader)
        if pointer.get("row_counts", {}).get(name) != len(rows[name]): raise ValueError(f"diff row count mismatch: {name}")
    if actual_hashes != pointer.get("files"):
        raise ValueError("active diff file hash mismatch")
    if sha256(canonical_json({"files": actual_hashes, "schema_version": "1", "source_generation_id": source_id})) != generation:
        raise ValueError("active diff generation ID mismatch")
    seen: set[str] = set(); customer: set[str] = set()
    routed = source.get("schema_version") == "2"
    routed_ids = {
        (kind, before, after): content_id("CMP-", {"after_role": after, "before_role": before, "comparison_kind": kind, "schema_version": "2", "source_comparison_epoch_fingerprint": source["source_comparison_epoch_fingerprint"]})
        for kind, before, after in COMPARISONS
    } if routed else {}
    for row in rows["diff-inventory.csv"]:
        if row["source_generation"] != source_id or row["before_role"] != "vendor_baseline" or row["after_role"] not in {"target_cf", "next_vendor"}:
            raise ValueError("mixed or invalid diff roles")
        kind = "customer-customization" if row["after_role"] == "target_cf" else "target-release"
        cmp_id = routed_ids[(kind, row["before_role"], row["after_role"])] if routed else comparison_id(kind, row["before_role"], row["after_role"], source["acquisition_profile_id"], source["representation_schema"], source["normalizer_version"])
        expected = diff_id(cmp_id, row["path"], row["change_type"], "2" if routed else "1")
        if row["comparison_id"] != cmp_id or row["stable_diff_id"] != expected or expected in seen:
            raise ValueError(f"invalid or duplicate stable DIF: {row['stable_diff_id']}")
        seen.add(expected)
        if row["after_role"] == "target_cf": customer.add(expected)
    active_map = [row for row in rows["diff-id-map.csv"] if row["active"] == "true"]
    if len({row["stable_diff_id"] for row in rows["diff-id-map.csv"]}) != len(rows["diff-id-map.csv"]) or {row["stable_diff_id"] for row in active_map} != seen or len(active_map) != len(seen):
        raise ValueError("diff ID map must contain exactly one active row per inventory DIF")
    if any(row["active"] not in {"true", "false"} or len(row["first_seen_generation"]) != 64 or len(row["last_seen_generation"]) != 64 for row in rows["diff-id-map.csv"]):
        raise ValueError("invalid diff ID history")
    coverage = [row["customer_diff_id"] for row in rows["target-coverage.csv"]]
    if set(coverage) != customer or len(coverage) != len(customer):
        raise ValueError("target coverage must contain exactly one row per customer DIF")
    return pointer


def build(repo: Path, source_pointer: dict[str, Any]) -> dict[str, Any]:
    if source_pointer.get("schema_version") == "2":
        from .sources import validate_active as validate_source
        if validate_source(repo, deep=False) != source_pointer:
            raise RuntimeError("stale or invalid source generation")
    with repository_lock(repo):
        current = __import__("json").loads((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
        if current != source_pointer:
            raise RuntimeError("stale source generation")
        return _build_locked(repo, source_pointer)


def _build_locked(repo: Path, source_pointer: dict[str, Any]) -> dict[str, Any]:
    if source_pointer.get("schema_version") == "2":
        return _build_routed(repo, source_pointer)
    if source_pointer.get("schema_version") == "1":
        raise ValueError("new diff builds require routed source schema version 2")
    if source_pointer.get("schema_version") is not None:
        raise ValueError("unsupported source schema version")
    source_id = source_pointer["generation_id"]
    source_root = repo / "sources/generations" / source_id
    profile = source_pointer["acquisition_profile_id"]
    representation = source_pointer["representation_schema"]
    normalizer = source_pointer["normalizer_version"]
    inventory: list[dict[str, str]] = []
    for kind, before, after in COMPARISONS:
        inventory.extend(compare(source_root / before, source_root / after, {"comparison_kind": kind, "before_role": before, "after_role": after, "acquisition_profile_id": profile, "representation_schema": representation, "normalizer_version": normalizer, "source_generation": source_id}))
    return _publish_inventory(repo, source_pointer, inventory)


def _publish_inventory(repo: Path, source_pointer: dict[str, Any], inventory: list[dict[str, str]]) -> dict[str, Any]:
    source_id = source_pointer["generation_id"]
    routed = source_pointer.get("schema_version") == "2"
    id_preimages: dict[str, bytes] = {}
    for row in inventory:
        preimage = canonical_json({"change_type": row["change_type"], "comparison_id": row["comparison_id"], "path": row["path"], "schema_version": "2" if routed else "1"})
        if row["stable_diff_id"] in id_preimages and id_preimages[row["stable_diff_id"]] != preimage:
            raise ValueError(f"truncated DIF hash collision: {row['stable_diff_id']}")
        id_preimages[row["stable_diff_id"]] = preimage
    by_payload: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in inventory:
        fp = row["after_fingerprint"] if row["change_type"] == "added" else row["before_fingerprint"]
        by_payload.setdefault((row["comparison_id"], fp), []).append(row)
    predecessors: dict[str, str] = {}; successors: dict[str, str] = {}
    for candidates in by_payload.values():
        deleted = [row for row in candidates if row["change_type"] == "deleted"]
        added = [row for row in candidates if row["change_type"] == "added"]
        if len(deleted) == len(added) == 1:
            predecessors[added[0]["stable_diff_id"]] = deleted[0]["stable_diff_id"]
            successors[deleted[0]["stable_diff_id"]] = added[0]["stable_diff_id"]
    previous: dict[str, dict[str, str]] = {}
    pointer_path = repo / "research/active-diff-generation.json"
    current_comparisons = {row["comparison_id"] for row in inventory}
    if not current_comparisons:
        if routed:
            epoch = source_pointer["source_comparison_epoch_fingerprint"]
            current_comparisons = {content_id("CMP-", {"after_role": after, "before_role": before, "comparison_kind": kind, "schema_version": "2", "source_comparison_epoch_fingerprint": epoch}) for kind, before, after in COMPARISONS}
        else:
            current_comparisons = {comparison_id(kind, before, after, source_pointer["acquisition_profile_id"], source_pointer["representation_schema"], source_pointer["normalizer_version"]) for kind, before, after in COMPARISONS}
    if pointer_path.is_file():
        old_pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        old_map = repo / "analysis/indexes/generations" / str(old_pointer.get("generation_id", "")) / "diff-id-map.csv"
        if old_map.is_file():
            with old_map.open(encoding="utf-8", newline="") as stream:
                previous = {row["stable_diff_id"]: row for row in csv.DictReader(stream) if row.get("comparison_id") in current_comparisons}
    id_map = [{"stable_diff_id": row["stable_diff_id"], "comparison_id": row["comparison_id"], "change_type": row["change_type"], "path": row["path"], "active": "true", "first_seen_generation": previous.get(row["stable_diff_id"], {}).get("first_seen_generation", source_id), "last_seen_generation": source_id, "predecessor_diff_id": predecessors.get(row["stable_diff_id"], ""), "successor_diff_id": successors.get(row["stable_diff_id"], ""), "content_fingerprint": row["content_fingerprint"], "notes": ""} for row in inventory]
    active_ids = {row["stable_diff_id"] for row in inventory}
    id_map.extend({**row, "active": "false"} for identifier, row in previous.items() if identifier not in active_ids)
    id_map.sort(key=lambda row: (row["comparison_id"], row["path"], row["change_type"], row["stable_diff_id"]))
    target_by_path = {row["path"]: row for row in inventory if row["after_role"] == "next_vendor"}
    coverage = []
    for row in inventory:
        if row["after_role"] != "target_cf":
            continue
        target = target_by_path.get(row["path"])
        if target and target["change_type"] == row["change_type"] and target["after_fingerprint"] == row["after_fingerprint"]:
            status = "covered_by_vendor"
        elif target is None:
            status = "still_required"
        else:
            status = "changed_in_target"
        evidence = {
            "baseline": row["before_fingerprint"],
            "customer": row["after_fingerprint"],
            "target": target["after_fingerprint"] if target else row["before_fingerprint"],
        }
        coverage.append({
            "customer_diff_id": row["stable_diff_id"],
            "source_generation": source_id,
            "target_diff_ids": target["stable_diff_id"] if target else "",
            "coverage_status": status,
            "evidence_ref": canonical_json(evidence).decode("utf-8"),
            "notes": "",
        })
    staging_parent = repo / "analysis/indexes/.staging"; staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=staging_parent) as temporary:
        staging = Path(temporary)
        _write_csv(staging / "diff-inventory.csv", INVENTORY_HEADER, inventory)
        _write_csv(staging / "diff-id-map.csv", ID_MAP_HEADER, id_map)
        _write_csv(staging / "target-coverage.csv", COVERAGE_HEADER, coverage)
        hashes = {name: sha256((staging / name).read_bytes()) for name in ("diff-inventory.csv", "diff-id-map.csv", "target-coverage.csv")}
        generation_id = sha256(canonical_json({"files": hashes, "schema_version": "1", "source_generation_id": source_id}))
        destination = repo / "analysis/indexes/generations" / generation_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            os.replace(staging, destination)
            parent_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        if any(sha256((destination / name).read_bytes()) != digest for name, digest in hashes.items()):
            raise RuntimeError("existing diff generation does not match its deterministic identity")
        pointer = {"schema_version": "1", "generation_id": generation_id, "source_generation_id": source_id, "files": hashes, "row_counts": {"diff-inventory.csv": len(inventory), "diff-id-map.csv": len(id_map), "target-coverage.csv": len(coverage)}}
        atomic_json(repo / "research/active-diff-generation.json", pointer)
        return pointer


def _build_routed(repo: Path, source_pointer: dict[str, Any]) -> dict[str, Any]:
    source_id = source_pointer["generation_id"]
    source_root = repo / "sources/generations" / source_id
    manifest = json.loads((source_root / source_pointer["routing_manifest_path"]).read_text(encoding="utf-8"))
    epoch = source_pointer["source_comparison_epoch_fingerprint"]
    components = {item["component_id"]: item for item in source_pointer["components"]}
    inventory = []
    empty_parent = repo / "analysis/indexes/.staging"
    empty_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=empty_parent) as empty:
        empty_root = Path(empty)
        for kind, before, after in COMPARISONS:
            cmp_id = content_id("CMP-", {"after_role": after, "before_role": before, "comparison_kind": kind, "schema_version": "2", "source_comparison_epoch_fingerprint": epoch})
            for group in manifest["groups"]:
                by_role = {member["role"]: components[member["component_id"]] for member in group["members"]}
                prefix = "configuration" if group["routing_group_id"] == "configuration" else group["routing_group_id"].replace("extension:", "extensions/", 1).replace("external:", "external/", 1)
                before_root = source_root / by_role[before]["path"] if before in by_role else empty_root
                after_root = source_root / by_role[after]["path"] if after in by_role else empty_root
                inventory.extend(compare(before_root, after_root, {
                    "comparison_kind": kind,
                    "before_role": before,
                    "after_role": after,
                    "acquisition_profile_id": source_pointer["acquisition_profile_id"],
                    "representation_schema": group["representation_schema"],
                    "normalizer_version": source_pointer["normalizer_version"],
                    "source_generation": source_id,
                    "comparison_id": cmp_id,
                    "path_prefix": prefix,
                    "schema_version": "2",
                }))
    return _publish_inventory(repo, source_pointer, inventory)
