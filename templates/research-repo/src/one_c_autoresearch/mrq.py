from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import DECISIONS, MRQ_STATES, atomic_json, canonical_json, mrq_id, repository_lock, require_tracked_clean, sha256, validate_unique_ids


FILES = ("mrq.jsonl", "dispositions.jsonl", "evidence.jsonl", "lineage.jsonl", "approvals.jsonl")
CONFIDENCE = {"low", "medium", "high"}
AGREEMENT_STATES = {"pending_review", "changes_requested", "approved"}


def comparison_epoch_fingerprint(source: dict[str, Any]) -> str:
    if source.get("schema_version") == "2":
        value = str(source.get("source_comparison_epoch_fingerprint", ""))
        if not value.startswith("sha256:"):
            raise ValueError("routed source comparison epoch is missing")
        return value.removeprefix("sha256:")
    return sha256(canonical_json({"acquisition_profile_id": source["acquisition_profile_id"], "normalizer_version": source["normalizer_version"], "representation_schema": source["representation_schema"]}))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("wb") as stream:
        for row in rows:
            stream.write(canonical_json(row) + b"\n")
        stream.flush(); os.fsync(stream.fileno())


def active(repo: Path, *, require_tracked_clean_state: bool = False) -> dict[str, Any]:
    pointer_path = repo / "research/active-generation.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    generation = pointer.get("canonical_generation_id")
    if not generation:
        return {"pointer": pointer, **{name: [] for name in FILES}}
    root = repo / "analysis/migration-requirements/generations" / generation
    if require_tracked_clean_state:
        require_tracked_clean(repo, [root, repo / "research/generations" / generation, pointer_path])
    result = {"pointer": pointer, **{name: _jsonl(root / name) for name in FILES}}
    manifest = json.loads((repo / "research/generations" / generation / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("canonical_generation_id") != generation:
        raise ValueError("canonical generation manifest mismatch")
    source_pointer = json.loads((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    diff_pointer = json.loads((repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
    source_id, diff_id = pointer.get("source_generation_id"), pointer.get("diff_generation_id")
    if source_id != source_pointer.get("generation_id") or diff_id != diff_pointer.get("generation_id") or diff_pointer.get("source_generation_id") != source_id:
        raise ValueError("mixed active generations")
    if manifest.get("source_generation_id") != source_id or manifest.get("diff_generation_id") != diff_id:
        raise ValueError("canonical manifest generation binding mismatch")
    for name in FILES:
        if sha256((root / name).read_bytes()) != manifest["files"][name]:
            raise ValueError(f"canonical artifact hash mismatch: {name}")
    epoch_fingerprint = comparison_epoch_fingerprint(source_pointer)
    if manifest.get("comparison_epoch_fingerprint") != epoch_fingerprint:
        raise ValueError("canonical comparison epoch mismatch")
    preimage = {"schema_version": "1", "source_generation_id": source_id, "diff_generation_id": diff_id, "comparison_epoch_fingerprint": epoch_fingerprint, "files": manifest["files"]}
    if sha256(canonical_json(preimage)) != generation:
        raise ValueError("canonical generation ID mismatch")
    import csv
    with (repo / "analysis/indexes/generations" / str(diff_id) / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
        customer = {row["stable_diff_id"]: row for row in csv.DictReader(stream) if row.get("before_role") == "vendor_baseline" and row.get("after_role") == "target_cf"}
    validate_graph({name: result[name] for name in FILES}, str(pointer.get("source_generation_id")), str(diff_id), customer)
    return result


def validate_graph(rows: dict[str, list[dict[str, Any]]], source_id: str, diff_id: str, valid_customer_diffs: set[str] | dict[str, dict[str, str]] | None = None) -> None:
    mrqs = rows["mrq.jsonl"]
    validate_unique_ids([{"id": item["mrq_id"], "preimage": {"schema_version": item["schema_version"], "semantic_key": item["semantic_key"]}} for item in mrqs], "id", "preimage")
    ids = {item["mrq_id"] for item in mrqs}
    active_ids = {item["mrq_id"] for item in mrqs if item.get("state") != "superseded"}
    semantic_keys = [item["semantic_key"] for item in mrqs]
    if len(semantic_keys) != len(set(semantic_keys)):
        raise ValueError("duplicate MRQ semantic key")
    evidence_ids: set[str] = set()
    runtime_evidence: dict[str, dict[str, Any]] = {}
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
            runtime_evidence[evidence_id] = evidence
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
                fact = valid_customer_diffs.get(evidence["stable_diff_id"])
                expected_fingerprint = fact.get("after_fingerprint") or fact.get("before_fingerprint") if fact else None
                if not fact or evidence["path"] != fact.get("path") or evidence["fingerprint"] != expected_fingerprint:
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
            datetime.fromisoformat(str(event.get("timestamp", "")).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("approval timestamp is invalid") from exc
        if any(len(str(event.get(key, ""))) != 64 for key in ("source_generation_id", "diff_generation_id")):
            raise ValueError("approval generation binding is invalid")
        if event["previous_generation"] is not None and not re.fullmatch(r"[0-9a-f]{64}", str(event["previous_generation"])) or not isinstance(event.get("evidence"), list):
            raise ValueError("approval previous generation and evidence are required")
    superseded: set[str] = set()
    for lineage in rows["lineage.jsonl"]:
        required = {"schema_version", "kind", "source_ids", "target_ids", "rationale", "evidence", "actor", "source_generation_id", "diff_generation_id"}
        sources, targets, kind = lineage.get("source_ids"), lineage.get("target_ids"), lineage.get("kind")
        if set(lineage) != required or lineage.get("schema_version") != "1" or kind not in {"split", "merge", "supersede", "revalidate"}:
            raise ValueError("invalid MRQ lineage fields")
        if not isinstance(sources, list) or not isinstance(targets, list) or not sources or not targets or len(sources) != len(set(sources)) or len(targets) != len(set(targets)) or any(value not in ids for value in sources + targets):
            raise ValueError("MRQ lineage references unknown or duplicate identities")
        if kind == "split" and (len(sources) != 1 or len(targets) < 2) or kind == "merge" and (len(sources) < 2 or len(targets) != 1) or kind == "revalidate" and sources != targets:
            raise ValueError(f"invalid {kind} lineage cardinality")
        if not str(lineage.get("rationale", "")).strip() or not str(lineage.get("actor", "")).strip() or not isinstance(lineage.get("evidence"), list) or not lineage["evidence"]:
            raise ValueError("MRQ lineage rationale, actor, and evidence are required")
        if lineage.get("source_generation_id") != source_id or lineage.get("diff_generation_id") != diff_id:
            raise ValueError("stale MRQ lineage generation binding")
        if kind in {"split", "merge", "supersede"}:
            superseded.update(sources)
    if {item["mrq_id"] for item in mrqs if item["state"] == "superseded"} != superseded:
        raise ValueError("superseded MRQ state requires exact lineage")
    if any(count != 1 for count in ownership.values()):
        raise ValueError("a customer DIF has conflicting primary dispositions")


def publish(repo: Path, rows: dict[str, list[dict[str, Any]]], source_id: str, diff_id: str, epoch: dict[str, str], expected_generation: str | None) -> dict[str, Any]:
    with repository_lock(repo):
        current = json.loads((repo / "research/active-generation.json").read_text(encoding="utf-8"))
        if current.get("canonical_generation_id") != expected_generation:
            raise RuntimeError("stale canonical generation")
        epoch_fingerprint = comparison_epoch_fingerprint(epoch)
        if expected_generation:
            prior_manifest = json.loads((repo / "research/generations" / expected_generation / "manifest.json").read_text(encoding="utf-8"))
            if prior_manifest.get("comparison_epoch_fingerprint") == epoch_fingerprint:
                previous = _jsonl(repo / "analysis/migration-requirements/generations" / expected_generation / "approvals.jsonl")
                if rows["approvals.jsonl"][:len(previous)] != previous:
                    raise ValueError("same-epoch approval ledger prefix was changed")
            elif any(rows[name] for name in FILES):
                raise ValueError("a new comparison epoch must start with empty MRQ ledgers")
        import csv
        diff_pointer = json.loads((repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
        if diff_pointer.get("generation_id") != diff_id or diff_pointer.get("source_generation_id") != source_id:
            raise RuntimeError("stale source or diff generation")
        with (repo / "analysis/indexes/generations" / diff_id / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
            valid_customer_diffs = {row["stable_diff_id"]: row for row in csv.DictReader(stream) if row.get("before_role") == "vendor_baseline" and row.get("after_role") == "target_cf"}
        validate_graph(rows, source_id, diff_id, valid_customer_diffs)
        parent = repo / "analysis/migration-requirements/.staging"; parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            staging = Path(temporary)
            for name in FILES:
                _write_jsonl(staging / name, rows[name] if name == "approvals.jsonl" else sorted(rows[name], key=canonical_json))
            hashes = {name: sha256((staging / name).read_bytes()) for name in FILES}
            preimage = {"schema_version": "1", "source_generation_id": source_id, "diff_generation_id": diff_id, "comparison_epoch_fingerprint": epoch_fingerprint, "files": hashes}
            generation = sha256(canonical_json(preimage))
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
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                if {key: existing.get(key) for key in preimage} != preimage or existing.get("canonical_generation_id") != generation:
                    raise ValueError("existing canonical generation manifest is inconsistent")
            if any(sha256((destination / name).read_bytes()) != digest for name, digest in hashes.items()):
                raise ValueError("existing canonical generation payload is inconsistent")
            pointer = {"schema_version": "1", "canonical_generation_id": generation, "source_generation_id": source_id, "diff_generation_id": diff_id}
            atomic_json(repo / "research/active-generation.json", pointer)
            return pointer


def revalidate_unchanged(repo: Path, new_source: dict[str, Any], new_diff: dict[str, Any], actor: str, rationale: str, timestamp: str) -> dict[str, Any]:
    if not actor.strip() or not rationale.strip() or not timestamp.strip():
        raise ValueError("revalidation actor, rationale, and timestamp are required")
    previous = json.loads((repo / "research/active-generation.json").read_text(encoding="utf-8"))
    if not previous.get("canonical_generation_id"):
        raise ValueError("there is no canonical generation to revalidate")
    generation = previous["canonical_generation_id"]
    root = repo / "analysis/migration-requirements/generations" / generation
    state = {name: _jsonl(root / name) for name in FILES}
    old_manifest = json.loads((repo / "research/generations" / previous["canonical_generation_id"] / "manifest.json").read_text(encoding="utf-8"))
    if old_manifest.get("comparison_epoch_fingerprint") != comparison_epoch_fingerprint(new_source):
        raise ValueError("a new comparison epoch cannot revalidate prior MRQ state")
    rows = {name: deepcopy(state[name]) for name in FILES}
    import csv
    with (repo / "analysis/indexes/generations" / previous["diff_generation_id"] / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
        old_all = {item["stable_diff_id"]: item for item in csv.DictReader(stream)}
    old_facts = {key: item for key, item in old_all.items() if item.get("before_role") == "vendor_baseline" and item.get("after_role") == "target_cf"}
    with (repo / "analysis/indexes/generations" / new_diff["generation_id"] / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
        new_all = {item["stable_diff_id"]: item for item in csv.DictReader(stream)}
    facts = {key: item for key, item in new_all.items() if item.get("before_role") == "vendor_baseline" and item.get("after_role") == "target_cf"}
    retained = [item for item in rows["dispositions.jsonl"] if item.get("stable_diff_id") in facts and (item.get("mrq_id") or old_facts.get(item["stable_diff_id"], {}).get("content_fingerprint") == facts[item["stable_diff_id"]].get("content_fingerprint"))]
    for relation in retained:
        identifier = relation["stable_diff_id"]
        if old_facts.get(identifier, {}).get("content_fingerprint") != facts[identifier].get("content_fingerprint"):
            raise ValueError(f"changed DIF requires an explicit updated MRQ proposal: {identifier}")
    owned = {item["stable_diff_id"] for item in retained if item.get("mrq_id")}
    missing = [item["mrq_id"] for item in rows["mrq.jsonl"] if any(relation.get("mrq_id") == item["mrq_id"] and relation["stable_diff_id"] not in owned for relation in rows["dispositions.jsonl"])]
    if missing:
        raise ValueError(f"disappeared MRQ meaning requires explicit supersede evidence: {missing[0]}")
    rows["dispositions.jsonl"] = retained
    with (repo / "analysis/indexes/generations" / new_diff["generation_id"] / "target-coverage.csv").open(encoding="utf-8", newline="") as stream:
        coverage = {item["customer_diff_id"]: {key: item[key] for key in ("customer_diff_id", "target_diff_ids", "coverage_status", "evidence_ref")} for item in csv.DictReader(stream)}
    for item in rows["mrq.jsonl"]:
        item["source_generation_id"] = new_source["generation_id"]; item["diff_generation_id"] = new_diff["generation_id"]
        refreshed = []
        for evidence in item.get("source_customization", {}).get("evidence", []):
            fact = facts.get(evidence.get("stable_diff_id"))
            if fact and evidence.get("path") == fact.get("path"):
                refreshed.append({**evidence, "fingerprint": fact.get("after_fingerprint") or fact.get("before_fingerprint")})
        item.setdefault("source_customization", {})["evidence"] = refreshed
        if not refreshed:
            raise ValueError(f"MRQ source evidence requires an explicit update: {item['mrq_id']}")
        for evidence in item.get("migration_decision", {}).get("target_evidence", []):
            identifier = evidence.get("stable_diff_id")
            if identifier and (identifier not in new_all or old_all.get(identifier, {}).get("content_fingerprint") != new_all[identifier].get("content_fingerprint")):
                raise ValueError(f"MRQ target evidence requires an explicit update: {item['mrq_id']}")
            if evidence.get("source_generation_id"): evidence["source_generation_id"] = new_source["generation_id"]
        current_coverage = item.get("migration_decision", {}).get("target_coverage", [])
        refreshed_coverage = [coverage.get(value.get("customer_diff_id")) for value in current_coverage]
        if any(value is None or {key: old.get(key, "") for key in value} != value for old, value in zip(current_coverage, refreshed_coverage)):
            raise ValueError(f"MRQ target coverage requires an explicit update: {item['mrq_id']}")
        item.get("migration_decision", {})["target_coverage"] = refreshed_coverage
        rows["lineage.jsonl"].append({"schema_version": "1", "kind": "revalidate", "source_ids": [item["mrq_id"]], "target_ids": [item["mrq_id"]], "rationale": rationale, "evidence": [{"old_source_generation_id": previous["source_generation_id"], "new_source_generation_id": new_source["generation_id"], "old_diff_generation_id": previous["diff_generation_id"], "new_diff_generation_id": new_diff["generation_id"], "affected_fields": ["source_generation_id", "diff_generation_id"]}], "actor": actor, "source_generation_id": new_source["generation_id"], "diff_generation_id": new_diff["generation_id"]})
        if item.get("state") == "approved":
            candidate = deepcopy(item); candidate["state"] = "ready_for_review"; candidate["migration_decision"]["agreement_status"] = "pending_review"
            rows["approvals.jsonl"].append({"schema_version": "1", "event": "approve", "target_id": item["mrq_id"], "actor": actor, "rationale": rationale, "evidence": item["source_customization"]["evidence"], "timestamp": timestamp, "previous_generation": generation, "fingerprint": sha256(canonical_json(candidate)), "source_generation_id": new_source["generation_id"], "diff_generation_id": new_diff["generation_id"]})
    for relation in rows["dispositions.jsonl"]:
        relation["source_generation_id"] = new_source["generation_id"]; relation["diff_generation_id"] = new_diff["generation_id"]
        noise = relation.get("approved_noise")
        if noise:
            rows["approvals.jsonl"].append({"schema_version": "1", "event": "approve", "target_id": relation["stable_diff_id"], "actor": actor, "rationale": rationale, "evidence": noise["evidence"], "timestamp": timestamp, "previous_generation": generation, "fingerprint": sha256(canonical_json(noise)), "source_generation_id": new_source["generation_id"], "diff_generation_id": new_diff["generation_id"]})
    return publish(repo, rows, new_source["generation_id"], new_diff["generation_id"], new_source, previous["canonical_generation_id"])


def propose(rows: dict[str, list[dict[str, Any]]], semantic_key: str, title: str, source_id: str, diff_id: str, stable_diff_ids: list[str], supporting_diff_ids: list[str], evidence: list[dict[str, Any]], business_meaning: str, scope: str, confidence: str, rationale: str) -> str:
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


def restructure(rows: dict[str, list[dict[str, Any]]], kind: str, source_ids: list[str], target_proposals: list[dict[str, Any]], rationale: str, evidence: list[dict[str, Any]], actor: str, source_id: str, diff_id: str) -> list[str]:
    if kind not in {"split", "merge", "supersede"} or not source_ids or len(source_ids) != len(set(source_ids)) or not rationale.strip() or not actor.strip() or not evidence:
        raise ValueError("invalid MRQ restructuring request")
    sources = [next((item for item in rows["mrq.jsonl"] if item["mrq_id"] == identifier), None) for identifier in source_ids]
    if any(item is None or item["state"] == "superseded" for item in sources):
        raise ValueError("MRQ restructuring source is missing or already superseded")
    if kind == "split" and (len(source_ids) != 1 or len(target_proposals) < 2) or kind == "merge" and (len(source_ids) < 2 or len(target_proposals) != 1) or kind == "supersede" and len(target_proposals) > 1:
        raise ValueError(f"invalid {kind} restructuring cardinality")
    prior_primary = {item["stable_diff_id"] for item in rows["dispositions.jsonl"] if item.get("primary") and item.get("mrq_id") in source_ids}
    assigned = [identifier for proposal in target_proposals for identifier in proposal.get("stable_diff_ids", [])]
    if target_proposals and (set(assigned) != prior_primary or len(assigned) != len(set(assigned))):
        raise ValueError("MRQ restructuring must reassign every primary DIF exactly once")
    for item in sources:
        item["state"] = "superseded"
    target_ids = []
    for proposal in target_proposals:
        required = {"semantic_key", "title", "stable_diff_ids", "supporting_diff_ids", "evidence", "business_meaning", "scope", "confidence", "rationale"}
        if set(proposal) != required:
            raise ValueError("invalid MRQ restructuring target proposal")
        target_ids.append(propose(rows, proposal["semantic_key"], proposal["title"], source_id, diff_id, proposal["stable_diff_ids"], proposal["supporting_diff_ids"], proposal["evidence"], proposal["business_meaning"], proposal["scope"], proposal["confidence"], proposal["rationale"]))
    lineage_targets = target_ids or source_ids
    rows["lineage.jsonl"].append({"schema_version": "1", "kind": kind, "source_ids": source_ids, "target_ids": lineage_targets, "rationale": rationale, "evidence": evidence, "actor": actor, "source_generation_id": source_id, "diff_generation_id": diff_id})
    return target_ids


def decide(rows: dict[str, list[dict[str, Any]]], identifier: str, decision: str, target_evidence: list[dict[str, Any]], target_coverage: list[dict[str, Any]], residual_gap: str, target_solution: str, rationale: str, acceptance_criteria: list[str], risk: str, open_questions: list[str]) -> None:
    if decision not in DECISIONS:
        raise ValueError(f"invalid migration decision: {decision}")
    item = next((value for value in rows["mrq.jsonl"] if value["mrq_id"] == identifier), None)
    if item is None or item["state"] not in {"draft", "ready_for_review"} or item.get("migration_decision", {}).get("agreement_status") not in {None, "changes_requested"} or not item["source_customization"].get("evidence"):
        raise ValueError("MRQ is not ready for a migration decision")
    if not target_evidence or not target_coverage or not residual_gap.strip() or not target_solution.strip() or not rationale.strip() or not acceptance_criteria or not risk.strip() or not isinstance(open_questions, list):
        raise ValueError("incomplete MRQ migration-decision section")
    item["migration_decision"] = {"decision": decision, "target_evidence": target_evidence, "target_coverage": target_coverage, "residual_gap": residual_gap, "target_solution": target_solution, "rationale": rationale, "acceptance_criteria": acceptance_criteria, "risk": risk, "open_questions": open_questions, "agreement_status": "pending_review"}
    item["state"] = "ready_for_review"


def review(rows: dict[str, list[dict[str, Any]]], identifier: str, event: str, actor: str, rationale: str, evidence: list[dict[str, Any]], source_id: str, diff_id: str, timestamp: str, previous_generation: str | None) -> None:
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


def approve_noise(rows: dict[str, list[dict[str, Any]]], stable_diff_id: str, actor: str, rationale: str, evidence: list[dict[str, Any]], source_id: str, diff_id: str, timestamp: str, previous_generation: str | None) -> None:
    if not stable_diff_id.startswith("DIF-") or not actor.strip() or not rationale.strip() or not evidence:
        raise ValueError("invalid approved-noise disposition")
    if any(item.get("stable_diff_id") == stable_diff_id and item.get("primary") for item in rows["dispositions.jsonl"]):
        raise ValueError("customer DIF already has a primary disposition")
    noise = {"rationale": rationale, "actor": actor, "evidence": evidence}
    rows["dispositions.jsonl"].append({"schema_version": "1", "stable_diff_id": stable_diff_id, "primary": True, "approved_noise": noise, "source_generation_id": source_id, "diff_generation_id": diff_id})
    rows["approvals.jsonl"].append({"schema_version": "1", "event": "approve", "target_id": stable_diff_id, "actor": actor, "rationale": rationale, "evidence": evidence, "timestamp": timestamp, "previous_generation": previous_generation, "fingerprint": sha256(canonical_json(noise)), "source_generation_id": source_id, "diff_generation_id": diff_id})
