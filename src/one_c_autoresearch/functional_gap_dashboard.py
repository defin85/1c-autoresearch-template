from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .common import repo_path, utc_now_iso
from .functional_gaps import (
    CHECK_STATUS_LABELS,
    CHECK_TYPE_LABELS,
    GAP_TYPE_LABELS,
    READINESS_LABELS,
    file_sha256,
    load_manifest,
    target_release_label,
)
from .review_dashboard import confidence_label, humanize_dashboard_text, status_label


DEFAULT_OUTPUT_DIR = "outputs/functional-gap-dashboard"
FUNCTIONAL_GAP_DASHBOARD_SCHEMA_VERSION = "functional-gap-dashboard/v1"
SCENARIO_COLUMNS = [
    "scenario_id",
    "scenario",
    "status",
    "standard_mechanism",
    "target_object",
    "evidence_ref",
    "gap_or_limit",
    "next_action",
    "confidence",
    "notes",
]
SCENARIO_STATUS_LABELS = {
    "standard_setting": "типовая настройка",
    "standard_process_change": "типовой процесс с изменением процесса",
    "adaptation_required": "требуется адаптация",
    "needs_infobase_data": "нужны данные ИБ",
    "needs_runtime_check": "нужна проверка в ИБ",
    "not_supported": "типовой механизм не найден",
    "": "не указано",
}
FINDING_TYPE_LABELS = {
    "same_object": "одноименный объект",
    "similar_object": "похожий объект",
    "standard_mechanism": "типовой механизм",
    "removed_or_changed_mechanism": "измененный или отсутствующий механизм",
    "needs_runtime_check": "нужна проверка в ИБ",
    "no_match": "совпадение не найдено",
    "": "не указано",
}
MAPPING_TYPE_LABELS = {
    "same_name": "одноименный объект",
    "shared_infrastructure": "инфраструктурное совпадение",
    "no_target_match": "прямого аналога нет",
    "same_object": "одноименный объект",
    "functional_candidate": "функциональный кандидат",
    "semantic_candidate": "смысловой кандидат",
    "manual-functional-equivalence": "ручная функциональная эквивалентность",
    "no_direct_mapping": "прямого сопоставления нет",
    "": "не указано",
}
OBJECT_ROLE_LABELS = {
    "core_source_object": "ядро разрыва",
    "supporting_standard_object": "типовая опорная часть",
    "standard_target_object": "типовой объект целевого релиза",
    "target_candidate_object": "кандидат целевого механизма",
    "noise_or_infrastructure": "технический след",
    "": "не указано",
}
FUNCTIONAL_RELEVANCE_LABELS = {
    "direct_standard_support": "прямое типовое покрытие",
    "candidate_only": "только кандидат",
    "gap_driver": "формирует разрыв",
    "technical_noise": "технический шум",
    "": "не указано",
}
BLOCKING_CHECK_STATUSES = {"open", "blocked"}
UNSAFE_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _trimmed(value: Any) -> str:
    return _text(value).strip()


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _count_values(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(value or "not_set" for value in values).items()))


def _split_multi(value: str) -> list[str]:
    parts = re.split(r"[;\n]+", value or "")
    return [part.strip() for part in parts if part.strip()]


def _safe_repo_relative(root: Path, relative: str) -> str:
    value = _trimmed(relative)
    if not value or value.startswith(("/", "\\")) or UNSAFE_SCHEME_RE.match(value):
        return ""
    path_part, separator, fragment = value.partition("#")
    path_part = path_part.replace("\\", "/").strip()
    if not path_part or UNSAFE_SCHEME_RE.match(path_part):
        return ""
    try:
        target = repo_path(root, path_part).resolve()
        root_resolved = root.resolve()
        repo_relative = target.relative_to(root_resolved).as_posix()
    except ValueError:
        return ""
    suffix = f"{separator}{fragment}" if separator else ""
    return f"{repo_relative}{suffix}"


def _href_from_repo_relative(relative: str) -> str:
    if not relative:
        return ""
    path_part, separator, fragment = relative.partition("#")
    if path_part.startswith("outputs/"):
        href = "../" + path_part.removeprefix("outputs/")
    else:
        href = "../" + path_part
    return href + (f"{separator}{fragment}" if separator else "")


def _browser_href(root: Path, output_dir: Path, relative: str) -> str:
    safe = _safe_repo_relative(root, relative)
    if not safe:
        return ""
    path_part, separator, fragment = safe.partition("#")
    target = repo_path(root, path_part).resolve()
    href = os.path.relpath(target, output_dir.resolve()).replace(os.sep, "/")
    return href + (f"{separator}{fragment}" if separator else "")


def _artifact_href(root: Path, relative: str) -> str:
    safe = _safe_repo_relative(root, relative)
    return _href_from_repo_relative(safe)


def _fingerprint(root: Path, relative: str, *, required: bool = False) -> dict[str, Any]:
    safe = _safe_repo_relative(root, relative)
    result: dict[str, Any] = {
        "path": safe or _trimmed(relative),
        "exists": False,
        "size": 0,
        "sha256": "",
        "required": required,
    }
    if not safe:
        result["warning"] = "unsafe_path"
        return result
    path = repo_path(root, safe)
    if path.exists() and path.is_file():
        result.update({"exists": True, "size": path.stat().st_size, "sha256": file_sha256(path)})
    return result


def _read_json_file(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Не удалось прочитать {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Некорректный JSON в {label}: ожидается объект")
    return value


def _read_csv_rows(path: Path, *, required_columns: list[str] | None = None, label: str = "") -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = list(reader.fieldnames or [])
            if required_columns:
                missing = [column for column in required_columns if column not in fieldnames]
                if missing:
                    raise ValueError(f"нет обязательных колонок: {', '.join(missing)}")
            rows = [dict(row) for row in reader if any((value or "").strip() for value in row.values())]
    except Exception as exc:
        source = label or path.as_posix()
        if isinstance(exc, ValueError):
            raise ValueError(f"Некорректный CSV {source}: {exc}") from exc
        raise ValueError(f"Не удалось прочитать CSV {source}: {exc}") from exc
    return rows


def _read_optional_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return _read_csv_rows(path)


def _source_metadata_labels(root: Path) -> dict[str, str]:
    path = repo_path(root, "analysis/custom-metadata/index.csv")
    if not path.exists():
        return {}
    labels: dict[str, str] = {}
    priority = {"added": 3, "modified": 2, "removed": 1}
    current_priority: dict[str, int] = {}
    for row in _read_csv_rows(path):
        if row.get("status") != "non_typical":
            continue
        name = _trimmed(row.get("metadata_full_name"))
        change = _trimmed(row.get("change_type"))
        if not name or change not in priority:
            continue
        if priority[change] <= current_priority.get(name, 0):
            continue
        current_priority[name] = priority[change]
        labels[name] = {
            "added": "добавлен в доработанном источнике",
            "modified": "изменен в доработанном источнике",
            "removed": "удален из доработанного источника",
        }[change]
    return labels


def _source_change_label(objects: str, labels: dict[str, str]) -> str:
    values = [value for value in _split_multi(objects) if value and value != "не найден"]
    if not values:
        return "исходный объект не указан"
    result = [labels.get(value, "не найден в нетиповых метаданных") for value in values]
    return "; ".join(dict.fromkeys(result))


def _normalize_row(row: dict[str, str], *, status_labels: dict[str, str] | None = None) -> dict[str, str]:
    normalized = {str(key): humanize_dashboard_text(value) for key, value in row.items()}
    status = _trimmed(row.get("status", ""))
    confidence = _trimmed(row.get("confidence", ""))
    if "status" in row:
        normalized["status"] = status
        normalized["status_label"] = (status_labels or {}).get(status, status_label(status))
    if "confidence" in row:
        normalized["confidence"] = confidence
        normalized["confidence_label"] = confidence_label(confidence)
    return normalized


def _normalize_scenarios(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    scenarios: list[dict[str, str]] = []
    for row in rows:
        normalized = {column: humanize_dashboard_text(row.get(column, "")) for column in SCENARIO_COLUMNS}
        status = _trimmed(row.get("status", ""))
        confidence = _trimmed(row.get("confidence", ""))
        normalized["status"] = status
        normalized["status_label"] = SCENARIO_STATUS_LABELS.get(status, status or "не указано")
        normalized["confidence"] = confidence
        normalized["confidence_label"] = confidence_label(confidence)
        normalized["target_objects"] = _split_multi(row.get("target_object", ""))
        scenarios.append(normalized)
    return scenarios


def _attach_scenario_sources(scenarios: list[dict[str, str]], mappings: list[dict[str, str]]) -> list[dict[str, str]]:
    by_scenario: dict[str, list[dict[str, str]]] = {}
    for mapping in mappings:
        scenario_id = _trimmed(mapping.get("scenario_id"))
        if scenario_id:
            by_scenario.setdefault(scenario_id, []).append(mapping)
    for scenario in scenarios:
        scenario_mappings = by_scenario.get(_trimmed(scenario.get("scenario_id")), [])
        source_objects = sorted({mapping.get("source_object", "").strip() for mapping in scenario_mappings if mapping.get("source_object", "").strip()})
        source_paths = sorted({mapping.get("source_path", "").strip() for mapping in scenario_mappings if mapping.get("source_path", "").strip()})
        mapping_ids = [mapping.get("mapping_id", "").strip() for mapping in scenario_mappings if mapping.get("mapping_id", "").strip()]
        if source_objects:
            scenario["source_object"] = ";".join(source_objects)
        if source_paths:
            scenario["source_path"] = ";".join(source_paths)
        if mapping_ids:
            scenario["mapping_ids"] = ";".join(mapping_ids)
    return scenarios


def _normalize_checks(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    for row in rows:
        normalized = _normalize_row(row, status_labels=CHECK_STATUS_LABELS)
        check_type = _trimmed(row.get("check_type", ""))
        normalized["check_type"] = check_type
        normalized["check_type_label"] = CHECK_TYPE_LABELS.get(check_type, check_type or "не указано")
        checks.append(normalized)
    return checks


def _normalize_hypotheses(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    hypotheses: list[dict[str, str]] = []
    for row in rows:
        normalized = _normalize_row(row)
        gap_type = _trimmed(row.get("gap_type", ""))
        decision = _trimmed(row.get("decision", ""))
        normalized["gap_type"] = gap_type
        normalized["gap_type_label"] = GAP_TYPE_LABELS.get(gap_type, gap_type or "не указано")
        normalized["decision"] = decision
        normalized["decision_label"] = GAP_TYPE_LABELS.get(decision, decision or "не указано")
        hypotheses.append(normalized)
    return hypotheses


def _normalize_findings(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for row in rows:
        normalized = _normalize_row(row)
        finding_type = _trimmed(row.get("finding_type", ""))
        object_role = _trimmed(row.get("object_role", ""))
        functional_relevance = _trimmed(row.get("functional_relevance", ""))
        normalized["finding_type"] = finding_type
        normalized["finding_type_label"] = FINDING_TYPE_LABELS.get(finding_type, finding_type or "не указано")
        normalized["object_role"] = object_role
        normalized["object_role_label"] = OBJECT_ROLE_LABELS.get(object_role, object_role or "не указано")
        normalized["functional_relevance"] = functional_relevance
        normalized["functional_relevance_label"] = FUNCTIONAL_RELEVANCE_LABELS.get(functional_relevance, functional_relevance or "не указано")
        findings.append(normalized)
    return findings


def _normalize_mappings(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    mappings: list[dict[str, str]] = []
    for row in rows:
        normalized = _normalize_row(row)
        mapping_type = _trimmed(row.get("mapping_type", ""))
        object_role = _trimmed(row.get("object_role", ""))
        decision = _trimmed(row.get("decision", ""))
        normalized["mapping_type"] = mapping_type
        normalized["mapping_type_label"] = MAPPING_TYPE_LABELS.get(mapping_type, mapping_type or "не указано")
        normalized["object_role"] = object_role
        normalized["object_role_label"] = OBJECT_ROLE_LABELS.get(object_role, object_role or "не указано")
        normalized["decision"] = decision
        normalized["decision_label"] = GAP_TYPE_LABELS.get(decision, decision or "не указано")
        mappings.append(normalized)
    return mappings


def _attach_finding_sources(findings: list[dict[str, str]], mappings: list[dict[str, str]]) -> list[dict[str, str]]:
    mapping_by_suffix = {
        mapping.get("mapping_id", "").removeprefix("FGM-"): mapping
        for mapping in mappings
        if mapping.get("mapping_id", "").startswith("FGM-")
    }
    for finding in findings:
        mapping = mapping_by_suffix.get(finding.get("finding_id", "").removeprefix("FGF-"))
        if not mapping:
            continue
        finding["source_object"] = mapping.get("source_object", "")
        finding["source_path"] = mapping.get("source_path", "")
        finding["mapping_id"] = mapping.get("mapping_id", "")
    return findings


def _filter_open_questions(rows: list[dict[str, str]], slug: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for row in rows:
        if _trimmed(row.get("subject_card_slug", "")) == slug:
            result.append(_normalize_row(row))
    return result


def _card_risk_level(card: dict[str, Any], scenario_statuses: dict[str, int]) -> str:
    if card["open_blocking_checks_count"] > 0 or scenario_statuses.get("adaptation_required", 0):
        return "risk"
    if scenario_statuses.get("needs_infobase_data", 0) or card["open_checks_count"] > 0:
        return "warn"
    if scenario_statuses and not set(scenario_statuses) - {"standard_setting", "standard_process_change"}:
        return "ok"
    if card.get("selected_decision") in {"replace_by_standard", "split", "data_migration", "business_decision"}:
        return "warn"
    if card.get("selected_decision") in {"adapt", "preserve"}:
        return "risk"
    return "neutral"


def _card_target_objects(
    scenarios: list[dict[str, Any]],
    target_findings: list[dict[str, str]],
    object_mappings: list[dict[str, str]],
) -> list[str]:
    values: list[str] = []
    for scenario in scenarios:
        values.extend([_text(item) for item in scenario.get("target_objects", [])])
    for row in target_findings:
        values.extend(_split_multi(row.get("target_object", "")))
    for row in object_mappings:
        values.extend(_split_multi(row.get("target_object", "")))
    return list(dict.fromkeys(value for value in values if value))


def _card_confidences(
    scenarios: list[dict[str, str]],
    hypotheses: list[dict[str, str]],
    target_findings: list[dict[str, str]],
    object_mappings: list[dict[str, str]],
) -> list[str]:
    values: list[str] = []
    for rows in (scenarios, hypotheses, target_findings, object_mappings):
        values.extend(_trimmed(row.get("confidence", "")) for row in rows if _trimmed(row.get("confidence", "")))
    return sorted(dict.fromkeys(values))


def _load_card(
    root: Path,
    output_dir: Path,
    raw_card: dict[str, Any],
    open_questions: list[dict[str, str]],
    source_metadata_labels: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    slug = _trimmed(raw_card.get("subject_card_slug", ""))
    if not slug:
        raise ValueError("outputs/functional-gap-map.json содержит карточку без subject_card_slug")
    card_dir = repo_path(root, f"analysis/functional-gaps/cards/{slug}")
    warnings: list[dict[str, Any]] = []
    source_artifacts: list[dict[str, Any]] = []
    artifact_paths = {
        "gap_card": _trimmed(raw_card.get("gap_card_path")) or f"analysis/functional-gaps/cards/{slug}/gap-card.json",
        "review": _trimmed(raw_card.get("review_path")) or f"analysis/functional-gaps/cards/{slug}/review.md",
        "hypotheses": f"analysis/functional-gaps/cards/{slug}/hypotheses.csv",
        "checks": f"analysis/functional-gaps/cards/{slug}/checks.csv",
        "target_findings": f"analysis/functional-gaps/cards/{slug}/target-findings.csv",
        "object_mapping": f"analysis/functional-gaps/cards/{slug}/object-mapping.csv",
        "functional_equivalence": f"analysis/functional-gaps/cards/{slug}/functional-equivalence.csv",
    }
    source_artifacts.extend(
        [
            _fingerprint(root, artifact_paths["gap_card"], required=True),
            _fingerprint(root, artifact_paths["review"]),
            _fingerprint(root, artifact_paths["hypotheses"]),
            _fingerprint(root, artifact_paths["checks"]),
            _fingerprint(root, artifact_paths["target_findings"]),
            _fingerprint(root, artifact_paths["object_mapping"]),
            _fingerprint(root, artifact_paths["functional_equivalence"]),
        ]
    )

    gap_card_path = repo_path(root, artifact_paths["gap_card"])
    gap_payload: dict[str, Any] = {}
    if gap_card_path.exists():
        gap_payload = _read_json_file(gap_card_path, f"{slug}/gap-card.json")
    else:
        warnings.append(
            {
                "card": slug,
                "path": artifact_paths["gap_card"],
                "message": "Не найден gap-card.json; используются данные из outputs/functional-gap-map.json.",
            }
        )

    scenario_path = card_dir / "functional-equivalence.csv"
    scenario_matrix_missing = not scenario_path.exists()
    scenarios: list[dict[str, str]] = []
    if scenario_matrix_missing:
        warnings.append(
            {
                "card": slug,
                "path": artifact_paths["functional_equivalence"],
                "message": "functional-equivalence.csv не найден; сценарная матрица для карточки еще не собрана.",
            }
        )
    else:
        scenario_rows = _read_csv_rows(
            scenario_path,
            required_columns=SCENARIO_COLUMNS,
            label=f"{slug}/functional-equivalence.csv",
        )
        scenarios = _normalize_scenarios(scenario_rows)

    hypotheses = _normalize_hypotheses(_read_optional_csv(card_dir / "hypotheses.csv"))
    checks = _normalize_checks(_read_optional_csv(card_dir / "checks.csv"))
    target_findings = _normalize_findings(_read_optional_csv(card_dir / "target-findings.csv"))
    object_mappings = _normalize_mappings(_read_optional_csv(card_dir / "object-mapping.csv"))
    for row in object_mappings:
        row["source_change_label"] = _source_change_label(row.get("source_object", ""), source_metadata_labels)
    scenarios = _attach_scenario_sources(scenarios, object_mappings)
    for row in scenarios:
        row["source_change_label"] = _source_change_label(row.get("source_object", ""), source_metadata_labels)
    target_findings = _attach_finding_sources(target_findings, object_mappings)
    card_open_questions = _filter_open_questions(open_questions, slug)
    scenario_statuses = _count_values([row.get("status", "") for row in scenarios])
    decision = _trimmed(raw_card.get("selected_decision") or gap_payload.get("selected_decision") or "undecided")
    if not decision:
        decision = "undecided"
    status = _trimmed(raw_card.get("status") or gap_payload.get("status") or "")
    readiness = _trimmed(raw_card.get("gap_readiness") or gap_payload.get("gap_readiness") or "")
    title = humanize_dashboard_text(raw_card.get("title") or gap_payload.get("title") or slug)
    target_objects = _card_target_objects(scenarios, target_findings, object_mappings)
    card = {
        "subject_card_slug": slug,
        "slug": slug,
        "title": title,
        "status": status,
        "status_label": status_label(status),
        "gap_readiness": readiness,
        "gap_readiness_label": READINESS_LABELS.get(readiness, status_label(readiness)),
        "selected_decision": decision,
        "selected_decision_label": GAP_TYPE_LABELS.get(decision, decision or "не указано"),
        "selected_decision_summary": humanize_dashboard_text(
            raw_card.get("selected_decision_summary") or gap_payload.get("selected_decision_summary") or ""
        ),
        "target_release": _trimmed(raw_card.get("target_release") or gap_payload.get("target_release") or ""),
        "manual_decision_locked": bool(raw_card.get("manual_decision_locked") or gap_payload.get("manual_decision_locked")),
        "hypotheses_count": _int_value(raw_card.get("hypotheses_count")) or len(hypotheses),
        "checks_count": _int_value(raw_card.get("checks_count")) or len(checks),
        "open_checks_count": _int_value(raw_card.get("open_checks_count")) or sum(1 for row in checks if row.get("status") in BLOCKING_CHECK_STATUSES),
        "open_blocking_checks_count": _int_value(raw_card.get("open_blocking_checks_count"))
        or sum(1 for row in checks if row.get("status") in BLOCKING_CHECK_STATUSES and _trimmed(row.get("blocking")).lower() == "true"),
        "target_findings_count": _int_value(raw_card.get("target_findings_count")) or len(target_findings),
        "object_mappings_count": _int_value(raw_card.get("object_mappings_count")) or len(object_mappings),
        "scenario_count": len(scenarios),
        "scenario_statuses": scenario_statuses,
        "scenario_matrix_missing": scenario_matrix_missing,
        "scenarios": scenarios,
        "hypotheses": hypotheses,
        "checks": checks,
        "target_findings": target_findings,
        "object_mappings": object_mappings,
        "open_questions": card_open_questions,
        "target_objects": target_objects,
        "confidences": _card_confidences(scenarios, hypotheses, target_findings, object_mappings),
        "source_warnings": warnings,
        "artifact_paths": artifact_paths,
        "artifact_links": {
            "gap_card_href": _artifact_href(root, artifact_paths["gap_card"]),
            "gap_card_browser_href": _browser_href(root, output_dir, artifact_paths["gap_card"]),
            "review_href": _artifact_href(root, artifact_paths["review"]),
            "review_browser_href": _browser_href(root, output_dir, artifact_paths["review"]),
            "functional_equivalence_href": _artifact_href(root, artifact_paths["functional_equivalence"]),
            "functional_equivalence_browser_href": _browser_href(root, output_dir, artifact_paths["functional_equivalence"]),
        },
    }
    card["risk_level"] = _card_risk_level(card, scenario_statuses)
    return card, source_artifacts, warnings


def _filter_options(entries: dict[str, str], values: list[str]) -> list[dict[str, str]]:
    result = []
    for value in sorted(dict.fromkeys(item for item in values if item)):
        result.append({"value": value, "label": entries.get(value, value)})
    return result


def build_functional_gap_dashboard_snapshot(root: Path, output_dir: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    output_dir = (output_dir or repo_path(root, DEFAULT_OUTPUT_DIR)).resolve()
    map_path = repo_path(root, "outputs/functional-gap-map.json")
    if not map_path.exists():
        raise RuntimeError(
            "Не найден outputs/functional-gap-map.json. "
            "Сначала выполните `python -m one_c_autoresearch functional-gap map-build`."
        )
    map_payload = _read_json_file(map_path, "outputs/functional-gap-map.json")
    raw_cards = map_payload.get("cards") or []
    if not isinstance(raw_cards, list):
        raise ValueError("Некорректный outputs/functional-gap-map.json: поле cards должно быть списком")

    open_questions_path = repo_path(root, "analysis/functional-gaps/open-questions.csv")
    open_questions = _read_optional_csv(open_questions_path)
    source_metadata_labels = _source_metadata_labels(root)
    source_artifacts = [_fingerprint(root, "outputs/functional-gap-map.json", required=True)]
    if open_questions_path.exists():
        source_artifacts.append(_fingerprint(root, "analysis/functional-gaps/open-questions.csv"))

    cards: list[dict[str, Any]] = []
    source_warnings: list[dict[str, Any]] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            raise ValueError("Некорректный outputs/functional-gap-map.json: карточка должна быть объектом")
        card, card_artifacts, card_warnings = _load_card(root, output_dir, raw_card, open_questions, source_metadata_labels)
        cards.append(card)
        source_artifacts.extend(card_artifacts)
        source_warnings.extend(card_warnings)

    scenario_rows = [scenario for card in cards for scenario in card["scenarios"]]
    checks = [check for card in cards for check in card["checks"]]
    summary = {
        "cards_total": len(cards),
        "ready_for_review": sum(1 for card in cards if card.get("status") == "ready_for_review"),
        "reviewed": sum(1 for card in cards if card.get("status") == "reviewed"),
        "open_checks": sum(_int_value(card.get("open_checks_count")) for card in cards),
        "open_blocking_checks": sum(_int_value(card.get("open_blocking_checks_count")) for card in cards),
        "decisions": _count_values([card.get("selected_decision", "") for card in cards]),
        "statuses": _count_values([card.get("status", "") for card in cards]),
        "readiness": _count_values([card.get("gap_readiness", "") for card in cards]),
        "scenario_statuses": _count_values([row.get("status", "") for row in scenario_rows]),
        "checks_by_status": _count_values([row.get("status", "") for row in checks]),
        "confidence": _count_values([row.get("confidence", "") for row in scenario_rows if row.get("confidence", "")]),
        "cards_missing_scenario_matrix": sum(1 for card in cards if card.get("scenario_matrix_missing")),
    }
    manifest = load_manifest(root)
    target_release = _trimmed(map_payload.get("target_release")) or target_release_label(manifest)
    return {
        "schema_version": FUNCTIONAL_GAP_DASHBOARD_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "target_release": target_release,
        "build": {
            "source": "outputs/functional-gap-map.json",
            "read_only": True,
            "decision_sources": [
                "outputs/functional-gap-map.json",
                "analysis/functional-gaps/cards/<slug>/functional-equivalence.csv",
                "analysis/functional-gaps/cards/<slug>/*.csv",
            ],
        },
        "summary": summary,
        "source_artifacts": source_artifacts,
        "source_warnings": source_warnings,
        "cards": cards,
        "filters": {
            "selected_decisions": _filter_options(GAP_TYPE_LABELS, [card.get("selected_decision", "") for card in cards]),
            "card_statuses": _filter_options({}, [card.get("status", "") for card in cards]),
            "readiness": _filter_options(READINESS_LABELS, [card.get("gap_readiness", "") for card in cards]),
            "scenario_statuses": _filter_options(SCENARIO_STATUS_LABELS, [row.get("status", "") for row in scenario_rows]),
            "confidences": _filter_options({"high": "высокая", "medium": "средняя", "low": "низкая"}, [value for card in cards for value in card.get("confidences", [])]),
            "target_objects": [{"value": value, "label": value} for value in sorted(dict.fromkeys(value for card in cards for value in card.get("target_objects", [])))],
        },
        "artifact_links": {
            "functional_gap_map_href": _artifact_href(root, "outputs/functional-gap-map.json"),
            "functional_gap_map_browser_href": _browser_href(root, output_dir, "outputs/functional-gap-map.json"),
            "open_questions_href": _artifact_href(root, "analysis/functional-gaps/open-questions.csv"),
            "open_questions_browser_href": _browser_href(root, output_dir, "analysis/functional-gaps/open-questions.csv"),
        },
    }


def _safe_json_for_script(data: dict[str, Any]) -> str:
    return (
        json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def dashboard_html(data: dict[str, Any]) -> str:
    json_data = _safe_json_for_script(data)
    template = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Карта функциональных разрывов</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-soft: #f1f5f9;
      --text: #111827;
      --muted: #667085;
      --border: #d9dee7;
      --accent: #14532d;
      --accent-soft: #dcfce7;
      --warn: #92400e;
      --warn-soft: #fef3c7;
      --danger: #991b1b;
      --danger-soft: #fee2e2;
      --info: #1e3a8a;
      --info-soft: #dbeafe;
      --neutral: #475467;
      --neutral-soft: #eef2f6;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }
    a { color: var(--info); text-decoration: none; }
    a:hover { text-decoration: underline; }
    main { max-width: 1680px; margin: 0 auto; padding: 22px; }
    h1 { margin: 0 0 4px; font-size: 26px; line-height: 1.2; }
    h2 { margin: 0 0 10px; font-size: 20px; }
    h3 { margin: 0 0 8px; font-size: 16px; }
    .topbar {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      border-bottom: 1px solid var(--border);
      padding-bottom: 16px;
      margin-bottom: 16px;
    }
    .subtle { color: var(--muted); }
    .badges { display: flex; flex-wrap: wrap; gap: 6px; }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      border-radius: 999px;
      padding: 2px 8px;
      border: 1px solid var(--border);
      background: var(--panel-soft);
      color: var(--text);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .badge.ok { background: var(--accent-soft); color: var(--accent); border-color: #86efac; }
    .badge.warn { background: var(--warn-soft); color: var(--warn); border-color: #fcd34d; }
    .badge.risk { background: var(--danger-soft); color: var(--danger); border-color: #fca5a5; }
    .badge.info { background: var(--info-soft); color: var(--info); border-color: #93c5fd; }
    .badge.neutral { background: var(--neutral-soft); color: var(--neutral); }
    .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 16px; }
    .metric, .panel, .card-row {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
    }
    .metric { padding: 13px; }
    .metric strong { display: block; font-size: 23px; line-height: 1.1; }
    .metric span { color: var(--muted); font-size: 12px; }
    .toolbar {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 10px;
      margin: 14px 0;
    }
    input, select {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 9px 10px;
      background: #ffffff;
      color: var(--text);
      font: inherit;
    }
    button {
      width: 100%;
      text-align: left;
      border: 0;
      background: transparent;
      color: inherit;
      font: inherit;
      cursor: pointer;
      padding: 0;
    }
    .node-title { font-weight: 700; overflow-wrap: anywhere; }
    .panel { padding: 14px; min-width: 0; overflow-wrap: anywhere; }
    .card-strip { margin-bottom: 14px; }
    .card-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
    .card-row { padding: 12px; border-left: 4px solid var(--neutral); min-height: 118px; }
    .card-row.ok { border-left-color: var(--accent); }
    .card-row.warn { border-left-color: var(--warn); }
    .card-row.risk { border-left-color: var(--danger); }
    .card-row.selected { outline: 2px solid #93c5fd; }
    .detail-panel { margin-bottom: 18px; }
    .transition-summary {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
      margin: 14px 0;
    }
    .transition-item {
      background: var(--panel-soft);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 11px;
      min-width: 0;
    }
    .transition-item h3 { margin: 0 0 6px; color: var(--muted); font-size: 13px; }
    .transition-item strong { display: block; overflow-wrap: anywhere; }
    .transition-item p { margin: 6px 0 0; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .summary-text {
      white-space: pre-wrap;
      max-width: 1120px;
      font-size: 15px;
      line-height: 1.55;
    }
    .section { margin-top: 14px; }
    .table-wrap { overflow: auto; border: 1px solid var(--border); border-radius: 8px; background: #fff; }
    table { width: 100%; border-collapse: collapse; min-width: 780px; }
    th, td { padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
    th { background: var(--panel-soft); color: var(--muted); font-weight: 600; font-size: 12px; }
    tr:last-child td { border-bottom: 0; }
    .empty { color: var(--muted); font-style: italic; }
    .warning { background: var(--warn-soft); border-color: #fcd34d; color: var(--warn); }
    .hidden-note { margin-bottom: 12px; }
    @media (max-width: 1050px) {
      main { padding: 16px; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <script id="functional-gap-dashboard-data" type="application/json">__FUNCTIONAL_GAP_DASHBOARD_DATA__</script>
  <main>
    <header class="topbar">
      <div>
        <h1>Карта функциональных разрывов</h1>
        <div class="subtle" id="build-caption"></div>
      </div>
      <div class="badges">
        <span class="badge info">Статичный HTML</span>
        <span class="badge info">Источник: gap-слой</span>
        <a class="badge" id="map-link" href="#">outputs/functional-gap-map.json</a>
      </div>
    </header>

    <section class="metrics" id="summary-metrics"></section>

    <section class="panel">
      <h2>Фильтры</h2>
      <div class="toolbar">
        <select id="selected-decision-filter"><option value="">Все решения</option></select>
        <select id="card-status-filter"><option value="">Все статусы карточек</option></select>
        <select id="readiness-filter"><option value="">Любая готовность</option></select>
        <select id="scenario-status-filter"><option value="">Все сценарные статусы</option></select>
        <select id="open-checks-filter">
          <option value="">Все проверки</option>
          <option value="open">Есть открытые проверки</option>
          <option value="blocking">Есть блокирующие проверки</option>
          <option value="none">Без открытых проверок</option>
        </select>
        <select id="confidence-filter"><option value="">Любая достоверность</option></select>
        <select id="target-object-filter"><option value="">Все целевые объекты</option></select>
        <input id="text-filter" type="search" placeholder="Поиск по карточке, сценарию, объекту, действию">
      </div>
    </section>

    <section class="panel card-strip">
      <h2>Gap-карточки</h2>
      <div id="card-list" class="card-list"></div>
    </section>

    <section class="panel detail-panel">
      <div id="selected-card-hidden" class="hidden-note warning panel" hidden>Выбранная карточка скрыта текущими фильтрами.</div>
      <div id="card-detail"></div>
    </section>
  </main>
  <script>
    const snapshot = JSON.parse(document.getElementById("functional-gap-dashboard-data").textContent);
    const cards = snapshot.cards || [];
    let selectedSlug = cards[0] ? cards[0].subject_card_slug : "";
    const byId = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\\\"":"&quot;","'":"&#39;"}[char]));
    const badgeClass = (risk) => risk === "ok" ? "ok" : risk === "warn" ? "warn" : risk === "risk" ? "risk" : "neutral";
    const rowsTable = (rows, columns) => {
      if (!rows || rows.length === 0) return '<div class="empty">Нет строк.</div>';
      const head = columns.map(([key, label]) => `<th>${esc(label)}</th>`).join("");
      const body = rows.map((row) => `<tr>${columns.map(([key]) => `<td>${esc(row[key] || "")}</td>`).join("")}</tr>`).join("");
      return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    };
    const scalarBadges = (items, labels = {}) => Object.entries(items || {}).map(([key, value]) => `<span class="badge">${esc(labels[key] || key)}: ${value}</span>`).join(" ");

    function fillSelect(id, options) {
      const select = byId(id);
      select.innerHTML += (options || []).map((item) => `<option value="${esc(item.value)}">${esc(item.label || item.value)}</option>`).join("");
    }

    function renderHeader() {
      byId("build-caption").textContent = `Целевой релиз: ${snapshot.target_release || "не указан"} · сформировано: ${snapshot.generated_at || ""}`;
      const href = (snapshot.artifact_links || {}).functional_gap_map_browser_href || (snapshot.artifact_links || {}).functional_gap_map_href || "#";
      byId("map-link").setAttribute("href", href);
    }

    function renderMetrics() {
      const summary = snapshot.summary || {};
      const metrics = [
        ["Gap-карточки", summary.cards_total || cards.length || 0],
        ["Готово к ревью", summary.ready_for_review || 0],
        ["Отревьюировано", summary.reviewed || 0],
        ["Открытые проверки", summary.open_checks || 0],
        ["Блокирующие проверки", summary.open_blocking_checks || 0],
        ["Без сценарной матрицы", summary.cards_missing_scenario_matrix || 0],
      ];
      byId("summary-metrics").innerHTML = metrics.map(([label, value]) => `<div class="metric"><strong>${value}</strong><span>${esc(label)}</span></div>`).join("");
    }

    function fillFilters() {
      const filters = snapshot.filters || {};
      fillSelect("selected-decision-filter", filters.selected_decisions);
      fillSelect("card-status-filter", filters.card_statuses);
      fillSelect("readiness-filter", filters.readiness);
      fillSelect("scenario-status-filter", filters.scenario_statuses);
      fillSelect("confidence-filter", filters.confidences);
      fillSelect("target-object-filter", filters.target_objects);
    }

    function cardSearchText(card) {
      const scenarioText = (card.scenarios || []).map((row) => [row.scenario, row.status_label, row.standard_mechanism, row.target_object, row.gap_or_limit, row.next_action, row.notes].join(" ")).join(" ");
      return [
        card.subject_card_slug,
        card.title,
        card.status_label,
        card.gap_readiness_label,
        card.selected_decision_label,
        card.selected_decision_summary,
        (card.target_objects || []).join(" "),
        scenarioText,
      ].join(" ").toLowerCase();
    }

    function cardMatches(card) {
      const decision = byId("selected-decision-filter").value;
      const status = byId("card-status-filter").value;
      const readiness = byId("readiness-filter").value;
      const scenarioStatus = byId("scenario-status-filter").value;
      const openChecks = byId("open-checks-filter").value;
      const confidence = byId("confidence-filter").value;
      const targetObject = byId("target-object-filter").value;
      const query = byId("text-filter").value.trim().toLowerCase();
      if (decision && card.selected_decision !== decision) return false;
      if (status && card.status !== status) return false;
      if (readiness && card.gap_readiness !== readiness) return false;
      if (scenarioStatus && !(card.scenarios || []).some((row) => row.status === scenarioStatus)) return false;
      if (openChecks === "open" && !(Number(card.open_checks_count || 0) > 0)) return false;
      if (openChecks === "blocking" && !(Number(card.open_blocking_checks_count || 0) > 0)) return false;
      if (openChecks === "none" && Number(card.open_checks_count || 0) > 0) return false;
      if (confidence && !(card.confidences || []).includes(confidence)) return false;
      if (targetObject && !(card.target_objects || []).includes(targetObject)) return false;
      if (query && !cardSearchText(card).includes(query)) return false;
      return true;
    }

    function filteredCards() {
      return cards.filter(cardMatches);
    }

    function cardRow(card) {
      return `<button type="button" class="card-row ${badgeClass(card.risk_level)} ${card.subject_card_slug === selectedSlug ? "selected" : ""}" data-select-card="${esc(card.subject_card_slug)}">
        <div class="node-title">${esc(card.title)}</div>
        <div class="subtle">${esc(card.subject_card_slug)}</div>
        <div class="badges">
          <span class="badge ${badgeClass(card.risk_level)}">${esc(card.selected_decision_label)}</span>
          <span class="badge">${esc(card.status_label)}</span>
          <span class="badge">открытых проверок: ${Number(card.open_checks_count || 0)}</span>
          <span class="badge">сценариев: ${Number(card.scenario_count || 0)}</span>
        </div>
      </button>`;
    }

    function renderCardList(cardsToRender) {
      byId("card-list").innerHTML = cardsToRender.length ? cardsToRender.map(cardRow).join("") : '<div class="empty">Карточки не найдены.</div>';
    }

    function linkHtml(href, label) {
      return href ? `<a class="badge info" href="${esc(href)}">${esc(label)}</a>` : "";
    }

    function renderCardDetail(cardsToRender) {
      const selected = cards.find((card) => card.subject_card_slug === selectedSlug) || cardsToRender[0] || cards[0];
      if (!selected) {
        byId("card-detail").innerHTML = '<div class="empty">Нет gap-карточек для отображения.</div>';
        byId("selected-card-hidden").hidden = true;
        return;
      }
      selectedSlug = selected.subject_card_slug;
      const hiddenByFilters = !cardsToRender.some((card) => card.subject_card_slug === selected.subject_card_slug);
      byId("selected-card-hidden").hidden = !hiddenByFilters;
      const links = selected.artifact_links || {};
      const scenarioStatusBadges = scalarBadges(selected.scenario_statuses, {});
      const sourceWarnings = (selected.source_warnings || []).map((warning) => `<li>${esc(warning.message || "")}</li>`).join("");
      const targetPreview = (selected.target_objects || []).slice(0, 5).join("; ") || "Механизмы не выделены";
      const readinessBadge = (selected.status === "ready_for_review" && Number(selected.open_checks_count || 0) === 0)
        ? ""
        : `<span class="badge">${esc(selected.gap_readiness_label)}</span>`;
      const scenarioBlock = selected.scenario_matrix_missing
        ? '<div class="panel warning">Сценарная матрица functional-equivalence.csv для карточки еще не собрана.</div>'
        : rowsTable(selected.scenarios, [
            ["scenario_id", "ID"],
            ["scenario", "Сценарий"],
            ["status_label", "Статус"],
            ["source_object", "Объект доработки"],
            ["source_change_label", "Статус исходника"],
            ["target_object", "Аналог в целевом релизе"],
            ["standard_mechanism", "Типовой механизм"],
            ["evidence_ref", "Доказательство"],
            ["gap_or_limit", "Разрыв или ограничение"],
            ["next_action", "Следующее действие"],
            ["confidence_label", "Достоверность"],
            ["notes", "Заметки"],
          ]);
      byId("card-detail").innerHTML = `<article>
        <div class="badges">
          <span class="badge ${badgeClass(selected.risk_level)}">${esc(selected.selected_decision_label)}</span>
          <span class="badge">${esc(selected.status_label)}</span>
          ${readinessBadge}
          <span class="badge">открытых проверок: ${Number(selected.open_checks_count || 0)}</span>
          <span class="badge">блокирующих: ${Number(selected.open_blocking_checks_count || 0)}</span>
        </div>
        <h2>${esc(selected.title)}</h2>
        <div class="subtle">${esc(selected.subject_card_slug)}</div>
        <section class="transition-summary" aria-label="Переход выбранной карточки">
          <div class="transition-item">
            <h3>Текущая доработка</h3>
            <strong>${esc(selected.title)}</strong>
            <p>${esc(selected.subject_card_slug)}</p>
          </div>
          <div class="transition-item">
            <h3>Решение перехода</h3>
            <strong>${esc(selected.selected_decision_label)}</strong>
            <p>${Number(selected.open_checks_count || 0)} открытых проверок</p>
          </div>
          <div class="transition-item">
            <h3>Целевой релиз</h3>
            <strong>${esc(targetPreview)}</strong>
            <p>сценариев: ${Number(selected.scenario_count || 0)}</p>
          </div>
        </section>
        <p class="summary-text">${esc(selected.selected_decision_summary || "Итоговое решение пока не описано.")}</p>
        <div class="badges">
          ${linkHtml(links.gap_card_browser_href || links.gap_card_href, "gap-card.json")}
          ${linkHtml(links.review_browser_href || links.review_href, "review.md")}
          ${linkHtml(links.functional_equivalence_browser_href || links.functional_equivalence_href, "functional-equivalence.csv")}
        </div>
        <section class="section">
          <h3>Статусы сценариев</h3>
          <div class="badges">${scenarioStatusBadges || '<span class="badge neutral">сценарии не собраны</span>'}</div>
        </section>
        <section class="section">
          <h3>Сценарная матрица</h3>
          ${scenarioBlock}
        </section>
        <section class="section">
          <h3>Проверки</h3>
          ${rowsTable(selected.checks, [["check_id","ID"],["check_type_label","Тип"],["status_label","Статус"],["source","Источник"],["question","Вопрос"],["result","Результат"],["blocking","Блокирует"]])}
        </section>
        <section class="section">
          <h3>Открытые вопросы</h3>
          ${rowsTable(selected.open_questions, [["question_id","ID"],["check_id","Проверка"],["question","Вопрос"],["needed_source","Нужный источник"],["blocking","Блокирует"],["status_label","Статус"],["notes","Заметки"]])}
        </section>
        <section class="section">
          <h3>Гипотезы</h3>
          ${rowsTable(selected.hypotheses, [["hypothesis_id","ID"],["gap_type_label","Тип разрыва"],["status_label","Статус"],["confidence_label","Достоверность"],["summary","Вывод"],["next_check","Следующая проверка"],["decision_label","Решение"]])}
        </section>
        <section class="section">
          <h3>Сопоставление объектов</h3>
          ${rowsTable(selected.object_mappings, [["mapping_id","ID"],["scenario_id","Сценарий"],["source_object","Объект доработки"],["source_change_label","Статус исходника"],["target_object","Объект целевого релиза"],["object_role_label","Роль"],["mapping_type_label","Тип"],["coverage_status","Покрытие"],["is_gap_driver","Драйвер разрыва"],["confidence_label","Достоверность"],["decision_label","Решение"],["notes","Заметки"]])}
        </section>
        <section class="section">
          <h3>Механизмы целевого релиза и остаточные разрывы</h3>
          ${rowsTable(selected.target_findings, [["finding_id","ID"],["source_object","Объект доработки"],["finding_type_label","Тип"],["target_object","Объект целевого релиза"],["object_role_label","Роль"],["functional_relevance_label","Значение"],["target_path","Путь"],["confidence_label","Достоверность"],["notes","Заметки"]])}
        </section>
        ${sourceWarnings ? `<section class="section"><h3>Предупреждения источников</h3><ul>${sourceWarnings}</ul></section>` : ""}
      </article>`;
    }

    function renderAll() {
      const visible = filteredCards();
      if (visible.length && !cards.some((card) => card.subject_card_slug === selectedSlug)) {
        selectedSlug = visible[0].subject_card_slug;
      }
      renderCardList(visible);
      renderCardDetail(visible);
    }

    function boot() {
      renderHeader();
      renderMetrics();
      fillFilters();
      renderAll();
      ["selected-decision-filter","card-status-filter","readiness-filter","scenario-status-filter","open-checks-filter","confidence-filter","target-object-filter","text-filter"].forEach((id) => {
        byId(id).addEventListener("input", renderAll);
      });
      document.addEventListener("click", (event) => {
        const target = event.target.closest ? event.target.closest("[data-select-card]") : null;
        if (!target) return;
        selectedSlug = target.dataset.selectCard || "";
        renderAll();
      });
    }
    boot();
  </script>
</body>
</html>
"""
    return template.replace("__FUNCTIONAL_GAP_DASHBOARD_DATA__", json_data)


def build_functional_gap_dashboard(root: Path, output_dir: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    output_dir = (output_dir or repo_path(root, DEFAULT_OUTPUT_DIR)).resolve()
    snapshot = build_functional_gap_dashboard_snapshot(root, output_dir=output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_path = output_dir / "data.json"
    html_path = output_dir / "index.html"
    data_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    html_path.write_text(dashboard_html(snapshot), encoding="utf-8", newline="\n")
    return {
        "status": "ok",
        "root": str(root),
        "output_dir": str(output_dir),
        "html": str(html_path),
        "data": str(data_path),
        "cards": len(snapshot["cards"]),
    }


def build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else repo_path(root, DEFAULT_OUTPUT_DIR)
    result = build_functional_gap_dashboard(root, output_dir)
    print(f"functional_gap_dashboard_html: {result['html']}")
    print(f"functional_gap_dashboard_data: {result['data']}")
    print(f"cards: {result['cards']}")
    return 0
