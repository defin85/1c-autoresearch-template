from __future__ import annotations

import pytest

from one_c_autoresearch.agents import validate_proposal
from one_c_autoresearch.consolidation import (
    approve_plan,
    ensure_context_payload,
    fingerprint,
    partition_manifest,
    plan_from_groups,
    sentinel,
    validate_plan,
)


def _classifications(count: int = 1) -> list[dict]:
    return [{
        "stable_diff_id": f"DIF-{index}",
        "classification": "meaning",
        "evidence": [{"path": f"{index}.bsl", "fingerprint": "sha256:" + str(index) * 64}],
    } for index in range(1, count + 1)]


def _snapshot(count: int = 1, mrq: dict | None = None) -> dict:
    return {
        "source_fingerprint": "sha256:" + "1" * 64,
        "diff_fingerprint": "sha256:" + "2" * 64,
        "classification_fingerprint": "sha256:" + "3" * 64,
        "classifications": _classifications(count),
        "mrq": mrq or {},
    }


def _agent_groups(*groups: dict) -> list[dict]:
    complete = [{
        "semantic_key": "",
        "title": "",
        "stable_diff_ids": [],
        "supporting_diff_ids": [],
        "component_keys": [],
        "source_mrq_ids": [],
        "evidence": [],
        "business_meaning": "",
        "scope": "",
        "confidence": "",
        "rationale": "",
        "split_source_mrq_id": "",
        **group,
    } for group in groups]
    return validate_proposal(
        "mrq.consolidate", {"groups": complete, "approved_noise": []},
        {"kind": "consolidation-coordinate"},
    )["groups"]


def test_partition_manifest_and_actual_payload_enforce_context_budget() -> None:
    manifest = partition_manifest(
        [{"record_id": f"DIF-{index}", "record_type": "dif", "stable_diff_id": f"DIF-{index}", "payload": "x" * 3000} for index in range(3)],
        8192,
    )
    count = len(manifest["partitions"])
    assert len(manifest["pairs"]) == count * (count + 1) // 2
    with pytest.raises(ValueError, match="context_capacity"):
        ensure_context_payload({"payload": "x" * 10_000}, 8192)


def test_plan_maps_every_meaning_dif_directly_to_mrq() -> None:
    snapshot = _snapshot(2)
    plan = plan_from_groups(
        snapshot,
        [{"semantic_key": "order", "stable_diff_ids": ["DIF-1", "DIF-2"]}],
        [],
        partition_manifest(snapshot["classifications"], 8192),
    )
    validate_plan(plan)
    assert "cus" not in plan
    assert {row["stable_diff_id"] for row in plan["mrq"]["dispositions.jsonl"]} == {"DIF-1", "DIF-2"}
    assert {row["mrq_id"] for row in plan["mrq"]["dispositions.jsonl"]} == {plan["mrq"]["mrq.jsonl"][0]["mrq_id"]}


def test_one_component_hint_can_be_split_into_multiple_ordinary_mrqs() -> None:
    prior = {
        "mrq.jsonl": [
            {"schema_version": "3", "mrq_id": "MRQ-old", "semantic_key": "old"},
        ],
    }
    snapshot = {
        **_snapshot(2, prior),
        "component_groups": [{
            "component_kind": "extension",
            "component_key": "ext",
            "stable_diff_ids": ["DIF-1", "DIF-2"],
        }],
    }
    plan = plan_from_groups(
        snapshot,
        _agent_groups(
            {"semantic_key": "first-function", "stable_diff_ids": ["DIF-1"], "split_source_mrq_id": "MRQ-old"},
            {"semantic_key": "second-function", "stable_diff_ids": ["DIF-2"], "split_source_mrq_id": "MRQ-old"},
        ),
        [],
        partition_manifest(snapshot["classifications"], 8192),
    )
    assert len(plan["mrq"]["mrq.jsonl"]) == 2
    assert {row["stable_diff_id"] for row in plan["mrq"]["dispositions.jsonl"] if row["primary"]} == {"DIF-1", "DIF-2"}
    assert plan["lineage"][0]["kind"] == "split"


def test_existing_mrqs_can_merge_without_intermediate_entity() -> None:
    prior = {
        "mrq.jsonl": [
            {"schema_version": "3", "mrq_id": "MRQ-old-1", "semantic_key": "old-1"},
            {"schema_version": "3", "mrq_id": "MRQ-old-2", "semantic_key": "old-2"},
        ],
    }
    snapshot = _snapshot(2, prior)
    plan = plan_from_groups(
        snapshot,
        _agent_groups({
            "semantic_key": "merged",
            "stable_diff_ids": ["DIF-1", "DIF-2"],
            "source_mrq_ids": ["MRQ-old-1", "MRQ-old-2"],
        }),
        [],
        partition_manifest(snapshot["classifications"], 8192),
    )
    assert plan["outcomes"]["merged"] == ["MRQ-old-1", "MRQ-old-2"]
    assert plan["lineage"][0]["domain"] == "mrq"


def test_cross_component_support_requires_explicit_keys_rationale_and_evidence() -> None:
    snapshot = {
        **_snapshot(2),
        "component_groups": [
            {"component_kind": "extension", "component_key": "ext", "stable_diff_ids": ["DIF-1"]},
            {"component_kind": "external_artifact", "component_key": "file", "stable_diff_ids": ["DIF-2"]},
        ],
    }
    group = {
        "semantic_key": "integration",
        "stable_diff_ids": ["DIF-1"],
        "supporting_diff_ids": ["DIF-2"],
        "component_keys": ["extension:ext", "external_artifact:file"],
        "rationale": "The extension calls the uploaded handler.",
        "evidence": [{"path": "target_cf/extensions/ext/a.bsl", "fingerprint": "sha256:" + "1" * 64}],
    }
    other = {"semantic_key": "handler", "stable_diff_ids": ["DIF-2"]}
    plan = plan_from_groups(snapshot, _agent_groups(group, other), [], partition_manifest(snapshot["classifications"], 8192))
    assert any(not row["primary"] and row["stable_diff_id"] == "DIF-2" for row in plan["mrq"]["dispositions.jsonl"])
    with pytest.raises(ValueError, match="cross-component"):
        plan_from_groups(snapshot, _agent_groups({**group, "component_keys": []}, other), [], partition_manifest(snapshot["classifications"], 8192))


def test_approval_publishes_only_mrq_generation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = _snapshot()
    plan = plan_from_groups(
        snapshot,
        [{"semantic_key": "order", "stable_diff_ids": ["DIF-1"]}],
        [],
        partition_manifest(snapshot["classifications"], 8192),
    )
    monkeypatch.setattr("one_c_autoresearch.consolidation.input_snapshot", lambda _repo: snapshot)
    monkeypatch.setattr("one_c_autoresearch.consolidation._publish_generation", lambda *_args, **_kwargs: "m" * 64)
    approval = {
        "schema_version": "2",
        "kind": "consolidation",
        "actor": "reviewer",
        "rationale": "reviewed",
        "evidence": [],
        **{key: plan["bindings"][key] for key in (
            "source_fingerprint", "diff_fingerprint", "classification_fingerprint", "prior_mrq_fingerprint"
        )},
        "plan_fingerprint": fingerprint(plan),
    }
    pointer = approve_plan(tmp_path, plan, sentinel(), approval)
    assert pointer["state"] == "active"
    assert pointer["mrq_generation_id"] == "m" * 64
    assert all("cus" not in key.lower() for key in pointer)
