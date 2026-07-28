import csv
import json
from pathlib import Path

from one_c_autoresearch.component_groups import ALGORITHM_VERSION, derive, deterministic_results
from one_c_autoresearch.consolidation import normalized_records, partition_manifest


HEADER = (
    "stable_diff_id", "comparison_id", "source_generation", "before_role", "after_role",
    "change_type", "path", "object_kind", "object_name", "area", "before_fingerprint",
    "after_fingerprint", "content_fingerprint",
)


def _repo(tmp_path: Path) -> Path:
    research = tmp_path / "research"
    research.mkdir()
    source_id = "source"
    source_root = tmp_path / "sources/generations" / source_id
    source_root.mkdir(parents=True)
    routing = {
        "groups": [
            {"routing_group_id": "extension:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "kind": "extension", "members": [{"role": "target_cf", "component_id": "target_cf:extension:a"}]},
            {"routing_group_id": "external:EXT-ADDED", "kind": "erf", "members": [{"role": "target_cf", "component_id": "target_cf:external:EXT-ADDED"}]},
            {"routing_group_id": "external:EXT-DELETED", "kind": "epf", "members": [{"role": "vendor_baseline", "component_id": "vendor_baseline:external:EXT-DELETED"}]},
            {"routing_group_id": "external:EXT-MODIFIED", "kind": "source-tree", "members": [{"role": role, "component_id": f"{role}:external:EXT-MODIFIED"} for role in ("vendor_baseline", "target_cf")]},
        ],
    }
    (source_root / "routing-manifest.json").write_text(json.dumps(routing), encoding="utf-8")
    (research / "active-source-generation.json").write_text(json.dumps({"generation_id": source_id, "routing_manifest_path": "routing-manifest.json"}), encoding="utf-8")
    diff_id = "diff"
    diff_root = tmp_path / "analysis/indexes/generations" / diff_id
    diff_root.mkdir(parents=True)
    rows = [
        ("DIF-ADD-1", "added", "extensions/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/A.xml"),
        ("DIF-ADD-2", "added", "extensions/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/B.xml"),
        ("DIF-EXT-ADD", "added", "external/EXT-ADDED/report.erf"),
        ("DIF-DELETE", "deleted", "external/EXT-DELETED/report.epf"),
        ("DIF-MODIFIED", "modified", "external/EXT-MODIFIED/source/A.bsl"),
    ]
    with (diff_root / "diff-inventory.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=HEADER)
        writer.writeheader()
        for identifier, change, path in rows:
            writer.writerow({
                "stable_diff_id": identifier, "comparison_id": "CMP-CUSTOMER", "source_generation": source_id,
                "before_role": "vendor_baseline", "after_role": "target_cf", "change_type": change,
                "path": path, "object_kind": "file", "object_name": path, "area": path.rsplit("/", 1)[0],
                "before_fingerprint": "sha256:" + "1" * 64, "after_fingerprint": "sha256:" + "2" * 64,
                "content_fingerprint": "sha256:" + "3" * 64,
            })
    with (diff_root / "target-coverage.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("customer_diff_id", "source_generation", "target_diff_ids", "coverage_status", "evidence_ref", "notes"))
        writer.writeheader()
    (research / "active-diff-generation.json").write_text(json.dumps({"generation_id": diff_id}), encoding="utf-8")
    return tmp_path


def test_component_groups_preserve_member_dif_and_role_correct_evidence(tmp_path: Path):
    repo = _repo(tmp_path)
    groups = derive(repo, validate=False)
    added = next(group for group in groups if group["component_key"].startswith("aaaaaaaa"))
    deleted = next(group for group in groups if group["component_key"] == "EXT-DELETED")
    external_added = next(group for group in groups if group["component_key"] == "EXT-ADDED")
    modified = next(group for group in groups if group["component_key"] == "EXT-MODIFIED")
    assert added["stable_diff_ids"] == ["DIF-ADD-1", "DIF-ADD-2"]
    assert all(item[0]["path"].startswith("target_cf/") for item in added["evidence"].values())
    assert deleted["evidence"]["DIF-DELETE"][0]["path"].startswith("vendor_baseline/")
    assert external_added["evidence"]["DIF-EXT-ADD"][0]["path"].startswith("target_cf/")
    assert modified["direction"] == "modified"
    assert "DIF-MODIFIED" not in deterministic_results(repo, ["DIF-MODIFIED"], validate=False)
    results = deterministic_results(repo, ["DIF-ADD-1", "DIF-ADD-2", "DIF-EXT-ADD", "DIF-DELETE"], validate=False)
    assert set(results) == {"DIF-ADD-1", "DIF-ADD-2", "DIF-EXT-ADD", "DIF-DELETE"}
    assert all(value["result"]["kind"] == "meaning" for value in results.values())
    assert all(ALGORITHM_VERSION in value["result"]["rationale"] or value["instruction_fingerprint"].startswith("sha256:") for value in results.values())


def test_component_context_is_a_bounded_hint_not_a_synthetic_dif(tmp_path: Path):
    groups = derive(_repo(tmp_path), validate=False)
    classifications = [
        {"stable_diff_id": identifier, "classification": "meaning"}
        for identifier in ("DIF-ADD-1", "DIF-ADD-2", "DIF-EXT-ADD", "DIF-DELETE", "DIF-MODIFIED")
    ]
    records = normalized_records({"classifications": classifications, "component_groups": groups, "mrq": {}})
    dif_records = [row for row in records if row["record_type"] == "dif"]
    summaries = [row for row in records if row["record_type"] == "component_summary"]
    assert {row["stable_diff_id"] for row in dif_records} == {row["stable_diff_id"] for row in classifications}
    assert all("stable_diff_id" not in row for row in summaries)
    manifest = partition_manifest(records, 8192)
    assert sorted(identifier for page in manifest["partitions"] for identifier in page["stable_diff_ids"]) == sorted(row["stable_diff_id"] for row in classifications)
