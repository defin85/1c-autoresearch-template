import json
import csv
from types import SimpleNamespace
from pathlib import Path

import pytest

from one_c_autoresearch.diffs import COVERAGE_HEADER, build, compare, validate_active


def test_two_comparisons_and_stable_ids(tmp_path: Path):
    source_id = "a" * 64
    root = tmp_path / "sources/generations" / source_id
    for role in ("vendor_baseline", "target_cf", "next_vendor"):
        (root / role / "configuration").mkdir(parents=True)
    (root / "vendor_baseline/configuration/a.bsl").write_text("old", encoding="utf-8")
    (root / "target_cf/configuration/a.bsl").write_text("custom", encoding="utf-8")
    (root / "next_vendor/configuration/a.bsl").write_text("new", encoding="utf-8")
    (root / "vendor_baseline/configuration/b.bsl").write_text("old", encoding="utf-8")
    (root / "target_cf/configuration/b.bsl").write_text("vendor feature", encoding="utf-8")
    (root / "next_vendor/configuration/b.bsl").write_text("vendor feature", encoding="utf-8")
    (root / "vendor_baseline/configuration/c.bsl").write_text("baseline", encoding="utf-8")
    (root / "target_cf/configuration/c.bsl").write_text("still custom", encoding="utf-8")
    (root / "next_vendor/configuration/c.bsl").write_text("baseline", encoding="utf-8")
    source_pointer = {"generation_id": source_id, "acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "representation_schema": "xml-hierarchical", "normalizer_version": "1"}
    (tmp_path / "research").mkdir()
    (tmp_path / "research/active-source-generation.json").write_text(json.dumps(source_pointer), encoding="utf-8")
    pointer = build(tmp_path, source_pointer)
    assert pointer["row_counts"] == {"diff-inventory.csv": 5, "diff-id-map.csv": 5, "target-coverage.csv": 3}
    first = (tmp_path / "analysis/indexes/generations" / pointer["generation_id"] / "diff-inventory.csv").read_bytes()
    with (tmp_path / "analysis/indexes/generations" / pointer["generation_id"] / "target-coverage.csv").open(encoding="utf-8", newline="") as stream:
        coverage = list(csv.DictReader(stream))
    assert tuple(coverage[0]) == COVERAGE_HEADER
    assert {row["coverage_status"] for row in coverage} == {"covered_by_vendor", "still_required", "changed_in_target"}
    assert all(row["customer_diff_id"].startswith("DIF-") and row["evidence_ref"] for row in coverage)
    assert build(tmp_path, source_pointer)["generation_id"] == pointer["generation_id"]
    assert first.startswith(b"stable_diff_id,comparison_id")
    assert validate_active(tmp_path) == pointer


def test_id_map_retains_disappeared_dif_as_inactive(tmp_path: Path):
    first_source = "a" * 64
    second_source = "b" * 64
    for source_id, custom in ((first_source, "custom"), (second_source, "old")):
        root = tmp_path / "sources/generations" / source_id
        for role in ("vendor_baseline", "target_cf", "next_vendor"):
            path = root / role / "configuration/a.bsl"; path.parent.mkdir(parents=True); path.write_text("old", encoding="utf-8")
        (root / "target_cf/configuration/a.bsl").write_text(custom, encoding="utf-8")
    (tmp_path / "research").mkdir()
    profile = {"acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "representation_schema": "xml-hierarchical", "normalizer_version": "1"}
    first = {"generation_id": first_source, **profile}
    (tmp_path / "research/active-source-generation.json").write_text(json.dumps(first), encoding="utf-8")
    build(tmp_path, first)
    second = {"generation_id": second_source, **profile}
    (tmp_path / "research/active-source-generation.json").write_text(json.dumps(second), encoding="utf-8")
    pointer = build(tmp_path, second)
    with (tmp_path / "analysis/indexes/generations" / pointer["generation_id"] / "diff-id-map.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and rows[0]["active"] == "false"
    assert rows[0]["first_seen_generation"] == rows[0]["last_seen_generation"] == first_source


def test_exact_rename_lineage_is_unique_and_ambiguous_matches_remain_separate(tmp_path: Path):
    source_id = "c" * 64; root = tmp_path / "sources/generations" / source_id
    for role in ("vendor_baseline", "target_cf", "next_vendor"):
        (root / role).mkdir(parents=True)
    (root / "vendor_baseline/old.bin").write_bytes(b"\x00same")
    (root / "target_cf/new.bin").write_bytes(b"\x00same")
    (root / "next_vendor/old.bin").write_bytes(b"\x00same")
    pointer = {"generation_id": source_id, "acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "representation_schema": "xml-hierarchical", "normalizer_version": "1"}
    (tmp_path / "research").mkdir(); (tmp_path / "research/active-source-generation.json").write_text(json.dumps(pointer), encoding="utf-8")
    result = build(tmp_path, pointer)
    with (tmp_path / "analysis/indexes/generations" / result["generation_id"] / "diff-id-map.csv").open(encoding="utf-8", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["comparison_id"] and row["active"] == "true"]
    deleted = next(row for row in rows if row["change_type"] == "deleted"); added = next(row for row in rows if row["change_type"] == "added")
    assert deleted["successor_diff_id"] == added["stable_diff_id"] and added["predecessor_diff_id"] == deleted["stable_diff_id"]
    assert not list(tmp_path.rglob(".git"))


def test_git_path_and_exit_validation_fail_closed(tmp_path: Path, monkeypatch):
    before = tmp_path / "before"; after = tmp_path / "after"; before.mkdir(); after.mkdir()
    metadata = {"comparison_kind": "customer-customization", "before_role": "vendor_baseline", "after_role": "target_cf", "acquisition_profile_id": "p", "representation_schema": "xml", "normalizer_version": "1", "source_generation": "a" * 64}
    monkeypatch.setattr("one_c_autoresearch.diffs.subprocess.run", lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stdout=b"", stderr=b"failure"))
    with pytest.raises(RuntimeError, match="exit 2"): compare(before, after, metadata)
    monkeypatch.setattr("one_c_autoresearch.diffs.subprocess.run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=b"A\0\xff\0", stderr=b""))
    with pytest.raises(ValueError, match="non-UTF-8"): compare(before, after, metadata)
    escaped = str(tmp_path.parent / "escape").encode()
    monkeypatch.setattr("one_c_autoresearch.diffs.subprocess.run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=b"A\0" + escaped + b"\0", stderr=b""))
    with pytest.raises(ValueError, match="escapes"): compare(before, after, metadata)


def test_identical_component_manifests_create_no_provenance_only_diff(tmp_path: Path):
    source_id = "d" * 64; root = tmp_path / "sources/generations" / source_id
    manifest = '{"kind":"configuration","name":"Cfg","payload_fingerprint":"sha256:same","schema_version":"1"}\n'
    for role in ("vendor_baseline", "target_cf", "next_vendor"):
        component = root / role / "configuration"; component.mkdir(parents=True)
        (component / "Configuration.xml").write_text("same", encoding="utf-8")
        (component / "component-manifest.json").write_text(manifest, encoding="utf-8")
    pointer = {"generation_id": source_id, "acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "representation_schema": "xml-hierarchical", "normalizer_version": "1"}
    (tmp_path / "research").mkdir(); (tmp_path / "research/active-source-generation.json").write_text(json.dumps(pointer), encoding="utf-8")
    result = build(tmp_path, pointer)
    with (tmp_path / "analysis/indexes/generations" / result["generation_id"] / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
        assert list(csv.DictReader(stream)) == []


def test_forced_truncated_dif_collision_fails_closed(tmp_path: Path, monkeypatch):
    source_id = "e" * 64
    root = tmp_path / "sources/generations" / source_id
    for role in ("vendor_baseline", "target_cf", "next_vendor"):
        (root / role).mkdir(parents=True)
    pointer = {"generation_id": source_id, "acquisition_profile_id": "p", "representation_schema": "xml", "normalizer_version": "1"}
    (tmp_path / "research").mkdir()
    (tmp_path / "research/active-source-generation.json").write_text(json.dumps(pointer), encoding="utf-8")

    def colliding_rows(_before, _after, metadata):
        path = f"{metadata['comparison_kind']}.bsl"
        return [{"stable_diff_id": "DIF-COLLISION", "comparison_id": "CMP-X", "source_generation": source_id, "before_role": metadata["before_role"], "after_role": metadata["after_role"], "change_type": "modified", "path": path, "object_kind": "file", "object_name": path, "area": "", "before_fingerprint": "sha256:a", "after_fingerprint": "sha256:b", "content_fingerprint": "sha256:c"}]

    monkeypatch.setattr("one_c_autoresearch.diffs.compare", colliding_rows)
    with pytest.raises(ValueError, match="truncated DIF hash collision"):
        build(tmp_path, pointer)
