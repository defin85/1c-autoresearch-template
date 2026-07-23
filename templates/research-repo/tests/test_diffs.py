import json
import csv
from types import SimpleNamespace
from pathlib import Path

import pytest

from one_c_autoresearch.contracts import canonical_json, sha256
from one_c_autoresearch.diffs import COMPARISONS, COVERAGE_HEADER, VERSION_FILES, _build_routed, _publish_inventory, build, compare, validate_active


def _routed_repo(
    repo: Path,
    extension_roles: set[str],
    representation: str = "xml-hierarchical/v1",
) -> dict:
    source_id = sha256(canonical_json({"representation": representation, "roles": sorted(extension_roles)}))
    source_root = repo / "sources/generations" / source_id
    uuid = "471acdde-293c-497c-bd55-e6ab48d98dc4"
    components = []
    groups = [
        {"routing_group_id": "configuration", "representation_schema": "xml-hierarchical/v1", "members": []},
        {"routing_group_id": f"extension:{uuid}", "representation_schema": representation, "members": []},
    ]
    for role in ("vendor_baseline", "target_cf", "next_vendor"):
        configuration = source_root / role / "configuration"
        configuration.mkdir(parents=True)
        (configuration / "Configuration.xml").write_text(
            '<MetaDataObject><Configuration uuid="11111111-1111-1111-1111-111111111111"><Properties><Name>Demo</Name></Properties></Configuration></MetaDataObject>',
            encoding="utf-8",
        )
        component_id = f"{role}:configuration"
        groups[0]["members"].append({"role": role, "component_id": component_id})
        components.append({"component_id": component_id, "kind": "configuration", "path": f"{role}/configuration", "fingerprint": "sha256:" + "1" * 64})
    for role in sorted(extension_roles):
        extension = source_root / role / "extensions" / uuid
        extension.mkdir(parents=True)
        manifest = {
            "schema_version": "2", "component_id": f"{role}:extension:{uuid}", "kind": "extension",
            "routing_group_id": f"extension:{uuid}", "probe_contract_version": "", "probe_fingerprint": "",
            "form_counts": {"managed": 0, "ordinary": int(representation == "v8unpack/v1"), "inconclusive": 0},
            "routing_reason": "ordinary_form_present" if representation == "v8unpack/v1" else "no_forms",
            "exporter": "ibcmd", "representation_schema": representation, "exporter_version": "8.3",
            "converter_version": "v8unpack 1" if representation == "v8unpack/v1" else "",
            "payload_file_count": 3 if representation == "v8unpack/v1" else 1,
            "payload_fingerprint": "sha256:" + "2" * 64,
            "uuid": uuid, "name": "DemoExtension", "version": "1.0", "active": True,
        }
        (extension / "component-manifest.json").write_bytes(canonical_json(manifest) + b"\n")
        if representation == "v8unpack/v1":
            catalog = extension / "Catalog/Products"
            catalog.mkdir(parents=True)
            (extension / "ConfigurationExtension.json").write_text('{"compatibility_version":"","name":"DemoExtension"}', encoding="utf-8")
            (catalog / "Catalog.id.json").write_text('{"uuid":"22222222-2222-2222-2222-222222222222"}', encoding="utf-8")
            (catalog / "Catalog.json").write_text('{"name":"Products"}', encoding="utf-8")
        else:
            (extension / "Catalog.xml").write_text(
                '<MetaDataObject><Catalog uuid="22222222-2222-2222-2222-222222222222"><Properties><ObjectBelonging>Own</ObjectBelonging><Name>Products</Name></Properties></Catalog></MetaDataObject>',
                encoding="utf-8",
            )
        component_id = f"{role}:extension:{uuid}"
        groups[1]["members"].append({"role": role, "component_id": component_id})
        components.append({"component_id": component_id, "kind": "extension", "path": f"{role}/extensions/{uuid}", "fingerprint": "sha256:" + sha256(canonical_json(manifest))})
    routing = {"schema_version": "2", "routing_contract_version": "form-routing/v1", "groups": groups}
    routing["routing_manifest_fingerprint"] = "sha256:" + sha256(canonical_json(routing))
    (source_root / "routing-manifest.json").write_bytes(canonical_json(routing) + b"\n")
    pointer = {
        "schema_version": "2", "generation_id": source_id, "acquisition_profile_id": "ibcmd+form-aware/v1",
        "normalizer_version": "3", "routing_manifest_path": "routing-manifest.json",
        "routing_manifest_fingerprint": routing["routing_manifest_fingerprint"],
        "source_comparison_epoch_fingerprint": "sha256:" + "3" * 64, "components": components,
    }
    (repo / "research").mkdir(exist_ok=True)
    (repo / "research/active-source-generation.json").write_bytes(canonical_json(pointer) + b"\n")
    return pointer


def _legacy_fixture_build(repo: Path, pointer: dict) -> dict:
    source_root = repo / "sources/generations" / pointer["generation_id"]
    inventory = []
    for kind, before, after in COMPARISONS:
        inventory.extend(compare(source_root / before, source_root / after, {
            "comparison_kind": kind, "before_role": before, "after_role": after,
            "acquisition_profile_id": pointer["acquisition_profile_id"],
            "representation_schema": pointer["representation_schema"],
            "normalizer_version": pointer["normalizer_version"],
            "source_generation": pointer["generation_id"],
        }))
    return _publish_inventory(repo, pointer, inventory)


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
    pointer = _legacy_fixture_build(tmp_path, source_pointer)
    assert pointer["row_counts"] == {"diff-inventory.csv": 5, "diff-id-map.csv": 5, "target-coverage.csv": 3}
    first = (tmp_path / "analysis/indexes/generations" / pointer["generation_id"] / "diff-inventory.csv").read_bytes()
    with (tmp_path / "analysis/indexes/generations" / pointer["generation_id"] / "target-coverage.csv").open(encoding="utf-8", newline="") as stream:
        coverage = list(csv.DictReader(stream))
    assert tuple(coverage[0]) == COVERAGE_HEADER
    assert {row["coverage_status"] for row in coverage} == {"covered_by_vendor", "still_required", "changed_in_target"}
    assert all(row["customer_diff_id"].startswith("DIF-") and row["evidence_ref"] for row in coverage)
    assert _legacy_fixture_build(tmp_path, source_pointer)["generation_id"] == pointer["generation_id"]
    assert first.startswith(b"stable_diff_id,comparison_id")
    assert validate_active(tmp_path) == pointer


def test_historical_source_schema_cannot_build_a_new_diff(tmp_path: Path):
    pointer = {"schema_version": "1", "generation_id": "a" * 64}
    (tmp_path / "research").mkdir()
    (tmp_path / "research/active-source-generation.json").write_bytes(canonical_json(pointer) + b"\n")
    with pytest.raises(ValueError, match="routed source schema version 2"):
        build(tmp_path, pointer)


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
    _legacy_fixture_build(tmp_path, first)
    second = {"generation_id": second_source, **profile}
    (tmp_path / "research/active-source-generation.json").write_text(json.dumps(second), encoding="utf-8")
    pointer = _legacy_fixture_build(tmp_path, second)
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
    result = _legacy_fixture_build(tmp_path, pointer)
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
    result = _legacy_fixture_build(tmp_path, pointer)
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

    monkeypatch.setattr(__name__ + ".compare", colliding_rows)
    with pytest.raises(ValueError, match="truncated DIF hash collision"):
        _legacy_fixture_build(tmp_path, pointer)


def test_routed_build_publishes_closed_semantic_extension_generation(tmp_path: Path):
    pointer = _routed_repo(tmp_path, {"target_cf", "next_vendor"})
    result = _build_routed(tmp_path, pointer)
    assert result["schema_version"] == "2" and tuple(sorted(result["files"])) == tuple(sorted(VERSION_FILES["2"]))
    root = tmp_path / "analysis/indexes/generations" / result["generation_id"]
    with (root / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
        inventory = list(csv.DictReader(stream))
    with (root / "extension-physical-diff.csv").open(encoding="utf-8", newline="") as stream:
        physical = list(csv.DictReader(stream))
    assert inventory and all(row["object_kind"] == "extension_intervention" for row in inventory)
    assert physical and not {row["stable_diff_id"] for row in physical} & {row["stable_diff_id"] for row in inventory}
    assert validate_active(tmp_path) == result
    assert _build_routed(tmp_path, pointer)["generation_id"] == result["generation_id"]
    before = (tmp_path / "research/active-diff-generation.json").read_bytes()
    with pytest.raises(InterruptedError, match="cancelled"):
        _build_routed(tmp_path, pointer, cancelled=lambda: True)
    assert (tmp_path / "research/active-diff-generation.json").read_bytes() == before
    (root / "unexpected.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mixed or incomplete"):
        validate_active(tmp_path)


@pytest.mark.parametrize(
    ("roles", "representation"),
    [
        ({"target_cf"}, "xml-hierarchical/v1"),
        ({"vendor_baseline", "target_cf"}, "xml-hierarchical/v1"),
        ({"vendor_baseline"}, "xml-hierarchical/v1"),
        ({"next_vendor"}, "xml-hierarchical/v1"),
        ({"vendor_baseline", "target_cf", "next_vendor"}, "xml-hierarchical/v1"),
        ({"target_cf"}, "v8unpack/v1"),
        ({"vendor_baseline", "target_cf"}, "v8unpack/v1"),
        ({"vendor_baseline"}, "v8unpack/v1"),
        ({"next_vendor"}, "v8unpack/v1"),
        ({"vendor_baseline", "target_cf", "next_vendor"}, "v8unpack/v1"),
    ],
)
def test_routed_role_representation_matrix_is_byte_repeatable(
    tmp_path: Path,
    roles: set[str],
    representation: str,
) -> None:
    pointer = _routed_repo(tmp_path, roles, representation)
    first = _build_routed(tmp_path, pointer)
    root = tmp_path / "analysis/indexes/generations" / first["generation_id"]
    payloads = {name: (root / name).read_bytes() for name in VERSION_FILES["2"]}
    second = _build_routed(tmp_path, pointer)
    assert second == first
    assert payloads == {name: (root / name).read_bytes() for name in VERSION_FILES["2"]}
    with (root / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
        inventory = list(csv.DictReader(stream))
    with (root / "extension-physical-diff.csv").open(encoding="utf-8", newline="") as stream:
        physical = list(csv.DictReader(stream))
    assert not {row["stable_diff_id"] for row in inventory} & {row["stable_diff_id"] for row in physical}
    assert validate_active(tmp_path) == first


@pytest.mark.parametrize("failure", ["serialization", "publication"])
def test_routed_failures_leave_active_pointer_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    import one_c_autoresearch.diffs as diff_module

    pointer = _routed_repo(tmp_path, {"target_cf"})
    _build_routed(tmp_path, pointer)
    active = tmp_path / "research/active-diff-generation.json"
    before = active.read_bytes()
    if failure == "serialization":
        monkeypatch.setattr(
            diff_module,
            "_write_jsonl",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("serialization failed")),
        )
    else:
        monkeypatch.setattr(
            diff_module,
            "atomic_json",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("publication failed")),
        )
    with pytest.raises(OSError, match="failed"):
        _build_routed(tmp_path, pointer)
    assert active.read_bytes() == before


def test_routed_build_rejects_source_changed_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import one_c_autoresearch.diffs as diff_module

    pointer = _routed_repo(tmp_path, {"target_cf"})
    original = diff_module._write_jsonl
    changed = False

    def change_source(*args, **kwargs):
        nonlocal changed
        original(*args, **kwargs)
        if not changed:
            changed = True
            (tmp_path / "research/active-source-generation.json").write_bytes(
                canonical_json({**pointer, "generation_id": "0" * 64}) + b"\n"
            )

    monkeypatch.setattr(diff_module, "_write_jsonl", change_source)
    with pytest.raises(RuntimeError, match="stale source"):
        _build_routed(tmp_path, pointer)
    assert not (tmp_path / "research/active-diff-generation.json").exists()
