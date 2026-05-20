from __future__ import annotations

import argparse
import csv
import json
import os
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
    "ui": "UI-поверхности",
    "integrations": "Интеграции",
    "sources": "Источники",
    "open_questions": "Открытые вопросы",
}


STATUS_LABELS = {
    "complete": "Завершено",
    "blocked_by_infobase_data": "Нужны данные ИБ",
    "requires_1c_review": "Требует ревью 1С",
    "requires_runtime_verification": "Требует runtime-проверки",
    "needs_reclassification": "Требует переклассификации",
    "open_question": "Открытый вопрос",
    "closed": "Закрыто",
    "confirmed_in_scenario": "Подтверждено в сценарии",
    "supporting_shared": "Общая поддержка",
    "needs_manual_review": "Требует ручного ревью",
    "needs_infobase_data": "Нужны данные ИБ",
    "needs_runtime_verification": "Нужна runtime-проверка",
    "belongs_to_other_scenario": "Другой сценарий",
    "technical_platform": "Техническая платформа",
    "technical_noise": "Технический шум",
    "out_of_scope": "Вне рамок",
    "draft": "Черновик",
    "needs_review": "Требует ревью",
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

RISK_STATUSES = {
    "blocked_by_infobase_data",
    "requires_1c_review",
    "requires_runtime_verification",
    "needs_reclassification",
}


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
        return text[:limit].rstrip() + "\n\n[Текст обрезан в dashboard; полный файл см. в исходном артефакте.]"
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
    return {
        "id": str(project.get("id", "") or ""),
        "product": str(project.get("product", "") or ""),
        "baseline_version": str(project.get("baseline_version", "") or ""),
        "target_version": str(project.get("target_version", "") or ""),
        "next_vendor_version": str(project.get("next_vendor_version", "") or ""),
        "description": str(project.get("description", "") or ""),
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


def sample_rows(rows: list[dict[str, str]], fields: list[str], limit: int = MAX_SAMPLE_ROWS) -> list[dict[str, str]]:
    sampled: list[dict[str, str]] = []
    for row in rows[:limit]:
        sampled.append({field: row.get(field, "") for field in fields})
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


def normalize_detail_rows(rows: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in as_list(rows):
        if isinstance(row, dict):
            normalized.append({str(key): "" if value is None else str(value) for key, value in row.items()})
    return normalized


def load_detail_maps(root: Path, output_dir: Path) -> list[dict[str, Any]]:
    detail_root = repo_path(root, "analysis/detail-maps")
    if not detail_root.exists():
        return []
    maps: list[dict[str, Any]] = []
    for path in sorted(detail_root.glob("*/detail-map.json")):
        if "_templates" in path.relative_to(detail_root).parts:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Could not parse {path.relative_to(root).as_posix()}: {exc}") from exc
        slug = str(raw.get("slug") or path.parent.name).strip()
        map_type = str(raw.get("type", "") or "other").strip()
        status = str(raw.get("status", "") or "draft").strip()
        confidence = str(raw.get("confidence", "") or "").strip()
        sections = {name: normalize_detail_rows(raw.get(name, [])) for name in DETAIL_MAP_SECTIONS}
        source_workbook = str(raw.get("source_workbook", "") or "").strip()
        maps.append(
            {
                "id": str(raw.get("id") or slug),
                "slug": slug,
                "title": str(raw.get("title", "") or slug),
                "type": map_type,
                "type_label": detail_type_label(map_type),
                "status": status,
                "status_label": status_label(status),
                "confidence": confidence,
                "confidence_label": confidence_label(confidence),
                "owner_feature": str(raw.get("owner_feature", "") or ""),
                "linked_features": text_list(raw.get("linked_features", [])),
                "summary": str(raw.get("summary", "") or ""),
                "migration_notes": text_list(raw.get("migration_notes", [])),
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
                    "status": detail_map["status"],
                    "status_label": detail_map["status_label"],
                    "href": f"#detail-{detail_map['slug']}",
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
    feature_id = (row.get("feature_id") or "").strip()
    scenario_dir = repo_path(root, f"analysis/reverse-map/scenarios/{feature_id}")
    scenario_evidence_rows = non_empty_rows(read_csv_rows(scenario_dir / "evidence.csv"))
    feature_pack_dir = repo_path(root, row.get("evidence_pack_path", "") or f"analysis/features/{feature_id}")
    feature_pack_evidence_rows = non_empty_rows(read_csv_rows(feature_pack_dir / "evidence.csv"))
    evidence_rows = scenario_evidence_rows or feature_pack_evidence_rows
    summary_md = read_text(scenario_dir / "summary.md")
    coverage_rows = non_empty_rows(coverage_by_scenario.get(feature_id, []))
    decisions = non_empty_rows(decisions_by_scenario.get(feature_id, []))
    unresolved = non_empty_rows(unresolved_by_scenario.get(feature_id, []))
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
        migration_risks.append(f"Есть проверки по ИБ: {len(infobase)}. Результаты демо/тестовой базы нужно отделять от production-вывода.")
    if status in RISK_STATUSES:
        migration_risks.append(f"Статус блока: {status_label(status)}. Требуется дополнительное подтверждение перед переходом на ДО 3.0.")
    if coverage_statuses.get("supporting_shared", 0):
        migration_risks.append("Часть строк является общей поддержкой нескольких сценариев; при сравнении с ДО 3.0 нужно не задвоить разрывы.")
    if coverage_statuses.get("belongs_to_other_scenario", 0):
        migration_risks.append("Есть строки, отнесенные к другому сценарию; нужна межсценарная сверка владения.")
    if not migration_risks:
        migration_risks.append("Критичных блокеров в текущих артефактах не выделено; проверить совместимость поведения с ДО 3.0 по доказательствам блока.")

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
        "evidence_pack_path": row.get("evidence_pack_path", ""),
        "evidence_pack_href": repo_href(root, output_dir, row.get("evidence_pack_path", "")) if row.get("evidence_pack_path") else "",
        "scenario_summary_path": f"analysis/reverse-map/scenarios/{feature_id}/summary.md",
        "scenario_summary_href": repo_href(root, output_dir, f"analysis/reverse-map/scenarios/{feature_id}/summary.md"),
        "summary_md": summary_md,
        "coverage_count": len(coverage_rows),
        "coverage_statuses": coverage_statuses,
        "coverage_confidence": coverage_confidence,
        "coverage_samples": sample_rows(
            coverage_rows,
            ["diff_id", "source", "path", "status", "confidence", "evidence_ref", "notes"],
        ),
        "evidence_count": len(evidence_rows),
        "evidence_confidence": evidence_confidence,
        "evidence_samples": sample_rows(
            evidence_rows,
            ["claim_id", "source_kind", "source_path", "line_start", "line_end", "evidence_type", "confidence", "summary"],
        ),
        "decisions_count": len(decisions),
        "decision_samples": sample_rows(decisions, ["decision_id", "decision", "confidence", "rationale", "evidence_ref"]),
        "open_questions_count": len(unresolved),
        "open_questions": unresolved,
        "infobase_checks_count": len(infobase),
        "infobase_checks": infobase,
        "detail_maps_count": len(linked_detail_maps),
        "detail_maps": linked_detail_maps,
        "migration_risks": migration_risks,
    }


def build_dashboard_data(root: Path, output_dir: Path) -> dict[str, Any]:
    project = load_project(root)
    feature_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/indexes/feature-map.csv")))
    diff_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/indexes/diff-inventory.csv")))
    final_diff_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/indexes/final-diff-inventory.csv")))
    final_feature_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/indexes/final-feature-map.csv")))
    coverage_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/reverse-map/coverage.csv")))
    decisions_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/reverse-map/decisions.csv")))
    unresolved_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/reverse-map/unresolved.csv")))
    infobase_rows = non_empty_rows(read_csv_rows(repo_path(root, "analysis/reverse-map/infobase-checks.csv")))
    output_open_questions = non_empty_rows(read_csv_rows(repo_path(root, "outputs/open-questions.csv")))
    detail_maps = load_detail_maps(root, output_dir)

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
        "risk_feature_count": len(risk_features),
        "detail_map_count": len(detail_maps),
        "detail_map_by_type": dict(sorted(Counter(detail_map["type"] for detail_map in detail_maps).items())),
        "detail_map_open_question_count": sum(detail_map["open_questions_count"] for detail_map in detail_maps),
    }

    final_audit_path = repo_path(root, "analysis/final-audit.md")
    output_files = []
    for relative in (
        "outputs/customization-map.md",
        "outputs/customization-map.xlsx",
        "outputs/open-questions.csv",
        "outputs/open-questions.xlsx",
        "analysis/final-audit.md",
    ):
        path = repo_path(root, relative)
        output_files.append(
            {
                "path": relative,
                "href": repo_href(root, output_dir, relative),
                "exists": path.exists(),
                "size": path.stat().st_size if path.exists() else 0,
            }
        )

    return {
        "schema_version": "review-dashboard/v1",
        "generated_at": utc_now_iso(),
        "project": project,
        "summary": summary,
        "features": features,
        "detail_maps": detail_maps,
        "open_questions": unresolved_rows,
        "output_open_questions": output_open_questions,
        "infobase_checks": infobase_rows,
        "risk_features": risk_features,
        "final_audit": {
            "path": "analysis/final-audit.md",
            "href": repo_href(root, output_dir, "analysis/final-audit.md"),
            "text": read_text(final_audit_path),
        },
        "outputs": output_files,
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
      border-radius: 6px;
      padding: 8px 10px;
      color: var(--text);
    }}
    .nav a:hover {{ background: var(--panel-soft); text-decoration: none; }}
    main {{ padding: 24px; max-width: 1680px; min-width: 0; width: 100%; overflow: hidden; }}
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
      grid-template-columns: minmax(260px, 1fr) minmax(160px, 220px) minmax(160px, 220px) minmax(160px, 200px);
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
    .feature-list {{ display: grid; gap: 12px; }}
    .feature {{ padding: 16px; scroll-margin-top: 16px; }}
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
      white-space: nowrap;
    }}
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
      .section-grid {{ grid-template-columns: 1fr; }}
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
        <a href="#summary">Свод <span id="nav-feature-count"></span></a>
        <a href="#detail-maps">Карты доработок <span id="nav-detail-count"></span></a>
        <a href="#features">Блоки BF <span id="nav-open-count"></span></a>
        <a href="#questions">Открытые вопросы</a>
        <a href="#migration">Переход на ДО 3.0</a>
        <a href="#audit">Финальный аудит</a>
      </nav>
    </aside>
    <main>
      <section class="hero">
        <div>
          <h1>Карта доработок для ревью аналитиком</h1>
          <div class="subtle" id="generated-at"></div>
        </div>
        <div class="badges">
          <span class="badge info">Статичный HTML</span>
          <span class="badge info">Без backend и БД</span>
          <span class="badge info">Источник: analysis/* и outputs/*</span>
        </div>
      </section>

      <section id="summary">
        <div class="grid metrics" id="metrics"></div>
      </section>

      <section id="detail-maps">
        <h2>Карты доработок</h2>
        <div class="toolbar">
          <input id="detail-search" type="search" placeholder="Поиск по карте, объекту, правилу, источнику">
          <select id="detail-type-filter"><option value="">Все типы</option></select>
          <select id="detail-status-filter"><option value="">Все статусы</option></select>
          <select id="detail-feature-filter"><option value="">Все BF</option></select>
          <select id="detail-question-filter">
            <option value="">Все карты</option>
            <option value="open">С открытыми вопросами</option>
          </select>
        </div>
        <div class="feature-list" id="detail-map-list"></div>
      </section>

      <section id="features">
        <h2>Блоки доработок BF-*</h2>
        <div class="toolbar">
          <input id="search" type="search" placeholder="Поиск по BF, названию, домену, описанию">
          <select id="status-filter"><option value="">Все статусы</option></select>
          <select id="confidence-filter"><option value="">Любая достоверность</option></select>
          <select id="question-filter">
            <option value="">Все блоки</option>
            <option value="open">С открытыми вопросами</option>
            <option value="runtime">С проверками ИБ/UI</option>
          </select>
        </div>
        <div class="feature-list" id="feature-list"></div>
      </section>

      <section id="questions">
        <h2>Открытые вопросы</h2>
        <div class="panel" id="open-questions"></div>
      </section>

      <section id="migration">
        <h2>Что важно для перехода на ДО 3.0</h2>
        <div class="panel" id="migration-risks"></div>
      </section>

      <section id="audit">
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
    const byId = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[char]));
    const badgeClass = (status) => {{
      if (["complete","closed","confirmed_in_scenario","supporting_shared"].includes(status)) return "ok";
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
      needs_runtime_verification: "Нужна runtime-проверка",
      requires_1c_review: "Требует ревью 1С",
      blocked_by_infobase_data: "Нужны данные ИБ",
      requires_runtime_verification: "Требует runtime-проверки",
      needs_reclassification: "Требует переклассификации",
    }};
    const statusLabel = (value, fallback) => fallback || value || "Не указано";
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
      byId("nav-feature-count").textContent = data.summary.feature_count || 0;
      byId("nav-detail-count").textContent = data.summary.detail_map_count || 0;
      byId("nav-open-count").textContent = data.summary.open_question_count || 0;
    }}

    function renderMetrics() {{
      const metrics = [
        ["Блоки BF", data.summary.feature_count],
        ["Строки сравнения", data.summary.diff_count],
        ["Строки покрытия", data.summary.coverage_count],
        ["Карты доработок", data.summary.detail_map_count || 0],
        ["Открытые вопросы", data.summary.open_question_count],
        ["Проверки ИБ/UI", data.summary.infobase_check_count],
        ["Рисковые блоки", data.summary.risk_feature_count],
      ];
      byId("metrics").innerHTML = metrics.map(([label, value]) => `<div class="metric"><strong>${{value}}</strong><span>${{esc(label)}}</span></div>`).join("");
    }}

    function fillFilters() {{
      const statuses = [...new Map(data.features.map((f) => [f.status, f.status_label])).entries()].filter(([key]) => key);
      const confidences = [...new Map(data.features.map((f) => [f.confidence, f.confidence_label])).entries()].filter(([key]) => key);
      byId("status-filter").innerHTML += statuses.map(([value, label]) => `<option value="${{esc(value)}}">${{esc(label)}}</option>`).join("");
      byId("confidence-filter").innerHTML += confidences.map(([value, label]) => `<option value="${{esc(value)}}">${{esc(label)}}</option>`).join("");
      const detailTypes = [...new Map((data.detail_maps || []).map((item) => [item.type, item.type_label])).entries()].filter(([key]) => key);
      const detailStatuses = [...new Map((data.detail_maps || []).map((item) => [item.status, item.status_label])).entries()].filter(([key]) => key);
      const detailFeatures = [...new Set((data.detail_maps || []).flatMap((item) => item.linked_features || []))].filter(Boolean).sort();
      byId("detail-type-filter").innerHTML += detailTypes.map(([value, label]) => `<option value="${{esc(value)}}">${{esc(label)}}</option>`).join("");
      byId("detail-status-filter").innerHTML += detailStatuses.map(([value, label]) => `<option value="${{esc(value)}}">${{esc(label)}}</option>`).join("");
      byId("detail-feature-filter").innerHTML += detailFeatures.map((value) => `<option value="${{esc(value)}}">${{esc(value)}}</option>`).join("");
    }}

    function linkedDetailMapsBox(feature) {{
      if (!feature.detail_maps || !feature.detail_maps.length) {{
        return '<span class="empty">Для блока пока нет отдельных предметных карт.</span>';
      }}
      return feature.detail_maps.map((item) => `<a class="badge ${{badgeClass(item.status)}}" href="${{esc(item.href)}}">${{esc(item.type_label)}} · ${{esc(item.title)}}</a>`).join(" ");
    }}

    function renderFeature(feature) {{
      const riskList = (feature.migration_risks || []).map((risk) => `<li>${{esc(risk)}}</li>`).join("");
      const questionTable = rowsTable(feature.open_questions, [
        ["item_id", "Вопрос"],
        ["status", "Статус"],
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
              <span class="badge">${{esc(feature.confidence_label)}}</span>
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
              ${{Object.entries(feature.source_counts || {{}}).map(([key, value]) => `<span class="badge">${{esc(key)}}: ${{value}}</span>`).join(" ") || '<span class="badge">не указаны</span>'}}
            </div>
          </div>
        </details>
        <details>
          <summary>Доказательства</summary>
          ${{rowsTable(feature.evidence_samples, [["claim_id","ID"],["source_kind","Источник"],["source_path","Путь"],["line_start","Начало"],["line_end","Конец"],["confidence","Достоверность"],["summary","Вывод"]])}}
        </details>
        <details>
          <summary>Решения reverse-map</summary>
          ${{rowsTable(feature.decision_samples, [["decision_id","ID"],["decision","Решение"],["confidence","Достоверность"],["rationale","Обоснование"],["evidence_ref","Evidence"]])}}
        </details>
        <details>
          <summary>Открытые вопросы блока</summary>
          ${{questionTable}}
        </details>
      </article>`;
    }}

    const detailColumns = {{
      attributes: [["object","Объект"],["kind","Тип"],["name","Имя"],["synonym","Синоним"],["data_type","Тип данных"],["vendor_status","Статус к вендору"],["relation","Связь с доработкой"],["confidence","Достоверность"],["source","Источник"],["line","Строка"],["comment","Комментарий"]],
      form_rules: [["id","ID"],["rule","Правило"],["description","Описание"],["mechanism","Условие/механизм"],["confidence","Достоверность"],["source","Источник"],["line","Строка"]],
      validations: [["id","ID"],["field","Поле"],["validation","Проверка/обязательность"],["mechanism","Механизм"],["confidence","Достоверность"],["source","Источник"],["line","Строка"]],
      lifecycle: [["id","ID"],["action","Переход/действие"],["description","Описание"],["mechanism","Условие/механизм"],["confidence","Достоверность"],["source","Источник"],["line","Строка"]],
      rights: [["id","ID"],["role","Роль/ФИО-роль"],["description","Описание"],["mechanism","Механизм"],["confidence","Достоверность"],["source","Источник"],["line","Строка"]],
      scheduled_jobs: [["id","ID"],["name","Регламентное задание"],["description","Описание"],["mechanism","Механизм"],["status","Статус"],["confidence","Достоверность"],["source","Источник"],["line","Строка"]],
      ui: [["id","ID"],["surface","UI-поверхность"],["command","Команда"],["description","Описание"],["confidence","Достоверность"],["source","Источник"],["line","Строка"]],
      integrations: [["id","ID"],["system","Система"],["flow","Поток"],["description","Описание"],["confidence","Достоверность"],["source","Источник"],["line","Строка"]],
      sources: [["id","ID"],["claim","Что подтверждает"],["source","Источник"],["line","Строка"],["evidence","Доказательство"]],
      open_questions: [["id","ID"],["question","Вопрос"],["why_open","Почему открыт"],["needed","Что нужно"]],
    }};
    const detailSectionLabels = {{
      attributes: "Реквизиты",
      form_rules: "Правила формы",
      validations: "Проверки заполнения",
      lifecycle: "Жизненный цикл",
      rights: "Права и роли",
      scheduled_jobs: "Регламентные задания",
      ui: "UI-поверхности",
      integrations: "Интеграции",
      sources: "Источники",
      open_questions: "Открытые вопросы",
    }};

    function detailSection(map, sectionKey) {{
      const rows = (map.sections || {{}})[sectionKey] || [];
      const count = (map.counts || {{}})[sectionKey] || rows.length;
      return `<details>
        <summary>${{esc(detailSectionLabels[sectionKey])}} · ${{count}}</summary>
        ${{rowsTable(rows, detailColumns[sectionKey])}}
      </details>`;
    }}

    function renderDetailMap(map) {{
      const notes = (map.migration_notes || []).map((note) => `<li>${{esc(note)}}</li>`).join("") || "<li>Отдельные замечания по переходу пока не указаны.</li>";
      const features = (map.linked_features || []).map((featureId) => `<a class="badge" href="#${{esc(featureId)}}">${{esc(featureId)}}</a>`).join(" ") || '<span class="badge">BF не указан</span>';
      const sourceLinks = `<div><a href="${{esc(map.source_href)}}">${{esc(map.source_path)}}</a></div>` + (map.source_workbook_href ? `<div><a href="${{esc(map.source_workbook_href)}}">${{esc(map.source_workbook)}}</a></div>` : "");
      return `<article class="feature" id="detail-${{esc(map.slug)}}">
        <div class="feature-head">
          <div>
            <div class="title-row">
              <span class="feature-title">${{esc(map.title)}}</span>
              <span class="badge info">${{esc(map.type_label)}}</span>
              <span class="badge ${{badgeClass(map.status)}}">${{esc(map.status_label)}}</span>
              <span class="badge">${{esc(map.confidence_label)}}</span>
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
        <div class="section-grid">
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
        ${{Object.keys(detailSectionLabels).map((sectionKey) => detailSection(map, sectionKey)).join("")}}
      </article>`;
    }}

    function filteredDetailMaps() {{
      const query = byId("detail-search").value.trim().toLowerCase();
      const type = byId("detail-type-filter").value;
      const status = byId("detail-status-filter").value;
      const feature = byId("detail-feature-filter").value;
      const questionMode = byId("detail-question-filter").value;
      return (data.detail_maps || []).filter((map) => {{
        const text = JSON.stringify(map).toLowerCase();
        if (query && !text.includes(query)) return false;
        if (type && map.type !== type) return false;
        if (status && map.status !== status) return false;
        if (feature && !(map.linked_features || []).includes(feature)) return false;
        if (questionMode === "open" && !map.open_questions_count) return false;
        return true;
      }});
    }}

    function renderDetailMaps() {{
      const maps = filteredDetailMaps();
      byId("detail-map-list").innerHTML = maps.length ? maps.map(renderDetailMap).join("") : '<div class="panel empty">Предметные карты по текущим фильтрам не найдены.</div>';
    }}

    function filteredFeatures() {{
      const query = byId("search").value.trim().toLowerCase();
      const status = byId("status-filter").value;
      const confidence = byId("confidence-filter").value;
      const questionMode = byId("question-filter").value;
      return data.features.filter((feature) => {{
        const linkedMaps = (feature.detail_maps || []).map((item) => `${{item.title}} ${{item.type_label}}`).join(" ");
        const text = [feature.feature_id, feature.title, feature.domain, feature.summary, feature.notes, linkedMaps, feature.summary_md].join(" ").toLowerCase();
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
        ["status", "Статус"],
        ["reason", "Причина"],
        ["needed_input", "Что нужно"],
        ["impact", "Влияние"],
        ["owner", "Владелец"],
      ]);
    }}

    function renderMigration() {{
      if (!data.risk_features.length) {{
        byId("migration-risks").innerHTML = '<div class="empty">Рисковые блоки не выделены текущими артефактами.</div>';
        return;
      }}
      byId("migration-risks").innerHTML = data.risk_features.map((feature) => {{
        const risks = feature.risks.map((risk) => `<li>${{esc(risk)}}</li>`).join("");
        return `<div class="box"><h3><a href="#${{esc(feature.feature_id)}}">${{esc(feature.feature_id)}} · ${{esc(feature.title)}}</a></h3><ul>${{risks}}</ul></div>`;
      }}).join("");
    }}

    function renderAudit() {{
      byId("final-audit").innerHTML = `<div class="box">
        <p>Покрытие построено по текущим индексам и reverse-map состоянию.</p>
        <ul>
          <li>Блоков BF: ${{data.summary.feature_count}}</li>
          <li>Строк сравнения: ${{data.summary.diff_count}}</li>
          <li>Строк final-gate: ${{data.summary.final_diff_count}}</li>
          <li>Открытых вопросов: ${{data.summary.open_question_count}}</li>
          <li>Закрытых проверок ИБ/UI: ${{data.summary.closed_infobase_check_count}} из ${{data.summary.infobase_check_count}}</li>
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
      renderHeader();
      renderMetrics();
      fillFilters();
      renderDetailMaps();
      renderFeatures();
      renderQuestions();
      renderMigration();
      renderAudit();
      ["search","status-filter","confidence-filter","question-filter"].forEach((id) => byId(id).addEventListener("input", renderFeatures));
      ["detail-search","detail-type-filter","detail-status-filter","detail-feature-filter","detail-question-filter"].forEach((id) => byId(id).addEventListener("input", renderDetailMaps));
    }}
    boot();
  </script>
</body>
</html>
"""


def build_review_dashboard(root: Path, output_dir: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    output_dir = output_dir or repo_path(root, DEFAULT_OUTPUT_DIR)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    data = build_dashboard_data(root, output_dir)
    data_path = output_dir / "data.json"
    html_path = output_dir / "index.html"
    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    html_path.write_text(dashboard_html(data), encoding="utf-8", newline="\n")
    return {
        "root": str(root),
        "output_dir": str(output_dir),
        "html": str(html_path),
        "data": str(data_path),
        "feature_count": data["summary"]["feature_count"],
        "detail_map_count": data["summary"]["detail_map_count"],
        "open_question_count": data["summary"]["open_question_count"],
        "risk_feature_count": data["summary"]["risk_feature_count"],
    }


def build_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else repo_path(root, DEFAULT_OUTPUT_DIR)
    result = build_review_dashboard(root, output_dir)
    print(f"review_dashboard_html: {result['html']}")
    print(f"review_dashboard_data: {result['data']}")
    print(f"features: {result['feature_count']}")
    print(f"detail_maps: {result['detail_map_count']}")
    print(f"open_questions: {result['open_question_count']}")
    print(f"risk_features: {result['risk_feature_count']}")
    return 0
