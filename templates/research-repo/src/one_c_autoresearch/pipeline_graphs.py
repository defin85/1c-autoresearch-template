"""Два фиксированных графа LangGraph вокруг существующего исполнителя.

Графы работают строго внутри существующих заданий ``discover-mrq`` и
``decide-mrq`` и не заменяют репозиторный DAG. Любой канонический эффект
проходит через прикладной сервис с ожидаемым отпечатком и идемпотентностью.

Лимиты ``design.md`` §2 (4/32/64) — константы кода, не параметры
``research/workflow.toml``.
"""

from __future__ import annotations

import csv
import json
from concurrent.futures import CancelledError, FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Callable, Iterable, Literal, TypedDict

from .contracts import canonical_json, sha256
from .mrq_batches import MAX_BATCH_SIZE, MRQBatch, batch_id, load_active, publish as publish_batches, source_mrq_payload, stable_windows


# -- зафиксированные лимиты (design.md §2) ---------------------------------

MAX_DIF_WINDOW = 32
MAX_GROUP_CANDIDATES = 64


# -- типизированные состояния графов --------------------------------------


class AnalyzeResult(TypedDict, total=False):
    stable_diff_id: str
    kind: Literal["meaning", "noise"]
    proposal: dict[str, Any]
    evidence: list[dict[str, Any]]
    rationale: str


class GroupProposal(TypedDict, total=False):
    anchor_diff_id: str
    semantic_key: str
    title: str
    stable_diff_ids: list[str]
    supporting_diff_ids: list[str]
    evidence: list[dict[str, Any]]
    business_meaning: str
    scope: str
    confidence: str
    rationale: str


class DiscoverState(TypedDict, total=False):
    project_id: str
    job_id: str
    work_unit_id: str
    source_generation_id: str
    diff_generation_id: str
    canonical_generation_id: str
    workflow_fingerprint: str
    agent_profile_fingerprint: str
    instruction_supplement: str
    execution_snapshot_fingerprint: str
    required_reuse_origin: dict[str, str]
    thread_id: str
    run_id: str
    window: list[str]  # стабильный порядок DIF в окне
    analyzed: dict[str, AnalyzeResult]
    analyze_results: dict[str, dict[str, Any]]
    meanings: list[str]  # упорядоченные смысловые DIF
    noise: list[str]
    noise_review_ids: list[str]
    approved_noise_ids: list[str]
    approved_noise: list[dict[str, Any]]
    preliminary_groups: dict[str, GroupProposal]  # anchor_diff_id -> proposal
    form_mrq_results: dict[str, dict[str, Any]]
    barrier_open: bool
    batch_proposals: list[GroupProposal]
    approved_batch: dict[str, Any] | None
    published_mrq_ids: list[str]
    target_batches: list[dict[str, Any]]
    status: str
    blocker: dict[str, Any] | None


class DecideState(TypedDict, total=False):
    project_id: str
    job_id: str
    work_unit_id: str
    mrq_id: str
    source_generation_id: str
    diff_generation_id: str
    canonical_generation_id: str
    workflow_fingerprint: str
    agent_profile_fingerprint: str
    instruction_supplement: str
    execution_snapshot_fingerprint: str
    required_reuse_origin: dict[str, str]
    thread_id: str
    run_id: str
    batch_id: str | None
    batch_mrqs: list[str]
    findings: dict[str, list[dict[str, Any]]]
    research_results: dict[str, dict[str, Any]]
    decision_proposal: dict[str, Any] | None
    approved_decision: dict[str, Any] | None
    status: str
    blocker: dict[str, Any] | None


class ClassifyState(TypedDict, total=False):
    project_id: str
    job_id: str
    work_unit_id: str
    source_generation_id: str
    diff_generation_id: str
    canonical_generation_id: str
    workflow_fingerprint: str
    agent_profile_fingerprint: str
    instruction_supplement: str
    execution_snapshot_fingerprint: str
    thread_id: str
    run_id: str
    source_mrq_fingerprint: str
    batches: list[dict[str, Any]]
    batch_generation: dict[str, Any] | None
    published: bool
    status: str
    blocker: dict[str, Any] | None


# -- helpers --------------------------------------------------------------


def _read_diff_inventory(repo: Path) -> list[dict[str, str]]:
    pointer = json.loads((repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
    path = repo / "analysis/indexes/generations" / pointer["generation_id"] / "diff-inventory.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        return [row for row in csv.DictReader(stream) if row.get("before_role") == "vendor_baseline" and row.get("after_role") == "target_cf"]


def _owned_diff_ids(repo: Path) -> set[str]:
    """Возвращает DIF с первичной дислокацией (MRQ или утверждённым шумом)."""

    from .mrq import active
    state = active(repo)
    active_ids = {item["mrq_id"] for item in state["mrq.jsonl"] if item.get("state") != "superseded"}
    owned: set[str] = set()
    for relation in state["dispositions.jsonl"]:
        if relation.get("primary") and (not relation.get("mrq_id") or relation.get("mrq_id") in active_ids):
            owned.add(relation["stable_diff_id"])
    return owned


def select_dif_window(repo: Path, *, limit: int = MAX_DIF_WINDOW) -> list[str]:
    """Детерминированно выбирает непокрытые клиентские DIF в стабильном порядке.

    Сохраняемая предметная очередь не создаётся: каждый вызов выводит окно из
    текущего канонического состояния.
    """

    customer = sorted((row["stable_diff_id"] for row in _read_diff_inventory(repo)), key=lambda identifier: identifier)
    owned = _owned_diff_ids(repo)
    return [identifier for identifier in customer if identifier not in owned][:limit]


def _ensure_active_generation(repo: Path) -> dict[str, Any]:
    try:
        return json.loads((repo / "research/active-generation.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid research/active-generation.json pointer") from exc


def _stable_group_candidates(anchor: str, meanings: list[str], inventory: dict[str, dict[str, str]], *, limit: int = MAX_GROUP_CANDIDATES) -> list[str]:
    """Формирует область кандидатов группы по проверяемым связям исходников.

    Кандидаты — DIF с тем же первым сегментом пути, что и у опорного DIF. Этого
    достаточно для конвейерной группировки; агент уточняет состав далее.
    """

    fact = inventory.get(anchor, {})
    anchor_root = str(fact.get("path", "")).split("/")[0]
    if not anchor_root:
        return [anchor]
    candidates = [identifier for identifier in meanings if str(inventory.get(identifier, {}).get("path", "")).split("/")[0] == anchor_root]
    if anchor not in candidates:
        candidates = [anchor, *candidates]
    # стабильный порядок + предел 64
    return sorted(set(candidates), key=lambda identifier: identifier)[:limit]


def _bounded_map(
    items: list[str],
    *,
    max_concurrency: int,
    slot_count: int,
    function: Callable[[str], Any],
    cancelled: Callable[[], bool],
    bindings_check: Callable[[], bool],
    cancel_active: Callable[[], None] = lambda: None,
) -> tuple[dict[str, Any], dict[str, str], bool]:
    """Выполняет только ограниченное окно и прекращает выдачу после первой ошибки."""

    limit = min(max_concurrency, slot_count, len(items))
    if limit <= 0:
        return {}, {}, cancelled() or not bindings_check()
    iterator = iter(items)
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    stopped = False
    pool = ThreadPoolExecutor(max_workers=limit)
    active: dict[Any, str] = {}
    try:
        while len(active) < limit and not cancelled() and bindings_check():
            try:
                item = next(iterator)
            except StopIteration:
                break
            active[pool.submit(function, item)] = item
        while active:
            completed, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in completed:
                item = active.pop(future)
                try:
                    results[item] = future.result()
                except Exception as exc:
                    errors[item] = f"{type(exc).__name__}: {exc}"
            stopped = stopped or cancelled() or not bindings_check()
            if errors or stopped:
                cancel_active()
                for future in active:
                    future.cancel()
                while active:
                    completed, _ = wait(active, return_when=FIRST_COMPLETED)
                    for future in completed:
                        item = active.pop(future)
                        try:
                            results[item] = future.result()
                        except CancelledError:
                            pass
                        except Exception as exc:
                            errors[item] = f"{type(exc).__name__}: {exc}"
                break
            while len(active) < limit and not cancelled() and bindings_check():
                try:
                    item = next(iterator)
                except StopIteration:
                    break
                active[pool.submit(function, item)] = item
            stopped = stopped or cancelled() or not bindings_check()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return results, errors, stopped


def _result_envelope(state: dict[str, Any], phase_id: str, role_id: str, work_unit: dict[str, Any], result: Any, instruction_version: str = "") -> dict[str, Any]:
    compatibility = {
        "operation": "mrq.decide-next" if phase_id == "research-target" else "mrq.discover-next",
        "phase_id": phase_id,
        "role_id": role_id,
        "work_unit": work_unit,
        "allowed_paths": sorted(work_unit.get("allowed_paths", [])),
        "base_instruction_version": instruction_version,
        "response_schema": f"{phase_id}-{role_id}/v1",
        "validator_version": "agent-phase-result/v1",
    }
    return {
        "source_run_id": state["run_id"],
        "source_execution_snapshot_fingerprint": state.get("execution_snapshot_fingerprint", ""),
        "compatibility_fingerprint": "sha256:" + sha256(canonical_json(compatibility)),
        "result_fingerprint": "sha256:" + sha256(canonical_json(result)),
        "result": result,
    }


def _compatible_cached(
    envelope: dict[str, Any] | None,
    expected: dict[str, Any],
    required_origin: dict[str, str] | None = None,
) -> Any | None:
    if not isinstance(envelope, dict) or envelope.get("compatibility_fingerprint") != expected["compatibility_fingerprint"]:
        return None
    if required_origin and (
        envelope.get("source_run_id") != required_origin.get("source_run_id")
        or envelope.get("source_execution_snapshot_fingerprint")
        != required_origin.get("source_execution_snapshot_fingerprint")
    ):
        return None
    return envelope.get("result")


# -- этапы discover-mrq ---------------------------------------------------


def _analyze_work_unit(repo: Path, stable_diff_id: str) -> dict[str, Any]:
    from .workflow import semantic_diff_context
    from .agents import build_context_manifest
    fact = next((row for row in _read_diff_inventory(repo) if row["stable_diff_id"] == stable_diff_id), None)
    unit = {"id": stable_diff_id, "kind": "uncovered-diff", **semantic_diff_context(repo, stable_diff_id, fact)}
    return {**unit, "allowed_path_fingerprints": build_context_manifest(repo, unit)["paths"]}


def _group_work_unit(state: DiscoverState, anchor: str, repo: Path) -> dict[str, Any]:
    meanings = list(state.get("meanings", []))
    inventory = {row["stable_diff_id"]: row for row in _read_diff_inventory(repo)}
    candidates = _stable_group_candidates(anchor, meanings, inventory)
    from .workflow import semantic_diff_context
    semantic = [semantic_diff_context(repo, identifier, inventory[identifier]) for identifier in candidates if inventory.get(identifier, {}).get("object_kind") == "extension_intervention"]
    unit = {
        "id": anchor,
        "kind": "preliminary-group",
        "candidate_diff_ids": candidates,
        "semantic_extension_context": semantic,
        "allowed_paths": sorted(
            {str(inventory[identifier]["path"]) for identifier in candidates if inventory.get(identifier, {}).get("path")}
            | {path for context in semantic for path in context["allowed_paths"]}
        ),
    }
    from .agents import build_context_manifest
    return {**unit, "allowed_path_fingerprints": build_context_manifest(repo, unit)["paths"]}


def discover_select_window(state: DiscoverState, *, repo: Path) -> DiscoverState:
    """Этап 1: детерминированный выбор окна непокрытых DIF."""

    owned = _owned_diff_ids(repo)
    analyzed = set(state.get("analyzed", {}))
    window = [
        row["stable_diff_id"]
        for row in sorted(_read_diff_inventory(repo), key=lambda item: item["stable_diff_id"])
        if row["stable_diff_id"] not in owned | analyzed
    ][:MAX_DIF_WINDOW]
    return {**state, "window": window, "status": "running"}


def discover_analyze_one(state: DiscoverState, stable_diff_id: str, *, executor: Callable[..., dict[str, Any]], repo: Path, profile: dict[str, Any], supplement: str, timeout_seconds: int, cancelled: Callable[[], bool], bindings_check: Callable[[], bool]) -> DiscoverState:
    """Этап 2: один вызов агента для DIF. Возвращает обновлённое состояние.

    Параллелизм ограничивается координатором/``AddDynamicEdges``; эта функция
    атомарна для одного DIF и безопасно вызывается из параллельных ветвей при
    условии внешней синхронизации записи (через ``DispatcherStore``).
    """

    if not bindings_check():
        return {**state, "status": "stale", "blocker": {"code": "dispatcher.bindings.stale", "message": "active generations changed during analysis", "action": "mrq.discover-next"}}
    work_unit = _analyze_work_unit(repo, stable_diff_id)
    payload = executor(repo, profile, "mrq.discover-next", work_unit, supplement, timeout_seconds, cancelled)
    result: AnalyzeResult = {"stable_diff_id": stable_diff_id, "kind": "meaning" if payload.get("semantic_key") else "noise", "proposal": payload, "evidence": payload.get("evidence", []), "rationale": str(payload.get("rationale", ""))}
    analyzed = dict(state.get("analyzed", {}))
    analyzed[stable_diff_id] = result
    meanings = list(state.get("meanings", []))
    noise = list(state.get("noise", []))
    if stable_diff_id not in meanings and stable_diff_id not in noise:
        if result["kind"] == "meaning":
            meanings.append(stable_diff_id)
        else:
            noise.append(stable_diff_id)
    meanings.sort()
    noise.sort()
    return {**state, "analyzed": analyzed, "meanings": meanings, "noise": noise}


def _path_for(repo: Path, stable_diff_id: str) -> str:
    for row in _read_diff_inventory(repo):
        if row["stable_diff_id"] == stable_diff_id:
            return str(row.get("path", ""))
    return ""


def discover_preliminary_group(state: DiscoverState, anchor: str, *, executor: Callable[..., dict[str, Any]], repo: Path, profile: dict[str, Any], supplement: str, timeout_seconds: int, cancelled: Callable[[], bool]) -> DiscoverState:
    """Этап 3: формирование/обновление предварительной группы для опорного DIF.

    Группы операционны: не создают владения и могут быть вытеснены новым полным
    предложением (поздний DIF).
    """

    work_unit = _group_work_unit(state, anchor, repo)
    proposal_payload = executor(repo, profile, "mrq.discover-next", work_unit, supplement, timeout_seconds, cancelled)
    proposal: GroupProposal = {
        "anchor_diff_id": anchor,
        "semantic_key": str(proposal_payload.get("semantic_key", "")),
        "title": str(proposal_payload.get("title", proposal_payload.get("semantic_key", ""))),
        "stable_diff_ids": list(proposal_payload.get("stable_diff_ids", [anchor])),
        "supporting_diff_ids": list(proposal_payload.get("supporting_diff_ids", [])),
        "evidence": list(proposal_payload.get("evidence", [])),
        "business_meaning": str(proposal_payload.get("business_meaning", "")),
        "scope": str(proposal_payload.get("scope", "")),
        "confidence": str(proposal_payload.get("confidence", "medium")),
        "rationale": str(proposal_payload.get("rationale", "")),
    }
    groups = dict(state.get("preliminary_groups", {}))
    groups[anchor] = proposal
    return {**state, "preliminary_groups": groups}


def discover_barrier(state: DiscoverState, *, repo: Path) -> DiscoverState:
    """Этап 4: барьер полного покрытия.

    Публикация блокируется, пока:
    * весь клиентский набор DIF классифицирован;
    * весь шум утверждён;
    * смысловые DIF образуют проверенное полное непересекающееся покрытие.
    """

    owned = _owned_diff_ids(repo)
    customer = [
        row["stable_diff_id"]
        for row in _read_diff_inventory(repo)
        if row["stable_diff_id"] not in owned
    ]
    customer_set = set(customer)
    analyzed = state.get("analyzed", {})
    meanings = set(state.get("meanings", []))
    noise = set(state.get("noise", []))
    classified = meanings | noise
    unclassified = customer_set - classified
    if unclassified:
        return {**state, "barrier_open": False, "status": "blocked", "blocker": {"code": "dispatcher.barrier.unclassified", "message": f"{len(unclassified)} customer DIF remain unclassified", "action": "mrq.discover-next"}}
    # все смысловые DIF должны входить в какую-то группу
    groups = state.get("preliminary_groups", {})
    referenced: set[str] = set()
    for proposal in groups.values():
        referenced.update(proposal.get("stable_diff_ids", []))
        referenced.update(proposal.get("supporting_diff_ids", []))
        evidence = proposal.get("evidence", [])
        if not evidence or any(
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not item["path"]
            or not isinstance(item.get("fingerprint"), str)
            or not item["fingerprint"].startswith("sha256:")
            for item in evidence
        ):
            return {**state, "barrier_open": False, "status": "failed", "blocker": {"code": "dispatcher.barrier.evidence", "message": "every group requires path and content fingerprint evidence", "action": "mrq.discover-next"}}
    unknown = referenced - customer_set
    if unknown:
        return {**state, "barrier_open": False, "status": "failed", "blocker": {"code": "dispatcher.barrier.unknown", "message": f"{len(unknown)} unknown DIF in coordinator result", "action": "mrq.discover-next"}}
    uncovered_meanings = meanings - {identifier for proposal in groups.values() for identifier in proposal.get("stable_diff_ids", [])}
    if uncovered_meanings:
        return {**state, "barrier_open": False, "status": "blocked", "blocker": {"code": "dispatcher.barrier.ungrouped", "message": f"{len(uncovered_meanings)} meaning DIF without group", "action": "mrq.discover-next"}}
    # полное непересекающееся покрытие: каждый смысловой DIF ровно в одной группе (primary)
    primary_counts: dict[str, int] = {}
    for proposal in groups.values():
        for identifier in proposal.get("stable_diff_ids", []):
            primary_counts[identifier] = primary_counts.get(identifier, 0) + 1
    overlaps = {identifier for identifier, count in primary_counts.items() if count > 1}
    if overlaps:
        return {**state, "barrier_open": False, "status": "blocked", "blocker": {"code": "dispatcher.barrier.overlap", "message": f"{len(overlaps)} DIF overlap across groups", "action": "mrq.discover-next"}}
    # пересечение шума и primary
    noise_primary_clash = noise & set(primary_counts)
    if noise_primary_clash:
        return {**state, "barrier_open": False, "status": "blocked", "blocker": {"code": "dispatcher.barrier.noise_clash", "message": "noise DIF also primary in a group", "action": "mrq.discover-next"}}
    if set(primary_counts) != meanings:
        return {**state, "barrier_open": False, "status": "failed", "blocker": {"code": "dispatcher.barrier.coverage", "message": "coordinator primary coverage differs from meaning DIF", "action": "mrq.discover-next"}}
    return {**state, "barrier_open": True, "status": "running", "blocker": None}


def discover_review_noise(
    state: DiscoverState,
    *,
    repo: Path,
    approved_noise_ids: Iterable[str] = (),
) -> DiscoverState:
    """Останавливает поток до явного утверждения найденного шума."""

    approved = set(approved_noise_ids)
    pending = sorted(set(state.get("noise", [])) - _owned_diff_ids(repo) - approved)
    if pending:
        return {
            **state,
            "noise_review_ids": pending,
            "status": "blocked",
            "blocker": {
                "code": "approval.noise",
                "message": f"{len(pending)} noise DIF require explicit local-user approval before coordination",
                "action": "mrq.approve-noise",
            },
        }
    return {
        **state,
        "noise_review_ids": [],
        "approved_noise_ids": sorted(approved),
        "status": "running",
        "blocker": None,
    }


def discover_batch_from_groups(
    state: DiscoverState,
    approved_noise: list[dict[str, Any]] | None = None,
) -> DiscoverState:
    """Этап 5: сборка одобряемого пакета групп из предварительных."""

    groups = state.get("preliminary_groups", {})
    batch_proposals = [groups[anchor] for anchor in sorted(groups)]
    reviewed = approved_noise or []
    return {
        **state,
        "batch_proposals": batch_proposals,
        "approved_noise_ids": [item["stable_diff_id"] for item in reviewed],
        "approved_noise": reviewed,
    }


def discover_publish_batch(state: DiscoverState, *, repo: Path, apply: Callable[[str, dict[str, Any]], dict[str, Any]], approved_noise_payload: list[dict[str, Any]]) -> DiscoverState:
    """Этап 6: применение одобренного пакета через ``mrq.publish-source-batch``.

    Вызывается только после явного одобрения локальным пользователем.
    Атомарность гарантируется прикладным сервисом под репозиторной блокировкой.
    """

    groups = state.get("batch_proposals", [])
    payload = {
        "approved_noise": approved_noise_payload,
        "group_proposals": [
            {
                "semantic_key": group["semantic_key"],
                "title": group.get("title", group["semantic_key"]),
                "stable_diff_ids": list(group["stable_diff_ids"]),
                "supporting_diff_ids": list(group.get("supporting_diff_ids", [])),
                "evidence": list(group.get("evidence", [])),
                "business_meaning": group["business_meaning"],
                "scope": group["scope"],
                "confidence": group["confidence"],
                "rationale": group["rationale"],
            }
            for group in groups
        ],
    }
    result = apply("mrq.publish-source-batch", payload)
    return {**state, "published_mrq_ids": result.get("mrq_ids", []), "status": "running"}


def build_classify_state(bindings: dict[str, Any], run_id: str, thread_id: str) -> ClassifyState:
    return ClassifyState(
        project_id=bindings["project_id"],
        job_id=bindings["job_id"],
        work_unit_id=bindings["work_unit_id"],
        source_generation_id=bindings["source_generation_id"],
        diff_generation_id=bindings["diff_generation_id"],
        canonical_generation_id=bindings["canonical_generation_id"],
        workflow_fingerprint=bindings["workflow_fingerprint"],
        agent_profile_fingerprint=bindings["agent_profile_fingerprint"],
        instruction_supplement=bindings["instruction_supplement"],
        execution_snapshot_fingerprint=bindings.get("execution_snapshot_fingerprint", ""),
        thread_id=thread_id,
        run_id=run_id,
        source_mrq_fingerprint="",
        batches=[],
        batch_generation=None,
        published=False,
        status="running",
        blocker=None,
    )


def _validated_window_batches(window: list[dict[str, Any]], response: dict[str, Any]) -> list[MRQBatch]:
    if set(response) != {"groups"}:
        raise ValueError("classifier response must contain only groups")
    groups = response.get("groups")
    if not isinstance(groups, list):
        raise ValueError("classifier response groups must be an array")
    expected = {record["mrq_id"] for record in window}
    covered: list[str] = []
    batches: list[MRQBatch] = []
    by_id = {record["mrq_id"]: record for record in window}
    for group in groups:
        if (
            not isinstance(group, dict)
            or set(group) != {"mrq_ids", "basis", "linkage_proven"}
            or not isinstance(group.get("mrq_ids"), list)
            or not isinstance(group.get("basis"), str)
            or not isinstance(group.get("linkage_proven"), bool)
        ):
            raise ValueError("classifier group has invalid structure")
        identifiers = sorted(str(value) for value in group["mrq_ids"])
        if not identifiers or len(identifiers) > MAX_BATCH_SIZE or any(value not in expected for value in identifiers):
            raise ValueError("classifier group references invalid MRQ set")
        covered.extend(identifiers)
        if group.get("linkage_proven") is not True and len(identifiers) > 1:
            for identifier in identifiers:
                record = by_id[identifier]
                batches.append(MRQBatch(batch_id([identifier]), (identifier,), "standalone MRQ without provable linkage", tuple(record["source_component_ids"])))
            continue
        components = tuple(sorted({component for identifier in identifiers for component in by_id[identifier]["source_component_ids"]}))
        basis = str(group.get("basis") or "").strip()
        if not basis:
            raise ValueError("classifier group basis is required")
        batches.append(MRQBatch(batch_id(identifiers), tuple(identifiers), basis, components))
    if len(covered) != len(set(covered)) or set(covered) != expected:
        raise ValueError("classifier response must cover every window MRQ exactly once")
    return sorted(batches, key=lambda batch: min(batch.mrq_ids))


def compile_classify_graph(
    *,
    repo: Path,
    saver: Any,
    executor: Callable[..., dict[str, Any]],
    profile: dict[str, Any],
    supplement: str,
    timeout_seconds: int,
    cancelled: Callable[[], bool],
    bindings_check: Callable[[], bool],
    phase_policy: dict[str, Any] | None = None,
    profiles_by_role: dict[str, dict[str, Any]] | None = None,
    load_result: Callable[[str], dict[str, Any] | None] | None = None,
    save_result: Callable[[str, dict[str, Any]], None] | None = None,
    register_work: Callable[[str, str, list[str]], None] | None = None,
    reuse_work: Callable[[str, str, str], None] | None = None,
):
    """Компилирует последовательную классификацию стабильных окон MRQ."""

    from langgraph.graph import END, START, StateGraph

    def classify_windows(state: ClassifyState) -> ClassifyState:
        if cancelled():
            return {**state, "status": "resumable", "blocker": {"code": "dispatcher.soft_stopped", "message": "dispatcher was stopped", "action": "mrq.classify-batches"}}
        try:
            existing = load_active(repo)
            return {**state, "batches": [batch.canonical_payload() for batch in existing], "published": False, "status": "completed", "blocker": None}
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass
        _generation_id, fingerprint, records = source_mrq_payload(repo)
        windows = stable_windows(records)
        classifier_profile = (profiles_by_role or {}).get("classifier", profile)
        policy = phase_policy or {"max_concurrency": 1, "roles": [{"role_id": "classifier", "count": 1, "instruction_supplement": supplement}]}
        classifier_supplement = next(role.get("instruction_supplement", "") for role in policy["roles"] if role["role_id"] == "classifier")
        if register_work:
            register_work("classify-batches", "classifier", [f"window:{index}" for index in range(len(windows))])
        batches: list[MRQBatch] = []
        for index, window in enumerate(windows):
            if cancelled():
                return {**state, "source_mrq_fingerprint": fingerprint, "batches": [batch.canonical_payload() for batch in batches], "status": "resumable", "blocker": {"code": "dispatcher.soft_stopped", "message": "dispatcher was stopped", "action": "mrq.classify-batches"}}
            if not bindings_check() or source_mrq_payload(repo)[1] != fingerprint:
                return {**state, "source_mrq_fingerprint": fingerprint, "batches": [batch.canonical_payload() for batch in batches], "status": "stale", "blocker": {"code": "dispatcher.bindings.stale", "message": "active generations changed during classification", "action": "mrq.classify-batches"}}
            result_name = f"classify-batches:window:{index}"
            cached = load_result(result_name) if load_result else None
            if cached is not None:
                if cached.get("source_mrq_fingerprint") != fingerprint:
                    raise ValueError("cached classifier result has a stale source MRQ fingerprint")
                batches.extend(_validated_window_batches(window, cached.get("response", {})))
                if reuse_work:
                    reuse_work("classify-batches", "classifier", f"window:{index}")
                continue
            work_unit = {
                "id": f"window:{index}",
                "kind": "mrq-batch-window",
                "source_mrq_fingerprint": fingerprint,
                "mrqs": window,
                "allowed_paths": sorted({evidence["path"] for record in window for evidence in record["source_evidence"]}),
            }
            response = executor(repo, classifier_profile, "mrq.classify-batches", work_unit, classifier_supplement, timeout_seconds, cancelled)
            batches.extend(_validated_window_batches(window, response))
            if save_result:
                save_result(result_name, {"source_mrq_fingerprint": fingerprint, "response": response})
        if source_mrq_payload(repo)[1] != fingerprint:
            return {**state, "source_mrq_fingerprint": fingerprint, "batches": [batch.canonical_payload() for batch in batches], "status": "stale", "blocker": {"code": "dispatcher.bindings.stale", "message": "active generations changed during classification", "action": "mrq.classify-batches"}}
        try:
            binding = publish_batches(repo, batches, fingerprint)
        except RuntimeError as exc:
            if str(exc) != "stale source MRQ fingerprint":
                raise
            return {**state, "source_mrq_fingerprint": fingerprint, "batches": [batch.canonical_payload() for batch in batches], "status": "stale", "blocker": {"code": "dispatcher.bindings.stale", "message": "active generations changed during classification", "action": "mrq.classify-batches"}}
        return {**state, "source_mrq_fingerprint": fingerprint, "batches": [batch.canonical_payload() for batch in batches], "batch_generation": binding, "published": True, "status": "completed", "blocker": None}

    graph = StateGraph(ClassifyState)
    graph.add_node("classify-batches", classify_windows)
    graph.add_edge(START, "classify-batches")
    graph.add_edge("classify-batches", END)
    return graph.compile(checkpointer=saver)


# -- этапы decide-mrq -----------------------------------------------------


def decide_select_mrq(state: DecideState, *, repo: Path) -> DecideState:
    """Выбор следующего MRQ без решения (с привязкой к пакету исследования)."""

    from .mrq import active
    rows = active(repo)["mrq.jsonl"]
    pending = sorted((item for item in rows if item.get("state") not in {"superseded", "approved"} and not item.get("migration_decision", {}).get("decision")), key=lambda item: item["mrq_id"])
    if not pending:
        return {**state, "status": "completed", "blocker": None}
    target = pending[0]
    batch_mrqs = state.get("batch_mrqs", []) or [target["mrq_id"]]
    return {**state, "mrq_id": target["mrq_id"], "batch_mrqs": batch_mrqs, "status": "running"}


def _research_work_unit(state: DecideState, mrq_id: str, repo: Path) -> dict[str, Any] | None:
    from .mrq import active
    rows = active(repo)["mrq.jsonl"]
    target = next((item for item in rows if item["mrq_id"] == mrq_id), None)
    if target is None:
        return None
    batch_ids = state.get("batch_mrqs", [])
    batch_context = [
        {"mrq_id": item["mrq_id"], "semantic_key": item.get("semantic_key"), "title": item.get("title"), "business_meaning": item.get("source_customization", {}).get("business_meaning"), "scope": item.get("source_customization", {}).get("scope")}
        for item in rows
        if item["mrq_id"] in batch_ids
    ]
    unit = {"id": mrq_id, "kind": "migration-decision", "mrq": target, "batch_context": batch_context, "allowed_paths": sorted({evidence["path"] for evidence in target.get("source_customization", {}).get("evidence", []) if evidence.get("path")})}
    from .agents import build_context_manifest
    return {**unit, "allowed_path_fingerprints": build_context_manifest(repo, unit)["paths"]}


def decide_research_one(state: DecideState, mrq_id: str, *, executor: Callable[..., dict[str, Any]], repo: Path, profile: dict[str, Any], supplement: str, timeout_seconds: int, cancelled: Callable[[], bool], bindings_check: Callable[[], bool]) -> DecideState:
    """Один вызов исследования цели. Запрещает менять исходную часть MRQ."""

    if not bindings_check():
        return {**state, "status": "stale", "blocker": {"code": "dispatcher.bindings.stale", "message": "active generations changed during research", "action": "mrq.decide-next"}}
    work_unit = _research_work_unit(state, mrq_id, repo)
    if work_unit is None:
        return {**state, "status": "failed", "blocker": {"code": "dispatcher.mrq.missing", "message": f"MRQ disappeared: {mrq_id}", "action": "mrq.decide-next"}}
    target = work_unit["mrq"]
    payload = executor(repo, profile, "mrq.decide-next", work_unit, supplement, timeout_seconds, cancelled)
    # запрет изменения исходной части MRQ
    if payload.get("semantic_key") and payload["semantic_key"] != target.get("semantic_key"):
        return {**state, "status": "failed", "blocker": {"code": "dispatcher.mrq.source_immutable", "message": "agent attempted to change source MRQ semantic_key", "action": "mrq.decide-next"}}
    findings = dict(state.get("findings", {}))
    findings.setdefault(mrq_id, []).append({"proposal": payload, "shared_context": bool(state.get("batch_mrqs"))})
    return {**state, "findings": findings, "decision_proposal": payload, "status": "running"}


def decide_apply(state: DecideState, *, apply: Callable[[str, dict[str, Any]], dict[str, Any]]) -> DecideState:
    """Применение одобренного решения через существующую ``mrq.decide``."""

    proposal = state.get("decision_proposal")
    if not proposal:
        return {**state, "status": "failed", "blocker": {"code": "dispatcher.decision.missing", "message": "no decision proposal to apply", "action": "mrq.decide-next"}}
    payload = {key: proposal[key] for key in ("mrq_id", "decision", "target_evidence", "target_coverage", "residual_gap", "target_solution", "rationale", "acceptance_criteria", "risk", "open_questions") if key in proposal}
    apply("mrq.decide", payload)
    return {**state, "approved_decision": payload, "status": "completed"}


def derived_gap_card(mrq_row: dict[str, Any]) -> dict[str, Any] | None:
    """Производная карточка функционального разрыва только для решения ``adapt``.

    Не создаёт отдельную каноническую сущность ``GAP-*``.
    """

    decision = mrq_row.get("migration_decision", {})
    if decision.get("decision") != "adapt":
        return None
    return {
        "schema_version": "1",
        "kind": "derived_gap",
        "mrq_id": mrq_row.get("mrq_id"),
        "residual_gap": decision.get("residual_gap"),
        "target_solution": decision.get("target_solution"),
        "acceptance_criteria": decision.get("acceptance_criteria"),
        "risk": decision.get("risk"),
    }


def build_discover_state(bindings: dict[str, Any], run_id: str, thread_id: str) -> DiscoverState:
    return DiscoverState(
        project_id=bindings["project_id"],
        job_id=bindings["job_id"],
        work_unit_id=bindings["work_unit_id"],
        source_generation_id=bindings["source_generation_id"],
        diff_generation_id=bindings["diff_generation_id"],
        canonical_generation_id=bindings["canonical_generation_id"],
        workflow_fingerprint=bindings["workflow_fingerprint"],
        agent_profile_fingerprint=bindings["agent_profile_fingerprint"],
        instruction_supplement=bindings["instruction_supplement"],
        execution_snapshot_fingerprint=bindings.get("execution_snapshot_fingerprint", ""),
        thread_id=thread_id,
        run_id=run_id,
        window=[],
        analyzed={},
        analyze_results={},
        meanings=[],
        noise=[],
        noise_review_ids=[],
        approved_noise_ids=[],
        approved_noise=[],
        preliminary_groups={},
        form_mrq_results={},
        barrier_open=False,
        batch_proposals=[],
        approved_batch=None,
        published_mrq_ids=[],
        target_batches=[],
        status="running",
        blocker=None,
    )


def build_decide_state(bindings: dict[str, Any], run_id: str, thread_id: str, *, batch: MRQBatch | None = None) -> DecideState:
    return DecideState(
        project_id=bindings["project_id"],
        job_id=bindings["job_id"],
        work_unit_id=bindings["work_unit_id"],
        mrq_id=bindings["work_unit_id"],
        source_generation_id=bindings["source_generation_id"],
        diff_generation_id=bindings["diff_generation_id"],
        canonical_generation_id=bindings["canonical_generation_id"],
        workflow_fingerprint=bindings["workflow_fingerprint"],
        agent_profile_fingerprint=bindings["agent_profile_fingerprint"],
        instruction_supplement=bindings["instruction_supplement"],
        execution_snapshot_fingerprint=bindings.get("execution_snapshot_fingerprint", ""),
        thread_id=thread_id,
        run_id=run_id,
        batch_id=batch.batch_id if batch else None,
        batch_mrqs=list(batch.mrq_ids) if batch else [],
        findings={},
        research_results={},
        decision_proposal=None,
        approved_decision=None,
        status="running",
        blocker=None,
    )


# -- скомпилированные графы ----------------------------------------------


def compile_discover_graph(
    *,
    repo: Path,
    saver: Any,
    executor: Callable[..., dict[str, Any]],
    profile: dict[str, Any],
    supplement: str,
    timeout_seconds: int,
    cancelled: Callable[[], bool],
    bindings_check: Callable[[], bool],
    phase_policies: dict[str, dict[str, Any]] | None = None,
    profiles_by_role: dict[str, dict[str, Any]] | None = None,
    load_result: Callable[[str], dict[str, Any] | None] | None = None,
    save_result: Callable[[str, dict[str, Any]], None] | None = None,
    register_work: Callable[[str, str, list[str]], None] | None = None,
    reuse_work: Callable[[str, str, str], None] | None = None,
    approved_noise: list[dict[str, Any]] | None = None,
):
    """Компилирует реальный LangGraph для ``discover-mrq``.

    Один рабочий поток сразу анализирует DIF и, если он смысловой, передаёт
    его в предварительную группировку. Пул ограничен четырьмя вызовами; ошибка
    ветви не повторяется автоматически и сохраняется как блокирующий исход.
    """

    from langgraph.graph import END, START, StateGraph
    policies = phase_policies or {
        "analyze-dif": {"max_concurrency": 4, "roles": [{"role_id": "analyzer", "count": 4, "instruction_supplement": supplement}]},
        "form-mrq": {"max_concurrency": 4, "roles": [{"role_id": "coordinator", "count": 1, "instruction_supplement": ""}, {"role_id": "grouper", "count": 4, "instruction_supplement": supplement}]},
    }
    role_profiles = profiles_by_role or {"analyzer": profile, "grouper": profile, "coordinator": profile}
    role_supplements = {role["role_id"]: role.get("instruction_supplement", "") for policy in policies.values() for role in policy["roles"]}

    def select(state: DiscoverState) -> DiscoverState:
        return discover_select_window(state, repo=repo)

    def analyze(state: DiscoverState) -> DiscoverState:
        if cancelled():
            return {**state, "status": "resumable", "blocker": {"code": "dispatcher.soft_stopped", "message": "dispatcher was stopped", "action": "mrq.discover-next"}}
        abort = Event()
        phase_cancelled = lambda: cancelled() or abort.is_set()

        def branch(identifier: str) -> AnalyzeResult:
            work_unit = _analyze_work_unit(repo, identifier)
            expected = _result_envelope(state, "analyze-dif", "analyzer", work_unit, None, str(role_profiles["analyzer"].get("instructions_version", "")))
            cached = _compatible_cached(load_result(f"analyze-dif:{identifier}") if load_result else None, expected, state.get("required_reuse_origin"))
            if cached is not None:
                if reuse_work:
                    reuse_work("analyze-dif", "analyzer", identifier)
                return cached
            local = discover_analyze_one(
                state,
                identifier,
                executor=executor,
                repo=repo,
                profile=role_profiles["analyzer"],
                supplement=role_supplements["analyzer"],
                timeout_seconds=timeout_seconds,
                cancelled=phase_cancelled,
                bindings_check=bindings_check,
            )
            result = local.get("analyzed", {}).get(identifier)
            if result is None:
                raise RuntimeError(str(local.get("blocker") or "analysis did not produce a result"))
            if save_result:
                save_result(f"analyze-dif:{identifier}", _result_envelope(state, "analyze-dif", "analyzer", work_unit, result, str(role_profiles["analyzer"].get("instructions_version", ""))))
            return result

        analyze_policy = policies["analyze-dif"]
        analyzed = dict(state.get("analyzed", {}))
        pending = [identifier for identifier in state.get("window", []) if identifier not in analyzed]
        if register_work:
            register_work("analyze-dif", "analyzer", pending)
        branch_results, errors, stopped = _bounded_map(
            pending,
            max_concurrency=analyze_policy["max_concurrency"],
            slot_count=next(role["count"] for role in analyze_policy["roles"] if role["role_id"] == "analyzer"),
            function=branch,
            cancelled=phase_cancelled,
            bindings_check=bindings_check,
            cancel_active=abort.set,
        )
        envelopes = dict(state.get("analyze_results", {}))
        for identifier, result in branch_results.items():
            analyzed[identifier] = result
            envelopes[identifier] = _result_envelope(state, "analyze-dif", "analyzer", _analyze_work_unit(repo, identifier), result, str(role_profiles["analyzer"].get("instructions_version", "")))
        meanings = sorted(identifier for identifier, result in analyzed.items() if result["kind"] == "meaning")
        noise = sorted(identifier for identifier, result in analyzed.items() if result["kind"] == "noise")
        if stopped:
            return {**state, "analyzed": analyzed, "analyze_results": envelopes, "meanings": meanings, "noise": noise, "status": "resumable", "blocker": {"code": "dispatcher.soft_stopped", "message": "dispatcher was stopped", "action": "mrq.discover-next"}}
        if errors:
            return {**state, "analyzed": analyzed, "analyze_results": envelopes, "meanings": meanings, "noise": noise, "status": "failed", "blocker": {"code": "dispatcher.agent.failed", "message": f"{len(errors)} agent branches failed; explicit retry is required", "action": "mrq.discover-next", "branches": errors}}
        return {**state, "analyzed": analyzed, "analyze_results": envelopes, "meanings": meanings, "noise": noise}

    def group(state: DiscoverState) -> DiscoverState:
        if state.get("status") in {"failed", "stale", "resumable"}:
            return state
        group_policy = policies["form-mrq"]
        groups = dict(state.get("preliminary_groups", {}))
        abort = Event()
        phase_cancelled = lambda: cancelled() or abort.is_set()

        def regroup(anchor: str) -> GroupProposal:
            work_unit = _group_work_unit(state, anchor, repo)
            expected = _result_envelope(state, "form-mrq", "grouper", work_unit, None, str(role_profiles["grouper"].get("instructions_version", "")))
            cached = _compatible_cached(load_result(f"form-mrq:{anchor}") if load_result else None, expected, state.get("required_reuse_origin"))
            if cached is not None:
                if reuse_work:
                    reuse_work("form-mrq", "grouper", anchor)
                return cached
            result = discover_preliminary_group(state, anchor, executor=executor, repo=repo, profile=role_profiles["grouper"], supplement=role_supplements["grouper"], timeout_seconds=timeout_seconds, cancelled=phase_cancelled)["preliminary_groups"][anchor]
            if save_result:
                save_result(f"form-mrq:{anchor}", _result_envelope(state, "form-mrq", "grouper", work_unit, result, str(role_profiles["grouper"].get("instructions_version", ""))))
            return result

        anchors = [identifier for identifier in state.get("meanings", []) if identifier not in groups]
        if register_work:
            register_work("form-mrq", "grouper", anchors)
        group_results, group_errors, stopped = _bounded_map(
            anchors,
            max_concurrency=group_policy["max_concurrency"],
            slot_count=next(role["count"] for role in group_policy["roles"] if role["role_id"] == "grouper"),
            function=regroup,
            cancelled=phase_cancelled,
            bindings_check=bindings_check,
            cancel_active=abort.set,
        )
        groups.update(group_results)
        envelopes = dict(state.get("form_mrq_results", {}))
        for anchor, result in group_results.items():
            unit = _group_work_unit(state, anchor, repo)
            envelopes[anchor] = _result_envelope(state, "form-mrq", "grouper", unit, result, str(role_profiles["grouper"].get("instructions_version", "")))
        if stopped:
            return {**state, "preliminary_groups": groups, "form_mrq_results": envelopes, "status": "resumable", "blocker": {"code": "dispatcher.soft_stopped", "message": "dispatcher was stopped", "action": "mrq.discover-next"}}
        if group_errors:
            return {**state, "preliminary_groups": groups, "form_mrq_results": envelopes, "status": "failed", "blocker": {"code": "dispatcher.agent.failed", "message": f"{len(group_errors)} grouping branches failed; explicit retry is required", "action": "mrq.discover-next", "branches": group_errors}}
        return {**state, "preliminary_groups": groups, "form_mrq_results": envelopes}

    def coordinate(state: DiscoverState) -> DiscoverState:
        if state.get("status") in {"failed", "stale", "resumable"}:
            return state
        customer = {row["stable_diff_id"] for row in _read_diff_inventory(repo)} - _owned_diff_ids(repo)
        if customer - (set(state.get("meanings", [])) | set(state.get("noise", []))):
            return {**state, "barrier_open": False, "status": "blocked", "blocker": {"code": "dispatcher.barrier.unclassified", "message": "customer DIF remain unclassified", "action": "mrq.discover-next"}}
        proposals = [state["preliminary_groups"][key] for key in sorted(state.get("preliminary_groups", {}))]
        input_fingerprints = [state["analyze_results"][key]["result_fingerprint"] for key in sorted(state.get("analyze_results", {}))]
        input_fingerprints += [state["form_mrq_results"][key]["result_fingerprint"] for key in sorted(state.get("form_mrq_results", {}))]
        allowed_paths = sorted({str(item.get("path")) for proposal in proposals for item in proposal.get("evidence", []) if item.get("path")})
        work_unit = {
            "id": state.get("work_unit_id", "discover-mrq"),
            "kind": "coordinate-groups",
            "input_fingerprints": input_fingerprints,
            "meaning_diff_ids": state.get("meanings", []),
            "noise_diff_ids": state.get("noise", []),
            "group_proposals": proposals,
            "allowed_paths": allowed_paths,
        }
        from .agents import build_context_manifest
        work_unit = {**work_unit, "allowed_path_fingerprints": build_context_manifest(repo, work_unit)["paths"]}
        if register_work:
            register_work("form-mrq", "coordinator", [str(work_unit["id"])])
        expected = _result_envelope(state, "form-mrq", "coordinator", work_unit, None, str(role_profiles["coordinator"].get("instructions_version", "")))
        cached = _compatible_cached(load_result("form-mrq:coordinate") if load_result else None, expected, state.get("required_reuse_origin"))
        if cached is not None:
            if reuse_work:
                reuse_work("form-mrq", "coordinator", str(work_unit["id"]))
            groups = cached
        else:
            payload = executor(repo, role_profiles["coordinator"], "mrq.discover-next", work_unit, role_supplements["coordinator"], timeout_seconds, cancelled)
            groups = payload["groups"]
            if save_result:
                save_result("form-mrq:coordinate", _result_envelope(state, "form-mrq", "coordinator", work_unit, groups, str(role_profiles["coordinator"].get("instructions_version", ""))))
        keyed = {str(group.get("semantic_key") or group.get("stable_diff_ids", ["group"])[0]): group for group in groups}
        return {**state, "preliminary_groups": keyed, "status": "running", "blocker": None}

    def review_noise(state: DiscoverState) -> DiscoverState:
        if state.get("status") in {"failed", "stale", "resumable"}:
            return state
        return discover_review_noise(
            state,
            repo=repo,
            approved_noise_ids=(item["stable_diff_id"] for item in (approved_noise or [])),
        )

    def barrier(state: DiscoverState) -> DiscoverState:
        if state.get("status") in {"failed", "stale", "resumable"}:
            return state
        return discover_barrier(state, repo=repo)

    def prepare(state: DiscoverState) -> DiscoverState:
        if not state.get("barrier_open"):
            return state
        prepared = discover_batch_from_groups(state, approved_noise)
        return {**prepared, "status": "blocked", "blocker": {"code": "approval.source_batch", "message": "source MRQ batch requires explicit local-user approval", "action": "mrq.publish-source-batch"}}

    graph = StateGraph(DiscoverState)
    graph.add_node("select-window", select)
    graph.add_node("analyze-dif", analyze)
    graph.add_node("form-mrq", group)
    graph.add_node("review-noise", review_noise)
    graph.add_node("coordinate-groups", coordinate)
    graph.add_node("coverage-barrier", barrier)
    graph.add_node("prepare-approval", prepare)
    graph.add_edge(START, "select-window")
    graph.add_edge("select-window", "analyze-dif")
    graph.add_edge("analyze-dif", "form-mrq")
    graph.add_conditional_edges(
        "form-mrq",
        lambda state: (
            "select-window"
            if state.get("status") not in {"failed", "stale", "resumable"}
            and any(
                row["stable_diff_id"] not in _owned_diff_ids(repo) | set(state.get("analyzed", {}))
                for row in _read_diff_inventory(repo)
            )
            else "review-noise"
        ),
        {
            "select-window": "select-window",
            "review-noise": "review-noise",
        },
    )
    graph.add_conditional_edges(
        "review-noise",
        lambda state: "end" if state.get("status") == "blocked" else "coordinate-groups",
        {"end": END, "coordinate-groups": "coordinate-groups"},
    )
    graph.add_edge("coordinate-groups", "coverage-barrier")
    graph.add_edge("coverage-barrier", "prepare-approval")
    graph.add_edge("prepare-approval", END)
    return graph.compile(checkpointer=saver)


def compile_decide_graph(
    *,
    repo: Path,
    saver: Any,
    executor: Callable[..., dict[str, Any]],
    profile: dict[str, Any],
    supplement: str,
    timeout_seconds: int,
    cancelled: Callable[[], bool],
    bindings_check: Callable[[], bool],
    phase_policy: dict[str, Any] | None = None,
    profiles_by_role: dict[str, dict[str, Any]] | None = None,
    load_result: Callable[[str], dict[str, Any] | None] | None = None,
    save_result: Callable[[str, dict[str, Any]], None] | None = None,
    register_work: Callable[[str, str, list[str]], None] | None = None,
    reuse_work: Callable[[str, str, str], None] | None = None,
):
    """Компилирует LangGraph исследования цели до границы одобрения."""

    from langgraph.graph import END, START, StateGraph

    def select(state: DecideState) -> DecideState:
        return decide_select_mrq(state, repo=repo)

    def research(state: DecideState) -> DecideState:
        if state.get("status") == "completed" or cancelled():
            return state
        from .mrq import active
        active_rows = {item["mrq_id"]: item for item in active(repo)["mrq.jsonl"] if item.get("state") != "superseded"}
        mrqs = [
            mrq_id
            for mrq_id in (state.get("batch_mrqs") or [state["mrq_id"]])
            if mrq_id in active_rows and not active_rows[mrq_id].get("migration_decision", {}).get("decision")
        ]
        current = state
        errors: dict[str, str] = {}
        policy = phase_policy or {"max_concurrency": 4, "roles": [{"role_id": "researcher", "count": 4, "instruction_supplement": supplement}]}
        researcher_profile = (profiles_by_role or {}).get("researcher", profile)
        researcher_supplement = next(role.get("instruction_supplement", "") for role in policy["roles"] if role["role_id"] == "researcher")
        if register_work:
            register_work("research-target", "researcher", mrqs)
        abort = Event()
        phase_cancelled = lambda: cancelled() or abort.is_set()

        def research_one(mrq_id: str) -> DecideState:
            work_unit = _research_work_unit(state, mrq_id, repo)
            if work_unit is None:
                raise RuntimeError(f"MRQ disappeared: {mrq_id}")
            expected = _result_envelope(state, "research-target", "researcher", work_unit, None, str(researcher_profile.get("instructions_version", "")))
            cached = _compatible_cached(load_result(f"research-target:{mrq_id}") if load_result else None, expected, state.get("required_reuse_origin"))
            if cached is not None:
                if reuse_work:
                    reuse_work("research-target", "researcher", mrq_id)
                return cached
            result = decide_research_one(state, mrq_id, executor=executor, repo=repo, profile=researcher_profile, supplement=researcher_supplement, timeout_seconds=timeout_seconds, cancelled=phase_cancelled, bindings_check=bindings_check)
            if save_result and result.get("decision_proposal"):
                save_result(f"research-target:{mrq_id}", _result_envelope(state, "research-target", "researcher", work_unit, result, str(researcher_profile.get("instructions_version", ""))))
            return result

        completed, errors, stopped = _bounded_map(
            mrqs,
            max_concurrency=policy["max_concurrency"],
            slot_count=next(role["count"] for role in policy["roles"] if role["role_id"] == "researcher"),
            function=research_one,
            cancelled=phase_cancelled,
            bindings_check=bindings_check,
            cancel_active=abort.set,
        )
        findings = dict(state.get("findings", {}))
        envelopes = dict(state.get("research_results", {}))
        proposals: dict[str, dict[str, Any]] = {}
        for mrq_id, result in completed.items():
            findings.update(result.get("findings", {}))
            work_unit = _research_work_unit(state, mrq_id, repo)
            if work_unit is not None:
                envelopes[mrq_id] = _result_envelope(state, "research-target", "researcher", work_unit, result, str(researcher_profile.get("instructions_version", "")))
            if result.get("decision_proposal"):
                proposals[mrq_id] = result["decision_proposal"]
        if stopped:
            return {**current, "findings": findings, "research_results": envelopes, "status": "resumable", "blocker": {"code": "dispatcher.soft_stopped", "message": "dispatcher was stopped", "action": "mrq.decide-next"}}
        if errors:
            return {**current, "findings": findings, "research_results": envelopes, "status": "failed", "blocker": {"code": "dispatcher.agent.failed", "message": f"{len(errors)} agent branches failed; explicit retry is required", "action": "mrq.decide-next", "branches": errors}}
        proposal = proposals.get(current.get("mrq_id", "")) or next(iter(proposals.values()), None)
        return {**current, "findings": findings, "research_results": envelopes, "decision_proposal": proposal, "status": "blocked", "blocker": {"code": "approval.target_decision", "message": "target decision requires explicit local-user approval", "action": "mrq.decide"}}

    graph = StateGraph(DecideState)
    graph.add_node("select-mrq", select)
    graph.add_node("research-target", research)
    graph.add_edge(START, "select-mrq")
    graph.add_edge("select-mrq", "research-target")
    graph.add_edge("research-target", END)
    return graph.compile(checkpointer=saver)
