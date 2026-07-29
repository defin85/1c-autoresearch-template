"""Тесты этапов диспетчерских графов.

Этапы реализованы как чистые функции поверх существующего исполнителя и
прикладного сервиса; LangGraph используется только для чекпойнтов через
``SqliteSaver`` в ``DispatcherStore``. Параллелизм ограничивается
координатором, поэтому здесь проверяем детерминизм и контракты этапов.
"""

from __future__ import annotations

import csv
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from one_c_autoresearch.dispatcher import DispatcherBindings, DispatcherCoordinator
from one_c_autoresearch.events import EventStore
from one_c_autoresearch.pipeline_graphs import (
    MAX_DIF_WINDOW,
    MAX_GROUP_CANDIDATES,
    build_decide_state,
    build_classify_state,
    build_discover_state,
    compile_analyze_graph,
    compile_classify_graph,
    compile_consolidate_graph,
    decide_apply,
    decide_research_one,
    decide_select_mrq,
    derived_gap_card,
    discover_analyze_one,
    discover_barrier,
    discover_batch_from_groups,
    discover_preliminary_group,
    discover_publish_batch,
    discover_review_noise,
    discover_select_window,
    _bounded_map,
    _compatible_cached,
    _validated_window_batches,
)
from one_c_autoresearch.sqlite_state import DispatcherStore


# -- helpers --------------------------------------------------------------


def _bootstrap_repo(tmp_path: Path, customer_diffs: list[dict[str, str]], *, with_mrq: list[dict] | None = None) -> Path:
    repo = tmp_path / "repo"
    research = repo / "research"
    research.mkdir(parents=True)
    (repo / "project.toml").write_text('[project]\nid="t"\nproduct="t"\nbaseline_version="1"\ntarget_version="1"\nnext_vendor_version="1"\ndescription="t"\n', encoding="utf-8")
    (research / "workflow.toml").write_bytes((Path(__file__).resolve().parents[1] / "research/workflow.toml").read_bytes())
    (research / "infobases.toml").write_text('schema_version = "1"\n', encoding="utf-8")
    (research / "external-artifacts.toml").write_text('schema_version = "1"\n', encoding="utf-8")
    (research / "indexing.toml").write_text('schema_version = "1"\nengine = "rlm-tools-bsl"\nengine_version = "1.28.1"\n', encoding="utf-8")
    (research / "forbidden-authorities.json").write_bytes((Path(__file__).resolve().parents[1] / "research/forbidden-authorities.json").read_bytes())
    source_id = "a" * 64
    diff_id = "b" * 64
    source_pointer = {
        "schema_version": "1",
        "generation_id": source_id,
        "acquisition_profile_id": "ibcmd+xml-hierarchical/v1",
        "normalizer_version": "3",
        "representation_schema": "xml-hierarchical",
        "components": [{"component_id": "target_cf:configuration", "path": "target_cf"}],
    }
    (research / "active-source-generation.json").write_text(json.dumps(source_pointer), encoding="utf-8")
    (research / "active-diff-generation.json").write_text(json.dumps({"schema_version": "1", "generation_id": diff_id, "source_generation_id": source_id}), encoding="utf-8")
    (research / "active-generation.json").write_text(json.dumps({"schema_version": "1", "canonical_generation_id": None, "source_generation_id": source_id, "diff_generation_id": diff_id}), encoding="utf-8")
    diff_root = repo / "analysis/indexes/generations" / diff_id
    diff_root.mkdir(parents=True)
    with (diff_root / "diff-inventory.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["stable_diff_id", "before_role", "after_role", "path", "after_fingerprint", "before_fingerprint"])
        writer.writeheader()
        for row in customer_diffs:
            writer.writerow(row)
            source_path = repo / "sources/generations" / source_id / "target_cf" / row["path"]
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(f"// {row['stable_diff_id']}\n", encoding="utf-8")
    with (diff_root / "target-coverage.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["customer_diff_id", "target_diff_ids", "coverage_status", "evidence_ref"])
        writer.writeheader()
    if with_mrq:
        # публикуем каноническое MRQ-поколение через сам `mrq.publish`, чтобы
        # generation_id был корректным SHA-256 от preimage.
        from one_c_autoresearch.mrq import FILES, publish
        from one_c_autoresearch.contracts import sha256
        rows_state = {name: [] for name in FILES}
        rows_state["mrq.jsonl"] = with_mrq
        # валидируем граф с допустимым набором customer_diffs
        valid_customer = {row["stable_diff_id"]: row for row in customer_diffs}
        from one_c_autoresearch.mrq import validate_graph
        validate_graph(rows_state, source_id, diff_id, valid_customer)
        publish(repo, rows_state, source_id, diff_id, source_pointer, None)
    return repo


def _bindings(work_unit_id: str = "discover-mrq") -> dict[str, Any]:
    return {
        "project_id": "proj-1",
        "job_id": "discover-mrq",
        "work_unit_id": work_unit_id,
        "source_generation_id": "a" * 64,
        "diff_generation_id": "b" * 64,
        "canonical_generation_id": "",
        "workflow_fingerprint": "sha256:wf",
        "agent_profile_fingerprint": "sha256:profile",
        "instruction_supplement": "",
    }


def _customer_diff(identifier: str, path: str = "Catalogs/Invoices/a.bsl") -> dict[str, str]:
    return {"stable_diff_id": identifier, "before_role": "vendor_baseline", "after_role": "target_cf", "path": path, "after_fingerprint": "sha256:" + "a" * 64, "before_fingerprint": "sha256:" + "b" * 64}


def _group(identifier: str, stable_diff_ids: list[str]) -> dict[str, Any]:
    return {
        "anchor_diff_id": identifier,
        "semantic_key": identifier,
        "title": identifier,
        "stable_diff_ids": stable_diff_ids,
        "supporting_diff_ids": [],
        "evidence": [{"path": "Catalogs/Invoices/a.bsl", "fingerprint": "sha256:" + "a" * 64}],
        "business_meaning": "M",
        "scope": "S",
        "confidence": "high",
        "rationale": "R",
    }


# -- лимиты ---------------------------------------------------------------


def test_fixed_limits_match_design_contract() -> None:
    assert MAX_DIF_WINDOW == 32
    assert MAX_GROUP_CANDIDATES == 64


def test_analyze_window_combines_local_and_agent_rows_atomically_without_starting_stage_three(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _bootstrap_repo(tmp_path, [_customer_diff("DIF-LOCAL"), _customer_diff("DIF-AGENT")])
    store = DispatcherStore(repo, base=tmp_path / "state")
    store.open()
    pending = ["DIF-AGENT", "DIF-LOCAL"]
    published: list[list[dict[str, Any]]] = []
    agent_calls: list[str] = []

    def publish_empty(_repo, **_kwargs):
        (repo / "research/active-dif-classification-generation.json").write_text("{}", encoding="utf-8")
        return {"generation_id": "empty"}

    def publish_window(_repo, rows, **_kwargs):
        published.append(list(rows))
        pending.clear()
        return {"generation_id": "published"}

    monkeypatch.setattr("one_c_autoresearch.dif_classifications.publish_empty", publish_empty)
    monkeypatch.setattr("one_c_autoresearch.dif_classifications.remaining_ids", lambda _repo: list(pending))
    monkeypatch.setattr("one_c_autoresearch.dif_classifications.load_active", lambda _repo: {"pointer": {"generation_id": "empty"}})
    monkeypatch.setattr("one_c_autoresearch.dif_classifications.publish_window", publish_window)
    monkeypatch.setattr("one_c_autoresearch.dif_classifications.physical_evidence_fingerprints", lambda _repo: {"DIF-AGENT": "sha256:" + "a" * 64})
    monkeypatch.setattr("one_c_autoresearch.dif_classifications.make_row", lambda identifier, result, **_kwargs: {"stable_diff_id": identifier, "kind": result["kind"]})
    monkeypatch.setattr("one_c_autoresearch.component_groups.deterministic_results", lambda _repo, _ids: {
        "DIF-LOCAL": {
            "result": {"kind": "meaning"},
            **{key: "sha256:" + "b" * 64 for key in (
                "evidence_fingerprint", "result_schema_fingerprint", "profile_fingerprint",
                "instruction_fingerprint", "context_fingerprint",
            )},
        },
    })
    monkeypatch.setattr("one_c_autoresearch.pipeline_graphs._analyze_work_unit", lambda _repo, identifier: {"id": identifier, "allowed_path_fingerprints": []})

    def analyze(state, identifier, **_kwargs):
        agent_calls.append(identifier)
        return {**state, "analyzed": {identifier: {"kind": "meaning"}}}

    monkeypatch.setattr("one_c_autoresearch.pipeline_graphs.discover_analyze_one", analyze)
    graph = compile_analyze_graph(
        repo=repo,
        saver=store.saver,
        executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("wrapped executor is not called directly")),
        profile={},
        supplement="",
        timeout_seconds=60,
        cancelled=lambda: False,
        bindings_check=lambda: True,
    )
    try:
        state = graph.invoke(build_discover_state(_bindings(), "run", "thread"), config={"configurable": {"thread_id": "thread"}})
        assert state["status"] == "completed"
        assert agent_calls == ["DIF-AGENT"]
        assert [{row["stable_diff_id"] for row in window} for window in published] == [{"DIF-AGENT", "DIF-LOCAL"}]
        pending[:] = ["DIF-AGENT", "DIF-LOCAL"]
        published.clear()
        monkeypatch.setattr(
            "one_c_autoresearch.pipeline_graphs.discover_analyze_one",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("agent failed")),
        )
        failed = graph.invoke(
            build_discover_state(_bindings(), "run-2", "thread-2"),
            config={"configurable": {"thread_id": "thread-2"}},
        )
    finally:
        store.close()
    assert failed["status"] == "failed"
    assert published == []


def test_bounded_map_has_no_implicit_total_20_and_respects_requested_limit() -> None:
    lock = threading.Lock()
    ready = threading.Barrier(24)
    active = peak = 0

    def invoke(identifier: str) -> str:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        ready.wait(timeout=2)
        with lock:
            active -= 1
        return identifier

    items = [f"DIF-{index:02d}" for index in range(24)]
    results, errors, stopped = _bounded_map(items, max_concurrency=24, slot_count=24, function=invoke, cancelled=lambda: False, bindings_check=lambda: True)
    assert len(results) == 24 and not errors and not stopped
    assert peak > 20


def test_bounded_map_sequential_mode_preserves_stable_order() -> None:
    calls: list[str] = []
    items = ["DIF-AAAA", "DIF-BBBB", "DIF-CCCC"]
    results, errors, stopped = _bounded_map(items, max_concurrency=1, slot_count=1, function=lambda item: calls.append(item) or item, cancelled=lambda: False, bindings_check=lambda: True)
    assert calls == items
    assert list(results) == items
    assert not errors and not stopped


def test_bounded_map_requests_active_cancellation_after_first_error() -> None:
    abort = threading.Event()
    sibling_started = threading.Event()
    sibling_observed_abort = threading.Event()

    def invoke(identifier: str) -> str:
        if identifier == "fail":
            sibling_started.wait(1)
            raise RuntimeError("failed")
        sibling_started.set()
        if abort.wait(1):
            sibling_observed_abort.set()
        return identifier

    _results, errors, stopped = _bounded_map(
        ["fail", "sibling", "never-issued"],
        max_concurrency=2,
        slot_count=2,
        function=invoke,
        cancelled=lambda: False,
        bindings_check=lambda: True,
        cancel_active=abort.set,
    )
    assert set(errors) == {"fail"}
    assert sibling_observed_abort.is_set()
    assert stopped is False


def test_reuse_snapshot_requires_matching_result_origin() -> None:
    expected = {"compatibility_fingerprint": "sha256:compatible"}
    envelope = {
        "compatibility_fingerprint": "sha256:compatible",
        "source_run_id": "old-run",
        "source_execution_snapshot_fingerprint": "sha256:old-snapshot",
        "result": {"ok": True},
    }
    assert _compatible_cached(envelope, expected) == {"ok": True}
    assert _compatible_cached(
        envelope,
        expected,
        {
            "source_run_id": "old-run",
            "source_execution_snapshot_fingerprint": "sha256:old-snapshot",
        },
    ) == {"ok": True}
    assert _compatible_cached(
        envelope,
        expected,
        {
            "source_run_id": "other-run",
            "source_execution_snapshot_fingerprint": "sha256:old-snapshot",
        },
    ) is None
    mismatch = {
        "compatibility_fingerprint": "sha256:new",
        "compatibility": {
            "context_contract_version": "context-envelope/v1",
            "context_estimator_version": "old-estimator",
        },
    }
    source = {
        "compatibility_fingerprint": "sha256:old",
        "compatibility": {
            "context_contract_version": "context-envelope/v1",
            "context_estimator_version": "utf8-v1",
        },
    }
    assert _compatible_cached(source, mismatch) is None
    assert mismatch["reuse_incompatibility"] == {
        "field": "context_estimator_version",
    }


# -- этапы discover-mrq ---------------------------------------------------


def test_select_window_returns_uncovered_diffs_in_stable_order(tmp_path: Path) -> None:
    diffs = [_customer_diff("DIF-BBBB"), _customer_diff("DIF-AAAA"), _customer_diff("DIF-CCCC")]
    repo = _bootstrap_repo(tmp_path, diffs)
    from one_c_autoresearch.pipeline_graphs import select_dif_window
    window = select_dif_window(repo)
    assert window == ["DIF-AAAA", "DIF-BBBB", "DIF-CCCC"]


def test_select_window_caps_at_max_dif_window(tmp_path: Path) -> None:
    diffs = [_customer_diff(f"DIF-{i:04d}") for i in range(MAX_DIF_WINDOW + 5)]
    repo = _bootstrap_repo(tmp_path, diffs)
    from one_c_autoresearch.pipeline_graphs import select_dif_window
    window = select_dif_window(repo)
    assert len(window) == MAX_DIF_WINDOW


def test_analyze_one_classifies_meaning(tmp_path: Path) -> None:
    diffs = [_customer_diff("DIF-AAAA")]
    repo = _bootstrap_repo(tmp_path, diffs)
    state = build_discover_state(_bindings(), "run-1", "thread-1")
    state = discover_select_window(state, repo=repo)

    def executor(_repo, _profile, _op, work_unit, _supplement, _timeout, _cancelled):
        return {
            "semantic_key": "x",
            "title": "X",
            "stable_diff_ids": [work_unit["id"]],
            "supporting_diff_ids": [],
            "evidence": [{"path": "Catalogs/Invoices/a.bsl", "fingerprint": "sha256:" + "a" * 64, "stable_diff_id": work_unit["id"]}],
            "business_meaning": "M",
            "scope": "S",
            "confidence": "high",
            "rationale": "R",
        }

    state = discover_analyze_one(state, "DIF-AAAA", executor=executor, repo=repo, profile={}, supplement="", timeout_seconds=60, cancelled=lambda: False, bindings_check=lambda: True)
    assert state["meanings"] == ["DIF-AAAA"]
    assert state["analyzed"]["DIF-AAAA"]["kind"] == "meaning"


def test_analyze_one_blocks_on_changed_bindings(tmp_path: Path) -> None:
    diffs = [_customer_diff("DIF-AAAA")]
    repo = _bootstrap_repo(tmp_path, diffs)
    state = build_discover_state(_bindings(), "run-1", "thread-1")
    state = discover_select_window(state, repo=repo)

    def executor(*_args, **_kwargs):
        pytest.fail("executor must not run when bindings changed")

    state = discover_analyze_one(state, "DIF-AAAA", executor=executor, repo=repo, profile={}, supplement="", timeout_seconds=60, cancelled=lambda: False, bindings_check=lambda: False)
    assert state["status"] == "stale"
    assert state["blocker"]["code"] == "dispatcher.bindings.stale"


def test_barrier_blocks_when_unclassified_diffs_remain(tmp_path: Path) -> None:
    diffs = [_customer_diff("DIF-AAAA"), _customer_diff("DIF-BBBB")]
    repo = _bootstrap_repo(tmp_path, diffs)
    state = build_discover_state(_bindings(), "run-1", "thread-1")
    state = {**state, "meanings": ["DIF-AAAA"], "noise": []}  # DIF-BBBB не классифицирован
    result = discover_barrier(state, repo=repo)
    assert result["barrier_open"] is False
    assert result["blocker"]["code"] == "dispatcher.barrier.unclassified"


def test_barrier_blocks_when_meaning_without_group(tmp_path: Path) -> None:
    diffs = [_customer_diff("DIF-AAAA")]
    repo = _bootstrap_repo(tmp_path, diffs)
    state = build_discover_state(_bindings(), "run-1", "thread-1")
    state = {**state, "meanings": ["DIF-AAAA"], "noise": [], "preliminary_groups": {}}
    result = discover_barrier(state, repo=repo)
    assert result["barrier_open"] is False
    assert result["blocker"]["code"] == "dispatcher.barrier.ungrouped"


def test_barrier_blocks_on_overlapping_groups(tmp_path: Path) -> None:
    diffs = [_customer_diff("DIF-AAAA"), _customer_diff("DIF-BBBB")]
    repo = _bootstrap_repo(tmp_path, diffs)
    state = build_discover_state(_bindings(), "run-1", "thread-1")
    state = {
        **state,
        "meanings": ["DIF-AAAA", "DIF-BBBB"],
        "noise": [],
        "preliminary_groups": {
            "DIF-AAAA": _group("DIF-AAAA", ["DIF-AAAA", "DIF-BBBB"]),
            "DIF-BBBB": _group("DIF-BBBB", ["DIF-BBBB"]),
        },
    }
    result = discover_barrier(state, repo=repo)
    assert result["barrier_open"] is False
    assert result["blocker"]["code"] == "dispatcher.barrier.overlap"


def test_barrier_blocks_on_noise_primary_clash(tmp_path: Path) -> None:
    diffs = [_customer_diff("DIF-AAAA")]
    repo = _bootstrap_repo(tmp_path, diffs)
    state = build_discover_state(_bindings(), "run-1", "thread-1")
    state = {
        **state,
        "meanings": ["DIF-AAAA"],
        "noise": ["DIF-AAAA"],  # шум и смысловое распределение одновременно
        "preliminary_groups": {"DIF-AAAA": _group("DIF-AAAA", ["DIF-AAAA"])},
    }
    result = discover_barrier(state, repo=repo)
    assert result["barrier_open"] is False
    assert result["blocker"]["code"] == "dispatcher.barrier.noise_clash"


def test_barrier_opens_for_full_disjoint_coverage(tmp_path: Path) -> None:
    diffs = [_customer_diff("DIF-AAAA"), _customer_diff("DIF-BBBB")]
    repo = _bootstrap_repo(tmp_path, diffs)
    state = build_discover_state(_bindings(), "run-1", "thread-1")
    state = {
        **state,
        "meanings": ["DIF-AAAA", "DIF-BBBB"],
        "noise": [],
        "preliminary_groups": {
            "DIF-AAAA": _group("DIF-AAAA", ["DIF-AAAA"]),
            "DIF-BBBB": _group("DIF-BBBB", ["DIF-BBBB"]),
        },
    }
    result = discover_barrier(state, repo=repo)
    assert result["barrier_open"] is True
    assert result["status"] == "running"


@pytest.mark.parametrize(
    ("groups", "code"),
    [
        ({"x": _group("x", ["DIF-UNKNOWN"])}, "dispatcher.barrier.unknown"),
        ({"x": {**_group("x", ["DIF-AAAA"]), "evidence": []}}, "dispatcher.barrier.evidence"),
        ({"x": {**_group("x", ["DIF-AAAA"]), "evidence": [{"path": "Catalogs/x.bsl"}]}}, "dispatcher.barrier.evidence"),
        ({"x": {**_group("x", []), "supporting_diff_ids": ["DIF-AAAA"]}}, "dispatcher.barrier.ungrouped"),
    ],
)
def test_barrier_rejects_unknown_unproved_or_nonprimary_coverage(tmp_path: Path, groups: dict[str, Any], code: str) -> None:
    repo = _bootstrap_repo(tmp_path, [_customer_diff("DIF-AAAA")])
    state = {
        **build_discover_state(_bindings(), "run-1", "thread-1"),
        "meanings": ["DIF-AAAA"],
        "noise": [],
        "preliminary_groups": groups,
    }
    result = discover_barrier(state, repo=repo)
    assert result["barrier_open"] is False
    assert result["blocker"]["code"] == code


def test_batch_from_groups_preserves_stable_order(tmp_path: Path) -> None:
    state = build_discover_state(_bindings(), "run-1", "thread-1")
    state = {
        **state,
        "preliminary_groups": {
            "DIF-BBBB": {"anchor_diff_id": "DIF-BBBB", "semantic_key": "b", "stable_diff_ids": ["DIF-BBBB"]},
            "DIF-AAAA": {"anchor_diff_id": "DIF-AAAA", "semantic_key": "a", "stable_diff_ids": ["DIF-AAAA"]},
        },
        "noise": ["DIF-CCCC"],
        "approved_noise_ids": ["DIF-CCCC"],  # шум утверждён локальным пользователем
    }
    result = discover_batch_from_groups(state)
    assert [g["anchor_diff_id"] for g in result["batch_proposals"]] == ["DIF-AAAA", "DIF-BBBB"]
    assert result["approved_noise_ids"] == []


def test_noise_requires_explicit_approval_before_coordinator(tmp_path: Path) -> None:
    repo = _bootstrap_repo(tmp_path, [_customer_diff("DIF-AAAA")])
    state = {
        **build_discover_state(_bindings(), "run-1", "thread-1"),
        "analyzed": {
            "DIF-AAAA": {
                "stable_diff_id": "DIF-AAAA",
                "kind": "noise",
                "proposal": {},
                "evidence": [{"path": "Catalogs/Invoices/a.bsl", "fingerprint": "sha256:" + "a" * 64}],
                "rationale": "technical",
            }
        },
        "noise": ["DIF-AAAA"],
    }
    result = discover_review_noise(state, repo=repo)
    assert result["status"] == "blocked"
    assert result["blocker"]["code"] == "approval.noise"
    assert result["noise_review_ids"] == ["DIF-AAAA"]
    reviewed = [{
        "stable_diff_id": "DIF-AAAA",
        "actor": "local-user",
        "rationale": "technical",
        "evidence": state["analyzed"]["DIF-AAAA"]["evidence"],
        "timestamp": "2026-07-23T00:00:00+00:00",
    }]
    approved = discover_review_noise(state, repo=repo, approved_noise_ids=["DIF-AAAA"])
    assert approved["status"] == "running"
    prepared = discover_batch_from_groups(approved, reviewed)
    assert prepared["approved_noise"] == reviewed
    assert prepared["approved_noise_ids"] == ["DIF-AAAA"]


def test_publish_batch_invokes_internal_operation(tmp_path: Path) -> None:
    diffs = [_customer_diff("DIF-AAAA")]
    repo = _bootstrap_repo(tmp_path, diffs)
    state = build_discover_state(_bindings(), "run-1", "thread-1")
    state = {**state, "batch_proposals": [{"semantic_key": "x", "title": "X", "stable_diff_ids": ["DIF-AAAA"], "supporting_diff_ids": [], "evidence": [], "business_meaning": "M", "scope": "S", "confidence": "high", "rationale": "R"}]}
    captured: list[tuple[str, dict[str, Any]]] = []

    def apply(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        captured.append((operation, payload))
        return {"mrq_ids": ["MRQ-AAA"], "canonical_generation_id": "c" * 64}

    state = discover_publish_batch(state, repo=repo, apply=apply, approved_noise_payload=[])
    assert captured[0][0] == "mrq.publish-source-batch"
    assert captured[0][1]["group_proposals"][0]["stable_diff_ids"] == ["DIF-AAAA"]
    assert state["published_mrq_ids"] == ["MRQ-AAA"]


def _mrq(identifier: str, semantic_key: str, *, state: str = "draft", evidence_path: str = "Catalogs/Invoices/a.bsl", stable_diff_id: str = "DIF-AAAA", decision: dict | None = None) -> dict:
    from one_c_autoresearch.contracts import mrq_id
    return {
        "schema_version": "1",
        "mrq_id": mrq_id(semantic_key),
        "semantic_key": semantic_key,
        "title": identifier,
        "state": state,
        "source_generation_id": "a" * 64,
        "diff_generation_id": "b" * 64,
        "source_customization": {"business_meaning": "M", "scope": "S", "confidence": "high", "rationale": "R", "evidence": [{"path": evidence_path, "fingerprint": "sha256:" + "a" * 64, "stable_diff_id": stable_diff_id}]},
        "migration_decision": decision or {},
    }


def test_classify_resume_reuses_completed_windows(tmp_path: Path) -> None:
    diffs = [_customer_diff(f"DIF-{index:04X}", f"Catalogs/Invoices/{index}.bsl") for index in range(17)]
    mrqs = [
        _mrq(
            f"MRQ {index}",
            f"invoice-{index}",
            evidence_path=f"Catalogs/Invoices/{index}.bsl",
            stable_diff_id=diff["stable_diff_id"],
        )
        for index, diff in enumerate(diffs)
    ]
    repo = _bootstrap_repo(tmp_path, diffs, with_mrq=mrqs)
    store = DispatcherStore(repo, base=tmp_path / "state")
    store.open()
    cached: dict[str, dict[str, Any]] = {}
    calls: list[str] = []
    reused: list[str] = []

    def executor(_repo, _profile, _operation, work_unit, _supplement, _timeout, _cancelled):
        calls.append(work_unit["id"])
        return {
            "groups": [{
                "mrq_ids": [item["mrq_id"] for item in work_unit["mrqs"]],
                "basis": "shared component",
                "linkage_proven": True,
            }],
        }

    arguments = dict(
        repo=repo,
        saver=store.saver,
        executor=executor,
        profile={},
        supplement="",
        timeout_seconds=60,
        bindings_check=lambda: True,
        load_result=cached.get,
        save_result=lambda key, value: cached.__setitem__(key, value),
        reuse_work=lambda _phase, _role, work: reused.append(work),
    )
    bindings = {**_bindings("classify:source"), "job_id": "classify-mrq"}
    try:
        stopped = compile_classify_graph(**arguments, cancelled=lambda: bool(calls)).invoke(
            build_classify_state(bindings, "run-1", "thread-1"),
            config={"configurable": {"thread_id": "thread-1"}},
        )
        assert stopped["status"] == "resumable"
        assert calls == ["window:0"]
        assert set(cached) == {"classify-batches:window:0"}

        completed = compile_classify_graph(**arguments, cancelled=lambda: False).invoke(
            build_classify_state(bindings, "run-1", "thread-1"),
            config={"configurable": {"thread_id": "thread-1"}},
        )
        assert completed["status"] == "completed"
        assert calls == ["window:0", "window:1"]
        assert reused == ["window:0"]
    finally:
        store.close()


@pytest.mark.parametrize("missing_links", [False, True])
def test_consolidation_uses_page_links_and_binary_reduction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_links: bool,
) -> None:
    repo = _bootstrap_repo(tmp_path, [_customer_diff("DIF-1")])
    store = DispatcherStore(repo, base=tmp_path / "state")
    store.open()
    snapshot = {
        "classifications": [{"stable_diff_id": "DIF-1", "classification": "meaning"}],
        "prior_consolidation": {},
    }
    monkeypatch.setattr("one_c_autoresearch.dif_classifications.coverage", lambda _repo: {
        "all_dif_classified": True, "remaining": 0,
    })
    monkeypatch.setattr("one_c_autoresearch.consolidation.input_snapshot", lambda _repo: snapshot)
    monkeypatch.setattr("one_c_autoresearch.consolidation.normalized_records", lambda _snapshot: [
        {"record_id": "DIF-1", "stable_diff_id": "DIF-1"},
    ])
    monkeypatch.setattr("one_c_autoresearch.consolidation.partition_manifest", lambda *_args, **_kwargs: {
        "partitions": [{"record_ids": ["DIF-1"]}],
        "pairs": [{"left": 0, "right": 0}],
        "planned_invocation_count": 2,
    })
    captured = {}
    monkeypatch.setattr(
        "one_c_autoresearch.consolidation.plan_from_groups",
        lambda _snapshot, groups, noise, _manifest: captured.update(
            groups=groups, noise=noise
        ) or {"outcomes": {"new": ["MRQ-1"]}},
    )
    monkeypatch.setattr(
        "one_c_autoresearch.consolidation.store_plan",
        lambda *_args: ("sha256:plan", tmp_path / "plan.json"),
    )
    kinds = []

    def executor(_repo, _profile, _operation, unit, *_args):
        kinds.append(unit["kind"])
        if unit["kind"] == "consolidation-link-page":
            if missing_links:
                return {"links": []}
            candidates = {
                row["candidate_id"]
                for key in ("left_candidates", "right_candidates")
                for row in unit[key]
            }
            left, right = sorted(candidates)
            return {"links": [{
                "left_candidate_id": left,
                "right_candidate_id": right,
                "decision": "merge",
                "rationale": "same",
            }]}
        if unit["kind"] == "consolidation-reduce-pair":
            return {"group": {
                "semantic_key": "orders", "title": "Orders",
                "business_meaning": "Orders", "scope": "Documents",
                "confidence": "high", "rationale": "same",
                "split_source_mrq_id": "",
            }}
        return {"groups": [{
            "semantic_key": unit["id"], "title": "Orders",
            "stable_diff_ids": ["DIF-1"], "supporting_diff_ids": [],
            "component_keys": [], "source_mrq_ids": [], "evidence": [],
            "business_meaning": "Orders", "scope": "Documents",
            "confidence": "high", "rationale": "same",
            "split_source_mrq_id": "",
        }]}

    profile = {
        "input_context_tokens": 8192,
        "context_estimator_version": "utf8-v1",
    }
    try:
        result = compile_consolidate_graph(
            repo=repo, saver=store.saver, executor=executor, profile=profile,
            profiles_by_role={"grouper": profile, "coordinator": profile},
            supplement="", timeout_seconds=60, cancelled=lambda: False,
            bindings_check=lambda: True, plan_root=tmp_path,
        ).invoke(
            build_discover_state(
                {**_bindings("consolidate"), "job_id": "consolidate-mrq"},
                "run", "thread",
            ),
            config={"configurable": {"thread_id": "thread"}},
        )
    finally:
        store.close()
    assert result["blocker"]["code"] == (
        "consolidation.plan_storage" if missing_links
        else "approval.consolidation"
    )
    assert kinds.count("consolidation-link-page") == 1
    assert kinds.count("consolidation-reduce-pair") == (0 if missing_links else 1)
    if not missing_links:
        assert captured["groups"][0]["stable_diff_ids"] == ["DIF-1"]


def test_classify_stops_before_next_window_when_source_fingerprint_changes(tmp_path: Path) -> None:
    diffs = [_customer_diff(f"DIF-{index:04X}", f"Catalogs/Invoices/{index}.bsl") for index in range(17)]
    mrqs = [
        _mrq(
            f"MRQ {index}",
            f"invoice-{index}",
            evidence_path=f"Catalogs/Invoices/{index}.bsl",
            stable_diff_id=diff["stable_diff_id"],
        )
        for index, diff in enumerate(diffs)
    ]
    repo = _bootstrap_repo(tmp_path, diffs, with_mrq=mrqs)
    store = DispatcherStore(repo, base=tmp_path / "state")
    store.open()
    calls: list[str] = []

    def executor(_repo, _profile, _operation, work_unit, _supplement, _timeout, _cancelled):
        calls.append(work_unit["id"])
        root = repo / "analysis/migration-requirements/generations"
        active_root = root / json.loads((repo / "research/active-generation.json").read_text(encoding="utf-8"))["canonical_generation_id"]
        rows = [json.loads(line) for line in (active_root / "mrq.jsonl").read_text(encoding="utf-8").splitlines()]
        rows[0]["title"] = "changed during classification"
        (active_root / "mrq.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return {
            "groups": [{
                "mrq_ids": [item["mrq_id"] for item in work_unit["mrqs"]],
                "basis": "shared component",
                "linkage_proven": True,
            }],
        }

    try:
        state = compile_classify_graph(
            repo=repo,
            saver=store.saver,
            executor=executor,
            profile={},
            supplement="",
            timeout_seconds=60,
            cancelled=lambda: False,
            bindings_check=lambda: True,
        ).invoke(
            build_classify_state({**_bindings("classify:source"), "job_id": "classify-mrq"}, "run-1", "thread-1"),
            config={"configurable": {"thread_id": "thread-1"}},
        )
        assert calls == ["window:0"]
        assert state["status"] == "stale"
        assert state["batch_generation"] is None
        assert not (repo / "analysis/migration-requirements/batch-generations").exists()
    finally:
        store.close()


@pytest.mark.parametrize("corrupt", [False, True])
def test_mrq_decision_only_publish_preserves_only_valid_batch_binding(tmp_path: Path, corrupt: bool) -> None:
    from one_c_autoresearch.mrq import FILES, active, decide, publish as publish_mrq
    from one_c_autoresearch.mrq_batches import MRQBatch, batch_id, publish as publish_batches, source_mrq_payload

    diff = _customer_diff("DIF-AAAA")
    mrq = _mrq("Invoice", "invoice", stable_diff_id=diff["stable_diff_id"])
    repo = _bootstrap_repo(tmp_path, [diff], with_mrq=[mrq])
    generation, fingerprint, _records = source_mrq_payload(repo)
    batch = MRQBatch(batch_id([mrq["mrq_id"]]), (mrq["mrq_id"],), "shared component", ("target_cf:configuration",))
    binding = publish_batches(repo, [batch], fingerprint)
    if corrupt:
        (repo / "analysis/migration-requirements/batch-generations" / binding["generation_id"] / "batches.jsonl").write_text("", encoding="utf-8")

    current = active(repo)
    rows = {name: current[name] for name in FILES}
    decide(
        rows,
        mrq["mrq_id"],
        "adopt_vendor",
        [{"path": "Catalogs/Invoices/a.bsl", "fingerprint": "sha256:" + "a" * 64, "stable_diff_id": "DIF-AAAA"}],
        [{"customer_diff_id": "DIF-AAAA", "target_diff_ids": "", "coverage_status": "covered", "evidence_ref": ""}],
        "none",
        "vendor",
        "rationale",
        ["scenario"],
        "low",
        [],
    )
    source_pointer = json.loads((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    pointer = publish_mrq(repo, rows, "a" * 64, "b" * 64, source_pointer, generation)
    assert pointer.get("batch_generation") == (None if corrupt else binding)


def test_completed_classify_retry_uses_new_identity_without_agent_or_new_generation(tmp_path: Path) -> None:
    diff = _customer_diff("DIF-AAAA")
    mrq = _mrq("Invoice", "invoice", stable_diff_id=diff["stable_diff_id"])
    repo = _bootstrap_repo(tmp_path, [diff], with_mrq=[mrq])
    store = DispatcherStore(repo, base=tmp_path / "state")
    store.open()
    bindings = {**_bindings("classify:source"), "job_id": "classify-mrq"}

    def executor(_repo, _profile, _operation, work_unit, _supplement, _timeout, _cancelled):
        return {
            "groups": [{
                "mrq_ids": [item["mrq_id"] for item in work_unit["mrqs"]],
                "basis": "shared component",
                "linkage_proven": True,
            }],
        }

    arguments = dict(
        repo=repo,
        saver=store.saver,
        profile={},
        supplement="",
        timeout_seconds=60,
        cancelled=lambda: False,
        bindings_check=lambda: True,
    )
    try:
        first = compile_classify_graph(**arguments, executor=executor).invoke(
            build_classify_state(bindings, "run-1", "thread-1"),
            config={"configurable": {"thread_id": "thread-1"}},
        )
        directories = sorted((repo / "analysis/migration-requirements/batch-generations").iterdir())

        pointer = json.loads((repo / "research/active-generation.json").read_text(encoding="utf-8"))
        dispatcher_bindings = DispatcherBindings(
            project_id="proj-1",
            job_id="classify-mrq",
            work_unit_id="classify:source",
            source_generation_id="a" * 64,
            diff_generation_id="b" * 64,
            canonical_generation_id=pointer["canonical_generation_id"],
            workflow_fingerprint="sha256:wf",
            agent_profile_fingerprint="sha256:profile",
            instruction_supplement="",
        )
        event_store = EventStore(tmp_path / "events", "proj-1")
        coordinator = DispatcherCoordinator(repo, "proj-1", store, event_store, actor="test")
        coordinator._validate_new_run_snapshot = lambda *_args: None

        def snapshot(run_id: str, predecessor_run_id: str = "") -> dict[str, Any]:
            return {
                "schema_version": "1",
                "run_id": run_id,
                "operation": "mrq.classify-batches",
                "operation_version": "1",
                "workflow_fingerprint": "sha256:wf",
                "timeout_seconds": 60,
                "agent_phases": [{
                    "phase_id": "classify-batches",
                    "max_concurrency": 1,
                    "roles": [{"role_id": "classifier", "count": 1, "agent_profile": "local", "instruction_supplement": ""}],
                }],
                "profiles": {"local": {"instructions_version": "1"}},
                "instructions": {},
                "environment": {},
                "codex_version": "test",
                "application_version": "one-c-autoresearch/0.2",
                "subject_bindings": {},
                "policy_source": "current-policy",
                "predecessor_run_id": predecessor_run_id,
                "work_unit": {"id": "classify:source", "allowed_paths": []},
                "context_manifest": {"paths": []},
            }

        predecessor_run_id = str(uuid.uuid4())
        predecessor = coordinator.start(
            "classify-mrq",
            dispatcher_bindings,
            run_id=predecessor_run_id,
            execution_snapshot=snapshot(predecessor_run_id),
        )
        coordinator.finish("classify-mrq", "completed", {"phase": "completed"})
        retry_run_id = str(uuid.uuid4())
        retry = coordinator.retry(
            "classify-mrq",
            dispatcher_bindings,
            run_id=retry_run_id,
            execution_snapshot=snapshot(retry_run_id, predecessor_run_id),
        )
        assert retry.status == "running"
        assert retry.run_id == retry_run_id != predecessor.run_id
        assert retry.thread_id != predecessor.thread_id
        effective = DispatcherBindings(**{
            key: retry.summary["bindings"].get(key, "")
            for key in DispatcherBindings.__dataclass_fields__
        })
        with (
            patch("one_c_autoresearch.agents.validate_execution_snapshot", lambda *_args: None),
            patch("one_c_autoresearch.agents.execute", side_effect=AssertionError("completed classify retry called the agent")),
        ):
            coordinator._run_graph(retry, effective, {}, {}, {}, 60)
        retry_snapshot = event_store.run_snapshot(retry_run_id)
        assert retry_snapshot["status"] == "completed", json.dumps(retry_snapshot, ensure_ascii=False)
        assert sorted((repo / "analysis/migration-requirements/batch-generations").iterdir()) == directories
        assert first["batch_generation"] == json.loads((repo / "research/active-generation.json").read_text(encoding="utf-8"))["batch_generation"]
    finally:
        store.close()


# -- этапы decide-mrq -----------------------------------------------------


def test_classifier_response_is_exact_and_weak_groups_split() -> None:
    window = [
        {"mrq_id": "MRQ-0000000000000001", "source_component_ids": ["c1"]},
        {"mrq_id": "MRQ-0000000000000002", "source_component_ids": ["c1"]},
    ]
    split = _validated_window_batches(window, {"groups": [{
        "mrq_ids": [item["mrq_id"] for item in window],
        "basis": "same area",
        "linkage_proven": False,
    }]})
    assert [len(batch.mrq_ids) for batch in split] == [1, 1]
    invalid = [
        {"groups": [], "extra": True},
        {"groups": [{"mrq_ids": [window[0]["mrq_id"]], "basis": "x", "linkage_proven": True, "extra": True}]},
        {"groups": [{"mrq_ids": [window[0]["mrq_id"]], "basis": "x", "linkage_proven": True}]},
        {"groups": [{"mrq_ids": [window[0]["mrq_id"], window[0]["mrq_id"]], "basis": "x", "linkage_proven": True}]},
        {"groups": [{"mrq_ids": ["MRQ-FFFFFFFFFFFFFFFF"], "basis": "x", "linkage_proven": True}]},
    ]
    for response in invalid:
        with pytest.raises(ValueError):
            _validated_window_batches(window, response)


def test_decide_select_mrq_picks_first_pending(tmp_path: Path) -> None:
    """Approved MRQ не выбирается; pending — выбирается.

    Готовое состояние с approved+pending строим через ``mrq.propose/decide/review``
    и публикуем, чтобы все approval-события были корректными.
    """

    from one_c_autoresearch.mrq import FILES, decide, propose, publish, review, validate_graph
    diffs = [_customer_diff("DIF-AAAA"), _customer_diff("DIF-BBBB", "Catalogs/Stock/b.bsl")]
    repo = _bootstrap_repo(tmp_path, diffs)  # без MRQ пока
    source_id = "a" * 64
    diff_id = "b" * 64
    rows_state = {name: [] for name in FILES}
    approved_id = propose(rows_state, "approved.semantic", "Approved", source_id, diff_id, ["DIF-AAAA"], [], [{"path": "Catalogs/Invoices/a.bsl", "fingerprint": "sha256:" + "a" * 64, "stable_diff_id": "DIF-AAAA"}], "M", "S", "high", "R")
    decide(rows_state, approved_id, "adopt_vendor", [{"path": "Catalogs/Invoices/a.bsl", "fingerprint": "sha256:" + "a" * 64, "stable_diff_id": "DIF-AAAA"}], [{"customer_diff_id": "DIF-AAAA", "target_diff_ids": "", "coverage_status": "covered", "evidence_ref": ""}], "none", "vendor", "rationale", ["scenario"], "low", [])
    review(rows_state, approved_id, "approve", "reviewer", "accepted", [], source_id, diff_id, "2026-01-01T00:00:00Z", None)
    pending_id = propose(rows_state, "pending.semantic", "Pending", source_id, diff_id, ["DIF-BBBB"], [], [{"path": "Catalogs/Stock/b.bsl", "fingerprint": "sha256:" + "a" * 64, "stable_diff_id": "DIF-BBBB"}], "M2", "S2", "high", "R2")
    valid_customer = {row["stable_diff_id"]: row for row in diffs}
    validate_graph(rows_state, source_id, diff_id, valid_customer)
    source_pointer = {"schema_version": "1", "generation_id": source_id, "acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "normalizer_version": "3", "representation_schema": "xml-hierarchical"}
    publish(repo, rows_state, source_id, diff_id, source_pointer, None)
    state = build_decide_state({**_bindings(), "job_id": "decide-mrq", "work_unit_id": pending_id}, "run-1", "thread-1")
    state = decide_select_mrq(state, repo=repo)
    assert state["mrq_id"] == pending_id


def test_decide_research_one_blocks_on_semantic_key_change(tmp_path: Path) -> None:
    mrq = _mrq("MRQ-BBB", "original", evidence_path="Catalogs/x.bsl", stable_diff_id="DIF-AAAA")
    repo = _bootstrap_repo(tmp_path, [_customer_diff("DIF-AAAA", "Catalogs/x.bsl")], with_mrq=[mrq])
    state = build_decide_state({**_bindings(), "job_id": "decide-mrq", "work_unit_id": mrq["mrq_id"]}, "run-1", "thread-1")
    state = decide_select_mrq(state, repo=repo)

    def executor(*_args, **_kwargs):
        return {"mrq_id": mrq["mrq_id"], "decision": "adapt", "target_evidence": [], "target_coverage": [], "residual_gap": "gap", "target_solution": "adapt code", "rationale": "R", "acceptance_criteria": ["c"], "risk": "low", "open_questions": [], "semantic_key": "tampered"}  # попытка изменить исходную часть

    state = decide_research_one(state, mrq["mrq_id"], executor=executor, repo=repo, profile={}, supplement="", timeout_seconds=60, cancelled=lambda: False, bindings_check=lambda: True)
    assert state["status"] == "failed"
    assert state["blocker"]["code"] == "dispatcher.mrq.source_immutable"


def test_decide_apply_calls_existing_mrq_decide(tmp_path: Path) -> None:
    state = build_decide_state({**_bindings(), "job_id": "decide-mrq"}, "run-1", "thread-1")
    proposal = {"mrq_id": "MRQ-AAA", "decision": "adapt", "target_evidence": [], "target_coverage": [], "residual_gap": "gap", "target_solution": "adapt", "rationale": "R", "acceptance_criteria": ["c"], "risk": "low", "open_questions": []}
    state = {**state, "decision_proposal": proposal}
    captured: list[tuple[str, dict[str, Any]]] = []

    def apply(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        captured.append((operation, payload))
        return {}

    state = decide_apply(state, apply=apply)
    assert captured[0][0] == "mrq.decide"
    assert captured[0][1]["decision"] == "adapt"
    assert state["approved_decision"]["decision"] == "adapt"


# -- производная карточка разрыва ----------------------------------------


def test_derived_gap_card_only_for_adapt() -> None:
    for decision in ("adopt_vendor", "retain_custom", "out_of_scope"):
        mrq = {"mrq_id": "MRQ-X", "migration_decision": {"decision": decision}}
        assert derived_gap_card(mrq) is None
    adapt_mrq = {"mrq_id": "MRQ-X", "migration_decision": {"decision": "adapt", "residual_gap": "missing feature", "target_solution": "custom module", "acceptance_criteria": ["scenario"], "risk": "medium"}}
    card = derived_gap_card(adapt_mrq)
    assert card is not None
    assert card["kind"] == "derived_gap"
    assert card["mrq_id"] == "MRQ-X"
    assert card["residual_gap"] == "missing feature"
    # отдельной канонической сущности GAP-* нет
    assert "gap_id" not in card
