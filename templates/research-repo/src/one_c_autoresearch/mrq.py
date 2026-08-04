from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import NotRequired, TypedDict

from .contracts import DECISIONS, MRQ_STATES, JsonValue, atomic_json, canonical_json, mrq_id, parse_json_object, recover_stage_publication, repository_lock, require_tracked_clean, sha256, stage_compatibility_fingerprint, validate_unique_ids


FILES = ("mrq.jsonl", "dispositions.jsonl", "evidence.jsonl", "lineage.jsonl", "approvals.jsonl")
CONFIDENCE = {"low", "medium", "high"}
AGREEMENT_STATES = {"pending_review", "changes_requested", "approved"}


def _compatibility_fingerprint(
    mrq: Mapping[str, object],
    dispositions: Iterable[Mapping[str, object]],
    diff_facts: Mapping[str, Mapping[str, object]],
    coverage: Mapping[str, Mapping[str, object]],
    approvals: Iterable[Mapping[str, object]],
) -> str:
    return stage_compatibility_fingerprint(
        mrq, dispositions, diff_facts, coverage, approvals,
    )
class EvidenceRef(TypedDict, total=False):
    path: str
    fingerprint: str
    stable_diff_id: str
    evidence_id: str
    opaque_external: bool
    source_generation_id: str
    customer_diff_id: str
    target_diff_ids: str
    coverage_status: str
    evidence_ref: str


class SourceCustomization(TypedDict):
    business_meaning: str
    scope: str
    confidence: str
    rationale: str
    evidence: list[EvidenceRef]


class MigrationDecision(TypedDict, total=False):
    decision: str
    target_evidence: list[EvidenceRef]
    target_coverage: list[EvidenceRef]
    residual_gap: str
    target_solution: str
    rationale: str
    acceptance_criteria: list[str]
    risk: str
    open_questions: list[str]
    agreement_status: str


class MRQRow(TypedDict):
    schema_version: str
    mrq_id: str
    semantic_key: str
    title: str
    state: str
    source_generation_id: str
    diff_generation_id: str
    source_customization: SourceCustomization
    migration_decision: MigrationDecision


class RuntimeEvidence(TypedDict):
    artifact_sha256: str
    environment_identity: str
    steps: list[str]
    observed_behavior: str
    result: str
    attachments: list[str]
    actor: str
    rationale: str


class EvidenceRow(TypedDict):
    schema_version: str
    evidence_id: str
    target_id: str
    path: str
    fingerprint: str
    source_generation_id: str
    diff_generation_id: str
    runtime: NotRequired[RuntimeEvidence]


class DispositionRow(TypedDict):
    schema_version: str
    stable_diff_id: str
    mrq_id: NotRequired[str]
    primary: bool
    approved_noise: NotRequired[ApprovedNoise]
    source_generation_id: str
    diff_generation_id: str


class LineageRow(TypedDict):
    schema_version: str
    kind: str
    source_ids: list[str]
    target_ids: list[str]
    rationale: str
    evidence: list[dict[str, JsonValue]]
    actor: str
    source_generation_id: str
    diff_generation_id: str


class ApprovalRow(TypedDict):
    schema_version: str
    event: str
    target_id: str
    actor: str
    rationale: str
    evidence: list[dict[str, JsonValue]]
    timestamp: str
    previous_generation: str | None
    fingerprint: str
    source_generation_id: str
    diff_generation_id: str


class ApprovedNoise(TypedDict):
    rationale: str
    actor: str
    evidence: list[dict[str, JsonValue]]


ArtifactRows = TypedDict("ArtifactRows", {
    "mrq.jsonl": list[MRQRow],
    "dispositions.jsonl": list[DispositionRow],
    "evidence.jsonl": list[EvidenceRow],
    "lineage.jsonl": list[LineageRow],
    "approvals.jsonl": list[ApprovalRow],
})

ActiveResult = TypedDict("ActiveResult", {
    "pointer": dict[str, JsonValue],
    "mrq.jsonl": list[MRQRow],
    "dispositions.jsonl": list[DispositionRow],
    "evidence.jsonl": list[EvidenceRow],
    "lineage.jsonl": list[LineageRow],
    "approvals.jsonl": list[ApprovalRow],
})


class TargetProposal(TypedDict):
    semantic_key: str
    title: str
    stable_diff_ids: list[str]
    supporting_diff_ids: list[str]
    evidence: list[EvidenceRef]
    business_meaning: str
    scope: str
    confidence: str
    rationale: str


def _artifact_rows(value: dict[str, list[dict[str, JsonValue]]]) -> ArtifactRows:
    if set(value) != set(FILES):
        raise ValueError("invalid MRQ artifact set")
    return {
        "mrq.jsonl": [_mrq_row(row) for row in value["mrq.jsonl"]],
        "dispositions.jsonl": [_disposition_row(row) for row in value["dispositions.jsonl"]],
        "evidence.jsonl": [_evidence_row(row) for row in value["evidence.jsonl"]],
        "lineage.jsonl": [_lineage_row(row) for row in value["lineage.jsonl"]],
        "approvals.jsonl": [_approval_row(row) for row in value["approvals.jsonl"]],
    }


def _string(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ValueError("expected JSON string")
    return value


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _objects(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("expected JSON object array")
    return [item for item in value if isinstance(item, dict)]


def _strings(value: JsonValue) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("expected JSON string array")
    return [item for item in value if isinstance(item, str)]


def _boolean(value: JsonValue) -> bool:
    if not isinstance(value, bool):
        raise ValueError("expected JSON boolean")
    return value


def _evidence_ref(value: JsonValue) -> EvidenceRef:
    row = _object(value)
    result: EvidenceRef = {}
    for key in (
        "path", "fingerprint", "stable_diff_id", "evidence_id", "source_generation_id",
        "customer_diff_id", "target_diff_ids", "coverage_status", "evidence_ref",
    ):
        if key in row:
            result[key] = _string(row[key])
    if "opaque_external" in row:
        result["opaque_external"] = _boolean(row["opaque_external"])
    return result


def _runtime_evidence(value: JsonValue) -> RuntimeEvidence:
    row = _object(value)
    return {
        "artifact_sha256": _string(row.get("artifact_sha256", "")),
        "environment_identity": _string(row.get("environment_identity", "")),
        "steps": _strings(row.get("steps", [])),
        "observed_behavior": _string(row.get("observed_behavior", "")),
        "result": _string(row.get("result", "")),
        "attachments": _strings(row.get("attachments", [])),
        "actor": _string(row.get("actor", "")),
        "rationale": _string(row.get("rationale", "")),
    }


def _source_customization(value: JsonValue) -> SourceCustomization:
    row = _object(value)
    return {
        "business_meaning": _string(row.get("business_meaning", "")),
        "scope": _string(row.get("scope", "")),
        "confidence": _string(row.get("confidence", "")),
        "rationale": _string(row.get("rationale", "")),
        "evidence": [_evidence_ref(item) for item in _objects(row.get("evidence", []))],
    }


def _migration_decision(value: JsonValue) -> MigrationDecision:
    row = _object(value)
    result: MigrationDecision = {}
    for key in ("decision", "residual_gap", "target_solution", "rationale", "risk", "agreement_status"):
        if key in row:
            result[key] = _string(row[key])
    for key in ("target_evidence", "target_coverage"):
        if key in row:
            result[key] = [_evidence_ref(item) for item in _objects(row[key])]
    for key in ("acceptance_criteria", "open_questions"):
        if key in row:
            result[key] = _strings(row[key])
    return result


def _mrq_row(row: dict[str, JsonValue]) -> MRQRow:
    return {
        "schema_version": _string(row.get("schema_version", "")),
        "mrq_id": _string(row.get("mrq_id", "")),
        "semantic_key": _string(row.get("semantic_key", "")),
        "title": _string(row.get("title", "")),
        "state": _string(row.get("state", "")),
        "source_generation_id": _string(row.get("source_generation_id", "")),
        "diff_generation_id": _string(row.get("diff_generation_id", "")),
        "source_customization": _source_customization(row.get("source_customization", {})),
        "migration_decision": _migration_decision(row.get("migration_decision", {})),
    }


def _approved_noise(value: JsonValue) -> ApprovedNoise:
    row = _object(value)
    return {
        "rationale": _string(row.get("rationale", "")),
        "actor": _string(row.get("actor", "")),
        "evidence": _objects(row.get("evidence", [])),
    }


def _disposition_row(row: dict[str, JsonValue]) -> DispositionRow:
    result = DispositionRow(
        schema_version=_string(row.get("schema_version", "")),
        stable_diff_id=_string(row.get("stable_diff_id", "")),
        primary=_boolean(row.get("primary", False)),
        source_generation_id=_string(row.get("source_generation_id", "")),
        diff_generation_id=_string(row.get("diff_generation_id", "")),
    )
    if "mrq_id" in row:
        result["mrq_id"] = _string(row["mrq_id"])
    if "approved_noise" in row:
        result["approved_noise"] = _approved_noise(row["approved_noise"])
    return result


def _evidence_row(row: dict[str, JsonValue]) -> EvidenceRow:
    result = EvidenceRow(
        schema_version=_string(row.get("schema_version", "")), evidence_id=_string(row.get("evidence_id", "")),
        target_id=_string(row.get("target_id", "")), path=_string(row.get("path", "")),
        fingerprint=_string(row.get("fingerprint", "")), source_generation_id=_string(row.get("source_generation_id", "")),
        diff_generation_id=_string(row.get("diff_generation_id", "")),
    )
    if "runtime" in row:
        result["runtime"] = _runtime_evidence(row["runtime"])
    return result


def _lineage_row(row: dict[str, JsonValue]) -> LineageRow:
    return {
        "schema_version": _string(row.get("schema_version", "")), "kind": _string(row.get("kind", "")),
        "source_ids": _strings(row.get("source_ids", [])), "target_ids": _strings(row.get("target_ids", [])),
        "rationale": _string(row.get("rationale", "")), "evidence": _objects(row.get("evidence", [])),
        "actor": _string(row.get("actor", "")), "source_generation_id": _string(row.get("source_generation_id", "")),
        "diff_generation_id": _string(row.get("diff_generation_id", "")),
    }


def _approval_row(row: dict[str, JsonValue]) -> ApprovalRow:
    previous = row.get("previous_generation")
    if previous is not None and not isinstance(previous, str):
        raise ValueError("expected previous generation string or null")
    return {
        "schema_version": _string(row.get("schema_version", "")), "event": _string(row.get("event", "")),
        "target_id": _string(row.get("target_id", "")), "actor": _string(row.get("actor", "")),
        "rationale": _string(row.get("rationale", "")), "evidence": _objects(row.get("evidence", [])),
        "timestamp": _string(row.get("timestamp", "")), "previous_generation": previous,
        "fingerprint": _string(row.get("fingerprint", "")), "source_generation_id": _string(row.get("source_generation_id", "")),
        "diff_generation_id": _string(row.get("diff_generation_id", "")),
    }


def _semantic_evidence(fact: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    return _objects(fact.get("semantic_evidence", []))


def _semantic_fingerprint(fact: dict[str, JsonValue], evidence: EvidenceRef) -> str | None:
    path = evidence.get("path")
    for value in _semantic_evidence(fact):
        if value.get("path") == path:
            fingerprint = value.get("fingerprint")
            return fingerprint if isinstance(fingerprint, str) else None
    return None


def _coverage_ref(row: Mapping[str, str]) -> EvidenceRef:
    return {
        "customer_diff_id": row["customer_diff_id"],
        "target_diff_ids": row["target_diff_ids"],
        "coverage_status": row["coverage_status"],
        "evidence_ref": row["evidence_ref"],
    }


def comparison_epoch_fingerprint(source: Mapping[str, JsonValue], diff: Mapping[str, JsonValue] | None = None) -> str:
    if source.get("schema_version") == "2":
        value = str(source.get("source_comparison_epoch_fingerprint", ""))
        if not value.startswith("sha256:"):
            raise ValueError("routed source comparison epoch is missing")
        if diff is None:
            return value.removeprefix("sha256:")
        diff = diff or source
        analyzer = str(diff.get("extension_analyzer_version", ""))
        adapters = _strings(diff.get("extension_adapter_versions"))
        if not analyzer or adapters != sorted(adapters) or any(not item.strip() for item in adapters):
            raise ValueError("extension comparison contract is missing")
        return sha256(canonical_json({
            "extension_adapter_versions": adapters,
            "extension_analyzer_version": analyzer,
            "source_comparison_epoch_fingerprint": value,
        }))
    return sha256(canonical_json({"acquisition_profile_id": source["acquisition_profile_id"], "normalizer_version": source["normalizer_version"], "representation_schema": source["representation_schema"]}))


def _jsonl(path: Path) -> list[dict[str, JsonValue]]:
    if not path.is_file():
        return []
    return [parse_json_object(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: Iterable[object]) -> None:
    with path.open("wb") as stream:
        for row in rows:
            _ = stream.write(canonical_json(row) + b"\n")
        stream.flush(); os.fsync(stream.fileno())


def _diff_facts(repo: Path, diff_id: str) -> dict[str, dict[str, JsonValue]]:
    import csv
    root = repo / "analysis/indexes/generations" / diff_id
    with (root / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
        facts: dict[str, dict[str, JsonValue]] = {
            row["stable_diff_id"]: dict(row) for row in csv.DictReader(stream)
        }
    for detail in _jsonl(root / "extension-diff.jsonl"):
        fact = facts.get(str(detail.get("stable_diff_id", "")))
        if fact is not None:
            extension = _string(detail.get("extension_uuid", ""))
            evidence = _objects(detail.get("evidence", []))
            fact["semantic_evidence"] = [
                {"path": f"{_string(item['role'])}/extensions/{extension}/{_string(item['path'])}", "fingerprint": _string(item["fingerprint"])}
                for item in evidence
            ]
    return facts


def active(
    repo: Path,
    *,
    require_tracked_clean_state: bool = False,
    pointer_candidate: dict[str, JsonValue] | None = None,
    source_candidate: dict[str, JsonValue] | None = None,
    diff_candidate: dict[str, JsonValue] | None = None,
) -> ActiveResult:
    if pointer_candidate is None:
        recover_stage_publication(repo)
    pointer_path = repo / "research/active-generation.json"
    pointer = pointer_candidate or parse_json_object(pointer_path.read_text(encoding="utf-8"))
    generation = pointer.get("canonical_generation_id")
    if not generation:
        return {"pointer": pointer, "mrq.jsonl": [], "dispositions.jsonl": [], "evidence.jsonl": [], "lineage.jsonl": [], "approvals.jsonl": []}
    generation_id = _string(generation)
    root = repo / "analysis/migration-requirements/generations" / generation_id
    if require_tracked_clean_state and pointer_candidate is None:
        require_tracked_clean(repo, [root, repo / "research/generations" / generation_id, pointer_path])
    artifacts = _artifact_rows({name: _jsonl(root / name) for name in FILES})
    result: ActiveResult = {"pointer": pointer, **artifacts}
    manifest = parse_json_object((repo / "research/generations" / generation_id / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("canonical_generation_id") != generation:
        raise ValueError("canonical generation manifest mismatch")
    source_pointer = source_candidate or parse_json_object((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    diff_pointer = diff_candidate or parse_json_object((repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
    source_id, diff_id = pointer.get("source_generation_id"), pointer.get("diff_generation_id")
    if source_id != source_pointer.get("generation_id") or diff_id != diff_pointer.get("generation_id") or diff_pointer.get("source_generation_id") != source_id:
        raise ValueError("mixed active generations")
    if manifest.get("source_generation_id") != source_id or manifest.get("diff_generation_id") != diff_id:
        raise ValueError("canonical manifest generation binding mismatch")
    for name in FILES:
        manifest_files = _object(manifest["files"])
        if sha256((root / name).read_bytes()) != manifest_files[name]:
            raise ValueError(f"canonical artifact hash mismatch: {name}")
    analyzer = parse_json_object((repo / "analysis/indexes/generations" / str(diff_id) / "extension-analyzer-manifest.json").read_text(encoding="utf-8")) if diff_pointer.get("schema_version") == "2" else None
    epoch_fingerprint = comparison_epoch_fingerprint(source_pointer, analyzer)
    if manifest.get("comparison_epoch_fingerprint") != epoch_fingerprint:
        raise ValueError("canonical comparison epoch mismatch")
    preimage = {"schema_version": "1", "source_generation_id": source_id, "diff_generation_id": diff_id, "comparison_epoch_fingerprint": epoch_fingerprint, "files": manifest["files"]}
    if sha256(canonical_json(preimage)) != generation_id:
        raise ValueError("canonical generation ID mismatch")
    customer = {key: row for key, row in _diff_facts(repo, str(diff_id)).items() if row.get("before_role") == "vendor_baseline" and row.get("after_role") == "target_cf"}
    validate_graph(artifacts, str(pointer.get("source_generation_id")), str(diff_id), customer)
    return result


def validate_graph(rows: ArtifactRows, source_id: str, diff_id: str, valid_customer_diffs: set[str] | dict[str, dict[str, JsonValue]] | None = None) -> None:
    mrqs = rows["mrq.jsonl"]
    validate_unique_ids([{"id": item["mrq_id"], "preimage": {"schema_version": item["schema_version"], "semantic_key": item["semantic_key"]}} for item in mrqs], "id", "preimage")
    ids = {item["mrq_id"] for item in mrqs}
    active_ids = {item["mrq_id"] for item in mrqs if item.get("state") != "superseded"}
    semantic_keys = [item["semantic_key"] for item in mrqs]
    if len(semantic_keys) != len(set(semantic_keys)):
        raise ValueError("duplicate MRQ semantic key")
    evidence_ids: set[str] = set()
    runtime_evidence: dict[str, dict[str, JsonValue]] = {}
    for evidence in rows["evidence.jsonl"]:
        required = {"schema_version", "evidence_id", "target_id", "path", "fingerprint", "source_generation_id", "diff_generation_id"}
        if set(evidence) != required and set(evidence) != required | {"runtime"} or evidence.get("schema_version") != "1":
            raise ValueError("invalid canonical evidence fields")
        evidence_id = str(evidence.get("evidence_id", ""))
        target_id = str(evidence.get("target_id", ""))
        if not evidence_id or evidence_id in evidence_ids or target_id not in ids and not re.fullmatch(r"DIF-[0-9A-F]{16}", target_id):
            raise ValueError("invalid or duplicate canonical evidence identity")
        if not str(evidence.get("path", "")).strip() or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(evidence.get("fingerprint", ""))):
            raise ValueError(f"invalid canonical evidence payload: {evidence_id}")
        if evidence.get("source_generation_id") != source_id or evidence.get("diff_generation_id") != diff_id:
            raise ValueError(f"stale canonical evidence generation binding: {evidence_id}")
        runtime = evidence.get("runtime")
        if runtime is not None:
            exact = {"artifact_sha256", "environment_identity", "steps", "observed_behavior", "result", "attachments", "actor", "rationale"}
            if set(runtime) != exact or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(runtime.get("artifact_sha256", ""))) or runtime["artifact_sha256"] != evidence["fingerprint"]:
                raise ValueError(f"invalid opaque-artifact runtime evidence: {evidence_id}")
            if not all(str(runtime.get(key, "")).strip() for key in ("environment_identity", "observed_behavior", "result", "actor", "rationale")) or not all(isinstance(runtime.get(key), list) and runtime[key] and all(str(value).strip() for value in runtime[key]) for key in ("steps", "attachments")):
                raise ValueError(f"incomplete opaque-artifact runtime evidence: {evidence_id}")
            runtime_evidence[evidence_id] = parse_json_object(canonical_json(evidence).decode())
        evidence_ids.add(evidence_id)
    for item in mrqs:
        if item["mrq_id"] != mrq_id(item["semantic_key"], item["schema_version"]):
            raise ValueError(f"invalid MRQ ID: {item['mrq_id']}")
        if item.get("state") not in MRQ_STATES:
            raise ValueError(f"invalid MRQ state: {item.get('state')}")
        if item.get("source_generation_id") != source_id or item.get("diff_generation_id") != diff_id:
            raise ValueError(f"stale MRQ generation binding: {item['mrq_id']}")
        decision = item.get("migration_decision", {}).get("decision")
        if decision is not None and decision not in DECISIONS:
            raise ValueError(f"invalid migration decision: {decision}")
        if not str(item.get("title", "")).strip():
            raise ValueError(f"MRQ title is required: {item['mrq_id']}")
        source = item.get("source_customization", {})
        if not all(str(source.get(key, "")).strip() for key in ("business_meaning", "scope", "rationale")) or source.get("confidence") not in CONFIDENCE:
            raise ValueError(f"MRQ source-customization section is incomplete: {item['mrq_id']}")
        for evidence in source.get("evidence", []):
            if not str(evidence.get("path", "")).strip() or not str(evidence.get("fingerprint", "")).startswith("sha256:") or not str(evidence.get("stable_diff_id", "")).startswith("DIF-"):
                raise ValueError(f"invalid source evidence: {item['mrq_id']}")
            if item["state"] != "superseded" and isinstance(valid_customer_diffs, dict):
                stable_diff_id = evidence.get("stable_diff_id")
                if stable_diff_id is None:
                    raise ValueError(f"source evidence lacks stable DIF identity: {item['mrq_id']}")
                fact = valid_customer_diffs.get(stable_diff_id)
                expected_fingerprint = fact.get("after_fingerprint") or fact.get("before_fingerprint") if fact else None
                semantic_evidence = _objects(fact.get("semantic_evidence", [])) if fact else []
                evidence_path = evidence.get("path", "")
                evidence_fingerprint = evidence.get("fingerprint", "")
                matches_semantic = any(evidence_path == semantic.get("path") and evidence_fingerprint == semantic.get("fingerprint") for semantic in semantic_evidence)
                if not fact or not matches_semantic and (evidence_path != fact.get("path") or evidence_fingerprint != expected_fingerprint):
                    raise ValueError(f"source evidence does not match active physical DIF: {item['mrq_id']}")
            if evidence.get("opaque_external") and evidence.get("evidence_id") not in runtime_evidence:
                raise ValueError(f"opaque external MRQ evidence requires a runtime record: {item['mrq_id']}")
        if item["state"] in {"ready_for_review", "approved"}:
            migration = item.get("migration_decision", {})
            required = ("decision", "target_evidence", "target_coverage", "residual_gap", "target_solution", "acceptance_criteria", "risk", "rationale")
            if not all(migration.get(key) for key in required) or not isinstance(migration.get("open_questions"), list) or migration.get("agreement_status") not in AGREEMENT_STATES:
                raise ValueError(f"review-ready MRQ is incomplete: {item['mrq_id']}")
        if item["state"] == "approved":
            candidate = deepcopy(item); candidate["state"] = "ready_for_review"; candidate["migration_decision"]["agreement_status"] = "pending_review"
            fingerprint = sha256(canonical_json(candidate))
            if not any(event.get("event") == "approve" and event.get("target_id") == item["mrq_id"] and event.get("fingerprint") == fingerprint and event.get("source_generation_id") == source_id and event.get("diff_generation_id") == diff_id for event in rows["approvals.jsonl"]):
                raise ValueError(f"approved MRQ lacks matching approval event: {item['mrq_id']}")
    ownership: dict[str, int] = {}
    for relation in rows["dispositions.jsonl"]:
        if relation.get("source_generation_id") != source_id or relation.get("diff_generation_id") != diff_id:
            raise ValueError("stale disposition generation binding")
        owner = relation.get("mrq_id")
        if owner and owner not in ids:
            raise ValueError(f"unknown MRQ disposition owner: {owner}")
        stable_diff = str(relation.get("stable_diff_id", ""))
        if not stable_diff.startswith("DIF-") or valid_customer_diffs is not None and stable_diff not in valid_customer_diffs and (not owner or owner in active_ids):
            raise ValueError(f"unknown DIF disposition: {stable_diff}")
        if relation.get("primary") and (not owner or owner in active_ids):
            ownership[stable_diff] = ownership.get(stable_diff, 0) + 1
        noise = relation.get("approved_noise")
        if not owner:
            if not isinstance(noise, dict) or not str(noise.get("rationale", "")).strip() or not str(noise.get("actor", "")).strip() or not noise.get("evidence"):
                raise ValueError(f"primary disposition without MRQ requires approved noise: {stable_diff}")
            fingerprint = sha256(canonical_json(noise))
            if not any(event.get("event") == "approve" and event.get("target_id") == stable_diff and event.get("fingerprint") == fingerprint and event.get("source_generation_id") == source_id and event.get("diff_generation_id") == diff_id for event in rows["approvals.jsonl"]):
                raise ValueError(f"technical noise lacks matching approval: {stable_diff}")
        elif noise:
            raise ValueError(f"DIF cannot have both MRQ ownership and approved noise: {stable_diff}")
    for event in rows["approvals.jsonl"]:
        required = {"schema_version", "event", "target_id", "actor", "rationale", "evidence", "timestamp", "previous_generation", "fingerprint", "source_generation_id", "diff_generation_id"}
        if set(event) != required or event.get("schema_version") != "1" or event.get("event") not in {"approve", "request_changes"}:
            raise ValueError("invalid approval event fields")
        if not str(event.get("actor", "")).strip() or not str(event.get("rationale", "")).strip() or not re.fullmatch(r"[0-9a-f]{64}", str(event.get("fingerprint", ""))):
            raise ValueError("approval actor, rationale, and fingerprint are required")
        try:
            _ = datetime.fromisoformat(str(event.get("timestamp", "")).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("approval timestamp is invalid") from exc
        if any(len(str(event.get(key, ""))) != 64 for key in ("source_generation_id", "diff_generation_id")):
            raise ValueError("approval generation binding is invalid")
        if event["previous_generation"] is not None and not re.fullmatch(r"[0-9a-f]{64}", str(event["previous_generation"])):
            raise ValueError("approval previous generation and evidence are required")
    superseded: set[str] = set()
    for lineage in rows["lineage.jsonl"]:
        required = {"schema_version", "kind", "source_ids", "target_ids", "rationale", "evidence", "actor", "source_generation_id", "diff_generation_id"}
        sources, targets, kind = lineage.get("source_ids"), lineage.get("target_ids"), lineage.get("kind")
        if set(lineage) != required or lineage.get("schema_version") != "1" or kind not in {"split", "merge", "supersede", "revalidate"}:
            raise ValueError("invalid MRQ lineage fields")
        if not sources or not targets or len(sources) != len(set(sources)) or len(targets) != len(set(targets)) or any(value not in ids for value in sources + targets):
            raise ValueError("MRQ lineage references unknown or duplicate identities")
        if kind == "split" and (len(sources) != 1 or len(targets) < 2) or kind == "merge" and (len(sources) < 2 or len(targets) != 1) or kind == "revalidate" and sources != targets:
            raise ValueError(f"invalid {kind} lineage cardinality")
        if not str(lineage.get("rationale", "")).strip() or not str(lineage.get("actor", "")).strip() or not lineage["evidence"]:
            raise ValueError("MRQ lineage rationale, actor, and evidence are required")
        if lineage.get("source_generation_id") != source_id or lineage.get("diff_generation_id") != diff_id:
            raise ValueError("stale MRQ lineage generation binding")
        if kind in {"split", "merge", "supersede"}:
            superseded.update(sources)
    if {item["mrq_id"] for item in mrqs if item["state"] == "superseded"} != superseded:
        raise ValueError("superseded MRQ state requires exact lineage")
    if any(count != 1 for count in ownership.values()):
        raise ValueError("a customer DIF has conflicting primary dispositions")


def publish(repo: Path, rows: ArtifactRows, source_id: str, diff_id: str, epoch: Mapping[str, JsonValue], expected_generation: str | None, *, activate: bool = True, diff_candidate: dict[str, JsonValue] | None = None, preserve_approval_prefix: bool = True, fence: Callable[[], None] | None = None) -> dict[str, JsonValue]:
    with repository_lock(repo):
        current = parse_json_object((repo / "research/active-generation.json").read_text(encoding="utf-8"))
        if current.get("canonical_generation_id") != expected_generation:
            raise RuntimeError("stale canonical generation")
        diff_pointer = diff_candidate or parse_json_object((repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
        analyzer = parse_json_object((repo / "analysis/indexes/generations" / diff_id / "extension-analyzer-manifest.json").read_text(encoding="utf-8")) if diff_pointer.get("schema_version") == "2" else None
        epoch_fingerprint = comparison_epoch_fingerprint(epoch, analyzer)
        if expected_generation:
            prior_manifest = parse_json_object((repo / "research/generations" / expected_generation / "manifest.json").read_text(encoding="utf-8"))
            if prior_manifest.get("comparison_epoch_fingerprint") == epoch_fingerprint:
                if preserve_approval_prefix:
                    previous = _jsonl(repo / "analysis/migration-requirements/generations" / expected_generation / "approvals.jsonl")
                    if rows["approvals.jsonl"][:len(previous)] != previous:
                        raise ValueError("same-epoch approval ledger prefix was changed")
            elif any(rows[name] for name in FILES):
                raise ValueError("a new comparison epoch must start with empty MRQ ledgers")
        if diff_pointer.get("generation_id") != diff_id or diff_pointer.get("source_generation_id") != source_id:
            raise RuntimeError("stale source or diff generation")
        valid_customer_diffs = {key: row for key, row in _diff_facts(repo, diff_id).items() if row.get("before_role") == "vendor_baseline" and row.get("after_role") == "target_cf"}
        validate_graph(rows, source_id, diff_id, valid_customer_diffs)
        parent = repo / "analysis/migration-requirements/.staging"; parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            staging = Path(temporary)
            for name in FILES:
                _write_jsonl(staging / name, rows[name] if name == "approvals.jsonl" else sorted(rows[name], key=canonical_json))
            hashes = {name: sha256((staging / name).read_bytes()) for name in FILES}
            preimage = {"schema_version": "1", "source_generation_id": source_id, "diff_generation_id": diff_id, "comparison_epoch_fingerprint": epoch_fingerprint, "files": hashes}
            generation = sha256(canonical_json(preimage))
            if fence is not None:
                fence()
            destination = repo / "analysis/migration-requirements/generations" / generation
            destination.parent.mkdir(parents=True, exist_ok=True)
            manifest_path = repo / "research/generations" / generation / "manifest.json"
            if not destination.exists():
                os.replace(staging, destination)
                parent_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
                manifest = {**preimage, "canonical_generation_id": generation, "created_at": datetime.now(timezone.utc).isoformat()}
                atomic_json(manifest_path, manifest)
            else:
                existing = parse_json_object(manifest_path.read_text(encoding="utf-8"))
                if {key: existing.get(key) for key in preimage} != preimage or existing.get("canonical_generation_id") != generation:
                    raise ValueError("existing canonical generation manifest is inconsistent")
            if any(sha256((destination / name).read_bytes()) != digest for name, digest in hashes.items()):
                raise ValueError("existing canonical generation payload is inconsistent")
            pointer: dict[str, JsonValue] = {"schema_version": "1", "canonical_generation_id": generation, "source_generation_id": source_id, "diff_generation_id": diff_id}
            prior_batch = current.get("batch_generation")
            if prior_batch:
                from .mrq_batches import load_active as load_active_batches, source_mrq_payload
                _, fingerprint, _ = source_mrq_payload(repo, generation)
                prior_batch_object = _object(prior_batch)
                if fingerprint == prior_batch_object.get("source_mrq_fingerprint"):
                    try:
                        _ = load_active_batches(repo)
                    except (OSError, ValueError, KeyError):
                        pass
                    else:
                        pointer["batch_generation"] = prior_batch_object
            if activate:
                atomic_json(repo / "research/active-generation.json", pointer)
            return pointer


def revalidate_unchanged(repo: Path, new_source: dict[str, JsonValue], new_diff: dict[str, JsonValue], actor: str, rationale: str, timestamp: str, *, activate: bool = True) -> dict[str, JsonValue]:
    if not actor.strip() or not rationale.strip() or not timestamp.strip():
        raise ValueError("revalidation actor, rationale, and timestamp are required")
    previous = parse_json_object((repo / "research/active-generation.json").read_text(encoding="utf-8"))
    if not previous.get("canonical_generation_id"):
        raise ValueError("there is no canonical generation to revalidate")
    generation = _string(previous["canonical_generation_id"])
    previous_source_id = _string(previous["source_generation_id"])
    previous_diff_id = _string(previous["diff_generation_id"])
    new_source_id = _string(new_source["generation_id"])
    new_diff_id = _string(new_diff["generation_id"])
    root = repo / "analysis/migration-requirements/generations" / generation
    state = _artifact_rows({name: _jsonl(root / name) for name in FILES})
    old_manifest = parse_json_object((repo / "research/generations" / generation / "manifest.json").read_text(encoding="utf-8"))
    analyzer = parse_json_object((repo / "analysis/indexes/generations" / new_diff_id / "extension-analyzer-manifest.json").read_text(encoding="utf-8")) if new_diff.get("schema_version") == "2" else None
    if old_manifest.get("comparison_epoch_fingerprint") != comparison_epoch_fingerprint(new_source, analyzer):
        raise ValueError("a new comparison epoch cannot revalidate prior MRQ state")
    rows = deepcopy(state)
    import csv
    old_all = _diff_facts(repo, previous_diff_id)
    old_facts = {key: item for key, item in old_all.items() if item.get("before_role") == "vendor_baseline" and item.get("after_role") == "target_cf"}
    new_all = _diff_facts(repo, new_diff_id)
    facts = {key: item for key, item in new_all.items() if item.get("before_role") == "vendor_baseline" and item.get("after_role") == "target_cf"}
    with (repo / "analysis/indexes/generations" / new_diff_id / "target-coverage.csv").open(encoding="utf-8", newline="") as stream:
        coverage = {item["customer_diff_id"]: {key: item[key] for key in ("customer_diff_id", "target_diff_ids", "coverage_status", "evidence_ref")} for item in csv.DictReader(stream)}
    with (repo / "analysis/indexes/generations" / previous_diff_id / "target-coverage.csv").open(encoding="utf-8", newline="") as stream:
        old_coverage = {item["customer_diff_id"]: {key: item[key] for key in ("customer_diff_id", "target_diff_ids", "coverage_status", "evidence_ref")} for item in csv.DictReader(stream)}
    compatibility: dict[str, str] = {}
    compatible: set[str] = set()
    affected_reasons: dict[str, list[str]] = {}
    for item in rows["mrq.jsonl"]:
        relations = [relation for relation in rows["dispositions.jsonl"] if relation.get("mrq_id") == item["mrq_id"]]
        identifiers = {relation["stable_diff_id"] for relation in relations}
        candidate_item = deepcopy(item)
        candidate_item["source_generation_id"] = new_source_id
        candidate_item["diff_generation_id"] = new_diff_id
        candidate_relations: list[DispositionRow] = [{**relation, "source_generation_id": new_source_id, "diff_generation_id": new_diff_id} for relation in relations]
        for evidence in candidate_item.get("source_customization", {}).get("evidence", []):
            stable_diff_id = evidence.get("stable_diff_id")
            fact = facts.get(stable_diff_id) if stable_diff_id else None
            refreshed_fingerprint = _semantic_fingerprint(fact, evidence) if fact else None
            if refreshed_fingerprint is None and fact and evidence.get("path") == fact.get("path"):
                candidate = fact.get("after_fingerprint") or fact.get("before_fingerprint")
                refreshed_fingerprint = candidate if isinstance(candidate, str) else None
            if refreshed_fingerprint is not None:
                evidence["fingerprint"] = refreshed_fingerprint
        candidate_migration = candidate_item["migration_decision"]
        candidate_migration["target_coverage"] = [
            _coverage_ref(row)
            for value in candidate_migration.get("target_coverage", [])
            if (customer_diff_id := value.get("customer_diff_id")) is not None
            and (row := coverage.get(customer_diff_id)) is not None
        ]
        old_fp = _compatibility_fingerprint(item, relations, {key: {name: value for name, value in fact.items() if name != "source_generation"} for key, fact in old_facts.items()}, old_coverage, rows["approvals.jsonl"])
        new_fp = _compatibility_fingerprint(candidate_item, candidate_relations, {key: {name: value for name, value in fact.items() if name != "source_generation"} for key, fact in facts.items()}, coverage, rows["approvals.jsonl"])
        source_ok = all(
            (stable_diff_id := evidence.get("stable_diff_id")) is not None
            and (fact := facts.get(stable_diff_id)) is not None
            and (
                _semantic_fingerprint(fact, evidence)
                or (fact.get("after_fingerprint") or fact.get("before_fingerprint") if evidence.get("path") == fact.get("path") else None)
            ) == evidence.get("fingerprint")
            for evidence in item.get("source_customization", {}).get("evidence", [])
        )
        target_ok = all(
            not (stable_diff_id := evidence.get("stable_diff_id"))
            or (
                (new_fact := new_all.get(stable_diff_id)) is not None
                and old_all.get(stable_diff_id, {}).get("content_fingerprint") == new_fact.get("content_fingerprint")
            )
            for evidence in item.get("migration_decision", {}).get("target_evidence", [])
        )
        coverage_ok = all(
            coverage.get(value.get("customer_diff_id")) == {key: value.get(key, "") for key in ("customer_diff_id", "target_diff_ids", "coverage_status", "evidence_ref")}
            for value in item.get("migration_decision", {}).get("target_coverage", [])
        )
        diffs_ok = all(identifier in facts and old_facts.get(identifier, {}).get("content_fingerprint") == facts[identifier].get("content_fingerprint") for identifier in identifiers)
        if relations and old_fp == new_fp and diffs_ok and source_ok and target_ok and coverage_ok:
            compatible.add(item["mrq_id"])
            compatibility[item["mrq_id"]] = new_fp
        else:
            affected_reasons[item["mrq_id"]] = sorted({
                *(["missing_disposition"] if not relations else []),
                *(["primary_or_supporting_dif_changed"] if not diffs_ok else []),
                *(["source_evidence_changed"] if not source_ok else []),
                *(["target_evidence_changed"] if not target_ok else []),
                *(["target_coverage_changed"] if not coverage_ok else []),
                *(["compatibility_closure_changed"] if old_fp != new_fp else []),
            })
    rows["mrq.jsonl"] = [item for item in rows["mrq.jsonl"] if item["mrq_id"] in compatible]
    rows["lineage.jsonl"] = [
        item for item in rows["lineage.jsonl"]
        if set(item.get("source_ids", []) + item.get("target_ids", [])) <= compatible
    ]
    retained = [
        item
        for item in rows["dispositions.jsonl"]
        if item.get("mrq_id") in compatible and item.get("stable_diff_id") in facts
    ]
    for relation in retained:
        identifier = relation["stable_diff_id"]
        if old_facts.get(identifier, {}).get("content_fingerprint") != facts[identifier].get("content_fingerprint"):
            raise ValueError(f"changed DIF requires an explicit updated MRQ proposal: {identifier}")
    rows["dispositions.jsonl"] = retained
    # Утверждения остаются в прежнем неизменяемом поколении. Новое поколение
    # требует отдельного ручного утверждения даже для совместимого MRQ.
    rows["approvals.jsonl"] = []
    for item in rows["mrq.jsonl"]:
        item["source_generation_id"] = new_source_id; item["diff_generation_id"] = new_diff_id
        refreshed: list[EvidenceRef] = []
        for evidence in item.get("source_customization", {}).get("evidence", []):
            stable_diff_id = evidence.get("stable_diff_id")
            fact = facts.get(stable_diff_id) if stable_diff_id else None
            semantic_fingerprint = _semantic_fingerprint(fact, evidence) if fact else None
            if semantic_fingerprint:
                refreshed.append({**evidence, "fingerprint": semantic_fingerprint})
            elif fact and evidence.get("path") == fact.get("path"):
                fingerprint = fact.get("after_fingerprint") or fact.get("before_fingerprint")
                if isinstance(fingerprint, str):
                    refreshed.append({**evidence, "fingerprint": fingerprint})
        item["source_customization"]["evidence"] = refreshed
        if not refreshed:
            raise ValueError(f"MRQ source evidence requires an explicit update: {item['mrq_id']}")
        for evidence in item.get("migration_decision", {}).get("target_evidence", []):
            identifier = evidence.get("stable_diff_id")
            if identifier and (identifier not in new_all or old_all.get(identifier, {}).get("content_fingerprint") != new_all[identifier].get("content_fingerprint")):
                raise ValueError(f"MRQ target evidence requires an explicit update: {item['mrq_id']}")
            if evidence.get("source_generation_id"): evidence["source_generation_id"] = new_source_id
        current_coverage = item.get("migration_decision", {}).get("target_coverage", [])
        refreshed_coverage = [coverage.get(value.get("customer_diff_id")) for value in current_coverage]
        if any(value is None or {key: old.get(key, "") for key in value} != value for old, value in zip(current_coverage, refreshed_coverage)):
            raise ValueError(f"MRQ target coverage requires an explicit update: {item['mrq_id']}")
        item["migration_decision"]["target_coverage"] = [_coverage_ref(value) for value in refreshed_coverage if value is not None]
        rows["lineage.jsonl"].append({"schema_version": "1", "kind": "revalidate", "source_ids": [item["mrq_id"]], "target_ids": [item["mrq_id"]], "rationale": rationale, "evidence": [{"old_source_generation_id": previous_source_id, "new_source_generation_id": new_source_id, "old_diff_generation_id": previous_diff_id, "new_diff_generation_id": new_diff_id, "affected_fields": ["source_generation_id", "diff_generation_id"], "compatibility_fingerprint": compatibility[item["mrq_id"]]}], "actor": actor, "source_generation_id": new_source_id, "diff_generation_id": new_diff_id})
        if item.get("state") == "approved":
            item["state"] = "ready_for_review"
            item["migration_decision"]["agreement_status"] = "pending_review"
    for relation in rows["dispositions.jsonl"]:
        relation["source_generation_id"] = new_source_id; relation["diff_generation_id"] = new_diff_id
    pointer = publish(
        repo,
        rows,
        new_source_id,
        new_diff_id,
        new_source,
        generation,
        activate=activate,
        diff_candidate=None if activate else new_diff,
        preserve_approval_prefix=False,
    )
    return {
        **pointer,
        "retained_mrq": sorted(compatible),
        "affected_mrq": sorted({item["mrq_id"] for item in state["mrq.jsonl"]} - compatible),
        "affected_mrq_reasons": affected_reasons,
        "new_dif": sorted(set(facts) - set(old_facts)),
        "changed_dif": sorted(
            identifier
            for identifier in set(facts) & set(old_facts)
            if facts[identifier].get("content_fingerprint") != old_facts[identifier].get("content_fingerprint")
        ),
        "disappeared_dif": sorted(set(old_facts) - set(facts)),
    }


def propose(rows: ArtifactRows, semantic_key: str, title: str, source_id: str, diff_id: str, stable_diff_ids: list[str], supporting_diff_ids: list[str], evidence: list[EvidenceRef], business_meaning: str, scope: str, confidence: str, rationale: str) -> str:
    all_diffs = stable_diff_ids + supporting_diff_ids
    if not semantic_key.strip() or not title.strip() or not stable_diff_ids or len(all_diffs) != len(set(all_diffs)) or any(not item.startswith("DIF-") for item in all_diffs):
        raise ValueError("invalid MRQ semantic key or DIF set")
    if not business_meaning.strip() or not scope.strip() or confidence not in CONFIDENCE or not rationale.strip():
        raise ValueError("incomplete MRQ source-customization section")
    active_ids = {item["mrq_id"] for item in rows["mrq.jsonl"] if item.get("state") != "superseded"}
    already_owned = {item["stable_diff_id"] for item in rows["dispositions.jsonl"] if item.get("primary") and (not item.get("mrq_id") or item.get("mrq_id") in active_ids)}
    if already_owned.intersection(stable_diff_ids):
        raise ValueError("customer DIF already has a primary disposition")
    identifier = mrq_id(semantic_key)
    if any(item["semantic_key"] == semantic_key or item["mrq_id"] == identifier for item in rows["mrq.jsonl"]):
        return identifier
    rows["mrq.jsonl"].append({"schema_version": "1", "mrq_id": identifier, "semantic_key": semantic_key, "title": title, "state": "draft", "source_generation_id": source_id, "diff_generation_id": diff_id, "source_customization": {"business_meaning": business_meaning, "scope": scope, "confidence": confidence, "rationale": rationale, "evidence": evidence}, "migration_decision": {}})
    rows["dispositions.jsonl"].extend({"schema_version": "1", "stable_diff_id": item, "mrq_id": identifier, "primary": True, "source_generation_id": source_id, "diff_generation_id": diff_id} for item in stable_diff_ids)
    rows["dispositions.jsonl"].extend({"schema_version": "1", "stable_diff_id": item, "mrq_id": identifier, "primary": False, "source_generation_id": source_id, "diff_generation_id": diff_id} for item in supporting_diff_ids)
    return identifier


def restructure(rows: ArtifactRows, kind: str, source_ids: list[str], target_proposals: list[TargetProposal], rationale: str, evidence: list[dict[str, JsonValue]], actor: str, source_id: str, diff_id: str) -> list[str]:
    if kind not in {"split", "merge", "supersede"} or not source_ids or len(source_ids) != len(set(source_ids)) or not rationale.strip() or not actor.strip() or not evidence:
        raise ValueError("invalid MRQ restructuring request")
    source_candidates = [next((item for item in rows["mrq.jsonl"] if item["mrq_id"] == identifier), None) for identifier in source_ids]
    if any(item is None or item["state"] == "superseded" for item in source_candidates):
        raise ValueError("MRQ restructuring source is missing or already superseded")
    sources = [item for item in source_candidates if item is not None]
    if kind == "split" and (len(source_ids) != 1 or len(target_proposals) < 2) or kind == "merge" and (len(source_ids) < 2 or len(target_proposals) != 1) or kind == "supersede" and len(target_proposals) > 1:
        raise ValueError(f"invalid {kind} restructuring cardinality")
    prior_primary = {item["stable_diff_id"] for item in rows["dispositions.jsonl"] if item.get("primary") and item.get("mrq_id") in source_ids}
    assigned = [identifier for proposal in target_proposals for identifier in proposal.get("stable_diff_ids", [])]
    if target_proposals and (set(assigned) != prior_primary or len(assigned) != len(set(assigned))):
        raise ValueError("MRQ restructuring must reassign every primary DIF exactly once")
    for item in sources:
        item["state"] = "superseded"
    target_ids: list[str] = []
    for proposal in target_proposals:
        required = {"semantic_key", "title", "stable_diff_ids", "supporting_diff_ids", "evidence", "business_meaning", "scope", "confidence", "rationale"}
        if set(proposal) != required:
            raise ValueError("invalid MRQ restructuring target proposal")
        target_ids.append(propose(rows, proposal["semantic_key"], proposal["title"], source_id, diff_id, proposal["stable_diff_ids"], proposal["supporting_diff_ids"], proposal["evidence"], proposal["business_meaning"], proposal["scope"], proposal["confidence"], proposal["rationale"]))
    lineage_targets = target_ids or source_ids
    rows["lineage.jsonl"].append({"schema_version": "1", "kind": kind, "source_ids": source_ids, "target_ids": lineage_targets, "rationale": rationale, "evidence": evidence, "actor": actor, "source_generation_id": source_id, "diff_generation_id": diff_id})
    return target_ids


def decide(rows: ArtifactRows, identifier: str, decision: str, target_evidence: list[EvidenceRef], target_coverage: list[EvidenceRef], residual_gap: str, target_solution: str, rationale: str, acceptance_criteria: list[str], risk: str, open_questions: list[str]) -> None:
    if decision not in DECISIONS:
        raise ValueError(f"invalid migration decision: {decision}")
    item = next((value for value in rows["mrq.jsonl"] if value["mrq_id"] == identifier), None)
    if item is None or item["state"] not in {"draft", "ready_for_review"} or item.get("migration_decision", {}).get("agreement_status") not in {None, "changes_requested"} or not item["source_customization"].get("evidence"):
        raise ValueError("MRQ is not ready for a migration decision")
    if not target_evidence or not target_coverage or not residual_gap.strip() or not target_solution.strip() or not rationale.strip() or not acceptance_criteria or not risk.strip():
        raise ValueError("incomplete MRQ migration-decision section")
    item["migration_decision"] = {"decision": decision, "target_evidence": target_evidence, "target_coverage": target_coverage, "residual_gap": residual_gap, "target_solution": target_solution, "rationale": rationale, "acceptance_criteria": acceptance_criteria, "risk": risk, "open_questions": open_questions, "agreement_status": "pending_review"}
    item["state"] = "ready_for_review"


def review(rows: ArtifactRows, identifier: str, event: str, actor: str, rationale: str, evidence: list[dict[str, JsonValue]], source_id: str, diff_id: str, timestamp: str, previous_generation: str | None) -> None:
    if event not in {"approve", "request_changes"} or not actor.strip() or not rationale.strip():
        raise ValueError("invalid review event")
    item = next((value for value in rows["mrq.jsonl"] if value["mrq_id"] == identifier), None)
    if item is None or item["state"] != "ready_for_review":
        raise ValueError("MRQ is not ready for review")
    candidate = deepcopy(item); candidate["migration_decision"]["agreement_status"] = "pending_review"
    fingerprint = sha256(canonical_json(candidate))
    rows["approvals.jsonl"].append({"schema_version": "1", "event": event, "target_id": identifier, "actor": actor, "rationale": rationale, "evidence": evidence, "timestamp": timestamp, "previous_generation": previous_generation, "fingerprint": fingerprint, "source_generation_id": source_id, "diff_generation_id": diff_id})
    if event == "approve":
        item["state"] = "approved"; item["migration_decision"]["agreement_status"] = "approved"
    else:
        item["migration_decision"]["agreement_status"] = "changes_requested"


def approve_noise(rows: ArtifactRows, stable_diff_id: str, actor: str, rationale: str, evidence: list[dict[str, JsonValue]], source_id: str, diff_id: str, timestamp: str, previous_generation: str | None) -> None:
    if not stable_diff_id.startswith("DIF-") or not actor.strip() or not rationale.strip() or not evidence:
        raise ValueError("invalid approved-noise disposition")
    if any(item.get("stable_diff_id") == stable_diff_id and item.get("primary") for item in rows["dispositions.jsonl"]):
        raise ValueError("customer DIF already has a primary disposition")
    noise: ApprovedNoise = {"rationale": rationale, "actor": actor, "evidence": evidence}
    rows["dispositions.jsonl"].append({"schema_version": "1", "stable_diff_id": stable_diff_id, "primary": True, "approved_noise": noise, "source_generation_id": source_id, "diff_generation_id": diff_id})
    rows["approvals.jsonl"].append({"schema_version": "1", "event": "approve", "target_id": stable_diff_id, "actor": actor, "rationale": rationale, "evidence": evidence, "timestamp": timestamp, "previous_generation": previous_generation, "fingerprint": sha256(canonical_json(noise)), "source_generation_id": source_id, "diff_generation_id": diff_id})
