from __future__ import annotations

from pathlib import Path

import pytest

from one_c_autoresearch import source_search
from one_c_autoresearch.source_search_bridge import _v2_provenance
from one_c_autoresearch.sqlite_state import DispatcherStore


REPO = Path(__file__).resolve().parents[1]


def _policy() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": source_search.POLICY_VERSION,
        "tool_schema_version": source_search.TOOL_SCHEMA_VERSION,
        "bridge_version": source_search.BRIDGE_VERSION,
        "operations": ["code.search_hybrid", "symbol.info"],
        "source_generation_id": "generation",
        "component_ids": ["target_cf:configuration"],
        "logical_path_prefixes": ["configuration/"],
        "routing_fingerprint": "sha256:routing",
        "scope_fingerprint": "sha256:scope",
        **{name: limit for name, limit in source_search.POLICY_LIMITS.items()},
    }
    value["policy_fingerprint"] = "sha256:policy"
    return value


def test_v2_provenance_contains_no_raw_query_or_content() -> None:
    provenance = _v2_provenance(
        {
            "schema_version": "source-search-result/v2",
            "operation": "symbol.info",
            "route": {"surface_identity": "sha256:surface"},
            "items": [
                {
                    "kind": "canonical-hit",
                    "component_id": "target_cf:configuration",
                    "source_generation_id": "generation",
                    "path": "configuration/CommonModules/A/Ext/Module.bsl",
                    "fingerprint": "sha256:file",
                    "secret_content": "must not enter ledger",
                },
                {"kind": "symbol-card", "body": "must not enter ledger"},
            ],
        }
    )
    assert provenance["result_class_counts"] == {
        "canonical_navigation_hit": 1,
        "derived_navigation_finding": 1,
    }
    assert "secret_content" not in str(provenance)
    assert "must not enter ledger" not in str(provenance)


def test_reference_hits_are_recorded_as_reference_navigation() -> None:
    provenance = _v2_provenance(
        {
            "schema_version": "source-search-result/v2",
            "operation": "reference.search_docs",
            "route": {
                "surface_identity": "sha256:surface",
                "reference_identity": "sha256:reference",
            },
            "items": [{"kind": "reference-hit", "reference_id": "ref-1", "title": "T"}],
        }
    )
    assert provenance["result_class_counts"] == {
        "reference_navigation_finding": 1
    }


def test_v2_settlement_is_fail_closed_and_reuse_ready(tmp_path: Path) -> None:
    with DispatcherStore(REPO, tmp_path) as store:
        store.configure_source_search("inv-v2", _policy(), "sha256:capability")
        store.reserve_source_search_call(
            "inv-v2",
            "call-1",
            "sha256:capability",
            query_hmac="v1:hmac-only",
            capability="symbol-info",
            operation="symbol.info",
            modality="workspace",
            query_bytes=10,
            requested_results=2,
            requested_returned_bytes=1000,
            requested_backend_seconds=5,
        )
        with pytest.raises(ValueError, match="incomplete_v2_provenance"):
            store.settle_source_search_call(
                "inv-v2",
                "call-1",
                status="completed",
                result_count=1,
                result_class_counts={"derived_navigation_finding": 1},
            )
        assert store.settle_source_search_call(
            "inv-v2",
            "call-1",
            status="completed",
            route_fingerprint="sha256:route",
            adapter_id="bsl-analyzer",
            index_fingerprint="sha256:index",
            result_manifest_fingerprint="sha256:result",
            surface_identity="sha256:surface",
            result_class_counts={"derived_navigation_finding": 1},
            derived_manifest_fingerprint="sha256:derived",
            result_count=1,
            returned_bytes=100,
            backend_seconds=1,
        )
        ledger = store.source_search_ledger("inv-v2")
        assert ledger["reuse_ready"] is True
        item = ledger["items"][0]
        assert item["operation"] == "symbol.info"
        assert item["modality"] == "workspace"
        assert item["result_class_counts"] == {"derived_navigation_finding": 1}
        assert "query" not in item
        assert "content" not in item


def test_hybrid_and_reference_identities_are_mandatory(tmp_path: Path) -> None:
    with DispatcherStore(REPO, tmp_path) as store:
        store.configure_source_search("inv-hybrid", _policy(), "sha256:capability")
        store.reserve_source_search_call(
            "inv-hybrid",
            "call-1",
            "sha256:capability",
            query_hmac="v1:hmac",
            capability="code-search-hybrid",
            operation="code.search_hybrid",
            modality="hybrid",
            query_bytes=1,
            requested_results=1,
            requested_returned_bytes=10,
            requested_backend_seconds=1,
        )
        with pytest.raises(ValueError, match="incomplete_v2_provenance"):
            store.settle_source_search_call(
                "inv-hybrid",
                "call-1",
                status="completed",
                surface_identity="sha256:surface",
                result_class_counts={},
            )
