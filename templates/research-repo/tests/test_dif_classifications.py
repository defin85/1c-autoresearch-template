from __future__ import annotations

from pathlib import Path

import pytest

from one_c_autoresearch.dif_classifications import (
    coverage,
    load_active,
    make_row,
    publish_empty,
    publish_window,
    remaining_ids,
)


FP = "sha256:" + "a" * 64


def _row(identifier: str, kind: str = "meaning", *, whole_component: bool = False, instruction: str = FP) -> dict:
    return make_row(
        identifier,
        {
            "kind": kind,
            "semantic_hints": ["component:extension:x", "whole-component:added"] if whole_component else [],
            "evidence": [{"path": "a", "fingerprint": FP}],
            "rationale": "evidence",
        },
        evidence_fingerprint=FP,
        result_schema_fingerprint=FP,
        profile_fingerprint=FP,
        instruction_fingerprint=instruction,
        context_fingerprint=FP,
    )


def test_make_row_preserves_optional_evidence_diff_identity() -> None:
    row = make_row(
        "DIF-0000000000000001",
        {
            "kind": "meaning",
            "semantic_hints": [],
            "evidence": [{"path": "a", "fingerprint": FP, "stable_diff_id": "DIF-0000000000000001"}],
            "rationale": "evidence",
        },
        evidence_fingerprint=FP,
        result_schema_fingerprint=FP,
        profile_fingerprint=FP,
        instruction_fingerprint=FP,
        context_fingerprint=FP,
    )
    assert row["evidence"][0]["stable_diff_id"] == "DIF-0000000000000001"


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    rows = [
        {"stable_diff_id": "DIF-0000000000000001"},
        {"stable_diff_id": "DIF-0000000000000002"},
    ]
    monkeypatch.setattr("one_c_autoresearch.dif_classifications.inventory", lambda _repo: rows)
    monkeypatch.setattr(
        "one_c_autoresearch.dif_classifications._bindings",
        lambda _repo: {
            "source_generation_id": "s" * 64,
            "source_fingerprint": "sha256:" + "1" * 64,
            "diff_generation_id": "d" * 64,
            "diff_fingerprint": "sha256:" + "2" * 64,
        },
    )
    return tmp_path


def test_accumulated_windows_are_atomic_idempotent_and_exact(repository: Path) -> None:
    empty = publish_empty(repository)
    first = publish_window(
        repository, [_row("DIF-0000000000000001")],
        expected_generation_id=empty["generation_id"],
    )
    same = publish_window(repository, [], expected_generation_id=first["generation_id"])
    assert same["generation_id"] == first["generation_id"]
    final = publish_window(
        repository, [_row("DIF-0000000000000002", "noise")],
        expected_generation_id=first["generation_id"],
    )
    assert final["generation_id"] != first["generation_id"]
    assert coverage(repository) == {
        "total": 2, "classified": 2, "remaining": 0,
        "meaning": 1, "noise_candidate": 1, "all_dif_classified": True,
    }
    assert len(load_active(repository)["rows"]) == 2


def test_stale_or_partial_window_does_not_move_pointer(repository: Path) -> None:
    active = publish_empty(repository)
    before = (repository / "research/active-dif-classification-generation.json").read_bytes()
    with pytest.raises(RuntimeError, match="stale"):
        publish_window(repository, [_row("DIF-0000000000000001")], expected_generation_id="wrong")
    assert (repository / "research/active-dif-classification-generation.json").read_bytes() == before
    with pytest.raises(ValueError, match="evidence"):
        bad = _row("DIF-0000000000000001")
        bad["evidence"] = []
        publish_window(repository, [bad], expected_generation_id=active["generation_id"])
    assert (repository / "research/active-dif-classification-generation.json").read_bytes() == before


def test_stale_classification_is_reset_for_current_diff(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from one_c_autoresearch.dif_classifications import ensure_current

    prior = publish_empty(repository)
    monkeypatch.setattr(
        "one_c_autoresearch.dif_classifications._bindings",
        lambda _repo: {
            "source_generation_id": "s" * 64,
            "source_fingerprint": "sha256:" + "1" * 64,
            "diff_generation_id": "e" * 64,
            "diff_fingerprint": "sha256:" + "3" * 64,
        },
    )
    with pytest.raises(ValueError, match="stale DIF classification bindings"):
        load_active(repository)
    current = ensure_current(repository)
    assert current["generation_id"] != prior["generation_id"]
    assert load_active(repository)["rows"] == []


def test_versioned_whole_component_result_can_replace_only_its_prior_result(repository: Path) -> None:
    active = publish_empty(repository)
    first = publish_window(
        repository,
        [_row("DIF-0000000000000001", whole_component=True)],
        expected_generation_id=active["generation_id"],
    )
    replacement = _row(
        "DIF-0000000000000001",
        whole_component=True,
        instruction="sha256:" + "b" * 64,
    )
    second = publish_window(repository, [replacement], expected_generation_id=first["generation_id"])
    assert second["generation_id"] != first["generation_id"]
    with pytest.raises(ValueError, match="cannot be replaced"):
        publish_window(
            repository,
            [_row("DIF-0000000000000001")],
            expected_generation_id=second["generation_id"],
        )


def test_stale_whole_component_version_does_not_satisfy_coverage(
    repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = repository / "research/active-source-generation.json"
    source.parent.mkdir(exist_ok=True)
    source.write_text('{"routing_manifest_path":"routing-manifest.json"}', encoding="utf-8")
    active = publish_empty(repository)
    first = publish_window(
        repository,
        [_row("DIF-0000000000000001", whole_component=True)],
        expected_generation_id=active["generation_id"],
    )
    expected = {
        "DIF-0000000000000001": {
            "result": {
                "kind": "meaning",
                "semantic_hints": ["component:extension:x", "whole-component:added"],
                "evidence": [{"path": "a", "fingerprint": FP}],
                "rationale": "evidence",
            },
            "evidence_fingerprint": FP,
            "result_schema_fingerprint": FP,
            "profile_fingerprint": FP,
            "instruction_fingerprint": "sha256:" + "b" * 64,
            "context_fingerprint": FP,
        },
    }
    monkeypatch.setattr("one_c_autoresearch.component_groups.deterministic_results", lambda *_args: expected)
    assert remaining_ids(repository) == ["DIF-0000000000000001", "DIF-0000000000000002"]
    assert coverage(repository)["all_dif_classified"] is False
    replacement = _row(
        "DIF-0000000000000001",
        whole_component=True,
        instruction="sha256:" + "b" * 64,
    )
    publish_window(repository, [replacement], expected_generation_id=first["generation_id"])
    assert remaining_ids(repository) == ["DIF-0000000000000002"]
