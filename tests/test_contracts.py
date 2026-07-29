import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from one_c_autoresearch import agents
from one_c_autoresearch.agents import (
    CONTEXT_CONTRACT_VERSION,
    MAX_RESPONSE_GROUPS,
    MAX_RESPONSE_ITEMS,
    STRUCTURED_RESPONSE_RESERVE_BYTES,
    build_context_manifest,
    prepare_context_envelope,
    proposal_schema,
    validate_proposal,
)
from one_c_autoresearch.contracts import reject_secrets


def test_context_token_limit_is_not_treated_as_secret():
    reject_secrets({"profiles": {"local": {"input_context_tokens": 272000}}}, "execution snapshot")

    with pytest.raises(ValueError, match="profiles.local.api_token"):
        reject_secrets({"profiles": {"local": {"api_token": "secret"}}}, "execution snapshot")


def test_direct_dif_classification_proposal_contract():
    proposal = {
        "classification": "meaning",
        "semantic_hints": ["Документы"],
        "evidence": [{"path": "Documents/Order.xml", "fingerprint": "sha256:x", "stable_diff_id": "DIF-1"}],
        "rationale": "Изменено прикладное поведение",
    }
    assert validate_proposal("dif.classify-next", proposal, {"id": "DIF-1"}) == proposal


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        ("consolidation-partition", {}),
        ("consolidation-coordinate", {"approved_noise": ["DIF-NOISE"]}),
    ],
)
def test_execute_supports_both_consolidation_result_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, extra: dict[str, object],
) -> None:
    group = {
        "semantic_key": "feature.orders",
        "title": "Orders",
        "stable_diff_ids": ["DIF-MEANING"],
        "supporting_diff_ids": [],
        "component_keys": ["extension:ext"],
        "source_mrq_ids": [],
        "evidence": [{"path": "Documents/Order.xml", "fingerprint": "sha256:x", "stable_diff_id": "DIF-MEANING"}],
        "business_meaning": "Order processing",
        "scope": "Documents",
        "confidence": "high",
        "rationale": "Same business behavior",
        "split_source_mrq_id": "",
    }
    payload = {"groups": [group], **extra}
    profile = {
        "instructions_version": "1",
        "environment_preset": "local-read-only",
        "model": "test",
        "reasoning_effort": "low",
        "input_context_tokens": 8192,
        "context_estimator_version": "utf8-v1",
        "capability_fingerprint": "sha256:test",
    }
    snapshot = {
        "operation": "mrq.consolidate",
        "profiles": {"selected": profile},
        "environment": {"executable": "/bin/true"},
        "context_manifest": {},
    }
    monkeypatch.setattr(agents, "validate_execution_snapshot", lambda *_args: None)
    monkeypatch.setattr(agents, "verify_context_manifest", lambda *_args: None)

    def run(_runner, command, **_kwargs):
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps(payload), encoding="utf-8")
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text(encoding="utf-8"))
        assert set(schema["required"]) == set(payload)
        group_schema = schema["properties"]["groups"]["items"]["properties"]
        assert group_schema["component_keys"]["type"] == "array"
        assert group_schema["source_mrq_ids"]["type"] == "array"
        assert group_schema["split_source_mrq_id"]["type"] == "string"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(agents, "_run_command", run)
    result = agents.execute(
        tmp_path, tmp_path / f"proposal-{kind}", profile, "mrq.consolidate",
        {"id": kind, "kind": kind, "allowed_paths": []}, "", 30, lambda: False,
        execution_snapshot=snapshot,
    )
    assert result == payload


def test_context_envelope_is_deterministic_budgeted_and_content_free(
    tmp_path: Path,
) -> None:
    work_unit = {
        "id": "DIF-1",
        "kind": "customer-diff",
        "diff": {"stable_diff_id": "DIF-1", "title": "secret business text"},
        "allowed_paths": [],
    }
    profile = {
        "instructions_version": "1",
        "input_context_tokens": 8192,
        "context_estimator_version": "utf8-v1",
        "capability_fingerprint": "sha256:profile",
    }
    snapshot = {"subject_bindings": {"source_generation_id": "source-1"}}
    manifest = build_context_manifest(tmp_path, work_unit)

    first = prepare_context_envelope(
        tmp_path, profile, "dif.classify-next", "analyze-dif", "analyzer",
        work_unit, "", snapshot, manifest,
    )
    second = prepare_context_envelope(
        tmp_path, profile, "dif.classify-next", "analyze-dif", "analyzer",
        work_unit, "", snapshot, manifest,
    )

    assert first == second
    assert first["envelope"]["contract_version"] == CONTEXT_CONTRACT_VERSION
    assert first["diagnostics"]["budget_truncation_count"] == 0
    assert first["diagnostics"]["headroom_bytes"] >= 0
    assert "secret business text" not in json.dumps(first["provenance"])
    assert {row["selection_reason"] for row in first["provenance"]} == {
        "primary_subject", "stage_policy",
    }


def test_context_envelope_fails_closed_for_missing_or_small_capacity(
    tmp_path: Path,
) -> None:
    unit = {"id": "DIF-1", "kind": "customer-diff", "allowed_paths": []}
    manifest = build_context_manifest(tmp_path, unit)
    base = {
        "instructions_version": "1",
        "context_estimator_version": "utf8-v1",
        "capability_fingerprint": "sha256:profile",
    }
    for profile in (base, {**base, "input_context_tokens": 4096}):
        with pytest.raises(ValueError, match="capacity"):
            prepare_context_envelope(
                tmp_path, profile, "dif.classify-next", "analyze-dif",
                "analyzer", unit, "", {}, manifest,
            )


def test_response_schemas_have_finite_bounds() -> None:
    schema = proposal_schema(
        "mrq.consolidate",
        {"id": "leaf", "kind": "consolidation-partition"},
    )
    groups = schema["properties"]["groups"]
    assert groups["maxItems"] == MAX_RESPONSE_GROUPS
    for field in groups["items"]["properties"].values():
        if field["type"] == "array":
            assert field["maxItems"] == MAX_RESPONSE_ITEMS
        elif field["type"] == "string":
            assert field["maxLength"] > 0
    assert STRUCTURED_RESPONSE_RESERVE_BYTES == 8192


def test_every_response_schema_maximum_fits_reserved_bytes() -> None:
    def maximum(schema):
        kind = schema["type"]
        if kind == "string":
            return "x" * schema["maxLength"]
        if kind == "boolean":
            return True
        if kind == "array":
            return [maximum(schema["items"])] * schema["maxItems"]
        return {
            key: maximum(schema["properties"][key])
            for key in schema["required"]
        }

    cases = [
        ("dif.classify-next", "dif"),
        ("mrq.discover-next", "coordinate-groups"),
        ("mrq.classify-batches", "batch"),
        ("mrq.decide-next", "target"),
        ("mrq.consolidate", "consolidation-partition"),
        ("mrq.consolidate", "consolidation-link-page"),
        ("mrq.consolidate", "consolidation-reduce-pair"),
        ("mrq.consolidate", "consolidation-noise-page"),
    ]
    for operation, kind in cases:
        maximum_payload = maximum(proposal_schema(
            operation, {"id": "test", "kind": kind}
        ))
        assert len(agents.canonical_json(maximum_payload)) <= STRUCTURED_RESPONSE_RESERVE_BYTES
