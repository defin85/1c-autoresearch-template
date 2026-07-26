from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import one_c_autoresearch.mrq_batches as mrq_batches
from one_c_autoresearch.mrq_batches import (
    MAX_BATCH_SIZE,
    MRQBatch,
    assign,
    batch_id,
    classify,
    load_active,
    publish,
    source_mrq_payload,
    stable_windows,
    validate_batch_id,
    validate_rows,
)


def _mrq(identifier: str, *, scope: str = "Sales", business: str = "Invoice numbering", state: str = "draft", evidence_path: str = "Catalogs/Invoices") -> dict:
    return {
        "mrq_id": identifier,
        "semantic_key": identifier.lower(),
        "title": identifier,
        "state": state,
        "source_customization": {
            "business_meaning": business,
            "scope": scope,
            "evidence": [{"path": evidence_path, "stable_diff_id": "DIF-AAA"}],
        },
    }


def test_batch_id_is_stable_and_order_invariant() -> None:
    first = batch_id(["MRQ-AAA", "MRQ-BBB"])
    second = batch_id(["MRQ-BBB", "MRQ-AAA"])
    third = batch_id(["MRQ-AAA"])
    assert first == second
    assert first != third
    assert first.startswith("MRQB-")
    assert len(first) == len("MRQB-") + 16


def test_batch_id_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="at least one MRQ"):
        batch_id([])


def test_validate_batch_id_detects_collision() -> None:
    identifier = batch_id(["MRQ-AAA", "MRQ-BBB"])
    validate_batch_id(identifier, ["MRQ-AAA", "MRQ-BBB"])
    with pytest.raises(ValueError, match="collision or mismatch"):
        validate_batch_id(identifier, ["MRQ-AAA", "MRQ-CCC"])


def test_classify_returns_empty_for_empty_input() -> None:
    assert classify([]) == []


def test_classify_skips_superseded_mrq() -> None:
    rows = [_mrq("MRQ-AAA", state="superseded"), _mrq("MRQ-BBB")]
    batches = classify(rows)
    assert len(batches) == 1
    assert batches[0].mrq_ids == ("MRQ-BBB",)


def test_classify_groups_related_mrq_into_single_batch() -> None:
    rows = [
        _mrq("MRQ-AAA", scope="Sales", business="Invoices", evidence_path="Catalogs/Invoices"),
        _mrq("MRQ-BBB", scope="Sales", business="Invoices", evidence_path="Catalogs/Invoices"),
        _mrq("MRQ-CCC", scope="Sales", business="Invoices", evidence_path="Catalogs/Invoice"),
    ]
    batches = classify(rows)
    assert len(batches) == 1
    assert set(batches[0].mrq_ids) == {"MRQ-AAA", "MRQ-BBB", "MRQ-CCC"}


def test_classify_splits_when_anchor_components_differ() -> None:
    """При недостаточных доказательствах связи (разные опорные компоненты)
    создаётся отдельный пакет — по ``spec.md`` «Связь MRQ не доказана».
    """

    rows = [
        _mrq("MRQ-AAA", scope="Sales", business="Invoices", evidence_path="Catalogs/Invoices"),
        _mrq("MRQ-BBB", scope="Sales", business="Invoices", evidence_path="Documents/Invoice"),
    ]
    batches = classify(rows)
    assert len(batches) == 2
    assert all(len(batch.mrq_ids) == 1 for batch in batches)


def test_classify_creates_standalone_batch_when_linkage_absent() -> None:
    rows = [
        _mrq("MRQ-AAA", scope="Sales", business="Invoices", evidence_path="Catalogs/Invoices"),
        _mrq("MRQ-BBB", scope="Warehouse", business="Stock", evidence_path="Catalogs/Stock"),
    ]
    batches = classify(rows)
    assert len(batches) == 2
    assert all(len(batch.mrq_ids) == 1 for batch in batches)
    # порядок пакетов задаётся минимальным MRQ-ID
    assert [batch.mrq_ids[0] for batch in batches] == ["MRQ-AAA", "MRQ-BBB"]


def test_classify_caps_batch_size_at_max() -> None:
    rows = [_mrq(f"MRQ-{i:03d}", scope="Sales", business="Invoices", evidence_path="Catalogs/Invoices") for i in range(MAX_BATCH_SIZE + 5)]
    batches = classify(rows, max_batch_size=MAX_BATCH_SIZE)
    sizes = [len(batch.mrq_ids) for batch in batches]
    assert max(sizes) <= MAX_BATCH_SIZE
    # каждый MRQ входит ровно в один пакет
    flat = [identifier for batch in batches for identifier in batch.mrq_ids]
    assert sorted(flat) == sorted([f"MRQ-{i:03d}" for i in range(MAX_BATCH_SIZE + 5)])


def test_classify_rejects_too_large_max_batch_size() -> None:
    with pytest.raises(ValueError, match="max batch size"):
        classify([_mrq("MRQ-AAA")], max_batch_size=MAX_BATCH_SIZE + 1)


def test_classify_produces_disjoint_complete_coverage() -> None:
    rows = [
        _mrq("MRQ-AAA", scope="A", business="x", evidence_path="A/x"),
        _mrq("MRQ-BBB", scope="B", business="y", evidence_path="B/y"),
        _mrq("MRQ-CCC", scope="A", business="x", evidence_path="A/x"),
    ]
    batches = classify(rows)
    flat = [identifier for batch in batches for identifier in batch.mrq_ids]
    # нет пересечений
    assert len(flat) == len(set(flat)) == 3


def test_assign_returns_owning_batch_or_none() -> None:
    batches = classify([_mrq("MRQ-AAA", scope="A", business="x", evidence_path="A/x"), _mrq("MRQ-BBB", scope="B", business="y", evidence_path="B/y")])
    owner = assign("MRQ-AAA", batches)
    assert owner is not None and owner.mrq_ids == ("MRQ-AAA",)
    assert assign("MRQ-ZZZ", batches) is None


def test_batch_payload_is_canonical() -> None:
    batches = classify([_mrq("MRQ-AAA", scope="A", business="x", evidence_path="A/x")])
    payload = batches[0].canonical_payload()
    assert payload["schema_version"] == "1"
    assert payload["mrq_ids"] == ["MRQ-AAA"]
    assert payload["batch_id"].startswith("MRQB-")
    assert isinstance(payload["anchor_component_ids"], list)


def test_recalculate_after_wipe_produces_same_batches() -> None:
    rows = [_mrq("MRQ-AAA", scope="A", business="x", evidence_path="A/x"), _mrq("MRQ-BBB", scope="A", business="x", evidence_path="A/x")]
    first = classify(rows)
    second = classify(rows)
    assert [batch.batch_id for batch in first] == [batch.batch_id for batch in second]
    # содержимое совпадает
    assert first[0].mrq_ids == second[0].mrq_ids


def _repository(tmp_path: Path) -> Path:
    source_id, diff_id, generation_id = "a" * 64, "b" * 64, "c" * 64
    research = tmp_path / "research"
    research.mkdir()
    (research / "active-source-generation.json").write_text(json.dumps({
        "schema_version": "2",
        "generation_id": source_id,
        "components": [{"component_id": "target_cf:configuration", "path": "target_cf/configuration"}],
    }), encoding="utf-8")
    (research / "active-diff-generation.json").write_text(json.dumps({"schema_version": "2", "generation_id": diff_id, "source_generation_id": source_id}), encoding="utf-8")
    (research / "active-generation.json").write_text(json.dumps({"schema_version": "1", "canonical_generation_id": generation_id, "source_generation_id": source_id, "diff_generation_id": diff_id}), encoding="utf-8")
    root = tmp_path / "analysis/migration-requirements/generations" / generation_id
    root.mkdir(parents=True)
    row = _mrq("MRQ-AAAAAAAAAAAAAAAA", evidence_path="configuration/Catalogs/Invoices.xml")
    row["source_customization"]["evidence"][0]["stable_diff_id"] = "DIF-AAAAAAAAAAAAAAAA"
    row["source_customization"]["evidence"][0]["fingerprint"] = "sha256:" + "d" * 64
    (root / "mrq.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (root / "dispositions.jsonl").write_text(json.dumps({"mrq_id": row["mrq_id"], "stable_diff_id": "DIF-AAAAAAAAAAAAAAAA", "primary": True}) + "\n", encoding="utf-8")
    diff_root = tmp_path / "analysis/indexes/generations" / diff_id
    diff_root.mkdir(parents=True)
    with (diff_root / "diff-inventory.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("stable_diff_id", "after_role", "path"))
        writer.writeheader()
        writer.writerow({"stable_diff_id": "DIF-AAAAAAAAAAAAAAAA", "after_role": "target_cf", "path": "configuration/Catalogs/Invoices.xml"})
    return tmp_path


def test_source_fingerprint_and_windows_use_exact_repository_inputs(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    generation_id, fingerprint, records = source_mrq_payload(repo)
    assert generation_id == "c" * 64
    assert fingerprint.startswith("sha256:")
    assert records[0]["primary_dif_ids"] == ["DIF-AAAAAAAAAAAAAAAA"]
    assert records[0]["source_component_ids"] == ["target_cf:configuration"]
    assert stable_windows(records) == [records]


def test_publish_load_and_same_input_noop(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _generation_id, fingerprint, records = source_mrq_payload(repo)
    batch = MRQBatch(batch_id([records[0]["mrq_id"]]), (records[0]["mrq_id"],), "same source component", ("target_cf:configuration",))
    first = publish(repo, [batch], fingerprint)
    directories = sorted((repo / "analysis/migration-requirements/batch-generations").iterdir())
    second = publish(repo, [batch], fingerprint)
    assert second == first
    assert sorted((repo / "analysis/migration-requirements/batch-generations").iterdir()) == directories
    assert load_active(repo) == [batch]


def test_corrupt_batch_generation_fails_closed(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _generation_id, fingerprint, records = source_mrq_payload(repo)
    batch = MRQBatch(batch_id([records[0]["mrq_id"]]), (records[0]["mrq_id"],), "same source component", ("target_cf:configuration",))
    binding = publish(repo, [batch], fingerprint)
    path = repo / "analysis/migration-requirements/batch-generations" / binding["generation_id"] / "batches.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt|fingerprint|coverage"):
        load_active(repo)


def test_empty_generation_is_valid_and_reused(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    root = repo / "analysis/migration-requirements/generations" / ("c" * 64)
    (root / "mrq.jsonl").write_text("", encoding="utf-8")
    (root / "dispositions.jsonl").write_text("", encoding="utf-8")
    _generation_id, fingerprint, records = source_mrq_payload(repo)
    assert records == []
    first = publish(repo, [], fingerprint)
    assert load_active(repo) == []
    assert publish(repo, [], fingerprint) == first


def test_source_field_change_makes_binding_stale(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _generation_id, fingerprint, records = source_mrq_payload(repo)
    batch = MRQBatch(batch_id([records[0]["mrq_id"]]), (records[0]["mrq_id"],), "basis", ("target_cf:configuration",))
    publish(repo, [batch], fingerprint)
    path = repo / "analysis/migration-requirements/generations" / ("c" * 64) / "mrq.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["title"] = "changed"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        load_active(repo)


@pytest.mark.parametrize("components, message", [
    ([], "no component"),
    ([
        {"component_id": "one", "path": "target_cf/configuration"},
        {"component_id": "two", "path": "target_cf/configuration"},
    ], "ambiguous component"),
])
def test_component_resolution_fails_closed(tmp_path: Path, components: list[dict], message: str) -> None:
    repo = _repository(tmp_path)
    pointer = json.loads((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    pointer["components"] = components
    (repo / "research/active-source-generation.json").write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        source_mrq_payload(repo)


def test_windows_are_stable_and_limited_to_sixteen(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    record = source_mrq_payload(repo)[2][0]
    records = [{**record, "mrq_id": f"MRQ-{index:016X}"} for index in range(19)]
    windows = stable_windows(list(reversed(records)))
    assert [len(window) for window in windows] == [16, 3]
    assert [item["mrq_id"] for window in windows for item in window] == sorted(item["mrq_id"] for item in records)


def test_validate_rows_rejects_missing_duplicate_oversize_and_short_id_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [{"mrq_id": f"MRQ-{index:016X}"} for index in range(17)]
    one = MRQBatch(batch_id([records[0]["mrq_id"]]), (records[0]["mrq_id"],), "basis", ())
    with pytest.raises(ValueError, match="missed"):
        validate_rows([mrq_batches._batch_row(one)], records[:2])
    duplicate = [
        mrq_batches._batch_row(one),
        mrq_batches._batch_row(MRQBatch(one.batch_id, one.mrq_ids, "basis", ())),
    ]
    with pytest.raises(ValueError, match="multiple"):
        validate_rows(duplicate, records[:1])
    oversize_ids = tuple(item["mrq_id"] for item in records)
    oversize = MRQBatch(batch_id(oversize_ids), oversize_ids, "basis", ())
    with pytest.raises(ValueError, match="size"):
        validate_rows([mrq_batches._batch_row(oversize)], records)
    collision = "MRQB-0000000000000000"
    monkeypatch.setattr(mrq_batches, "batch_id", lambda _ids: collision)
    rows = [
        mrq_batches._batch_row(MRQBatch(collision, (records[0]["mrq_id"],), "one", ())),
        mrq_batches._batch_row(MRQBatch(collision, (records[1]["mrq_id"],), "two", ())),
    ]
    with pytest.raises(ValueError, match="shortened ID collision"):
        validate_rows(rows, records[:2])
