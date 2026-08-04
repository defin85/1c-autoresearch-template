from __future__ import annotations

import os
import re
import tempfile
from copy import deepcopy
from collections.abc import Iterable
from pathlib import Path

from .contracts import JsonValue, atomic_json, canonical_json, json_object, parse_json_object, repository_lock, sha256


ROOT = Path("analysis/migration-requirements/decision-generations")
FILES = ("decisions.jsonl",)
DECISIONS = {"adopt_vendor", "adapt", "retain_custom", "out_of_scope"}
DECISION_FIELDS = {
    "decision", "agreement_status", "target_evidence", "target_coverage",
    "residual_gap", "target_solution", "rationale", "acceptance_criteria",
    "risk", "open_questions",
}


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def _rows(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("expected row array")
    return [row for row in value if isinstance(row, dict)]


def _string(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def fingerprint(value: object) -> str:
    return "sha256:" + sha256(canonical_json(value))


def consolidation_input_fingerprint(pointer: dict[str, JsonValue]) -> str:
    if pointer.get("state") != "active":
        raise ValueError("decisions require active consolidation")
    return fingerprint({
        key: pointer[key]
        for key in (
            "mrq_generation_id", "source_fingerprint",
            "diff_fingerprint", "classification_fingerprint",
            "plan_fingerprint", "transaction_id",
        )
    })


def _write_jsonl(path: Path, rows: Iterable[dict[str, JsonValue]]) -> None:
    with path.open("wb") as stream:
        for row in sorted(rows, key=canonical_json):
            _ = stream.write(canonical_json(row) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _validate_rows(rows: list[dict[str, JsonValue]]) -> None:
    seen: set[str] = set()
    for row in rows:
        required = {
            "schema_version", "mrq_id", "decision", "approval_fingerprint",
            "provenance",
        }
        if set(row) != required or row["schema_version"] != "1":
            raise ValueError("invalid decision row")
        mrq_id = str(row["mrq_id"])
        decision = row["decision"]
        if (
            not mrq_id.startswith("MRQ-")
            or mrq_id in seen
            or not isinstance(decision, dict)
            or set(decision) != DECISION_FIELDS
            or decision.get("decision") not in DECISIONS
            or decision.get("agreement_status") != "approved"
            or not str(decision.get("rationale", "")).strip()
            or not isinstance(decision.get("target_evidence"), list)
            or not isinstance(decision.get("target_coverage"), list)
            or not isinstance(decision.get("acceptance_criteria"), list)
            or not isinstance(decision.get("open_questions"), list)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(row["approval_fingerprint"]))
            or not isinstance(row["provenance"], dict)
            or row["provenance"].get("kind") not in {"stage-5", "legacy-v1"}
        ):
            raise ValueError("invalid decision row")
        seen.add(mrq_id)


def validate_generation(
    repo: Path, generation_id: str, *, allowed_mrq_ids: set[str] | None = None,
) -> dict[str, JsonValue]:
    root = repo / ROOT / generation_id
    manifest = parse_json_object((root / "manifest.json").read_text(encoding="utf-8"))
    payload = (root / FILES[0]).read_bytes()
    rows = [parse_json_object(line.decode()) for line in payload.splitlines()]
    if payload != b"".join(canonical_json(row) + b"\n" for row in rows):
        raise ValueError("non-canonical decision generation")
    if rows != sorted(rows, key=canonical_json):
        raise ValueError("unsorted decision generation")
    _validate_rows(rows)
    if allowed_mrq_ids is not None and any(row["mrq_id"] not in allowed_mrq_ids for row in rows):
        raise ValueError("decision references inactive MRQ")
    preimage = {
        "schema_version": "1",
        "kind": "target-decisions",
        "files": {FILES[0]: sha256(payload)},
        "row_counts": {FILES[0]: len(rows)},
        "input_fingerprint": manifest.get("input_fingerprint"),
        "decision_fingerprint": fingerprint(rows),
        "approval_fingerprint": manifest.get("approval_fingerprint"),
        "row_approval_fingerprints": {
            row["mrq_id"]: row["approval_fingerprint"] for row in rows
        },
    }
    if (
        set(manifest) != set(preimage) | {"generation_id"}
        or manifest != {**preimage, "generation_id": generation_id}
        or generation_id != sha256(canonical_json(preimage))
    ):
        raise ValueError("invalid decision generation manifest")
    return {"manifest": manifest, FILES[0]: rows}


def create_generation(
    repo: Path,
    rows: list[dict[str, JsonValue]],
    *,
    input_fingerprint: str,
    approval_fingerprint: str,
) -> dict[str, JsonValue]:
    """Пишет неизменяемое поколение; активную привязку меняет вызывающий CAS."""

    _validate_rows(rows)
    decision_fingerprint = fingerprint(rows)
    preimage = {
        "schema_version": "1",
        "kind": "target-decisions",
        "files": {},
        "row_counts": {FILES[0]: len(rows)},
        "input_fingerprint": input_fingerprint,
        "decision_fingerprint": decision_fingerprint,
        "approval_fingerprint": approval_fingerprint,
        "row_approval_fingerprints": {
            row["mrq_id"]: row["approval_fingerprint"] for row in rows
        },
    }
    parent = repo / ROOT
    staging = parent / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=staging) as temporary:
        root = Path(temporary)
        _write_jsonl(root / FILES[0], rows)
        preimage["files"] = {FILES[0]: sha256((root / FILES[0]).read_bytes())}
        generation_id = sha256(canonical_json(preimage))
        atomic_json(root / "manifest.json", {**preimage, "generation_id": generation_id})
        destination = parent / generation_id
        if not destination.exists():
            os.replace(root, destination)
    return validate_generation(repo, generation_id)


def publish(
    repo: Path,
    rows: list[dict[str, JsonValue]],
    approval: dict[str, JsonValue],
    *,
    expected_transaction_id: str,
) -> dict[str, JsonValue]:
    from .consolidation import load_active

    _validate_rows(rows)
    with repository_lock(repo):
        active = json_object(load_active(repo))
        pointer = json_object(active["pointer"])
        if pointer["transaction_id"] != expected_transaction_id:
            raise RuntimeError("stale consolidation transaction")
        input_fingerprint = consolidation_input_fingerprint(pointer)
        decision_fingerprint = fingerprint(rows)
        required = {
            "schema_version", "kind", "actor", "rationale", "evidence",
            "input_fingerprint", "decision_fingerprint",
            "row_approval_fingerprints",
        }
        if (
            set(approval) != required
            or approval["schema_version"] != "1"
            or approval["kind"] != "target-decisions"
            or not str(approval["actor"]).strip()
            or not str(approval["rationale"]).strip()
            or not isinstance(approval["evidence"], list)
            or approval["input_fingerprint"] != input_fingerprint
            or approval["decision_fingerprint"] != decision_fingerprint
            or approval["row_approval_fingerprints"] != {
                row["mrq_id"]: row["approval_fingerprint"] for row in rows
            }
        ):
            raise ValueError("invalid or stale decision approval")

        if pointer.get("decision_input_fingerprint") == input_fingerprint:
            existing = validate_generation(repo, _string(pointer["decision_generation_id"]))
            existing_rows = {_string(row["mrq_id"]): row for row in _rows(existing[FILES[0]])}
            requested_rows = {_string(row["mrq_id"]): row for row in rows}
            if _object(existing["manifest"])["decision_fingerprint"] == decision_fingerprint:
                return {"pointer": pointer, "generation": existing, "idempotent": True}
            if any(requested_rows.get(mrq_id) != row for mrq_id, row in existing_rows.items()):
                raise RuntimeError("published decisions are immutable")

        generation = create_generation(
            repo,
            rows,
            input_fingerprint=input_fingerprint,
            approval_fingerprint=fingerprint(approval),
        )
        generation_id = _string(_object(generation["manifest"])["generation_id"])

        # Lazy import avoids making consolidation depend on stage-5 payload semantics.
        from .consolidation import replace_downstream_binding
        updated = replace_downstream_binding(
            repo, "decision", generation_id, input_fingerprint,
            expected_transaction_id=expected_transaction_id,
            expected_generation_id=_string(pointer["decision_generation_id"]) if pointer.get("decision_generation_id") is not None else None,
            already_locked=True,
        )
        return {"pointer": updated, "generation": generation, "idempotent": False}


def extract_legacy(
    legacy: dict[str, list[dict[str, JsonValue]]],
    current: dict[str, list[dict[str, JsonValue]]],
    retained_or_revalidated: set[str],
    *,
    source_generation_id: str,
    diff_generation_id: str,
) -> dict[str, list[dict[str, JsonValue]]]:
    current_ids = {_string(row["mrq_id"]) for row in current["mrq.jsonl"]}
    old_closure: dict[str, set[str]] = {}
    for relation in legacy["dispositions.jsonl"]:
        if relation.get("primary") and relation.get("mrq_id"):
            old_closure.setdefault(_string(relation["mrq_id"]), set()).add(_string(relation["stable_diff_id"]))
    new_closure: dict[str, set[str]] = {}
    for relation in current["dispositions.jsonl"]:
        if relation.get("primary"):
            new_closure.setdefault(_string(relation["mrq_id"]), set()).add(_string(relation["stable_diff_id"]))

    approvals = legacy["approvals.jsonl"]
    carried: list[dict[str, JsonValue]] = []
    stale: list[dict[str, JsonValue]] = []
    for item in legacy["mrq.jsonl"]:
        decision = _object(item.get("migration_decision", {}))
        if not decision.get("decision"):
            continue
        candidate = deepcopy(item)
        candidate["state"] = "ready_for_review"
        _object(candidate["migration_decision"])["agreement_status"] = "pending_review"
        approval_hash = sha256(canonical_json(candidate))
        approval = next((
            row for row in approvals
            if row.get("event") == "approve"
            and row.get("target_id") == item.get("mrq_id")
            and row.get("fingerprint") == approval_hash
            and row.get("source_generation_id") == source_generation_id
            and row.get("diff_generation_id") == diff_generation_id
        ), None)
        reasons: list[JsonValue] = []
        mrq_id = _string(item["mrq_id"])
        if mrq_id not in current_ids or mrq_id not in retained_or_revalidated:
            reasons.append("identity_not_retained_or_revalidated")
        if old_closure.get(mrq_id, set()) != new_closure.get(mrq_id, set()):
            reasons.append("primary_closure_changed")
        if (
            item.get("state") != "approved"
            or decision.get("agreement_status") != "approved"
            or approval is None
        ):
            reasons.append("approval_mismatch")
        if (
            item.get("source_generation_id") != source_generation_id
            or item.get("diff_generation_id") != diff_generation_id
        ):
            reasons.append("normalized_inputs_changed")
        if reasons:
            stale.append({"mrq_id": mrq_id, "reasons": reasons})
            continue
        carried.append({
            "schema_version": "1",
            "mrq_id": mrq_id,
            "decision": decision,
            "approval_fingerprint": "sha256:" + approval_hash,
            "provenance": {
                "kind": "legacy-v1",
                "source_generation_id": source_generation_id,
                "diff_generation_id": diff_generation_id,
            },
        })
    return {
        "carried": sorted(carried, key=canonical_json),
        "stale": sorted(stale, key=canonical_json),
    }
