from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .common import read_toml, repo_path, utc_now_iso


DEFAULT_OUTPUT_DIR = "outputs/review"
MAX_SUMMARY_CHARS = 12000
MAX_SAMPLE_ROWS = 8
DETAIL_MAP_SECTIONS = {
    "attributes": "Реквизиты",
    "form_rules": "Правила формы",
    "validations": "Проверки заполнения",
    "lifecycle": "Жизненный цикл",
    "rights": "Права и роли",
    "scheduled_jobs": "Регламентные задания",
    "ui": "Интерфейс 1С",
    "integrations": "Интеграции",
    "sources": "Источники",
    "open_questions": "Открытые вопросы",
}


STATUS_LABELS = {
    "complete": "Завершено",
    "blocked_by_infobase_data": "Нужны данные ИБ",
    "requires_1c_review": "Требует ревью 1С",
    "requires_runtime_verification": "Требует проверки в ИБ",
    "needs_reclassification": "Требует переклассификации",
    "open_question": "Открытый вопрос",
    "open": "Открыто",
    "closed": "Закрыто",
    "disabled": "Отключено",
    "enabled": "Включено",
    "active": "Включено",
    "inactive": "Отключено",
    "confirmed_in_scenario": "Подтверждено в сценарии",
    "supporting_shared": "Общая поддержка",
    "needs_manual_review": "Требует ручного ревью",
    "needs_infobase_data": "Нужны данные ИБ",
    "needs_runtime_verification": "Нужна проверка в ИБ",
    "belongs_to_other_scenario": "Другой сценарий",
    "technical_platform": "Техническая платформа",
    "technical_noise": "Технический шум",
    "out_of_scope": "Вне рамок",
    "draft": "Черновик",
    "needs_review": "Требует ревью",
    "candidate": "Кандидат",
    "needs_static_analysis": "Нужен статический анализ",
    "needs_runtime_data": "Нужны данные ИБ",
    "needs_ui_check": "Нужна проверка интерфейса",
    "ready_for_review": "Готово к ревью",
    "reviewed": "Отревьюировано",
    "rejected": "Отклонено",
    "merged_into_other": "Объединено с другой",
    "accepted": "Принято",
    "supporting": "Поддерживающий слой",
    "unclassified": "Не классифицировано",
}

CONFIDENCE_LABELS = {
    "high": "Высокая",
    "medium": "Средняя",
    "low": "Низкая",
    "": "Не указана",
}

CLASSIFICATION_LABELS = {
    "confirmed business feature": "Подтвержденная бизнес-функция",
    "access/security change": "Права и безопасность",
    "integration customization": "Интеграционная доработка",
    "platform automation": "Регламентные и фоновые операции",
    "data migration or service logic": "Миграционная/сервисная логика",
    "ui customization": "Пользовательский интерфейс",
    "reference data model": "Нормативно-справочная модель",
    "platform compatibility customization": "Платформенная совместимость",
}

DETAIL_MAP_TYPE_LABELS = {
    "document": "Документ",
    "catalog": "Справочник",
    "route": "Маршрут",
    "scheduled_job": "Регламентное задание",
    "rights": "Права и роли",
    "integration": "Интеграция",
    "report": "Отчет",
    "ui": "Пользовательский интерфейс",
    "other": "Прочее",
}

SUBJECT_TYPE_LABELS = {
    "business_process": "Бизнес-процесс",
    "business_document": "Бизнес-документ",
    "reference_model": "НСИ / справочная модель",
    "integration": "Интеграция",
    "access_model": "Модель доступа",
    "ui_surface": "Интерфейс 1С",
    "background_automation": "Фоновая автоматизация",
    "technical_support": "Техническая поддержка",
}

SUBJECT_RELATION_LABELS = {
    "covered": "Покрыто карточкой",
    "covered_by_subject_card": "Покрыто карточкой",
    "unclassified": "Требует классификации",
    "technical_support": "Технический слой",
    "supporting": "Поддерживает",
    "shared": "Общий слой",
    "rejected": "Отклонено",
}

GENERATION_MODE_LABELS = {
    "generated": "Сгенерировано",
    "manual": "Ручная карта",
    "enriched": "Дообогащено",
    "subject_card": "Карточка доработки",
    "": "Не указано",
}

COMPLETENESS_LABELS = {
    "generated_seed": "Автоинвентаризация",
    "partial": "Частично",
    "medium": "Средняя",
    "high": "Высокая",
    "complete": "Полная",
    "": "Не указана",
}

RISK_STATUSES = {
    "blocked_by_infobase_data",
    "requires_1c_review",
    "requires_runtime_verification",
    "needs_reclassification",
}

PROJECT_PRODUCT_LABELS = {
    "1C Document Management": "1С:Документооборот",
}

PROJECT_DESCRIPTION_LABELS = {
    "Concrete research workspace generated from 1c-autoresearch-template.": "Рабочий репозиторий исследования доработок 1С.",
}

SOURCE_MODE_LABELS = {
    "generated": "сформировано автоматически",
    "manual": "заполнено вручную",
    "enriched": "дозаполнено по артефактам",
    "subject_card": "карточка доработки",
    "subject_card_refined": "карточка доработки, уточненная по источникам",
    "feature_candidate_refined": "кандидат, уточненный по источникам",
    "registry": "реестр доработок",
    "": "не указан",
}

CHECK_METHOD_LABELS = {
    "direct_postgresql_scheduledjobs_and_extension_bsl": "Прямой запрос к PostgreSQL и проверка BSL расширения",
    "direct_postgresql_reference_scan": "Прямой запрос к PostgreSQL по справочникам",
    "direct_postgresql_scheduledjobs": "Прямой запрос к PostgreSQL по регламентным заданиям",
    "1c_mcp_run_select_query": "Запрос 1С-MCP без записи",
    "1c_mcp_debug_execute_bsl": "Выполнение BSL через 1С-MCP на демо-ИБ",
    "playwright_1c_web_ui": "Проверка веб-интерфейса 1С",
    "direct_postgresql_query": "Прямой read-only запрос к PostgreSQL",
    "manual_1c_scenario": "Ручная проверка сценария в 1С",
}

INFOBASE_RESULT_LABELS = {
    "custom_only": "только custom",
    "same_as_vendor": "как у вендора",
    "vendor_differs": "отличается от вендора",
    "runtime_only": "только данные ИБ",
    "manual_scenario_required": "нужен ручной сценарий",
    "inconclusive": "не закрыто",
    "": "не указан",
}

SOURCE_KIND_LABELS = {
    "clean-diff": "очищенное сравнение",
    "clean_diff": "очищенное сравнение",
    "clean-rebase": "очищенное сравнение",
    "clean_rebase_and_infobase": "очищенное сравнение и данные ИБ",
    "clean_rebase_diff": "очищенное сравнение",
    "extension": "расширение 1С",
    "extension_bsl": "BSL расширения",
    "extension_cross_scenario": "расширение 1С, общий слой",
    "extension_supporting": "расширение 1С, поддерживающий слой",
    "infobase_runtime": "данные демо-ИБ",
    "infobase_sql": "запрос к ИБ",
    "target_cf": "доработанная конфигурация",
    "target_cfe": "доработанное расширение",
    "target_cf_and_extension": "конфигурация и расширение",
    "target_cf_cross_scenario": "конфигурация, общий слой",
    "vendor_baseline": "типовая конфигурация",
}

HUMAN_TEXT_FIELDS = {
    "claim",
    "comment",
    "condition",
    "description",
    "domain",
    "effect",
    "evidence",
    "flow",
    "impact",
    "identification",
    "key_conclusion",
    "mechanism",
    "needed",
    "needed_input",
    "notes",
    "question",
    "rationale",
    "relation",
    "result",
    "review_notes",
    "review_status",
    "rule",
    "runtime_data_needed",
    "summary",
    "upgrade_risk",
    "validation",
    "why_open",
    "why_separate_card",
}

DASHBOARD_TEXT_REPLACEMENTS = [
    ("Runtime-проверки", "Проверки в ИБ"),
    ("runtime-проверки", "проверки в ИБ"),
    ("runtime-проверок", "проверок в ИБ"),
    ("runtime-проверка", "проверка в ИБ"),
    ("runtime количество", "количество записей ИБ"),
    ("runtime-вопросы", "вопросы по данным ИБ"),
    ("runtime-вопрос", "вопрос по данным ИБ"),
    ("runtime-выгрузкам", "выгрузкам из ИБ"),
    ("runtime CSV", "результаты проверки ИБ"),
    ("runtime data", "данные ИБ"),
    ("Runtime", "Проверки в ИБ"),
    ("UI-поверхности", "Интерфейс 1С"),
    ("UI-поверхность", "Интерфейс 1С"),
    ("UI-проверками", "проверками интерфейса"),
    ("UI-проверки", "проверки интерфейса"),
    ("UI-проверка", "проверка интерфейса"),
    ("UI-команда", "команда интерфейса"),
    ("web UI", "веб-интерфейс 1С"),
    ("UI/runtime", "интерфейс и ИБ"),
    ("UI/НСИ", "интерфейс и НСИ"),
    ("ИБ/UI", "ИБ и интерфейс"),
    ("Формы/UI", "Формы и команды"),
    ("clean diff", "очищенное сравнение"),
    ("target_cf", "доработанная конфигурация"),
    ("read-only", "без записи"),
    ("production-вывода", "выводов по рабочей базе"),
    ("production-статистика", "статистика рабочей базы"),
    ("generated detail maps", "сгенерированные технические карты"),
    ("detail maps", "технические карты"),
    ("subject cards", "карточки доработок"),
    ("subject card", "карточка доработки"),
    ("registry", "реестр"),
    ("candidate/unclassified/supporting/technical", "кандидат / не классифицировано / поддерживающий слой / технический слой"),
    ("Merged / rejected / supporting", "Объединено / отклонено / поддерживающий слой"),
    ("Supporting", "Поддерживающий слой"),
    ("Merged", "Объединено"),
    ("Rejected", "Отклонено"),
    ("split/merge", "разделения или объединения"),
    ("coverage", "покрытие"),
    ("counts", "количество записей"),
    ("evidence rows", "строк доказательств"),
    ("evidence", "доказательства"),
    ("gaps", "пробелы"),
    ("Evidence", "Доказательство"),
    ("Upgrade-риск", "Риск перехода на ДО 3.0"),
    ("upgrade risk", "риск перехода на ДО 3.0"),
    ("final-gate", "финальная проверка"),
    ("smoke-test", "контрольная проверка запуска"),
    ("Dashboard", "Дашборд"),
    ("dashboard", "дашборд"),
    ("ServerCall", "серверный вызов"),
    ("guards тонкого клиента", "условия выполнения для тонкого клиента"),
    ("startup/session hooks", "обработчики запуска и параметры сеанса"),
    ("startup/session", "запуск и параметры сеанса"),
    ("vendor-релиз", "целевой типовой релиз"),
    ("backlog", "перечень требований"),
    ("granularity", "детализация"),
    ("metadata", "метаданные"),
]

PATH_PATTERN = re.compile(r"(?:analysis|outputs|sources|work|scenarios)/[^\s;,]+")


def humanize_dashboard_text(value: Any) -> str:
    text = "" if value is None else str(value)
    for source, target in DASHBOARD_TEXT_REPLACEMENTS:
        text = text.replace(source, target)
    return text


def source_mode_label(value: str) -> str:
    raw = (value or "").strip()
    return SOURCE_MODE_LABELS.get(raw, raw or "не указан")


def source_kind_label(value: str) -> str:
    raw = (value or "").strip()
    return SOURCE_KIND_LABELS.get(raw, raw or "не указан")


def humanize_row_fields(row: dict[str, str], fields: set[str] = HUMAN_TEXT_FIELDS) -> dict[str, str]:
    result = {
        key: humanize_dashboard_text(value) if key in fields else value
        for key, value in row.items()
    }
    if "status" in row and "status_label" not in result:
        result["status_label"] = status_label(row.get("status", ""))
    if "confidence" in row and "confidence_label" not in result:
        result["confidence_label"] = confidence_label(row.get("confidence", ""))
    return result


def artifact_refs(value: str) -> str:
    refs = PATH_PATTERN.findall(value or "")
    return "; ".join(dict.fromkeys(refs))


def infobase_result_label(row: dict[str, str]) -> str:
    method = (row.get("check_method") or "").strip()
    if method.startswith("1c_mcp"):
        return "Закрыто проверкой через 1С-MCP на демо-ИБ."
    if method == "playwright_1c_web_ui":
        return "Закрыто проверкой веб-интерфейса 1С."
    if method.startswith("direct_postgresql"):
        return "Закрыто прямой проверкой данных PostgreSQL и метаданных демо-ИБ."
    return "Закрыто проверкой по данным демо-ИБ."


def normalize_infobase_checks(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        status = (row.get("status_after_pass") or "").strip()
        method = (row.get("check_method") or "").strip()
        artifacts = artifact_refs(row.get("notes", "")) or artifact_refs(row.get("result", ""))
        normalized.append(
            {
                "check_id": row.get("check_id", ""),
                "scenario_id": row.get("scenario_id", ""),
                "item_id": row.get("item_id", ""),
                "feature_id": row.get("feature_id", ""),
                "subject_card_slug": row.get("subject_card_slug", ""),
                "status_after_pass": status,
                "status_after_pass_label": status_label(status),
                "check_method": method,
                "check_method_label": CHECK_METHOD_LABELS.get(method, method or "не указан"),
                "custom_target": row.get("custom_target", ""),
                "vendor_target": row.get("vendor_target", ""),
                "result": row.get("result", ""),
                "result_comparison_label": INFOBASE_RESULT_LABELS.get(row.get("result", ""), row.get("result", "") or "не указан"),
                "result_label": infobase_result_label(row),
                "evidence_ref": row.get("evidence_ref", ""),
                "artifact_refs": artifacts or row.get("evidence_ref", "") or "Подробности см. в исходном CSV: analysis/reverse-map/infobase-checks.csv",
            }
        )
    return normalized


def normalize_infobase_questions(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        status = (row.get("status") or "").strip()
        normalized.append(
            {
                **humanize_row_fields(row),
                "status": status,
                "status_label": status_label(status),
                "artifact_refs": row.get("source_ref", "") or "Подробности см. в исходном CSV: outputs/infobase-questions.csv",
            }
        )
    return normalized


def add_status_labels(rows: list[dict[str, str]], key: str = "status") -> list[dict[str, str]]:
    return [{**row, f"{key}_label": status_label(row.get(key, ""))} for row in rows]


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def read_text(path: Path, limit: int = MAX_SUMMARY_CHARS) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "\n\n[Текст обрезан в дашборде; полный файл см. в исходном артефакте.]"
    return text


def relative_href(output_dir: Path, target: Path) -> str:
    try:
        return target.resolve().relative_to(output_dir.resolve()).as_posix()
    except ValueError:
        try:
            return target.resolve().relative_to(output_dir.resolve().parent.parent).as_posix()
        except ValueError:
            return target.as_posix()


def repo_href(root: Path, output_dir: Path, relative: str) -> str:
    if not relative:
        return ""
    target = repo_path(root, relative)
    return os.path.relpath(target.resolve(), output_dir.resolve()).replace(os.sep, "/")


def split_refs(value: str) -> list[str]:
    return [part.strip() for part in value.replace("\n", ";").split(";") if part.strip()]


def status_label(value: str) -> str:
    return STATUS_LABELS.get((value or "").strip(), value or "Не указано")


def confidence_label(value: str) -> str:
    return CONFIDENCE_LABELS.get((value or "").strip(), value or "Не указана")


def classification_label(value: str) -> str:
    return CLASSIFICATION_LABELS.get((value or "").strip(), value or "Не указана")


def non_empty_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if any((value or "").strip() for value in row.values())]


def load_project(root: Path) -> dict[str, str]:
    path = repo_path(root, "project.toml")
    if not path.exists():
        return {}
    manifest = read_toml(path)
    project = manifest.get("project", {})
    autopilot = manifest.get("autopilot", {})
    product = str(project.get("product", "") or "")
    description = str(project.get("description", "") or "")
    return {
        "id": str(project.get("id", "") or ""),
        "product": PROJECT_PRODUCT_LABELS.get(product, product),
        "baseline_version": str(project.get("baseline_version", "") or ""),
        "target_version": str(project.get("target_version", "") or ""),
        "next_vendor_version": str(project.get("next_vendor_version", "") or ""),
        "description": PROJECT_DESCRIPTION_LABELS.get(description, humanize_dashboard_text(description)),
        "autopilot_enabled": str(autopilot.get("enabled", "")).lower(),
    }


def group_by(rows: list[dict[str, str]], key: str) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row.get(key) or "").strip()].append(row)
    return grouped


def count_values(rows: list[dict[str, str]], key: str) -> dict[str, int]:
    counts = Counter((row.get(key) or "").strip() or "not_set" for row in rows)
    return dict(sorted(counts.items()))


def sample_rows(
    rows: list[dict[str, str]],
    fields: list[str],
    limit: int = MAX_SAMPLE_ROWS,
    humanize_fields: set[str] | None = None,
) -> list[dict[str, str]]:
    sampled: list[dict[str, str]] = []
    for row in rows[:limit]:
        sampled_row: dict[str, str] = {}
        for field in fields:
            value = row.get(field, "")
            sampled_row[field] = humanize_dashboard_text(value) if humanize_fields and field in humanize_fields else value
        sampled.append(sampled_row)
    return sampled


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def text_list(value: Any) -> list[str]:
    return [str(item) for item in as_list(value) if str(item).strip()]


def detail_type_label(value: str) -> str:
    return DETAIL_MAP_TYPE_LABELS.get((value or "").strip(), value or "Не указано")


def generation_mode_label(value: str) -> str:
    return GENERATION_MODE_LABELS.get((value or "").strip(), value or "Не указано")


def completeness_label(value: str) -> str:
    return COMPLETENESS_LABELS.get((value or "").strip(), value or "Не указана")


def subject_type_label(value: str) -> str:
    return SUBJECT_TYPE_LABELS.get((value or "").strip(), value or "Не указано")


def subject_relation_label(value: str) -> str:
    return SUBJECT_RELATION_LABELS.get((value or "").strip(), value or "Не указано")


def normalize_detail_rows(rows: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in as_list(rows):
        if isinstance(row, dict):
            normalized.append(humanize_row_fields({str(key): "" if value is None else str(value) for key, value in row.items()}))
    return normalized


def load_detail_maps(root: Path, output_dir: Path) -> list[dict[str, Any]]:
    detail_root = repo_path(root, "analysis/detail-maps")
    if not detail_root.exists():
        return []
    maps: list[dict[str, Any]] = []
    for path in sorted(detail_root.rglob("detail-map.json")):
        if "_templates" in path.relative_to(detail_root).parts:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Could not parse {path.relative_to(root).as_posix()}: {exc}") from exc
        slug = str(raw.get("slug") or path.parent.name).strip()
        map_type = str(raw.get("type", "") or "other").strip()
        generation_mode = str(raw.get("generation_mode", "") or "manual").strip()
        completeness = str(raw.get("completeness", "") or "").strip()
        status = str(raw.get("status", "") or "draft").strip()
        confidence = str(raw.get("confidence", "") or "").strip()
        sections = {name: normalize_detail_rows(raw.get(name, [])) for name in DETAIL_MAP_SECTIONS}
        source_workbook = str(raw.get("source_workbook", "") or "").strip()
        maps.append(
            {
                "id": str(raw.get("id") or slug),
                "slug": slug,
                "title": humanize_dashboard_text(str(raw.get("title", "") or slug)),
                "type": map_type,
                "type_label": detail_type_label(map_type),
                "generation_mode": generation_mode,
                "generation_mode_label": generation_mode_label(generation_mode),
                "completeness": completeness,
                "completeness_label": completeness_label(completeness),
                "status": status,
                "status_label": status_label(status),
                "confidence": confidence,
                "confidence_label": confidence_label(confidence),
                "owner_feature": str(raw.get("owner_feature", "") or ""),
                "linked_features": text_list(raw.get("linked_features", [])),
                "summary": humanize_dashboard_text(raw.get("summary", "") or ""),
                "identification": humanize_dashboard_text(raw.get("identification", "") or ""),
                "key_conclusion": humanize_dashboard_text(raw.get("key_conclusion", "") or ""),
                "upgrade_risk": humanize_dashboard_text(raw.get("upgrade_risk", "") or ""),
                "runtime_data_needed": humanize_dashboard_text(raw.get("runtime_data_needed", "") or ""),
                "review_status": humanize_dashboard_text(raw.get("review_status", "") or ""),
                "migration_notes": [humanize_dashboard_text(item) for item in text_list(raw.get("migration_notes", []))],
                "source_path": path.relative_to(root).as_posix(),
                "source_href": repo_href(root, output_dir, path.relative_to(root).as_posix()),
                "source_workbook": source_workbook,
                "source_workbook_href": repo_href(root, output_dir, source_workbook) if source_workbook else "",
                "sections": sections,
                "counts": {name: len(rows) for name, rows in sections.items()},
                "open_questions_count": len(sections["open_questions"]),
            }
        )
    return maps


def load_subject_cards(root: Path, output_dir: Path) -> list[dict[str, Any]]:
    cards_root = repo_path(root, "analysis/subject-cards/cards")
    if not cards_root.exists():
        return []
    cards: list[dict[str, Any]] = []
    for path in sorted(cards_root.glob("*/subject-card.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Could not parse {path.relative_to(root).as_posix()}: {exc}") from exc
        slug = str(raw.get("slug") or path.parent.name).strip()
        status = str(raw.get("status") or "draft").strip()
        confidence = str(raw.get("confidence") or "").strip()
        sections_raw = raw.get("sections", {})
        if not isinstance(sections_raw, dict):
            sections_raw = {}
        sections = {name: normalize_detail_rows(sections_raw.get(name, [])) for name in DETAIL_MAP_SECTIONS}
        linked_features = text_list(raw.get("linked_features", []))
        subject_type = str(raw.get("subject_type") or "").strip()
        card_dir = path.parent
        evidence_rows = [humanize_row_fields(row) for row in non_empty_rows(read_csv_rows(card_dir / "evidence.csv"))]
        gaps = [humanize_row_fields(row) for row in non_empty_rows(read_csv_rows(card_dir / "gaps.csv"))]
        open_gaps = [row for row in gaps if (row.get("status") or "").strip() != "closed"]
        source_path = path.relative_to(root).as_posix()
        completeness = "complete" if status in {"ready_for_review", "reviewed"} else "partial"
        cards.append(
            {
                "id": str(raw.get("id") or slug),
                "slug": slug,
                "title": humanize_dashboard_text(str(raw.get("title") or slug)),
                "type": "subject",
                "type_label": "Предметная карта",
                "subject_type": subject_type,
                "subject_type_label": subject_type_label(subject_type),
                "origin_layer": str(raw.get("origin_layer") or ""),
                "primary_objects": text_list(raw.get("primary_objects", [])),
                "coverage_scope": str(raw.get("coverage_scope") or ""),
                "coverage_scope_label": subject_relation_label(str(raw.get("coverage_scope") or "")),
                "why_separate_card": humanize_dashboard_text(raw.get("why_separate_card") or ""),
                "merge_into": str(raw.get("merge_into") or ""),
                "split_from": str(raw.get("split_from") or ""),
                "generation_mode": "subject_card",
                "generation_mode_label": generation_mode_label("subject_card"),
                "completeness": completeness,
                "completeness_label": completeness_label(completeness),
                "status": status,
                "status_label": status_label(status),
                "confidence": confidence,
                "confidence_label": confidence_label(confidence),
                "owner_feature": linked_features[0] if linked_features else "",
                "linked_features": linked_features,
                "linked_detail_maps": text_list(raw.get("linked_detail_maps", [])),
                "summary": humanize_dashboard_text(raw.get("summary") or ""),
                "identification": humanize_dashboard_text(raw.get("identification") or ""),
                "key_conclusion": humanize_dashboard_text(raw.get("key_conclusion") or ""),
                "upgrade_risk": humanize_dashboard_text(raw.get("upgrade_risk") or ""),
                "runtime_data_needed": humanize_dashboard_text(raw.get("runtime_data_needed") or ""),
                "review_status": humanize_dashboard_text(raw.get("review_status") or ""),
                "migration_notes": [humanize_dashboard_text(item) for item in text_list(raw.get("migration_notes", []))],
                "source_mode": str(raw.get("source_mode") or ""),
                "source_mode_label": source_mode_label(str(raw.get("source_mode") or "")),
                "source_artifacts": text_list(raw.get("source_artifacts", [])),
                "source_path": source_path,
                "source_href": repo_href(root, output_dir, source_path),
                "source_workbook": "",
                "source_workbook_href": "",
                "sections": sections,
                "counts": {name: len(rows) for name, rows in sections.items()},
                "open_questions_count": len(sections["open_questions"]),
                "evidence_count": len(evidence_rows),
                "gap_count": len(open_gaps),
                "gaps": open_gaps,
            }
        )
    return cards


def load_subject_registry(root: Path, output_dir: Path) -> list[dict[str, Any]]:
    rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/subject-cards/registry.csv")))
    result: list[dict[str, Any]] = []
    for row in rows:
        row = humanize_row_fields(row)
        slug = row.get("slug", "")
        status = row.get("status", "")
        subject_type = row.get("subject_type", "")
        card_path = row.get("card_path", "")
        if not card_path:
            continue
        result.append(
            {
                **row,
                "title": humanize_dashboard_text(row.get("title", "")),
                "slug": slug,
                "subject_type_label": subject_type_label(subject_type),
                "status_label": status_label(status),
                "confidence_label": confidence_label(row.get("confidence", "")),
                "relation_label": subject_relation_label(row.get("coverage_scope", "")),
                "linked_features_list": split_refs(row.get("linked_features", "")),
                "linked_detail_maps_list": split_refs(row.get("linked_detail_maps", "")),
                "primary_objects_list": split_refs(row.get("primary_objects", "")),
                "has_card": bool(card_path),
                "card_href": f"#card/{slug}" if card_path else "",
                "card_source_href": repo_href(root, output_dir, card_path) if card_path else "",
            }
        )
    return result


def load_subject_coverage(root: Path) -> list[dict[str, Any]]:
    rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/subject-cards/coverage.csv")))
    return [
        {
            **humanize_row_fields(row),
            "relation_label": subject_relation_label(row.get("relation", "")),
            "confidence_label": confidence_label(row.get("confidence", "")),
        }
        for row in rows
    ]


def load_functional_gap_map(root: Path, output_dir: Path) -> dict[str, Any]:
    path = repo_path(root, "outputs/functional-gap-map.json")
    if not path.exists():
        return {
            "target_release": "",
            "generated_at": "",
            "summary": {
                "cards_total": 0,
                "ready_for_review": 0,
                "reviewed": 0,
                "open_blocking_checks": 0,
            },
            "cards": [],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "target_release": "",
            "generated_at": "",
            "summary": {
                "cards_total": 0,
                "ready_for_review": 0,
                "reviewed": 0,
                "open_blocking_checks": 0,
            },
            "cards": [],
        }
    decision_labels = {
        "replace_by_standard": "Заменить типовым механизмом",
        "adapt": "Адаптировать доработку",
        "preserve": "Перенести доработку",
        "retire": "Не переносить",
        "split": "Разделить решение по частям",
        "data_migration": "Проверить перенос данных",
        "business_decision": "Нужно бизнес-решение",
        "": "Решение не выбрано",
    }
    cards: list[dict[str, Any]] = []
    for raw in data.get("cards", []) or []:
        decision = str(raw.get("selected_decision") or "")
        gap_card_path = str(raw.get("gap_card_path") or "")
        review_path = str(raw.get("review_path") or "")
        normalized_raw = {**raw}
        for nested_key in ("target_findings", "object_mappings", "checks", "hypotheses"):
            nested_rows = raw.get(nested_key, [])
            if isinstance(nested_rows, list):
                normalized_raw[nested_key] = [
                    humanize_row_fields({str(key): "" if value is None else str(value) for key, value in row.items()})
                    for row in nested_rows
                    if isinstance(row, dict)
                ]
        cards.append(
            {
                **normalized_raw,
                "title": humanize_dashboard_text(raw.get("title") or raw.get("subject_card_slug") or ""),
                "status_label": status_label(str(raw.get("status") or "")),
                "gap_readiness_label": status_label(str(raw.get("gap_readiness") or "")),
                "selected_decision_label": decision_labels.get(decision, decision),
                "gap_card_href": repo_href(root, output_dir, gap_card_path) if gap_card_path else "",
                "review_href": repo_href(root, output_dir, review_path) if review_path else "",
            }
        )
    return {
        "target_release": str(data.get("target_release") or ""),
        "generated_at": str(data.get("generated_at") or ""),
        "summary": data.get("summary") or {},
        "cards": cards,
        "source_path": "outputs/functional-gap-map.json",
        "source_href": repo_href(root, output_dir, "outputs/functional-gap-map.json"),
    }


def functional_gap_dashboard_link(root: Path, output_dir: Path) -> dict[str, Any]:
    relative = "outputs/functional-gap-dashboard/index.html"
    path = repo_path(root, relative)
    return {
        "path": relative,
        "href": repo_href(root, output_dir, relative),
        "exists": path.exists(),
    }


def detail_map_summaries(detail_maps: list[dict[str, Any]], feature_id: str) -> list[dict[str, Any]]:
    linked: list[dict[str, Any]] = []
    for detail_map in detail_maps:
        if feature_id in set(detail_map.get("linked_features", [])):
            linked.append(
                {
                    "slug": detail_map["slug"],
                    "title": detail_map["title"],
                    "type": detail_map["type"],
                    "type_label": detail_map["type_label"],
                    "generation_mode": detail_map["generation_mode"],
                    "generation_mode_label": detail_map["generation_mode_label"],
                    "status": detail_map["status"],
                    "status_label": detail_map["status_label"],
                    "href": f"#card/{detail_map['slug']}" if detail_map["generation_mode"] in {"manual", "enriched", "subject_card"} else f"#detail-{detail_map['slug']}",
                }
            )
    return linked


def parse_feature_sources(value: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for part in split_refs(value):
        if ":" not in part:
            continue
        key, raw_value = part.split(":", 1)
        try:
            result[key.strip()] = int(raw_value.strip())
        except ValueError:
            continue
    return result


def build_feature(
    root: Path,
    output_dir: Path,
    row: dict[str, str],
    coverage_by_scenario: dict[str, list[dict[str, str]]],
    decisions_by_scenario: dict[str, list[dict[str, str]]],
    unresolved_by_scenario: dict[str, list[dict[str, str]]],
    infobase_by_scenario: dict[str, list[dict[str, str]]],
    detail_maps: list[dict[str, Any]],
) -> dict[str, Any]:
    row = humanize_row_fields(row)
    feature_id = (row.get("feature_id") or "").strip()
    scenario_dir = repo_path(root, f"analysis/reverse-map/scenarios/{feature_id}")
    scenario_evidence_rows = [humanize_row_fields(item) for item in non_empty_rows(read_csv_rows(scenario_dir / "evidence.csv"))]
    feature_pack_dir = repo_path(root, row.get("evidence_pack_path", "") or f"analysis/features/{feature_id}")
    feature_pack_evidence_rows = [humanize_row_fields(item) for item in non_empty_rows(read_csv_rows(feature_pack_dir / "evidence.csv"))]
    evidence_rows = scenario_evidence_rows or feature_pack_evidence_rows
    coverage_rows = [humanize_row_fields(item) for item in non_empty_rows(coverage_by_scenario.get(feature_id, []))]
    decisions = [humanize_row_fields(item) for item in non_empty_rows(decisions_by_scenario.get(feature_id, []))]
    unresolved = add_status_labels([humanize_row_fields(item) for item in non_empty_rows(unresolved_by_scenario.get(feature_id, []))])
    infobase = non_empty_rows(infobase_by_scenario.get(feature_id, []))
    coverage_statuses = count_values(coverage_rows, "status")
    coverage_confidence = count_values(coverage_rows, "confidence")
    evidence_confidence = count_values(evidence_rows, "confidence")
    source_counts = parse_feature_sources(row.get("source_bucket", ""))
    status = (row.get("status") or "").strip()
    linked_detail_maps = detail_map_summaries(detail_maps, feature_id)
    migration_risks: list[str] = []
    if unresolved:
        migration_risks.append(f"Есть открытые вопросы: {len(unresolved)}. Их нужно закрыть до уверенной карты функциональных разрывов.")
    if infobase:
        migration_risks.append(f"Есть проверки по ИБ: {len(infobase)}. Результаты демо/тестовой базы нужно отделять от выводов по рабочей базе.")
    if status in RISK_STATUSES:
        migration_risks.append(f"Статус блока: {status_label(status)}. Требуется дополнительное подтверждение перед переходом на ДО 3.0.")
    if coverage_statuses.get("supporting_shared", 0):
        migration_risks.append("Часть строк является общей поддержкой нескольких сценариев; при сравнении с ДО 3.0 нужно не задвоить разрывы.")
    if coverage_statuses.get("belongs_to_other_scenario", 0):
        migration_risks.append("Есть строки, отнесенные к другому сценарию; нужна межсценарная сверка владения.")
    if not migration_risks:
        migration_risks.append("Критичных блокеров в текущих артефактах не выделено; проверить совместимость поведения с ДО 3.0 по доказательствам блока.")

    evidence_samples = sample_rows(
        evidence_rows,
        ["claim_id", "source_kind", "source_path", "line_start", "line_end", "evidence_type", "confidence_label", "summary"],
        humanize_fields={"summary"},
    )
    for sample in evidence_samples:
        sample["source_kind_label"] = source_kind_label(sample.get("source_kind", ""))
    source_count_labels = {source_kind_label(key): value for key, value in source_counts.items()}

    return {
        "feature_id": feature_id,
        "title": row.get("title", ""),
        "domain": row.get("domain", ""),
        "classification": row.get("classification", ""),
        "classification_label": classification_label(row.get("classification", "")),
        "confidence": row.get("confidence", ""),
        "confidence_label": confidence_label(row.get("confidence", "")),
        "status": status,
        "status_label": status_label(status),
        "owner": row.get("owner", ""),
        "summary": row.get("summary", ""),
        "notes": row.get("notes", ""),
        "source_bucket": row.get("source_bucket", ""),
        "source_counts": source_counts,
        "source_count_labels": source_count_labels,
        "evidence_pack_path": row.get("evidence_pack_path", ""),
        "evidence_pack_href": repo_href(root, output_dir, row.get("evidence_pack_path", "")) if row.get("evidence_pack_path") else "",
        "scenario_summary_path": f"analysis/reverse-map/scenarios/{feature_id}/summary.md",
        "scenario_summary_href": repo_href(root, output_dir, f"analysis/reverse-map/scenarios/{feature_id}/summary.md"),
        "coverage_count": len(coverage_rows),
        "coverage_statuses": coverage_statuses,
        "coverage_confidence": coverage_confidence,
        "coverage_samples": sample_rows(
            coverage_rows,
            ["diff_id", "source", "path", "status_label", "confidence_label", "evidence_ref", "notes"],
            humanize_fields={"notes"},
        ),
        "evidence_count": len(evidence_rows),
        "evidence_confidence": evidence_confidence,
        "evidence_samples": evidence_samples,
        "decisions_count": len(decisions),
        "decision_samples": sample_rows(decisions, ["decision_id", "decision", "confidence_label", "rationale", "evidence_ref"], humanize_fields={"rationale"}),
        "open_questions_count": len(unresolved),
        "open_questions": unresolved,
        "infobase_checks_count": len(infobase),
        "infobase_checks": infobase,
        "detail_maps_count": len(linked_detail_maps),
        "detail_maps": linked_detail_maps,
        "migration_risks": migration_risks,
    }


def output_files(root: Path, output_dir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for relative in (
        "outputs/customization-map.md",
        "outputs/customization-map.xlsx",
        "outputs/open-questions.csv",
        "outputs/infobase-questions.csv",
        "outputs/open-questions.xlsx",
        "outputs/functional-gap-map.md",
        "outputs/functional-gap-map.json",
        "outputs/functional-gap-dashboard/index.html",
        "outputs/functional-gap-dashboard/data.json",
        "analysis/final-audit.md",
    ):
        path = repo_path(root, relative)
        files.append(
            {
                "path": relative,
                "href": repo_href(root, output_dir, relative),
                "exists": path.exists(),
                "size": path.stat().st_size if path.exists() else 0,
            }
        )
    return files


def subject_card_dashboard_data(root: Path, output_dir: Path, project: dict[str, str], cards: list[dict[str, Any]], slug: str) -> dict[str, Any]:
    selected = [card for card in cards if card["slug"] == slug]
    if not selected:
        available = ", ".join(card["slug"] for card in cards) or "нет карточек"
        raise RuntimeError(f"Subject card not found: {slug}. Available: {available}")

    card = selected[0]
    card_questions = normalize_detail_rows((card.get("sections") or {}).get("open_questions", []))
    summary = {
        "feature_count": 0,
        "diff_count": 0,
        "final_diff_count": 0,
        "final_feature_count": 0,
        "coverage_count": 0,
        "coverage_by_status": {},
        "feature_by_status": {},
        "feature_by_confidence": {},
        "open_question_count": len(card_questions),
        "output_open_question_count": 0,
        "infobase_check_count": 0,
        "closed_infobase_check_count": 0,
        "risk_feature_count": 0,
        "detail_map_count": 0,
        "subject_map_count": 1,
        "technical_map_count": 0,
        "complete_subject_map_count": 1 if card.get("key_conclusion") and card.get("upgrade_risk") else 0,
        "detail_map_by_type": {},
        "detail_map_by_generation_mode": {},
        "detail_map_open_question_count": len(card_questions),
        "subject_card_by_status": {card.get("status", "not_set"): 1},
        "subject_registry_count": 1,
        "subject_registry_ready_count": 1,
        "subject_registry_candidate_count": 0,
        "subject_registry_by_type": {card.get("subject_type") or "not_set": 1},
        "subject_coverage_count": 0,
        "subject_bf_coverage_count": 0,
        "subject_bf_covered_count": 0,
        "subject_bf_unclassified_count": 0,
        "subject_bf_unique_count": 0,
        "subject_bf_unique_covered_count": 0,
        "subject_bf_unique_unclassified_count": 0,
    }
    return {
        "schema_version": "review-dashboard/v1",
        "dashboard_scope": {
            "mode": "subject_card",
            "subject_card": slug,
            "title": card.get("title", slug),
        },
        "generated_at": utc_now_iso(),
        "project": project,
        "summary": summary,
        "features": [],
        "subject_cards": selected,
        "subject_maps": selected,
        "subject_registry": [],
        "subject_card_coverage": [],
        "functional_gap_dashboard": functional_gap_dashboard_link(root, output_dir),
        "functional_gap_map": {
            "target_release": "",
            "generated_at": "",
            "summary": {
                "cards_total": 0,
                "ready_for_review": 0,
                "reviewed": 0,
                "open_blocking_checks": 0,
            },
            "cards": [],
        },
        "detail_maps": [],
        "open_questions": card_questions,
        "output_open_questions": [],
        "infobase_checks": [],
        "risk_features": [],
        "final_audit": {
            "path": "",
            "href": "",
            "text": "",
        },
        "outputs": [],
    }


def build_dashboard_data(root: Path, output_dir: Path, subject_card: str = "") -> dict[str, Any]:
    project = load_project(root)
    feature_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/indexes/feature-map.csv")))
    diff_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/indexes/diff-inventory.csv")))
    final_diff_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/indexes/final-diff-inventory.csv")))
    final_feature_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/indexes/final-feature-map.csv")))
    coverage_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/reverse-map/coverage.csv")))
    decisions_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/reverse-map/decisions.csv")))
    unresolved_rows = add_status_labels([humanize_row_fields(row) for row in non_empty_rows(read_csv_rows(repo_path(root, "analysis/reverse-map/unresolved.csv")))])
    infobase_rows = normalize_infobase_checks(non_empty_rows(read_csv_rows(repo_path(root, "analysis/reverse-map/infobase-checks.csv"))))
    infobase_questions = normalize_infobase_questions(non_empty_rows(read_csv_rows(repo_path(root, "outputs/infobase-questions.csv"))))
    output_open_questions = [humanize_row_fields(row) for row in non_empty_rows(read_csv_rows(repo_path(root, "outputs/open-questions.csv")))]
    detail_maps = load_detail_maps(root, output_dir)
    subject_cards = load_subject_cards(root, output_dir)
    subject_registry = load_subject_registry(root, output_dir)
    subject_coverage = load_subject_coverage(root)
    functional_gap_map = load_functional_gap_map(root, output_dir)
    if subject_card:
        return subject_card_dashboard_data(root, output_dir, project, subject_cards, subject_card)

    subject_maps = subject_cards
    technical_maps = [detail_map for detail_map in detail_maps if detail_map["generation_mode"] == "generated"]

    if not feature_rows:
        raise RuntimeError("No feature rows found in analysis/indexes/feature-map.csv")

    coverage_by_scenario = group_by(coverage_rows, "scenario_id")
    decisions_by_scenario = group_by(decisions_rows, "scenario_id")
    unresolved_by_scenario = group_by(unresolved_rows, "scenario_id")
    infobase_by_scenario = group_by(infobase_rows, "scenario_id")
    features = [
        build_feature(
            root,
            output_dir,
            row,
            coverage_by_scenario,
            decisions_by_scenario,
            unresolved_by_scenario,
            infobase_by_scenario,
            detail_maps,
        )
        for row in feature_rows
    ]

    risk_features = [
        {
            "feature_id": feature["feature_id"],
            "title": feature["title"],
            "status": feature["status"],
            "status_label": feature["status_label"],
            "open_questions_count": feature["open_questions_count"],
            "infobase_checks_count": feature["infobase_checks_count"],
            "risks": feature["migration_risks"],
        }
        for feature in features
        if feature["open_questions_count"] or feature["infobase_checks_count"] or feature["status"] in RISK_STATUSES
    ]
    subject_bf_rows = [row for row in subject_coverage if row.get("source_kind") == "BF" and row.get("feature_id")]
    subject_bf_feature_ids = {row["feature_id"] for row in subject_bf_rows}
    subject_bf_covered_ids = {row["feature_id"] for row in subject_bf_rows if row.get("relation") == "covered_by_subject_card"}
    subject_bf_unclassified_ids = {
        row["feature_id"]
        for row in subject_bf_rows
        if row.get("relation") == "unclassified" and row["feature_id"] not in subject_bf_covered_ids
    }

    summary = {
        "feature_count": len(features),
        "diff_count": len(diff_rows),
        "final_diff_count": len(final_diff_rows),
        "final_feature_count": len(final_feature_rows),
        "coverage_count": len(coverage_rows),
        "coverage_by_status": count_values(coverage_rows, "status"),
        "feature_by_status": count_values(feature_rows, "status"),
        "feature_by_confidence": count_values(feature_rows, "confidence"),
        "open_question_count": len(unresolved_rows),
        "output_open_question_count": len(output_open_questions),
        "infobase_check_count": len(infobase_rows),
        "closed_infobase_check_count": sum(1 for row in infobase_rows if (row.get("status_after_pass") or "").strip() == "closed"),
        "infobase_question_count": len(infobase_questions),
        "open_infobase_question_count": sum(1 for row in infobase_questions if (row.get("status") or "").strip() == "open"),
        "risk_feature_count": len(risk_features),
        "detail_map_count": len(detail_maps),
        "subject_map_count": len(subject_maps),
        "technical_map_count": len(technical_maps),
        "complete_subject_map_count": sum(1 for subject_map in subject_maps if subject_map["key_conclusion"] and subject_map["upgrade_risk"]),
        "detail_map_by_type": dict(sorted(Counter(detail_map["type"] for detail_map in detail_maps).items())),
        "detail_map_by_generation_mode": dict(sorted(Counter(detail_map["generation_mode"] for detail_map in detail_maps).items())),
        "detail_map_open_question_count": sum(detail_map["open_questions_count"] for detail_map in detail_maps),
        "subject_card_by_status": dict(sorted(Counter(subject_map["status"] for subject_map in subject_maps).items())),
        "subject_registry_count": len(subject_registry),
        "subject_registry_ready_count": sum(1 for row in subject_registry if row.get("has_card")),
        "subject_registry_candidate_count": sum(1 for row in subject_registry if row.get("status") == "candidate"),
        "subject_registry_by_type": dict(sorted(Counter(row.get("subject_type") or "not_set" for row in subject_registry).items())),
        "subject_coverage_count": len(subject_coverage),
        "subject_bf_coverage_count": sum(1 for row in subject_coverage if row.get("source_kind") == "BF"),
        "subject_bf_covered_count": sum(1 for row in subject_coverage if row.get("source_kind") == "BF" and row.get("relation") == "covered_by_subject_card"),
        "subject_bf_unclassified_count": sum(1 for row in subject_coverage if row.get("source_kind") == "BF" and row.get("relation") == "unclassified"),
        "subject_bf_unique_count": len(subject_bf_feature_ids),
        "subject_bf_unique_covered_count": len(subject_bf_covered_ids),
        "subject_bf_unique_unclassified_count": len(subject_bf_unclassified_ids),
        "functional_gap_card_count": functional_gap_map.get("summary", {}).get("cards_total", 0),
        "functional_gap_ready_count": functional_gap_map.get("summary", {}).get("ready_for_review", 0),
        "functional_gap_reviewed_count": functional_gap_map.get("summary", {}).get("reviewed", 0),
        "functional_gap_open_blocking_count": functional_gap_map.get("summary", {}).get("open_blocking_checks", 0),
    }

    final_audit_path = repo_path(root, "analysis/final-audit.md")
    final_audit_text = (
        "Финальный аудит доступен отдельным артефактом. "
        f"В дашборд вынесена краткая сводка: BF-контейнеров {summary['feature_count']}, "
        f"карточек доработок {summary['subject_registry_ready_count']}, "
        f"открытых вопросов {summary['open_question_count']}, "
        f"закрытых проверок в ИБ {summary['closed_infobase_check_count']} из {summary['infobase_check_count']}, "
        f"открытых вопросов к ИБ {summary['open_infobase_question_count']}."
    )
    return {
        "schema_version": "review-dashboard/v1",
        "dashboard_scope": {"mode": "full"},
        "generated_at": utc_now_iso(),
        "project": project,
        "summary": summary,
        "features": features,
        "subject_cards": subject_cards,
        "subject_maps": subject_maps,
        "subject_registry": subject_registry,
        "subject_card_coverage": subject_coverage,
        "functional_gap_dashboard": functional_gap_dashboard_link(root, output_dir),
        "functional_gap_map": functional_gap_map,
        "detail_maps": detail_maps,
        "open_questions": unresolved_rows,
        "output_open_questions": output_open_questions,
        "infobase_questions": infobase_questions,
        "infobase_checks": infobase_rows,
        "risk_features": risk_features,
        "final_audit": {
            "path": "analysis/final-audit.md",
            "href": repo_href(root, output_dir, "analysis/final-audit.md"),
            "text": final_audit_text if final_audit_path.exists() else "",
        },
        "outputs": output_files(root, output_dir),
    }


def dashboard_html(data: dict[str, Any]) -> str:
    json_data = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Карта доработок 1С</title>
  <style>
    :root {{
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-soft: #f1f5f9;
      --text: #111827;
      --muted: #5f6b7a;
      --border: #d9dee7;
      --accent: #14532d;
      --accent-soft: #dcfce7;
      --warn: #92400e;
      --warn-soft: #fef3c7;
      --danger: #991b1b;
      --danger-soft: #fee2e2;
      --info: #1e3a8a;
      --info-soft: #dbeafe;
      --shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }}
    a {{ color: var(--info); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .layout {{ display: grid; grid-template-columns: 280px 1fr; min-height: 100vh; }}
    .sidebar {{
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
      border-right: 1px solid var(--border);
      background: #ffffff;
      padding: 20px 16px;
    }}
    .brand {{ font-size: 18px; font-weight: 700; margin-bottom: 4px; }}
    .subtle {{ color: var(--muted); }}
    .nav {{ display: flex; flex-direction: column; gap: 6px; margin-top: 22px; }}
    .nav a {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      border: 1px solid transparent;
      border-radius: 6px;
      padding: 8px 10px;
      color: var(--text);
    }}
    .nav a:hover {{ background: var(--panel-soft); text-decoration: none; }}
    .nav a.active {{ background: var(--accent-soft); border-color: #86efac; color: var(--accent); text-decoration: none; }}
    main {{ padding: 24px; max-width: 1680px; min-width: 0; width: 100%; overflow: hidden; }}
    main *, .feature, .panel, .metric, .box {{
      min-width: 0;
      overflow-wrap: anywhere;
    }}
    .hero {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 24px;
      align-items: flex-start;
      border-bottom: 1px solid var(--border);
      padding-bottom: 18px;
      margin-bottom: 18px;
    }}
    .view[hidden] {{ display: none; }}
    .screen-head {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 14px;
    }}
    .screen-head p {{ margin: 4px 0 0; color: var(--muted); max-width: 880px; }}
    .card-list {{
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      align-items: stretch;
    }}
    .subject-card-link {{
      display: flex;
      flex-direction: column;
      gap: 12px;
      color: var(--text);
      min-height: 260px;
    }}
    .subject-card-link:hover {{
      text-decoration: none;
      border-color: #93c5fd;
      box-shadow: 0 8px 18px rgba(15, 23, 42, 0.08);
    }}
    .card-summary {{
      flex: 1;
      color: var(--text);
    }}
    .card-actions {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      color: var(--muted);
    }}
    .back-row {{ margin-bottom: 14px; }}
    .back-link {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      background: #ffffff;
      color: var(--text);
    }}
    .back-link:hover {{ background: var(--panel-soft); text-decoration: none; }}
    h1 {{ margin: 0 0 6px; font-size: 26px; line-height: 1.2; }}
    h2 {{ margin: 28px 0 12px; font-size: 20px; }}
    h3 {{ margin: 0 0 8px; font-size: 16px; }}
    .grid {{ display: grid; gap: 12px; }}
    .metrics {{ grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }}
    .metric, .panel, .feature {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .metric {{ padding: 14px; }}
    .metric strong {{ display: block; font-size: 24px; line-height: 1.1; }}
    .metric span {{ color: var(--muted); font-size: 12px; }}
    .panel {{ padding: 16px; }}
    .toolbar {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px;
      margin: 18px 0 12px;
    }}
    input, select {{
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 9px 10px;
      background: #ffffff;
      color: var(--text);
      font: inherit;
    }}
    button {{
      width: auto;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      background: #ffffff;
      color: var(--text);
      font: inherit;
      cursor: pointer;
    }}
    button:hover {{ background: var(--panel-soft); }}
    .feature-list {{ display: grid; gap: 12px; }}
    .feature {{ padding: 16px; scroll-margin-top: 16px; }}
    .list-footer {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 8px 0 0;
      color: var(--muted);
    }}
    .customization-body {{
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(280px, 0.9fr);
      gap: 16px;
      margin-top: 12px;
    }}
    .customization-block h3 {{ margin-top: 0; }}
    .subject-overview {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 12px;
      margin-top: 12px;
    }}
    .subject-overview .box h3 {{ margin-top: 0; }}
    .subject-section {{ margin-top: 14px; }}
    .object-links {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .feature-head {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      align-items: start;
    }}
    .title-row {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }}
    .feature-title {{ font-weight: 700; font-size: 17px; }}
    .badges {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      border-radius: 999px;
      padding: 2px 8px;
      border: 1px solid var(--border);
      background: var(--panel-soft);
      color: var(--text);
      font-size: 12px;
      max-width: 100%;
      white-space: normal;
      line-height: 1.25;
    }}
    .title-row .badge {{ flex-shrink: 0; }}
    .badge.ok {{ background: var(--accent-soft); color: var(--accent); border-color: #86efac; }}
    .badge.warn {{ background: var(--warn-soft); color: var(--warn); border-color: #fcd34d; }}
    .badge.danger {{ background: var(--danger-soft); color: var(--danger); border-color: #fca5a5; }}
    .badge.info {{ background: var(--info-soft); color: var(--info); border-color: #93c5fd; }}
    .stats {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; color: var(--muted); }}
    .section-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }}
    .box {{ background: var(--panel-soft); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }}
    .box ul {{ margin: 8px 0 0 18px; padding: 0; }}
    .table-wrap {{ overflow: auto; border: 1px solid var(--border); border-radius: 8px; background: #ffffff; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 760px; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }}
    th {{ background: var(--panel-soft); font-size: 12px; color: var(--muted); font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    .md {{ white-space: pre-wrap; max-height: 420px; overflow: auto; }}
    .empty {{ color: var(--muted); font-style: italic; }}
    .hidden {{ display: none; }}
    @media (max-width: 1100px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .sidebar {{ position: relative; height: auto; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .toolbar {{ grid-template-columns: 1fr; }}
      .feature-head {{ grid-template-columns: 1fr; }}
      .section-grid {{ grid-template-columns: 1fr; }}
      .customization-body {{ grid-template-columns: 1fr; }}
      .subject-overview {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 1400px) and (min-width: 1101px) {{
      .toolbar {{ grid-template-columns: 1fr 1fr; }}
      .feature-head {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <script id="dashboard-data" type="application/json">{json_data}</script>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">Карта доработок 1С</div>
      <div class="subtle" id="project-caption"></div>
      <nav class="nav">
        <a href="#cards" data-nav-view="cards">Карточки доработок <span id="nav-subject-count"></span></a>
        <a href="#gaps" data-nav-view="gaps">Карта перехода <span id="nav-gap-count"></span></a>
        <a href="#questions" data-nav-view="questions">Открытые вопросы <span id="nav-open-count"></span></a>
        <a href="#runtime" data-nav-view="runtime">Проверки в ИБ <span id="nav-runtime-count"></span></a>
        <a href="#bf" data-nav-view="bf">BF-контейнеры <span id="nav-bf-count"></span></a>
        <a href="#technical" data-nav-view="technical">Технические карты <span id="nav-technical-count"></span></a>
        <a href="#audit" data-nav-view="audit">Финальный аудит</a>
      </nav>
    </aside>
    <main>
      <section class="hero">
        <div>
          <h1 id="dashboard-title">Карта доработок для ревью аналитиком</h1>
          <div class="subtle" id="generated-at"></div>
        </div>
        <div class="badges">
          <span class="badge info">Статичный HTML</span>
          <span class="badge info">Навигация по экранам</span>
          <span class="badge info">Источник: карточки доработок</span>
        </div>
      </section>

      <section class="view" data-view="cards" id="cards">
        <div class="screen-head">
          <div>
            <h2>Реестр предметных доработок</h2>
            <p>Первый экран показывает канонический реестр: готовые карточки доработок, кандидаты к разбору и покрытие BF. BF и сгенерированные технические карты остаются вторичной доказательной подложкой.</p>
          </div>
          <div class="badges" id="card-list-badges"></div>
        </div>
        <div class="grid metrics" id="metrics"></div>
        <div class="section-grid" id="subject-registry-summary"></div>
        <h2>Предметные доработки и кандидаты</h2>
        <div class="feature-list" id="subject-registry-list"></div>
        <h2>Покрытие BF</h2>
        <div class="panel" id="coverage-summary"></div>
        <div class="feature-list" id="subject-map-list"></div>
      </section>

      <section class="view" data-view="card" id="card-screen" hidden>
        <div class="back-row"><a class="back-link" href="#cards">Назад к списку</a></div>
        <div id="subject-card-screen"></div>
      </section>

      <section class="view" data-view="gaps" id="functional-gaps" hidden>
        <div class="screen-head">
          <div>
            <h2>Карта перехода на ДО 3.0</h2>
            <p>Этот экран показывает решения по функциональным разрывам, собранные только из gap-карточек. BF и технические карты остаются доказательной подложкой.</p>
          </div>
          <div>
            <div class="badges" id="functional-gap-badges"></div>
            <div class="badges" id="functional-gap-dashboard-link"></div>
          </div>
        </div>
        <div class="grid metrics" id="functional-gap-metrics"></div>
        <div class="feature-list" id="functional-gap-list"></div>
      </section>

      <section class="view" data-view="questions" id="questions" hidden>
        <div class="screen-head">
          <div>
            <h2>Открытые вопросы</h2>
            <p>Единый список вопросов, которые требуют решения, проверки в ИБ или аналитического ревью.</p>
          </div>
        </div>
        <div class="panel" id="open-questions"></div>
      </section>

      <section class="view" data-view="runtime" id="runtime-checks" hidden>
        <div class="screen-head">
          <div>
            <h2>Проверки в ИБ</h2>
            <p>Проверки, которыми закрывались вопросы по демо-ИБ, 1С-MCP, веб-интерфейсу 1С и прямым выгрузкам из ИБ. Это доказательства по тестовой базе, а не статистика рабочей базы.</p>
          </div>
          <div class="badges" id="runtime-check-badges"></div>
        </div>
        <div class="toolbar">
          <input id="runtime-search" type="search" placeholder="Поиск по BF, вопросу, методу, результату, заметкам">
          <select id="runtime-scenario-filter"><option value="">Все BF</option></select>
          <select id="runtime-method-filter"><option value="">Все методы</option></select>
          <select id="runtime-status-filter"><option value="">Все статусы</option></select>
        </div>
        <div class="panel" id="runtime-check-list"></div>
      </section>

      <section class="view" data-view="bf" id="bf-groups" hidden>
        <div class="screen-head">
          <div>
            <h2>BF-контейнеры</h2>
            <p>Группировка по BF и детальные блоки reverse-map остаются отдельным экраном, чтобы не смешивать их с карточками доработок.</p>
          </div>
        </div>
        <div class="feature-list" id="bf-group-list"></div>
        <h2>Детализация BF</h2>
        <div class="toolbar">
          <input id="search" type="search" placeholder="Поиск по BF, названию, домену, описанию">
          <select id="status-filter"><option value="">Все статусы</option></select>
          <select id="confidence-filter"><option value="">Любая достоверность</option></select>
          <select id="question-filter">
            <option value="">Все блоки</option>
            <option value="open">С открытыми вопросами</option>
            <option value="runtime">С проверками в ИБ</option>
          </select>
        </div>
        <div class="feature-list" id="feature-list"></div>
      </section>

      <section class="view" data-view="technical" id="technical-maps" hidden>
        <div class="screen-head">
          <div>
            <h2>Техническая подложка</h2>
            <p>Сгенерированные технические карты доступны отдельно, с поиском и пагинацией. Они не выводятся на первом экране.</p>
          </div>
        </div>
        <div class="toolbar">
          <input id="technical-search" type="search" placeholder="Поиск по технической карте, объекту, правилу, источнику">
          <select id="technical-type-filter"><option value="">Все типы</option></select>
          <select id="technical-status-filter"><option value="">Все статусы</option></select>
          <select id="technical-feature-filter"><option value="">Все BF</option></select>
          <select id="technical-question-filter">
            <option value="">Все технические карты</option>
            <option value="open">С открытыми вопросами</option>
          </select>
        </div>
        <div class="feature-list" id="technical-map-list"></div>
      </section>

      <section class="view" data-view="audit" id="audit" hidden>
        <div class="screen-head">
          <div>
            <h2>Финальный аудит</h2>
            <p>Свод по проверкам, рискам перехода на ДО 3.0 и поставочным артефактам.</p>
          </div>
        </div>
        <h2>Что важно для перехода на ДО 3.0</h2>
        <div class="panel" id="migration-risks"></div>
        <h2>Финальный аудит и поставочные артефакты</h2>
        <div class="section-grid">
          <div class="panel">
            <h3>Финальный аудит</h3>
            <div class="md" id="final-audit"></div>
          </div>
          <div class="panel">
            <h3>Файлы поставки</h3>
            <div id="outputs"></div>
          </div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const data = JSON.parse(document.getElementById("dashboard-data").textContent);
    const DETAIL_MAP_PAGE_SIZE = 60;
    let detailMapVisibleLimit = DETAIL_MAP_PAGE_SIZE;
    const dashboardScope = data.dashboard_scope || {{mode: "full"}};
    const isSubjectCardScope = dashboardScope.mode === "subject_card";
    const detailMapBySlug = new Map((data.detail_maps || []).map((map) => [map.slug, map]));
    const subjectMaps = data.subject_maps || [];
    const subjectCards = (data.subject_cards && data.subject_cards.length ? data.subject_cards : subjectMaps);
    const subjectRegistry = data.subject_registry || [];
    const subjectCoverage = data.subject_card_coverage || [];
    const functionalGapDashboard = data.functional_gap_dashboard || {{}};
    const functionalGapMap = data.functional_gap_map || {{summary: {{}}, cards: []}};
    const functionalGapCards = functionalGapMap.cards || [];
    const subjectCardBySlug = new Map(subjectCards.map((map) => [map.slug, map]));
    const subjectMapBySlug = new Map(subjectMaps.map((map) => [map.slug, map]));
    const technicalMaps = (data.detail_maps || []).filter((map) => map.generation_mode === "generated");
    const infobaseQuestions = data.infobase_questions || [];
    const runtimeChecks = data.infobase_checks || [];
    const byId = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[char]));
    const badgeClass = (status) => {{
      if (["complete","closed","confirmed_in_scenario","supporting_shared","ready_for_review","reviewed"].includes(status)) return "ok";
      if (["blocked_by_infobase_data","needs_infobase_data","requires_runtime_verification","needs_runtime_verification","needs_manual_review","requires_1c_review","needs_reclassification"].includes(status)) return "warn";
      if (["technical_noise","out_of_scope"].includes(status)) return "danger";
      return "info";
    }};
    const statusRu = {{
      complete: "Завершено",
      closed: "Закрыто",
      confirmed_in_scenario: "Подтверждено в сценарии",
      supporting_shared: "Общая поддержка",
      technical_noise: "Технический шум",
      technical_platform: "Техническая платформа",
      belongs_to_other_scenario: "Другой сценарий",
      needs_manual_review: "Требует ручного ревью",
      needs_infobase_data: "Нужны данные ИБ",
      needs_runtime_verification: "Нужна проверка в ИБ",
      requires_1c_review: "Требует ревью 1С",
      blocked_by_infobase_data: "Нужны данные ИБ",
      requires_runtime_verification: "Требует проверки в ИБ",
      needs_reclassification: "Требует переклассификации",
      candidate: "Кандидат",
      draft: "Черновик",
      needs_static_analysis: "Нужен статический анализ",
      needs_runtime_data: "Нужны данные ИБ",
      needs_ui_check: "Нужна проверка интерфейса",
      ready_for_review: "Готово к ревью",
      reviewed: "Отревьюировано",
      rejected: "Отклонено",
      merged_into_other: "Объединено с другой",
      accepted: "Принято",
      supporting: "Поддерживающий слой",
      unclassified: "Не классифицировано",
      covered_by_subject_card: "Покрыто карточкой",
      technical_support: "Технический слой",
      needs_target_analysis: "Нужна проверка ДО 3.0",
      needs_runtime_check: "Нужна runtime-проверка",
      needs_analyst_decision: "Нужно решение аналитика",
      blocked: "Заблокировано",
      replace_by_standard: "Заменить типовым механизмом",
      adapt: "Адаптировать",
      preserve: "Перенести",
      retire: "Не переносить",
      split: "Разделить решение",
      data_migration: "Проверить перенос данных",
      business_decision: "Нужно бизнес-решение",
    }};
    const subjectTypeRu = {{
      business_process: "Бизнес-процесс",
      business_document: "Бизнес-документ",
      reference_model: "НСИ / справочная модель",
      integration: "Интеграция",
      access_model: "Модель доступа",
      ui_surface: "Интерфейс 1С",
      background_automation: "Фоновая автоматизация",
      technical_support: "Техническая поддержка",
    }};
    const statusLabel = (value, fallback) => fallback || value || "Не указано";
    const confidenceBadge = (value) => value ? `Достоверность: ${{String(value).toLocaleLowerCase("ru")}}` : "Достоверность не указана";
    const rowsTable = (rows, columns) => {{
      if (!rows || rows.length === 0) return '<div class="empty">Нет строк.</div>';
      const head = columns.map(([key, label]) => `<th>${{esc(label)}}</th>`).join("");
      const body = rows.map((row) => `<tr>${{columns.map(([key]) => `<td>${{esc(row[key] || "")}}</td>`).join("")}}</tr>`).join("");
      return `<div class="table-wrap"><table><thead><tr>${{head}}</tr></thead><tbody>${{body}}</tbody></table></div>`;
    }};
    const countList = (items) => Object.entries(items || {{}}).map(([key, value]) => `<span class="badge">${{esc(statusRu[key] || key)}}: ${{value}}</span>`).join(" ");

    function renderHeader() {{
      const project = data.project || {{}};
      byId("project-caption").textContent = [project.id, project.product].filter(Boolean).join(" · ");
      byId("generated-at").textContent = `Сформировано: ${{data.generated_at || ""}}`;
      if (isSubjectCardScope) {{
        byId("dashboard-title").textContent = `Карточка доработки: ${{dashboardScope.title || dashboardScope.subject_card || ""}}`;
      }}
      byId("nav-subject-count").textContent = subjectRegistry.length || subjectCards.length || 0;
      byId("nav-gap-count").textContent = functionalGapCards.length || 0;
      byId("nav-bf-count").textContent = data.summary.feature_count || 0;
      byId("nav-technical-count").textContent = data.summary.technical_map_count || 0;
      byId("nav-open-count").textContent = data.summary.open_question_count || 0;
      byId("nav-runtime-count").textContent = (runtimeChecks.length || 0) + (infobaseQuestions.length || 0);
      byId("card-list-badges").innerHTML = [
        `<span class="badge">строк реестра: ${{subjectRegistry.length || subjectCards.length || 0}}</span>`,
        `<span class="badge ok">готовых карточек: ${{data.summary.subject_registry_ready_count || subjectCards.length || 0}}</span>`,
        `<span class="badge warn">кандидатов: ${{data.summary.subject_registry_candidate_count || 0}}</span>`,
        `<span class="badge">BF: ${{data.summary.feature_count || 0}}</span>`,
      ].join("");
      byId("runtime-check-badges").innerHTML = [
        `<span class="badge">вопросов: ${{infobaseQuestions.length || 0}}</span>`,
        `<span class="badge warn">открыто: ${{data.summary.open_infobase_question_count || 0}}</span>`,
        `<span class="badge">проверок: ${{runtimeChecks.length || 0}}</span>`,
        `<span class="badge ok">закрыто: ${{data.summary.closed_infobase_check_count || 0}}</span>`,
      ].join("");
      byId("functional-gap-badges").innerHTML = [
        `<span class="badge">релиз: ${{esc(functionalGapMap.target_release || "не указан")}}</span>`,
        `<span class="badge">карточек: ${{functionalGapCards.length || 0}}</span>`,
        `<span class="badge warn">блокирующих проверок: ${{functionalGapMap.summary.open_blocking_checks || 0}}</span>`,
      ].join("");
      byId("functional-gap-dashboard-link").innerHTML = functionalGapDashboard.href
        ? `<a class="badge info" href="${{esc(functionalGapDashboard.href)}}">Детальная карта функциональных разрывов</a>`
        : '<span class="badge">Детальная карта функциональных разрывов не собрана</span>';
    }}

    function applyDashboardScope() {{
      if (!isSubjectCardScope) return;
      document.querySelectorAll('[data-nav-view="bf"], [data-nav-view="technical"], [data-nav-view="questions"], [data-nav-view="runtime"], [data-nav-view="gaps"]').forEach((link) => link.classList.add("hidden"));
    }}

    function renderMetrics() {{
      const metrics = [
        ["Строк реестра", data.summary.subject_registry_count || subjectRegistry.length || 0],
        ["Готовые карточки", data.summary.subject_registry_ready_count || subjectCards.length || 0],
        ["Кандидаты", data.summary.subject_registry_candidate_count || 0],
        ["BF-контейнеры", data.summary.subject_bf_unique_count || data.summary.feature_count],
        ["BF покрыты карточками", data.summary.subject_bf_unique_covered_count || 0],
        ["BF требуют классификации", data.summary.subject_bf_unique_unclassified_count || 0],
        ["Gap-карточки", data.summary.functional_gap_card_count || functionalGapCards.length || 0],
      ];
      byId("metrics").innerHTML = metrics.map(([label, value]) => `<div class="metric"><strong>${{value}}</strong><span>${{esc(label)}}</span></div>`).join("");
    }}

    function fillFilters() {{
      const statuses = [...new Map(data.features.map((f) => [f.status, f.status_label])).entries()].filter(([key]) => key);
      const confidences = [...new Map(data.features.map((f) => [f.confidence, f.confidence_label])).entries()].filter(([key]) => key);
      byId("status-filter").innerHTML += statuses.map(([value, label]) => `<option value="${{esc(value)}}">${{esc(label)}}</option>`).join("");
      byId("confidence-filter").innerHTML += confidences.map(([value, label]) => `<option value="${{esc(value)}}">${{esc(label)}}</option>`).join("");
      const technicalTypes = [...new Map(technicalMaps.map((item) => [item.type, item.type_label])).entries()].filter(([key]) => key);
      const technicalStatuses = [...new Map(technicalMaps.map((item) => [item.status, item.status_label])).entries()].filter(([key]) => key);
      const technicalFeatures = [...new Set(technicalMaps.flatMap((item) => item.linked_features || []))].filter(Boolean).sort();
      byId("technical-type-filter").innerHTML += technicalTypes.map(([value, label]) => `<option value="${{esc(value)}}">${{esc(label)}}</option>`).join("");
      byId("technical-status-filter").innerHTML += technicalStatuses.map(([value, label]) => `<option value="${{esc(value)}}">${{esc(label)}}</option>`).join("");
      byId("technical-feature-filter").innerHTML += technicalFeatures.map((value) => `<option value="${{esc(value)}}">${{esc(value)}}</option>`).join("");
      const runtimeScenarios = [...new Set(runtimeChecks.map((row) => row.scenario_id))].filter(Boolean).sort();
      const runtimeMethods = [...new Map(runtimeChecks.map((row) => [row.check_method, row.check_method_label || row.check_method])).entries()].filter(([key]) => key).sort((left, right) => String(left[1]).localeCompare(String(right[1]), "ru"));
      const runtimeStatuses = [...new Set(runtimeChecks.map((row) => row.status_after_pass))].filter(Boolean).sort();
      byId("runtime-scenario-filter").innerHTML += runtimeScenarios.map((value) => `<option value="${{esc(value)}}">${{esc(value)}}</option>`).join("");
      byId("runtime-method-filter").innerHTML += runtimeMethods.map(([value, label]) => `<option value="${{esc(value)}}">${{esc(label)}}</option>`).join("");
      byId("runtime-status-filter").innerHTML += runtimeStatuses.map((value) => `<option value="${{esc(value)}}">${{esc(statusRu[value] || value)}}</option>`).join("");
    }}

    function detailMapWeight(map) {{
      const count = Object.values(map.counts || {{}}).reduce((total, value) => total + Number(value || 0), 0);
      const modeBonus = map.generation_mode === "enriched" ? 10000 : 0;
      const typePenalty = map.type === "other" ? -100 : 0;
      return modeBonus + typePenalty + count;
    }}

    const subjectMapSlugs = new Set(subjectMaps.map((map) => map.slug));
    function mapHref(map) {{
      return subjectMapSlugs.has(map.slug) ? `#card/${{encodeURIComponent(map.slug)}}` : `#detail-${{map.slug}}`;
    }}

    function detailMapsForFeature(feature) {{
      return (feature.detail_maps || [])
        .map((item) => detailMapBySlug.get(item.slug))
        .filter(Boolean)
        .sort((left, right) => detailMapWeight(right) - detailMapWeight(left) || String(left.title).localeCompare(String(right.title), "ru"));
    }}

    function customizationTypeBadges(maps) {{
      const counts = new Map();
      maps.forEach((map) => counts.set(map.type_label, (counts.get(map.type_label) || 0) + 1));
      const entries = [...counts.entries()].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], "ru"));
      return entries.map(([label, count]) => `<span class="badge">${{esc(label)}}: ${{count}}</span>`).join(" ") || '<span class="badge">объекты не выделены</span>';
    }}

    function keyObjectLinks(maps) {{
      const visible = maps.slice(0, 8);
      const links = visible.map((map) => `<a class="badge ${{badgeClass(map.status)}}" href="${{esc(mapHref(map))}}">${{esc(map.title)}}</a>`);
      if (maps.length > visible.length) links.push(`<span class="badge">еще ${{maps.length - visible.length}}</span>`);
      return links.join(" ") || '<span class="empty">Ключевые объекты не выделены.</span>';
    }}

    function renderBfGroup(feature) {{
      const maps = detailMapsForFeature(feature);
      const risks = (feature.migration_risks || []).slice(0, 2).map((risk) => `<li>${{esc(risk)}}</li>`).join("") || "<li>Отдельные риски перехода не выделены текущими артефактами.</li>";
      const subjectMaps = maps.filter((map) => subjectMapSlugs.has(map.slug));
      return `<article class="feature" id="bf-group-${{esc(feature.feature_id)}}">
        <div class="feature-head">
          <div>
            <div class="title-row">
              <span class="feature-title">${{esc(feature.feature_id)}} · ${{esc(feature.title)}}</span>
              <span class="badge ${{badgeClass(feature.status)}}">${{esc(feature.status_label)}}</span>
              <span class="badge">${{esc(confidenceBadge(feature.confidence_label))}}</span>
            </div>
            <div class="subtle">${{esc(feature.domain)}} · ${{esc(feature.classification_label)}}</div>
          </div>
          <div class="badges">
            <span class="badge">предметных карт: ${{subjectMaps.length}}</span>
            <span class="badge">техкарт: ${{maps.length - subjectMaps.length}}</span>
            <span class="badge">вопросы: ${{feature.open_questions_count || 0}}</span>
            <span class="badge">ИБ и интерфейс: ${{feature.infobase_checks_count || 0}}</span>
          </div>
        </div>
        <div class="customization-body">
          <div class="customization-block">
            <h3>BF-группа</h3>
            <p>${{esc(feature.summary)}}</p>
            <h3>Состав по техническим объектам</h3>
            <div class="badges">${{customizationTypeBadges(maps)}}</div>
          </div>
          <div class="customization-block">
            <h3>Связанные предметные и технические карты</h3>
            <div class="object-links">${{keyObjectLinks(maps)}}</div>
            <h3>Для ревью перехода</h3>
            <ul>${{risks}}</ul>
            <div class="stats"><a href="#bf/${{esc(feature.feature_id)}}">Детализация BF</a></div>
          </div>
        </div>
      </article>`;
    }}

    function renderBfGroups() {{
      byId("bf-group-list").innerHTML = data.features.length
        ? data.features.map(renderBfGroup).join("")
        : '<div class="panel empty">BF-группы не выделены текущими артефактами.</div>';
    }}

    function linkedDetailMapsBox(feature) {{
      if (!feature.detail_maps || !feature.detail_maps.length) {{
        return '<span class="empty">Для блока пока нет отдельных предметных карт.</span>';
      }}
      return feature.detail_maps.map((item) => `<a class="badge ${{badgeClass(item.status)}}" href="${{esc(item.href)}}">${{esc(item.type_label)}} · ${{esc(item.generation_mode_label)}} · ${{esc(item.title)}}</a>`).join(" ");
    }}

    function renderFeature(feature) {{
      const riskList = (feature.migration_risks || []).map((risk) => `<li>${{esc(risk)}}</li>`).join("");
      const questionTable = rowsTable(feature.open_questions, [
        ["item_id", "Вопрос"],
        ["status_label", "Статус"],
        ["reason", "Причина"],
        ["needed_input", "Что нужно"],
        ["impact", "Влияние"],
      ]);
      return `<article class="feature" id="${{esc(feature.feature_id)}}">
        <div class="feature-head">
          <div>
            <div class="title-row">
              <span class="feature-title">${{esc(feature.feature_id)}} · ${{esc(feature.title)}}</span>
              <span class="badge ${{badgeClass(feature.status)}}">${{esc(feature.status_label)}}</span>
              <span class="badge">${{esc(confidenceBadge(feature.confidence_label))}}</span>
            </div>
            <div class="subtle">${{esc(feature.domain)}} · ${{esc(feature.classification_label)}}</div>
          </div>
          <div class="badges">
            <span class="badge">доказательства: ${{feature.evidence_count}}</span>
            <span class="badge">покрытие: ${{feature.coverage_count}}</span>
            <span class="badge">вопросы: ${{feature.open_questions_count}}</span>
            <span class="badge">карты: ${{feature.detail_maps_count}}</span>
          </div>
        </div>
        <p>${{esc(feature.summary)}}</p>
        <div class="stats">
          <span>Статусы покрытия:</span> ${{countList(feature.coverage_statuses)}}
        </div>
        <div class="section-grid">
          <div class="box">
            <h3>Что важно для ДО 3.0</h3>
            <ul>${{riskList}}</ul>
          </div>
          <div class="box">
            <h3>Ссылки</h3>
            <div><a href="${{esc(feature.scenario_summary_href)}}">${{esc(feature.scenario_summary_path)}}</a></div>
            ${{feature.evidence_pack_href ? `<div><a href="${{esc(feature.evidence_pack_href)}}">${{esc(feature.evidence_pack_path)}}</a></div>` : ""}}
          </div>
        </div>
        <details>
          <summary>Связанные карты доработок</summary>
          <div class="box">${{linkedDetailMapsBox(feature)}}</div>
        </details>
        <details>
          <summary>Сводка блока</summary>
          <div class="box">
            <p>${{esc(feature.summary)}}</p>
            <p class="subtle">${{esc(feature.notes || "Дополнительные заметки не указаны.")}}</p>
            <div class="stats">
              <span>Источники:</span>
              ${{Object.entries(feature.source_count_labels || {{}}).map(([key, value]) => `<span class="badge">${{esc(key)}}: ${{value}}</span>`).join(" ") || '<span class="badge">не указаны</span>'}}
            </div>
          </div>
        </details>
        <details>
          <summary>Доказательства</summary>
          ${{rowsTable(feature.evidence_samples, [["claim_id","ID"],["source_kind_label","Источник"],["source_path","Путь"],["line_start","Начало"],["line_end","Конец"],["confidence_label","Достоверность"],["summary","Вывод"]])}}
        </details>
        <details>
          <summary>Решения reverse-map</summary>
          ${{rowsTable(feature.decision_samples, [["decision_id","ID"],["decision","Решение"],["confidence_label","Достоверность"],["rationale","Обоснование"],["evidence_ref","Доказательство"]])}}
        </details>
        <details>
          <summary>Открытые вопросы блока</summary>
          ${{questionTable}}
        </details>
      </article>`;
    }}

    const detailColumns = {{
      attributes: [["object","Объект"],["kind","Тип"],["name","Имя"],["synonym","Синоним"],["data_type","Тип данных"],["vendor_status","Статус к вендору"],["relation","Связь с доработкой"],["confidence_label","Достоверность"],["source","Источник"],["line","Строка"],["comment","Комментарий"]],
      form_rules: [["id","ID"],["rule","Правило"],["description","Описание"],["mechanism","Условие/механизм"],["confidence_label","Достоверность"],["source","Источник"],["line","Строка"]],
      validations: [["id","ID"],["field","Поле"],["validation","Проверка/обязательность"],["mechanism","Механизм"],["confidence_label","Достоверность"],["source","Источник"],["line","Строка"]],
      lifecycle: [["id","ID"],["action","Переход/действие"],["description","Описание"],["mechanism","Условие/механизм"],["confidence_label","Достоверность"],["source","Источник"],["line","Строка"]],
      rights: [["id","ID"],["role","Роль/ФИО-роль"],["description","Описание"],["mechanism","Механизм"],["confidence_label","Достоверность"],["source","Источник"],["line","Строка"]],
      scheduled_jobs: [["id","ID"],["name","Регламентное задание"],["description","Описание"],["mechanism","Механизм"],["status_label","Статус"],["confidence_label","Достоверность"],["source","Источник"],["line","Строка"]],
      ui: [["id","ID"],["surface","Интерфейс 1С"],["command","Команда"],["description","Описание"],["confidence_label","Достоверность"],["source","Источник"],["line","Строка"]],
      integrations: [["id","ID"],["system","Система"],["flow","Поток"],["description","Описание"],["confidence_label","Достоверность"],["source","Источник"],["line","Строка"]],
      sources: [["id","ID"],["claim","Что подтверждает"],["source","Источник"],["line","Строка"],["evidence","Доказательство"]],
      open_questions: [["id","ID"],["question","Вопрос"],["why_open","Почему открыт"],["needed","Что нужно"]],
    }};
    const claimSectionColumns = [["claim","Что подтверждает"],["source","Источник"],["line","Строка"],["confidence_label","Достоверность"]];
    const detailSectionLabels = {{
      attributes: "Реквизиты",
      form_rules: "Правила формы",
      validations: "Проверки заполнения",
      lifecycle: "Жизненный цикл",
      rights: "Права и роли",
      scheduled_jobs: "Регламентные задания",
      ui: "Интерфейс 1С",
      integrations: "Интеграции",
      sources: "Источники",
      open_questions: "Открытые вопросы",
    }};
    const subjectClaimSectionLabels = {{
      attributes: "Выводы по данным и реквизитам",
      form_rules: "Выводы по форме",
      validations: "Выводы по проверкам заполнения",
      lifecycle: "Выводы по жизненному циклу",
      rights: "Выводы по правам и ролям",
      scheduled_jobs: "Выводы по регламентным заданиям",
      ui: "Выводы по интерфейсу 1С",
      integrations: "Выводы по интеграциям",
      sources: "Подтверждающие источники",
      open_questions: "Открытые вопросы",
    }};

    function linkedFeatureBadges(map) {{
      return (map.linked_features || []).map((featureId) => isSubjectCardScope
        ? `<span class="badge">${{esc(featureId)}}</span>`
        : `<a class="badge" href="#bf/${{esc(featureId)}}">${{esc(featureId)}}</a>`
      ).join(" ") || '<span class="badge">BF не указан</span>';
    }}

    function sourceLinks(map) {{
      const jsonLink = map.source_href ? `<div><a href="${{esc(map.source_href)}}">${{esc(map.source_path)}}</a></div>` : "";
      const workbookLink = map.source_workbook_href ? `<div><a href="${{esc(map.source_workbook_href)}}">${{esc(map.source_workbook)}}</a></div>` : "";
      return jsonLink + workbookLink || '<span class="empty">Источник не указан.</span>';
    }}

    function subjectSection(map, sectionKey) {{
      const rows = (map.sections || {{}})[sectionKey] || [];
      const count = (map.counts || {{}})[sectionKey] || rows.length;
      const usesClaimRows = map.generation_mode === "subject_card" && rows.some((row) => row && row.claim);
      const columns = usesClaimRows
        ? claimSectionColumns
        : detailColumns[sectionKey];
      const label = usesClaimRows ? subjectClaimSectionLabels[sectionKey] : detailSectionLabels[sectionKey];
      return `<section class="subject-section">
        <h3>${{esc(label)}} · ${{count}}</h3>
        ${{rowsTable(rows, columns)}}
      </section>`;
    }}

    const linkedDetailAttributeColumns = [
      ["detail_map_title","Техническая карта"],
      ["object","Объект"],
      ["name","Реквизит"],
      ["data_type","Тип"],
      ["relation","Зачем нужен"],
      ["source","Источник"],
      ["line","Строка"],
    ];

    function isAutoSummaryAttribute(row) {{
      const comment = String((row || {{}}).comment || "").toLowerCase();
      return comment.includes("автосводка") || comment.includes("требуют ручной детализации");
    }}

    function linkedDetailAttributeRows(map) {{
      return (map.linked_detail_maps || []).flatMap((slug) => {{
        const detailMap = detailMapBySlug.get(slug);
        if (!detailMap) return [];
        const rows = ((detailMap.sections || {{}}).attributes || []);
        return rows.map((row) => ({{
          ...row,
          detail_map_slug: detailMap.slug || slug,
          detail_map_title: detailMap.title || slug,
          __auto_summary: isAutoSummaryAttribute(row),
        }}));
      }});
    }}

    function linkedDetailMapLinks(map) {{
      const linkedMaps = (map.linked_detail_maps || [])
        .map((slug) => detailMapBySlug.get(slug))
        .filter(Boolean);
      return linkedMaps.map((detailMap) => `<a class="badge ${{badgeClass(detailMap.status)}}" href="#detail-${{esc(detailMap.slug)}}">${{esc(detailMap.title || detailMap.slug)}}</a>`).join(" ");
    }}

    function linkedDetailAttributesSection(map) {{
      const linkedSlugs = map.linked_detail_maps || [];
      if (!linkedSlugs.length) return "";
      const rows = linkedDetailAttributeRows(map);
      const autoSummaryCount = rows.filter((row) => row.__auto_summary).length;
      const detailedCount = rows.length - autoSummaryCount;
      const visibleRows = rows.slice(0, 12);
      const detailLinks = linkedDetailMapLinks(map) || '<span class="badge">связанные карты не найдены в данных</span>';
      const table = rows.length
        ? rowsTable(visibleRows, linkedDetailAttributeColumns)
        : '<div class="empty">В связанных технических картах реквизиты не заполнены.</div>';
      const limitNote = rows.length > visibleRows.length
        ? `<p class="subtle">Показаны первые ${{visibleRows.length}} строк из ${{rows.length}}. Полный список смотрите в связанных технических картах.</p>`
        : "";
      const detailNote = rows.length && detailedCount === 0
        ? '<p class="subtle">Детализированный список реквизитов не заполнен; есть только автосводка diff. Требуется ручная детализация технических карт.</p>'
        : "";
      return `<section class="subject-section">
        <h3>Реквизиты из технических карт · ${{rows.length}}</h3>
        <div class="badges">
          <span class="badge">технических карт: ${{linkedSlugs.length}}</span>
          <span class="badge ok">детализированных строк: ${{detailedCount}}</span>
          <span class="badge warn">автосводок: ${{autoSummaryCount}}</span>
        </div>
        <div class="badges">${{detailLinks}}</div>
        ${{detailNote}}
        ${{table}}
        ${{limitNote}}
      </section>`;
    }}

    function subjectCardDetail(slug) {{
      return subjectMapBySlug.get(slug) || subjectCardBySlug.get(slug) || null;
    }}

    function refBadges(values, prefix) {{
      const refs = values || [];
      if (!refs.length) return '<span class="badge">не указано</span>';
      return refs.slice(0, 10).map((value) => prefix === "bf" && !isSubjectCardScope
        ? `<a class="badge" href="#bf/${{esc(value)}}">${{esc(value)}}</a>`
        : `<span class="badge">${{esc(value)}}</span>`
      ).join(" ") + (refs.length > 10 ? ` <span class="badge">еще ${{refs.length - 10}}</span>` : "");
    }}

    function registryAction(row) {{
      if (row.has_card || row.card_path) {{
        return `<a class="badge ok" href="#card/${{encodeURIComponent(row.slug)}}">Открыть карточку</a>`;
      }}
      if (row.status === "candidate") {{
        return '<span class="badge warn">Требует решения о разделении или объединении</span>';
      }}
      if (row.status === "supporting") return '<span class="badge">Поддерживающий слой</span>';
      if (row.status === "merged_into_other") return '<span class="badge">Объединено</span>';
      if (row.status === "rejected") return '<span class="badge danger">Отклонено</span>';
      return '<span class="badge info">Создать карточку из реестра</span>';
    }}

    function renderSubjectRegistryRow(row) {{
      const features = row.linked_features_list || [];
      const detailMaps = row.linked_detail_maps_list || [];
      const title = row.has_card || row.card_path
        ? `<a href="#card/${{encodeURIComponent(row.slug)}}">${{esc(row.title || row.slug)}}</a>`
        : esc(row.title || row.slug);
      return `<article class="feature subject-registry-row">
        <div class="feature-head">
          <div>
            <div class="title-row">
              <span class="feature-title">${{title}}</span>
              <span class="badge info">${{esc(row.subject_type_label || subjectTypeRu[row.subject_type] || row.subject_type || "тип не указан")}}</span>
              <span class="badge ${{badgeClass(row.status)}}">${{esc(row.status_label || statusRu[row.status] || row.status || "статус не указан")}}</span>
              <span class="badge">${{esc(confidenceBadge(row.confidence_label || row.confidence))}}</span>
            </div>
            <div class="subtle">${{esc(row.slug)}} · слой: ${{esc(row.origin_layer || "не указан")}} · покрытие: ${{esc(row.relation_label || row.coverage_scope_label || row.coverage_scope || "не указано")}}</div>
          </div>
          <div class="badges">
            <span class="badge">доказательства: ${{row.evidence_count || 0}}</span>
            <span class="badge">пробелы: ${{row.gap_count || 0}}</span>
            ${{registryAction(row)}}
          </div>
        </div>
        <div class="section-grid">
          <div class="box">
            <h3>Почему это отдельная строка реестра</h3>
            <p>${{esc(row.why_separate_card || "Требуется классификационное решение.")}}</p>
          </div>
          <div class="box">
            <h3>Связанные BF и технические карты</h3>
            <div class="badges">${{refBadges(features, "bf")}}</div>
            <div class="badges">${{refBadges(detailMaps, "detail")}}</div>
          </div>
        </div>
        ${{row.review_notes ? `<p class="subtle">${{esc(row.review_notes)}}</p>` : ""}}
      </article>`;
    }}

    function renderSubjectRegistrySummary() {{
      const ready = subjectRegistry.filter((row) => row.has_card || row.card_path);
      const candidates = subjectRegistry.filter((row) => row.status === "candidate");
      const other = subjectRegistry.filter((row) => ["supporting","rejected","merged_into_other"].includes(row.status));
      byId("subject-registry-summary").innerHTML = [
        `<div class="panel"><h3>Готовые карточки</h3><p><strong>${{ready.length}}</strong></p><p class="subtle">Открываются как отдельные карточки доработок для ревью.</p></div>`,
        `<div class="panel"><h3>Кандидаты к разбору</h3><p><strong>${{candidates.length}}</strong></p><p class="subtle">Это BF-гипотезы; они не считаются готовыми доработками.</p></div>`,
        `<div class="panel"><h3>Объединено / отклонено / поддержка</h3><p><strong>${{other.length}}</strong></p><p class="subtle">Отдельные решения классификации без самостоятельной карточки.</p></div>`,
        `<div class="panel"><h3>Покрытие BF</h3><p><strong>${{data.summary.subject_bf_unique_count || data.summary.feature_count || 0}}</strong></p><p class="subtle">Каждый BF покрыт карточкой либо явно помечен как кандидат, не классифицирован, поддерживающий или технический слой.</p></div>`,
      ].join("");
    }}

    function renderCoverageSummary() {{
      const bfRows = subjectCoverage.filter((row) => row.source_kind === "BF");
      const grouped = {{}};
      bfRows.forEach((row) => grouped[row.relation || "unclassified"] = (grouped[row.relation || "unclassified"] || 0) + 1);
      const badges = Object.entries(grouped).map(([relation, count]) => `<span class="badge ${{badgeClass(relation)}}">${{esc(statusRu[relation] || relation)}}: ${{count}}</span>`).join(" ");
      const rows = bfRows.map((row) => ({{
        feature_id: row.feature_id,
        subject_card_slug: row.subject_card_slug,
        relation: statusRu[row.relation] || row.relation,
        confidence: row.confidence_label || row.confidence,
        notes: row.notes,
      }}));
      byId("coverage-summary").innerHTML = `<div class="badges">${{badges || '<span class="badge">покрытие не построено</span>'}}</div>${{rowsTable(rows, [["feature_id","BF"],["subject_card_slug","Карточка в реестре"],["relation","Связь"],["confidence","Достоверность"],["notes","Заметки"]])}}`;
    }}

    function renderSubjectRegistry() {{
      renderSubjectRegistrySummary();
      renderCoverageSummary();
      const rows = subjectRegistry.length ? subjectRegistry : subjectCards.map((card) => ({{
        ...card,
        has_card: true,
        card_path: card.source_path,
        linked_features_list: card.linked_features || [],
        linked_detail_maps_list: card.linked_detail_maps || [],
        evidence_count: card.evidence_count,
        gap_count: card.gap_count,
        why_separate_card: card.why_separate_card,
      }}));
      byId("subject-registry-list").innerHTML = rows.length
        ? rows.map(renderSubjectRegistryRow).join("")
        : '<div class="panel empty">Реестр предметных доработок пока не построен. Выполните команды поиска, классификации и сборки реестра карточек.</div>';
      byId("subject-map-list").innerHTML = "";
    }}

    function renderSubjectCardListItem(map) {{
      const featureLabel = (map.linked_features || []).join(", ") || map.owner_feature || "BF не указан";
      const gapCount = map.gap_count || (map.gaps || []).length || 0;
      const summary = map.summary || map.key_conclusion || "Краткое резюме пока не заполнено.";
      return `<a class="feature subject-card-link" href="#card/${{encodeURIComponent(map.slug)}}">
        <div>
          <div class="title-row">
            <span class="feature-title">${{esc(map.title)}}</span>
            <span class="badge ${{badgeClass(map.status)}}">${{esc(map.status_label)}}</span>
            <span class="badge">${{esc(confidenceBadge(map.confidence_label))}}</span>
          </div>
          <div class="subtle">${{esc(map.slug)}} · ${{esc(featureLabel)}}</div>
        </div>
        <p class="card-summary">${{esc(summary)}}</p>
        <div class="badges">
          <span class="badge">доказательства: ${{map.evidence_count || 0}}</span>
          <span class="badge">пробелы: ${{gapCount}}</span>
          <span class="badge">вопросы: ${{map.open_questions_count || 0}}</span>
          <span class="badge">источник: ${{esc(map.source_mode_label || map.generation_mode_label || "карточка доработки")}}</span>
        </div>
        <div class="card-actions">
          <span>Открыть карточку</span>
          <span class="badge info">отдельный экран</span>
        </div>
      </a>`;
    }}

    function renderSubjectCardList() {{
      renderSubjectRegistry();
    }}

    function renderSubjectMap(map) {{
      const notes = (map.migration_notes || []).map((note) => `<li>${{esc(note)}}</li>`).join("") || "<li>Отдельные замечания по переходу пока не указаны.</li>";
      return `<article class="feature subject-card" id="subject-${{esc(map.slug)}}">
        <div class="feature-head">
          <div>
            <div class="title-row">
              <span class="feature-title">${{esc(map.title)}}</span>
              <span class="badge info">${{esc(map.type_label)}}</span>
              <span class="badge info">${{esc(map.subject_type_label || subjectTypeRu[map.subject_type] || map.subject_type || "тип не указан")}}</span>
              <span class="badge info">${{esc(map.generation_mode_label)}}</span>
              <span class="badge">${{esc(map.completeness_label)}}</span>
              <span class="badge ${{badgeClass(map.status)}}">${{esc(map.status_label)}}</span>
              <span class="badge">${{esc(confidenceBadge(map.confidence_label))}}</span>
            </div>
            <div class="subtle">Предметная карта доработки · Владелец: ${{esc(map.owner_feature || "не указан")}}</div>
          </div>
          <div class="badges">
            <span class="badge">реквизиты: ${{(map.counts || {{}}).attributes || 0}}</span>
            <span class="badge">проверки: ${{(map.counts || {{}}).validations || 0}}</span>
            <span class="badge">вопросы: ${{map.open_questions_count || 0}}</span>
            <span class="badge">доказательства: ${{map.evidence_count || 0}}</span>
            <span class="badge">пробелы: ${{map.gap_count || 0}}</span>
          </div>
        </div>
        <div class="subject-overview">
          <div class="box">
            <h3>Сводка</h3>
            <p>${{esc(map.summary || "Сводка не заполнена.")}}</p>
          </div>
          <div class="box">
            <h3>Идентификация</h3>
            <p>${{esc(map.identification || "Идентификация не заполнена.")}}</p>
          </div>
          <div class="box">
            <h3>Ключевой вывод</h3>
            <p>${{esc(map.key_conclusion || "Ключевой вывод не заполнен.")}}</p>
          </div>
          <div class="box">
            <h3>Риск перехода на ДО 3.0</h3>
            <p>${{esc(map.upgrade_risk || "Риск перехода на ДО 3.0 не заполнен.")}}</p>
          </div>
          <div class="box">
            <h3>Статус проверки</h3>
            <p>${{esc(map.review_status || map.status_label || "Статус не указан.")}}</p>
            <p class="subtle">${{esc(map.runtime_data_needed || "Дополнительные данные ИБ не указаны.")}}</p>
          </div>
          <div class="box">
            <h3>Почему отдельная карточка</h3>
            <p>${{esc(map.why_separate_card || "Обоснование отдельной предметной карточки не заполнено.")}}</p>
            <p class="subtle">Слой происхождения: ${{esc(map.origin_layer || "не указан")}} · покрытие: ${{esc(map.coverage_scope_label || map.coverage_scope || "не указано")}}</p>
          </div>
          <div class="box">
            <h3>Связи и источники</h3>
            <div class="badges">${{linkedFeatureBadges(map)}}</div>
            <p class="subtle">Режим источника: ${{esc(map.source_mode_label || "не указан")}}</p>
            ${{sourceLinks(map)}}
          </div>
          <div class="box">
            <h3>Что важно для ДО 3.0</h3>
            <ul>${{notes}}</ul>
          </div>
        </div>
        ${{linkedDetailAttributesSection(map)}}
        ${{Object.keys(detailSectionLabels).map((sectionKey) => subjectSection(map, sectionKey)).join("")}}
        ${{(map.gaps || []).length ? `<section class="subject-section"><h3>Пробелы карточки · ${{map.gaps.length}}</h3>${{rowsTable(map.gaps, [["gap_id","ID"],["section","Раздел"],["question","Вопрос"],["needed_source","Источник"],["status_label","Статус"],["blocking","Блокирует"],["notes","Заметки"]])}}</section>` : ""}}
      </article>`;
    }}

    function renderSubjectCardScreen(slug) {{
      const map = subjectCardDetail(slug);
      if (!map) {{
        byId("subject-card-screen").innerHTML = '<div class="panel empty">Карточка не найдена в данных дашборда.</div>';
        return;
      }}
      byId("subject-card-screen").innerHTML = renderSubjectMap(map);
      byId("dashboard-title").textContent = `Карточка доработки: ${{map.title || map.slug}}`;
    }}

    function renderSubjectMaps() {{
      renderSubjectCardList();
    }}

    function renderFunctionalGapCard(card) {{
      const findings = (card.target_findings || []).slice(0, 5);
      const mappings = (card.object_mappings || []).slice(0, 5);
      return `<article class="feature">
        <div class="feature-head">
          <div>
            <div class="title-row">
              <span class="feature-title">${{esc(card.title || card.subject_card_slug)}}</span>
              <span class="badge ${{badgeClass(card.status)}}">${{esc(card.status_label || statusRu[card.status] || card.status || "статус не указан")}}</span>
              <span class="badge info">${{esc(card.selected_decision_label || statusRu[card.selected_decision] || "Решение не выбрано")}}</span>
            </div>
            <div class="subtle">${{esc(card.subject_card_slug)}} · готовность: ${{esc(card.gap_readiness_label || card.gap_readiness || "не указана")}}</div>
          </div>
          <div class="badges">
            <span class="badge">гипотезы: ${{card.hypotheses_count || 0}}</span>
            <span class="badge">проверки: ${{card.checks_count || 0}}</span>
            <span class="badge warn">блокирующие: ${{card.open_blocking_checks_count || 0}}</span>
            <span class="badge">находки ДО 3.0: ${{card.target_findings_count || 0}}</span>
          </div>
        </div>
        <div class="section-grid">
          <div class="box">
            <h3>Решение по переходу</h3>
            <p>${{esc(card.selected_decision_summary || card.selected_decision_label || "Решение пока не выбрано.")}}</p>
            <div class="badges">
              ${{card.review_href ? `<a class="badge" href="${{esc(card.review_href)}}">Открыть review.md</a>` : ""}}
              ${{card.gap_card_href ? `<a class="badge" href="${{esc(card.gap_card_href)}}">Открыть gap-card.json</a>` : ""}}
            </div>
          </div>
          <div class="box">
            <h3>Проверки и сопоставления</h3>
            <p>Открытых проверок: ${{card.open_checks_count || 0}}. Сопоставлений объектов: ${{card.object_mappings_count || 0}}.</p>
          </div>
        </div>
        <section class="subject-section">
          <h3>Находки в ДО 3.0</h3>
          ${{rowsTable(findings, [["finding_id","ID"],["finding_type","Тип"],["target_object","Объект"],["target_path","Путь"],["confidence","Достоверность"],["notes","Заметки"]])}}
        </section>
        <section class="subject-section">
          <h3>Сопоставление объектов</h3>
          ${{rowsTable(mappings, [["mapping_id","ID"],["source_object","Объект доработки"],["target_object","Объект ДО 3.0"],["mapping_type","Тип связи"],["confidence","Достоверность"],["notes","Заметки"]])}}
        </section>
      </article>`;
    }}

    function renderFunctionalGaps() {{
      const summary = functionalGapMap.summary || {{}};
      const metrics = [
        ["Gap-карточки", summary.cards_total || functionalGapCards.length || 0],
        ["Готово к ревью", summary.ready_for_review || 0],
        ["Отревьюировано", summary.reviewed || 0],
        ["Блокирующие проверки", summary.open_blocking_checks || 0],
      ];
      byId("functional-gap-metrics").innerHTML = metrics.map(([label, value]) => `<div class="metric"><strong>${{value}}</strong><span>${{esc(label)}}</span></div>`).join("");
      byId("functional-gap-list").innerHTML = functionalGapCards.length
        ? functionalGapCards.map(renderFunctionalGapCard).join("")
        : '<div class="panel empty">Карта функциональных разрывов пока не собрана. Выполните `python -m one_c_autoresearch functional-gap map-build`.</div>';
    }}

    function pushDetailSearchRows(parts, rows) {{
      (rows || []).slice(0, 20).forEach((row) => {{
        Object.values(row || {{}}).forEach((value) => parts.push(value));
      }});
    }}

    function buildDetailSearchText(map) {{
      if (map.__searchText) return map.__searchText;
      const searchableMap = detailMapBySlug.get(map.slug) || map;
      const parts = [
        searchableMap.slug,
        searchableMap.title,
        searchableMap.type,
        searchableMap.type_label,
        searchableMap.generation_mode,
        searchableMap.generation_mode_label,
        searchableMap.status,
        searchableMap.status_label,
        searchableMap.completeness_label,
        searchableMap.confidence_label,
        searchableMap.owner_feature,
        (searchableMap.linked_features || []).join(" "),
        searchableMap.summary,
        (searchableMap.migration_notes || []).join(" "),
        searchableMap.source_path,
        searchableMap.source_workbook,
      ];
      Object.values(searchableMap.sections || {{}}).forEach((rows) => pushDetailSearchRows(parts, rows));
      map.__searchText = parts.filter((value) => value !== undefined && value !== null).join(" ").toLowerCase();
      return map.__searchText;
    }}

    function detailSection(map, sectionKey) {{
      const rows = (map.sections || {{}})[sectionKey] || [];
      const count = (map.counts || {{}})[sectionKey] || rows.length;
      return `<details>
        <summary>${{esc(detailSectionLabels[sectionKey])}} · ${{count}}</summary>
        ${{rowsTable(rows, detailColumns[sectionKey])}}
      </details>`;
    }}

    function renderDetailMapDetails(map) {{
      const notes = (map.migration_notes || []).map((note) => `<li>${{esc(note)}}</li>`).join("") || "<li>Отдельные замечания по переходу пока не указаны.</li>";
      const features = (map.linked_features || []).map((featureId) => `<a class="badge" href="#bf/${{esc(featureId)}}">${{esc(featureId)}}</a>`).join(" ") || '<span class="badge">BF не указан</span>';
      const sourceLinks = `<div><a href="${{esc(map.source_href)}}">${{esc(map.source_path)}}</a></div>` + (map.source_workbook_href ? `<div><a href="${{esc(map.source_workbook_href)}}">${{esc(map.source_workbook)}}</a></div>` : "");
      return `<div class="section-grid">
        <div class="box">
          <h3>Что важно для ДО 3.0</h3>
          <ul>${{notes}}</ul>
        </div>
        <div class="box">
          <h3>Связи и источники</h3>
          <div class="badges">${{features}}</div>
          ${{sourceLinks}}
        </div>
      </div>
      ${{Object.keys(detailSectionLabels).map((sectionKey) => detailSection(map, sectionKey)).join("")}}`;
    }}

    function renderDetailMap(map) {{
      return `<article class="feature" id="detail-${{esc(map.slug)}}">
        <div class="feature-head">
          <div>
            <div class="title-row">
              <span class="feature-title">${{esc(map.title)}}</span>
              <span class="badge info">${{esc(map.type_label)}}</span>
              <span class="badge info">${{esc(map.generation_mode_label)}}</span>
              <span class="badge">${{esc(map.completeness_label)}}</span>
              <span class="badge ${{badgeClass(map.status)}}">${{esc(map.status_label)}}</span>
              <span class="badge">${{esc(confidenceBadge(map.confidence_label))}}</span>
            </div>
            <div class="subtle">Владелец: ${{esc(map.owner_feature || "не указан")}} · Связанные BF: ${{(map.linked_features || []).join(", ") || "не указаны"}}</div>
          </div>
          <div class="badges">
            <span class="badge">реквизиты: ${{(map.counts || {{}}).attributes || 0}}</span>
            <span class="badge">проверки: ${{(map.counts || {{}}).validations || 0}}</span>
            <span class="badge">вопросы: ${{map.open_questions_count || 0}}</span>
          </div>
        </div>
        <p>${{esc(map.summary)}}</p>
        <details class="detail-map-details" data-slug="${{esc(map.slug)}}">
          <summary>Детализация карты</summary>
          <div class="detail-map-details-body empty">Откройте карту, чтобы построить таблицы детализации.</div>
        </details>
      </article>`;
    }}

    function renderOpenedDetailMap(details) {{
      if (details.dataset.rendered === "1") return;
      const body = details.querySelector(".detail-map-details-body");
      if (!body) return;
      const map = detailMapBySlug.get(details.dataset.slug || "");
      if (!map) {{
        body.innerHTML = '<div class="empty">Карта не найдена в данных дашборда.</div>';
        details.dataset.rendered = "1";
        return;
      }}
      body.classList.remove("empty");
      body.innerHTML = renderDetailMapDetails(map);
      details.dataset.rendered = "1";
    }}

    function filteredDetailMaps() {{
      const query = byId("technical-search").value.trim().toLowerCase();
      const type = byId("technical-type-filter").value;
      const status = byId("technical-status-filter").value;
      const feature = byId("technical-feature-filter").value;
      const questionMode = byId("technical-question-filter").value;
      return technicalMaps.filter((map) => {{
        if (query && !buildDetailSearchText(map).includes(query)) return false;
        if (type && map.type !== type) return false;
        if (status && map.status !== status) return false;
        if (feature && !(map.linked_features || []).includes(feature)) return false;
        if (questionMode === "open" && !map.open_questions_count) return false;
        return true;
      }});
    }}

    function renderDetailMaps() {{
      const maps = filteredDetailMaps();
      const visibleMaps = maps.slice(0, detailMapVisibleLimit);
      if (!maps.length) {{
        byId("technical-map-list").innerHTML = '<div class="panel empty">Технические карты по текущим фильтрам не найдены.</div>';
        return;
      }}
      const footer = maps.length > visibleMaps.length
        ? `<div class="list-footer"><span>Показано ${{visibleMaps.length}} из ${{maps.length}}</span><button type="button" data-detail-more="1">Показать еще</button></div>`
        : `<div class="list-footer"><span>Показано ${{visibleMaps.length}} из ${{maps.length}}</span></div>`;
      byId("technical-map-list").innerHTML = visibleMaps.map(renderDetailMap).join("") + footer;
    }}

    function resetDetailMapFilters() {{
      detailMapVisibleLimit = DETAIL_MAP_PAGE_SIZE;
      renderDetailMaps();
    }}

    function ensureDetailMapVisible(slug) {{
      const maps = filteredDetailMaps();
      const index = maps.findIndex((map) => map.slug === slug);
      if (index >= detailMapVisibleLimit) {{
        detailMapVisibleLimit = Math.ceil((index + 1) / DETAIL_MAP_PAGE_SIZE) * DETAIL_MAP_PAGE_SIZE;
        renderDetailMaps();
      }}
      const target = byId(`detail-${{slug}}`);
      if (target) target.scrollIntoView({{block: "start"}});
    }}

    function revealDetailMapFromHash() {{
      const hash = window.location.hash || "";
      if (!hash.startsWith("#detail-")) return;
      ensureDetailMapVisible(decodeURIComponent(hash.slice("#detail-".length)));
    }}

    function routeFromHash() {{
      const raw = decodeURIComponent((window.location.hash || "").replace(/^#/, ""));
      if (!raw && isSubjectCardScope && subjectCards.length === 1) {{
        return {{view: "card", slug: subjectCards[0].slug}};
      }}
      if (!raw || raw === "subject-maps" || raw === "summary") return {{view: "cards"}};
      if (raw.startsWith("card/")) return {{view: "card", slug: raw.slice("card/".length)}};
      if (raw === "gaps" || raw === "functional-gaps" || raw === "gap-map") return {{view: "gaps"}};
      if (raw === "questions") return {{view: "questions"}};
      if (raw === "runtime" || raw === "infobase-checks") return {{view: "runtime"}};
      if (raw === "bf" || raw === "bf-groups" || raw === "features") return {{view: "bf"}};
      if (raw.startsWith("bf/")) return {{view: "bf", featureId: raw.slice("bf/".length)}};
      if (raw === "technical" || raw === "technical-maps") return {{view: "technical"}};
      if (raw.startsWith("detail-")) return {{view: "technical", detailSlug: raw.slice("detail-".length)}};
      if (raw === "audit" || raw === "migration") return {{view: "audit"}};
      return {{view: "cards"}};
    }}

    function setActiveView(view) {{
      document.querySelectorAll("[data-view]").forEach((section) => {{
        section.hidden = section.dataset.view !== view;
      }});
      document.querySelectorAll(".nav a").forEach((link) => {{
        const activeView = view === "card" ? "cards" : view;
        link.classList.toggle("active", link.dataset.navView === activeView);
      }});
      if (view !== "card") {{
        byId("dashboard-title").textContent = "Карта доработок для ревью аналитиком";
      }}
    }}

    function scrollToFeature(featureId) {{
      if (!featureId) return;
      const target = byId(`bf-group-${{featureId}}`) || byId(featureId);
      if (target) target.scrollIntoView({{block: "start"}});
    }}

    function routeDashboard() {{
      const route = routeFromHash();
      if (route.view === "card") {{
        renderSubjectCardScreen(route.slug || "");
      }}
      setActiveView(route.view);
      if (route.detailSlug) {{
        ensureDetailMapVisible(route.detailSlug);
      }}
      if (route.featureId) {{
        scrollToFeature(route.featureId);
      }}
    }}

    function filteredFeatures() {{
      const query = byId("search").value.trim().toLowerCase();
      const status = byId("status-filter").value;
      const confidence = byId("confidence-filter").value;
      const questionMode = byId("question-filter").value;
      return data.features.filter((feature) => {{
        const linkedMaps = (feature.detail_maps || []).map((item) => `${{item.title}} ${{item.type_label}}`).join(" ");
        const text = [feature.feature_id, feature.title, feature.domain, feature.summary, feature.notes, linkedMaps].join(" ").toLowerCase();
        if (query && !text.includes(query)) return false;
        if (status && feature.status !== status) return false;
        if (confidence && feature.confidence !== confidence) return false;
        if (questionMode === "open" && !feature.open_questions_count) return false;
        if (questionMode === "runtime" && !feature.infobase_checks_count) return false;
        return true;
      }});
    }}

    function renderFeatures() {{
      const features = filteredFeatures();
      byId("feature-list").innerHTML = features.length ? features.map(renderFeature).join("") : '<div class="panel empty">По текущим фильтрам блоки не найдены.</div>';
    }}

    function renderQuestions() {{
      byId("open-questions").innerHTML = rowsTable(data.open_questions, [
        ["item_id", "ID"],
        ["scenario_id", "BF"],
        ["status_label", "Статус"],
        ["reason", "Причина"],
        ["needed_input", "Что нужно"],
        ["impact", "Влияние"],
        ["owner", "Владелец"],
      ]);
    }}

    function filteredRuntimeChecks() {{
      const query = byId("runtime-search").value.trim().toLowerCase();
      const scenario = byId("runtime-scenario-filter").value;
      const method = byId("runtime-method-filter").value;
      const status = byId("runtime-status-filter").value;
      return runtimeChecks.filter((row) => {{
        const text = [row.item_id, row.scenario_id, row.status_after_pass_label, row.check_method_label, row.result_label, row.artifact_refs].join(" ").toLowerCase();
        if (query && !text.includes(query)) return false;
        if (scenario && row.scenario_id !== scenario) return false;
        if (method && row.check_method !== method) return false;
        if (status && row.status_after_pass !== status) return false;
        return true;
      }});
    }}

    function renderRuntimeChecks() {{
      const rows = filteredRuntimeChecks();
      const questionTable = rowsTable(infobaseQuestions, [
        ["question_id", "Вопрос"],
        ["feature_id", "BF"],
        ["subject_card_slug", "Карточка"],
        ["object_or_setting", "Объект или настройка"],
        ["status_label", "Статус"],
        ["check_target", "Что проверить"],
        ["risk_if_open", "Риск"],
      ]);
      const checkTable = rowsTable(rows, [
        ["scenario_id", "BF"],
        ["item_id", "Вопрос"],
        ["status_after_pass_label", "Статус"],
        ["check_method_label", "Метод"],
        ["result_label", "Результат"],
        ["artifact_refs", "Артефакты"],
      ]);
      byId("runtime-check-list").innerHTML = `<h3>Вопросы к ИБ</h3>${{questionTable}}<h3>Выполненные проверки</h3>${{checkTable}}`;
    }}

    function renderMigration() {{
      if (!data.risk_features.length) {{
        byId("migration-risks").innerHTML = '<div class="empty">Рисковые блоки не выделены текущими артефактами.</div>';
        return;
      }}
      byId("migration-risks").innerHTML = data.risk_features.map((feature) => {{
        const risks = feature.risks.map((risk) => `<li>${{esc(risk)}}</li>`).join("");
        return `<div class="box"><h3><a href="#bf/${{esc(feature.feature_id)}}">${{esc(feature.feature_id)}} · ${{esc(feature.title)}}</a></h3><ul>${{risks}}</ul></div>`;
      }}).join("");
    }}

    function renderAudit() {{
      byId("final-audit").innerHTML = `<div class="box">
        <p>Покрытие построено по текущим индексам и reverse-map состоянию.</p>
        <ul>
          <li>У нас ${{data.summary.feature_count}} BF-контейнеров.</li>
          <li>Из них выделено ${{data.summary.subject_registry_ready_count || subjectCards.length || 0}} предметных доработок в виде готовых карточек.</li>
          <li>Каждая BF либо покрыта карточкой доработки, либо явно помечена как кандидат, не классифицирована, поддерживающий слой или технический слой.</li>
          <li>Дашборд показывает предметные доработки, а BF остаются вторичной навигацией и доказательной подложкой.</li>
          <li>Строк сравнения: ${{data.summary.diff_count}}</li>
          <li>Строк финальной проверки: ${{data.summary.final_diff_count}}</li>
          <li>Открытых вопросов: ${{data.summary.open_question_count}}</li>
          <li>Вопросов к ИБ: ${{data.summary.infobase_question_count || 0}}, открыто: ${{data.summary.open_infobase_question_count || 0}}</li>
          <li>Закрытых проверок в ИБ: ${{data.summary.closed_infobase_check_count}} из ${{data.summary.infobase_check_count}}</li>
        </ul>
        <p><a href="${{esc(data.final_audit.href)}}">${{esc(data.final_audit.path)}}</a></p>
      </div>`;
      const outputs = (data.outputs || []).map((row) => ({{...row, exists: row.exists ? "да" : "нет"}}));
      byId("outputs").innerHTML = rowsTable(outputs, [
        ["path", "Файл"],
        ["exists", "Есть"],
        ["size", "Размер"],
      ]);
    }}

    function boot() {{
      applyDashboardScope();
      renderHeader();
      renderMetrics();
      fillFilters();
      renderSubjectMaps();
      renderFunctionalGaps();
      renderBfGroups();
      renderDetailMaps();
      renderFeatures();
      renderQuestions();
      renderRuntimeChecks();
      renderMigration();
      renderAudit();
      ["search","status-filter","confidence-filter","question-filter"].forEach((id) => byId(id).addEventListener("input", renderFeatures));
      ["technical-search","technical-type-filter","technical-status-filter","technical-feature-filter","technical-question-filter"].forEach((id) => byId(id).addEventListener("input", resetDetailMapFilters));
      ["runtime-search","runtime-scenario-filter","runtime-method-filter","runtime-status-filter"].forEach((id) => byId(id).addEventListener("input", renderRuntimeChecks));
      byId("technical-map-list").addEventListener("click", (event) => {{
        const more = event.target.closest ? event.target.closest("[data-detail-more]") : null;
        if (!more) return;
        detailMapVisibleLimit += DETAIL_MAP_PAGE_SIZE;
        renderDetailMaps();
      }});
      byId("technical-map-list").addEventListener("toggle", (event) => {{
        const details = event.target;
        if (details && details.classList && details.classList.contains("detail-map-details") && details.open) {{
          renderOpenedDetailMap(details);
        }}
      }}, true);
      document.addEventListener("click", (event) => {{
        const link = event.target.closest ? event.target.closest('a[href^="#detail-"]') : null;
        if (!link) return;
        const slug = decodeURIComponent(link.getAttribute("href").slice("#detail-".length));
        if (byId(`detail-${{slug}}`)) return;
        event.preventDefault();
        ensureDetailMapVisible(slug);
        window.location.hash = `detail-${{slug}}`;
      }});
      window.addEventListener("hashchange", routeDashboard);
      routeDashboard();
    }}
    boot();
  </script>
</body>
</html>
"""


def build_review_dashboard(root: Path, output_dir: Path | None = None, subject_card: str = "") -> dict[str, Any]:
    root = root.resolve()
    output_dir = output_dir or repo_path(root, DEFAULT_OUTPUT_DIR)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    data = build_dashboard_data(root, output_dir, subject_card=subject_card)
    data_path = output_dir / "data.json"
    html_path = output_dir / "index.html"
    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    html_path.write_text(dashboard_html(data), encoding="utf-8", newline="\n")
    return {
        "root": str(root),
        "output_dir": str(output_dir),
        "html": str(html_path),
        "data": str(data_path),
        "scope_mode": data.get("dashboard_scope", {}).get("mode", "full"),
        "subject_card": data.get("dashboard_scope", {}).get("subject_card", ""),
        "feature_count": data["summary"]["feature_count"],
        "detail_map_count": data["summary"]["detail_map_count"],
        "open_question_count": data["summary"]["open_question_count"],
        "risk_feature_count": data["summary"]["risk_feature_count"],
    }


def build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else repo_path(root, DEFAULT_OUTPUT_DIR)
    result = build_review_dashboard(root, output_dir, subject_card=args.subject_card or "")
    print(f"review_dashboard_html: {result['html']}")
    print(f"review_dashboard_data: {result['data']}")
    print(f"scope_mode: {result['scope_mode']}")
    if result["subject_card"]:
        print(f"subject_card: {result['subject_card']}")
    print(f"features: {result['feature_count']}")
    print(f"detail_maps: {result['detail_map_count']}")
    print(f"open_questions: {result['open_question_count']}")
    print(f"risk_features: {result['risk_feature_count']}")
    return 0
