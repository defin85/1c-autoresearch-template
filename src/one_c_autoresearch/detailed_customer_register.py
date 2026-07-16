from __future__ import annotations

import argparse
import csv
import html
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .autopilot import _excel_column_name, excel_cell_text, validate_minimal_xlsx
from .common import read_jsonl, repo_path, utc_now_iso, write_jsonl
from .customization_registry import customization_trace_payload, customizations_for_object, load_registry_index
from .migration_requirements import load_index as load_migration_requirement_index, requirements_for_customizations
from .customer_register import (
    DECISION_LABELS,
    FORBIDDEN_CUSTOMER_MARKERS,
    METADATA_TYPE_LABELS,
    TYPE_LABELS,
    _compact,
    _join_objects,
)
from .subject_cards import read_csv_rows, split_refs, write_csv_rows


DEFAULT_TZ_WORKBOOK = "inputs/customer-requirements.xlsx"
TZ_INPUT_CSV = "analysis/tz-rework-registry/input.csv"
TZ_CLASSIFICATION_CSV = "analysis/tz-rework-registry/classification.csv"
TZ_OPEN_QUESTIONS_CSV = "analysis/tz-rework-registry/open-questions.csv"
CUSTOM_METADATA_JSONL = "analysis/custom-metadata/index.jsonl"
CUSTOM_METADATA_OBJECT_SUMMARY = "analysis/custom-metadata/object-summary.json"
CUSTOM_METADATA_CSV = "analysis/custom-metadata/index.csv"
CUSTOM_METADATA_REPORTS = "analysis/custom-metadata/reports"
FINAL_DIFF_INVENTORY_CSV = "analysis/indexes/final-diff-inventory.csv"
FINAL_FEATURE_MAP_CSV = "analysis/indexes/final-feature-map.csv"
DETAILED_REGISTER_CSV = "outputs/detailed-customer-customization-register.csv"
DETAILED_REGISTER_MD = "outputs/detailed-customer-customization-register.md"
DETAILED_REGISTER_XLSX = "outputs/detailed-customer-customization-register.xlsx"
DETAILED_REGISTER_TRACE_JSONL = "outputs/detailed-customer-customization-register.trace.jsonl"

DETAILED_REGISTER_COLUMNS = (
    "row_id",
    "number",
    "source",
    "source_registry_number",
    "title",
    "source_type",
    "customization_type",
    "detailed_class",
    "external_kind",
    "external_code",
    "matched_name",
    "key_objects",
    "business_area",
    "business_meaning",
    "transition_decision",
    "target_release_coverage",
    "risk",
    "verification_status",
    "open_question",
    "recommendation",
)
DETAILED_REGISTER_HEADER = ",".join(DETAILED_REGISTER_COLUMNS)
DETAILED_REGISTER_COLUMN_LABELS = {
    "row_id": "Идентификатор строки",
    "number": "№",
    "source": "Источник",
    "source_registry_number": "Номер в источнике",
    "title": "Наименование",
    "source_type": "Тип источника",
    "customization_type": "Тип доработки",
    "detailed_class": "Детальный класс",
    "external_kind": "Вид внешнего элемента",
    "external_code": "Код внешнего элемента",
    "matched_name": "Сопоставленное наименование",
    "key_objects": "Ключевые объекты",
    "business_area": "Бизнес-область",
    "business_meaning": "Бизнес-смысл",
    "transition_decision": "Решение по переходу",
    "target_release_coverage": "Покрытие целевого релиза",
    "risk": "Риск",
    "verification_status": "Статус проверки",
    "open_question": "Открытый вопрос",
    "recommendation": "Рекомендация",
}
DETAILED_REGISTER_OUTPUT_HEADER = [DETAILED_REGISTER_COLUMN_LABELS[column] for column in DETAILED_REGISTER_COLUMNS]
RUSSIAN_METADATA_TYPE_LABELS = {label: technical for technical, label in METADATA_TYPE_LABELS.items()}

DETAIL_FORBIDDEN_CUSTOMER_MARKERS = re.compile(FORBIDDEN_CUSTOMER_MARKERS.pattern + r"|\b(?:CMI|V8D)-\d+\b|\bbf_container_refined\b", re.IGNORECASE)
CUSTOMER_FIELDS = {
    "number",
    "source",
    "source_registry_number",
    "title",
    "source_type",
    "customization_type",
    "detailed_class",
    "external_kind",
    "external_code",
    "matched_name",
    "key_objects",
    "business_area",
    "business_meaning",
    "transition_decision",
    "target_release_coverage",
    "risk",
    "verification_status",
    "open_question",
    "recommendation",
}
REQUIRED_FIELDS = {
    "row_id",
    "number",
    "source",
    "title",
    "customization_type",
    "detailed_class",
    "business_area",
    "business_meaning",
    "transition_decision",
    "target_release_coverage",
    "risk",
    "verification_status",
    "recommendation",
}


def _root(args: argparse.Namespace) -> Path:
    return Path(args.repo_path).resolve() if getattr(args, "repo_path", "") else Path.cwd()


def _workbook_path(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "workbook", "") or DEFAULT_TZ_WORKBOOK)


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    result: list[str] = []
    for item in root.findall("m:si", ns):
        result.append("".join(node.text or "" for node in item.findall(".//m:t", ns)))
    return result


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    cell_type = cell.attrib.get("t", "")
    if cell_type == "s":
        node = cell.find("m:v", ns)
        if node is None or node.text is None:
            return ""
        index = int(node.text)
        return shared_strings[index] if index < len(shared_strings) else ""
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//m:t", ns)).strip()
    node = cell.find("m:v", ns)
    return (node.text or "").strip() if node is not None else ""


def read_tz_workbook(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"XLSX source is not available: {path}")
    with zipfile.ZipFile(path) as zf:
        shared_strings = _xlsx_shared_strings(zf)
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[dict[str, str]] = []
    for row in sheet.findall(".//m:row", ns):
        source_row_number = row.attrib.get("r", "")
        values: dict[str, str] = {}
        for cell in row.findall("m:c", ns):
            ref = cell.attrib.get("r", "")
            column = re.match(r"[A-Z]+", ref)
            if column:
                values[column.group(0)] = _xlsx_cell_value(cell, shared_strings)
        registry_number = values.get("A", "").strip()
        source_name = values.get("B", "").strip()
        if not registry_number.isdigit() or not source_name:
            continue
        rows.append(
            {
                "registry_row_id": f"TZR-{int(registry_number):04d}",
                "source_row_number": source_row_number,
                "registry_number": registry_number,
                "source_name": source_name,
                "source_type": values.get("C", "").strip(),
                "source_path": str(path),
            }
        )
    return rows


def _sync_tz_input(root: Path, workbook_rows: list[dict[str, str]]) -> None:
    write_csv_rows(repo_path(root, TZ_INPUT_CSV), "registry_row_id,source_row_number,registry_number,source_name,source_type,source_path", workbook_rows)


def freshness_errors(root: Path, workbook_path: Path) -> list[str]:
    errors: list[str] = []
    workbook_rows = read_tz_workbook(workbook_path)
    input_rows = read_csv_rows(repo_path(root, TZ_INPUT_CSV))
    if len(workbook_rows) != len(input_rows):
        errors.append(f"{TZ_INPUT_CSV} row count differs from XLSX: expected {len(workbook_rows)}, got {len(input_rows)}")
        return errors
    by_id = {row.get("registry_row_id", ""): row for row in input_rows}
    for workbook_row in workbook_rows:
        row_id = workbook_row["registry_row_id"]
        input_row = by_id.get(row_id)
        if not input_row:
            errors.append(f"{TZ_INPUT_CSV} misses source row: {row_id}")
            continue
        for field in ("registry_number", "source_name", "source_type"):
            if (input_row.get(field) or "").strip() != workbook_row[field]:
                errors.append(f"{TZ_INPUT_CSV} {row_id} {field} differs from XLSX: expected {workbook_row[field]!r}, got {(input_row.get(field) or '').strip()!r}")
    return errors


def _classification_label(value: str, status: str) -> str:
    if status == "needs_review":
        return "Требуется уточнение"
    if value == "mixed":
        return "Внешний элемент + доработка конфигурации"
    if value == "external_tool":
        return "Внешний элемент"
    return _plain_text(value or "Не классифицировано")


def _customization_type(classification: str, status: str) -> str:
    if status == "needs_review":
        return "Требуется уточнение"
    if classification == "mixed":
        return "Внешний элемент + доработка конфигурации"
    return "Внешний элемент"


def _subject_indexes(root: Path) -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, str], dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    subjects = {row.get("slug", ""): row for row in read_csv_rows(repo_path(root, "analysis/subject-cards/registry.csv")) if row.get("slug")}
    object_to_slug: dict[str, str] = {}
    feature_to_slug: dict[str, str] = {}
    for slug, row in subjects.items():
        for obj in split_refs(row.get("primary_objects")):
            object_to_slug.setdefault(obj, slug)
        for feature_id in split_refs(row.get("linked_features") or row.get("owner_feature")):
            feature_to_slug.setdefault(feature_id, slug)
    gaps = {row.get("subject_card_slug", ""): row for row in read_csv_rows(repo_path(root, "analysis/functional-gaps/index.csv")) if row.get("subject_card_slug")}
    features = {row.get("feature_id", ""): row for row in read_csv_rows(repo_path(root, FINAL_FEATURE_MAP_CSV)) if row.get("feature_id")}
    return subjects, object_to_slug, feature_to_slug, gaps, features


def _subject_meaning(subject: dict[str, str]) -> str:
    coverage_scope = subject.get("coverage_scope") if subject.get("coverage_scope") != "bf_container_refined" else ""
    return _plain_text(subject.get("why_separate_card") or coverage_scope or subject.get("title"))


def _feature_meaning(feature_id: str, features: dict[str, dict[str, str]]) -> str:
    feature = features.get(feature_id, {})
    return _plain_text(feature.get("summary") or feature.get("title")) if feature else ""


def _object_business_area(object_name: str) -> str:
    return {
        "Report": "Отчетность",
        "Document": "Документы и операции",
        "Catalog": "НСИ",
        "DataProcessor": "Обработки и сервисные операции",
        "CommonModule": "Общая логика конфигурации",
        "InformationRegister": "Регистры и настройки учета",
        "AccumulationRegister": "Регистры и движения",
        "AccountingRegister": "Регламентированный учет",
        "ChartOfAccounts": "Регламентированный учет",
        "ExchangePlan": "Интеграции",
        "Role": "Права и доступ",
        "ScheduledJob": "Фоновые операции",
        "Enum": "НСИ",
        "CommonForm": "Интерфейс",
        "CommonTemplate": "Печатные формы и макеты",
    }.get(object_name.split(".", 1)[0], "Конфигурационная доработка")


def _object_business_meaning(object_name: str) -> str:
    kind = object_name.split(".", 1)[0]
    name = object_name.split(".", 1)[-1]
    templates = {
        "Report": "Отчет или аналитическая форма для получения пользовательских показателей",
        "Document": "Документ или операция пользовательского процесса",
        "Catalog": "Справочник или настройка нормативно-справочной информации",
        "DataProcessor": "Обработка или сервисная операция для пользователей",
        "CommonModule": "Общая прикладная логика, используемая несколькими сценариями",
        "InformationRegister": "Регистр сведений для хранения настроек, статусов или аналитики",
        "AccumulationRegister": "Регистр накопления для движений и остатков учета",
        "AccountingRegister": "Бухгалтерский регистр для регламентированного учета",
        "ChartOfAccounts": "План счетов или связанная настройка регламентированного учета",
        "ExchangePlan": "План обмена или интеграционный контур",
        "Role": "Роль или настройка доступа пользователей",
        "ScheduledJob": "Фоновое задание или регламентная операция",
        "Enum": "Перечисление для пользовательских состояний или вариантов учета",
        "CommonForm": "Общая форма пользовательского интерфейса",
        "CommonTemplate": "Макет или печатная форма",
    }
    return _plain_text(f"{templates.get(kind, 'Объект основной конфигурации с пользовательской доработкой')}: {name}")


def _external_business_area(source_type: str, external_kind: str) -> str:
    value = f"{source_type} {external_kind}".lower()
    if "отчет" in value:
        return "Отчетность"
    if "печат" in value or "форма" in value:
        return "Печатные формы и макеты"
    if "заполн" in value:
        return "Заполнение документов"
    if "обработ" in value:
        return "Обработки и сервисные операции"
    return "Внешние элементы"


def _external_business_meaning(title: str, source_type: str, external_kind: str) -> str:
    kind = source_type or external_kind or "внешний элемент"
    return _plain_text(f"{kind}: {title}. Назначение зафиксировано в реестре ТЗ; точная логика переноса требует внешнего файла или подтверждения владельца процесса.")


def _enrichment(
    slug: str,
    subjects: dict[str, dict[str, str]],
    gaps: dict[str, dict[str, str]],
    default_recommendation: str,
    fallback_area: str,
    fallback_meaning: str,
) -> dict[str, str]:
    subject = subjects.get(slug, {})
    gap = gaps.get(slug, {})
    decision = gap.get("selected_decision") or "business_decision"
    readiness = gap.get("gap_readiness") or "not_checked"
    open_checks = int(gap.get("open_checks_count") or 0) if str(gap.get("open_checks_count") or "").isdigit() else 0
    if not subject:
        return {
            "business_area": fallback_area,
            "business_meaning": fallback_meaning,
            "transition_decision": "Требуется уточнение",
            "target_release_coverage": "Не оценивалось по целевому релизу",
            "risk": "средний",
            "verification_status": "Бизнес-смысл определен по типу строки; предметная карточка не привязана",
            "recommendation": default_recommendation,
        }
    return {
        "business_area": TYPE_LABELS.get(subject.get("subject_type", ""), _plain_text(subject.get("subject_type") or "Не определено")),
        "business_meaning": _subject_meaning(subject) or fallback_meaning,
        "transition_decision": DECISION_LABELS.get(decision, _plain_text(decision or "Требуется анализ")),
        "target_release_coverage": _plain_text(f"Статус проверки целевого релиза: {readiness}"),
        "risk": "средний" if open_checks == 0 else "высокий",
        "verification_status": "Связано с предметной карточкой и картой функционального разрыва",
        "recommendation": default_recommendation,
    }


def _read_classification(root: Path) -> dict[str, dict[str, str]]:
    rows = read_csv_rows(repo_path(root, TZ_CLASSIFICATION_CSV))
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        key = row.get("registry_row_id", "")
        if key:
            result[key] = row
    return result


def _read_open_questions(root: Path) -> dict[str, dict[str, str]]:
    return {row.get("registry_row_id", ""): row for row in read_csv_rows(repo_path(root, TZ_OPEN_QUESTIONS_CSV)) if row.get("registry_row_id")}


def _public_row(row: dict[str, str]) -> dict[str, str]:
    return {DETAILED_REGISTER_COLUMN_LABELS[column]: row.get(column, "") for column in DETAILED_REGISTER_COLUMNS}


def _plain_text(value: Any) -> str:
    return " ".join(str(value or "").replace("|", "/").split())


def _public_external_code(value: Any) -> str:
    code = _plain_text(value)
    if re.fullmatch(r"(?:EXT|EPR)-[A-Z0-9-]+", code, flags=re.IGNORECASE):
        return ""
    return code


def _escape_md(value: Any) -> str:
    return _plain_text(value).replace("|", "\\|")


def _normalize_public_row(row: dict[str, str]) -> dict[str, str]:
    if "row_id" in row:
        return {column: row.get(column, "") for column in DETAILED_REGISTER_COLUMNS}
    return {column: row.get(DETAILED_REGISTER_COLUMN_LABELS[column], "") for column in DETAILED_REGISTER_COLUMNS}


def read_detailed_register_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [_normalize_public_row(dict(row)) for row in csv.DictReader(fh)]


def _technical_object(value: str) -> str:
    kind, sep, name = value.partition(".")
    if not sep:
        return value
    return f"{RUSSIAN_METADATA_TYPE_LABELS.get(kind, kind)}.{name}"


def _source_ref_objects(classification_rows: dict[str, dict[str, str]]) -> set[str]:
    result: set[str] = set()
    for row in classification_rows.values():
        result.update(split_refs(row.get("configuration_objects")))
    return result


def _final_diff_feature_index(root: Path) -> dict[str, str]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in read_csv_rows(repo_path(root, FINAL_DIFF_INVENTORY_CSV)):
        if row.get("final_action") != "accept":
            continue
        object_name = row.get("object_name", "")
        feature_id = row.get("final_feature_id") or row.get("feature_id")
        if object_name and feature_id:
            counts[object_name][feature_id] += 1
    return {object_name: feature_counts.most_common(1)[0][0] for object_name, feature_counts in counts.items()}


def _load_custom_metadata_groups(root: Path, skip_objects: set[str]) -> list[dict[str, Any]]:
    summary_path = repo_path(root, CUSTOM_METADATA_OBJECT_SUMMARY)
    jsonl_path = repo_path(root, CUSTOM_METADATA_JSONL)
    if not summary_path.exists() and not jsonl_path.exists():
        return []
    candidate_counts: dict[str, Counter[str]] = defaultdict(Counter)
    if summary_path.exists():
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        for obj in data.get("objects", []):
            name = str(obj.get("metadata_full_name") or "").strip()
            counts = Counter(obj.get("counts") or {})
            if not name or name in skip_objects:
                continue
            changed = sum(count for kind, count in counts.items() if kind != "unchanged")
            if changed:
                candidate_counts[name].update(counts)
    subjects_by_object: dict[str, Counter[str]] = defaultdict(Counter)
    if jsonl_path.exists():
        with jsonl_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                name = str(row.get("metadata_full_name") or "").strip()
                if not name or name in skip_objects:
                    continue
                if row.get("status") != "non_typical" or row.get("change_type") == "unchanged":
                    continue
                candidate_counts[name][str(row.get("change_type") or "unknown")] += 0
                links = ((row.get("reconciliation") or {}).get("links") or {})
                for slug in links.get("subject_card_slugs") or []:
                    subjects_by_object[name][str(slug)] += 1
    rows: list[dict[str, Any]] = []
    for name in sorted(candidate_counts):
        counts = candidate_counts[name]
        changed = sum(count for kind, count in counts.items() if kind != "unchanged")
        if changed <= 0:
            continue
        rows.append({"metadata_full_name": name, "counts": counts, "subject_slugs": subjects_by_object.get(name, Counter())})
    return rows


def _pick_subject_for_object(object_name: str, subject_counts: Counter[str], object_to_slug: dict[str, str], feature_to_slug: dict[str, str], object_to_feature: dict[str, str]) -> str:
    if subject_counts:
        return subject_counts.most_common(1)[0][0]
    feature_id = object_to_feature.get(object_name, "")
    if feature_id and feature_id in feature_to_slug:
        return feature_to_slug[feature_id]
    return object_to_slug.get(object_name, "")


def build_detailed_customer_register(root: Path, workbook_path: Path = Path(DEFAULT_TZ_WORKBOOK)) -> dict[str, Any]:
    workbook_rows = read_tz_workbook(workbook_path)
    _sync_tz_input(root, workbook_rows)
    classification_rows = _read_classification(root)
    open_questions = _read_open_questions(root)
    subjects, object_to_slug, feature_to_slug, gaps, features = _subject_indexes(root)
    object_to_feature = _final_diff_feature_index(root)
    registry = load_registry_index(root)
    migration_requirements = load_migration_requirement_index(root)
    rows: list[dict[str, str]] = []
    trace_rows: list[dict[str, Any]] = []
    xlsx_objects = _source_ref_objects(classification_rows)
    for workbook_row in workbook_rows:
        registry_row_id = workbook_row["registry_row_id"]
        classification = classification_rows.get(registry_row_id, {})
        status = classification.get("status") or "needs_review"
        objects = split_refs(classification.get("configuration_objects"))
        slug = next((object_to_slug[obj] for obj in objects if obj in object_to_slug), "")
        recommendation = "Запросить внешний файл или подтверждение использования для оценки переноса"
        if classification.get("classification") == "mixed":
            recommendation = "Оценить перенос внешнего элемента вместе с доработкой конфигурации"
        fallback_area = _external_business_area(workbook_row["source_type"], classification.get("external_kind", ""))
        fallback_meaning = _external_business_meaning(workbook_row["source_name"], workbook_row["source_type"], classification.get("external_kind", ""))
        enrich = _enrichment(slug, subjects, gaps, recommendation, fallback_area, fallback_meaning)
        question = open_questions.get(registry_row_id, {})
        external_code = _plain_text(classification.get("external_code"))
        output_row = {
                "row_id": f"DCCR-{registry_row_id}",
                "number": str(len(rows) + 1),
                "source": "Реестр ТЗ",
                "source_registry_number": workbook_row["registry_number"],
                "title": _plain_text(workbook_row["source_name"]),
                "source_type": workbook_row["source_type"],
                "customization_type": _customization_type(classification.get("classification", ""), status),
                "detailed_class": _classification_label(classification.get("classification", ""), status),
                "external_kind": _plain_text(classification.get("external_kind")),
                "external_code": _public_external_code(external_code),
                "matched_name": _plain_text(classification.get("matched_name")),
                "key_objects": _join_objects(objects, limit=6),
                "open_question": _plain_text(question.get("needed_evidence") or question.get("reason") or ("Требуется уточнение" if status == "needs_review" else "")),
                **enrich,
            }
        rows.append(output_row)
        customization_ids = set()
        if external_code:
            customization_ids.update(registry["links_by_target"].get(("external_processing", external_code), []))
        for object_name in objects:
            customization_ids.update(str(item.get("customization_id") or "") for item in customizations_for_object(root, object_name, registry))
        trace_rows.append(
            {
                "row_id": output_row["row_id"],
                "source": output_row["source"],
                "source_registry_number": output_row["source_registry_number"],
                "external_code": external_code,
                "source_objects": objects,
                "customization_ids": sorted(customization_ids),
                "semantic_customizations": customization_trace_payload(root, list(customization_ids), registry),
                "migration_requirements": requirements_for_customizations(root, list(customization_ids), migration_requirements),
            }
        )
    for group in _load_custom_metadata_groups(root, xlsx_objects):
        object_name = str(group["metadata_full_name"])
        feature_id = object_to_feature.get(object_name, "")
        slug = _pick_subject_for_object(object_name, group.get("subject_slugs") or Counter(), object_to_slug, feature_to_slug, object_to_feature)
        counts = group.get("counts") or Counter()
        changed_count = sum(count for kind, count in counts.items() if kind != "unchanged")
        fallback_meaning = _feature_meaning(feature_id, features) or _object_business_meaning(object_name)
        enrich = _enrichment(slug, subjects, gaps, "Оценить перенос доработки основной конфигурации на целевом релизе", _object_business_area(object_name), fallback_meaning)
        output_row = {
                "row_id": f"DCCR-CFG-{len(rows) + 1:05d}",
                "number": str(len(rows) + 1),
                "source": "Перечень метаданных",
                "source_registry_number": "",
                "title": _plain_text(object_name),
                "source_type": "",
                "customization_type": "Доработка конфигурации",
                "detailed_class": "Доработка объекта метаданных",
                "external_kind": "",
                "external_code": "",
                "matched_name": "",
                "key_objects": _join_objects([object_name], limit=1),
                "target_release_coverage": enrich["target_release_coverage"],
                "verification_status": f"Подтверждено перечнем метаданных; измененных частей: {changed_count}",
                "open_question": "",
                **{key: value for key, value in enrich.items() if key not in {"target_release_coverage", "verification_status"}},
            }
        rows.append(output_row)
        customization_ids = [str(item.get("customization_id") or "") for item in customizations_for_object(root, object_name, registry)]
        trace_rows.append(
            {
                "row_id": output_row["row_id"],
                "source": output_row["source"],
                "source_registry_number": "",
                "external_code": "",
                "source_objects": [object_name],
                "customization_ids": sorted(set(customization_ids)),
                "semantic_customizations": customization_trace_payload(root, customization_ids, registry),
                "migration_requirements": requirements_for_customizations(root, customization_ids, migration_requirements),
            }
        )
    csv_path = repo_path(root, DETAILED_REGISTER_CSV)
    md_path = repo_path(root, DETAILED_REGISTER_MD)
    xlsx_path = repo_path(root, DETAILED_REGISTER_XLSX)
    write_csv_rows(csv_path, ",".join(DETAILED_REGISTER_OUTPUT_HEADER), [_public_row(row) for row in rows])
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(_render_markdown(rows), encoding="utf-8", newline="\n")
    _write_xlsx_book(
        xlsx_path,
        {
            "Детализация": [DETAILED_REGISTER_OUTPUT_HEADER]
            + [[row.get(field, "") for field in DETAILED_REGISTER_COLUMNS] for row in rows],
            "Сводка": _summary_rows(rows),
            "Открытые вопросы": [DETAILED_REGISTER_OUTPUT_HEADER]
            + [[row.get(field, "") for field in DETAILED_REGISTER_COLUMNS] for row in rows if row.get("open_question")],
        },
    )
    write_jsonl(repo_path(root, DETAILED_REGISTER_TRACE_JSONL), trace_rows)
    return {"status": "ok", "rows": len(rows), "xlsx_rows": len(workbook_rows), "csv": DETAILED_REGISTER_CSV, "markdown": DETAILED_REGISTER_MD, "xlsx": DETAILED_REGISTER_XLSX}


def _summary_rows(rows: list[dict[str, str]]) -> list[list[str]]:
    result = [["Разрез", "Значение", "Количество"]]
    for field, label in (("source", "Источник"), ("business_area", "Бизнес-область"), ("customization_type", "Тип доработки"), ("risk", "Риск")):
        for value, count in sorted(Counter(row.get(field, "") for row in rows).items()):
            result.append([label, value, str(count)])
    result.append(["Итого", "Строк", str(len(rows))])
    return result


def _render_markdown(rows: list[dict[str, str]]) -> str:
    lines = [
        "# Детальный реестр доработок",
        "",
        f"Сформировано: {utc_now_iso()}.",
        f"Всего строк: {len(rows)}.",
        "",
        "## Сводка",
        "",
    ]
    for field, label in (("source", "Источник"), ("business_area", "Бизнес-область"), ("customization_type", "Тип доработки"), ("risk", "Риск")):
        lines.append(f"### {label}")
        for value, count in sorted(Counter(row.get(field, "") for row in rows).items()):
            lines.append(f"- {value or 'Не заполнено'}: {count}")
        lines.append("")
    lines.extend(
        [
            "## Реестр",
            "",
            "| № | Источник | Номер в источнике | Наименование | Тип доработки | Бизнес-область | Бизнес-смысл | Решение по переходу | Риск | Статус проверки | Рекомендация |",
            "|---:|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                _escape_md(row.get(field))
                for field in (
                    "number",
                    "source",
                    "source_registry_number",
                    "title",
                    "customization_type",
                    "business_area",
                    "business_meaning",
                    "transition_decision",
                    "risk",
                    "verification_status",
                    "recommendation",
                )
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _sheet_inline_xml(rows: list[list[str]]) -> str:
    row_xml: list[str] = []
    for row_number, row in enumerate(rows, 1):
        cells: list[str] = []
        for column_index, value in enumerate(row, 1):
            cell = f"{_excel_column_name(column_index)}{row_number}"
            cells.append(f'<c r="{cell}" t="inlineStr"><is><t>{html.escape(excel_cell_text(str(value or "")))}</t></is></c>')
        row_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    max_column_count = max((len(row) for row in rows), default=0)
    dimension = f"A1:{_excel_column_name(max_column_count)}{len(rows)}" if rows and max_column_count else "A1"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/>'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        "</worksheet>"
    )


def _write_xlsx_book(path: Path, sheets: dict[str, list[list[str]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_names = list(sheets)
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(sheet_names) + 1)
    )
    workbook_sheets = "".join(
        f'<sheet name="{html.escape(name)}" sheetId="{index}" r:id="rId{index}"/>' for index, name in enumerate(sheet_names, 1)
    )
    rels = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheet_names) + 1)
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{overrides}"
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{workbook_sheets}</sheets></workbook>",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}"
            f'<Relationship Id="rId{len(sheet_names) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>",
        )
        zf.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>')
        for index, name in enumerate(sheet_names, 1):
            zf.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_inline_xml(sheets[name]))


def _xlsx_sheet_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("xl/workbook.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return [sheet.attrib.get("name", "") for sheet in root.findall(".//m:sheet", ns)]


def _eligible_custom_metadata_objects(root: Path) -> set[str]:
    return {str(row["metadata_full_name"]) for row in _load_custom_metadata_groups(root, set())}


def validate_detailed_customer_register(root: Path, workbook_path: Path = Path(DEFAULT_TZ_WORKBOOK)) -> dict[str, Any]:
    errors: list[str] = []
    try:
        errors.extend(freshness_errors(root, workbook_path))
        workbook_rows = read_tz_workbook(workbook_path)
    except Exception as exc:
        errors.append(str(exc))
        workbook_rows = []
    csv_path = repo_path(root, DETAILED_REGISTER_CSV)
    md_path = repo_path(root, DETAILED_REGISTER_MD)
    xlsx_path = repo_path(root, DETAILED_REGISTER_XLSX)
    trace_path = repo_path(root, DETAILED_REGISTER_TRACE_JSONL)
    registry = load_registry_index(root)
    for relative, path in ((DETAILED_REGISTER_CSV, csv_path), (DETAILED_REGISTER_MD, md_path), (DETAILED_REGISTER_XLSX, xlsx_path)):
        if not path.exists():
            errors.append(f"Missing required path: {relative}")
    if registry["exists"] and not trace_path.exists():
        errors.append(f"Missing required path: {DETAILED_REGISTER_TRACE_JSONL}")
    rows: list[dict[str, str]] = []
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames or []
            raw_rows = [dict(row) for row in reader]
        expected_headers = [list(DETAILED_REGISTER_COLUMNS), DETAILED_REGISTER_OUTPUT_HEADER]
        if header not in expected_headers:
            errors.append(f"{DETAILED_REGISTER_CSV} header mismatch: expected {DETAILED_REGISTER_OUTPUT_HEADER}, got {header}")
        rows = [_normalize_public_row(row) for row in raw_rows]
        trace_rows = [row for _, row in read_jsonl(trace_path)] if trace_path.exists() else []
        trace_by_row_id = {str(row.get("row_id") or ""): row for row in trace_rows}
        for row in rows:
            for field in REQUIRED_FIELDS:
                if not str(row.get(field) or "").strip():
                    errors.append(f"{DETAILED_REGISTER_CSV} row {row.get('row_id', '?')} has empty field: {field}")
            if row.get("business_area") == "Не привязано":
                errors.append(f"{DETAILED_REGISTER_CSV} row {row.get('row_id', '?')} lacks business area")
            for field in CUSTOMER_FIELDS:
                value = str(row.get(field) or "")
                if DETAIL_FORBIDDEN_CUSTOMER_MARKERS.search(value):
                    errors.append(f"{DETAILED_REGISTER_CSV} row {row.get('row_id', '?')} exposes technical marker in {field}: {value}")
        xlsx_numbers = {row["registry_number"] for row in workbook_rows}
        output_numbers = {row.get("source_registry_number", "") for row in rows if row.get("source") == "Реестр ТЗ"}
        if xlsx_numbers and xlsx_numbers != output_numbers:
            errors.append(f"{DETAILED_REGISTER_CSV} does not cover all XLSX rows: expected {len(xlsx_numbers)}, got {len(output_numbers)}")
        eligible_objects = _eligible_custom_metadata_objects(root)
        config_rows = [row for row in rows if row.get("source") == "Перечень метаданных"]
        if eligible_objects and not config_rows:
            errors.append(f"{DETAILED_REGISTER_CSV} lacks configuration rows from {CUSTOM_METADATA_JSONL}")
        for row in config_rows:
            if _technical_object(row.get("key_objects", "")) not in eligible_objects:
                errors.append(f"{DETAILED_REGISTER_CSV} configuration row is not an eligible changed object: {row.get('key_objects')}")
            if registry["exists"]:
                object_name = _technical_object(row.get("key_objects", ""))
                linked_ids = {str(item.get("customization_id") or "") for item in customizations_for_object(root, object_name, registry)}
                trace_ids = set(trace_by_row_id.get(row.get("row_id", ""), {}).get("customization_ids") or [])
                missing_trace = sorted(linked_ids - trace_ids)
                if missing_trace:
                    errors.append(f"{DETAILED_REGISTER_TRACE_JSONL} misses CUS links for {row.get('row_id')}: {';'.join(missing_trace[:10])}")
        if registry["exists"]:
            for trace_row in trace_rows:
                for customization_id in trace_row.get("customization_ids") or []:
                    if customization_id not in registry["by_id"]:
                        errors.append(f"{DETAILED_REGISTER_TRACE_JSONL} references missing customization_id: {customization_id}")
        if len(config_rows) > len(eligible_objects):
            errors.append(f"{DETAILED_REGISTER_CSV} has more configuration rows than eligible metadata objects")
        open_questions = set(_read_open_questions(root))
        for question_id in open_questions:
            if not any(row.get("row_id") == f"DCCR-{question_id}" and row.get("open_question") for row in rows):
                errors.append(f"{DETAILED_REGISTER_CSV} misses open question row: {question_id}")
    if md_path.exists() and DETAIL_FORBIDDEN_CUSTOMER_MARKERS.search(md_path.read_text(encoding="utf-8")):
        errors.append(f"{DETAILED_REGISTER_MD} exposes technical markers")
    if xlsx_path.exists():
        errors.extend(f"{DETAILED_REGISTER_XLSX}: {error}" for error in validate_minimal_xlsx(xlsx_path))
        sheet_names = _xlsx_sheet_names(xlsx_path)
        for name in ("Детализация", "Сводка"):
            if name not in sheet_names:
                errors.append(f"{DETAILED_REGISTER_XLSX} misses sheet: {name}")
        if rows and any(row.get("open_question") for row in rows) and "Открытые вопросы" not in sheet_names:
            errors.append(f"{DETAILED_REGISTER_XLSX} misses sheet: Открытые вопросы")
    return {"status": "fail" if errors else "ok", "errors": errors, "rows": len(rows)}


def build_command(args: argparse.Namespace) -> int:
    print(json.dumps(build_detailed_customer_register(_root(args), _workbook_path(args)), ensure_ascii=False, indent=2))
    return 0


def validate_command(args: argparse.Namespace) -> int:
    result = validate_detailed_customer_register(_root(args), _workbook_path(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1
