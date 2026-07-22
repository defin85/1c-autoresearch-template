"""Операционная классификация готовых MRQ в пакеты исследования цели.

После атомарной публикации исходных MRQ (этап 3 конвейера) классификатор
объединяет родственные MRQ в ограниченные пакеты, чтобы один исследователь мог
переиспользовать общий контекст и находки. Пакеты существуют только в
удаляемой SQLite-контрольной точке и не являются предметной истиной:

* они не меняют MRQ, владение DIF, ворота или одобрения;
* пересчитываются из активного MRQ-поколения при утрате контрольной точки;
* целевые доказательства, решение и одобрение фиксируются отдельно для
  каждого MRQ.

Идентификатор пакета — ``MRQB-`` плюс первые 16 прописных шестнадцатеричных
символов SHA-256 канонического JSON ``{"schema_version":"1","mrq_ids":
[<лексикографически упорядоченные MRQ-ID>]}``. Коллизия разных полных
отпечатков отклоняется.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .contracts import canonical_json, sha256


SCHEMA_VERSION = "1"
MAX_BATCH_SIZE = 16
MRQB_ID_PATTERN = re.compile(r"^MRQB-[0-9A-F]{16}$")


@dataclass(frozen=True)
class MRQBatch:
    """Один операционный пакет исследования цели.

    Поля:
        batch_id: ``MRQB-`` плюс 16 шестнадцатеричных символов отпечатка.
        mrq_ids: упорядоченные MRQ-ID участников (1..16).
        basis: основание объединения (функциональный контекст, общий scope и т.д.).
        anchor_component_ids: опорные исходные компоненты для переиспользования контекста.
    """

    batch_id: str
    mrq_ids: tuple[str, ...]
    basis: str
    anchor_component_ids: tuple[str, ...]

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "batch_id": self.batch_id,
            "mrq_ids": list(self.mrq_ids),
            "basis": self.basis,
            "anchor_component_ids": list(self.anchor_component_ids),
        }


def batch_id(mrq_ids: Iterable[str]) -> str:
    """Детерминированный идентификатор пакета.

    Входные MRQ-ID сортируются лексикографически, чтобы одинаковые наборы
    давали одинаковый отпечаток независимо от порядка входа.
    """

    ordered = sorted(set(mrq_ids))
    if not ordered:
        raise ValueError("MRQ batch must contain at least one MRQ")
    preimage = {"schema_version": SCHEMA_VERSION, "mrq_ids": ordered}
    return "MRQB-" + sha256(canonical_json(preimage))[:16].upper()


def validate_batch_id(identifier: str, mrq_ids: Iterable[str]) -> None:
    """Отклоняет коллизию: разные полные отпечатки не могут давать один ID."""

    expected = batch_id(mrq_ids)
    if identifier != expected:
        raise ValueError(f"MRQ batch ID collision or mismatch: expected {expected}, got {identifier}")
    if not MRQB_ID_PATTERN.fullmatch(identifier):
        raise ValueError(f"invalid MRQ batch ID format: {identifier}")


def _batch_key(mrq: dict[str, Any]) -> tuple[Any, ...]:
    """Ключ предварительной группировки по существующим полям MRQ.

    Используются только канонические поля, разрешённые ``spec.md``:
    ``semantic_key``, ``title``, ``business_meaning``, ``scope``, первичные и
    вспомогательные DIF, доказательства и идентификаторы исходных компонентов.
    """

    source = mrq.get("source_customization", {}) or {}
    evidence = source.get("evidence", []) or []
    components = tuple(sorted({str(item.get("path", "")).split("/")[0] for item in evidence if item.get("path")}))
    scope = str(source.get("scope", "")).strip()
    business = str(source.get("business_meaning", "")).strip()
    return (scope, business, components)


def classify(mrq_rows: list[dict[str, Any]], *, max_batch_size: int = MAX_BATCH_SIZE) -> list[MRQBatch]:
    """Детерминированно разбивает активные MRQ на пакеты исследования цели.

    Правила (по ``spec.md`` «Классификация готовых MRQ для исследования цели»):

    * вход — только активные MRQ (``state != "superseded"``) одного поколения;
    * каждый MRQ входит ровно в один пакет;
    * пакет содержит 1..16 MRQ;
    * порядок пакетов задаётся минимальным ``MRQ-*``;
    * если MRQ нельзя доказательно связать с другими — формируется пакет из одного MRQ.

    Группировка консервативна: объединяет только MRQ с идентичным
    ``business_meaning``/``scope`` и общими опорными компонентами. Этого
    достаточно для переиспользования контекста поиска; ``decide-mrq`` всё равно
    собирает целевые доказательства отдельно для каждого MRQ.
    """

    if not 1 <= max_batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"max batch size must be 1..{MAX_BATCH_SIZE}")
    active = [row for row in mrq_rows if row.get("state") != "superseded"]
    if not active:
        return []
    # лексикографическая сортировка по MRQ-ID обеспечивает стабильность
    active.sort(key=lambda row: str(row.get("mrq_id", "")))
    groups: dict[tuple[Any, ...], list[str]] = {}
    for row in active:
        key = _batch_key(row)
        if not any(value for value in key):
            key = ("__standalone__", str(row.get("mrq_id")))
        groups.setdefault(key, []).append(str(row.get("mrq_id")))
    batches: list[MRQBatch] = []
    # детерминированный обход групп: сначала по минимальному MRQ-ID в группе
    ordered_groups = sorted(groups.values(), key=lambda ids: min(ids))
    for chunk in ordered_groups:
        # делим слишком большие группы на части не более max_batch_size
        for start in range(0, len(chunk), max_batch_size):
            window = chunk[start:start + max_batch_size]
            anchor = _anchor_components(active, window)
            identifier = batch_id(window)
            basis = _group_basis(active, window)
            batches.append(MRQBatch(batch_id=identifier, mrq_ids=tuple(window), basis=basis, anchor_component_ids=anchor))
    # финальная сортировка пакетов по минимальному MRQ-ID
    batches.sort(key=lambda batch: min(batch.mrq_ids))
    _ensure_disjoint_coverage(batches, active)
    return batches


def _anchor_components(active: list[dict[str, Any]], mrq_ids: list[str]) -> tuple[str, ...]:
    by_id = {str(row.get("mrq_id")): row for row in active}
    components: set[str] = set()
    for identifier in mrq_ids:
        row = by_id.get(identifier, {})
        for evidence in (row.get("source_customization", {}) or {}).get("evidence", []) or []:
            path = str(evidence.get("path", ""))
            if path:
                components.add(path.split("/")[0])
    return tuple(sorted(components))


def _group_basis(active: list[dict[str, Any]], mrq_ids: list[str]) -> str:
    by_id = {str(row.get("mrq_id")): row for row in active}
    scopes = {(by_id.get(identifier, {}).get("source_customization", {}) or {}).get("scope", "").strip() for identifier in mrq_ids}
    scopes.discard("")
    if len(scopes) == 1:
        return f"shared scope: {next(iter(scopes))}"
    return "standalone MRQ without provable linkage"


def _ensure_disjoint_coverage(batches: list[MRQBatch], active: list[dict[str, Any]]) -> None:
    seen: dict[str, str] = {}
    expected = {str(row.get("mrq_id")) for row in active}
    covered: set[str] = set()
    for batch in batches:
        validate_batch_id(batch.batch_id, batch.mrq_ids)
        if len(batch.mrq_ids) < 1 or len(batch.mrq_ids) > MAX_BATCH_SIZE:
            raise ValueError(f"MRQ batch size out of range 1..{MAX_BATCH_SIZE}: {batch.batch_id}")
        for identifier in batch.mrq_ids:
            if identifier in covered:
                raise ValueError(f"MRQ belongs to multiple batches: {identifier}")
            if identifier not in expected:
                raise ValueError(f"MRQ batch references unknown or superseded MRQ: {identifier}")
            covered.add(identifier)
            seen[identifier] = batch.batch_id
    missing = expected - covered
    if missing:
        raise ValueError(f"MRQ classification missed active MRQ: {sorted(missing)[0]}")


def assign(mrq_id: str, batches: list[MRQBatch]) -> MRQBatch | None:
    """Возвращает пакет, которому принадлежит MRQ, или ``None``."""

    for batch in batches:
        if mrq_id in batch.mrq_ids:
            return batch
    return None
