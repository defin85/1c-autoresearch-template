import json
import shutil
from pathlib import Path

import pytest

from one_c_autoresearch import indexes


REPO = Path(__file__).parents[1]


def test_components_are_generation_bound_and_never_role_parents(tmp_path: Path):
    rows = indexes.discover(REPO)
    assert [row["component_id"] for row in rows] == sorted(row["component_id"] for row in rows)
    assert all(":configuration" in row["component_id"] or ":extension:" in row["component_id"] or ":external:" in row["component_id"] for row in rows)
    assert all(row["source_generation_id"] for row in rows)
    assert all(row["path"].split("/")[-1] in {"configuration", "source"} or "/extensions/" in row["path"] for row in rows)
    assert indexes.required_component_ids(REPO, ["configuration/CommonModules/Test/Ext/Module.bsl"], ("vendor_baseline", "target_cf")) == ["target_cf:configuration", "vendor_baseline:configuration"]
    extension = "471acdde-293c-497c-bd55-e6ab48d98dc4"
    assert indexes.required_component_ids(REPO, [f"extensions/{extension}/Configuration.xml"], ("target_cf",)) == [f"target_cf:extension:{extension}"]
    assert indexes.required_component_ids(REPO, [f"target_cf/extensions/{extension}/Configuration.xml"], ("vendor_baseline", "target_cf")) == [f"target_cf:extension:{extension}"]
    evidence = indexes.canonical_evidence(REPO, "target_cf:configuration", "CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl")
    assert evidence["path"] == "configuration/CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl" and evidence["fingerprint"].startswith("sha256:")
    with pytest.raises(ValueError, match="unsafe relative path"):
        indexes.canonical_evidence(REPO, "target_cf:configuration", "../rlm-snippet")


def test_index_ensure_reuses_ready_state_and_rebuild_is_confirmed(tmp_path: Path):
    first = indexes.ensure(REPO, lambda *_: {"ready": True}, state_root=tmp_path)
    assert all(row["status"] in {"ready", "not_indexable"} for row in first)
    second = indexes.ensure(
        REPO,
        lambda *_: (_ for _ in ()).throw(AssertionError("ready index rebuilt")),
        state_root=tmp_path,
    )
    assert [row["status"] for row in second] == [row["status"] for row in first]
    with pytest.raises(ValueError, match="explicit confirmation"):
        indexes.ensure(REPO, lambda *_: {"ready": True}, state_root=tmp_path, rebuild=True)
    rebuilt = indexes.ensure(REPO, lambda *_: {"ready": True}, state_root=tmp_path, rebuild=True, confirmed=True)
    assert all(row["status"] in {"ready", "not_indexable"} for row in rebuilt)


def test_unsupported_representation_is_not_indexed(tmp_path: Path):
    (tmp_path / "research").mkdir(); (tmp_path / "sources/generations/g").mkdir(parents=True)
    (tmp_path / "research/active-source-generation.json").write_text('{"generation_id":"g","representation_schema":"binary"}', encoding="utf-8")
    (tmp_path / "research/indexing.toml").write_text('engine="rlm-tools-bsl"\nengine_version="1.0.0"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported source representation"):
        indexes.discover(tmp_path)


def test_index_loss_does_not_change_canonical_workflow(tmp_path: Path):
    from one_c_autoresearch.workflow import status
    before = status(REPO), (REPO / "outputs/projections.json").read_bytes()
    indexes.ensure(REPO, lambda *_: {"ready": True}, state_root=tmp_path)
    assert (status(REPO), (REPO / "outputs/projections.json").read_bytes()) == before
