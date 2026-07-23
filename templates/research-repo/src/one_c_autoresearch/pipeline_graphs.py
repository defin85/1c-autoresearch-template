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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, TypedDict

from .contracts import canonical_json, sha256
from .mrq_batches import MAX_BATCH_SIZE, MRQBatch, classify


# -- зафиксированные лимиты (design.md §2) ---------------------------------

MAX_PARALLEL_AGENT_CALLS = 4
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
    thread_id: str
    run_id: str
    window: list[str]  # стабильный порядок DIF в окне
    analyzed: dict[str, AnalyzeResult]
    meanings: list[str]  # упорядоченные смысловые DIF
    noise: list[str]
    approved_noise_ids: list[str]
    preliminary_groups: dict[str, GroupProposal]  # anchor_diff_id -> proposal
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
    thread_id: str
    run_id: str
    batch_id: str | None
    batch_mrqs: list[str]
    findings: dict[str, list[dict[str, Any]]]
    decision_proposal: dict[str, Any] | None
    approved_decision: dict[str, Any] | None
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


# -- этапы discover-mrq ---------------------------------------------------


def discover_select_window(state: DiscoverState, *, repo: Path) -> DiscoverState:
    """Этап 1: детерминированный выбор окна непокрытых DIF."""

    window = select_dif_window(repo)
    return {**state, "window": window, "status": "running"}


def discover_analyze_one(state: DiscoverState, stable_diff_id: str, *, executor: Callable[..., dict[str, Any]], repo: Path, profile: dict[str, Any], supplement: str, timeout_seconds: int, cancelled: Callable[[], bool], bindings_check: Callable[[], bool]) -> DiscoverState:
    """Этап 2: один вызов агента для DIF. Возвращает обновлённое состояние.

    Параллелизм ограничивается координатором/``AddDynamicEdges``; эта функция
    атомарна для одного DIF и безопасно вызывается из параллельных ветвей при
    условии внешней синхронизации записи (через ``DispatcherStore``).
    """

    if not bindings_check():
        return {**state, "status": "stale", "blocker": {"code": "dispatcher.bindings.stale", "message": "active generations changed during analysis", "action": "mrq.discover-next"}}
    from .workflow import semantic_diff_context
    fact = next((row for row in _read_diff_inventory(repo) if row["stable_diff_id"] == stable_diff_id), None)
    work_unit = {"id": stable_diff_id, "kind": "uncovered-diff", **semantic_diff_context(repo, stable_diff_id, fact)}
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

    meanings = list(state.get("meanings", []))
    inventory = {row["stable_diff_id"]: row for row in _read_diff_inventory(repo)}
    candidates = _stable_group_candidates(anchor, meanings, inventory)
    from .workflow import semantic_diff_context
    semantic = [semantic_diff_context(repo, identifier, inventory[identifier]) for identifier in candidates if inventory.get(identifier, {}).get("object_kind") == "extension_intervention"]
    work_unit = {
        "id": anchor,
        "kind": "preliminary-group",
        "candidate_diff_ids": candidates,
        "semantic_extension_context": semantic,
        "allowed_paths": sorted(
            {str(inventory[identifier]["path"]) for identifier in candidates if inventory.get(identifier, {}).get("path")}
            | {path for context in semantic for path in context["allowed_paths"]}
        ),
    }
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

    customer = [row["stable_diff_id"] for row in _read_diff_inventory(repo)]
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
    covered: set[str] = set()
    for proposal in groups.values():
        covered.update(proposal.get("stable_diff_ids", []))
        covered.update(proposal.get("supporting_diff_ids", []))
    uncovered_meanings = meanings - covered
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
    return {**state, "barrier_open": True, "status": "running", "blocker": None}


def discover_batch_from_groups(state: DiscoverState) -> DiscoverState:
    """Этап 5: сборка одобряемого пакета групп из предварительных."""

    groups = state.get("preliminary_groups", {})
    batch_proposals = [groups[anchor] for anchor in sorted(groups)]
    approved_noise_ids = list(state.get("approved_noise_ids", state.get("noise", [])))
    return {**state, "batch_proposals": batch_proposals, "approved_noise_ids": approved_noise_ids}


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


def discover_classify_target_batches(state: DiscoverState, *, repo: Path) -> DiscoverState:
    """Этап 7: операционная классификация готовых MRQ в пакеты исследования цели."""

    from .mrq import active
    rows = active(repo)["mrq.jsonl"]
    batches = classify(rows)
    payload = [batch.canonical_payload() for batch in batches]
    return {**state, "target_batches": payload, "status": "completed"}


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


def decide_research_one(state: DecideState, mrq_id: str, *, executor: Callable[..., dict[str, Any]], repo: Path, profile: dict[str, Any], supplement: str, timeout_seconds: int, cancelled: Callable[[], bool], bindings_check: Callable[[], bool]) -> DecideState:
    """Один вызов исследования цели. Запрещает менять исходную часть MRQ."""

    if not bindings_check():
        return {**state, "status": "stale", "blocker": {"code": "dispatcher.bindings.stale", "message": "active generations changed during research", "action": "mrq.decide-next"}}
    from .mrq import active
    rows = active(repo)["mrq.jsonl"]
    target = next((item for item in rows if item["mrq_id"] == mrq_id), None)
    if target is None:
        return {**state, "status": "failed", "blocker": {"code": "dispatcher.mrq.missing", "message": f"MRQ disappeared: {mrq_id}", "action": "mrq.decide-next"}}
    batch_ids = state.get("batch_mrqs", [])
    batch_context = [
        {"mrq_id": item["mrq_id"], "semantic_key": item.get("semantic_key"), "title": item.get("title"), "business_meaning": item.get("source_customization", {}).get("business_meaning"), "scope": item.get("source_customization", {}).get("scope")}
        for item in rows
        if item["mrq_id"] in batch_ids
    ]
    work_unit = {"id": mrq_id, "kind": "migration-decision", "mrq": target, "batch_context": batch_context, "allowed_paths": sorted({evidence["path"] for evidence in target.get("source_customization", {}).get("evidence", []) if evidence.get("path")})}
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
        thread_id=thread_id,
        run_id=run_id,
        window=[],
        analyzed={},
        meanings=[],
        noise=[],
        approved_noise_ids=[],
        preliminary_groups={},
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
        thread_id=thread_id,
        run_id=run_id,
        batch_id=batch.batch_id if batch else None,
        batch_mrqs=list(batch.mrq_ids) if batch else [],
        findings={},
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
    load_result: Callable[[str], dict[str, Any] | None] | None = None,
    save_result: Callable[[str, dict[str, Any]], None] | None = None,
):
    """Компилирует реальный LangGraph для ``discover-mrq``.

    Один рабочий поток сразу анализирует DIF и, если он смысловой, передаёт
    его в предварительную группировку. Пул ограничен четырьмя вызовами; ошибка
    ветви не повторяется автоматически и сохраняется как блокирующий исход.
    """

    from langgraph.graph import END, START, StateGraph

    def select(state: DiscoverState) -> DiscoverState:
        return discover_select_window(state, repo=repo)

    def analyze_and_group(state: DiscoverState) -> DiscoverState:
        if cancelled():
            return {**state, "status": "resumable", "blocker": {"code": "dispatcher.soft_stopped", "message": "dispatcher was stopped", "action": "mrq.discover-next"}}

        def branch(identifier: str) -> tuple[str, AnalyzeResult, GroupProposal | None]:
            cached = load_result(f"analyze:{identifier}") if load_result else None
            if cached is not None:
                return identifier, cached["result"], cached.get("group")
            local = discover_analyze_one(
                state,
                identifier,
                executor=executor,
                repo=repo,
                profile=profile,
                supplement=supplement,
                timeout_seconds=timeout_seconds,
                cancelled=cancelled,
                bindings_check=bindings_check,
            )
            result = local.get("analyzed", {}).get(identifier)
            if result is None:
                raise RuntimeError(str(local.get("blocker") or "analysis did not produce a result"))
            group = None
            if result["kind"] == "meaning":
                grouped = discover_preliminary_group(
                    {**state, "meanings": [identifier]},
                    identifier,
                    executor=executor,
                    repo=repo,
                    profile=profile,
                    supplement=supplement,
                    timeout_seconds=timeout_seconds,
                    cancelled=cancelled,
                )
                group = grouped["preliminary_groups"][identifier]
            if save_result:
                save_result(f"analyze:{identifier}", {"result": result, "group": group})
            return identifier, result, group

        analyzed = dict(state.get("analyzed", {}))
        groups = dict(state.get("preliminary_groups", {}))
        errors: dict[str, str] = {}
        pending = [identifier for identifier in state.get("window", []) if identifier not in analyzed]
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_AGENT_CALLS, thread_name_prefix="discover-mrq") as pool:
            futures = {pool.submit(branch, identifier): identifier for identifier in pending}
            for future in as_completed(futures):
                identifier = futures[future]
                try:
                    _, result, group = future.result()
                    analyzed[identifier] = result
                    if group is not None:
                        groups[identifier] = group
                except Exception as exc:
                    errors[identifier] = f"{type(exc).__name__}: {exc}"
        meanings = sorted(identifier for identifier, result in analyzed.items() if result["kind"] == "meaning")
        noise = sorted(identifier for identifier, result in analyzed.items() if result["kind"] == "noise")
        if errors:
            return {**state, "analyzed": analyzed, "meanings": meanings, "noise": noise, "preliminary_groups": groups, "status": "failed", "blocker": {"code": "dispatcher.agent.failed", "message": f"{len(errors)} agent branches failed; explicit retry is required", "action": "mrq.discover-next", "branches": errors}}
        # Поздние связанные DIF пересобирают предварительные группы уже на
        # полном наборе смысловых результатов. Новое полное предложение
        # атомарно вытесняет раннее предложение того же опорного DIF.
        complete = {**state, "analyzed": analyzed, "meanings": meanings, "noise": noise, "preliminary_groups": groups}
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_AGENT_CALLS, thread_name_prefix="group-mrq") as pool:
            def regroup(anchor: str) -> GroupProposal:
                cached = load_result(f"group:{anchor}") if load_result else None
                if cached is not None:
                    return cached["group"]
                result = discover_preliminary_group(complete, anchor, executor=executor, repo=repo, profile=profile, supplement=supplement, timeout_seconds=timeout_seconds, cancelled=cancelled)["preliminary_groups"][anchor]
                if save_result:
                    save_result(f"group:{anchor}", {"group": result})
                return result

            futures = {pool.submit(regroup, anchor): anchor for anchor in meanings}
            for future in as_completed(futures):
                anchor = futures[future]
                try:
                    groups[anchor] = future.result()
                except Exception as exc:
                    errors[anchor] = f"{type(exc).__name__}: {exc}"
        if errors:
            return {**complete, "preliminary_groups": groups, "status": "failed", "blocker": {"code": "dispatcher.agent.failed", "message": f"{len(errors)} grouping branches failed; explicit retry is required", "action": "mrq.discover-next", "branches": errors}}
        consolidated: dict[str, GroupProposal] = {}
        for anchor in sorted(groups):
            proposal = groups[anchor]
            key = proposal.get("semantic_key") or anchor
            existing_anchor = next((item for item, value in consolidated.items() if (value.get("semantic_key") or item) == key), None)
            if existing_anchor is None:
                consolidated[anchor] = proposal
                continue
            existing = consolidated[existing_anchor]
            existing["stable_diff_ids"] = sorted(set(existing.get("stable_diff_ids", [])) | set(proposal.get("stable_diff_ids", [])))
            existing["supporting_diff_ids"] = sorted(set(existing.get("supporting_diff_ids", [])) | set(proposal.get("supporting_diff_ids", [])))
            evidence = {canonical_json(item): item for item in [*existing.get("evidence", []), *proposal.get("evidence", [])]}
            existing["evidence"] = [evidence[key] for key in sorted(evidence)]
        groups = consolidated
        return {**state, "analyzed": analyzed, "meanings": meanings, "noise": noise, "preliminary_groups": groups}

    def barrier(state: DiscoverState) -> DiscoverState:
        if state.get("status") in {"failed", "stale", "resumable"}:
            return state
        return discover_barrier(state, repo=repo)

    def prepare(state: DiscoverState) -> DiscoverState:
        if not state.get("barrier_open"):
            return state
        prepared = discover_batch_from_groups(state)
        return {**prepared, "status": "blocked", "blocker": {"code": "approval.source_batch", "message": "source MRQ batch requires explicit local-user approval", "action": "mrq.publish-source-batch"}}

    graph = StateGraph(DiscoverState)
    graph.add_node("select-window", select)
    graph.add_node("analyze-and-group", analyze_and_group)
    graph.add_node("coverage-barrier", barrier)
    graph.add_node("prepare-approval", prepare)
    graph.add_edge(START, "select-window")
    graph.add_edge("select-window", "analyze-and-group")
    graph.add_edge("analyze-and-group", "coverage-barrier")
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
    load_result: Callable[[str], dict[str, Any] | None] | None = None,
    save_result: Callable[[str, dict[str, Any]], None] | None = None,
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
        def research_one(mrq_id: str) -> DecideState:
            cached = load_result(f"research:{mrq_id}") if load_result else None
            if cached is not None:
                return {**state, "findings": cached["findings"], "decision_proposal": cached["decision_proposal"], "status": "running"}
            result = decide_research_one(state, mrq_id, executor=executor, repo=repo, profile=profile, supplement=supplement, timeout_seconds=timeout_seconds, cancelled=cancelled, bindings_check=bindings_check)
            if save_result and result.get("decision_proposal"):
                save_result(f"research:{mrq_id}", {"findings": result.get("findings", {}), "decision_proposal": result["decision_proposal"]})
            return result

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_AGENT_CALLS, thread_name_prefix="decide-mrq") as pool:
            futures = {
                pool.submit(research_one, mrq_id): mrq_id
                for mrq_id in mrqs
            }
            findings = dict(state.get("findings", {}))
            proposals: dict[str, dict[str, Any]] = {}
            for future in as_completed(futures):
                mrq_id = futures[future]
                try:
                    result = future.result()
                    findings.update(result.get("findings", {}))
                    if result.get("decision_proposal"):
                        proposals[mrq_id] = result["decision_proposal"]
                except Exception as exc:
                    errors[mrq_id] = f"{type(exc).__name__}: {exc}"
        if errors:
            return {**current, "findings": findings, "status": "failed", "blocker": {"code": "dispatcher.agent.failed", "message": f"{len(errors)} agent branches failed; explicit retry is required", "action": "mrq.decide-next", "branches": errors}}
        proposal = proposals.get(current.get("mrq_id", "")) or next(iter(proposals.values()), None)
        return {**current, "findings": findings, "decision_proposal": proposal, "status": "blocked", "blocker": {"code": "approval.target_decision", "message": "target decision requires explicit local-user approval", "action": "mrq.decide"}}

    graph = StateGraph(DecideState)
    graph.add_node("select-mrq", select)
    graph.add_node("research-target", research)
    graph.add_edge(START, "select-mrq")
    graph.add_edge("select-mrq", "research-target")
    graph.add_edge("research-target", END)
    return graph.compile(checkpointer=saver)
