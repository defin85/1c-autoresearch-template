from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from contextlib import nullcontext
from pathlib import Path
from typing import TypedDict

from .contracts import JsonValue, atomic_json, canonical_json, content_id, decision_consolidation_fingerprint, json_object, parse_json, parse_json_object, repository_lock, sha256, validate_decision_generation
POINTER = "research/active-consolidation-generation.json"
MRQ_FILES = ("mrq.jsonl", "dispositions.jsonl", "evidence.jsonl", "lineage.jsonl")
JsonObject = dict[str, JsonValue]


def _value(value: object) -> JsonValue:
    return parse_json(canonical_json(value).decode("utf-8"))


def _object(value: object) -> JsonObject:
    return json_object(_value(value))


def _objects(value: object) -> list[JsonObject]:
    normalized = _value(value)
    if not isinstance(normalized, list) or not all(isinstance(item, dict) for item in normalized):
        raise ValueError("expected object array")
    return [item for item in normalized if isinstance(item, dict)]


def _strings(value: object) -> list[str]:
    normalized = _value(value)
    if not isinstance(normalized, list) or not all(isinstance(item, str) for item in normalized):
        raise ValueError("expected string array")
    return [item for item in normalized if isinstance(item, str)]


def _integers(value: object) -> list[int]:
    normalized = _value(value)
    if not isinstance(normalized, list) or not all(isinstance(item, int) and not isinstance(item, bool) for item in normalized):
        raise ValueError("expected integer array")
    return [item for item in normalized if isinstance(item, int) and not isinstance(item, bool)]


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected integer")
    return value


class PartitionManifest(TypedDict):
    schema_version: str
    estimator_version: str
    input_context_tokens: int
    partitions: list[JsonObject]
    pairs: list[dict[str, int]]
    planned_invocation_count: int


def fingerprint(value: object) -> str:
    return "sha256:" + sha256(canonical_json(value))


def sentinel() -> JsonObject:
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


def _jsonl(path: Path) -> list[JsonObject]:
    if not path.is_file():
        raise ValueError(f"missing generation file: {path.name}")
    rows = [parse_json_object(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if path.read_bytes() != b"".join(canonical_json(row) + b"\n" for row in rows):
        raise ValueError(f"non-canonical generation file: {path.name}")
    if rows != sorted(rows, key=canonical_json):
        raise ValueError(f"unsorted generation file: {path.name}")
    return rows


def _write_jsonl(path: Path, rows: Iterable[JsonObject]) -> None:
    with path.open("wb") as stream:
        for row in sorted(rows, key=canonical_json):
            _ = stream.write(canonical_json(row) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def validate_pointer(pointer: JsonObject) -> None:
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
    expected_bindings: JsonObject,
) -> JsonObject:
    root = repo / "analysis/migration-requirements/generations" / generation_id
    manifest = parse_json_object((root / "manifest.json").read_text(encoding="utf-8"))
    rows = {name: _jsonl(root / name) for name in MRQ_FILES}
    hashes = {name: sha256((root / name).read_bytes()) for name in MRQ_FILES}
    bindings = _object(manifest.get("input_bindings", {}))
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
    mrq_ids = {_string(row["mrq_id"]) for row in rows["mrq.jsonl"]}
    legacy_ids = set(_strings(bindings.get("legacy_identity_ids", [])))
    if len(mrq_ids) != len(rows["mrq.jsonl"]) or any(
        row.get("schema_version") != "3"
        or (
            row["mrq_id"] != content_id("MRQ-", {"schema_version": "3", "semantic_key": _string(row["semantic_key"])})
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
    prior_ids = set(_strings(bindings.get("prior_ids", [])))
    edges: dict[str, set[str]] = {}
    for row in rows["lineage.jsonl"]:
        kind = row.get("kind")
        sources = _strings(row.get("source_ids", []))
        targets = _strings(row.get("target_ids", []))
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
        pending: list[tuple[str, frozenset[str]]] = [(start, frozenset())]
        while pending:
            current, ancestors = pending.pop()
            if current in ancestors:
                raise ValueError("cyclic MRQ lineage")
            pending.extend((target, ancestors | {current}) for target in edges.get(current, ()))
    return {"manifest": manifest, **rows}


def _publish_generation(repo: Path, rows: dict[str, list[JsonObject]], input_bindings: JsonObject) -> str:
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
    _ = validate_generation(repo, generation_id, input_bindings)
    return generation_id


def load_active(repo: Path, *, allow_legacy: bool = False) -> JsonObject:
    from .dif_classifications import load_active as load_classifications

    del allow_legacy
    path = repo / POINTER
    pointer = parse_json_object(path.read_text(encoding="utf-8")) if path.is_file() else sentinel()
    validate_pointer(pointer)
    if pointer["state"] != "active":
        return {"pointer": pointer, "mrq": {}}
    classifications = load_classifications(repo)
    source = parse_json_object((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    diff = parse_json_object((repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
    bindings = {
        "source_fingerprint": fingerprint(source),
        "diff_fingerprint": fingerprint(diff),
        "classification_fingerprint": fingerprint(classifications["pointer"]),
    }
    if any(pointer[key] != value for key, value in bindings.items()):
        raise ValueError("active consolidation bindings are stale")
    mrq = validate_generation(repo, _string(pointer["mrq_generation_id"]), _object(bindings))
    if pointer.get("decision_generation_id"):
        validate_decision_generation(repo, _string(pointer["decision_generation_id"]), {_string(row["mrq_id"]) for row in _objects(mrq["mrq.jsonl"])})
        input_fingerprint = decision_consolidation_fingerprint(pointer)
        if pointer["decision_input_fingerprint"] != input_fingerprint:
            raise ValueError("stale decision generation binding")
    return {"pointer": pointer, "mrq": mrq}


def input_snapshot(repo: Path) -> JsonObject:
    from .dif_classifications import load_active as load_classifications

    classification = load_classifications(repo)
    current = load_active(repo)
    pointer = classification["pointer"]
    from .component_groups import derive
    return _object({
        "source_generation_id": pointer["source_generation_id"],
        "diff_generation_id": pointer["diff_generation_id"],
        "source_fingerprint": pointer["source_fingerprint"],
        "diff_fingerprint": pointer["diff_fingerprint"],
        "classification_fingerprint": fingerprint(pointer),
        "classifications": classification["rows"],
        "prior_consolidation": current["pointer"],
        "mrq": current["mrq"],
        "component_groups": derive(repo),
    })


def normalized_records(snapshot: JsonObject) -> list[JsonObject]:
    component_groups = _objects(snapshot.get("component_groups", []))
    classifications = _objects(snapshot["classifications"])
    mrq = _object(snapshot.get("mrq", {}))
    dispositions = _objects(mrq.get("dispositions.jsonl", []))
    membership = {
        identifier: _object({
            "component_kind": group["component_kind"],
            "component_key": group["component_key"],
            "component_direction": group["direction"],
        })
        for group in component_groups
        for identifier in _strings(group["stable_diff_ids"])
    }
    records: list[JsonObject] = [
        _object({"record_id": row["stable_diff_id"], "record_type": "dif", **row, **membership.get(_string(row["stable_diff_id"]), {})}) for row in classifications
    ]
    records.extend(_object({
            "record_id": f"component:{group['component_kind']}:{group['component_key']}",
            "record_type": "component_summary",
            "component_kind": group["component_kind"],
            "component_key": group["component_key"],
            "component_direction": group["direction"],
            "member_count": len(_strings(group["stable_diff_ids"])),
            "member_fingerprint": fingerprint(group["stable_diff_ids"]),
            "page_count": len(_strings(group["stable_diff_ids"])),
        }) for group in component_groups)
    records.extend(
            _object({
                "record_id": f"component-page:{group['component_kind']}:{group['component_key']}:{index}",
                "record_type": "component_page",
                "component_kind": group["component_kind"],
                "component_key": group["component_key"],
                "component_direction": group["direction"],
                "page_index": index,
                "page_count": len(_strings(group["stable_diff_ids"])),
                "stable_diff_ids": [identifier],
                "evidence": _object(group["evidence"]).get(identifier, []),
                "target_coverage": _object(group["target_coverage"]).get(identifier),
            })
            for group in component_groups
            for index, identifier in enumerate(_strings(group["stable_diff_ids"]))
        )
    records.extend(
            _object({
                "record_id": row["mrq_id"],
                "record_type": "mrq",
                "mrq_id": row["mrq_id"],
                "semantic_key": row["semantic_key"],
                "stable_diff_ids": sorted(
                    _string(item["stable_diff_id"])
                    for item in dispositions
                    if item.get("mrq_id") == row["mrq_id"] and item.get("primary")
                ),
            })
            for row in _objects(mrq.get("mrq.jsonl", []))
        )
    return records


def partition_manifest(records: list[JsonObject], input_context_tokens: object, *, estimator_version: str = "utf8-v1") -> PartitionManifest:
    if not isinstance(input_context_tokens, int) or input_context_tokens <= 4096:
        raise ValueError("consolidation.context_capacity")
    half = ((input_context_tokens - 4096) * 2) // 2
    partitions: list[list[JsonObject]] = []
    current: list[JsonObject] = []
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
            "record_ids": [row["record_id"] if "record_id" in row else row["stable_diff_id"] for row in rows],
            "stable_diff_ids": [
                row["stable_diff_id"] for row in rows
                if row.get("record_type", "dif") == "dif"
            ],
        } for index, rows in enumerate(partitions)],
        "pairs": [{"left": left, "right": right} for left, right in pairs],
        "planned_invocation_count": len(partitions) + len(pairs),
    }


def ensure_context_payload(payload: object, input_context_tokens: object) -> None:
    if not isinstance(input_context_tokens, int) or input_context_tokens <= 4096 or len(canonical_json(payload)) > (input_context_tokens - 4096) * 2:
        raise ValueError("consolidation.context_capacity")


def validate_plan(plan: JsonObject) -> None:
    required = {
        "schema_version", "bindings", "mrq", "approved_noise", "outcomes",
        "lineage", "coverage_bitmap", "partition_manifest", "decision_carryover",
    }
    if set(plan) != required or plan["schema_version"] != "2":
        raise ValueError("invalid consolidation plan")
    bindings = _object(plan["bindings"])
    mrq = _object(plan["mrq"])
    outcomes = _object(plan["outcomes"])
    partition = _object(plan["partition_manifest"])
    classifications = {_string(row["stable_diff_id"]) for row in _objects(bindings["classifications"])}
    primary = [_string(row["stable_diff_id"]) for row in _objects(mrq["dispositions.jsonl"]) if row.get("primary")]
    noise = [_string(row["stable_diff_id"]) for row in _objects(plan["approved_noise"])]
    if set(primary) & set(noise) or set(primary) | set(noise) != classifications or len(primary) != len(set(primary)):
        raise ValueError("consolidation plan has incomplete or overlapping DIF coverage")
    mrq_ids = {_string(row["mrq_id"]) for row in _objects(mrq["mrq.jsonl"])}
    if any(row.get("mrq_id") not in mrq_ids for name in ("dispositions.jsonl", "evidence.jsonl") for row in _objects(mrq[name])):
        raise ValueError("consolidation plan has dangling MRQ references")
    raw_coverage = _value(plan["coverage_bitmap"])
    if not isinstance(raw_coverage, list):
        raise ValueError("consolidation comparison bitmap is incomplete")
    coverage = {tuple(_integers(row)) for row in raw_coverage}
    pairs = {
        (_integer(row["left"]), _integer(row["right"])) for row in _objects(partition["pairs"])
    }
    if coverage != pairs:
        raise ValueError("consolidation comparison bitmap is incomplete")
    seen: set[str] = set()
    for name in ("retained", "new", "merged", "split", "superseded", "revalidated"):
        values = set(_strings(outcomes.get(name, [])))
        if seen & values:
            raise ValueError("overlapping MRQ outcomes")
        seen |= values
    prior_ids = set(_strings(bindings["prior_mrq_ids"]))
    source_outcomes: set[str] = set()
    for name in ("retained", "revalidated", "merged", "split", "superseded"):
        source_outcomes.update(_strings(outcomes[name]))
    targets = {target for row in _objects(plan["lineage"]) for target in _strings(row.get("target_ids", []))}
    current_ids = {_string(row["mrq_id"]) for row in _objects(mrq["mrq.jsonl"])}
    if source_outcomes != prior_ids or set(_strings(outcomes["retained"])) | set(_strings(outcomes["revalidated"])) | set(_strings(outcomes["new"])) | targets != current_ids:
        raise ValueError("non-exhaustive MRQ outcomes")


def plan_from_groups(
    snapshot: JsonObject,
    groups: list[JsonObject],
    approved_noise: list[JsonObject],
    partition: JsonObject,
) -> JsonObject:
    classifications = _objects(snapshot["classifications"])
    prior_mrq = _object(snapshot.get("mrq", {}))
    component_groups = _objects(snapshot.get("component_groups", []))
    partition_pairs = _objects(partition["pairs"])
    classified = {_string(row["stable_diff_id"]): row for row in classifications}
    meaning = {key for key, row in classified.items() if row["classification"] == "meaning"}
    noise = {key for key, row in classified.items() if row["classification"] == "noise_candidate"}
    if {str(row.get("stable_diff_id", "")) for row in approved_noise} != noise:
        raise ValueError("every noise candidate requires explicit consolidation approval")
    prior = _objects(prior_mrq.get("mrq.jsonl", []))
    prior_by_semantic = {_string(row["semantic_key"]): row for row in prior}
    old_ids = {_string(row["mrq_id"]) for row in prior}
    mrqs: list[JsonObject] = []
    dispositions: list[JsonObject] = []
    evidence_rows: list[JsonObject] = []
    lineage: list[JsonObject] = []
    assigned: set[str] = set()
    merged_sources: set[str] = set()
    merge_targets: set[str] = set()
    split_targets: dict[str, list[str]] = {}
    component_membership = {
        identifier: f"{group['component_kind']}:{group['component_key']}"
        for group in component_groups
        for identifier in _strings(group["stable_diff_ids"])
    }
    for group in sorted(groups, key=lambda row: str(row.get("semantic_key", ""))):
        semantic_key = str(group.get("semantic_key", "")).strip()
        members = sorted(set(_strings(group.get("stable_diff_ids", []))))
        if not semantic_key or not members or any(member not in meaning or member in assigned for member in members):
            raise ValueError("invalid or overlapping consolidation group")
        supporting = sorted(set(_strings(group.get("supporting_diff_ids", []))) - set(members))
        if any(identifier not in classified for identifier in supporting):
            raise ValueError("unknown supporting DIF")
        component_keys = sorted({
            component_membership[identifier]
            for identifier in members + supporting
            if identifier in component_membership
        })
        if len(component_keys) > 1:
            evidence = _objects(group.get("evidence", []))
            if (
                group.get("component_keys") != component_keys
                or not str(group.get("rationale", "")).strip()
                or not evidence
                or any(
                    not str(item.get("path", "")).strip()
                    or not str(item.get("fingerprint", "")).startswith("sha256:")
                    for item in evidence
                )
            ):
                raise ValueError("cross-component group requires exact component keys, rationale, and evidence")
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
            for index, evidence in enumerate(_objects(classified[member]["evidence"])):
                evidence_rows.append({
                    "schema_version": "3",
                    "evidence_id": content_id("EVD-", {"mrq": mrq_id, "dif": member, "index": index, "evidence": evidence}),
                    "mrq_id": mrq_id,
                    "stable_diff_id": member,
                    "payload": evidence,
                })
        for member in supporting:
            dispositions.append({"schema_version": "3", "mrq_id": mrq_id, "stable_diff_id": member, "primary": False})
            for index, evidence in enumerate(_objects(classified[member]["evidence"])):
                evidence_rows.append({
                    "schema_version": "3",
                    "evidence_id": content_id("EVD-", {"mrq": mrq_id, "dif": member, "index": index, "evidence": evidence}),
                    "mrq_id": mrq_id,
                    "stable_diff_id": member,
                    "payload": evidence,
                })
        sources = sorted(set(_strings(group.get("source_mrq_ids", []))))
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
    new_ids = {_string(row["mrq_id"]) for row in mrqs}
    for source, targets in split_targets.items():
        if source not in old_ids or len(set(targets)) < 2:
            raise ValueError("MRQ split requires one active source and at least two targets")
        lineage.append({"domain": "mrq", "kind": "split", "source_ids": [source], "target_ids": sorted(set(targets))})
    superseded = (old_ids - new_ids) - merged_sources - set(split_targets)
    lineage.extend({"domain": "mrq", "kind": "supersede", "source_ids": [identifier], "target_ids": []} for identifier in sorted(superseded))
    plan: object = {
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
        "coverage_bitmap": [[row["left"], row["right"]] for row in partition_pairs],
        "partition_manifest": partition,
    }
    normalized_plan = _object(plan)
    validate_plan(normalized_plan)
    return normalized_plan


def store_plan(root: Path, plan: JsonObject) -> tuple[str, Path]:
    validate_plan(plan)
    plan_fingerprint = fingerprint(plan)
    path = root / "consolidation-plans" / f"{plan_fingerprint.removeprefix('sha256:')}.json"
    if path.is_file() and fingerprint(parse_json_object(path.read_text(encoding="utf-8"))) != plan_fingerprint:
        raise ValueError("consolidation.plan_storage")
    atomic_json(path, plan)
    return plan_fingerprint, path


def approve_plan(repo: Path, plan: JsonObject, expected_pointer: JsonObject, approval: JsonObject) -> JsonObject:
    validate_plan(plan)
    plan_fingerprint = fingerprint(plan)
    plan_bindings = _object(plan["bindings"])
    plan_mrq = _object(plan["mrq"])
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
        or any(approval[key] != plan_bindings[key] for key in (
            "source_fingerprint", "diff_fingerprint", "classification_fingerprint", "prior_mrq_fingerprint"
        ))
    ):
        raise ValueError("invalid or stale consolidation approval")
    with repository_lock(repo):
        path = repo / POINTER
        current = parse_json_object(path.read_text(encoding="utf-8")) if path.is_file() else sentinel()
        validate_pointer(current)
        if current.get("plan_fingerprint") == plan_fingerprint:
            return current
        if current != expected_pointer:
            raise RuntimeError("stale consolidation inputs")
        snapshot = input_snapshot(repo)
        if any(plan_bindings[key] != snapshot[key] for key in ("source_fingerprint", "diff_fingerprint", "classification_fingerprint")):
            raise RuntimeError("stale consolidation inputs")
        if plan_bindings["prior_mrq_fingerprint"] != fingerprint(snapshot.get("mrq", {})):
            raise RuntimeError("stale consolidation graph inputs")
        bindings = {
            key: snapshot[key]
            for key in ("source_fingerprint", "diff_fingerprint", "classification_fingerprint")
        }
        generation_rows = {name: _objects(plan_mrq[name]) for name in MRQ_FILES}
        generation_id = _publish_generation(repo, generation_rows, _object({
            **bindings,
            "prior_generation_id": current.get("mrq_generation_id"),
            "prior_ids": plan_bindings["prior_mrq_ids"],
            "legacy_identity_ids": [],
        }))
        changed = generation_id != current.get("mrq_generation_id")
        pointer = _object({
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
        })
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
) -> JsonObject:
    if kind not in {"batch", "decision"}:
        raise ValueError("unknown downstream binding")
    with (nullcontext() if already_locked else repository_lock(repo)):
        current = _object(load_active(repo)["pointer"])
        if current["transaction_id"] != expected_transaction_id or current.get(f"{kind}_generation_id") != expected_generation_id:
            raise RuntimeError(f"stale {kind} generation")
        updated = _object({
            **current,
            f"{kind}_generation_id": generation_id,
            f"{kind}_input_fingerprint": input_fingerprint,
        })
        atomic_json(repo / POINTER, updated)
        return updated
