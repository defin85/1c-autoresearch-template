import json
import shutil
from pathlib import Path

import pytest

from one_c_autoresearch import indexes


REPO = Path(__file__).parents[1]


def test_index_executable_is_found_above_nested_project(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "OneC/rlm-tools-bsl/.venv/bin/rlm-bsl-index"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    repo = tmp_path / "OneC/Presail/project"
    repo.mkdir(parents=True)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert indexes.discover_executable(repo) == str(executable)


def test_next_work_indexes_every_source_component_before_research(tmp_path: Path, monkeypatch) -> None:
    from one_c_autoresearch.service import ApplicationService
    from one_c_autoresearch import workflow

    service = ApplicationService.__new__(ApplicationService)
    service.repo = tmp_path
    service.rlm_executable = "rlm-bsl-index"
    service.snapshot = lambda: {}
    monkeypatch.setattr(workflow, "next_work", lambda _repo: {"action": "mrq.discover-next", "work_unit": {"id": "DIF-1"}})
    monkeypatch.setattr(indexes, "discover", lambda _repo: [{"component_id": "b"}, {"component_id": "a"}])
    monkeypatch.setattr(indexes, "statuses", lambda *_args, **_kwargs: [
        {"component_id": "b", "status": "stale", "index_key": "b-key"},
        {"component_id": "a", "status": "stale", "index_key": "a-key"},
    ])
    monkeypatch.setattr(indexes, "cli_probe", lambda *_args: {})
    work = service.next()
    assert work["action"] == "indexes.build"
    assert work["work_unit"]["component_ids"] == ["b", "a"]


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


def test_routed_pointer_metadata_avoids_source_tree_scan(monkeypatch):
    monkeypatch.setattr(indexes, "_component_fingerprint", lambda _path: (_ for _ in ()).throw(AssertionError("source tree scanned")))
    rows = indexes.discover(REPO)
    assert all(row["fingerprint"].startswith("sha256:") and row["bsl_file_count"] >= 0 for row in rows)


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
    if not (REPO / "outputs/projections.json").is_file():
        pytest.skip("repository has no published projections")
    before = status(REPO), (REPO / "outputs/projections.json").read_bytes()
    indexes.ensure(REPO, lambda *_: {"ready": True}, state_root=tmp_path)
    assert (status(REPO), (REPO / "outputs/projections.json").read_bytes()) == before
