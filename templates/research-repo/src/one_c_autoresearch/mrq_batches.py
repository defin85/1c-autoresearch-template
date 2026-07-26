"""Операционная классификация готовых MRQ в пакеты исследования цели.

После атомарной публикации исходных MRQ классификатор объединяет родственные
MRQ в ограниченные пакеты, чтобы один исследователь мог переиспользовать общий
контекст и находки. Пакеты публикуются как неизменяемое производное поколение:

* они не меняют MRQ, владение DIF, ворота или одобрения;
* проверяются по активному MRQ-поколению и его исходному отпечатку;
* целевые доказательства, решение и одобрение фиксируются отдельно для
  каждого MRQ.

Идентификатор пакета — ``MRQB-`` плюс первые 16 прописных шестнадцатеричных
символов SHA-256 канонического JSON ``{"schema_version":"1","mrq_ids":
[<лексикографически упорядоченные MRQ-ID>]}``. Коллизия разных полных
отпечатков отклоняется.
"""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .contracts import atomic_json, canonical_json, repository_lock, sha256


SCHEMA_VERSION = "1"
WINDOW_ALGORITHM_VERSION = "1"
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


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _source_inputs(repo: Path, generation_id: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, str]], list[dict[str, Any]]]:
    pointer = json.loads((repo / "research/active-generation.json").read_text(encoding="utf-8"))
    generation_id = generation_id or str(pointer.get("canonical_generation_id") or "")
    root = repo / "analysis/migration-requirements/generations" / generation_id
    mrqs = [row for row in _jsonl(root / "mrq.jsonl") if row.get("state") != "superseded"]
    dispositions = _jsonl(root / "dispositions.jsonl")
    diff_pointer = json.loads((repo / "research/active-diff-generation.json").read_text(encoding="utf-8"))
    diff_root = repo / "analysis/indexes/generations" / str(diff_pointer.get("generation_id") or "")
    with (diff_root / "diff-inventory.csv").open(encoding="utf-8", newline="") as stream:
        diffs = {row["stable_diff_id"]: row for row in csv.DictReader(stream)}
    source_pointer = json.loads((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    return pointer, mrqs, dispositions, diffs, list(source_pointer.get("components", []))


def _component_id(evidence: dict[str, Any], diffs: dict[str, dict[str, str]], components: list[dict[str, Any]]) -> str:
    stable_diff_id = str(evidence.get("stable_diff_id") or "")
    fact = diffs.get(stable_diff_id)
    if fact is None:
        raise ValueError(f"source evidence references unknown DIF: {stable_diff_id}")
    candidate = f"{fact.get('after_role', '')}/{evidence.get('path', '')}".strip("/")
    matches = [
        component for component in components
        if candidate == str(component.get("path", "")).rstrip("/")
        or candidate.startswith(str(component.get("path", "")).rstrip("/") + "/")
    ]
    if not matches:
        raise ValueError(f"source evidence has no component: {candidate}")
    longest = max(len(str(component.get("path", "")).rstrip("/")) for component in matches)
    winners = [component for component in matches if len(str(component.get("path", "")).rstrip("/")) == longest]
    if len(winners) != 1:
        raise ValueError(f"source evidence has ambiguous component: {candidate}")
    return str(winners[0]["component_id"])


def source_mrq_payload(repo: Path, generation_id: str | None = None) -> tuple[str, str, list[dict[str, Any]]]:
    """Возвращает канонический предобраз и отпечаток разрешённых исходных полей MRQ."""

    pointer, mrqs, dispositions, diffs, components = _source_inputs(repo, generation_id)
    relations: dict[str, list[dict[str, Any]]] = {}
    for row in dispositions:
        relations.setdefault(str(row.get("mrq_id") or ""), []).append(row)
    records: list[dict[str, Any]] = []
    for mrq in sorted(mrqs, key=lambda row: str(row.get("mrq_id") or "")):
        mrq_id = str(mrq.get("mrq_id") or "")
        source = mrq.get("source_customization", {}) or {}
        evidence = [
            {
                "path": str(item.get("path") or ""),
                "fingerprint": str(item.get("fingerprint") or ""),
                "stable_diff_id": str(item.get("stable_diff_id") or ""),
            }
            for item in source.get("evidence", []) or []
        ]
        evidence.sort(key=lambda item: (item["path"], item["fingerprint"], item["stable_diff_id"]))
        source_component_ids = sorted({_component_id(item, diffs, components) for item in evidence})
        owned = relations.get(mrq_id, [])
        records.append({
            "mrq_id": mrq_id,
            "semantic_key": str(mrq.get("semantic_key") or ""),
            "title": str(mrq.get("title") or ""),
            "business_meaning": str(source.get("business_meaning") or ""),
            "scope": str(source.get("scope") or ""),
            "primary_dif_ids": sorted(str(item["stable_diff_id"]) for item in owned if item.get("primary") is True),
            "supporting_dif_ids": sorted(str(item["stable_diff_id"]) for item in owned if item.get("primary") is False),
            "source_component_ids": source_component_ids,
            "source_evidence": evidence,
        })
    payload = {"schema_version": SCHEMA_VERSION, "window_algorithm_version": WINDOW_ALGORITHM_VERSION, "mrqs": records}
    return generation_id or str(pointer.get("canonical_generation_id") or ""), "sha256:" + sha256(canonical_json(payload)), records


def stable_windows(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Группирует и режет разрешённые MRQ на стабильные окна не более 16."""

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in records:
        key: tuple[Any, ...] = (
            record["scope"],
            record["business_meaning"],
            tuple(record["source_component_ids"]),
        )
        if not any(key):
            key = ("__standalone__", record["mrq_id"])
        groups.setdefault(key, []).append(record)
    windows: list[list[dict[str, Any]]] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda item: item["mrq_id"])
        windows.extend(ordered[start:start + MAX_BATCH_SIZE] for start in range(0, len(ordered), MAX_BATCH_SIZE))
    return sorted(windows, key=lambda window: window[0]["mrq_id"])


def _batch_row(batch: MRQBatch) -> dict[str, Any]:
    payload = batch.canonical_payload()
    payload["full_fingerprint"] = "sha256:" + sha256(canonical_json(payload))
    return payload


def validate_rows(rows: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    batches = [
        MRQBatch(
            batch_id=str(row.get("batch_id") or ""),
            mrq_ids=tuple(row.get("mrq_ids") or []),
            basis=str(row.get("basis") or ""),
            anchor_component_ids=tuple(row.get("anchor_component_ids") or []),
        )
        for row in rows
    ]
    active = [{"mrq_id": record["mrq_id"]} for record in records]
    _ensure_disjoint_coverage(batches, active)
    identifiers: dict[str, str] = {}
    for row, batch in zip(rows, batches):
        expected = _batch_row(batch)
        if row != expected:
            raise ValueError(f"MRQ batch fingerprint or fields mismatch: {batch.batch_id}")
        previous = identifiers.setdefault(batch.batch_id, expected["full_fingerprint"])
        if previous != expected["full_fingerprint"]:
            raise ValueError(f"MRQ batch shortened ID collision: {batch.batch_id}")


def publish(repo: Path, batches: list[MRQBatch], expected_source_mrq_fingerprint: str) -> dict[str, Any]:
    """Атомарно публикует проверенное content-addressed поколение пакетов."""

    with repository_lock(repo):
        origin_id, current_fingerprint, records = source_mrq_payload(repo)
        if current_fingerprint != expected_source_mrq_fingerprint:
            raise RuntimeError("stale source MRQ fingerprint")
        pointer_path = repo / "research/active-generation.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        existing = pointer.get("batch_generation")
        if existing and existing.get("source_mrq_fingerprint") == current_fingerprint:
            load_active(repo)
            return existing
        rows = [_batch_row(batch) for batch in batches]
        validate_rows(rows, records)
        payload = b"".join(canonical_json(row) + b"\n" for row in rows)
        result_fingerprint = "sha256:" + sha256(payload)
        manifest_preimage = {
            "schema_version": SCHEMA_VERSION,
            "origin_canonical_generation_id": origin_id,
            "source_mrq_fingerprint": current_fingerprint,
            "result_fingerprint": result_fingerprint,
            "batches_sha256": sha256(payload),
            "batch_count": len(rows),
            "mrq_count": len(records),
        }
        generation_id = sha256(canonical_json(manifest_preimage))
        destination = repo / "analysis/migration-requirements/batch-generations" / generation_id
        if not destination.exists():
            staging_parent = repo / "analysis/migration-requirements/.staging"
            staging_parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=staging_parent) as temporary:
                staging = Path(temporary)
                (staging / "batches.jsonl").write_bytes(payload)
                atomic_json(staging / "manifest.json", {**manifest_preimage, "generation_id": generation_id})
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, destination)
        binding = {
            "generation_id": generation_id,
            "result_fingerprint": result_fingerprint,
            "source_mrq_fingerprint": current_fingerprint,
            "origin_canonical_generation_id": origin_id,
        }
        atomic_json(pointer_path, {**pointer, "batch_generation": binding})
        return binding


def load_active(repo: Path) -> list[MRQBatch]:
    """Проверяет активную привязку и возвращает только опубликованные пакеты."""

    pointer = json.loads((repo / "research/active-generation.json").read_text(encoding="utf-8"))
    binding = pointer.get("batch_generation")
    if not isinstance(binding, dict):
        raise ValueError("active MRQ batch generation is missing")
    origin_id, source_fingerprint, records = source_mrq_payload(repo)
    if binding.get("source_mrq_fingerprint") != source_fingerprint:
        raise ValueError("active MRQ batch generation is stale")
    generation_id = str(binding.get("generation_id") or "")
    root = repo / "analysis/migration-requirements/batch-generations" / generation_id
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    payload = (root / "batches.jsonl").read_bytes()
    rows = [json.loads(line) for line in payload.splitlines() if line.strip()]
    expected_manifest = {
        "schema_version": SCHEMA_VERSION,
        "origin_canonical_generation_id": str(binding.get("origin_canonical_generation_id") or origin_id),
        "source_mrq_fingerprint": source_fingerprint,
        "result_fingerprint": "sha256:" + sha256(payload),
        "batches_sha256": sha256(payload),
        "batch_count": len(rows),
        "mrq_count": len(records),
    }
    expected_generation = sha256(canonical_json(expected_manifest))
    if manifest != {**expected_manifest, "generation_id": expected_generation} or generation_id != expected_generation:
        raise ValueError("active MRQ batch manifest is corrupt")
    if binding.get("result_fingerprint") != expected_manifest["result_fingerprint"]:
        raise ValueError("active MRQ batch result fingerprint mismatch")
    validate_rows(rows, records)
    return [
        MRQBatch(row["batch_id"], tuple(row["mrq_ids"]), row["basis"], tuple(row["anchor_component_ids"]))
        for row in rows
    ]
