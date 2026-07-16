from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .autopilot import write_minimal_xlsx
from .common import read_jsonl, repo_path, utc_now_iso, write_jsonl
from .configuration_source_parser import (
    PARSER_SCHEMA_VERSION,
    PART_KINDS,
    PART_KIND_ATTRIBUTE,
    PART_KIND_COMMAND,
    PART_KIND_FORM,
    PART_KIND_FORM_ELEMENT,
    PART_KIND_MODULE,
    PART_KIND_OBJECT,
    PART_KIND_PREDEFINED_VALUE,
    PART_KIND_ROLE_RIGHT,
    PART_KIND_SCHEDULED_JOB,
    PART_KIND_SERVICE,
    PART_KIND_SUBSCRIPTION,
    PART_KIND_TABULAR_SECTION,
    PART_KIND_TEMPLATE,
    PART_KIND_UNKNOWN,
    ParsedConfigurationSnapshot,
    ParsedMetadataItem,
    parse_configuration_source,
    read_snapshot,
    validate_snapshot,
    write_snapshot,
)


CUSTOM_METADATA_SCHEMA_VERSION = "custom-metadata-inventory/v1"
DEFAULT_INVENTORY_DIR = "analysis/custom-metadata"
DEFAULT_REPORT_DIR = f"{DEFAULT_INVENTORY_DIR}/reports"

COMPARISON_MODE_FULL = "full"
COMPARISON_MODE_REDUCED = "reduced"
COMPARISON_MODES = {COMPARISON_MODE_FULL, COMPARISON_MODE_REDUCED}

CHANGE_TYPE_ADDED = "added"
CHANGE_TYPE_MODIFIED = "modified"
CHANGE_TYPE_REMOVED = "removed"
CHANGE_TYPE_UNCHANGED = "unchanged"
CHANGE_TYPE_UNKNOWN = "unknown"
CHANGE_TYPES = {CHANGE_TYPE_ADDED, CHANGE_TYPE_MODIFIED, CHANGE_TYPE_REMOVED, CHANGE_TYPE_UNCHANGED, CHANGE_TYPE_UNKNOWN}

STATUS_NON_TYPICAL = "non_typical"
STATUS_UNCHANGED = "unchanged"
STATUSES = {STATUS_NON_TYPICAL, STATUS_UNCHANGED}

INDEX_JSONL = "index.jsonl"
INDEX_CSV = "index.csv"
OBJECT_SUMMARY_JSON = "object-summary.json"
SOURCE_BALANCE_JSON = "source-balance.json"
BUILD_METADATA_JSON = "build-metadata.json"
RECONCILIATION_EXCEPTIONS_CSV = "reconciliation-exceptions.csv"

INDEX_CSV_FIELDS = [
    "schema_version",
    "item_id",
    "item_key",
    "comparison_mode",
    "source_format",
    "metadata_kind",
    "metadata_name",
    "metadata_full_name",
    "part_kind",
    "part_name",
    "part_path",
    "change_type",
    "status",
    "typical_item_key",
    "typical_content_hash",
    "customer_item_key",
    "customer_content_hash",
    "reconciliation_status",
    "diff_ids",
    "final_diff_ids",
    "feature_ids",
    "reverse_statuses",
    "subject_card_slugs",
    "artifact_paths",
    "diagnostics",
]

SPREADSHEET_FIELDS = [
    ("item_id", "ID строки"),
    ("metadata_kind", "Вид объекта 1С"),
    ("metadata_name", "Имя объекта"),
    ("metadata_full_name", "Полное имя объекта"),
    ("part_kind", "Часть объекта"),
    ("part_name", "Имя части"),
    ("part_path", "Путь части"),
    ("change_type", "Тип изменения"),
    ("status", "Статус"),
    ("reconciliation_status", "Статус сверки"),
    ("diff_ids", "Отличия"),
    ("final_diff_ids", "Финальные отличия"),
    ("feature_ids", "Функциональные блоки"),
    ("reverse_statuses", "Статусы обратной привязки"),
    ("subject_card_slugs", "Карточки тем"),
    ("artifact_paths", "Артефакты"),
    ("diagnostics", "Диагностика"),
    ("item_key", "Технический ключ"),
    ("comparison_mode", "Режим сравнения"),
    ("source_format", "Формат исходников"),
    ("typical_item_key", "Ключ типовой"),
    ("typical_content_hash", "Хеш типовой"),
    ("customer_item_key", "Ключ доработанной"),
    ("customer_content_hash", "Хеш доработанной"),
    ("schema_version", "Версия схемы"),
]

METADATA_KIND_LABELS = {
    "AccountingRegister": "Регистр бухгалтерии",
    "AccumulationRegister": "Регистр накопления",
    "Catalog": "Справочник",
    "ChartOfAccounts": "План счетов",
    "ChartOfCalculationTypes": "План видов расчета",
    "ChartOfCharacteristicTypes": "План видов характеристик",
    "CommandGroup": "Группа команд",
    "CommonAttribute": "Общий реквизит",
    "CommonCommand": "Общая команда",
    "CommonForm": "Общая форма",
    "CommonModule": "Общий модуль",
    "CommonPicture": "Общая картинка",
    "CommonTemplate": "Общий макет",
    "ConfigDumpInfo": "Сведения о выгрузке конфигурации",
    "Configuration": "Конфигурация",
    "Constant": "Константа",
    "DataProcessor": "Обработка",
    "Document": "Документ",
    "DocumentJournal": "Журнал документов",
    "DocumentNumerator": "Нумератор документов",
    "Enum": "Перечисление",
    "EventSubscription": "Подписка на событие",
    "ExchangePlan": "План обмена",
    "Ext": "Расширение",
    "FilterCriterion": "Критерий отбора",
    "FunctionalOption": "Функциональная опция",
    "HTTPService": "HTTP-сервис",
    "InformationRegister": "Регистр сведений",
    "Interface": "Интерфейс",
    "Language": "Язык",
    "Report": "Отчет",
    "Role": "Роль",
    "ScheduledJob": "Регламентное задание",
    "Sequence": "Последовательность",
    "SessionParameter": "Параметр сеанса",
    "Style": "Стиль",
    "StyleItem": "Элемент стиля",
    "Subsystem": "Подсистема",
    "Template": "Макет",
    "WebService": "Веб-сервис",
    "WSReference": "WS-ссылка",
    "XDTOPackage": "Пакет XDTO",
}

PART_KIND_LABELS = {
    PART_KIND_ATTRIBUTE: "Реквизит",
    PART_KIND_COMMAND: "Команда",
    PART_KIND_FORM: "Форма",
    PART_KIND_FORM_ELEMENT: "Элемент формы",
    PART_KIND_MODULE: "Модуль",
    PART_KIND_OBJECT: "Объект верхнего уровня",
    PART_KIND_PREDEFINED_VALUE: "Предопределенный элемент",
    PART_KIND_ROLE_RIGHT: "Право роли",
    PART_KIND_SCHEDULED_JOB: "Регламентное задание",
    PART_KIND_SERVICE: "Сервис",
    PART_KIND_SUBSCRIPTION: "Подписка",
    PART_KIND_TABULAR_SECTION: "Табличная часть",
    PART_KIND_TEMPLATE: "Макет",
    PART_KIND_UNKNOWN: "Нераспознанная часть",
}

CHANGE_TYPE_LABELS = {
    CHANGE_TYPE_ADDED: "Добавлено",
    CHANGE_TYPE_MODIFIED: "Изменено",
    CHANGE_TYPE_REMOVED: "Удалено",
    CHANGE_TYPE_UNCHANGED: "Без изменений",
    CHANGE_TYPE_UNKNOWN: "Не определено",
}

STATUS_LABELS = {
    STATUS_NON_TYPICAL: "Нетиповое",
    STATUS_UNCHANGED: "Без изменений",
}

RECONCILIATION_STATUS_LABELS = {
    "linked": "Связано с отличиями",
    "parser_only": "Только в снимках парсера",
}

COMPARISON_MODE_LABELS = {
    COMPARISON_MODE_FULL: "Полная сверка",
    COMPARISON_MODE_REDUCED: "Сокращенная сверка",
}

SOURCE_FORMAT_LABELS = {
    "xml-bsl": "Выгрузка Designer XML/BSL",
    "v8unpack": "Дерево v8unpack",
}


class InventoryError(Exception):
    pass


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_key_part(value: str) -> str:
    return " ".join(str(value or "").replace("\\", "/").split())


def inventory_item_key(item: ParsedMetadataItem) -> str:
    return "|".join(
        [
            _safe_key_part(item.metadata_kind),
            _safe_key_part(item.metadata_full_name),
            _safe_key_part(item.part_kind),
            _safe_key_part(item.part_name),
            _safe_key_part(item.part_path),
        ]
    )


def _object_key(item: ParsedMetadataItem | dict[str, Any]) -> str:
    return f"{getattr(item, 'metadata_kind', '') if not isinstance(item, dict) else item.get('metadata_kind', '')}|{getattr(item, 'metadata_full_name', '') if not isinstance(item, dict) else item.get('metadata_full_name', '')}"


def _parser_ref(item: ParsedMetadataItem | None) -> dict[str, Any]:
    if item is None:
        return {}
    return {
        "source_format": item.source_format,
        "item_key": item.item_key,
        "content_hash": item.content_hash,
        "source_refs": [ref.to_dict() for ref in item.source_refs],
        "diagnostics": item.diagnostics,
    }


@dataclass
class CustomMetadataItem:
    item_id: str
    item_key: str
    comparison_mode: str
    source_format: str
    metadata_kind: str
    metadata_name: str
    metadata_full_name: str
    part_kind: str
    part_name: str
    part_path: str
    change_type: str
    status: str
    typical_parser_ref: dict[str, Any] = field(default_factory=dict)
    customer_parser_ref: dict[str, Any] = field(default_factory=dict)
    reconciliation: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CUSTOM_METADATA_SCHEMA_VERSION,
            "item_id": self.item_id,
            "item_key": self.item_key,
            "comparison_mode": self.comparison_mode,
            "source_format": self.source_format,
            "metadata_kind": self.metadata_kind,
            "metadata_name": self.metadata_name,
            "metadata_full_name": self.metadata_full_name,
            "part_kind": self.part_kind,
            "part_name": self.part_name,
            "part_path": self.part_path,
            "change_type": self.change_type,
            "status": self.status,
            "typical_parser_ref": self.typical_parser_ref,
            "customer_parser_ref": self.customer_parser_ref,
            "reconciliation": self.reconciliation,
            "diagnostics": self.diagnostics,
        }


def _snapshot_errors(label: str, snapshot: ParsedConfigurationSnapshot) -> list[str]:
    errors: list[str] = []
    if snapshot.schema_version != PARSER_SCHEMA_VERSION:
        errors.append(f"{label} snapshot has incompatible schema_version: {snapshot.schema_version}")
    for error in validate_snapshot(snapshot):
        errors.append(f"{label} snapshot: {error}")
    return errors


def validate_snapshot_pair(typical: ParsedConfigurationSnapshot, customer: ParsedConfigurationSnapshot) -> list[str]:
    return [*_snapshot_errors("typical", typical), *_snapshot_errors("customer", customer)]


def _classify_change(typical: ParsedMetadataItem | None, customer: ParsedMetadataItem | None) -> str:
    if customer is None:
        return CHANGE_TYPE_REMOVED
    if typical is None:
        return CHANGE_TYPE_ADDED
    if typical.part_kind == PART_KIND_UNKNOWN or customer.part_kind == PART_KIND_UNKNOWN:
        return CHANGE_TYPE_UNKNOWN
    if typical.content_hash != customer.content_hash or typical.structural_payload != customer.structural_payload:
        return CHANGE_TYPE_MODIFIED
    return CHANGE_TYPE_UNCHANGED


def _source_format(typical: ParsedMetadataItem | None, customer: ParsedMetadataItem | None) -> str:
    formats = {item.source_format for item in (typical, customer) if item is not None and item.source_format}
    return next(iter(formats)) if len(formats) == 1 else "mixed"


def _selected_item(typical: ParsedMetadataItem | None, customer: ParsedMetadataItem | None) -> ParsedMetadataItem:
    item = customer or typical
    if item is None:
        raise InventoryError("Cannot build inventory row without typical or customer item")
    return item


def _added_object_keys(typical_by_key: dict[str, ParsedMetadataItem], customer_by_key: dict[str, ParsedMetadataItem]) -> set[str]:
    added: set[str] = set()
    for key, item in customer_by_key.items():
        if item.part_kind == PART_KIND_OBJECT and key not in typical_by_key:
            added.add(_object_key(item))
    return added


def _include_in_mode(
    comparison_mode: str,
    typical: ParsedMetadataItem | None,
    customer: ParsedMetadataItem | None,
    added_objects: set[str],
) -> bool:
    if comparison_mode == COMPARISON_MODE_FULL:
        return True
    item = customer or typical
    if item is None or customer is None:
        return False
    return _object_key(item) in added_objects


def compare_snapshots(
    typical: ParsedConfigurationSnapshot,
    customer: ParsedConfigurationSnapshot,
    comparison_mode: str = COMPARISON_MODE_FULL,
) -> list[CustomMetadataItem]:
    if comparison_mode not in COMPARISON_MODES:
        raise InventoryError(f"Unsupported comparison mode: {comparison_mode}")
    errors = validate_snapshot_pair(typical, customer)
    if errors:
        raise InventoryError("; ".join(errors))

    typical_by_key = {inventory_item_key(item): item for item in typical.items}
    customer_by_key = {inventory_item_key(item): item for item in customer.items}
    added_objects = _added_object_keys(typical_by_key, customer_by_key)
    rows: list[CustomMetadataItem] = []
    for item_key in sorted(set(typical_by_key) | set(customer_by_key)):
        typical_item = typical_by_key.get(item_key)
        customer_item = customer_by_key.get(item_key)
        if not _include_in_mode(comparison_mode, typical_item, customer_item, added_objects):
            continue
        selected = _selected_item(typical_item, customer_item)
        change_type = _classify_change(typical_item, customer_item)
        status = STATUS_UNCHANGED if change_type == CHANGE_TYPE_UNCHANGED else STATUS_NON_TYPICAL
        diagnostics = []
        for item in (typical_item, customer_item):
            if item is not None:
                diagnostics.extend(item.diagnostics)
        rows.append(
            CustomMetadataItem(
                item_id="",
                item_key=item_key,
                comparison_mode=comparison_mode,
                source_format=_source_format(typical_item, customer_item),
                metadata_kind=selected.metadata_kind,
                metadata_name=selected.metadata_name,
                metadata_full_name=selected.metadata_full_name,
                part_kind=selected.part_kind,
                part_name=selected.part_name,
                part_path=selected.part_path,
                change_type=change_type,
                status=status,
                typical_parser_ref=_parser_ref(typical_item),
                customer_parser_ref=_parser_ref(customer_item),
                reconciliation={"status": "parser_only", "links": []},
                diagnostics=diagnostics,
            )
        )
    for index, row in enumerate(sorted(rows, key=lambda row: row.item_key), 1):
        row.item_id = f"CMI-{index:05d}"
    return rows


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _artifact_ref(relative: str, anchor: str = "") -> str:
    return f"{relative}#{anchor}" if anchor else relative


def _add_artifact_path(values: dict[str, set[str]], path: str) -> None:
    if path:
        values["artifact_paths"].add(path)


def _row_is_non_noise(row: dict[str, str]) -> bool:
    text = " ".join(str(value or "").lower() for value in row.values())
    return not any(token in text for token in ("noise", "technical_noise", "excluded_noise"))


def _research_row_is_object_level(row: dict[str, str]) -> bool:
    object_kind = str(row.get("object_kind") or "").strip()
    object_name = str(row.get("object_name") or row.get("object") or "").strip()
    path = str(row.get("path") or "").replace("\\", "/").strip("/")
    if not path:
        return True
    parts = [part for part in path.split("/") if part]
    short_name = object_name.rsplit(".", 1)[-1] if object_name else ""
    basename = parts[-1] if parts else ""
    object_file_names = {
        f"{object_kind}.json",
        f"{object_kind}.id.json",
        f"{object_kind}.xml",
        f"{short_name}.xml",
        f"{short_name}.json",
    }
    if basename in object_file_names:
        return True
    return len(parts) <= 2


def _research_row_part_kind(row: dict[str, str]) -> str:
    if _research_row_is_object_level(row):
        return PART_KIND_OBJECT
    path = str(row.get("path") or "").replace("\\", "/").strip("/")
    lowered = path.lower()
    parts = [part.lower() for part in path.split("/") if part]
    basename = parts[-1] if parts else ""
    if basename.endswith(".elem.json"):
        return PART_KIND_FORM_ELEMENT
    if any(token in lowered for token in ("predefined", "предустанов", "предопредел")):
        return PART_KIND_PREDEFINED_VALUE
    if any(part in {"attributes", "attribute"} for part in parts):
        return PART_KIND_ATTRIBUTE
    if any(part in {"tabularsections", "tabularsection", "tabular_sections", "tabular_section"} for part in parts):
        return PART_KIND_TABULAR_SECTION
    if any(part in {"commands", "command"} for part in parts):
        return PART_KIND_COMMAND
    if any(part in {"scheduledjobs", "scheduledjob", "scheduled_jobs", "scheduled_job"} for part in parts):
        return PART_KIND_SCHEDULED_JOB
    if any(part in {"eventsubscriptions", "eventsubscription", "subscriptions", "subscription"} for part in parts):
        return PART_KIND_SUBSCRIPTION
    if any(part in {"httpservices", "httpservice", "webservices", "webservice", "services", "service"} for part in parts):
        return PART_KIND_SERVICE
    if "template" in lowered:
        return PART_KIND_TEMPLATE
    if "role" in lowered:
        return PART_KIND_ROLE_RIGHT
    if "form/" in path or "form." in lowered or any(part.endswith("form") for part in path.split("/")):
        return PART_KIND_FORM
    if basename.endswith(".bsl"):
        return PART_KIND_MODULE
    return PART_KIND_UNKNOWN


def _research_path(row: dict[str, str]) -> str:
    return str(row.get("path") or "").replace("\\", "/").strip("/")


def _row_source_paths(row: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for ref_name in ("typical_parser_ref", "customer_parser_ref"):
        parser_ref = row.get(ref_name)
        if not isinstance(parser_ref, dict):
            continue
        for source_ref in parser_ref.get("source_refs") or []:
            if isinstance(source_ref, dict) and source_ref.get("path"):
                paths.add(str(source_ref.get("path")).replace("\\", "/").strip("/"))
    return paths


def _inventory_row_covers_research_row(inventory_row: dict[str, Any], research_row: dict[str, str]) -> bool:
    object_name = str(research_row.get("object_name") or research_row.get("object") or "")
    if str(inventory_row.get("metadata_full_name") or "") != object_name:
        return False
    expected_part_kind = _research_row_part_kind(research_row)
    if str(inventory_row.get("part_kind") or "") != expected_part_kind:
        return False
    if expected_part_kind == PART_KIND_OBJECT:
        return True
    research_path = _research_path(research_row)
    if not research_path:
        return True
    source_paths = _row_source_paths(inventory_row)
    if research_path in source_paths:
        return True
    part_path = str(inventory_row.get("part_path") or "").replace("\\", "/").strip("/")
    if part_path and (research_path.endswith(part_path) or f"/{part_path}/" in f"/{research_path}/"):
        return True
    part_name = str(inventory_row.get("part_name") or "").strip().lower()
    return bool(part_name and part_name in research_path.lower())


def _research_row_is_reduced_mode_filtered(row: dict[str, str], covered_objects: set[str]) -> bool:
    object_name = str(row.get("object_name") or row.get("object") or "")
    if not object_name or object_name in covered_objects:
        return False
    return not _research_row_is_object_level(row)


def _load_reconciliation_links(root: Path) -> dict[str, dict[str, set[str]]]:
    links: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    diff_relative = "analysis/indexes/diff-inventory.csv"
    final_relative = "analysis/indexes/final-diff-inventory.csv"
    feature_relative = "analysis/indexes/feature-map.csv"
    coverage_relative = "analysis/reverse-map/coverage.csv"
    subject_relative = "analysis/subject-cards/coverage.csv"
    diff_rows = _read_csv(repo_path(root, diff_relative))
    final_rows = _read_csv(repo_path(root, final_relative))
    feature_rows = _read_csv(repo_path(root, feature_relative))
    coverage_rows = _read_csv(repo_path(root, coverage_relative))
    subject_rows = _read_csv(repo_path(root, subject_relative))
    feature_artifacts: dict[str, set[str]] = defaultdict(set)
    for row in feature_rows:
        feature_id = row.get("feature_id", "")
        if not feature_id:
            continue
        feature_artifacts[feature_id].add(_artifact_ref(feature_relative, feature_id))
        for field_name in ("evidence_pack_path", "open_questions_path", "outputs"):
            for path in str(row.get(field_name) or "").split(";"):
                if path.strip():
                    feature_artifacts[feature_id].add(path.strip())

    for row in diff_rows:
        key = row.get("object_name") or row.get("object") or ""
        if key:
            values = links[key]
            diff_id = row.get("diff_id", "")
            feature_id = row.get("feature_id", "")
            values["diff_ids"].add(diff_id)
            values["feature_ids"].add(feature_id)
            _add_artifact_path(values, _artifact_ref(diff_relative, diff_id))
            _add_artifact_path(values, row.get("evidence_ref", ""))
    for row in final_rows:
        key = row.get("object_name") or row.get("object") or ""
        if key:
            values = links[key]
            diff_id = row.get("diff_id", "")
            values["final_diff_ids"].add(diff_id)
            values["feature_ids"].add(row.get("final_feature_id") or row.get("feature_id", ""))
            _add_artifact_path(values, _artifact_ref(final_relative, diff_id))
            _add_artifact_path(values, row.get("evidence_ref", ""))
    for values in links.values():
        for feature_id in list(values.get("feature_ids", set())):
            for path in feature_artifacts.get(feature_id, set()):
                _add_artifact_path(values, path)
    for row in coverage_rows:
        key = row.get("object_name") or row.get("object") or row.get("metadata_full_name") or ""
        if key:
            values = links[key]
            values["reverse_statuses"].add(row.get("status_after_pass") or row.get("status") or "")
            _add_artifact_path(values, _artifact_ref(coverage_relative, row.get("diff_id", "")))
            _add_artifact_path(values, row.get("evidence_ref", ""))
            _add_artifact_path(values, row.get("decision_ref", ""))
    for row in subject_rows:
        feature_id = row.get("feature_id", "")
        slug = row.get("subject_card_slug", "")
        if feature_id and slug:
            for values in links.values():
                if feature_id in values.get("feature_ids", set()):
                    values["subject_card_slugs"].add(slug)
                    _add_artifact_path(values, _artifact_ref(subject_relative, slug))
                    _add_artifact_path(values, f"analysis/subject-cards/cards/{slug}/subject-card.json")
    return links


def enrich_rows(root: Path, rows: list[CustomMetadataItem]) -> None:
    links = _load_reconciliation_links(root)
    for row in rows:
        values = links.get(row.metadata_full_name, {})
        clean = {key: sorted(value for value in values_set if value) for key, values_set in values.items()}
        if any(clean.values()):
            row.reconciliation = {"status": "linked", "links": clean}
        else:
            row.reconciliation = {"status": "parser_only", "links": []}


def _csv_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return _stable_json(value)
    return str(value or "")


def _snapshot_metadata(snapshot: ParsedConfigurationSnapshot) -> dict[str, Any]:
    return {
        "schema_version": snapshot.schema_version,
        "parser_version": snapshot.parser_version,
        "source_format": snapshot.source_format,
        "source_root": snapshot.source_root,
        "source_root_identity": snapshot.source_root_identity,
        "item_count": len(snapshot.items),
        "diagnostics": snapshot.diagnostics,
        "layout_diagnostics": snapshot.layout_diagnostics,
        "validation_errors": validate_snapshot(snapshot),
    }


def build_metadata(
    comparison_mode: str,
    typical: ParsedConfigurationSnapshot,
    customer: ParsedConfigurationSnapshot,
) -> dict[str, Any]:
    return {
        "schema_version": CUSTOM_METADATA_SCHEMA_VERSION,
        "comparison_mode": comparison_mode,
        "generated_at": utc_now_iso(),
        "snapshots": {
            "typical": _snapshot_metadata(typical),
            "customer": _snapshot_metadata(customer),
        },
    }


def write_inventory(
    root: Path,
    output_dir: Path,
    rows: list[CustomMetadataItem],
    comparison_mode: str,
    typical: ParsedConfigurationSnapshot | None = None,
    customer: ParsedConfigurationSnapshot | None = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    row_dicts = [row.to_dict() for row in rows]
    index_jsonl = output_dir / INDEX_JSONL
    write_jsonl(index_jsonl, row_dicts)
    index_csv = output_dir / INDEX_CSV
    with index_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=INDEX_CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in row_dicts:
            reconciliation = row.get("reconciliation") or {}
            links = reconciliation.get("links") if isinstance(reconciliation, dict) else {}
            writer.writerow(
                {
                    **{field: _csv_value(row.get(field, "")) for field in INDEX_CSV_FIELDS},
                    "typical_item_key": row.get("typical_parser_ref", {}).get("item_key", ""),
                    "typical_content_hash": row.get("typical_parser_ref", {}).get("content_hash", ""),
                    "customer_item_key": row.get("customer_parser_ref", {}).get("item_key", ""),
                    "customer_content_hash": row.get("customer_parser_ref", {}).get("content_hash", ""),
                    "reconciliation_status": reconciliation.get("status", ""),
                    "diff_ids": ";".join(links.get("diff_ids", [])) if isinstance(links, dict) else "",
                    "final_diff_ids": ";".join(links.get("final_diff_ids", [])) if isinstance(links, dict) else "",
                    "feature_ids": ";".join(links.get("feature_ids", [])) if isinstance(links, dict) else "",
                    "reverse_statuses": ";".join(links.get("reverse_statuses", [])) if isinstance(links, dict) else "",
                    "subject_card_slugs": ";".join(links.get("subject_card_slugs", [])) if isinstance(links, dict) else "",
                    "artifact_paths": ";".join(links.get("artifact_paths", [])) if isinstance(links, dict) else "",
                    "diagnostics": _stable_json(row.get("diagnostics", [])),
                }
            )
    object_summary = build_object_summary(row_dicts, comparison_mode)
    object_summary_path = output_dir / OBJECT_SUMMARY_JSON
    object_summary_path.write_text(json.dumps(object_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    source_balance = build_source_balance(row_dicts, comparison_mode)
    source_balance_path = output_dir / SOURCE_BALANCE_JSON
    source_balance_path.write_text(json.dumps(source_balance, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths = {
        "index_jsonl": index_jsonl,
        "index_csv": index_csv,
        "object_summary": object_summary_path,
        "source_balance": source_balance_path,
    }
    if typical is not None and customer is not None:
        build_metadata_path = output_dir / BUILD_METADATA_JSON
        build_metadata_path.write_text(
            json.dumps(build_metadata(comparison_mode, typical, customer), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths["build_metadata"] = build_metadata_path
    return paths


def build_object_summary(rows: list[dict[str, Any]], comparison_mode: str) -> dict[str, Any]:
    by_object: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_object[str(row.get("metadata_full_name", ""))][str(row.get("change_type", ""))] += 1
    return {
        "schema_version": CUSTOM_METADATA_SCHEMA_VERSION,
        "comparison_mode": comparison_mode,
        "generated_at": utc_now_iso(),
        "object_count": len(by_object),
        "objects": [
            {"metadata_full_name": key, "counts": dict(sorted(counter.items()))}
            for key, counter in sorted(by_object.items())
        ],
    }


def build_source_balance(rows: list[dict[str, Any]], comparison_mode: str) -> dict[str, Any]:
    return {
        "schema_version": CUSTOM_METADATA_SCHEMA_VERSION,
        "comparison_mode": comparison_mode,
        "generated_at": utc_now_iso(),
        "row_count": len(rows),
        "by_change_type": dict(sorted(Counter(str(row.get("change_type", "")) for row in rows).items())),
        "by_part_kind": dict(sorted(Counter(str(row.get("part_kind", "")) for row in rows).items())),
        "by_reconciliation_status": dict(sorted(Counter(str((row.get("reconciliation") or {}).get("status", "")) for row in rows).items())),
    }


def read_inventory(index_path: Path) -> list[dict[str, Any]]:
    return [row for _, row in read_jsonl(index_path)]


def _source_refs_present(row: dict[str, Any]) -> bool:
    for key in ("typical_parser_ref", "customer_parser_ref"):
        ref = row.get(key)
        if isinstance(ref, dict) and ref.get("source_refs"):
            return True
    return False


def _read_exceptions(path: Path) -> set[str]:
    if not path.exists():
        return set()
    rows = _read_csv(path)
    return {row.get("diff_id", "") for row in rows if row.get("diff_id")}


def validate_build_metadata(inventory_dir: Path, comparison_modes: set[str]) -> list[str]:
    path = inventory_dir / BUILD_METADATA_JSON
    if not path.exists():
        return [f"Missing custom metadata artifact: {path.as_posix()}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"Custom metadata build metadata does not parse: {exc}"]
    errors: list[str] = []
    if data.get("schema_version") != CUSTOM_METADATA_SCHEMA_VERSION:
        errors.append(f"build metadata has unsupported schema_version: {data.get('schema_version')}")
    mode = str(data.get("comparison_mode") or "")
    if mode not in COMPARISON_MODES:
        errors.append(f"build metadata has invalid comparison_mode: {mode}")
    elif comparison_modes and mode not in comparison_modes:
        errors.append(f"build metadata comparison_mode does not match inventory rows: {mode}")
    snapshots = data.get("snapshots") if isinstance(data.get("snapshots"), dict) else {}
    for label in ("typical", "customer"):
        snapshot = snapshots.get(label) if isinstance(snapshots, dict) else None
        if not isinstance(snapshot, dict):
            errors.append(f"build metadata missing {label} snapshot")
            continue
        if snapshot.get("schema_version") != PARSER_SCHEMA_VERSION:
            errors.append(f"{label} snapshot has incompatible schema_version: {snapshot.get('schema_version')}")
        if not snapshot.get("parser_version"):
            errors.append(f"{label} snapshot lacks parser_version")
        if not snapshot.get("source_format"):
            errors.append(f"{label} snapshot lacks source_format")
        try:
            item_count = int(snapshot.get("item_count") or 0)
        except (TypeError, ValueError):
            item_count = 0
        if item_count <= 0:
            errors.append(f"{label} snapshot has no items")
        for error in snapshot.get("validation_errors") or []:
            errors.append(f"{label} snapshot: {error}")
    return errors


def validate_inventory(root: Path, output_dir: Path | None = None, strict_reconciliation: bool = False) -> list[str]:
    inventory_dir = output_dir or repo_path(root, DEFAULT_INVENTORY_DIR)
    index_path = inventory_dir / INDEX_JSONL
    errors: list[str] = []
    if not index_path.exists():
        errors.append(f"Missing custom metadata inventory: {index_path.relative_to(root).as_posix() if index_path.is_relative_to(root) else index_path.as_posix()}")
        return errors
    try:
        rows = read_inventory(index_path)
    except Exception as exc:
        return [f"Custom metadata inventory does not parse: {exc}"]
    seen: set[str] = set()
    comparison_modes = {str(row.get("comparison_mode", "")) for row in rows}
    for line_index, row in enumerate(rows, 1):
        if row.get("schema_version") != CUSTOM_METADATA_SCHEMA_VERSION:
            errors.append(f"row {line_index}: unsupported schema_version: {row.get('schema_version')}")
        for field_name in ("item_id", "item_key", "comparison_mode", "metadata_kind", "metadata_full_name", "part_kind", "change_type", "status"):
            if not row.get(field_name):
                errors.append(f"row {line_index}: missing required field {field_name}")
        key = str(row.get("item_key", ""))
        if key in seen:
            errors.append(f"row {line_index}: duplicate item_key: {key}")
        seen.add(key)
        if row.get("comparison_mode") not in COMPARISON_MODES:
            errors.append(f"row {line_index}: invalid comparison_mode: {row.get('comparison_mode')}")
        if row.get("part_kind") not in PART_KINDS:
            errors.append(f"row {line_index}: invalid part_kind: {row.get('part_kind')}")
        if row.get("change_type") not in CHANGE_TYPES:
            errors.append(f"row {line_index}: invalid change_type: {row.get('change_type')}")
        if row.get("status") not in STATUSES:
            errors.append(f"row {line_index}: invalid status: {row.get('status')}")
        if not _source_refs_present(row):
            errors.append(f"row {line_index}: missing parser source references")
    if len(comparison_modes) > 1:
        errors.append("Inventory contains multiple comparison modes")
    for relative in (INDEX_CSV, OBJECT_SUMMARY_JSON, SOURCE_BALANCE_JSON):
        if not (inventory_dir / relative).exists():
            errors.append(f"Missing custom metadata artifact: {(inventory_dir / relative).as_posix()}")
    errors.extend(validate_build_metadata(inventory_dir, comparison_modes))
    if strict_reconciliation and rows:
        errors.extend(validate_reconciliation(root, rows, next(iter(comparison_modes or {COMPARISON_MODE_FULL})), inventory_dir))
    return errors


def validate_reconciliation(root: Path, inventory_rows: list[dict[str, Any]], comparison_mode: str, inventory_dir: Path) -> list[str]:
    errors: list[str] = []
    covered_objects = {str(row.get("metadata_full_name", "")) for row in inventory_rows}
    exceptions = _read_exceptions(inventory_dir / RECONCILIATION_EXCEPTIONS_CSV)
    for relative in ("analysis/indexes/diff-inventory.csv", "analysis/indexes/final-diff-inventory.csv"):
        for row in _read_csv(repo_path(root, relative)):
            diff_id = row.get("diff_id", "")
            object_name = row.get("object_name", "")
            status = row.get("status", "")
            if not diff_id or not object_name or diff_id in exceptions or not _row_is_non_noise(row):
                continue
            if status and status in {"technical_noise", "excluded_noise"}:
                continue
            if comparison_mode == COMPARISON_MODE_REDUCED and _research_row_is_reduced_mode_filtered(row, covered_objects):
                continue
            if not any(_inventory_row_covers_research_row(inventory_row, row) for inventory_row in inventory_rows):
                errors.append(f"{relative}: accepted research row has no parser inventory coverage and no exception: {diff_id} {object_name}")
    return errors


def _snapshot_from_args(root: Path, snapshot_arg: str, source_root_arg: str, source_format: str, cache_name: str) -> ParsedConfigurationSnapshot:
    if snapshot_arg:
        return read_snapshot(repo_path(root, snapshot_arg) if not Path(snapshot_arg).is_absolute() else Path(snapshot_arg))
    if not source_root_arg:
        raise InventoryError(f"Either --{cache_name}-snapshot or --{cache_name}-source-root is required")
    source_root = Path(source_root_arg)
    if not source_root.is_absolute():
        source_root = repo_path(root, source_root_arg)
    snapshot = parse_configuration_source(source_root, source_format)
    cache_dir = repo_path(root, "analysis/cache/custom-metadata/parser")
    cache_dir.mkdir(parents=True, exist_ok=True)
    write_snapshot(snapshot, cache_dir / f"{cache_name}.{snapshot.source_format}.snapshot.json")
    return snapshot


def build_inventory(
    root: Path,
    typical: ParsedConfigurationSnapshot,
    customer: ParsedConfigurationSnapshot,
    comparison_mode: str,
    output_dir: Path,
) -> dict[str, Any]:
    rows = compare_snapshots(typical, customer, comparison_mode)
    enrich_rows(root, rows)
    paths = write_inventory(root, output_dir, rows, comparison_mode, typical, customer)
    return {"rows": rows, "paths": paths}


def build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    output_dir = Path(args.output_dir) if args.output_dir else repo_path(root, DEFAULT_INVENTORY_DIR)
    if not output_dir.is_absolute():
        output_dir = repo_path(root, output_dir.as_posix())
    try:
        typical = _snapshot_from_args(root, args.typical_snapshot, args.typical_source_root, args.source_format, "typical")
        customer = _snapshot_from_args(root, args.customer_snapshot, args.customer_source_root, args.source_format, "customer")
        result = build_inventory(root, typical, customer, args.comparison_mode, output_dir)
    except InventoryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    errors = validate_inventory(root, output_dir, strict_reconciliation=args.strict_reconciliation)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"custom_metadata_inventory: {result['paths']['index_jsonl'].relative_to(root).as_posix() if result['paths']['index_jsonl'].is_relative_to(root) else result['paths']['index_jsonl'].as_posix()}")
    print(f"rows: {len(result['rows'])}")
    print(f"comparison_mode: {args.comparison_mode}")
    return 0


def validate_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    output_dir = Path(args.output_dir) if args.output_dir else repo_path(root, DEFAULT_INVENTORY_DIR)
    if not output_dir.is_absolute():
        output_dir = repo_path(root, output_dir.as_posix())
    errors = validate_inventory(root, output_dir, strict_reconciliation=args.strict_reconciliation)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"custom_metadata_inventory_valid: {output_dir.relative_to(root).as_posix() if output_dir.is_relative_to(root) else output_dir.as_posix()}")
    return 0


def export_markdown(rows: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, dict[str, dict[tuple[str, str], list[dict[str, Any]]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in rows:
        reconciliation = row.get("reconciliation") or {}
        links = reconciliation.get("links") if isinstance(reconciliation, dict) else {}
        feature_ids = links.get("feature_ids") if isinstance(links, dict) else []
        feature_label = "Feature " + ", ".join(feature_ids) if feature_ids else "Feature unlinked"
        review_status = str(reconciliation.get("status") or row.get("status") or "unknown")
        grouped[str(row.get("metadata_kind", ""))][str(row.get("metadata_full_name", ""))][(feature_label, review_status)].append(row)
    lines = ["# Custom metadata inventory", ""]
    for metadata_kind in sorted(grouped):
        lines.extend([f"## {metadata_kind}", ""])
        for metadata_full_name in sorted(grouped[metadata_kind]):
            lines.extend([f"### {metadata_full_name}", ""])
            for feature_label, review_status in sorted(grouped[metadata_kind][metadata_full_name]):
                lines.extend([f"#### {feature_label} / {review_status}", ""])
                for row in sorted(grouped[metadata_kind][metadata_full_name][(feature_label, review_status)], key=lambda item: (str(item.get("part_kind", "")), str(item.get("part_name", "")))):
                    lines.append(
                        f"- `{row.get('part_kind')}` `{row.get('part_name') or row.get('metadata_name')}` "
                        f"- {row.get('change_type')} / {row.get('status')}"
                    )
                lines.append("")
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def _label(value: Any, labels: dict[str, str]) -> str:
    text = str(value or "")
    return labels.get(text, text)


def _spreadsheet_row(row: dict[str, Any]) -> dict[str, str]:
    reconciliation = row.get("reconciliation") or {}
    links = reconciliation.get("links") if isinstance(reconciliation, dict) else {}
    link_values = links if isinstance(links, dict) else {}
    metadata_kind = str(row.get("metadata_kind", ""))
    metadata_name = str(row.get("metadata_name", ""))
    metadata_full_name = f"{_label(metadata_kind, METADATA_KIND_LABELS)}.{metadata_name}" if metadata_kind and metadata_name else str(row.get("metadata_full_name", ""))
    return {
        "schema_version": str(row.get("schema_version", "")),
        "item_id": str(row.get("item_id", "")),
        "item_key": str(row.get("item_key", "")),
        "comparison_mode": _label(row.get("comparison_mode", ""), COMPARISON_MODE_LABELS),
        "source_format": _label(row.get("source_format", ""), SOURCE_FORMAT_LABELS),
        "metadata_kind": _label(metadata_kind, METADATA_KIND_LABELS),
        "metadata_name": metadata_name,
        "metadata_full_name": metadata_full_name,
        "part_kind": _label(row.get("part_kind", ""), PART_KIND_LABELS),
        "part_name": str(row.get("part_name", "")),
        "part_path": str(row.get("part_path", "")),
        "change_type": _label(row.get("change_type", ""), CHANGE_TYPE_LABELS),
        "status": _label(row.get("status", ""), STATUS_LABELS),
        "typical_item_key": str((row.get("typical_parser_ref") or {}).get("item_key", "")),
        "typical_content_hash": str((row.get("typical_parser_ref") or {}).get("content_hash", "")),
        "customer_item_key": str((row.get("customer_parser_ref") or {}).get("item_key", "")),
        "customer_content_hash": str((row.get("customer_parser_ref") or {}).get("content_hash", "")),
        "reconciliation_status": _label(reconciliation.get("status", ""), RECONCILIATION_STATUS_LABELS),
        "diff_ids": ";".join(link_values.get("diff_ids", [])),
        "final_diff_ids": ";".join(link_values.get("final_diff_ids", [])),
        "feature_ids": ";".join(link_values.get("feature_ids", [])),
        "reverse_statuses": ";".join(link_values.get("reverse_statuses", [])),
        "subject_card_slugs": ";".join(link_values.get("subject_card_slugs", [])),
        "artifact_paths": ";".join(link_values.get("artifact_paths", [])),
        "diagnostics": _stable_json(row.get("diagnostics", [])),
    }


def export_spreadsheet(rows: list[dict[str, Any]], output_path: Path) -> None:
    sheet_rows = [[label for _, label in SPREADSHEET_FIELDS]]
    for row in rows:
        spreadsheet_row = _spreadsheet_row(row)
        sheet_rows.append([spreadsheet_row[field] for field, _ in SPREADSHEET_FIELDS])
    write_minimal_xlsx(output_path, sheet_rows, force=True)


def export_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    inventory_dir = Path(args.output_dir) if args.output_dir else repo_path(root, DEFAULT_INVENTORY_DIR)
    if not inventory_dir.is_absolute():
        inventory_dir = repo_path(root, inventory_dir.as_posix())
    index_path = inventory_dir / INDEX_JSONL
    if not index_path.exists():
        print(f"Missing custom metadata inventory: {index_path}", file=sys.stderr)
        return 1
    rows = read_inventory(index_path)
    report_dir = inventory_dir / "reports"
    if args.format == "markdown":
        output_path = report_dir / "custom-metadata.md"
        export_markdown(rows, output_path)
    else:
        output_path = report_dir / "custom-metadata.xlsx"
        export_spreadsheet(rows, output_path)
    print(f"custom_metadata_report: {output_path.relative_to(root).as_posix() if output_path.is_relative_to(root) else output_path.as_posix()}")
    return 0
