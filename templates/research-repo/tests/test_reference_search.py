from __future__ import annotations

import json
from pathlib import Path

import pytest

import one_c_autoresearch.reference_search as reference_search
from one_c_autoresearch.reference_search import (
    ItsAdmission,
    current_reference_index,
    normalize_docs_result,
    normalize_its_result,
    normalize_syntax_result,
    promote_reference_index,
    reference_process_spec,
    require_repository_evidence,
    stage_reference_index,
    validate_its_request,
    validate_reference_contract,
    validate_syntax_tool,
)


FIXTURE = Path(__file__).parent / "fixtures" / "bsl-analyzer-contract-1.3.json"


def test_current_reference_contract_is_accepted() -> None:
    validate_reference_contract(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_reference_build_is_private_and_rejects_credentials_and_external_corpus(
    tmp_path: Path,
) -> None:
    spec = reference_process_spec(
        Path("/bin/bsl-analyzer"),
        tmp_path,
        "build",
        environment={"HTTPS_PROXY": "proxy", "NAPARNIK_TOKEN": "secret", "LANG": "C"},
    )
    assert spec["network"] is False
    assert spec["downloads"] is False
    assert spec["reuse_process"] is False
    assert spec["command"][-2:] == ["--mode", "stdio"]
    assert spec["environment"]["XDG_RUNTIME_DIR"].endswith("/runtime")
    assert "HTTPS_PROXY" not in spec["environment"]
    assert "NAPARNIK_TOKEN" not in spec["environment"]
    with pytest.raises(ValueError, match="external_corpus_forbidden"):
        stage_reference_index(
            tmp_path,
            "sha256:tool",
            {"source": "download", "corpus_fingerprint": "sha256:corpus"},
        )


@pytest.mark.parametrize(
    "result",
    [
        {"state": "cancelled", "cancelled": True},
        {"state": "ready", "network_attempts": 1},
        {"state": "ready", "download_attempts": 1},
        {"state": "ready", "corpus_fingerprint": "sha256:stale"},
    ],
)
def test_reference_index_is_not_promoted_after_partial_or_stale_build(
    tmp_path: Path, result: dict[str, object]
) -> None:
    staging = stage_reference_index(
        tmp_path,
        "sha256:tool",
        {
            "source": "selected-build-bundled",
            "corpus_fingerprint": "sha256:corpus",
            "build_version": "1.3.0",
        },
    )
    with pytest.raises(ValueError, match="not_promotable"):
        promote_reference_index(staging, result)


def test_reference_index_promotes_only_matching_complete_build(tmp_path: Path) -> None:
    staging = stage_reference_index(
        tmp_path,
        "sha256:tool",
        {
            "source": "selected-build-bundled",
            "corpus_fingerprint": "sha256:corpus",
            "build_version": "1.3.0",
        },
    )
    destination = promote_reference_index(
        staging, {"state": "ready", "corpus_fingerprint": "sha256:corpus"}
    )
    assert current_reference_index(tmp_path, "sha256:tool") == destination
    with pytest.raises(ValueError, match="stale_reference_index"):
        current_reference_index(tmp_path, "sha256:other")


def test_reference_index_rejects_symlinked_build_output(tmp_path: Path) -> None:
    staging = stage_reference_index(
        tmp_path,
        "sha256:tool",
        {
            "source": "selected-build-bundled",
            "corpus_fingerprint": "sha256:corpus",
            "build_version": "1.3.0",
        },
    )
    (staging / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink_forbidden"):
        promote_reference_index(
            staging, {"state": "ready", "corpus_fingerprint": "sha256:corpus"}
        )


def test_docs_use_structured_schema_and_are_navigation_only() -> None:
    result = normalize_docs_result(
        {
            "structuredContent": {
                "schema_version": "1",
                "hits": [{"path": "docs/a.md", "snippet": "Массив"}],
                "shown": 1,
                "total": 1,
            },
            "content": [{"type": "text", "text": "ignored mirror"}],
        }
    )
    assert result["hits"][0]["path"] == "docs/a.md"
    with pytest.raises(ValueError, match="not_canonical_evidence"):
        require_repository_evidence(result)


def test_docs_reject_unsupported_schema_and_bounds() -> None:
    with pytest.raises(ValueError, match="unsupported_docs_schema"):
        normalize_docs_result({"structuredContent": {"schema_version": "2", "hits": []}})
    with pytest.raises(ValueError, match="docs_bounds_exceeded"):
        normalize_docs_result(
            {"structuredContent": {"schema_version": "1", "hits": [{}] * 51}}
        )


def test_syntax_requires_output_schema_one_closed_kind_and_structured_body() -> None:
    validate_syntax_tool(
        {
            "outputSchema": {
                "properties": {
                    "schema_version": {"const": "1"},
                    "kind": {
                        "enum": ["type", "method", "global_function", "keyword"]
                    },
                }
            }
        }
    )
    result = normalize_syntax_result(
        {
            "structuredContent": {
                "schema_version": "1",
                "kind": "keyword",
                "name": "Если",
            },
            "content": [{"type": "text", "text": "compatibility only"}],
        }
    )
    assert result["structured"]["name"] == "Если"
    assert result["compatibility_diagnostic"] == "compatibility only"
    with pytest.raises(ValueError, match="unsupported_syntax_schema"):
        normalize_syntax_result(
            {"structuredContent": {"schema_version": "1", "kind": "future_kind"}}
        )


def test_its_policy_is_fixed_bounded_and_single_flight(tmp_path: Path) -> None:
    validate_its_request("Как работает Массив?", disclosure_acknowledged=True, token="token")
    spec = reference_process_spec(
        Path("/bin/bsl-analyzer"),
        tmp_path,
        "its",
        token="token",
        environment={"HTTPS_PROXY": "proxy", "NAPARNIK_TOKEN": "old"},
    )
    assert spec["network"] is True
    assert spec["environment"]["NAPARNIK_TOKEN"] == "token"
    assert "HTTPS_PROXY" not in spec["environment"]
    normalized = normalize_its_result(
        {"content": [{"type": "text", "text": "Ответ"}]}
    )
    assert normalized["endpoint"] == "https://code.1c.ai"
    with pytest.raises(ValueError, match="its_disclosure_required"):
        validate_its_request("Вопрос", disclosure_acknowledged=False, token="token")
    with pytest.raises(ValueError, match="its_question_too_large"):
        validate_its_request("я" * 4097, disclosure_acknowledged=True, token="token")

    admission = ItsAdmission()
    with admission.acquire():
        with pytest.raises(RuntimeError, match="its_busy"):
            with admission.acquire():
                pass


def test_reference_execution_passes_cancellation_to_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancelled = lambda: False
    observed = {}

    def call(*_args, **kwargs):
        observed["cancelled"] = kwargs["cancelled"]
        return {
            "structuredContent": {
                "schema_version": "1",
                "kind": "keyword",
                "name": "Если",
            },
            "content": [{"type": "text", "text": "compatibility only"}],
        }

    monkeypatch.setattr(reference_search, "_mcp_call", call)
    result = reference_search.execute_reference(
        Path("/bin/bsl-analyzer"),
        tmp_path,
        {"operation": "reference.syntax_help", "name": "Если"},
        cancelled=cancelled,
    )
    assert result["items"][0]["reference_id"] == "syntax:keyword:Если"
    assert observed["cancelled"] is cancelled
