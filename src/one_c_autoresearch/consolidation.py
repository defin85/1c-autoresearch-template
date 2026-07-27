from __future__ import annotations

import json
import os
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Iterable

from .contracts import atomic_json, canonical_json, content_id, repository_lock, sha256
from .dif_classifications import load_active as load_classifications


POINTER = "research/active-consolidation-generation.json"
MRQ_FILES = ("mrq.jsonl", "dispositions.jsonl", "evidence.jsonl", "lineage.jsonl")


def fingerprint(value: Any) -> str:
    return "sha256:" + sha256(canonical_json(value))


def sentinel() -> dict[str, Any]:
    return {
        "schema_version": "2",
        "state": "unpublished",
        "mrq_generation_id": None,
        "source_fingerprint": None,
        "diff_fingerprint": None,
        "classification_fingerprint": None,
        "plan_fingerprint": None,
        "consolidation_approval_fingerprint": None,
        "transaction_id": None,
        "batch_generation_id": None,
        "batch_input_fingerprint": None,
        "decision_generation_id": None,
        "decision_input_fingerprint": None,
    }


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"missing generation file: {path.name}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if path.read_bytes() != b"".join(canonical_json(row) + b"\n" for row in rows):
        raise ValueError(f"non-canonical generation file: {path.name}")
    if rows != sorted(rows, key=canonical_json):
        raise ValueError(f"unsorted generation file: {path.name}")
    return rows


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("wb") as stream:
        for row in sorted(rows, key=canonical_json):
            stream.write(canonical_json(row) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _validate_pointer(pointer: dict[str, Any]) -> None:
    if set(pointer) != set(sentinel()) or pointer["schema_version"] != "2" or pointer["state"] not in {"unpublished", "active"}:
        raise ValueError("invalid consolidation pointer")
    active = pointer["state"] == "active"
    required = (
        "mrq_generation_id", "source_fingerprint", "diff_fingerprint",
        "classification_fingerprint", "plan_fingerprint",
        "consolidation_approval_fingerprint", "transaction_id",
    )
    if active != all(bool(pointer[key]) for key in required):
        raise ValueError("incomplete consolidation pointer")
    for generation_key, input_key in (
        ("batch_generation_id", "batch_input_fingerprint"),
        ("decision_generation_id", "decision_input_fingerprint"),
    ):
        if bool(pointer[generation_key]) != bool(pointer[input_key]):
            raise ValueError("incomplete downstream consolidation binding")


def validate_generation(
    repo: Path,
    generation_id: str,
    expected_bindings: dict[str, Any],
) -> dict[str, Any]:
    root = repo / "analysis/migration-requirements/generations" / generation_id
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    rows = {name: _jsonl(root / name) for name in MRQ_FILES}
    hashes = {name: sha256((root / name).read_bytes()) for name in MRQ_FILES}
    bindings = manifest.get("input_bindings", {})
    if any(bindings.get(key) != value for key, value in expected_bindings.items()):
        raise ValueError("stale MRQ generation bindings")
    preimage = {
        "schema_version": "3",
        "kind": "mrq",
        "files": hashes,
        "row_counts": {name: len(rows[name]) for name in MRQ_FILES},
        "input_bindings": bindings,
    }
    if manifest != {**preimage, "generation_id": generation_id} or generation_id != sha256(canonical_json(preimage)):
        raise ValueError("invalid MRQ generation manifest")
    mrq_ids = {row["mrq_id"] for row in rows["mrq.jsonl"]}
    legacy_ids = set(bindings.get("legacy_identity_ids", []))
    if len(mrq_ids) != len(rows["mrq.jsonl"]) or any(
        row.get("schema_version") != "3"
        or (
            row["mrq_id"] != content_id("MRQ-", {"schema_version": "3", "semantic_key": row["semantic_key"]})
            and row["mrq_id"] not in legacy_ids
        )
        for row in rows["mrq.jsonl"]
    ):
        raise ValueError("invalid MRQ v3 identity")
    if any(row.get("mrq_id") not in mrq_ids for name in ("dispositions.jsonl", "evidence.jsonl") for row in rows[name]):
        raise ValueError("dangling MRQ reference")
    primary = [row["stable_diff_id"] for row in rows["dispositions.jsonl"] if row.get("primary")]
    if len(primary) != len(set(primary)):
        raise ValueError("duplicate primary DIF disposition")
    prior_ids = set(bindings.get("prior_ids", []))
    edges: dict[str, set[str]] = {}
    for row in rows["lineage.jsonl"]:
        kind, sources, targets = row.get("kind"), row.get("source_ids", []), row.get("target_ids", [])
        if kind not in {"merge", "split", "supersede"} or not set(sources) <= prior_ids or not set(targets) <= mrq_ids:
            raise ValueError("invalid MRQ lineage")
        if kind == "merge" and (len(sources) < 2 or len(targets) != 1):
            raise ValueError("invalid MRQ merge lineage")
        if kind == "split" and (len(sources) != 1 or len(targets) < 2):
            raise ValueError("invalid MRQ split lineage")
        if kind == "supersede" and (not sources or targets):
            raise ValueError("invalid MRQ supersede lineage")
        for source in sources:
            edges.setdefault(source, set()).update(targets)
    for start in edges:
        pending = [(start, frozenset())]
        while pending:
            current, ancestors = pending.pop()
            if current in ancestors:
                raise ValueError("cyclic MRQ lineage")
            pending.extend((target, ancestors | {current}) for target in edges.get(current, ()))
    return {"manifest": manifest, **rows}


def _publish_generation(repo: Path, rows: dict[str, list[dict[str, Any]]], input_bindings: dict[str, Any]) -> str:
    if set(rows) != set(MRQ_FILES):
        raise ValueError("incomplete MRQ generation")
    parent = repo / "analysis/migration-requirements"
    staging = parent / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=staging) as temporary:
        root = Path(temporary)
        for name in MRQ_FILES:
            _write_jsonl(root / name, rows[name])
        preimage = {
            "schema_version": "3",
            "kind": "mrq",
            "files": {name: sha256((root / name).read_bytes()) for name in MRQ_FILES},
            "row_counts": {name: len(rows[name]) for name in MRQ_FILES},
            "input_bindings": input_bindings,
        }
        generation_id = sha256(canonical_json(preimage))
        atomic_json(root / "manifest.json", {**preimage, "generation_id": generation_id})
        destination = parent / "generations" / generation_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            os.replace(root, destination)
    validate_generation(repo, generation_id, input_bindings)
    return generation_id


def load_active(repo: Path, *, allow_legacy: bool = False) -> dict[str, Any]:
    del allow_legacy
    path = repo / POINTER
    pointer = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else sentinel()
    _validate_pointer(pointer)
    if pointer["state"] != "active":
        return {"pointer": pointer, "mrq": {}}
    classifications = load_classifications(repo)
    source = json.loads((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    diff = json.loads((repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
    bindings = {
        "source_fingerprint": fingerprint(source),
        "diff_fingerprint": fingerprint(diff),
        "classification_fingerprint": fingerprint(classifications["pointer"]),
    }
    if any(pointer[key] != value for key, value in bindings.items()):
        raise ValueError("active consolidation bindings are stale")
    mrq = validate_generation(repo, pointer["mrq_generation_id"], bindings)
    if pointer.get("decision_generation_id"):
        from .decision_generations import consolidation_input_fingerprint, validate_generation as validate_decisions
        validate_decisions(repo, pointer["decision_generation_id"], allowed_mrq_ids={row["mrq_id"] for row in mrq["mrq.jsonl"]})
        if pointer["decision_input_fingerprint"] != consolidation_input_fingerprint(pointer):
            raise ValueError("stale decision generation binding")
    return {"pointer": pointer, "mrq": mrq}


def input_snapshot(repo: Path) -> dict[str, Any]:
    classification = load_classifications(repo)
    current = load_active(repo)
    pointer = classification["pointer"]
    return {
        "source_generation_id": pointer["source_generation_id"],
        "diff_generation_id": pointer["diff_generation_id"],
        "source_fingerprint": pointer["source_fingerprint"],
        "diff_fingerprint": pointer["diff_fingerprint"],
        "classification_fingerprint": fingerprint(pointer),
        "classifications": classification["rows"],
        "prior_consolidation": current["pointer"],
        "mrq": current["mrq"],
    }


def normalized_records(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *[{"record_id": row["stable_diff_id"], "record_type": "dif", **row} for row in snapshot["classifications"]],
        *[
            {
                "record_id": row["mrq_id"],
                "record_type": "mrq",
                "mrq_id": row["mrq_id"],
                "semantic_key": row["semantic_key"],
                "stable_diff_ids": sorted(
                    item["stable_diff_id"]
                    for item in snapshot.get("mrq", {}).get("dispositions.jsonl", [])
                    if item.get("mrq_id") == row["mrq_id"] and item.get("primary")
                ),
            }
            for row in snapshot.get("mrq", {}).get("mrq.jsonl", [])
        ],
    ]


def partition_manifest(records: list[dict[str, Any]], input_context_tokens: int, *, estimator_version: str = "utf8-v1") -> dict[str, Any]:
    if not isinstance(input_context_tokens, int) or input_context_tokens <= 4096:
        raise ValueError("consolidation.context_capacity")
    half = ((input_context_tokens - 4096) * 2) // 2
    partitions: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for record in sorted(records, key=canonical_json):
        record_size = len(canonical_json(record))
        if record_size > half:
            raise ValueError("consolidation.context_capacity")
        if current and size + record_size > half:
            partitions.append(current)
            current, size = [], 0
        current.append(record)
        size += record_size
    if current or not partitions:
        partitions.append(current)
    pairs = [(left, right) for left in range(len(partitions)) for right in range(left, len(partitions))]
    return {
        "schema_version": "1",
        "estimator_version": estimator_version,
        "input_context_tokens": input_context_tokens,
        "partitions": [{
            "index": index,
            "fingerprint": fingerprint(rows),
            "count": len(rows),
            "record_ids": [row.get("record_id", row["stable_diff_id"]) for row in rows],
            "stable_diff_ids": [
                row["stable_diff_id"] for row in rows
                if row.get("record_type", "dif") == "dif"
            ],
        } for index, rows in enumerate(partitions)],
        "pairs": [{"left": left, "right": right} for left, right in pairs],
        "planned_invocation_count": len(partitions) + len(pairs),
    }


def ensure_context_payload(payload: Any, input_context_tokens: int) -> None:
    if not isinstance(input_context_tokens, int) or input_context_tokens <= 4096 or len(canonical_json(payload)) > (input_context_tokens - 4096) * 2:
        raise ValueError("consolidation.context_capacity")


def validate_plan(plan: dict[str, Any]) -> None:
    required = {
        "schema_version", "bindings", "mrq", "approved_noise", "outcomes",
        "lineage", "coverage_bitmap", "partition_manifest", "decision_carryover",
    }
    if set(plan) != required or plan["schema_version"] != "2":
        raise ValueError("invalid consolidation plan")
    classifications = {row["stable_diff_id"] for row in plan["bindings"]["classifications"]}
    primary = [row["stable_diff_id"] for row in plan["mrq"]["dispositions.jsonl"] if row.get("primary")]
    noise = [row["stable_diff_id"] for row in plan["approved_noise"]]
    if set(primary) & set(noise) or set(primary) | set(noise) != classifications or len(primary) != len(set(primary)):
        raise ValueError("consolidation plan has incomplete or overlapping DIF coverage")
    mrq_ids = {row["mrq_id"] for row in plan["mrq"]["mrq.jsonl"]}
    if any(row.get("mrq_id") not in mrq_ids for name in ("dispositions.jsonl", "evidence.jsonl") for row in plan["mrq"][name]):
        raise ValueError("consolidation plan has dangling MRQ references")
    if {tuple(row) for row in plan["coverage_bitmap"]} != {
        (row["left"], row["right"]) for row in plan["partition_manifest"]["pairs"]
    }:
        raise ValueError("consolidation comparison bitmap is incomplete")
    buckets = plan["outcomes"]
    seen: set[str] = set()
    for name in ("retained", "new", "merged", "split", "superseded", "revalidated"):
        values = set(buckets.get(name, []))
        if seen & values:
            raise ValueError("overlapping MRQ outcomes")
        seen |= values
    prior_ids = set(plan["bindings"]["prior_mrq_ids"])
    source_outcomes = set().union(*(set(buckets[name]) for name in ("retained", "revalidated", "merged", "split", "superseded")))
    targets = {target for row in plan["lineage"] for target in row.get("target_ids", [])}
    current_ids = {row["mrq_id"] for row in plan["mrq"]["mrq.jsonl"]}
    if source_outcomes != prior_ids or set(buckets["retained"]) | set(buckets["revalidated"]) | set(buckets["new"]) | targets != current_ids:
        raise ValueError("non-exhaustive MRQ outcomes")


def plan_from_groups(
    snapshot: dict[str, Any],
    groups: list[dict[str, Any]],
    approved_noise: list[dict[str, Any]],
    partition: dict[str, Any],
) -> dict[str, Any]:
    classified = {row["stable_diff_id"]: row for row in snapshot["classifications"]}
    meaning = {key for key, row in classified.items() if row["classification"] == "meaning"}
    noise = {key for key, row in classified.items() if row["classification"] == "noise_candidate"}
    if {str(row.get("stable_diff_id", "")) for row in approved_noise} != noise:
        raise ValueError("every noise candidate requires explicit consolidation approval")
    prior = snapshot.get("mrq", {}).get("mrq.jsonl", [])
    prior_by_semantic = {row.get("semantic_key"): row for row in prior}
    old_ids = {row["mrq_id"] for row in prior}
    mrqs: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    assigned: set[str] = set()
    merged_sources: set[str] = set()
    merge_targets: set[str] = set()
    split_targets: dict[str, list[str]] = {}
    for group in sorted(groups, key=lambda row: str(row.get("semantic_key", ""))):
        semantic_key = str(group.get("semantic_key", "")).strip()
        members = sorted(set(group.get("stable_diff_ids", [])))
        if not semantic_key or not members or any(member not in meaning or member in assigned for member in members):
            raise ValueError("invalid or overlapping consolidation group")
        mrq_id = content_id("MRQ-", {"schema_version": "3", "semantic_key": semantic_key})
        prior_row = prior_by_semantic.get(semantic_key)
        mrqs.append(
            prior_row if prior_row and prior_row.get("mrq_id") == mrq_id else {
                "schema_version": "3",
                "mrq_id": mrq_id,
                "semantic_key": semantic_key,
                "title": str(group.get("title", semantic_key)),
                "state": "draft",
                "business_meaning": str(group.get("business_meaning", "")),
                "scope": str(group.get("scope", "")),
                "rationale": str(group.get("rationale", "")),
            }
        )
        for member in members:
            dispositions.append({"schema_version": "3", "mrq_id": mrq_id, "stable_diff_id": member, "primary": True})
            for index, evidence in enumerate(classified[member]["evidence"]):
                evidence_rows.append({
                    "schema_version": "3",
                    "evidence_id": content_id("EVD-", {"mrq": mrq_id, "dif": member, "index": index, "evidence": evidence}),
                    "mrq_id": mrq_id,
                    "stable_diff_id": member,
                    "payload": evidence,
                })
        sources = sorted(set(group.get("source_mrq_ids", [])))
        if sources:
            if len(sources) < 2:
                raise ValueError("MRQ merge requires at least two sources")
            merged_sources.update(sources)
            merge_targets.add(mrq_id)
            lineage.append({"domain": "mrq", "kind": "merge", "source_ids": sources, "target_ids": [mrq_id]})
        split_source = str(group.get("split_source_mrq_id", "")).strip()
        if split_source:
            split_targets.setdefault(split_source, []).append(mrq_id)
        assigned.update(members)
    if assigned != meaning:
        raise ValueError("consolidation groups do not cover every meaning DIF")
    new_ids = {row["mrq_id"] for row in mrqs}
    for source, targets in split_targets.items():
        if source not in old_ids or len(set(targets)) < 2:
            raise ValueError("MRQ split requires one active source and at least two targets")
        lineage.append({"domain": "mrq", "kind": "split", "source_ids": [source], "target_ids": sorted(set(targets))})
    superseded = (old_ids - new_ids) - merged_sources - set(split_targets)
    lineage.extend({"domain": "mrq", "kind": "supersede", "source_ids": [identifier], "target_ids": []} for identifier in sorted(superseded))
    plan = {
        "schema_version": "2",
        "bindings": {
            "source_fingerprint": snapshot["source_fingerprint"],
            "diff_fingerprint": snapshot["diff_fingerprint"],
            "classification_fingerprint": snapshot["classification_fingerprint"],
            "classifications": snapshot["classifications"],
            "prior_mrq_fingerprint": fingerprint(snapshot.get("mrq", {})),
            "prior_mrq_ids": sorted(old_ids),
            "legacy_mrq_ids": [],
        },
        "mrq": {
            "mrq.jsonl": mrqs,
            "dispositions.jsonl": dispositions,
            "evidence.jsonl": evidence_rows,
            "lineage.jsonl": [{
                "schema_version": "1",
                "kind": row["kind"],
                "source_ids": row["source_ids"],
                "target_ids": row["target_ids"],
                "rationale": "approved whole-inventory consolidation",
                "evidence": [{"classification_fingerprint": snapshot["classification_fingerprint"]}],
                "actor": "consolidation-plan",
                "source_generation_id": snapshot.get("source_generation_id", snapshot["source_fingerprint"]),
                "diff_generation_id": snapshot.get("diff_generation_id", snapshot["diff_fingerprint"]),
            } for row in lineage],
        },
        "approved_noise": approved_noise,
        "outcomes": {
            "retained": sorted((old_ids & new_ids) - merged_sources),
            "new": sorted((new_ids - old_ids) - merge_targets - {target for targets in split_targets.values() for target in targets}),
            "merged": sorted(merged_sources),
            "split": sorted(split_targets),
            "superseded": sorted(superseded),
            "revalidated": [],
        },
        "lineage": lineage,
        "decision_carryover": {"carried": [], "stale": []},
        "coverage_bitmap": [[row["left"], row["right"]] for row in partition["pairs"]],
        "partition_manifest": partition,
    }
    validate_plan(plan)
    return plan


def store_plan(root: Path, plan: dict[str, Any]) -> tuple[str, Path]:
    validate_plan(plan)
    plan_fingerprint = fingerprint(plan)
    path = root / "consolidation-plans" / f"{plan_fingerprint.removeprefix('sha256:')}.json"
    if path.is_file() and fingerprint(json.loads(path.read_text(encoding="utf-8"))) != plan_fingerprint:
        raise ValueError("consolidation.plan_storage")
    atomic_json(path, plan)
    return plan_fingerprint, path


def approve_plan(repo: Path, plan: dict[str, Any], expected_pointer: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
    validate_plan(plan)
    plan_fingerprint = fingerprint(plan)
    fields = {
        "schema_version", "kind", "actor", "rationale", "evidence",
        "source_fingerprint", "diff_fingerprint", "classification_fingerprint",
        "prior_mrq_fingerprint", "plan_fingerprint",
    }
    if (
        set(approval) != fields
        or approval["schema_version"] != "2"
        or approval["kind"] != "consolidation"
        or not str(approval["actor"]).strip()
        or not str(approval["rationale"]).strip()
        or approval["plan_fingerprint"] != plan_fingerprint
        or any(approval[key] != plan["bindings"][key] for key in (
            "source_fingerprint", "diff_fingerprint", "classification_fingerprint", "prior_mrq_fingerprint"
        ))
    ):
        raise ValueError("invalid or stale consolidation approval")
    with repository_lock(repo):
        path = repo / POINTER
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else sentinel()
        _validate_pointer(current)
        if current.get("plan_fingerprint") == plan_fingerprint:
            return current
        if current != expected_pointer:
            raise RuntimeError("stale consolidation inputs")
        snapshot = input_snapshot(repo)
        if any(plan["bindings"][key] != snapshot[key] for key in ("source_fingerprint", "diff_fingerprint", "classification_fingerprint")):
            raise RuntimeError("stale consolidation inputs")
        if plan["bindings"]["prior_mrq_fingerprint"] != fingerprint(snapshot.get("mrq", {})):
            raise RuntimeError("stale consolidation graph inputs")
        bindings = {
            key: snapshot[key]
            for key in ("source_fingerprint", "diff_fingerprint", "classification_fingerprint")
        }
        generation_id = _publish_generation(repo, plan["mrq"], {
            **bindings,
            "prior_generation_id": current.get("mrq_generation_id"),
            "prior_ids": plan["bindings"]["prior_mrq_ids"],
            "legacy_identity_ids": [],
        })
        changed = generation_id != current.get("mrq_generation_id")
        pointer = {
            **sentinel(),
            "state": "active",
            "mrq_generation_id": generation_id,
            **bindings,
            "plan_fingerprint": plan_fingerprint,
            "consolidation_approval_fingerprint": fingerprint(approval),
            "transaction_id": sha256(canonical_json({"mrq": generation_id, "plan": plan_fingerprint})),
            "batch_generation_id": None if changed else current.get("batch_generation_id"),
            "batch_input_fingerprint": None if changed else current.get("batch_input_fingerprint"),
            "decision_generation_id": None if changed else current.get("decision_generation_id"),
            "decision_input_fingerprint": None if changed else current.get("decision_input_fingerprint"),
        }
        atomic_json(path, pointer)
        return pointer


def replace_downstream_binding(
    repo: Path,
    kind: str,
    generation_id: str,
    input_fingerprint: str,
    *,
    expected_transaction_id: str,
    expected_generation_id: str | None = None,
    already_locked: bool = False,
) -> dict[str, Any]:
    if kind not in {"batch", "decision"}:
        raise ValueError("unknown downstream binding")
    with (nullcontext() if already_locked else repository_lock(repo)):
        current = load_active(repo)["pointer"]
        if current["transaction_id"] != expected_transaction_id or current.get(f"{kind}_generation_id") != expected_generation_id:
            raise RuntimeError(f"stale {kind} generation")
        updated = {
            **current,
            f"{kind}_generation_id": generation_id,
            f"{kind}_input_fingerprint": input_fingerprint,
        }
        atomic_json(repo / POINTER, updated)
        return updated
