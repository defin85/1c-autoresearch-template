import json
import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from one_c_autoresearch import indexes, search_runtime


REPO = Path(__file__).parents[1] / "templates/research-repo"
requires_source_generation = pytest.mark.skipif(
    not (REPO / "research/active-source-generation.json").is_file(),
    reason="requires a concrete research repository source generation",
)


def _schema2_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "research").mkdir(parents=True)
    (repo / "project.toml").write_text('[project]\nid = "migration-test"\n')
    (repo / "research/indexing.toml").write_text(
        'schema_version = "2"\n\n'
        '[[backends]]\nadapter_id = "bsl-analyzer"\nengine_version = "1.0"\n\n'
        '[routes]\n"text-search" = ["bsl-analyzer"]\n',
        encoding="utf-8",
    )
    return repo


def _schema3_candidate() -> dict:
    return {
        "schema_version": "3",
        "machine_contract_version": "1.3",
        "backends": [{"adapter_id": "bsl-analyzer", "engine_version": "1.0"}],
        "routes": {
            "code-search-lexical": ["bsl-analyzer"],
            "code-search-hybrid": ["bsl-analyzer"],
        },
        "service_profiles": {
            "lexical": "source-search-lexical/v2",
            "hybrid": "source-search-hybrid/v2",
        },
    }


def _complete_bsl_probe(_repo: Path, _backend: dict) -> dict:
    return {
        "available": True,
        "contract_version": "1.3",
        "surface_manifest": {
            "state": "complete",
            "machine_contract_version": "1.3",
        },
    }


def test_schema3_migration_is_previewed_backed_up_and_does_not_build(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _schema2_repo(tmp_path)
    state = tmp_path / "state"
    original = (repo / "research/indexing.toml").read_bytes()
    monkeypatch.setattr(indexes, "probe_backend", _complete_bsl_probe)
    operations = {
        "lexical": ["code.search_lexical"],
        "hybrid": ["code.search_hybrid", "graph.callers"],
    }
    preview = indexes.preview_schema3_migration(
        repo, _schema3_candidate(), indexes.config_fingerprint(repo), operations,
        ("legacy-running",),
    )
    assert (repo / "research/indexing.toml").read_bytes() == original
    assert preview["explicit_routes"] == {
        "lexical": ["bsl-analyzer"],
        "hybrid": ["bsl-analyzer"],
    }
    result = indexes.apply_schema3_migration(
        repo, _schema3_candidate(), indexes.config_fingerprint(repo),
        preview["plan_fingerprint"], operations, ("legacy-running",), state,
    )
    assert result["build_started"] is False
    assert result["preserved_v1_inflight"] == ["legacy-running"]
    assert indexes.load_config(repo)["source_schema_version"] == "3"
    backup = Path(result["backup"]["path"])
    assert backup.read_bytes() == original
    assert backup.stat().st_mode & 0o777 == 0o600
    marker = (
        state / indexes.repository_instance_fingerprint(repo).split(":", 1)[1]
        / "schema3/stale-semantic-targets.json"
    )
    assert json.loads(marker.read_text())["targets"] == ["code-search-hybrid"]


def test_schema3_migration_requires_exact_contract_and_rejects_legacy_operation(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _schema2_repo(tmp_path)
    fingerprint = indexes.config_fingerprint(repo)
    operations = {
        "lexical": ["code.search_lexical"],
        "hybrid": ["code.search_hybrid"],
    }
    monkeypatch.setattr(
        indexes, "probe_backend",
        lambda *_args: {
            "available": True,
            "contract_version": "1.2",
            "surface_manifest": {"state": "complete", "machine_contract_version": "1.2"},
        },
    )
    with pytest.raises(RuntimeError, match="contract 1.3"):
        indexes.preview_schema3_migration(
            repo, _schema3_candidate(), fingerprint, operations,
        )
    monkeypatch.setattr(indexes, "probe_backend", _complete_bsl_probe)
    operations["hybrid"].append("find_references")
    with pytest.raises(ValueError, match="not admitted"):
        indexes.preview_schema3_migration(
            repo, _schema3_candidate(), fingerprint, operations,
        )


def test_schema3_rollback_checks_readiness_and_restores_schema2(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _schema2_repo(tmp_path)
    state = tmp_path / "state"
    original = (repo / "research/indexing.toml").read_bytes()
    monkeypatch.setattr(indexes, "probe_backend", _complete_bsl_probe)
    operations = {
        "lexical": ["code.search_lexical"],
        "hybrid": ["code.search_hybrid"],
    }
    migration = indexes.preview_schema3_migration(
        repo, _schema3_candidate(), indexes.config_fingerprint(repo), operations,
    )
    indexes.apply_schema3_migration(
        repo, _schema3_candidate(), indexes.config_fingerprint(repo),
        migration["plan_fingerprint"], operations, state_root=state,
    )
    schema3 = (repo / "research/indexing.toml").read_bytes()
    rollback = indexes.preview_schema3_rollback(
        repo, indexes.config_fingerprint(repo), state,
        v2_inflight_ids=("v2-running",), purge_v2_state=True,
    )
    with pytest.raises(RuntimeError, match="readiness"):
        indexes.apply_schema3_rollback(
            repo, indexes.config_fingerprint(repo), rollback["plan_fingerprint"],
            lambda _backup: False, state, v2_inflight_ids=("v2-running",),
            purge_v2_state=True, admission_closed=True,
            inflight_handling="drained",
        )
    assert (repo / "research/indexing.toml").read_bytes() == schema3
    result = indexes.apply_schema3_rollback(
        repo, indexes.config_fingerprint(repo), rollback["plan_fingerprint"],
        lambda backup: backup.read_bytes() == original, state,
        v2_inflight_ids=("v2-running",), purge_v2_state=True,
        admission_closed=True, inflight_handling="cancelled",
    )
    assert result["prior_runtime_ready"] is True
    assert (repo / "research/indexing.toml").read_bytes() == original
    assert indexes.load_config(repo)["source_schema_version"] == "2"


def test_index_executable_is_found_above_nested_project(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "OneC/rlm-tools-bsl/.venv/bin/rlm-bsl-index"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    repo = tmp_path / "OneC/Presail/project"
    repo.mkdir(parents=True)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert indexes.discover_executable(repo) == str(executable)


def test_backend_tool_inventory_marks_only_routed_adapter_required(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        indexes,
        "load_config",
        lambda _repo: {
            "backends": [{"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"}],
            "routes": {"text-search": ["rlm-tools-bsl"]},
        },
    )
    monkeypatch.setattr(
        indexes,
        "backend_executable",
        lambda _repo, adapter_id: "/tools/rlm-bsl-index"
        if adapter_id == "rlm-tools-bsl"
        else None,
    )
    monkeypatch.setattr(
        indexes,
        "probe_backend",
        lambda _repo, _backend: {
            "available": True,
            "engine_version": "1.30.0",
        },
    )
    tools = {item["tool_id"]: item for item in indexes.backend_tool_inventory(tmp_path)}
    assert tools["rlm-tools-bsl"]["required"] is True
    assert tools["rlm-tools-bsl"]["route_capabilities"] == ["text-search"]
    assert tools["rlm-tools-bsl"]["status"] == "ready"
    assert tools["bsl-analyzer"]["required"] is False
    assert tools["bsl-analyzer"]["status"] == "unavailable"


def test_backend_tool_inventory_reads_unconfigured_adapter_version(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        indexes,
        "load_config",
        lambda _repo: {"backends": [], "routes": {}},
    )
    monkeypatch.setattr(
        indexes,
        "backend_executable",
        lambda _repo, adapter_id: "/tools/bsl-analyzer"
        if adapter_id == "bsl-analyzer"
        else None,
    )
    monkeypatch.setattr(indexes, "cli_version", lambda _executable: "0.2.65")
    tools = {item["tool_id"]: item for item in indexes.backend_tool_inventory(tmp_path)}
    assert tools["bsl-analyzer"]["instances"][0]["version"] == "0.2.65"


def test_next_work_indexes_every_source_component_before_research(tmp_path: Path, monkeypatch) -> None:
    from one_c_autoresearch.service import ApplicationService
    from one_c_autoresearch import workflow

    service = ApplicationService.__new__(ApplicationService)
    service.repo = tmp_path
    service.rlm_executable = "rlm-bsl-index"
    service.snapshot = lambda: {}
    monkeypatch.setattr(workflow, "next_work", lambda _repo: {"action": "mrq.discover-next", "work_unit": {"id": "DIF-1"}})
    monkeypatch.setattr(indexes, "discover", lambda _repo: [
        {"component_id": "b", "representation": "xml-hierarchical/v1", "bsl_file_count": 1},
        {"component_id": "a", "representation": "xml-hierarchical/v1", "bsl_file_count": 1},
    ])
    monkeypatch.setattr(indexes, "load_config", lambda _repo: {
        "routes": {"text-search": ["rlm-tools-bsl"]},
    })
    monkeypatch.setattr(indexes, "backend_statuses", lambda *_args, **_kwargs: [
        {"component_id": "b", "adapter_id": "rlm-tools-bsl", "status": "stale", "capabilities": []},
        {"component_id": "a", "adapter_id": "rlm-tools-bsl", "status": "stale", "capabilities": []},
    ])
    monkeypatch.setattr(indexes, "cli_probe", lambda *_args: {})
    work = service.next()
    assert work["action"] == "indexes.build"
    assert work["work_unit"]["component_ids"] == ["a", "b"]
    assert work["work_unit"]["backend_ids"] == ["rlm-tools-bsl"]


def test_next_gate_checks_only_capabilities_used_by_assigned_profiles(tmp_path: Path, monkeypatch) -> None:
    from one_c_autoresearch.service import ApplicationService
    from one_c_autoresearch import workflow

    service = ApplicationService.__new__(ApplicationService)
    service.repo = tmp_path
    service.agent_profiles = {
        "local": {"source_search": {"operations": ["search_text"]}},
    }
    service.snapshot = lambda: {}
    monkeypatch.setattr(
        workflow,
        "next_work",
        lambda _repo: {"action": "mrq.discover-next", "work_unit": {"id": "DIF-1"}},
    )
    monkeypatch.setattr(
        workflow,
        "step_configurations",
        lambda _repo: [{
            "step": {
                "agent_phases": [{
                    "roles": [{"agent_profile": "local"}],
                }],
            },
        }],
    )
    component = {
        "component_id": "target_cf:configuration",
        "representation": "xml-hierarchical/v1",
        "bsl_file_count": 1,
    }
    monkeypatch.setattr(indexes, "discover", lambda _repo: [component])
    monkeypatch.setattr(
        indexes,
        "load_config",
        lambda _repo: {
            "routes": {
                "text-search": ["rlm-tools-bsl"],
                "callers": ["rlm-tools-bsl"],
            },
        },
    )
    monkeypatch.setattr(
        indexes,
        "backend_statuses",
        lambda *_args, **_kwargs: [{
            **component,
            "adapter_id": "rlm-tools-bsl",
            "status": "ready",
            "capabilities": ["text-search"],
        }],
    )
    assert service.next()["action"] == "mrq.discover-next"


def test_configuration_requires_routes_used_by_assigned_profiles():
    from one_c_autoresearch.service import ApplicationService

    service = ApplicationService.__new__(ApplicationService)
    service.repo = REPO
    service.agent_profiles = {
        "local": {"source_search": {"operations": ["search_text"]}},
    }
    assert service._required_index_capabilities() == ("text-search",)
    with pytest.raises(ValueError, match="missing required indexing routes"):
        service.preview_index_configuration({
            "configuration": {
                "schema_version": "2",
                "backends": [{
                    "adapter_id": "rlm-tools-bsl",
                    "engine_version": "1.30.0",
                }],
                "routes": {},
            },
            "expected_file_fingerprint": indexes.config_fingerprint(REPO),
        })


@requires_source_generation
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


@requires_source_generation
def test_routed_pointer_metadata_avoids_source_tree_scan(monkeypatch):
    monkeypatch.setattr(indexes, "_component_fingerprint", lambda _path: (_ for _ in ()).throw(AssertionError("source tree scanned")))
    rows = indexes.discover(REPO)
    assert all(row["fingerprint"].startswith("sha256:") and row["bsl_file_count"] >= 0 for row in rows)


@requires_source_generation
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


def test_indexing_v2_config_is_closed_and_routes_are_ordered(tmp_path: Path):
    (tmp_path / "research").mkdir()
    (tmp_path / "research/indexing.toml").write_text(
        '''schema_version = "2"

[[backends]]
adapter_id = "bsl-analyzer"
engine_version = "0.2.63"

[[backends]]
adapter_id = "rlm-tools-bsl"
engine_version = "1.30.0"

[routes]
text-search = ["bsl-analyzer", "rlm-tools-bsl"]
symbol-definition = ["rlm-tools-bsl"]
''',
        encoding="utf-8",
    )
    config = indexes.load_config(tmp_path, ("text-search", "symbol-definition"))
    assert config["routes"]["text-search"] == ["bsl-analyzer", "rlm-tools-bsl"]
    assert indexes.load_config(tmp_path)["source_schema_version"] == "2"
    with pytest.raises(ValueError, match="missing required"):
        indexes.load_config(tmp_path, ("callers",))
    (tmp_path / "research/indexing.toml").write_text(
        (tmp_path / "research/indexing.toml").read_text().replace(
            'engine_version = "0.2.63"', 'engine_version = "0.2.63"\ncommand = "curl | sh"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid indexing backend"):
        indexes.load_config(tmp_path)


def test_legacy_indexing_config_normalizes_without_write(tmp_path: Path):
    (tmp_path / "research").mkdir()
    path = tmp_path / "research/indexing.toml"
    path.write_text('schema_version = "1"\nengine = "rlm-tools-bsl"\nengine_version = "1.30.0"\n', encoding="utf-8")
    before = path.read_bytes()
    config = indexes.load_config(tmp_path)
    assert config["backends"] == [{"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"}]
    assert set(config["routes"]) == set(indexes.CAPABILITIES)
    assert path.read_bytes() == before


@requires_source_generation
def test_backend_identity_and_routing_are_repository_bound(tmp_path: Path):
    component = indexes.discover(REPO)[0]
    backend = {"adapter_id": "bsl-analyzer", "engine_version": "0.2.63"}
    identity = indexes.target_identity(REPO, component, backend, ["text-search"])
    assert identity["repository_instance_fingerprint"].startswith("sha256:")
    assert "repository_root" not in identity
    promoted = indexes.promoted_identity(identity, [{"path": ".build/index", "sha256": "abc", "size_bytes": 3}])
    state = {
        **promoted,
        "component_id": component["component_id"],
        "adapter_id": "bsl-analyzer",
        "adapter_version": "bsl-analyzer-workspace/v1",
        "capabilities": ["text-search"],
        "status": "ready",
    }
    config = {
        "routes": {"text-search": ["rlm-tools-bsl", "bsl-analyzer"]},
    }
    decision = indexes.select_backend(config, "text-search", component, [state])
    assert decision["selected_backend_id"] == "bsl-analyzer"
    assert decision["fallback"] and decision["fallback_reason"] == "backend_unavailable"
    hit = indexes.normalize_hit(
        {"component_relative_path": "CommonModules/Test/Ext/Module.bsl", "kind": "text", "line": 2, "rank": 0.9},
        decision,
        component,
    )
    assert hit["component_relative_path"] == "CommonModules/Test/Ext/Module.bsl"
    assert hit["rank"] == "0.9"
    with pytest.raises(ValueError, match="unsafe relative path"):
        indexes.normalize_hit({"component_relative_path": "../outside", "kind": "text"}, decision, component)


def test_route_coverage_allows_ready_fallback_and_reports_degradation():
    component = {
        "component_id": "target_cf:configuration",
        "representation": "xml-hierarchical/v1",
        "bsl_file_count": 1,
    }
    config = {
        "routes": {
            "text-search": ["bsl-analyzer", "rlm-tools-bsl"],
        },
    }
    states = [
        {
            "component_id": component["component_id"],
            "adapter_id": "bsl-analyzer",
            "status": "unavailable",
            "capabilities": [],
        },
        {
            "component_id": component["component_id"],
            "adapter_id": "rlm-tools-bsl",
            "status": "ready",
            "capabilities": ["text-search"],
        },
    ]
    coverage = indexes.route_coverage(config, [component], states)
    assert coverage["blockers"] == []
    assert coverage["degraded"] == [{
        "component_id": component["component_id"],
        "capability": "text-search",
        "selected_backend_id": "rlm-tools-bsl",
        "fallback_reason": "index_not_ready",
        "skipped": [{"adapter_id": "bsl-analyzer", "reason": "index_not_ready"}],
    }]


def test_index_configuration_preview_apply_is_stale_safe_and_starts_no_build(tmp_path: Path):
    (tmp_path / "research").mkdir()
    path = tmp_path / "research/indexing.toml"
    path.write_text('schema_version = "1"\nengine = "rlm-tools-bsl"\nengine_version = "1.30.0"\n', encoding="utf-8")
    expected = indexes.config_fingerprint(tmp_path)
    candidate = {
        "schema_version": "2",
        "backends": [
            {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"},
            {"adapter_id": "bsl-analyzer", "engine_version": "0.2.63"},
        ],
        "routes": {
            capability: ["bsl-analyzer", "rlm-tools-bsl"]
            for capability in indexes.CAPABILITIES
        },
    }
    preview = indexes.preview_config(tmp_path, candidate, expected)
    assert preview["rebuild_backends"] == ["bsl-analyzer"]
    assert path.read_text().startswith('schema_version = "1"')
    with pytest.raises(RuntimeError, match="stale.*plan"):
        indexes.apply_config(tmp_path, candidate, expected, "sha256:stale")
    result = indexes.apply_config(tmp_path, candidate, expected, preview["plan_fingerprint"])
    assert result["build_started"] is False
    assert indexes.load_config(tmp_path)["backends"] == candidate["backends"]
    with pytest.raises(RuntimeError, match="stale.*configuration"):
        indexes.preview_config(tmp_path, candidate, expected)


def test_index_configuration_exact_v1_downgrade_and_blocked_downgrade(tmp_path: Path):
    (tmp_path / "research").mkdir()
    path = tmp_path / "research/indexing.toml"
    representable = {
        "schema_version": "2",
        "backends": [{"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"}],
        "routes": {
            capability: ["rlm-tools-bsl"]
            for capability in indexes.CAPABILITIES
        },
    }
    path.write_bytes(indexes.serialize_config(representable))
    expected = indexes.config_fingerprint(tmp_path)
    preview = indexes.preview_config(tmp_path, {"schema_version": "1"}, expected)
    assert preview["target_schema_version"] == "1"
    assert preview["normalized_file"] == (
        'schema_version = "1"\n'
        'engine = "rlm-tools-bsl"\n'
        'engine_version = "1.30.0"\n'
    )
    indexes.apply_config(
        tmp_path,
        {"schema_version": "1"},
        expected,
        preview["plan_fingerprint"],
    )
    assert indexes.load_config(tmp_path)["source_schema_version"] == "1"

    blocked = {
        "schema_version": "2",
        "backends": [
            {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"},
            {"adapter_id": "bsl-analyzer", "engine_version": "0.2.63"},
        ],
        "routes": {
            capability: ["bsl-analyzer", "rlm-tools-bsl"]
            for capability in indexes.CAPABILITIES
        },
    }
    path.write_bytes(indexes.serialize_config(blocked))
    with pytest.raises(ValueError, match="not exactly representable"):
        indexes.preview_config(
            tmp_path,
            {"schema_version": "1"},
            indexes.config_fingerprint(tmp_path),
        )


def test_schema2_backup_is_owner_only_and_prior_runtime_reports_ready(
    tmp_path: Path, monkeypatch
):
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    (repo / "research").mkdir(parents=True)
    (repo / "project.toml").write_text('[project]\nid = "backup-test"\n', encoding="utf-8")
    config = {
        "schema_version": "2",
        "backends": [{"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"}],
        "routes": {
            capability: ["rlm-tools-bsl"]
            for capability in indexes.CAPABILITIES
        },
    }
    original = indexes.serialize_config(config)
    (repo / "research/indexing.toml").write_bytes(original)

    captured = indexes.capture_schema2_backup(repo, state)
    backup = Path(captured["path"])
    assert backup.read_bytes() == original
    assert backup.stat().st_mode & 0o777 == 0o600
    assert captured["fingerprint"] == indexes.config_fingerprint(repo)

    restored = tmp_path / "prior-runtime"
    (restored / "research").mkdir(parents=True)
    (restored / "research/indexing.toml").write_bytes(backup.read_bytes())
    assert indexes.load_config(restored) == {
        **config,
        "source_schema_version": "2",
    }
    component = {
        "component_id": "target_cf:configuration",
        "path": "target_cf/configuration",
        "fingerprint": "sha256:source",
        "representation": "xml-hierarchical",
        "source_generation_id": "source-1",
        "engine": "rlm-tools-bsl",
        "engine_version": "1.30.0",
        "bsl_file_count": 1,
    }
    monkeypatch.setattr(indexes, "discover", lambda _repo: [component])
    monkeypatch.setattr(indexes, "probe_backend", lambda *_args, **_kwargs: {
        "available": True,
        "capabilities": list(indexes.CAPABILITIES),
        "contract_version": "provider-query/v1",
        "capability_fingerprint": "sha256:capabilities",
    })
    monkeypatch.setattr(indexes, "ready_backend_state", lambda *_args, **_kwargs: {
        "status": "ready",
        "index_fingerprint": "sha256:index",
        "target_fingerprint": "sha256:target",
        "last_validation": "2026-07-30T00:00:00+00:00",
    })
    status = indexes.backend_statuses(restored)
    assert len(status) == 1
    assert status[0]["status"] == "ready"
    assert status[0]["index_fingerprint"] == "sha256:index"

    assert indexes.capture_schema2_backup(repo, state) == captured
    (repo / "research/indexing.toml").write_text(
        original.decode().replace("1.30.0", "1.31.0"),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="backup conflicts"):
        indexes.capture_schema2_backup(repo, state)


def _minimal_index_repo(root: Path, project_id: str = "index-test") -> tuple[Path, dict]:
    (root / "research").mkdir(parents=True)
    (root / "project.toml").write_text(f'[project]\nid = "{project_id}"\n', encoding="utf-8")
    (root / "research/indexing.toml").write_text(
        'schema_version = "1"\nengine = "rlm-tools-bsl"\nengine_version = "1.30.0"\n',
        encoding="utf-8",
    )
    source = root / "sources/generations/gen/target_cf/configuration"
    (source / "CommonModules/Test/Ext").mkdir(parents=True)
    (source / "CommonModules/Test/Ext/Module.bsl").write_text(
        "Процедура Тест() Экспорт\nКонецПроцедуры\n",
        encoding="utf-8",
    )
    fingerprint = "sha256:" + indexes.sha256(indexes.canonical_json(indexes.file_manifest(source)))
    component = {
        "component_id": "target_cf:configuration",
        "path": "target_cf/configuration",
        "fingerprint": fingerprint,
        "representation": "xml-hierarchical/v1",
        "source_generation_id": "gen",
        "engine": "rlm-tools-bsl",
        "engine_version": "1.30.0",
        "bsl_file_count": 1,
    }
    (root / "research/active-source-generation.json").write_text(
        json.dumps({
            "schema_version": "2",
            "generation_id": "gen",
            "components": [{
                "component_id": component["component_id"],
                "path": component["path"],
                "kind": "configuration",
                "representation_schema": component["representation"],
                "fingerprint": fingerprint,
                "bsl_file_count": 1,
            }],
        }),
        encoding="utf-8",
    )
    return root, component


def test_backend_status_has_safe_recovery_without_mutating_state(
    monkeypatch, tmp_path: Path,
):
    repo, _component = _minimal_index_repo(tmp_path / "repo")
    state = tmp_path / "state"
    monkeypatch.setattr(indexes, "backend_executable", lambda *_args: None)
    row = indexes.backend_statuses(repo, state)[0]
    assert row["status"] == "unavailable"
    assert row["readiness_reason"] == "backend.executable_unavailable"
    assert row["recovery_action"] == "fix_backend_installation"
    assert not state.exists()


def test_ready_validation_does_not_create_operational_state(
    monkeypatch, tmp_path: Path,
):
    repo, component = _minimal_index_repo(tmp_path / "repo")
    state = tmp_path / "state"
    monkeypatch.setattr(
        indexes,
        "probe_backend",
        lambda *_args, **_kwargs: {
            "available": True,
            "executable": "/fixed/rlm-bsl-index",
            "executable_fingerprint": "sha256:binary",
            "capabilities": list(indexes.CAPABILITIES),
            "contract_version": "provider-query/v1",
        },
    )
    assert indexes.ready_backend_state(
        repo,
        component,
        {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"},
        state_root=state,
    ) is None
    assert not state.exists()


def test_backend_probe_uses_bsl_machine_contract(monkeypatch, tmp_path: Path):
    repo, _component = _minimal_index_repo(tmp_path)
    executable = tmp_path / "bsl-analyzer"
    executable.write_bytes(b"fixed binary")
    monkeypatch.setattr(indexes, "backend_executable", lambda *_args: str(executable))
    contract = {
        "contract_version": "1.3",
        "build_version": "0.2.63",
        "mcp": {"profiles": {
            profile: {"tools": [
                {
                    "name": tool,
                    "actions": [{"name": action} for action in actions],
                    **({
                        "output_schema_version": "1",
                        "output_schema_fingerprint": indexes.BSL_SYNTAX_OUTPUT_SCHEMA_FINGERPRINT,
                    } if tool == "syntax_help" else {}),
                }
                for disposition in ("allowed", "denied")
                for tool, actions in groups[disposition].items()
            ]}
            for profile, groups in indexes.BSL_SEARCH_SURFACE_V1.items()
        }},
        "transports": {"workspace": {"broker-required": {
            "backend_pid_required": True,
            "auto_launch": False,
            "stdio_fallback": False,
            "peer_identity": "supervised-pid+platform-trust",
        }}},
    }
    monkeypatch.setattr(
        indexes,
        "_bounded_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, json.dumps(contract), ""),
    )
    probe = indexes.probe_backend(
        repo,
        {"adapter_id": "bsl-analyzer", "engine_version": "0.2.63"},
    )
    assert probe["available"] is True
    assert probe["contract_version"] == "1.3"
    assert set(probe["capabilities"]) == {
        *indexes.BSL_CAPABILITIES,
        *indexes.COMPLETE_SEARCH_CAPABILITIES,
    }
    assert probe["surface_manifest"]["state"] == "complete"

    assert probe["surface_manifest"]["machine_contract_version"] == "1.3"
    assert probe["surface_manifest"]["build_version"] == "0.2.63"
    assert probe["surface_manifest"]["surface_fingerprint"].startswith("sha256:")
    before = probe["capability_fingerprint"]
    executable.write_bytes(b"replaced binary")
    after = indexes.probe_backend(
        repo,
        {"adapter_id": "bsl-analyzer", "engine_version": "0.2.63"},
    )
    assert before != after["capability_fingerprint"]
    assert probe["executable_fingerprint"] != after["executable_fingerprint"]


def test_bsl_lexical_and_hybrid_targets_have_distinct_identities(tmp_path: Path):
    repo, component = _minimal_index_repo(tmp_path)
    backend = {"adapter_id": "bsl-analyzer", "engine_version": "0.2.65"}
    common = (repo, component, backend, list(indexes.COMPLETE_SEARCH_CAPABILITIES), "sha256:tool")
    lexical = indexes.target_identity(*common, modality="lexical")
    hybrid = indexes.target_identity(
        *common, modality="hybrid", embedding_identity="sha256:embedding"
    )
    assert indexes.target_fingerprint(lexical) != indexes.target_fingerprint(hybrid)
    assert "embedding_identity" not in lexical
    assert hybrid["embedding_identity"] == "sha256:embedding"


def test_index_gc_removes_only_reviewed_unreferenced_instances(tmp_path: Path):
    repo, _component = _minimal_index_repo(tmp_path / "repo")
    state = tmp_path / "state"
    target = indexes._operational_root(repo, state) / "targets" / "target"
    current = target / "instances" / "current"
    old = target / "instances" / "old"
    current.mkdir(parents=True)
    old.mkdir()
    (current / "data").write_bytes(b"current")
    (old / "data").write_bytes(b"old")
    (target / "current.json").write_text(
        json.dumps({"instance": "instances/current"})
    )
    plan = indexes.preview_index_gc(repo, state)
    assert [item["instance"] for item in plan["candidates"]] == ["old"]
    result = indexes.apply_index_gc(
        repo, plan["plan_fingerprint"], confirmed=True, state_root=state
    )
    assert result["removed_instances"] == 1
    assert current.is_dir()
    assert not old.exists()


def test_bsl_search_surface_fixture_is_complete_and_content_free():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/bsl-analyzer-contract-1.3.json").read_text()
    )
    expected = {
        profile: {
            disposition: {
                tool: sorted(actions)
                for tool, actions in groups[disposition].items()
            }
            for disposition in ("allowed", "denied")
        }
        for profile, groups in indexes.BSL_SEARCH_SURFACE_V1.items()
    }
    actual = {
        profile: {
            disposition: {
                tool: sorted(actions)
                for tool, actions in groups[disposition].items()
            }
            for disposition in ("allowed", "denied")
        }
        for profile, groups in fixture["profiles"].items()
    }
    assert fixture["machine_contract_version"] == "1.3"
    assert "build_version" not in fixture
    assert actual == expected


def test_bsl_search_surface_fails_closed_on_contract_drift(
    monkeypatch, tmp_path: Path
):
    executable = tmp_path / "bsl-analyzer"
    executable.write_bytes(b"fixed binary")
    contract = {
        "contract_version": "1.3",
        "build_version": "selected",
        "mcp": {"profiles": {
            profile: {"tools": [
                {
                    "name": tool,
                    "actions": [{"name": action} for action in actions],
                    **({
                        "output_schema_version": "1",
                        "output_schema_fingerprint": indexes.BSL_SYNTAX_OUTPUT_SCHEMA_FINGERPRINT,
                    } if tool == "syntax_help" else {}),
                }
                for disposition in ("allowed", "denied")
                for tool, actions in groups[disposition].items()
            ]}
            for profile, groups in indexes.BSL_SEARCH_SURFACE_V1.items()
        }},
        "transports": {"workspace": {"broker-required": {
            "backend_pid_required": True,
            "auto_launch": False,
            "stdio_fallback": False,
            "peer_identity": "supervised-pid+platform-trust",
        }}},
    }
    contract["mcp"]["profiles"]["workspace"]["tools"][0]["actions"].append(
        {"name": "new_unreviewed_action"}
    )
    monkeypatch.setattr(
        indexes,
        "_bounded_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps(contract), ""
        ),
    )
    with pytest.raises(
        indexes.BslSurfaceContractError,
        match="unreviewed additions",
    ):
        indexes._bsl_contract(str(executable), "selected")


def test_bsl_search_surface_reports_partial_when_an_approved_action_is_missing(
    monkeypatch, tmp_path: Path
):
    executable = tmp_path / "bsl-analyzer"
    executable.write_bytes(b"fixed binary")
    contract = {
        "contract_version": "1.3",
        "build_version": "selected",
        "mcp": {"profiles": {
            profile: {"tools": [
                {
                    "name": tool,
                    "actions": [{"name": action} for action in actions],
                    **({
                        "output_schema_version": "1",
                        "output_schema_fingerprint": indexes.BSL_SYNTAX_OUTPUT_SCHEMA_FINGERPRINT,
                    } if tool == "syntax_help" else {}),
                }
                for disposition in ("allowed", "denied")
                for tool, actions in groups[disposition].items()
            ]}
            for profile, groups in indexes.BSL_SEARCH_SURFACE_V1.items()
        }},
        "transports": {"workspace": {"broker-required": {
            "backend_pid_required": True,
            "auto_launch": False,
            "stdio_fallback": False,
            "peer_identity": "supervised-pid+platform-trust",
        }}},
    }
    contract["mcp"]["profiles"]["workspace"]["tools"][0]["actions"].pop()
    monkeypatch.setattr(
        indexes, "_bounded_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps(contract), ""
        ),
    )
    monkeypatch.setattr(indexes, "backend_executable", lambda *_args: str(executable))
    probe = indexes.probe_backend(
        tmp_path, {"adapter_id": "bsl-analyzer", "engine_version": "selected"}
    )
    assert probe["available"] is False
    assert probe["surface_manifest"]["state"] == "partial"


def test_native_text_envelope_requires_one_bounded_text_item():
    assert indexes.native_text_envelope({
        "content": [{"type": "text", "text": "ok"}],
        "isError": False,
    }) == {
        "schema_version": "native-text-envelope/v1",
        "text": "ok",
        "returned_bytes": 2,
    }
    with pytest.raises(RuntimeError, match="invalid text-only"):
        indexes.native_text_envelope({
            "content": [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}],
        })
    with pytest.raises(RuntimeError, match="byte limit"):
        indexes.native_text_envelope(
            {"content": [{"type": "text", "text": "too long"}]},
            max_bytes=2,
        )


def test_every_workspace_v2_operation_maps_to_one_fixed_native_action():
    cases = {
        "code.search_lexical": ("search", "search_code"),
        "code.search_hybrid": ("search", "search_code"),
        "symbol.info": ("symbol_info", None),
        "symbol.info_at": ("symbol_info", None),
        **{
            f"graph.{action}": ("graph", action)
            for action in (
                "overview", "schema", "resolve", "node", "source", "neighbors",
                "callers", "callees",
            )
        },
        **{
            f"metadata.{action}": ("metadata", action)
            for action in ("info", "tree", "object", "form")
        },
        **{
            f"diagnostics.{action}": ("diagnostics", action)
            for action in ("catalog", "schema", "file", "workspace")
        },
    }
    for operation, (expected_tool, expected_action) in cases.items():
        request = {
            "operation": operation,
            "query": "Q",
            "symbol": "S",
            "path": "CommonModules/M/Ext/Module.bsl",
            "line": 1,
            "id": "node",
            "ids": ["node"],
            "object_type": "Catalog",
            "object_name": "Items",
            "max_results": 3,
        }
        tool, arguments = indexes._bsl_workspace_request(request)
        assert tool == expected_tool
        assert arguments.get("action") == expected_action
        assert not {
            "backend", "adapter", "native_tool", "native_action", "connection"
        } & set(arguments)
        if operation.startswith("metadata."):
            assert arguments["mode"] == "source"


def test_workspace_code_modality_is_enforced_without_silent_fallback(monkeypatch, tmp_path: Path):
    response = {
        "structuredContent": {
            "schema_version": "1",
            "modality": "L",
            "items": [{"path": "CommonModules/M/Ext/Module.bsl", "line": 0}],
        },
        "isError": False,
    }
    monkeypatch.setattr(indexes, "_bsl_mcp", lambda *_args, **_kwargs: [response])
    lexical = indexes._bsl_workspace_query(
        "bsl-analyzer", tmp_path,
        {"operation": "code.search_lexical", "query": "Q", "max_results": 2},
        10,
    )
    assert lexical["modalities"] == ["l"]
    assert lexical["items"][0]["component_relative_path"].endswith("Module.bsl")
    with pytest.raises(RuntimeError, match="semantic_modality_unavailable"):
        indexes._bsl_workspace_query(
            "bsl-analyzer", tmp_path,
            {"operation": "code.search_hybrid", "query": "Q", "max_results": 2},
            10,
        )
    response["structuredContent"]["modality"] = "H"
    assert indexes._bsl_workspace_query(
        "bsl-analyzer", tmp_path,
        {"operation": "code.search_hybrid", "query": "Q", "max_results": 2},
        10,
    )["modalities"] == ["h"]


@pytest.mark.skipif(
    not os.environ.get("BSL_ANALYZER_CONFORMANCE_EXECUTABLE"),
    reason="selected BSL Analyzer executable is not explicitly provisioned",
)
def test_selected_bsl_analyzer_matches_offline_surface_fixture():
    executable = os.environ["BSL_ANALYZER_CONFORMANCE_EXECUTABLE"]
    raw = subprocess.run(
        [executable, "contract"],
        check=True,
        capture_output=True,
        text=True,
    )
    contract = json.loads(raw.stdout)
    assert indexes._bsl_contract(executable, str(contract["build_version"]))


def test_adapter_output_is_stopped_at_the_byte_limit(monkeypatch):
    monkeypatch.setattr(indexes, "ADAPTER_OUTPUT_LIMIT", 1024)
    with pytest.raises(RuntimeError, match="output limit"):
        indexes._bounded_run([
            sys.executable,
            "-c",
            "import os; os.write(1, b'x' * 4096)",
        ])


def test_backend_process_environment_is_private_and_closed(monkeypatch):
    captured = {}

    class Process:
        pid = 1
        stdout = type("Output", (), {"fileno": lambda self: 1})()
        returncode = 0

        def poll(self): return 0
        def wait(self, timeout=None): return 0

    class Selector:
        def register(self, *_args): return None
        def select(self, _timeout): return []
        def close(self): return None

    def popen(_command, **kwargs):
        captured.update(kwargs["env"])
        return Process()

    monkeypatch.setenv("DATABASE_URL", "postgres://private")
    monkeypatch.setenv("OPENAI_API_KEY", "private")
    monkeypatch.setattr(indexes.subprocess, "Popen", popen)
    monkeypatch.setattr(indexes.selectors, "DefaultSelector", Selector)
    indexes._bounded_run(["fixed-adapter"])
    assert "DATABASE_URL" not in captured and "OPENAI_API_KEY" not in captured
    assert "BSL_MCP_BROKER" not in captured
    assert captured["HOME"] != str(Path.home())
    assert all(
        captured[name].startswith(captured["HOME"])
        for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
    )


def test_adapter_reuses_explicit_rlm_index_directory_across_processes(
    tmp_path: Path,
) -> None:
    index_dir = tmp_path / "index"
    write = indexes._bounded_run(
        [
            sys.executable,
            "-c",
            "import os,pathlib; pathlib.Path(os.environ['RLM_INDEX_DIR'],'marker').write_text('ready')",
        ],
        index_dir=index_dir,
    )
    read = indexes._bounded_run(
        [
            sys.executable,
            "-c",
            "import os,pathlib; print(pathlib.Path(os.environ['RLM_INDEX_DIR'],'marker').read_text())",
        ],
        index_dir=index_dir,
    )
    assert write.returncode == 0
    assert read.stdout.strip() == "ready"


def test_adapter_process_is_cancelled_before_unbounded_work():
    with pytest.raises(InterruptedError, match="cancelled"):
        indexes._bounded_run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cancelled=lambda: True,
        )


def test_bsl_mcp_requires_structured_ready_state(monkeypatch, tmp_path: Path):
    @contextmanager
    def proxy(*_args, **_kwargs):
        yield {
            "command": ["bsl-analyzer", "--mode", "broker-required"],
            "environment": {},
        }
    monkeypatch.setattr(search_runtime, "supervised_workspace_proxy", proxy)
    class Input:
        def write(self, _value): return None
        def flush(self): return None
        def close(self): return None

    replies = iter([
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "structuredContent": {
                    "schema_version": "bsl-analyzer-status/v1",
                    "state": "loading",
                    "message": "not ready",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "result": {
                "structuredContent": {
                    "schema_version": "bsl-analyzer-status/v1",
                    "state": "ready",
                    "stale": False,
                },
            },
        },
    ])

    class Output:
        def readline(self): return json.dumps(next(replies)) + "\n"

    class Process:
        pid = 1
        stdin = Input()
        stdout = Output()
        def wait(self, timeout=None): return 0

    class Selector:
        def register(self, *_args): return None
        def select(self, _timeout): return [object()]
        def close(self): return None

    monkeypatch.setattr(indexes.subprocess, "Popen", lambda _command, **_kwargs: Process())
    monkeypatch.setattr(indexes.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(indexes.time, "sleep", lambda _seconds: None)
    result = indexes._bsl_mcp(
        "bsl-analyzer",
        tmp_path,
        [("graph", {"action": "status"}, True)],
    )
    assert result[0]["structuredContent"]["state"] == "ready"


def test_bsl_mcp_rejects_incompatible_structured_output(monkeypatch, tmp_path: Path):
    @contextmanager
    def proxy(*_args, **_kwargs):
        yield {
            "command": ["bsl-analyzer", "--mode", "broker-required"],
            "environment": {},
        }
    monkeypatch.setattr(search_runtime, "supervised_workspace_proxy", proxy)
    class Input:
        def write(self, _value): return None
        def flush(self): return None
        def close(self): return None

    replies = iter([
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "structuredContent": {"schema_version": "2", "state": "ready"}
            },
        },
    ])

    class Output:
        def readline(self): return json.dumps(next(replies)) + "\n"

    class Process:
        pid = 1
        stdin = Input()
        stdout = Output()
        def wait(self, timeout=None): return 0

    class Selector:
        def register(self, *_args): return None
        def select(self, _timeout): return [object()]
        def close(self): return None

    monkeypatch.setattr(
        indexes.subprocess, "Popen", lambda *_args, **_kwargs: Process(),
    )
    monkeypatch.setattr(indexes.selectors, "DefaultSelector", Selector)
    with pytest.raises(RuntimeError, match="structured schema"):
        indexes._bsl_mcp(
            "bsl-analyzer",
            tmp_path,
            [("graph", {"action": "status"}, True)],
        )


def test_private_index_promotion_is_clone_isolated_and_keeps_source_clean(monkeypatch, tmp_path: Path):
    repo, component = _minimal_index_repo(tmp_path / "a", "same-project")
    clone, clone_component = _minimal_index_repo(tmp_path / "b", "same-project")
    monkeypatch.setattr(
        indexes,
        "probe_backend",
        lambda *_args, **_kwargs: {
            "available": True,
            "executable": "/fixed/rlm-bsl-index",
            "capabilities": list(indexes.CAPABILITIES),
            "contract_version": "provider-query/v1",
        },
    )

    def build(command, **_kwargs):
        index_dir = _kwargs["index_dir"]
        index_dir.mkdir(exist_ok=True)
        (index_dir / "bsl_index.db").write_bytes(b"index")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(indexes, "_bounded_run", build)
    monkeypatch.setattr(indexes, "cli_probe", lambda *_args: {"ready": True})
    backend = {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"}
    state = tmp_path / "state"
    first = indexes.build_backend_index(repo, component, backend, state_root=state)
    second = indexes.build_backend_index(clone, clone_component, backend, state_root=state)
    assert first["index_fingerprint"] != second["index_fingerprint"]
    assert first["target_fingerprint"] != second["target_fingerprint"]
    assert not (repo / "sources/generations/gen/target_cf/configuration/.rlm-index").exists()
    ready = indexes.ready_backend_state(repo, component, backend, state_root=state)
    assert ready
    assert Path(ready["index_dir"], "bsl_index.db").read_bytes() == b"index"


def test_rebuild_promotes_a_new_instance_and_atomically_switches_pointer(monkeypatch, tmp_path: Path):
    repo, component = _minimal_index_repo(tmp_path / "repo")
    probe = {
        "available": True,
        "executable": "/fixed/rlm-bsl-index",
        "capabilities": list(indexes.CAPABILITIES),
        "contract_version": "provider-query/v1",
    }
    monkeypatch.setattr(indexes, "probe_backend", lambda *_args, **_kwargs: probe)
    generation = iter((b"first", b"second"))

    def build(command, **_kwargs):
        index_dir = _kwargs["index_dir"]
        index_dir.mkdir(exist_ok=True)
        (index_dir / "bsl_index.db").write_bytes(next(generation))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(indexes, "_bounded_run", build)
    monkeypatch.setattr(indexes, "cli_probe", lambda *_args: {"ready": True})
    backend = {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"}
    state_root = tmp_path / "state"
    first = indexes.build_backend_index(
        repo, component, backend, state_root=state_root,
    )
    second = indexes.build_backend_index(
        repo, component, backend, state_root=state_root,
    )
    assert first["index_fingerprint"] != second["index_fingerprint"]
    target = (
        indexes._operational_root(repo, state_root)
        / "targets"
        / first["target_fingerprint"].split(":", 1)[1]
    )
    assert len(list((target / "instances").iterdir())) == 2
    pointer = json.loads((target / "current.json").read_text(encoding="utf-8"))
    assert pointer["index_fingerprint"] == second["index_fingerprint"]
    assert indexes.ready_backend_state(
        repo, component, backend, state_root=state_root,
    )["index_fingerprint"] == second["index_fingerprint"]


def test_index_build_rejects_adapter_mutation_of_private_source(monkeypatch, tmp_path: Path):
    repo, component = _minimal_index_repo(tmp_path / "repo")
    monkeypatch.setattr(
        indexes,
        "probe_backend",
        lambda *_args, **_kwargs: {
            "available": True,
            "executable": "/fixed/rlm-bsl-index",
            "capabilities": list(indexes.CAPABILITIES),
            "contract_version": "provider-query/v1",
        },
    )

    def build(command, **_kwargs):
        mirror = Path(command[-1])
        (mirror / "CommonModules/Test/Ext/Module.bsl").write_text("Изменено", encoding="utf-8")
        index_dir = _kwargs["index_dir"]
        index_dir.mkdir(exist_ok=True)
        (index_dir / "bsl_index.db").write_bytes(b"index")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(indexes, "_bounded_run", build)
    monkeypatch.setattr(indexes, "cli_probe", lambda *_args: {"ready": True})
    with pytest.raises(RuntimeError, match="changed the private source mirror"):
        indexes.build_backend_index(
            repo,
            component,
            {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"},
            state_root=tmp_path / "state",
        )


def test_cancelled_build_quarantines_output_without_promotion(
    monkeypatch, tmp_path: Path,
):
    repo, component = _minimal_index_repo(tmp_path / "repo")
    monkeypatch.setattr(
        indexes,
        "probe_backend",
        lambda *_args, **_kwargs: {
            "available": True,
            "executable": "/fixed/rlm-bsl-index",
            "capabilities": list(indexes.CAPABILITIES),
            "contract_version": "provider-query/v1",
        },
    )

    def build(command, **_kwargs):
        index_dir = _kwargs["index_dir"]
        index_dir.mkdir(exist_ok=True)
        (index_dir / "bsl_index.db").write_bytes(b"late")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(indexes, "_bounded_run", build)
    monkeypatch.setattr(indexes, "cli_probe", lambda *_args: {"ready": True})
    checks = iter((False, True))
    state_root = tmp_path / "state"
    with pytest.raises(InterruptedError, match="cancelled"):
        indexes.build_backend_index(
            repo,
            component,
            {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"},
            state_root=state_root,
            cancelled=lambda: next(checks),
        )
    operational = indexes._operational_root(repo, state_root)
    targets = list((operational / "targets").iterdir())
    assert len(targets) == 1
    assert not (targets[0] / "current.json").exists()
    assert len(list((targets[0] / "quarantine").iterdir())) == 1
    assert not (repo / "sources/generations/gen/target_cf/configuration/.rlm-index").exists()


def test_oversized_state_fails_before_instance_promotion(monkeypatch, tmp_path: Path):
    repo, component = _minimal_index_repo(tmp_path / "repo")
    monkeypatch.setattr(
        indexes,
        "probe_backend",
        lambda *_args, **_kwargs: {
            "available": True,
            "executable": "/fixed/rlm-bsl-index",
            "capabilities": list(indexes.CAPABILITIES),
            "contract_version": "provider-query/v1",
        },
    )

    def build(command, **_kwargs):
        index_dir = _kwargs["index_dir"]
        index_dir.mkdir(exist_ok=True)
        (index_dir / "bsl_index.db").write_bytes(b"index")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(indexes, "_bounded_run", build)
    monkeypatch.setattr(indexes, "cli_probe", lambda *_args: {"ready": True})
    monkeypatch.setattr(indexes, "ADAPTER_STATE_LIMIT", 32)
    state_root = tmp_path / "state"
    with pytest.raises(RuntimeError, match="state exceeds"):
        indexes.build_backend_index(
            repo,
            component,
            {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"},
            state_root=state_root,
        )
    target = next(
        (indexes._operational_root(repo, state_root) / "targets").iterdir()
    )
    assert not (target / "current.json").exists()
    assert not any((target / "instances").iterdir())


def test_oversized_index_payload_is_quarantined_before_promotion(
    monkeypatch, tmp_path: Path,
):
    repo, component = _minimal_index_repo(tmp_path / "repo")
    monkeypatch.setattr(
        indexes,
        "probe_backend",
        lambda *_args, **_kwargs: {
            "available": True,
            "executable": "/fixed/rlm-bsl-index",
            "capabilities": list(indexes.CAPABILITIES),
            "contract_version": "provider-query/v1",
        },
    )

    def build(command, **_kwargs):
        index_dir = _kwargs["index_dir"]
        index_dir.mkdir(exist_ok=True)
        (index_dir / "bsl_index.db").write_bytes(b"too large")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(indexes, "_bounded_run", build)
    monkeypatch.setattr(indexes, "cli_probe", lambda *_args: {"ready": True})
    monkeypatch.setattr(indexes, "ADAPTER_INDEX_LIMIT", 1)
    state_root = tmp_path / "state"
    with pytest.raises(RuntimeError, match="payload exceeds"):
        indexes.build_backend_index(
            repo,
            component,
            {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"},
            state_root=state_root,
        )
    target = next(
        (indexes._operational_path(repo, state_root) / "targets").iterdir()
    )
    assert not (target / "current.json").exists()
    assert len(list((target / "quarantine").iterdir())) == 1


def test_reference_index_is_built_explicitly_and_failed_staging_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from one_c_autoresearch import reference_search

    root = tmp_path / "reference"
    executable = tmp_path / "bsl-analyzer"
    executable.touch()
    probe = {
        "available": True,
        "engine_version": "current",
        "executable_fingerprint": "sha256:" + "a" * 64,
        "surface_manifest": {"surface_fingerprint": "sha256:" + "b" * 64},
    }
    monkeypatch.setattr(
        indexes,
        "_reference_index_target",
        lambda *_args, **_kwargs: (
            executable, "sha256:" + "c" * 64, probe, root,
        ),
    )
    observed: dict[str, Path] = {}

    def execute(_executable, state_dir, *_args, **_kwargs):
        observed["state_dir"] = state_dir
        return {"items": []}

    monkeypatch.setattr(reference_search, "execute_reference", execute)
    ready = indexes.ensure_reference_index(
        tmp_path, {"adapter_id": "bsl-analyzer"}, state_root=tmp_path / "state"
    )
    assert ready["status"] == "ready"
    assert reference_search.current_reference_index(
        root, probe["executable_fingerprint"]
    ).is_dir()
    assert observed["state_dir"].parent.name == "staging"

    monkeypatch.setattr(
        reference_search,
        "execute_reference",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    failed = indexes.ensure_reference_index(
        tmp_path,
        {"adapter_id": "bsl-analyzer"},
        state_root=tmp_path / "state",
        rebuild=True,
    )
    assert failed["status"] == "failed"
    assert not (root / "staging" / probe["executable_fingerprint"]).exists()


def test_simultaneous_rebuild_is_fenced_and_validation_keeps_old_pointer(
    monkeypatch, tmp_path: Path,
):
    repo, component = _minimal_index_repo(tmp_path / "repo")
    probe = {
        "available": True,
        "executable": "/fixed/rlm-bsl-index",
        "capabilities": list(indexes.CAPABILITIES),
        "contract_version": "provider-query/v1",
    }
    monkeypatch.setattr(indexes, "probe_backend", lambda *_args, **_kwargs: probe)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def build(command, **_kwargs):
        nonlocal calls
        index_dir = _kwargs["index_dir"]
        index_dir.mkdir(exist_ok=True)
        calls += 1
        if calls == 2:
            started.set()
            assert release.wait(5)
        (index_dir / "bsl_index.db").write_bytes(
            b"initial" if calls == 1 else b"replacement"
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(indexes, "_bounded_run", build)
    monkeypatch.setattr(indexes, "cli_probe", lambda *_args: {"ready": True})
    backend = {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"}
    state_root = tmp_path / "state"
    first = indexes.build_backend_index(
        repo, component, backend, state_root=state_root,
    )
    completed: list[dict] = []
    thread = threading.Thread(
        target=lambda: completed.append(indexes.build_backend_index(
            repo, component, backend, state_root=state_root,
        ))
    )
    thread.start()
    assert started.wait(5)
    validated = indexes.validate_configured(
        repo,
        component_ids=[component["component_id"]],
        backend_ids=[backend["adapter_id"]],
        state_root=state_root,
    )
    assert validated[0]["index_fingerprint"] == first["index_fingerprint"]
    with pytest.raises(RuntimeError, match="already leased"):
        indexes.build_backend_index(
            repo, component, backend, state_root=state_root,
        )
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert completed[0]["index_fingerprint"] != first["index_fingerprint"]


def test_dead_index_lease_is_recovered_but_live_lease_is_fenced(monkeypatch, tmp_path: Path):
    repo, component = _minimal_index_repo(tmp_path / "repo")
    probe = {
        "available": True,
        "executable": "/fixed/rlm-bsl-index",
        "capabilities": list(indexes.CAPABILITIES),
        "contract_version": "provider-query/v1",
    }
    monkeypatch.setattr(indexes, "probe_backend", lambda *_args, **_kwargs: probe)

    def build(command, **_kwargs):
        index_dir = _kwargs["index_dir"]
        index_dir.mkdir(exist_ok=True)
        (index_dir / "bsl_index.db").write_bytes(b"index")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(indexes, "_bounded_run", build)
    monkeypatch.setattr(indexes, "cli_probe", lambda *_args: {"ready": True})
    backend = {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"}
    identity = indexes.target_identity(repo, component, backend, probe["capabilities"])
    target = (
        indexes._operational_root(repo, tmp_path / "state")
        / "targets"
        / indexes.target_fingerprint(identity).split(":", 1)[1]
    )
    target.mkdir(parents=True)
    (target / "lease.json").write_text(
        json.dumps({
            "token": "dead",
            "process_identity": {"pid": -1, "start_time": "", "boot_id": ""},
        }),
        encoding="utf-8",
    )
    assert indexes.build_backend_index(
        repo, component, backend, state_root=tmp_path / "state",
    )["status"] == "ready"

    from one_c_autoresearch.events import process_identity
    (target / "lease.json").write_text(
        json.dumps({"token": "live", "process_identity": process_identity()}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="already leased"):
        indexes.build_backend_index(
            repo, component, backend, state_root=tmp_path / "state",
        )


def test_explicit_validation_never_builds(monkeypatch, tmp_path: Path):
    repo, component = _minimal_index_repo(tmp_path / "repo")
    monkeypatch.setattr(
        indexes,
        "ready_backend_state",
        lambda *_args, **_kwargs: {
            "index_fingerprint": "sha256:index",
        },
    )
    monkeypatch.setattr(
        indexes,
        "build_backend_index",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("validation started a build")
        ),
    )
    assert indexes.validate_configured(
        repo,
        component_ids=[component["component_id"]],
        backend_ids=["rlm-tools-bsl"],
        state_root=tmp_path / "state",
    )[0]["status"] == "ready"


def test_legacy_rlm_state_is_adopted_by_the_real_query_path(monkeypatch, tmp_path: Path):
    repo, component = _minimal_index_repo(tmp_path / "repo")
    backend = {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"}
    state_root = tmp_path / "legacy-state"
    legacy = state_root / indexes.index_key(repo, component) / "state.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps({
            "schema_version": "1",
            "index_key": indexes.index_key(repo, component),
            "status": "ready",
            "last_validation": "2026-01-01T00:00:00+00:00",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        indexes,
        "probe_backend",
        lambda *_args, **_kwargs: {
            "available": True,
            "executable": "/fixed/rlm-bsl-index",
            "capabilities": list(indexes.CAPABILITIES),
            "capability_fingerprint": "sha256:capabilities",
            "contract_version": "provider-query/v1",
        },
    )
    ready = indexes.ready_backend_state(
        repo, component, backend, state_root=state_root,
    )
    assert ready and ready["legacy_adopted"]
    assert ready["instance_path"].endswith("target_cf/configuration")
