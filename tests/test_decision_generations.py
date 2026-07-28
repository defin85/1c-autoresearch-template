from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from one_c_autoresearch.contracts import canonical_json, sha256
from one_c_autoresearch.decision_generations import (
    consolidation_input_fingerprint,
    extract_legacy,
    fingerprint,
    publish,
)


def _pointer() -> dict:
    return {
        "schema_version": "2", "state": "active",
        "mrq_generation_id": "m" * 64,
        "source_fingerprint": "sha256:" + "1" * 64,
        "diff_fingerprint": "sha256:" + "2" * 64,
        "classification_fingerprint": "sha256:" + "3" * 64,
        "plan_fingerprint": "sha256:" + "4" * 64,
        "transaction_id": "t" * 64,
        "batch_generation_id": "b" * 64,
        "batch_input_fingerprint": "sha256:" + "5" * 64,
        "decision_generation_id": None, "decision_input_fingerprint": None,
    }


def _decision(mrq_id: str = "MRQ-1") -> dict:
    return {
        "schema_version": "1", "mrq_id": mrq_id,
        "decision": {
            "decision": "adapt", "target_evidence": [{"path": "target.bsl"}],
            "target_coverage": [{"coverage_status": "still_required"}],
            "residual_gap": "gap", "target_solution": "change target",
            "rationale": "required", "acceptance_criteria": ["works"],
            "risk": "low", "open_questions": [], "agreement_status": "approved",
        },
        "approval_fingerprint": "sha256:" + "a" * 64,
        "provenance": {"kind": "stage-5"},
    }


def test_publish_is_cas_idempotent_and_preserves_other_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pointer = _pointer()
    monkeypatch.setattr(
        "one_c_autoresearch.consolidation.load_active",
        lambda repo: {"pointer": deepcopy(pointer)},
    )

    def replace(repo, kind, generation_id, input_fingerprint, *, expected_transaction_id, expected_generation_id, already_locked):
        assert kind == "decision"
        assert expected_transaction_id == pointer["transaction_id"]
        assert expected_generation_id == pointer.get("decision_generation_id")
        assert already_locked
        pointer["decision_generation_id"] = generation_id
        pointer["decision_input_fingerprint"] = input_fingerprint
        return deepcopy(pointer)

    monkeypatch.setattr(
        "one_c_autoresearch.consolidation.replace_downstream_binding", replace,
    )
    rows = [_decision()]
    approval = {
        "schema_version": "1", "kind": "target-decisions",
        "actor": "reviewer", "rationale": "approved", "evidence": [],
        "input_fingerprint": consolidation_input_fingerprint(pointer),
        "decision_fingerprint": fingerprint(rows),
        "row_approval_fingerprints": {
            row["mrq_id"]: row["approval_fingerprint"] for row in rows
        },
    }
    first = publish(
        tmp_path, rows, approval,
        expected_transaction_id=pointer["transaction_id"],
    )
    second = publish(
        tmp_path, rows, approval,
        expected_transaction_id=pointer["transaction_id"],
    )
    assert not first["idempotent"] and second["idempotent"]
    assert second["pointer"]["batch_generation_id"] == "b" * 64
    assert second["pointer"]["mrq_generation_id"] == "m" * 64
    accumulated = sorted([_decision(), _decision("MRQ-2")], key=canonical_json)
    accumulated_approval = {
        **approval,
        "decision_fingerprint": fingerprint(accumulated),
        "row_approval_fingerprints": {
            row["mrq_id"]: row["approval_fingerprint"] for row in accumulated
        },
    }
    third = publish(
        tmp_path, accumulated, accumulated_approval,
        expected_transaction_id=pointer["transaction_id"],
    )
    assert not third["idempotent"]
    assert len(third["generation"]["decisions.jsonl"]) == 2


def test_publish_rejects_stale_or_different_second_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pointer = _pointer()
    monkeypatch.setattr(
        "one_c_autoresearch.consolidation.load_active",
        lambda repo: {"pointer": deepcopy(pointer)},
    )
    rows = [_decision()]
    approval = {
        "schema_version": "1", "kind": "target-decisions",
        "actor": "reviewer", "rationale": "approved", "evidence": [],
        "input_fingerprint": consolidation_input_fingerprint(pointer),
        "decision_fingerprint": fingerprint(rows),
        "row_approval_fingerprints": {
            row["mrq_id"]: row["approval_fingerprint"] for row in rows
        },
    }
    with pytest.raises(RuntimeError, match="stale"):
        publish(tmp_path, rows, approval, expected_transaction_id="wrong")


def _legacy() -> tuple[dict, dict, str, str]:
    source, diff = "s" * 64, "d" * 64
    item = {
        "schema_version": "1", "mrq_id": "MRQ-1", "state": "approved",
        "source_generation_id": source, "diff_generation_id": diff,
        "migration_decision": {
            **_decision()["decision"],
            "agreement_status": "approved",
        },
    }
    candidate = deepcopy(item)
    candidate["state"] = "ready_for_review"
    candidate["migration_decision"]["agreement_status"] = "pending_review"
    approval_hash = sha256(canonical_json(candidate))
    legacy = {
        "mrq.jsonl": [item],
        "dispositions.jsonl": [{
            "mrq_id": "MRQ-1", "stable_diff_id": "DIF-1", "primary": True,
        }],
        "approvals.jsonl": [{
            "event": "approve", "target_id": "MRQ-1",
            "fingerprint": approval_hash,
            "source_generation_id": source, "diff_generation_id": diff,
        }],
    }
    current = {
        "mrq.jsonl": [{"mrq_id": "MRQ-1"}],
        "dispositions.jsonl": [{
            "mrq_id": "MRQ-1", "stable_diff_id": "DIF-1", "primary": True,
        }],
    }
    return legacy, current, source, diff


def test_legacy_carry_requires_exact_closure_approval_and_inputs() -> None:
    legacy, current, source, diff = _legacy()
    result = extract_legacy(
        legacy, current, {"MRQ-1"},
        source_generation_id=source, diff_generation_id=diff,
    )
    assert [row["mrq_id"] for row in result["carried"]] == ["MRQ-1"]
    assert result["stale"] == []
    assert result["carried"][0]["provenance"]["kind"] == "legacy-v1"

    changed = deepcopy(current)
    changed["dispositions.jsonl"][0]["stable_diff_id"] = "DIF-2"
    result = extract_legacy(
        legacy, changed, {"MRQ-1"},
        source_generation_id=source, diff_generation_id=diff,
    )
    assert result["carried"] == []
    assert result["stale"] == [{
        "mrq_id": "MRQ-1", "reasons": ["primary_closure_changed"],
    }]
