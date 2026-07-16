from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import read_jsonl, repo_path, utc_now_iso, write_jsonl


REGISTRY_DIR = "analysis/customization-registry"
ITEMS_JSONL = f"{REGISTRY_DIR}/customization-items.jsonl"
EVIDENCE_JSONL = f"{REGISTRY_DIR}/customization-evidence.jsonl"
LINKS_JSONL = f"{REGISTRY_DIR}/customization-links.jsonl"
LINEAGE_JSONL = f"{REGISTRY_DIR}/customization-lineage.jsonl"
BUILD_METADATA_JSON = f"{REGISTRY_DIR}/build-metadata.json"
SUMMARY_MD = f"{REGISTRY_DIR}/summary.md"
EXPORT_CSV = f"{REGISTRY_DIR}/customization-items.csv"
DIFF_CONTEXT_JSONL = f"{REGISTRY_DIR}/diff-context.jsonl"
DESIGNER_REPORT_JSONL = f"{REGISTRY_DIR}/designer-report-index.jsonl"
UUID_RESOLUTION_JSONL = f"{REGISTRY_DIR}/uuid-resolution.jsonl"

FINAL_DIFF_CSV = "analysis/indexes/final-diff-inventory.csv"
CUSTOM_METADATA_JSONL = "analysis/custom-metadata/index.jsonl"
CUSTOM_METADATA_CSV = "analysis/custom-metadata/index.csv"
EXTERNAL_INVENTORY_CSV = "analysis/external-processing/inventory.csv"
EXTERNAL_RECONCILIATION_CSV = "analysis/external-processing/external-tools-source-reconciliation.csv"
EXTERNAL_REVIEW_INDEX_CSV = "analysis/external-processing/review/index.csv"
FUNCTIONAL_GAP_INDEX_CSV = "analysis/functional-gaps/index.csv"

REGISTRY_SCHEMA_VERSION = "customization-registry/v1"
LINEAGE_EVENT_TYPES = {"split", "merge", "supersede", "restore"}
ACTIVE_PUBLICATION_STATUSES = {"confirmed", "ready_for_review", "approved"}
MIGRATION_DECISIONS = {"carry", "adapt", "drop", "needs_customer_decision"}
BP30_COVERAGE_STATUSES = {"covered", "partially_covered", "not_covered", "unknown", "needs_customer_decision"}
SCOPE_STATUSES = {"included", "excluded_by_customer", "excluded_false_positive"}


class CustomizationRegistryError(RuntimeError):
    pass


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle) if any(str(value or "").strip() for value in row.values())]


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha1(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest().upper()


def stable_customization_id(semantic_key: str) -> str:
    return f"CUS-{_sha1(semantic_key)[:10]}"


def _split_refs(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[;,]", str(value or "")) if item.strip()]


def _normalize_external_title(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\b20\d{2}\b", "", text)
    text = re.sub(r"\bv\d+\b", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_status(row: dict[str, str], root: Path) -> str:
    state = str(row.get("source_state") or "").strip()
    source_dir = str(row.get("source_dir") or "").strip()
    if state == "found" and source_dir and repo_path(root, source_dir).is_dir():
        return "found"
    if state in {"failed_unpack", "not_found"}:
        return state
    if state == "source_only":
        return "source_only"
    return "missing"


def _item(
    semantic_key: str,
    title: str,
    source_kinds: list[str],
    source_objects: list[str],
    change_kind: str,
    business_area: str,
    business_meaning: str,
    status: str = "ready_for_review",
    confidence: str = "medium",
    coverage_status: str = "unknown",
    migration_decision: str = "needs_customer_decision",
    scope_status: str = "included",
    exclusion_reason: str = "",
    reviewer_notes: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "customization_id": stable_customization_id(semantic_key),
        "semantic_key": semantic_key,
        "title": title,
        "source_bp20_objects": sorted(set(source_objects)),
        "source_kinds": sorted(set(source_kinds)),
        "change_kind": change_kind,
        "business_area": business_area,
        "business_meaning": business_meaning,
        "status": status,
        "confidence": confidence,
        "bp30_coverage_status": coverage_status,
        "migration_decision": migration_decision,
        "scope_status": scope_status,
        "exclusion_reason": exclusion_reason,
        "reviewer_notes": reviewer_notes,
    }


def _evidence(customization_id: str, evidence_type: str, **payload: Any) -> dict[str, Any]:
    return {"schema_version": REGISTRY_SCHEMA_VERSION, "customization_id": customization_id, "evidence_type": evidence_type, **payload}


def _link(customization_id: str, target_type: str, target_id: str, relation: str, **payload: Any) -> dict[str, Any]:
    return {"schema_version": REGISTRY_SCHEMA_VERSION, "customization_id": customization_id, "target_type": target_type, "target_id": target_id, "relation": relation, **payload}


def _custom_metadata_groups(root: Path) -> dict[str, list[dict[str, Any]]]:
    path = repo_path(root, CUSTOM_METADATA_JSONL)
    if not path.exists():
        return {}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for _, row in read_jsonl(path):
        if row.get("status") != "non_typical" or row.get("change_type") == "unchanged":
            continue
        object_name = str(row.get("metadata_full_name") or row.get("metadata_name") or "").strip()
        if object_name:
            groups[object_name].append(row)
    return groups


def _build_custom_metadata_items(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    diff_by_id = {row.get("diff_id", ""): row for row in _read_csv_rows(repo_path(root, FINAL_DIFF_CSV)) if row.get("diff_id")}
    for object_name, rows in sorted(_custom_metadata_groups(root).items()):
        semantic_key = f"custom_metadata:{object_name}"
        item = _item(
            semantic_key,
            f"Доработка метаданных {object_name}",
            ["custom_metadata"],
            [object_name],
            "metadata",
            "Доработки основной конфигурации",
            f"Нетиповые изменения объекта {object_name}. Детальные части сохранены как evidence CMI.",
        )
        items.append(item)
        customization_id = item["customization_id"]
        diff_ids: set[str] = set()
        subject_cards: set[str] = set()
        for row in rows:
            reconciliation = row.get("reconciliation") or {}
            row_links = reconciliation.get("links") or {}
            diff_ids.update(str(value) for value in row_links.get("final_diff_ids") or row_links.get("diff_ids") or [])
            subject_cards.update(str(value) for value in row_links.get("subject_card_slugs") or [])
            evidence.append(
                _evidence(
                    customization_id,
                    "custom_metadata",
                    item_id=row.get("item_id", ""),
                    custom_metadata_item_key=row.get("item_key", ""),
                    metadata_object=object_name,
                    part_kind=row.get("part_kind", ""),
                    part_name=row.get("part_name", ""),
                    part_path=row.get("part_path", ""),
                    change_type=row.get("change_type", ""),
                    summary=f"{row.get('change_type', '')}: {row.get('part_kind', '')} {row.get('part_name', '')}".strip(),
                )
            )
        for diff_id in sorted(diff_ids):
            links.append(_link(customization_id, "diff", diff_id, "physical_evidence"))
            diff_row = diff_by_id.get(diff_id, {})
            evidence.append(
                _evidence(
                    customization_id,
                    "diff",
                    diff_id=diff_id,
                    source_path=diff_row.get("path") or diff_row.get("evidence_ref") or "",
                    summary=diff_row.get("summary") or diff_row.get("classification") or "Физическое изменение из final-diff-inventory",
                )
            )
        for slug in sorted(subject_cards):
            links.append(_link(customization_id, "subject_card", slug, "supporting_context"))
    return items, evidence, links


def _diff_source_kind(row: dict[str, str]) -> str:
    area = str(row.get("area") or "").strip()
    if area == "bsl":
        return "bsl_source"
    if area in {"metadata", "binary"}:
        return "diff"
    return area or "diff"


def _build_final_diff_items(root: Path, covered_diff_ids: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in _read_csv_rows(repo_path(root, FINAL_DIFF_CSV)):
        diff_id = str(row.get("diff_id") or "")
        if not diff_id or diff_id in covered_diff_ids:
            continue
        key = str(row.get("object_name") or row.get("path") or diff_id)
        groups[key].append(row)
    items: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for object_name, rows in sorted(groups.items()):
        source_kinds = sorted({_diff_source_kind(row) for row in rows})
        semantic_key = f"final_diff:{object_name}"
        item = _item(
            semantic_key,
            f"Доработка по diff {object_name}",
            source_kinds,
            [object_name],
            "bsl" if "bsl_source" in source_kinds else "diff",
            rows[0].get("final_feature_id") or rows[0].get("feature_id") or "Доработки основной конфигурации",
            rows[0].get("summary") or f"Смысловая доработка, восстановленная из final-diff для {object_name}.",
            status="ready_for_review",
            confidence=rows[0].get("reverse_confidence") or rows[0].get("confidence") or "medium",
            reviewer_notes="Fallback-кандидат из final-diff для строк без custom-metadata CUS.",
        )
        items.append(item)
        customization_id = item["customization_id"]
        for row in rows:
            diff_id = str(row.get("diff_id") or "")
            links.append(_link(customization_id, "diff", diff_id, "physical_evidence"))
            evidence.append(
                _evidence(
                    customization_id,
                    "diff",
                    diff_id=diff_id,
                    source_path=row.get("path") or row.get("evidence_ref") or "",
                    summary=row.get("summary") or "Физическое изменение из final-diff-inventory",
                    reverse_status=row.get("reverse_status", ""),
                    reverse_scenario_id=row.get("reverse_scenario_id", ""),
                )
            )
            if row.get("area") == "bsl":
                evidence.append(
                    _evidence(
                        customization_id,
                        "bsl_source",
                        source_path=row.get("evidence_ref") or row.get("path") or "",
                        line_range="",
                        search_marker=row.get("object_name") or "",
                        summary=row.get("summary") or "BSL-изменение из final-diff-inventory",
                    )
                )
            if row.get("reverse_status"):
                evidence.append(
                    _evidence(
                        customization_id,
                        "reverse_map_decision",
                        diff_id=diff_id,
                        decision=row.get("reverse_status", ""),
                        confidence=row.get("reverse_confidence", ""),
                        scenario_id=row.get("reverse_scenario_id", ""),
                        summary=row.get("blocking_reason") or row.get("notes") or "",
                    )
                )
    return items, evidence, links


def _external_rows(root: Path) -> list[dict[str, str]]:
    rows = _read_csv_rows(repo_path(root, EXTERNAL_REVIEW_INDEX_CSV))
    return [row for row in rows if row.get("external_review_id") or row.get("external_id")]


def _build_external_items(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    items_by_key: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for row in _external_rows(root):
        external_id = str(row.get("external_id") or row.get("external_review_id") or "").strip()
        title = str(row.get("title") or row.get("original_name") or external_id).strip()
        business_summary = str(row.get("business_purpose") or row.get("migration_relevance") or title).strip()
        status = _source_status(row, root)
        semantic_key = f"external_processing:{_normalize_external_title(business_summary or title) or external_id}"
        item = items_by_key.get(semantic_key)
        item_status = "ready_for_review" if status in {"found", "source_only"} else "needs_source"
        if item is None:
            item = _item(
                semantic_key,
                title,
                ["external_processing"],
                _split_refs(row.get("technical_scope", "")),
                "external_processing",
                "Внешние обработки и отчеты",
                business_summary,
                status=item_status,
                confidence="medium" if status == "found" else "low",
                coverage_status="unknown",
                migration_decision="needs_customer_decision",
                reviewer_notes="Сформировано из analysis/external-processing.",
            )
            items_by_key[semantic_key] = item
        customization_id = item["customization_id"]
        evidence.append(
            _evidence(
                customization_id,
                "external_processing",
                external_id=external_id,
                external_review_id=row.get("external_review_id", ""),
                external_kind=row.get("source_kind", ""),
                source_status=status,
                source_path=row.get("source_dir", ""),
                review_summary_path=f"analysis/external-processing/review/{external_id}/summary.md" if external_id else "",
                business_summary=business_summary,
                version_relation="primary" if semantic_key not in {link.get("semantic_key", "") for link in links} else "variant",
                affected_bp20_objects=_split_refs(row.get("technical_scope", "")),
            )
        )
        links.append(
            _link(
                customization_id,
                "external_processing",
                external_id,
                "primary_source" if status == "found" else "missing_source_candidate",
                external_review_id=row.get("external_review_id", ""),
                source_status=status,
                semantic_key=semantic_key,
            )
        )
        for slug in _split_refs(row.get("subject_card_links", "")):
            links.append(_link(customization_id, "subject_card", slug, "supporting_context"))
        for gap in _split_refs(row.get("functional_gap_links", "")):
            links.append(_link(customization_id, "functional_gap", gap, "coverage_context"))
    return list(items_by_key.values()), evidence, links


def _build_diff_context(root: Path, designer_report: str = "", v8unpack_root: str = "") -> dict[str, Any]:
    registry_root = repo_path(root, REGISTRY_DIR)
    registry_root.mkdir(parents=True, exist_ok=True)
    diff_rows = _read_csv_rows(repo_path(root, FINAL_DIFF_CSV))
    diff_context = [
        {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "diff_id": row.get("diff_id", ""),
            "path": row.get("path", ""),
            "object_name": row.get("object_name", ""),
            "change_type": row.get("change_type", ""),
            "summary": row.get("summary", ""),
            "source": row.get("source", ""),
        }
        for row in diff_rows
        if row.get("diff_id")
    ]
    write_jsonl(repo_path(root, DIFF_CONTEXT_JSONL), diff_context)
    report_rows, report_meta = parse_designer_report(root, designer_report)
    write_jsonl(repo_path(root, DESIGNER_REPORT_JSONL), report_rows)
    uuid_rows = resolve_v8unpack_uuids(root, v8unpack_root)
    write_jsonl(repo_path(root, UUID_RESOLUTION_JSONL), uuid_rows)
    return {"diff_context_rows": len(diff_context), "designer_report": report_meta, "uuid_resolution_rows": len(uuid_rows)}


def _read_text_with_encoding(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ("utf-16", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def parse_designer_report(root: Path, designer_report: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not designer_report:
        return [], {"status": "unavailable", "path": "", "sha256": "", "encoding": ""}
    path = Path(designer_report)
    if not path.is_absolute():
        path = repo_path(root, designer_report)
    if not path.exists():
        return [], {"status": "missing", "path": designer_report, "sha256": "", "encoding": ""}
    text, encoding = _read_text_with_encoding(path)
    rows: list[dict[str, Any]] = []
    current_object = ""
    for number, line in enumerate(text.splitlines(), 1):
        clean = line.strip()
        object_match = re.search(r"(?:Объект|Object)\s*[:=]\s*(.+)$", clean)
        if object_match:
            current_object = object_match.group(1).strip()
        if "Модуль" in clean or "Различаются" in clean or "Добавлен" in clean or "Удален" in clean:
            rows.append(
                {
                    "schema_version": REGISTRY_SCHEMA_VERSION,
                    "line_number": number,
                    "object_name": current_object,
                    "marker": clean[:240],
                    "section_kind": "bsl_module" if "Модуль" in clean else "metadata",
                }
            )
    return rows, {"status": "found", "path": str(path), "sha256": _file_sha256(path), "encoding": encoding}


def resolve_v8unpack_uuids(root: Path, v8unpack_root: str = "") -> list[dict[str, Any]]:
    if not v8unpack_root:
        v8unpack_root = "analysis/cache/noise/clean-rebase-v8unpack/repo"
    base = Path(v8unpack_root)
    if not base.is_absolute():
        base = repo_path(root, v8unpack_root)
    if not base.exists():
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    uuid_re = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    for path in sorted(base.rglob("*.json")):
        relative = path.relative_to(base).as_posix()
        object_name = ".".join(path.parts[-3:-1]) if len(path.parts) >= 3 else path.stem
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        for uuid in uuid_re.findall(text):
            if uuid in seen:
                continue
            seen.add(uuid)
            rows.append({"schema_version": REGISTRY_SCHEMA_VERSION, "uuid": uuid, "metadata_object": object_name, "source_path": relative})
    return rows


def build_registry(root: Path) -> dict[str, Any]:
    root = root.resolve()
    repo_path(root, REGISTRY_DIR).mkdir(parents=True, exist_ok=True)
    metadata_items, metadata_evidence, metadata_links = _build_custom_metadata_items(root)
    covered_diff_ids = {str(row.get("diff_id") or "") for row in metadata_evidence if row.get("evidence_type") == "diff"}
    diff_items, diff_evidence, diff_links = _build_final_diff_items(root, covered_diff_ids)
    external_items, external_evidence, external_links = _build_external_items(root)
    items = sorted(metadata_items + diff_items + external_items, key=lambda row: row["customization_id"])
    evidence = sorted(metadata_evidence + diff_evidence + external_evidence, key=lambda row: (row["customization_id"], row["evidence_type"], json.dumps(row, ensure_ascii=False, sort_keys=True)))
    links = sorted(metadata_links + diff_links + external_links, key=lambda row: (row["customization_id"], row["target_type"], row["target_id"], row["relation"]))
    write_jsonl(repo_path(root, ITEMS_JSONL), items)
    write_jsonl(repo_path(root, EVIDENCE_JSONL), evidence)
    if not repo_path(root, LINEAGE_JSONL).exists():
        write_jsonl(repo_path(root, LINEAGE_JSONL), [])
    write_jsonl(repo_path(root, LINKS_JSONL), links)
    write_summary(root, items, evidence, links)
    return {"status": "ok", "items": len(items), "evidence": len(evidence), "links": len(links)}


def write_summary(root: Path, items: list[dict[str, Any]], evidence: list[dict[str, Any]], links: list[dict[str, Any]]) -> None:
    by_source: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    for item in items:
        by_status[str(item.get("status"))] += 1
        for source_kind in item.get("source_kinds") or []:
            by_source[str(source_kind)] += 1
    lines = [
        "# Customization Registry",
        "",
        f"- items: {len(items)}",
        f"- evidence rows: {len(evidence)}",
        f"- link rows: {len(links)}",
        "",
        "## Source Kinds",
        "",
        *[f"- {key}: {value}" for key, value in sorted(by_source.items())],
        "",
        "## Statuses",
        "",
        *[f"- {key}: {value}" for key, value in sorted(by_status.items())],
        "",
    ]
    repo_path(root, SUMMARY_MD).write_text("\n".join(lines), encoding="utf-8", newline="\n")


def export_registry(root: Path) -> dict[str, Any]:
    items = [row for _, row in read_jsonl(repo_path(root, ITEMS_JSONL))] if repo_path(root, ITEMS_JSONL).exists() else []
    fieldnames = [
        "customization_id",
        "title",
        "source_kinds",
        "source_bp20_objects",
        "change_kind",
        "business_area",
        "status",
        "confidence",
        "bp30_coverage_status",
        "migration_decision",
        "scope_status",
        "exclusion_reason",
        "business_meaning",
    ]
    csv_rows = []
    for item in items:
        row = dict(item)
        row["source_kinds"] = ";".join(item.get("source_kinds") or [])
        row["source_bp20_objects"] = ";".join(item.get("source_bp20_objects") or [])
        csv_rows.append(row)
    _write_csv(repo_path(root, EXPORT_CSV), csv_rows, fieldnames)
    return {"status": "ok", "rows": len(csv_rows), "path": EXPORT_CSV}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [row for _, row in read_jsonl(path)]


def load_registry_index(root: Path) -> dict[str, Any]:
    items = _load_jsonl(repo_path(root, ITEMS_JSONL))
    evidence = _load_jsonl(repo_path(root, EVIDENCE_JSONL))
    links = _load_jsonl(repo_path(root, LINKS_JSONL))
    by_id = {str(row.get("customization_id")): row for row in items if row.get("customization_id")}
    by_object: dict[str, list[dict[str, Any]]] = defaultdict(list)
    links_by_target: dict[tuple[str, str], list[str]] = defaultdict(list)
    evidence_by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in items:
        for object_name in row.get("source_bp20_objects") or []:
            by_object[str(object_name)].append(row)
    for row in links:
        customization_id = str(row.get("customization_id") or "")
        target_type = str(row.get("target_type") or "")
        target_id = str(row.get("target_id") or "")
        if customization_id and target_type and target_id:
            links_by_target[(target_type, target_id)].append(customization_id)
    for row in evidence:
        evidence_by_item[str(row.get("customization_id") or "")].append(row)
    return {
        "exists": repo_path(root, ITEMS_JSONL).exists(),
        "items": items,
        "by_id": by_id,
        "by_object": by_object,
        "links": links,
        "links_by_target": links_by_target,
        "evidence": evidence,
        "evidence_by_item": evidence_by_item,
    }


def customizations_for_subject(root: Path, subject_card_slug: str, registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    registry = registry or load_registry_index(root)
    ids = sorted(set(registry["links_by_target"].get(("subject_card", subject_card_slug), [])))
    return [registry["by_id"][customization_id] for customization_id in ids if customization_id in registry["by_id"]]


def customizations_for_object(root: Path, object_name: str, registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    registry = registry or load_registry_index(root)
    return list(registry["by_object"].get(object_name, []))


def customization_trace_payload(root: Path, customization_ids: list[str], registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    registry = registry or load_registry_index(root)
    result: list[dict[str, Any]] = []
    for customization_id in sorted(set(customization_ids)):
        item = registry["by_id"].get(customization_id)
        if not item:
            continue
        evidence = registry["evidence_by_item"].get(customization_id, [])
        result.append(
            {
                "customization_id": customization_id,
                "title": item.get("title", ""),
                "source_bp20_objects": item.get("source_bp20_objects") or [],
                "bp30_coverage_status": item.get("bp30_coverage_status", ""),
                "migration_decision": item.get("migration_decision", ""),
                "scope_status": item.get("scope_status", ""),
                "exclusion_reason": item.get("exclusion_reason", ""),
                "evidence_types": sorted({str(row.get("evidence_type") or "") for row in evidence if row.get("evidence_type")}),
            }
        )
    return result


def validate_registry(root: Path) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    required = [ITEMS_JSONL, EVIDENCE_JSONL, LINKS_JSONL, LINEAGE_JSONL, SUMMARY_MD, BUILD_METADATA_JSON]
    for relative in required:
        if not repo_path(root, relative).exists():
            errors.append(f"Missing customization-registry artifact: {relative}")
    items = _load_jsonl(repo_path(root, ITEMS_JSONL))
    evidence = _load_jsonl(repo_path(root, EVIDENCE_JSONL))
    links = _load_jsonl(repo_path(root, LINKS_JSONL))
    lineage = _load_jsonl(repo_path(root, LINEAGE_JSONL))
    item_ids = {str(row.get("customization_id")) for row in items}
    evidence_by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence:
        evidence_by_item[str(row.get("customization_id"))].append(row)
    diff_ids = {row.get("diff_id", "") for row in _read_csv_rows(repo_path(root, FINAL_DIFF_CSV))}
    cmi_rows = _load_jsonl(repo_path(root, CUSTOM_METADATA_JSONL))
    cmi_ids = {str(row.get("item_id")) for row in cmi_rows}
    cmi_keys = {str(row.get("item_key")) for row in cmi_rows}
    external_ids = {
        *(row.get("external_id", "") for row in _read_csv_rows(repo_path(root, EXTERNAL_INVENTORY_CSV))),
        *(row.get("external_id", "") for row in _read_csv_rows(repo_path(root, EXTERNAL_RECONCILIATION_CSV))),
        *(row.get("external_id", "") for row in _read_csv_rows(repo_path(root, EXTERNAL_REVIEW_INDEX_CSV))),
        *(row.get("external_review_id", "") for row in _read_csv_rows(repo_path(root, EXTERNAL_REVIEW_INDEX_CSV))),
    }
    for item in items:
        customization_id = str(item.get("customization_id") or "")
        if not customization_id.startswith("CUS-"):
            errors.append(f"Invalid customization_id: {customization_id}")
        for field in ("title", "source_bp20_objects", "source_kinds", "change_kind", "business_area", "business_meaning", "status", "confidence", "bp30_coverage_status", "migration_decision", "scope_status"):
            if field not in item:
                errors.append(f"{customization_id} lacks required field: {field}")
        if item.get("bp30_coverage_status") not in BP30_COVERAGE_STATUSES:
            errors.append(f"{customization_id} has invalid target-release coverage status: {item.get('bp30_coverage_status')}")
        if item.get("migration_decision") not in MIGRATION_DECISIONS:
            errors.append(f"{customization_id} has invalid migration decision: {item.get('migration_decision')}")
        if item.get("scope_status") not in SCOPE_STATUSES:
            errors.append(f"{customization_id} has invalid scope status: {item.get('scope_status')}")
        if item.get("scope_status") == "excluded_by_customer" and not str(item.get("exclusion_reason") or "").strip():
            errors.append(f"{customization_id} is excluded without customer agreement reason")
        if item.get("scope_status") == "excluded_false_positive" and not str(item.get("exclusion_reason") or "").strip():
            errors.append(f"{customization_id} is excluded as false positive without source-backed reason")
        if item.get("status") in ACTIVE_PUBLICATION_STATUSES and not evidence_by_item.get(customization_id):
            errors.append(f"{customization_id} has no evidence rows")
        if item.get("status") == "approved" and set(item.get("source_kinds") or []) == {"external_processing"}:
            external_evidence = [row for row in evidence_by_item.get(customization_id, []) if row.get("evidence_type") == "external_processing"]
            if not any(row.get("source_status") == "found" for row in external_evidence):
                errors.append(f"{customization_id} external processing item is approved without source-backed evidence")
    for row in evidence:
        customization_id = str(row.get("customization_id") or "")
        if customization_id not in item_ids:
            errors.append(f"Evidence references missing customization_id: {customization_id}")
        if row.get("evidence_type") == "diff" and row.get("diff_id") not in diff_ids:
            errors.append(f"{customization_id} references missing diff_id: {row.get('diff_id')}")
        if row.get("evidence_type") == "custom_metadata":
            item_id = str(row.get("item_id") or "")
            item_key = str(row.get("custom_metadata_item_key") or "")
            if item_id and item_id not in cmi_ids and item_key not in cmi_keys:
                errors.append(f"{customization_id} references stale CMI item: {item_id}")
        if row.get("evidence_type") == "external_processing":
            external_id = str(row.get("external_id") or "")
            if external_id and external_id not in external_ids:
                errors.append(f"{customization_id} references missing external_id: {external_id}")
            if row.get("source_status") == "found" and row.get("source_path") and not repo_path(root, str(row.get("source_path"))).is_dir():
                errors.append(f"{customization_id} external source is marked found but folder is missing: {row.get('source_path')}")
    for row in links:
        if row.get("customization_id") not in item_ids:
            errors.append(f"Link references missing customization_id: {row.get('customization_id')}")
    for row in lineage:
        if row.get("event_type") not in LINEAGE_EVENT_TYPES:
            errors.append(f"Invalid lineage event_type: {row.get('event_type')}")
        if not row.get("source_ids") or not row.get("target_ids"):
            errors.append("Lineage event lacks source_ids or target_ids")
        for customization_id in row.get("source_ids") or []:
            if customization_id not in item_ids:
                errors.append(f"Lineage source_id is missing from registry: {customization_id}")
        for customization_id in row.get("target_ids") or []:
            if customization_id not in item_ids:
                errors.append(f"Lineage target_id is missing from registry: {customization_id}")
    superseded_ids = {
        customization_id
        for row in lineage
        if row.get("event_type") in {"split", "merge", "supersede"}
        for customization_id in (row.get("source_ids") or [])
    }
    for item in items:
        customization_id = str(item.get("customization_id") or "")
        if customization_id in superseded_ids and item.get("status") in ACTIVE_PUBLICATION_STATUSES:
            errors.append(f"{customization_id} is superseded by lineage but still active")
    if not items:
        warnings.append("Customization registry has no items")
    return {"status": "fail" if errors else "ok", "items": len(items), "evidence": len(evidence), "links": len(links), "errors": errors, "warnings": warnings}


def write_build_metadata(root: Path, mode: str, context_result: dict[str, Any] | None = None, build_result: dict[str, Any] | None = None, validation: dict[str, Any] | None = None) -> None:
    previous: dict[str, Any] = {}
    metadata_path = repo_path(root, BUILD_METADATA_JSON)
    if metadata_path.exists():
        try:
            previous = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
    inputs = []
    for relative in (FINAL_DIFF_CSV, CUSTOM_METADATA_JSONL, EXTERNAL_INVENTORY_CSV, EXTERNAL_RECONCILIATION_CSV, EXTERNAL_REVIEW_INDEX_CSV, FUNCTIONAL_GAP_INDEX_CSV):
        path = repo_path(root, relative)
        inputs.append({"path": relative, "exists": path.exists(), "sha256": _file_sha256(path), "size": path.stat().st_size if path.exists() and path.is_file() else 0})
    _write_json(
        metadata_path,
        {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "mode": mode,
            "inputs": inputs,
            "context": context_result if context_result is not None else previous.get("context", {}),
            "build": build_result or {},
            "validation": validation or {},
        },
    )


def bootstrap_registry(root: Path, designer_report: str = "", v8unpack_root: str = "") -> dict[str, Any]:
    missing = [relative for relative in (FINAL_DIFF_CSV, CUSTOM_METADATA_JSONL) if not repo_path(root, relative).exists()]
    if missing:
        repo_path(root, REGISTRY_DIR).mkdir(parents=True, exist_ok=True)
        write_build_metadata(root, "bootstrap_failed", validation={"status": "fail", "missing_prerequisites": missing})
        return {"status": "fail", "missing_prerequisites": missing}
    context_result = _build_diff_context(root, designer_report=designer_report, v8unpack_root=v8unpack_root)
    build_result = build_registry(root)
    export_registry(root)
    write_build_metadata(root, "bootstrap", context_result, build_result, {"status": "pending"})
    validation = validate_registry(root)
    write_build_metadata(root, "bootstrap", context_result, build_result, validation)
    return {"status": validation["status"], "context": context_result, "build": build_result, "validation": validation}


def ensure_empty_artifacts(root: Path) -> None:
    repo_path(root, REGISTRY_DIR).mkdir(parents=True, exist_ok=True)
    for relative in (ITEMS_JSONL, EVIDENCE_JSONL, LINKS_JSONL, LINEAGE_JSONL, DIFF_CONTEXT_JSONL, DESIGNER_REPORT_JSONL, UUID_RESOLUTION_JSONL):
        path = repo_path(root, relative)
        if not path.exists():
            write_jsonl(path, [])
    if not repo_path(root, SUMMARY_MD).exists():
        repo_path(root, SUMMARY_MD).write_text("# Customization Registry\n\n- items: 0\n", encoding="utf-8")
    if not repo_path(root, BUILD_METADATA_JSON).exists():
        write_build_metadata(root, "seed")


def bootstrap_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    ensure_empty_artifacts(root)
    result = bootstrap_registry(root, designer_report=args.designer_report or "", v8unpack_root=args.v8unpack_root or "")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


def context_build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    ensure_empty_artifacts(root)
    result = _build_diff_context(root, designer_report=args.designer_report or "", v8unpack_root=args.v8unpack_root or "")
    write_build_metadata(root, "context-build", context_result=result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    ensure_empty_artifacts(root)
    result = build_registry(root)
    export_result = export_registry(root)
    write_build_metadata(root, "build", build_result={**result, "export": export_result}, validation={"status": "pending"})
    validation = validate_registry(root)
    write_build_metadata(root, "build", build_result={**result, "export": export_result}, validation=validation)
    print(json.dumps({**result, "validation_status": validation["status"]}, ensure_ascii=False, indent=2))
    return 0 if validation["status"] == "ok" else 1


def validate_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    result = validate_registry(root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


def export_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    result = export_registry(root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
