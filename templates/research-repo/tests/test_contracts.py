import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from one_c_autoresearch import agents
from one_c_autoresearch.agents import validate_proposal
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
