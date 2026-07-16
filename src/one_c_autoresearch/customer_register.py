from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .autopilot import validate_minimal_xlsx, write_minimal_xlsx
from .common import read_jsonl, repo_path, utc_now_iso, write_jsonl
from .customization_registry import customization_trace_payload, customizations_for_subject, load_registry_index
from .migration_requirements import PUBLISHABLE, canonical_active, load_index as load_migration_requirement_index
from .subject_cards import read_csv_rows, split_refs


CUSTOMER_REGISTER_COLUMNS = (
    "number",
    "code",
    "title",
    "business_area",
    "change_summary",
    "key_objects",
    "transition_decision",
    "target_release_coverage",
    "residual_gap",
    "risk",
    "verification_status",
    "recommendation",
)
CUSTOMER_REGISTER_HEADER = ",".join(CUSTOMER_REGISTER_COLUMNS)
CUSTOMER_REGISTER_COLUMN_LABELS = {
    "number": "№",
    "code": "Код",
    "title": "Доработка",
    "business_area": "Бизнес-область",
    "change_summary": "Что менялось",
    "key_objects": "Ключевые объекты",
    "transition_decision": "Решение по переходу",
    "target_release_coverage": "Покрытие целевого релиза",
    "residual_gap": "Остаточный разрыв",
    "risk": "Риск",
    "verification_status": "Статус проверки",
    "recommendation": "Рекомендация",
}
CUSTOMER_REGISTER_OUTPUT_HEADER = [CUSTOMER_REGISTER_COLUMN_LABELS[column] for column in CUSTOMER_REGISTER_COLUMNS]
CUSTOMER_REGISTER_CSV = "outputs/customer-customization-register.csv"
CUSTOMER_REGISTER_MD = "outputs/customer-customization-register.md"
CUSTOMER_REGISTER_XLSX = "outputs/customer-customization-register.xlsx"
CUSTOMER_REGISTER_TRACE_JSONL = "outputs/customer-customization-register.trace.jsonl"

FORBIDDEN_CUSTOMER_MARKERS = re.compile(
    r"\b(?:FGF|FGM|BFP)-\d+\b|"
    r"\b(?:CUS|CMI|V8D|EXT|EPR)-[A-Z0-9-]+\b|"
    r"(?:^|[\\/])analysis[\\/]|"
    r"\.id\.json\b|"
    r"\bDataProcessor\.(?:id|json)\b|"
    r"\.json\b",
    re.IGNORECASE,
)

TYPE_LABELS = {
    "business_process": "Бизнес-процесс",
    "business_document": "Документооборот",
    "reference_model": "НСИ",
    "integration": "Интеграции",
    "access_model": "Права и доступ",
    "ui_surface": "Интерфейс и печать",
    "background_automation": "Фоновые операции",
    "technical_support": "Техническая поддержка",
}

DECISION_LABELS = {
    "adapt": "Адаптировать на типовой основе",
    "replace_by_standard": "Заменить типовым механизмом",
    "preserve": "Перенести без изменений",
    "retire": "Вывести из эксплуатации",
    "business_decision": "Требуется бизнес-решение",
}

METADATA_TYPE_LABELS = {
    "AccountingRegister": "Регистр бухгалтерии",
    "AccumulationRegister": "Регистр накопления",
    "Catalog": "Справочник",
    "ChartOfCalculationTypes": "План видов расчета",
    "ChartOfCharacteristicType": "План видов характеристик",
    "CommonAttribute": "Общий реквизит",
    "CommonCommand": "Общая команда",
    "CommonForm": "Общая форма",
    "CommonModule": "Общий модуль",
    "CommonPicture": "Общая картинка",
    "CommonTemplate": "Общий макет",
    "Configuration": "Конфигурация",
    "DataProcessor": "Обработка",
    "Document": "Документ",
    "DocumentJournal": "Журнал документов",
    "Enum": "Перечисление",
    "EventSubscription": "Подписка на событие",
    "ExchangePlan": "План обмена",
    "InformationRegister": "Регистр сведений",
    "Interface": "Интерфейс",
    "Report": "Отчет",
    "Role": "Роль",
    "ScheduledJob": "Регламентное задание",
    "SessionParameter": "Параметр сеанса",
    "Subsystem": "Подсистема",
    "WebService": "Веб-сервис",
}

METADATA_TYPE_MARKERS = re.compile(r"\b(" + "|".join(sorted(METADATA_TYPE_LABELS, key=len, reverse=True)) + r")\.")


def _root(args: argparse.Namespace) -> Path:
    return Path(args.repo_path).resolve() if getattr(args, "repo_path", "") else Path.cwd()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _compact(text: Any, limit: int = 320) -> str:
    value = " ".join(str(text or "").replace("|", "/").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip(" ,.;") + "…"


def _customer_text(text: Any) -> str:
    return _localize_metadata_types(" ".join(str(text or "").replace("|", "/").split()))


def _localize_metadata_types(text: str) -> str:
    return METADATA_TYPE_MARKERS.sub(lambda match: f"{METADATA_TYPE_LABELS[match.group(1)]}.", text)


def _clean_object(value: str) -> str:
    item = _compact(value, 120)
    if not item:
        return ""
    if item.endswith(".json") or item.endswith(".id"):
        return ""
    if item in {"DataProcessor.id", "DataProcessor.json"}:
        return ""
    if "/" in item or "\\" in item:
        return ""
    return item


def _customer_object(value: str) -> str:
    return _localize_metadata_types(value)


def _join_objects(values: list[str], limit: int = 7) -> str:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _clean_object(value)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(_customer_object(item))
        if len(result) >= limit:
            break
    return "; ".join(result)


def _ready_subject_rows(root: Path) -> list[dict[str, str]]:
    rows = read_csv_rows(repo_path(root, "analysis/subject-cards/registry.csv"))
    return [row for row in rows if (row.get("status") or "").strip() == "ready_for_review"]


def _live_check_index(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    review_root = repo_path(root, "analysis/functional-gaps/review")
    if not review_root.exists():
        return result
    for path in sorted(review_root.glob("live-check*.md")):
        text = path.read_text(encoding="utf-8")
        for slug in re.findall(r"\bbf-[a-z0-9-]+|\b[a-z0-9-]+-i-[a-z0-9-]+", text):
            result.setdefault(slug, "Есть живая проверка runtime-метаданных; приемочный сценарий на данных не закрыт")
    return result


def _decision(gap_card: dict[str, Any]) -> str:
    selected = str(gap_card.get("selected_decision") or "").strip()
    if selected:
        return selected
    for hypothesis in gap_card.get("hypotheses") or []:
        if str(hypothesis.get("status") or "").strip() == "selected":
            return str(hypothesis.get("gap_type") or "").strip()
    return ""


def _decision_summary(gap_card: dict[str, Any]) -> str:
    for hypothesis in gap_card.get("hypotheses") or []:
        if str(hypothesis.get("status") or "").strip() == "selected":
            return _customer_text(hypothesis.get("decision") or hypothesis.get("summary"))
    return _customer_text(gap_card.get("selected_decision_summary"))


def _review_summary(review_text: str) -> str:
    for header in ("## Вывод", "## Итоговое решение", "## Решение"):
        if header not in review_text:
            continue
        section = review_text.split(header, 1)[1].strip()
        section = section.split("\n## ", 1)[0].strip()
        paragraphs = [line.strip("- \t") for line in section.splitlines() if line.strip()]
        if paragraphs:
            return _customer_text(paragraphs[0])
    return ""


def _residual_gap(gap_card: dict[str, Any], review_text: str) -> str:
    return _decision_summary(gap_card) or _review_summary(review_text) or "Остаточный разрыв не выделен отдельно"


def _coverage_status(row: dict[str, str]) -> str:
    status = (row.get("coverage_status") or "unknown").strip() or "unknown"
    mapping_type = (row.get("mapping_type") or "").lower()
    if status == "covered" and any(marker in mapping_type for marker in ("candidate_only", "shared_infrastructure", "name_only")):
        return "partially_covered"
    return status


def _coverage(mapping_rows: list[dict[str, str]], target_rows: list[dict[str, str]]) -> tuple[str, Counter[str]]:
    counts = Counter(_coverage_status(row) for row in mapping_rows)
    covered = counts.get("covered", 0)
    partial = counts.get("partially_covered", 0)
    missing = counts.get("not_covered", 0) + counts.get("unknown", 0)
    findings = len(target_rows)
    if not mapping_rows:
        base = "Проверка целевого релиза не найдена"
    elif missing == 0 and partial == 0:
        base = "Целевой релиз покрывает ключевые объекты сценария"
    elif covered or partial:
        base = f"Есть типовая основа: покрыто {covered}, частично покрыто {partial}, не покрыто {missing}"
    else:
        base = f"Прямое покрытие не подтверждено: не покрыто {missing}"
    if findings:
        return f"{base}; проверенных механизмов: {findings}", counts
    return base, counts


def _risk(decision: str, coverage_counts: Counter[str]) -> str:
    missing = coverage_counts.get("not_covered", 0) + coverage_counts.get("unknown", 0)
    if decision == "replace_by_standard" and missing == 0:
        return "низкий"
    if missing >= 5:
        return "высокий"
    if decision in {"adapt", "business_decision"}:
        return "средний"
    return "низкий"


def _verification_status(slug: str, check_rows: list[dict[str, str]], live_checks: dict[str, str]) -> str:
    if slug in live_checks:
        return live_checks[slug]
    statuses = {(row.get("status") or "").strip() for row in check_rows if row.get("status")}
    if statuses and statuses <= {"done", "closed"}:
        return "Статическая проверка целевого релиза закрыта; живая приемка не выполнялась"
    if any(status in {"open", "todo", "needs_review"} for status in statuses):
        return "Есть открытые проверочные вопросы"
    return "Проверка по карточке зафиксирована статическими артефактами"


def _recommendation(decision: str) -> str:
    if decision == "replace_by_standard":
        return "Использовать типовой механизм целевого релиза и выполнить приемочную регрессию"
    if decision == "retire":
        return "Согласовать вывод сценария из эксплуатации"
    if decision == "preserve":
        return "Перенести точечно и проверить совместимость на приемочном сценарии"
    if decision == "business_decision":
        return "Вынести решение на владельца процесса до оценки реализации"
    return "Запланировать адаптацию на типовой основе целевого релиза"


def build_customer_register(root: Path) -> dict[str, Any]:
    if canonical_active(root):
        return _build_customer_register_from_requirements(root)
    rows: list[dict[str, str]] = []
    trace_rows: list[dict[str, Any]] = []
    registry = load_registry_index(root)
    live_checks = _live_check_index(root)
    for index, registry_row in enumerate(_ready_subject_rows(root), 1):
        slug = (registry_row.get("slug") or "").strip()
        subject = _read_json(repo_path(root, f"analysis/subject-cards/cards/{slug}/subject-card.json"))
        gap_root = repo_path(root, f"analysis/functional-gaps/cards/{slug}")
        gap_card = _read_json(gap_root / "gap-card.json")
        mapping_rows = read_csv_rows(gap_root / "object-mapping.csv")
        target_rows = read_csv_rows(gap_root / "target-findings.csv")
        check_rows = read_csv_rows(gap_root / "checks.csv")
        review_path = gap_root / "review.md"
        review_text = review_path.read_text(encoding="utf-8") if review_path.exists() else ""
        decision = _decision(gap_card)
        coverage, coverage_counts = _coverage(mapping_rows, target_rows)
        primary_objects = split_refs(registry_row.get("primary_objects") or subject.get("primary_objects") or [])
        summary = subject.get("summary") or subject.get("subject_summary") or registry_row.get("coverage_scope") or registry_row.get("why_separate_card")
        rows.append(
            {
                "number": str(index),
                "code": _compact(registry_row.get("owner_feature") or (split_refs(registry_row.get("linked_features")) or [slug])[0], 40),
                "title": _compact(registry_row.get("title") or subject.get("title") or slug, 180),
                "business_area": TYPE_LABELS.get(registry_row.get("subject_type") or subject.get("subject_type"), _compact(registry_row.get("subject_type"), 80)),
                "change_summary": _customer_text(summary),
                "key_objects": _join_objects(primary_objects),
                "transition_decision": DECISION_LABELS.get(decision, _compact(decision or "Требуется анализ", 120)),
                "target_release_coverage": _compact(coverage, 180),
                "residual_gap": _residual_gap(gap_card, review_text),
                "risk": _risk(decision, coverage_counts),
                "verification_status": _verification_status(slug, check_rows, live_checks),
                "recommendation": _recommendation(decision),
            }
        )
        customization_ids = [str(item.get("customization_id") or "") for item in customizations_for_subject(root, slug, registry)]
        trace_rows.append(
            {
                "row_number": str(index),
                "subject_card_slug": slug,
                "title": rows[-1]["title"],
                "customization_ids": sorted(set(customization_ids)),
                "semantic_customizations": customization_trace_payload(root, customization_ids, registry),
            }
        )
    csv_path = repo_path(root, CUSTOMER_REGISTER_CSV)
    md_path = repo_path(root, CUSTOMER_REGISTER_MD)
    _write_customer_csv(csv_path, rows)
    write_minimal_xlsx(
        repo_path(root, CUSTOMER_REGISTER_XLSX),
        [CUSTOMER_REGISTER_OUTPUT_HEADER, *[[row[column] for column in CUSTOMER_REGISTER_COLUMNS] for row in rows]],
        force=True,
        sheet_name="Реестр",
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(_render_markdown(rows), encoding="utf-8", newline="\n")
    write_jsonl(repo_path(root, CUSTOMER_REGISTER_TRACE_JSONL), trace_rows)
    return {"status": "ok", "rows": len(rows), "csv": CUSTOMER_REGISTER_CSV, "markdown": CUSTOMER_REGISTER_MD, "xlsx": CUSTOMER_REGISTER_XLSX}


def _build_customer_register_from_requirements(root: Path) -> dict[str, Any]:
    index = load_migration_requirement_index(root)
    requirements = [row for row in index["requirements"] if row.get("status") in PUBLISHABLE]
    rows = []
    trace_rows = []
    for number, requirement in enumerate(requirements, 1):
        links = index["links_by_req"][requirement["requirement_id"]]
        rows.append({
            "number": str(number),
            "code": f"REQ-{number:03d}",
            "title": _customer_text(requirement["title"]),
            "business_area": _customer_text("; ".join(TYPE_LABELS.get(tag, tag) for tag in requirement.get("subject_tags") or [])),
            "change_summary": _customer_text(requirement["source_scenario"]),
            "key_objects": "См. внутреннюю трассировку",
            "transition_decision": _customer_text(DECISION_LABELS.get(requirement["target_solution"], requirement["target_solution"])),
            "target_release_coverage": _customer_text(requirement["bp30_coverage"]),
            "residual_gap": _customer_text(requirement["residual_gap"]),
            "risk": _customer_text(requirement["risk"]),
            "verification_status": {"ready_for_review": "Готово к проверке", "approved": "Согласовано"}.get(requirement["status"], _customer_text(requirement["status"])),
            "recommendation": _customer_text(requirement["specification_text"]),
        })
        trace_rows.append({"row_number": str(number), "requirement_id": requirement["requirement_id"], "requirement_hash": _sha256_json(requirement), "customization_ids": [link["customization_id"] for link in links]})
    _write_customer_csv(repo_path(root, CUSTOMER_REGISTER_CSV), rows)
    write_minimal_xlsx(repo_path(root, CUSTOMER_REGISTER_XLSX), [CUSTOMER_REGISTER_OUTPUT_HEADER, *[[row[column] for column in CUSTOMER_REGISTER_COLUMNS] for row in rows]], force=True, sheet_name="Реестр")
    repo_path(root, CUSTOMER_REGISTER_MD).write_text(_render_markdown(rows), encoding="utf-8")
    write_jsonl(repo_path(root, CUSTOMER_REGISTER_TRACE_JSONL), trace_rows)
    return {"status": "ok", "rows": len(rows), "source": "migration-requirements", "csv": CUSTOMER_REGISTER_CSV, "markdown": CUSTOMER_REGISTER_MD, "xlsx": CUSTOMER_REGISTER_XLSX}


def _sha256_json(payload: Any) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write_customer_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CUSTOMER_REGISTER_OUTPUT_HEADER, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({CUSTOMER_REGISTER_COLUMN_LABELS[column]: row.get(column, "") for column in CUSTOMER_REGISTER_COLUMNS})


def _read_customer_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = []
        for raw_row in reader:
            rows.append({column: raw_row.get(CUSTOMER_REGISTER_COLUMN_LABELS[column], "") for column in CUSTOMER_REGISTER_COLUMNS})
    return header, rows


def _escape_md(value: Any) -> str:
    return _customer_text(value).replace("|", "\\|")


def _render_markdown(rows: list[dict[str, str]]) -> str:
    decisions = Counter(row["transition_decision"] for row in rows)
    risks = Counter(row["risk"] for row in rows)
    lines = [
        "# Краткий реестр доработок",
        "",
        f"Сформировано: {utc_now_iso()}.",
        f"Всего предметных доработок: {len(rows)}.",
        "",
        "## Сводка",
        "",
    ]
    for decision, count in sorted(decisions.items()):
        lines.append(f"- {decision}: {count}")
    lines.append("")
    for risk, count in sorted(risks.items()):
        lines.append(f"- Риск {risk}: {count}")
    lines.extend(
        [
            "",
            "## Реестр",
            "",
            "| " + " | ".join(CUSTOMER_REGISTER_OUTPUT_HEADER) + " |",
            "|" + "|".join("---:" if column == "number" else "---" for column in CUSTOMER_REGISTER_COLUMNS) + "|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(_escape_md(row[column]) for column in CUSTOMER_REGISTER_COLUMNS)
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def validate_customer_register(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    csv_path = repo_path(root, CUSTOMER_REGISTER_CSV)
    md_path = repo_path(root, CUSTOMER_REGISTER_MD)
    if not csv_path.exists():
        errors.append(f"Missing required path: {CUSTOMER_REGISTER_CSV}")
        return {"status": "fail", "errors": errors, "rows": 0}
    if not md_path.exists():
        errors.append(f"Missing required path: {CUSTOMER_REGISTER_MD}")
    trace_path = repo_path(root, CUSTOMER_REGISTER_TRACE_JSONL)
    registry = load_registry_index(root)
    if registry["exists"] and not trace_path.exists():
        errors.append(f"Missing required path: {CUSTOMER_REGISTER_TRACE_JSONL}")
    xlsx_path = repo_path(root, CUSTOMER_REGISTER_XLSX)
    if not xlsx_path.exists():
        errors.append(f"Missing required path: {CUSTOMER_REGISTER_XLSX}")
    else:
        errors.extend(f"{CUSTOMER_REGISTER_XLSX}: {error}" for error in validate_minimal_xlsx(xlsx_path))
    header, rows = _read_customer_csv(csv_path)
    expected_header = CUSTOMER_REGISTER_OUTPUT_HEADER
    if header != expected_header:
        errors.append(f"{CUSTOMER_REGISTER_CSV} header mismatch: expected {expected_header}, got {header}")
    mrq_index = load_migration_requirement_index(root) if canonical_active(root) else None
    ready_rows = ([{"slug": row["requirement_id"], "title": row["title"]} for row in mrq_index["requirements"] if row.get("status") in PUBLISHABLE] if mrq_index else _ready_subject_rows(root))
    trace_rows: list[dict[str, Any]] = []
    if trace_path.exists():
        trace_rows = [row for _, row in read_jsonl(trace_path)]
    trace_by_slug = {str(row.get("subject_card_slug") or row.get("requirement_id") or ""): row for row in trace_rows}
    if len(rows) != len(ready_rows):
        errors.append(f"{CUSTOMER_REGISTER_CSV} should contain one row per ready subject card: expected {len(ready_rows)}, got {len(rows)}")
    ready_titles = {row.get("title", "") for row in ready_rows}
    row_title_counts = Counter(row.get("title", "") for row in rows)
    for ready_row in ready_rows:
        title = ready_row.get("title", "")
        if row_title_counts[title] < 1:
            errors.append(f"{CUSTOMER_REGISTER_CSV} misses ready subject-card row: {ready_row.get('slug', '')}")
        slug = ready_row.get("slug", "")
        if registry["exists"] and not mrq_index:
            linked_ids = {str(item.get("customization_id") or "") for item in customizations_for_subject(root, slug, registry)}
            trace_ids = set(trace_by_slug.get(slug, {}).get("customization_ids") or [])
            missing_trace = sorted(linked_ids - trace_ids)
            if missing_trace:
                errors.append(f"{CUSTOMER_REGISTER_TRACE_JSONL} misses CUS links for {slug}: {';'.join(missing_trace[:10])}")
        elif mrq_index:
            expected = {link["customization_id"] for link in mrq_index["links_by_req"].get(slug, [])}
            actual = set(trace_by_slug.get(slug, {}).get("customization_ids") or [])
            if expected != actual:
                errors.append(f"{CUSTOMER_REGISTER_TRACE_JSONL} has stale MRQ trace for {slug}")
    for row in rows:
        if any("…" in str(value or "") for value in row.values()):
            errors.append(f"{CUSTOMER_REGISTER_CSV} row {row.get('number', '?')} contains truncated text marker")
        if any(METADATA_TYPE_MARKERS.search(str(value or "")) for value in row.values()):
            errors.append(f"{CUSTOMER_REGISTER_CSV} row {row.get('number', '?')} exposes untranslated metadata type")
        if row.get("title", "") not in ready_titles:
            errors.append(f"{CUSTOMER_REGISTER_CSV} contains title outside ready subject-card registry: {row.get('title')}")
        for field, value in row.items():
            if not str(value or "").strip():
                errors.append(f"{CUSTOMER_REGISTER_CSV} row {row.get('number', '?')} has empty field: {field}")
            if FORBIDDEN_CUSTOMER_MARKERS.search(str(value or "")):
                errors.append(f"{CUSTOMER_REGISTER_CSV} row {row.get('number', '?')} exposes technical marker in {field}: {value}")
    if md_path.exists():
        text = md_path.read_text(encoding="utf-8")
        for title in ready_titles:
            if title and title not in text:
                errors.append(f"{CUSTOMER_REGISTER_MD} misses ready subject title: {title}")
        if FORBIDDEN_CUSTOMER_MARKERS.search(text):
            errors.append(f"{CUSTOMER_REGISTER_MD} exposes technical markers")
    return {"status": "fail" if errors else "ok", "errors": errors, "rows": len(rows)}


def build_command(args: argparse.Namespace) -> int:
    print(json.dumps(build_customer_register(_root(args)), ensure_ascii=False, indent=2))
    return 0


def validate_command(args: argparse.Namespace) -> int:
    result = validate_customer_register(_root(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1
