from one_c_autoresearch.contracts import canonical_json, sha256
import json
from pathlib import Path

from one_c_autoresearch.mrq import FILES, active, approve_noise, comparison_epoch_fingerprint, decide, propose, publish, restructure, revalidate_unchanged, review, validate_graph
import pytest


def empty():
    return {name: [] for name in FILES}


def test_comparison_epoch_changes_for_profile_representation_or_normalizer():
    base = {"acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "normalizer_version": "1", "representation_schema": "xml-hierarchical"}
    fingerprint = comparison_epoch_fingerprint(base)
    assert comparison_epoch_fingerprint({**base, "acquisition_profile_id": "designer+xml-hierarchical/v1"}) != fingerprint
    assert comparison_epoch_fingerprint({**base, "representation_schema": "v8unpack"}) != fingerprint
    assert comparison_epoch_fingerprint({**base, "normalizer_version": "2"}) != fingerprint


def test_schema2_comparison_epoch_uses_exact_extension_contract():
    source = {"schema_version": "2", "source_comparison_epoch_fingerprint": "sha256:" + "a" * 64}
    analyzer = {"extension_analyzer_version": "extension-semantic/v1", "extension_adapter_versions": ["v8unpack@1", "xml-hierarchical@1"]}
    expected = sha256(canonical_json({
        "source_comparison_epoch_fingerprint": source["source_comparison_epoch_fingerprint"],
        "extension_analyzer_version": analyzer["extension_analyzer_version"],
        "extension_adapter_versions": analyzer["extension_adapter_versions"],
    }))
    assert comparison_epoch_fingerprint(source, analyzer) == expected
    assert comparison_epoch_fingerprint(source, {**analyzer, "extension_analyzer_version": "extension-semantic/v2"}) != expected
    with pytest.raises(ValueError, match="comparison contract"):
        comparison_epoch_fingerprint(source, {**analyzer, "extension_adapter_versions": list(reversed(analyzer["extension_adapter_versions"]))})


def test_mrq_forward_transition_and_request_changes():
    rows = empty(); source = "a" * 64; diff = "b" * 64
    identifier = propose(rows, "sales.approval", "Sales approval", source, diff, ["DIF-AAAAAAAAAAAAAAAA"], [], [{"path": "configuration/a.bsl", "fingerprint": "sha256:" + "c" * 64, "stable_diff_id": "DIF-AAAAAAAAAAAAAAAA"}], "Approve sales", "Sales documents", "high", "Direct code evidence")
    decide(rows, identifier, "adapt", [{"stable_diff_id": "DIF-BBBBBBBBBBBBBBBB"}], [{"coverage_status": "still_required"}], "Behavior is absent", "Adapt code", "keep behavior", ["scenario passes"], "Regression risk", [])
    review(rows, identifier, "request_changes", "reviewer", "add evidence", [], source, diff, "2026-01-01T00:00:00Z", None)
    assert rows["mrq.jsonl"][0]["state"] == "ready_for_review"
    assert rows["mrq.jsonl"][0]["migration_decision"]["agreement_status"] == "changes_requested"
    decide(rows, identifier, "adapt", [{"stable_diff_id": "DIF-BBBBBBBBBBBBBBBB"}], [{"coverage_status": "still_required"}], "Behavior is absent", "Adapt code", "keep behavior", ["scenario passes"], "Regression risk", [])
    review(rows, identifier, "approve", "reviewer", "accepted", [], source, diff, "2026-01-02T00:00:00Z", "c" * 64)
    assert rows["mrq.jsonl"][0]["state"] == "approved"
    validate_graph(rows, source, diff)


@pytest.mark.parametrize("decision", ["adopt_vendor", "adapt", "retain_custom", "out_of_scope"])
def test_every_migration_decision_requires_and_accepts_full_review(decision: str):
    rows = empty(); source = "a" * 64; diff = "b" * 64; dif = "DIF-AAAAAAAAAAAAAAAA"; raw = "sha256:" + "c" * 64
    identifier = propose(rows, f"decision.{decision}", decision, source, diff, [dif], [], [{"path": "configuration/a.bsl", "fingerprint": raw, "stable_diff_id": dif}], "Meaning", "Scope", "high", "Evidence")
    decide(rows, identifier, decision, [{"path": "configuration/a.bsl", "fingerprint": raw}], [{"coverage_status": "still_required"}], "Residual gap", "Target solution", "Rationale", ["Accepted"], "Risk", [])
    review(rows, identifier, "approve", "reviewer", "approved", [], source, diff, "2026-01-01T00:00:00Z", None)
    validate_graph(rows, source, diff, {dif})
    with pytest.raises(ValueError, match="not ready"):
        decide(rows, identifier, decision, [{"path": "configuration/a.bsl", "fingerprint": raw}], [{"coverage_status": "still_required"}], "Gap", "Solution", "Rationale", ["Accepted"], "Risk", [])


def test_noise_requires_matching_approval():
    rows = empty(); source = "a" * 64; diff = "b" * 64; dif = "DIF-AAAAAAAAAAAAAAAA"
    noise = {"rationale": "exporter ordering", "actor": "reviewer", "evidence": [{"path": "configuration/a.xml", "fingerprint": "sha256:" + "c" * 64}]}
    rows["dispositions.jsonl"].append({"stable_diff_id": dif, "primary": True, "approved_noise": noise, "source_generation_id": source, "diff_generation_id": diff})
    with pytest.raises(ValueError, match="approval"):
        validate_graph(rows, source, diff, {dif})
    rows["approvals.jsonl"].append({"schema_version": "1", "event": "approve", "target_id": dif, "fingerprint": sha256(canonical_json(noise)), "actor": "reviewer", "rationale": "reviewed", "timestamp": "2026-01-01T00:00:00Z", "source_generation_id": source, "diff_generation_id": diff, "previous_generation": None, "evidence": []})
    validate_graph(rows, source, diff, {dif})


def test_historical_approval_does_not_approve_a_new_generation():
    rows = empty(); old_source = "a" * 64; old_diff = "b" * 64; source = "c" * 64; diff = "d" * 64
    identifier = propose(rows, "revalidated", "Revalidated", source, diff, ["DIF-AAAAAAAAAAAAAAAA"], [], [{"path": "configuration/a.bsl", "fingerprint": "sha256:" + "e" * 64, "stable_diff_id": "DIF-AAAAAAAAAAAAAAAA"}], "Meaning", "Scope", "high", "Evidence")
    decide(rows, identifier, "adapt", [{"stable_diff_id": "DIF-BBBBBBBBBBBBBBBB"}], [{"coverage_status": "still_required"}], "Gap", "Solution", "Rationale", ["Accepted"], "Risk", [])
    item = rows["mrq.jsonl"][0]; candidate = json.loads(json.dumps(item)); candidate["migration_decision"]["agreement_status"] = "pending_review"
    item["state"] = "approved"; item["migration_decision"]["agreement_status"] = "approved"
    rows["approvals.jsonl"].append({"event": "approve", "target_id": identifier, "fingerprint": sha256(canonical_json(candidate)), "actor": "reviewer", "source_generation_id": old_source, "diff_generation_id": old_diff, "previous_generation": None, "evidence": []})
    with pytest.raises(ValueError, match="matching approval"):
        validate_graph(rows, source, diff)


def test_approved_noise_is_added_atomically_to_candidate_rows():
    rows = empty(); source = "a" * 64; diff = "b" * 64; dif = "DIF-AAAAAAAAAAAAAAAA"
    evidence = [{"path": "configuration/component-manifest.json", "fingerprint": "sha256:" + "c" * 64}]
    approve_noise(rows, dif, "reviewer", "derived aggregate only", evidence, source, diff, "2026-01-01T00:00:00Z", None)
    validate_graph(rows, source, diff, {dif})
    assert rows["approvals.jsonl"][0]["target_id"] == rows["dispositions.jsonl"][0]["stable_diff_id"]


def test_source_evidence_uses_raw_file_fingerprint_not_aggregate_dif_hash():
    rows = empty(); source = "a" * 64; diff = "b" * 64; dif = "DIF-AAAAAAAAAAAAAAAA"; raw = "sha256:" + "c" * 64
    propose(rows, "evidence.raw", "Raw evidence", source, diff, [dif], [], [{"path": "configuration/a.bsl", "fingerprint": raw, "stable_diff_id": dif}], "Behavior", "Scope", "high", "Read directly")
    validate_graph(rows, source, diff, {dif: {"path": "configuration/a.bsl", "before_fingerprint": "sha256:" + "d" * 64, "after_fingerprint": raw, "content_fingerprint": "sha256:" + "e" * 64}})
    rows["mrq.jsonl"][0]["source_customization"]["evidence"][0]["fingerprint"] = "sha256:" + "e" * 64
    with pytest.raises(ValueError, match="does not match"):
        validate_graph(rows, source, diff, {dif: {"path": "configuration/a.bsl", "before_fingerprint": "sha256:" + "d" * 64, "after_fingerprint": raw, "content_fingerprint": "sha256:" + "e" * 64}})


def test_opaque_external_evidence_requires_complete_runtime_contract():
    rows = empty(); source = "a" * 64; diff = "b" * 64; dif = "DIF-AAAAAAAAAAAAAAAA"; raw = "sha256:" + "c" * 64
    identifier = propose(rows, "opaque", "Opaque", source, diff, [dif], [], [{"path": "external/EXT-X/raw.epf", "fingerprint": raw, "stable_diff_id": dif, "opaque_external": True, "evidence_id": "runtime-1"}], "Meaning", "Scope", "high", "Evidence")
    with pytest.raises(ValueError, match="runtime record"):
        validate_graph(rows, source, diff, {dif})
    rows["evidence.jsonl"].append({"schema_version": "1", "evidence_id": "runtime-1", "target_id": identifier, "path": "external/EXT-X/raw.epf", "fingerprint": raw, "source_generation_id": source, "diff_generation_id": diff, "runtime": {"artifact_sha256": raw, "environment_identity": "test/base", "steps": ["open report"], "observed_behavior": "report opens", "result": "passed", "attachments": ["outputs/runtime.txt"], "actor": "tester", "rationale": "source is opaque"}})
    validate_graph(rows, source, diff, {dif})
    rows["evidence.jsonl"][0]["runtime"]["steps"] = []
    with pytest.raises(ValueError, match="incomplete opaque"):
        validate_graph(rows, source, diff, {dif})


def test_superseded_mrq_requires_lineage_and_does_not_own_current_dif():
    rows = empty(); source = "a" * 64; diff = "b" * 64; old_dif = "DIF-AAAAAAAAAAAAAAAA"; new_dif = "DIF-BBBBBBBBBBBBBBBB"; raw = "sha256:" + "c" * 64
    old = propose(rows, "old", "Old", source, diff, [old_dif], [], [{"path": "old.bsl", "fingerprint": raw, "stable_diff_id": old_dif}], "Old", "Scope", "high", "Evidence")
    new = propose(rows, "new", "New", source, diff, [new_dif], [], [{"path": "new.bsl", "fingerprint": raw, "stable_diff_id": new_dif}], "New", "Scope", "high", "Evidence")
    rows["mrq.jsonl"][0]["state"] = "superseded"
    with pytest.raises(ValueError, match="exact lineage"):
        validate_graph(rows, source, diff, {new_dif})
    rows["lineage.jsonl"].append({"schema_version": "1", "kind": "supersede", "source_ids": [old], "target_ids": [new], "rationale": "meaning changed", "evidence": [{"old": raw, "new": raw}], "actor": "researcher", "source_generation_id": source, "diff_generation_id": diff})
    validate_graph(rows, source, diff, {new_dif})


def test_split_atomically_supersedes_source_and_reassigns_every_primary_dif():
    rows = empty(); source = "a" * 64; diff = "b" * 64; diffs = ["DIF-AAAAAAAAAAAAAAAA", "DIF-BBBBBBBBBBBBBBBB"]; raw = "sha256:" + "c" * 64
    original = propose(rows, "combined", "Combined", source, diff, diffs, [], [{"path": f"{value}.bsl", "fingerprint": raw, "stable_diff_id": value} for value in diffs], "Combined", "Scope", "high", "Evidence")
    targets = [{"semantic_key": f"part-{index}", "title": f"Part {index}", "stable_diff_ids": [value], "supporting_diff_ids": [], "evidence": [{"path": f"{value}.bsl", "fingerprint": raw, "stable_diff_id": value}], "business_meaning": f"Part {index}", "scope": "Scope", "confidence": "high", "rationale": "Direct evidence"} for index, value in enumerate(diffs, 1)]
    identifiers = restructure(rows, "split", [original], targets, "two meanings", [{"old": "combined", "new": "separate"}], "researcher", source, diff)
    validate_graph(rows, source, diff, set(diffs))
    assert len(identifiers) == 2 and rows["mrq.jsonl"][0]["state"] == "superseded"
    assert rows["lineage.jsonl"][0]["source_ids"] == [original] and rows["lineage.jsonl"][0]["target_ids"] == identifiers


def test_republishing_identical_canonical_generation_does_not_rewrite_manifest(tmp_path: Path):
    source = "a" * 64; diff = "b" * 64
    (tmp_path / "research").mkdir()
    (tmp_path / "research/active-generation.json").write_text(json.dumps({"schema_version": "1", "canonical_generation_id": None, "source_generation_id": source, "diff_generation_id": diff}), encoding="utf-8")
    (tmp_path / "research/active-source-generation.json").write_text(json.dumps({"generation_id": source, "acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "normalizer_version": "1", "representation_schema": "xml-hierarchical"}), encoding="utf-8")
    (tmp_path / "research/active-diff-generation.json").write_text(json.dumps({"generation_id": diff, "source_generation_id": source}), encoding="utf-8")
    inventory = tmp_path / "analysis/indexes/generations" / diff / "diff-inventory.csv"
    inventory.parent.mkdir(parents=True)
    inventory.write_text("stable_diff_id,comparison_id,source_generation,before_role,after_role,change_type,path,object_kind,object_name,area,before_fingerprint,after_fingerprint,content_fingerprint\n", encoding="utf-8")
    epoch = {"acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "normalizer_version": "1", "representation_schema": "xml-hierarchical"}
    pointer = publish(tmp_path, empty(), source, diff, epoch, None)
    manifest = tmp_path / "research/generations" / pointer["canonical_generation_id"] / "manifest.json"
    first = manifest.read_bytes()
    assert publish(tmp_path, empty(), source, diff, epoch, pointer["canonical_generation_id"]) == pointer
    assert manifest.read_bytes() == first
    assert active(tmp_path)["pointer"] == pointer
    (tmp_path / "research/active-generation.json").write_text(json.dumps({**pointer, "source_generation_id": "c" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="mixed active generations"):
        active(tmp_path)


def test_new_comparison_epoch_requires_empty_ledgers(tmp_path: Path):
    source = "a" * 64; diff = "b" * 64
    (tmp_path / "research").mkdir()
    (tmp_path / "research/active-generation.json").write_text(json.dumps({"schema_version": "1", "canonical_generation_id": None, "source_generation_id": source, "diff_generation_id": diff}), encoding="utf-8")
    (tmp_path / "research/active-source-generation.json").write_text(json.dumps({"generation_id": source, "acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "normalizer_version": "1", "representation_schema": "xml-hierarchical"}), encoding="utf-8")
    (tmp_path / "research/active-diff-generation.json").write_text(json.dumps({"generation_id": diff, "source_generation_id": source}), encoding="utf-8")
    inventory = tmp_path / "analysis/indexes/generations" / diff / "diff-inventory.csv"
    inventory.parent.mkdir(parents=True)
    inventory.write_text("stable_diff_id,comparison_id,source_generation,before_role,after_role,change_type,path,object_kind,object_name,area,before_fingerprint,after_fingerprint,content_fingerprint\n", encoding="utf-8")
    first = publish(tmp_path, empty(), source, diff, {"acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "normalizer_version": "1", "representation_schema": "xml-hierarchical"}, None)
    changed_epoch = {"acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "normalizer_version": "2", "representation_schema": "xml-hierarchical"}
    nonempty = empty(); nonempty["evidence.jsonl"].append({"path": "configuration/a.bsl"})
    with pytest.raises(ValueError, match="new comparison epoch must start with empty"):
        publish(tmp_path, nonempty, source, diff, changed_epoch, first["canonical_generation_id"])
    second = publish(tmp_path, empty(), source, diff, changed_epoch, first["canonical_generation_id"])
    (tmp_path / "research/active-source-generation.json").write_text(json.dumps({"generation_id": source, **changed_epoch}), encoding="utf-8")
    assert second["canonical_generation_id"] != first["canonical_generation_id"]
    assert all(not active(tmp_path)[name] for name in FILES)


def test_same_epoch_revalidation_keeps_history_and_requires_fresh_approval(tmp_path: Path):
    import csv
    old_source, new_source, old_diff, new_diff = (char * 64 for char in "abcd")
    epoch = {"acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "normalizer_version": "1", "representation_schema": "xml-hierarchical"}
    dif, raw = "DIF-AAAAAAAAAAAAAAAA", "sha256:" + "e" * 64
    (tmp_path / "research").mkdir()
    source_pointer = {"generation_id": old_source, **epoch}
    (tmp_path / "research/active-source-generation.json").write_text(json.dumps(source_pointer), encoding="utf-8")
    (tmp_path / "research/active-generation.json").write_text(json.dumps({"schema_version": "1", "canonical_generation_id": None, "source_generation_id": old_source, "diff_generation_id": old_diff}), encoding="utf-8")
    header = "stable_diff_id,comparison_id,source_generation,before_role,after_role,change_type,path,object_kind,object_name,area,before_fingerprint,after_fingerprint,content_fingerprint\n"
    row = f"{dif},CMP-X,{old_source},vendor_baseline,target_cf,modified,configuration/a.bsl,file,a.bsl,configuration,sha256:{'f' * 64},{raw},sha256:{'1' * 64}\n"
    for generation, source in ((old_diff, old_source), (new_diff, new_source)):
        root = tmp_path / "analysis/indexes/generations" / generation; root.mkdir(parents=True)
        (root / "diff-inventory.csv").write_text(header + row.replace(old_source, source), encoding="utf-8")
        with (root / "target-coverage.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=("customer_diff_id", "source_generation", "target_diff_ids", "coverage_status", "evidence_ref", "notes")); writer.writeheader(); writer.writerow({"customer_diff_id": dif, "source_generation": source, "target_diff_ids": "", "coverage_status": "still_required", "evidence_ref": "same", "notes": ""})
    (tmp_path / "research/active-diff-generation.json").write_text(json.dumps({"generation_id": old_diff, "source_generation_id": old_source}), encoding="utf-8")
    rows = empty(); identifier = propose(rows, "same", "Same", old_source, old_diff, [dif], [], [{"path": "configuration/a.bsl", "fingerprint": raw, "stable_diff_id": dif}], "Meaning", "Scope", "high", "Evidence")
    decide(rows, identifier, "adapt", [{"path": "configuration/a.bsl", "fingerprint": raw}], [{"customer_diff_id": dif, "target_diff_ids": "", "coverage_status": "still_required", "evidence_ref": "same"}], "Gap", "Solution", "Rationale", ["Accepted"], "Risk", [])
    review(rows, identifier, "approve", "reviewer", "approved", [], old_source, old_diff, "2026-01-01T00:00:00Z", None)
    first = publish(tmp_path, rows, old_source, old_diff, epoch, None)
    (tmp_path / "research/active-source-generation.json").write_text(json.dumps({"generation_id": new_source, **epoch}), encoding="utf-8")
    (tmp_path / "research/active-diff-generation.json").write_text(json.dumps({"generation_id": new_diff, "source_generation_id": new_source}), encoding="utf-8")
    second = revalidate_unchanged(tmp_path, {"generation_id": new_source, **epoch}, {"generation_id": new_diff}, "reviewer", "unchanged", "2026-01-02T00:00:00Z")
    state = active(tmp_path)
    assert second != first and state["mrq.jsonl"][0]["state"] == "ready_for_review"
    assert state["approvals.jsonl"] == []
    assert state["lineage.jsonl"][0]["kind"] == "revalidate"
