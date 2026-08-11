from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import threading

import pytest

from one_c_autoresearch import indexes, source_search


REPO = Path(__file__).parents[1]
requires_source_generation = pytest.mark.skipif(
    not (REPO / "research/active-source-generation.json").is_file(),
    reason="requires a concrete research repository source generation",
)


def policy(**overrides):
    value = {
        "operations": ["code.search_lexical", "symbol.info"],
        "max_calls": 4,
        "max_concurrent_calls": 2,
        "per_call_deadline_seconds": 10,
        "max_backend_seconds": 30,
        "max_query_bytes": 100,
        "max_total_query_bytes": 200,
        "max_results_per_call": 5,
        "max_total_results": 10,
        "max_returned_bytes_per_call": 4096,
        "max_total_returned_bytes": 8192,
    }
    return {**value, **overrides}


def test_profile_policy_is_closed_bounded_and_has_exact_dynamic_reserve():
    value = source_search.validate_profile_policy(policy())
    assert value["operations"] == ["code.search_lexical", "symbol.info"]
    assert source_search.dynamic_reserve_bytes(value) == 200 + 8192 + 4 * 512
    with pytest.raises(ValueError, match="fields"):
        source_search.validate_profile_policy({**value, "backend": "bsl-analyzer"})
    with pytest.raises(ValueError, match="max_calls"):
        source_search.validate_profile_policy(policy(max_calls=65))
    with pytest.raises(ValueError, match="concurrency"):
        source_search.validate_profile_policy(policy(max_calls=1, max_concurrent_calls=2))


def test_v2_request_surface_is_closed_complete_and_has_no_infrastructure_controls():
    schema = source_search.request_schema()
    variants = {
        item["properties"]["operation"]["const"]: item
        for item in schema["oneOf"]
    }
    assert set(variants) == set(source_search.V2_OPERATIONS)
    assert len(variants) == len(schema["oneOf"])
    assert all(item["additionalProperties"] is False for item in variants.values())
    assert all(
        not {"backend", "adapter", "native_tool", "native_action"} & set(item["properties"])
        for item in variants.values()
    )
    assert "query" in variants["code.search_hybrid"]["required"]
    assert "id" in variants["graph.neighbors"]["required"]
    assert "path" in variants["diagnostics.file"]["required"]
    assert "question" in variants["reference.its_help"]["required"]
    assert "component_id" not in variants["reference.search_docs"]["properties"]


def test_v2_response_surface_is_closed_bounded_and_has_no_cursor():
    schema = source_search.response_schema()
    assert schema["additionalProperties"] is False
    assert "cursor" not in schema["properties"]
    items = schema["properties"]["items"]
    assert items["maxItems"] == source_search.POLICY_LIMITS["max_results_per_call"]
    variants = items["items"]["oneOf"]
    assert {
        item["properties"]["kind"]["const"] for item in variants
    } == {
        "canonical-hit", "canonical-excerpt", "navigation-node",
        "navigation-edge", "derived-finding", "reference-hit", "native-text",
        "retry", "degraded",
    }
    assert all(item["additionalProperties"] is False for item in variants)


def test_schema3_operations_are_exact_and_false_references_fail_with_guidance():
    migrated = source_search.validate_profile_policy(policy(operations=[
        "code.search_lexical", "symbol.info", "graph.callers", "graph.callees", "metadata.tree",
    ]))
    assert migrated["operations"] == [
        "code.search_lexical", "symbol.info", "graph.callers", "graph.callees",
        "metadata.tree",
    ]
    with pytest.raises(ValueError, match=r"find_references_unsupported.*symbol\.info.*graph\.callers"):
        source_search.validate_profile_policy(policy(operations=["find_references"]))
    with pytest.raises(ValueError, match="unsupported"):
        source_search.request_schema("source-search-tool/v1")


@requires_source_generation
def test_policy_freezes_role_scope_and_routes():
    profile = {"source_search": policy()}
    resolved = source_search.resolve_policy(
        REPO,
        profile,
        "analyzer",
        {
            "allowed_paths": [
                "target_cf/configuration/CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl"
            ]
        },
    )
    assert resolved is not None
    assert resolved["schema_version"] == source_search.POLICY_VERSION
    assert resolved["component_ids"] == ["target_cf:configuration"]
    assert resolved["operations"] == ["code.search_lexical", "symbol.info"]
    assert resolved["logical_path_prefixes"] == [
        "configuration/CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl"
    ]
    assert resolved["policy_fingerprint"].startswith("sha256:")
    assert source_search.resolve_policy(REPO, {}, "analyzer", {"allowed_paths": []}) is None


@requires_source_generation
def test_source_search_returns_only_canonical_verified_navigation():
    component = next(item for item in indexes.discover(REPO) if item["component_id"] == "target_cf:configuration")
    backend = {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"}
    identity = indexes.target_identity(REPO, component, backend, ["code-search-lexical"])
    state = {
        **indexes.promoted_identity(identity, [{"path": "index", "sha256": "x", "size_bytes": 1}]),
        "adapter_id": "rlm-tools-bsl",
        "adapter_version": "rlm-index/v1",
        "component_id": component["component_id"],
        "capabilities": ["code-search-lexical"],
        "status": "ready",
    }
    resolved = source_search.resolve_policy(
        REPO,
        {"source_search": policy(operations=["code.search_lexical"])},
        "analyzer",
        {
            "allowed_paths": [
                "target_cf/configuration/CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl"
            ]
        },
    )
    result = source_search.execute_query(
        REPO,
        resolved,
        {
            "operation": "code.search_lexical",
            "query": "Процедура",
            "component_id": component["component_id"],
            "path_prefix": None,
            "max_results": 1,
        },
        [state],
        lambda _decision, _request: [{
            "component_relative_path": "CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl",
            "line": 1,
            "kind": "text",
            "rank": 0.95,
        }],
    )
    assert set(result["items"][0]) == {
        "path", "fingerprint", "source_generation_id", "component_id", "line", "kind"
    }
    assert result["items"][0]["path"].startswith("configuration/")
    assert "rank" not in result["items"][0]
    with pytest.raises(ValueError, match="out_of_scope"):
        source_search.execute_query(
            REPO,
            resolved,
            {
                "operation": "code.search_lexical",
                "query": "Процедура",
                "component_id": component["component_id"],
                "path_prefix": None,
                "max_results": 1,
            },
            [state],
            lambda _decision, _request: [{
                "component_relative_path": "Configuration.xml",
                "kind": "text",
            }],
        )
    with pytest.raises(RuntimeError, match="source_stale"):
        source_search.execute_query(
            REPO,
            {**resolved, "source_generation_id": "stale"},
            {
                "operation": "code.search_lexical",
                "query": "Процедура",
                "component_id": component["component_id"],
                "path_prefix": None,
                "max_results": 1,
            },
            [state],
            lambda *_args: [],
        )
    with pytest.raises(ValueError):
        source_search.execute_query(
            REPO,
            resolved,
            {
                "operation": "code.search_lexical",
                "query": "Процедура",
                "component_id": component["component_id"],
                "path_prefix": None,
                "max_results": 1,
            },
            [state],
            lambda *_args: [{
                "component_relative_path": "../outside.bsl",
                "kind": "text",
            }],
        )


@requires_source_generation
def test_workspace_v2_canonicalizes_hits_and_rereads_excerpts(monkeypatch):
    component = next(
        item for item in indexes.discover(REPO)
        if item["component_id"] == "target_cf:configuration"
    )
    resolved = source_search.resolve_policy(
        REPO,
        {"source_search": policy(operations=["graph.source"])},
        "analyzer",
        {
            "allowed_paths": [
                "target_cf/configuration/CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl"
            ]
        },
    )
    assert resolved
    state = {
        "adapter_id": "bsl-analyzer",
        "adapter_version": "bsl-analyzer-workspace/v2",
        "component_id": component["component_id"],
        "capabilities": ["graph-source"],
        "status": "ready",
        "index_fingerprint": "sha256:index",
        "capability_fingerprint": "sha256:capability",
    }
    monkeypatch.setattr(indexes, "select_backend", lambda *_args: {
        "route_fingerprint": "sha256:route",
        "preferred_backend_id": "bsl-analyzer",
        "selected_backend_id": "bsl-analyzer",
        "fallback_reason": None,
        "state": state,
    })
    result = source_search.execute_query(
        REPO,
        resolved,
        {
            "operation": "graph.source",
            "ids": ["node"],
            "component_id": component["component_id"],
            "max_results": 2,
        },
        [state],
        lambda _decision, request: {
            "items": [{
                "kind": "canonical-hit",
                "component_relative_path":
                    "CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl",
                "line": 1,
                "text": "MUST NOT SURVIVE",
            }],
            "truncated": False,
        },
    )
    assert result["schema_version"] == "source-search-result/v2"
    assert [item["kind"] for item in result["items"]] == [
        "canonical-hit", "canonical-excerpt",
    ]
    assert "MUST NOT SURVIVE" not in json.dumps(result, ensure_ascii=False)
    excerpt = result["items"][1]
    canonical = (
        REPO / "sources/generations"
        / component["source_generation_id"] / component["path"]
        / "CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl"
    ).read_text(encoding="utf-8")
    assert excerpt["text"] in canonical
    assert excerpt["fingerprint"] == result["items"][0]["fingerprint"]


@requires_source_generation
def test_workspace_v2_rejects_scope_escape_and_discards_native_source_text(monkeypatch):
    component = next(
        item for item in indexes.discover(REPO)
        if item["component_id"] == "target_cf:configuration"
    )
    resolved = source_search.resolve_policy(
        REPO,
        {"source_search": policy(operations=["metadata.form"])},
        "analyzer",
        {
            "allowed_paths": [
                "target_cf/configuration/CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl"
            ]
        },
    )
    assert resolved
    state = {
        "adapter_id": "bsl-analyzer",
        "adapter_version": "bsl-analyzer-workspace/v2",
        "component_id": component["component_id"],
        "capabilities": ["metadata-form"],
        "status": "ready",
        "index_fingerprint": "sha256:index",
    }
    monkeypatch.setattr(indexes, "select_backend", lambda *_args: {
        "route_fingerprint": "sha256:route",
        "preferred_backend_id": "bsl-analyzer",
        "selected_backend_id": "bsl-analyzer",
        "fallback_reason": None,
        "state": state,
    })
    result = source_search.execute_query(
        REPO,
        resolved,
        {
            "operation": "metadata.form",
            "component_id": component["component_id"],
            "object_type": "Catalog",
        },
        [state],
        lambda *_args: {
            "items": [{
                "kind": "native-text",
                "schema_version": "native-text-envelope/v1",
                "text": "native source body",
            }],
            "truncated": False,
        },
    )
    assert result["items"] == [{
        "kind": "degraded",
        "reason": "source_search.native_source_text_discarded",
        "narrowing_hint": "use a structured BSL Analyzer response",
    }]
    with pytest.raises(ValueError, match="path_scope_forbidden"):
        source_search.execute_query(
            REPO,
            {**resolved, "operations": ["diagnostics.file"]},
            {
                "operation": "diagnostics.file",
                "component_id": component["component_id"],
                "path": "configuration/Configuration.xml",
                "max_results": 1,
            },
            [state],
            lambda *_args: {"items": [], "truncated": False},
        )


@requires_source_generation
def test_every_workspace_v2_operation_reaches_the_fixed_backend(monkeypatch):
    component = next(
        item for item in indexes.discover(REPO)
        if item["component_id"] == "target_cf:configuration"
    )
    operations = [
        operation for operation in source_search.V2_OPERATIONS
        if not operation.startswith("reference.")
    ]
    resolved = source_search.resolve_policy(
        REPO,
        {"source_search": policy(operations=operations)},
        "analyzer",
        {
            "allowed_paths": [
                "target_cf/configuration/CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl"
            ]
        },
    )
    assert resolved
    state = {
        "adapter_id": "bsl-analyzer",
        "adapter_version": "bsl-analyzer-workspace/v2",
        "component_id": component["component_id"],
        "status": "ready",
        "index_fingerprint": "sha256:index",
    }
    monkeypatch.setattr(indexes, "select_backend", lambda *_args: {
        "route_fingerprint": "sha256:route",
        "preferred_backend_id": "bsl-analyzer",
        "selected_backend_id": "bsl-analyzer",
        "fallback_reason": None,
        "state": state,
    })
    common_path = "configuration/CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl"
    requests = {
        "code.search_lexical": {"query": "Q", "max_results": 1},
        "code.search_hybrid": {"query": "Q", "max_results": 1},
        "symbol.info": {"symbol": "S"},
        "symbol.info_at": {
            "component_id": component["component_id"], "path": common_path, "line": 1,
        },
        "graph.overview": {},
        "graph.schema": {},
        "graph.resolve": {"query": "Q", "max_results": 1},
        **{
            f"graph.{name}": {"id": "node", "max_results": 1}
            for name in ("node", "neighbors", "callers", "callees")
        },
        "graph.source": {"ids": ["node"], "max_results": 1},
        "metadata.info": {"component_id": component["component_id"]},
        "metadata.tree": {"component_id": component["component_id"], "max_results": 1},
        "metadata.object": {
            "component_id": component["component_id"],
            "object_type": "Catalog", "object_name": "Items",
        },
        "metadata.form": {
            "component_id": component["component_id"], "object_type": "Catalog",
        },
        "diagnostics.catalog": {"max_results": 1},
        "diagnostics.schema": {"max_results": 1},
        "diagnostics.workspace": {"max_results": 1},
        "diagnostics.file": {
            "component_id": component["component_id"], "path": common_path,
            "max_results": 1,
        },
    }
    seen = []
    for operation in operations:
        arguments = requests[operation]
        result = source_search.execute_query(
            REPO, resolved, {"operation": operation, **arguments}, [state],
            lambda _decision, request: (
                seen.append(request["operation"])
                or {"items": [], "truncated": False}
            ),
        )
        assert result["schema_version"] == "source-search-result/v2"
    assert seen == operations


@requires_source_generation
def test_empty_success_never_falls_back_and_conflicting_hits_fail_closed(monkeypatch):
    component = next(
        item for item in indexes.discover(REPO)
        if item["component_id"] == "target_cf:configuration"
    )
    resolved = source_search.resolve_policy(
        REPO,
        {"source_search": policy(operations=["code.search_lexical"])},
        "analyzer",
        {
            "allowed_paths": [
                "target_cf/configuration/CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl"
            ]
        },
    )
    assert resolved
    monkeypatch.setattr(
        indexes,
        "load_config",
        lambda _repo: {
            "backends": [
                {"adapter_id": "bsl-analyzer", "engine_version": "0.2.63"},
                {"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"},
            ],
            "routes": {
                "code-search-lexical": ["bsl-analyzer", "rlm-tools-bsl"],
            },
        },
    )
    states = []
    for adapter_id, adapter_version in (
        ("bsl-analyzer", "bsl-analyzer-workspace/v1"),
        ("rlm-tools-bsl", "rlm-index/v1"),
    ):
        backend = {
            "adapter_id": adapter_id,
            "engine_version": "0.2.63" if adapter_id == "bsl-analyzer" else "1.30.0",
        }
        identity = indexes.target_identity(REPO, component, backend, ["code-search-lexical"])
        states.append({
            **indexes.promoted_identity(
                identity,
                [{"path": "index", "sha256": adapter_id, "size_bytes": 1}],
            ),
            "adapter_id": adapter_id,
            "adapter_version": adapter_version,
            "component_id": component["component_id"],
            "capabilities": ["code-search-lexical"],
            "status": "ready",
        })
    calls = []
    request = {
        "operation": "code.search_lexical",
        "query": "ничего",
        "component_id": component["component_id"],
        "path_prefix": None,
        "max_results": 2,
    }
    empty = source_search.execute_query(
        REPO,
        resolved,
        request,
        states,
        lambda decision, _request: calls.append(decision["selected_backend_id"]) or [],
    )
    assert calls == ["bsl-analyzer"]
    assert empty["result_count"] == 0
    assert empty["route"]["selected_backend_id"] == "bsl-analyzer"
    with pytest.raises(ValueError, match="conflicting_hits"):
        source_search.execute_query(
            REPO,
            resolved,
            request,
            states,
            lambda _decision, _request: [
                {
                    "component_relative_path": "CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl",
                    "line": 1,
                    "kind": "text",
                    "symbol": "Первый",
                },
                {
                    "component_relative_path": "CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl",
                    "line": 1,
                    "kind": "text",
                    "symbol": "Второй",
                },
            ],
        )


@requires_source_generation
def test_extension_hit_becomes_an_ordinary_extension_evidence_path():
    component = next(
        item for item in indexes.discover(REPO)
        if ":extension:" in item["component_id"]
    )
    evidence = indexes.canonical_evidence(
        REPO,
        component["component_id"],
        "Languages/Русский.xml",
    )
    assert evidence["path"].startswith("extensions/")
    assert evidence["path"].endswith("/Languages/Русский.xml")
    assert evidence["fingerprint"].startswith("sha256:")


def test_external_artifact_hit_becomes_an_ordinary_external_evidence_path(
    tmp_path: Path,
):
    (tmp_path / "research").mkdir()
    (tmp_path / "research/indexing.toml").write_bytes(indexes.serialize_config({
        "schema_version": "3",
        "machine_contract_version": "1.3",
        "backends": [{"adapter_id": "rlm-tools-bsl", "engine_version": "1.30.0"}],
        "routes": {
            capability: ["rlm-tools-bsl"]
            for capability in indexes.COMPLETE_SEARCH_CAPABILITIES
        },
        "service_profiles": {
            "lexical": "lexical-default",
            "hybrid": "embedding-default",
        },
    }))
    source = (
        tmp_path
        / "sources/generations/gen/target_cf/external/processor/source"
    )
    source.mkdir(parents=True)
    (source / "Module.bsl").write_text("Процедура Тест()\n", encoding="utf-8")
    fingerprint = "sha256:" + indexes.sha256(
        indexes.canonical_json(indexes.file_manifest(source))
    )
    (tmp_path / "research/active-source-generation.json").write_text(
        json.dumps({
            "schema_version": "2",
            "generation_id": "gen",
            "components": [{
                "component_id": "target_cf:external:processor",
                "path": "target_cf/external/processor",
                "kind": "epf",
                "representation_schema": "xml-hierarchical/v1",
                "fingerprint": fingerprint,
                "bsl_file_count": 1,
            }],
        }),
        encoding="utf-8",
    )
    resolved = source_search.resolve_policy(
        tmp_path,
        {"source_search": policy(operations=["code.search_lexical"])},
        "analyzer",
        {"allowed_paths": ["target_cf/external/processor/source/Module.bsl"]},
    )
    assert resolved and resolved["component_ids"] == [
        "target_cf:external:processor"
    ]
    evidence = indexes.canonical_evidence(
        tmp_path, "target_cf:external:processor", "Module.bsl",
    )
    assert evidence["path"] == "external/processor/source/Module.bsl"


def test_query_hmac_is_project_keyed_and_content_free():
    query = {"operation": "code.search_lexical", "query": "СекретныйТекст"}
    first = source_search.query_hmac(b"a" * 32, "v1", query)
    second = source_search.query_hmac(b"b" * 32, "v1", query)
    assert first != second and "СекретныйТекст" not in first


def test_hmac_rotation_keeps_old_key_and_changes_query_identity(tmp_path: Path):
    from one_c_autoresearch import source_search_bridge

    first_version, first_key = source_search_bridge._key(tmp_path)
    assert first_version == "v1"
    assert source_search_bridge.rotate_hmac_key(tmp_path) == "v2"
    second_version, second_key = source_search_bridge._key(tmp_path)
    assert second_version == "v2"
    assert first_key != second_key
    assert (tmp_path / "source-search-hmac-v1.key").read_bytes() == first_key
    query = {"operation": "code.search_lexical", "query": "Закрытый запрос"}
    assert source_search.query_hmac(first_key, first_version, query) != (
        source_search.query_hmac(second_key, second_version, query)
    )


@requires_source_generation
def test_final_evidence_revalidation_and_reuse_ignore_volatile_ledger_fields(
    monkeypatch,
):
    resolved = source_search.resolve_policy(
        REPO,
        {"source_search": policy(operations=["code.search_lexical"])},
        "analyzer",
        {
            "allowed_paths": [
                "target_cf/configuration/CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl"
            ]
        },
    )
    assert resolved
    evidence = indexes.canonical_evidence(
        REPO,
        "target_cf:configuration",
        "CommonModules/ЗагрузкаМетаданныхEDT/Ext/Module.bsl",
    )
    manifest = source_search.revalidate_proposal_evidence(
        REPO, resolved, {"evidence": [evidence]},
    )
    with pytest.raises(ValueError, match="stale_or_unbound"):
        source_search.revalidate_proposal_evidence(
            REPO,
            resolved,
            {"evidence": [{**evidence, "fingerprint": "sha256:altered"}]},
        )
    stable = {
        "capability": "code-search-lexical",
        "route_fingerprint": resolved["routing_fingerprint"],
        "adapter_id": "rlm-tools-bsl",
        "adapter_version": "rlm-index/v1",
        "capability_fingerprint": "sha256:capability",
        "index_fingerprint": "sha256:index",
        "result_manifest_fingerprint": "sha256:result",
        "status": "completed",
    }
    ledger = {
        "ledger_complete": True,
        "reconciled": True,
        "policy_fingerprint": resolved["policy_fingerprint"],
        "scope_fingerprint": resolved["scope_fingerprint"],
        "items": [{**stable, "ordinal": 1, "backend_seconds": 1, "query_hmac": "v1:a"}],
    }
    first = source_search.reuse_binding(resolved, ledger, manifest, "v1")
    second = source_search.reuse_binding(
        resolved,
        {
            **ledger,
            "items": [{**stable, "ordinal": 99, "backend_seconds": 9, "query_hmac": "v1:b"}],
        },
        manifest,
        "v1",
    )
    assert first == second
    with pytest.raises(RuntimeError, match="hmac_key_rotated"):
        source_search.reuse_binding(resolved, ledger, manifest, "v2")
    changed = source_search.reuse_binding(
        resolved,
        {
            **ledger,
            "items": [{
                **stable,
                "index_fingerprint": "sha256:other",
                "query_hmac": "v1:a",
            }],
        },
        manifest,
        "v1",
    )
    assert changed["binding_fingerprint"] != first["binding_fingerprint"]
    monkeypatch.setattr(
        indexes,
        "load_config",
        lambda _repo: {"routes": {"code-search-lexical": ["bsl-analyzer"]}},
    )
    with pytest.raises(RuntimeError, match="route_stale"):
        source_search.revalidate_reuse_environment(REPO, resolved, ledger)


def test_context_budget_reserves_worst_case_dynamic_transcript(monkeypatch):
    from one_c_autoresearch import agents

    profile = {
        "provider": "codex-cli",
        "model": "test",
        "reasoning_effort": "low",
        "instructions_version": "1",
        "environment_preset": "local-read-only",
        "input_context_tokens": 20000,
        "context_estimator_version": "utf8-v1",
        "capability_fingerprint": "sha256:model",
        "source_search": policy(),
    }
    work = {
        "id": "DIF-test",
        "kind": "dif",
        "allowed_paths": [
            "target_cf/configuration/CommonModules/ЗагрузкаМетаданныхEDТ/Ext/Module.bsl"
        ],
    }
    # The context manifest is unrelated to policy resolution in this focused capacity check.
    manifest = {
        "schema_version": "1",
        "paths": [],
        "source_generation_id": "",
    }
    monkeypatch.setattr(agents, "verify_context_manifest", lambda *_args: None)
    snapshot = {
        "subject_bindings": {},
        "source_search_policies": {
            "analyze-dif:analyzer": {
                **policy(),
                "schema_version": source_search.POLICY_VERSION,
            }
        },
    }
    prepared = agents.prepare_context_envelope(
        REPO, profile, "dif.classify-next", "analyze-dif", "analyzer",
        work, "", snapshot, manifest,
    )
    reserve = source_search.dynamic_reserve_bytes(policy())
    assert prepared["diagnostics"]["dynamic_search_reserve_bytes"] == reserve
    assert prepared["diagnostics"]["headroom_bytes"] == (
        prepared["diagnostics"]["input_allowance_bytes"]
        - prepared["diagnostics"]["prepared_input_bytes"]
        - reserve
    )
    with pytest.raises(ValueError, match="agent.context_capacity"):
        agents.prepare_context_envelope(
            REPO,
            {**profile, "input_context_tokens": 5000},
            "dif.classify-next",
            "analyze-dif",
            "analyzer",
            work,
            "",
            snapshot,
            manifest,
        )


def test_sqlite_reservation_is_atomic_replay_safe_and_exactly_once(tmp_path: Path):
    from one_c_autoresearch.sqlite_state import DispatcherStore

    resolved = {
        **policy(),
        "schema_version": source_search.POLICY_VERSION,
        "policy_fingerprint": "sha256:policy",
        "scope_fingerprint": "sha256:scope",
        "operations": ["code.search_lexical"],
    }
    with DispatcherStore(REPO, tmp_path) as store:
        store.configure_source_search("inv-1", resolved, "sha256:capability")
        reserved = store.reserve_source_search_call(
            "inv-1", "call-1", "sha256:capability",
            query_hmac="v1:query",
            capability="code-search-lexical",
            query_bytes=10,
            requested_results=2,
            requested_returned_bytes=100,
            requested_backend_seconds=5,
        )
        assert reserved["ordinal"] == 1
        with pytest.raises(RuntimeError, match="replay"):
            store.reserve_source_search_call(
                "inv-1", "call-1", "sha256:capability",
                query_hmac="v1:query", capability="code-search-lexical", query_bytes=10,
                requested_results=2, requested_returned_bytes=100,
                requested_backend_seconds=5,
            )
        assert store.settle_source_search_call(
            "inv-1", "call-1", status="completed",
            route_fingerprint="sha256:route", fallback_reason="index_not_ready",
            adapter_id="rlm-tools-bsl", index_fingerprint="sha256:index",
            result_manifest_fingerprint="sha256:results", result_count=1,
            returned_bytes=50, backend_seconds=1,
        )
        assert not store.settle_source_search_call("inv-1", "call-1", status="completed")
        ledger = store.source_search_ledger("inv-1")
        item = ledger["items"][0]
        assert item["query_hmac"] == "v1:query" and item["status"] == "completed"
        assert "query" not in item
        assert ledger["status_counts"] == {"completed": 1}
        assert ledger["route_summaries"] == [{
            "capability": "code-search-lexical",
            "selected_backend_id": "rlm-tools-bsl",
            "fallback_reason": "index_not_ready",
            "calls": 1,
        }]
        assert ledger["capacity_reserve_bytes"] == source_search.dynamic_reserve_bytes(resolved)


def test_sqlite_close_fences_admission_and_terminalizes_inflight(tmp_path: Path):
    from one_c_autoresearch.sqlite_state import DispatcherStore

    resolved = {
        **policy(),
        "schema_version": source_search.POLICY_VERSION,
        "policy_fingerprint": "sha256:policy",
        "scope_fingerprint": "sha256:scope",
        "operations": ["code.search_lexical"],
    }
    with DispatcherStore(REPO, tmp_path) as store:
        store.configure_source_search("inv-2", resolved, "sha256:capability")
        store.reserve_source_search_call(
            "inv-2", "call-1", "sha256:capability",
            query_hmac="v1:query", capability="code-search-lexical", query_bytes=10,
            requested_results=2, requested_returned_bytes=100,
            requested_backend_seconds=5,
        )
        assert store.close_source_search("inv-2") == 1
        assert not store.settle_source_search_call(
            "inv-2", "call-1", status="completed",
            result_manifest_fingerprint="sha256:late",
        )
        with pytest.raises(RuntimeError, match="terminal"):
            store.reserve_source_search_call(
                "inv-2", "call-2", "sha256:capability",
                query_hmac="v1:query2", capability="code-search-lexical", query_bytes=10,
                requested_results=2, requested_returned_bytes=100,
                requested_backend_seconds=5,
            )
        assert store.source_search_ledger("inv-2")["items"][0]["status"] == "interrupted"


def test_dispatcher_cancellation_atomically_closes_source_search(tmp_path: Path):
    from one_c_autoresearch.sqlite_state import DispatcherStore

    resolved = {
        **policy(),
        "schema_version": source_search.POLICY_VERSION,
        "policy_fingerprint": "sha256:policy",
        "scope_fingerprint": "sha256:scope",
        "operations": ["code.search_lexical"],
    }
    with DispatcherStore(REPO, tmp_path) as store:
        token = store.acquire_lease(
            "analyze-dif", "thread", "work", "test", None,
            run_id="run",
        )
        assert token
        invocation = store.start_invocation(
            "analyze-dif", "run", "analyze-dif", "analyzer", "work", 1, token,
        )
        assert invocation
        invocation_id = invocation["invocation_id"]
        store.configure_source_search(
            invocation_id, resolved, "sha256:capability",
        )
        store.reserve_source_search_call(
            invocation_id, "call-1", "sha256:capability",
            query_hmac="v1:query", capability="code-search-lexical", query_bytes=10,
            requested_results=1, requested_returned_bytes=10,
            requested_backend_seconds=1,
        )
        assert store.cancel_running_work("analyze-dif", "run", token) == 1
        assert not store.source_search_admission_open(invocation_id)
        ledger = store.source_search_ledger(invocation_id)
        assert ledger["admission_state"] == "closed"
        assert ledger["items"][0]["status"] == "cancelled"
        assert ledger["reconciled"]


def test_mcp_bridge_exposes_only_source_search(monkeypatch, tmp_path: Path):
    import io
    import json
    from one_c_autoresearch import source_search_bridge
    from one_c_autoresearch.sqlite_state import DispatcherStore

    resolved = {
        **policy(),
        "schema_version": source_search.POLICY_VERSION,
        "policy_fingerprint": "sha256:policy",
        "scope_fingerprint": "sha256:scope",
        "operations": ["code.search_lexical"],
    }
    capability = "private-capability"
    from one_c_autoresearch.contracts import sha256
    with DispatcherStore(REPO, tmp_path) as store:
        store.configure_source_search(
            "inv-bridge", resolved, "sha256:" + sha256(capability.encode())
        )
    input_stream = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        + "\n"
    )
    output_stream = io.StringIO()
    monkeypatch.setattr(source_search_bridge.sys, "stdin", input_stream)
    monkeypatch.setattr(source_search_bridge.sys, "stdout", output_stream)
    source_search_bridge.serve(REPO, tmp_path, "inv-bridge", capability)
    responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
    assert responses[0]["result"]["serverInfo"]["version"] == source_search.BRIDGE_VERSION
    assert [tool["name"] for tool in responses[1]["result"]["tools"]] == ["source_search"]


def test_mcp_tool_failure_is_typed_bounded_and_protocol_success():
    from one_c_autoresearch import source_search_bridge
    from one_c_autoresearch.sqlite_state import SourceSearchBudgetError

    failure = source_search_bridge._tool_failure(
        SourceSearchBudgetError("max_calls", 4, 4, 1)
    )
    assert failure["isError"] is True
    assert failure["structuredContent"] == {
        "schema_version": "source-search-failure/v1",
        "type": "SourceSearchBudgetError",
        "code": "source_search.budget_exhausted",
        "limit": "max_calls",
        "limit_value": 4,
        "consumed": 4,
        "requested": 1,
    }
    assert "query" not in failure["content"][0]["text"]
    assert source_search_bridge._tool_failure(
        TimeoutError("backend details")
    )["structuredContent"]["code"] == "source_search.timeout"


def test_mcp_bridge_discards_result_closed_before_settlement(
    monkeypatch, tmp_path: Path,
):
    import io
    from one_c_autoresearch import source_search_bridge
    from one_c_autoresearch.contracts import sha256
    from one_c_autoresearch.sqlite_state import DispatcherStore

    resolved = {
        **policy(),
        "schema_version": source_search.POLICY_VERSION,
        "policy_fingerprint": "sha256:policy",
        "scope_fingerprint": "sha256:scope",
        "operations": ["code.search_lexical"],
    }
    capability = "private-capability"
    with DispatcherStore(REPO, tmp_path) as store:
        store.configure_source_search(
            "inv-late", resolved, "sha256:" + sha256(capability.encode()),
        )

    def execute(*_args, **_kwargs):
        with DispatcherStore(REPO, tmp_path) as store:
            store.close_source_search("inv-late", "cancelled")
        return {
            "route": {
                "route_fingerprint": "sha256:route",
                "fallback_reason": None,
                "selected_backend_id": "rlm-tools-bsl",
                "adapter_version": "rlm-index/v1",
                "capability_fingerprint": "sha256:capability",
                "index_fingerprint": "sha256:index",
            },
            "result_manifest_fingerprint": "sha256:result",
            "result_count": 0,
            "returned_bytes": 2,
        }

    monkeypatch.setattr(source_search, "execute_query", execute)
    monkeypatch.setattr(source_search_bridge.indexes, "backend_statuses", lambda _repo: [])
    monkeypatch.setattr(source_search_bridge.sys, "stdin", io.StringIO(
        json.dumps({
            "jsonrpc": "2.0",
            "id": "call-late",
            "method": "tools/call",
            "params": {
                "name": "source_search",
                "arguments": {
                    "operation": "code.search_lexical",
                    "query": "test",
                    "component_id": None,
                    "path_prefix": None,
                    "max_results": 1,
                },
            },
        }) + "\n"
    ))
    output = io.StringIO()
    monkeypatch.setattr(source_search_bridge.sys, "stdout", output)
    source_search_bridge.serve(REPO, tmp_path, "inv-late", capability)
    reply = json.loads(output.getvalue())
    assert reply["result"]["isError"] is True
    assert reply["result"]["structuredContent"]["code"] == "source_search.cancelled"
    assert "sha256:result" not in output.getvalue()


def test_reservation_releases_unused_result_and_byte_capacity(tmp_path: Path):
    from one_c_autoresearch.sqlite_state import DispatcherStore

    resolved = {
        **policy(max_total_results=2, max_total_returned_bytes=100),
        "schema_version": source_search.POLICY_VERSION,
        "policy_fingerprint": "sha256:policy",
        "scope_fingerprint": "sha256:scope",
        "operations": ["code.search_lexical"],
    }
    with DispatcherStore(REPO, tmp_path) as store:
        store.configure_source_search("inv-release", resolved, "sha256:capability")
        store.reserve_source_search_call(
            "inv-release", "call-1", "sha256:capability",
            query_hmac="v1:first", capability="code-search-lexical", query_bytes=10,
            requested_results=2, requested_returned_bytes=100,
            requested_backend_seconds=5,
        )
        store.settle_source_search_call(
            "inv-release", "call-1", status="completed",
            result_count=0, returned_bytes=0, backend_seconds=1,
        )
        store.reserve_source_search_call(
            "inv-release", "call-2", "sha256:capability",
            query_hmac="v1:second", capability="code-search-lexical", query_bytes=10,
            requested_results=2, requested_returned_bytes=100,
            requested_backend_seconds=5,
        )


def test_reservation_shortens_deadline_and_reports_exact_exhausted_limit(tmp_path: Path):
    from one_c_autoresearch.sqlite_state import DispatcherStore, SourceSearchBudgetError

    resolved = {
        **policy(max_backend_seconds=6, max_total_query_bytes=10),
        "schema_version": source_search.POLICY_VERSION,
        "policy_fingerprint": "sha256:policy",
        "scope_fingerprint": "sha256:scope",
        "operations": ["code.search_lexical"],
    }
    with DispatcherStore(REPO, tmp_path) as store:
        store.configure_source_search("inv-limit", resolved, "sha256:capability")
        first = store.reserve_source_search_call(
            "inv-limit", "call-1", "sha256:capability",
            query_hmac="v1:first", capability="code-search-lexical", query_bytes=5,
            requested_results=1, requested_returned_bytes=10,
            requested_backend_seconds=5,
        )
        assert first["deadline_seconds"] == 5
        store.settle_source_search_call(
            "inv-limit", "call-1", status="completed", backend_seconds=5,
        )
        second = store.reserve_source_search_call(
            "inv-limit", "call-2", "sha256:capability",
            query_hmac="v1:second", capability="code-search-lexical", query_bytes=5,
            requested_results=1, requested_returned_bytes=10,
            requested_backend_seconds=5,
        )
        assert second["deadline_seconds"] == 1
        store.settle_source_search_call(
            "inv-limit", "call-2", status="completed", backend_seconds=1,
        )
        with pytest.raises(SourceSearchBudgetError) as failure:
            store.reserve_source_search_call(
                "inv-limit", "call-3", "sha256:capability",
                query_hmac="v1:third", capability="code-search-lexical", query_bytes=1,
                requested_results=1, requested_returned_bytes=10,
                requested_backend_seconds=1,
            )
        assert failure.value.limit == "max_total_query_bytes"
        assert failure.value.limit_value == 10
        assert failure.value.consumed == 10
        assert failure.value.requested == 1
        assert store.source_search_ledger("inv-limit")["last_error"] == {
            "code": "source_search.budget_exhausted",
            "limit": "max_total_query_bytes",
            "limit_value": 10,
            "consumed": 10,
            "requested": 1,
            "recovery": "start_new_invocation",
        }


def test_concurrent_reservation_admits_only_capacity_that_fits(tmp_path: Path):
    from one_c_autoresearch.sqlite_state import DispatcherStore, SourceSearchBudgetError

    resolved = {
        **policy(max_calls=1, max_concurrent_calls=1),
        "schema_version": source_search.POLICY_VERSION,
        "policy_fingerprint": "sha256:policy",
        "scope_fingerprint": "sha256:scope",
        "operations": ["code.search_lexical"],
    }
    with DispatcherStore(REPO, tmp_path) as store:
        store.configure_source_search("inv-race", resolved, "sha256:capability")
    barrier = threading.Barrier(2)
    outcomes = []

    def reserve(call_id: str):
        with DispatcherStore(REPO, tmp_path) as store:
            barrier.wait()
            try:
                store.reserve_source_search_call(
                    "inv-race", call_id, "sha256:capability",
                    query_hmac=f"v1:{call_id}", capability="code-search-lexical",
                    query_bytes=1, requested_results=1,
                    requested_returned_bytes=1, requested_backend_seconds=1,
                )
                outcomes.append("admitted")
            except SourceSearchBudgetError as exc:
                outcomes.append(exc.limit)

    threads = [
        threading.Thread(target=reserve, args=(f"call-{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["admitted", "max_calls"]


def test_capability_cannot_cross_invocations_or_projects(tmp_path: Path):
    from one_c_autoresearch.sqlite_state import DispatcherStore

    resolved = {
        **policy(),
        "schema_version": source_search.POLICY_VERSION,
        "policy_fingerprint": "sha256:policy",
        "scope_fingerprint": "sha256:scope",
        "operations": ["code.search_lexical"],
    }
    with DispatcherStore(REPO, tmp_path / "first") as store:
        store.configure_source_search("inv-first", resolved, "sha256:first")
        with pytest.raises(PermissionError, match="authentication"):
            store.reserve_source_search_call(
                "inv-first", "call", "sha256:second",
                query_hmac="v1:query", capability="code-search-lexical", query_bytes=1,
                requested_results=1, requested_returned_bytes=1,
                requested_backend_seconds=1,
            )
    with DispatcherStore(REPO, tmp_path / "second") as store:
        with pytest.raises(PermissionError, match="authentication"):
            store.reserve_source_search_call(
                "inv-first", "call", "sha256:first",
                query_hmac="v1:query", capability="code-search-lexical", query_bytes=1,
                requested_results=1, requested_returned_bytes=1,
                requested_backend_seconds=1,
            )


def test_expired_capability_and_inflight_completion_fail_closed(tmp_path: Path):
    from one_c_autoresearch.sqlite_state import DispatcherStore

    resolved = {
        **policy(),
        "schema_version": source_search.POLICY_VERSION,
        "policy_fingerprint": "sha256:policy",
        "scope_fingerprint": "sha256:scope",
        "operations": ["code.search_lexical"],
    }
    with DispatcherStore(REPO, tmp_path) as store:
        store.configure_source_search(
            "inv-expired",
            resolved,
            "sha256:capability",
            (datetime.now(timezone.utc) + timedelta(milliseconds=1)).isoformat(),
        )
        import time
        time.sleep(0.01)
        with pytest.raises(RuntimeError, match="expired"):
            store.reserve_source_search_call(
                "inv-expired", "call-1", "sha256:capability",
                query_hmac="v1:query", capability="code-search-lexical", query_bytes=10,
                requested_results=1, requested_returned_bytes=10,
                requested_backend_seconds=1,
            )
        store.configure_source_search("inv-complete", resolved, "sha256:capability")
        store.reserve_source_search_call(
            "inv-complete", "call-1", "sha256:capability",
            query_hmac="v1:query", capability="code-search-lexical", query_bytes=10,
            requested_results=1, requested_returned_bytes=10,
            requested_backend_seconds=1,
        )
        with pytest.raises(RuntimeError, match="in_flight"):
            store.complete_source_search("inv-complete")
        store.settle_source_search_call("inv-complete", "call-1", status="completed")
        store.complete_source_search("inv-complete")
        ledger = store.source_search_ledger("inv-complete")
        assert ledger["admission_state"] == "completed"
        assert ledger["ledger_complete"] and ledger["ledger_fingerprint"].startswith("sha256:")


def test_capability_is_inherited_without_appearing_in_codex_argv(monkeypatch, tmp_path: Path):
    from one_c_autoresearch import agents

    profile = {
        "instructions_version": "1",
        "environment_preset": "local-read-only",
        "model": "test",
        "reasoning_effort": "low",
        "input_context_tokens": 8192,
        "context_estimator_version": "utf8-v1",
        "capability_fingerprint": "sha256:test",
    }
    snapshot = {
        "operation": "mrq.consolidate",
        "profiles": {"selected": profile},
        "environment": {"executable": "/bin/true"},
        "context_manifest": {},
    }
    work = {"id": "coordinate", "kind": "consolidation-coordinate", "allowed_paths": []}
    schema = agents.proposal_schema("mrq.consolidate", work)
    capability = "raw-private-capability"
    monkeypatch.setattr(agents, "validate_execution_snapshot", lambda *_args: None)
    monkeypatch.setattr(agents, "verify_context_manifest", lambda *_args: None)

    def run(_runner, command, **kwargs):
        assert capability not in "\0".join(command)
        assert kwargs["env"]["ONE_C_AUTORESEARCH_SOURCE_SEARCH_CAPABILITY"] == capability
        raise RuntimeError("checked")

    monkeypatch.setattr(agents, "_run_command", run)
    with pytest.raises(RuntimeError, match="checked"):
        agents.execute(
            tmp_path,
            tmp_path / "proposal",
            profile,
            "mrq.consolidate",
            work,
            "",
            30,
            lambda: False,
            execution_snapshot=snapshot,
            prepared_context={"response_schema": schema, "prompt": "test"},
            invocation_id="inv",
            source_search_capability=capability,
            operational_state_root=tmp_path / "state",
        )
