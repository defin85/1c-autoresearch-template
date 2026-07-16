from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .common import repo_path, utc_now_iso
from .detailed_customer_register import DETAILED_REGISTER_CSV, DETAIL_FORBIDDEN_CUSTOMER_MARKERS, read_detailed_register_rows
from .subject_cards import join_refs, read_csv_rows, split_refs, write_csv_rows


WORK_DIR = "analysis/detailed-register-reverse-review"
OUTPUT_DIR = "outputs/detailed-register-reverse-review"
MARKUP_CSV = f"{WORK_DIR}/markup.csv"
SOURCE_MANIFEST = f"{WORK_DIR}/source-manifest.json"
CONFLICTS_CSV = f"{WORK_DIR}/conflicts.csv"
CARD_CROSS_REVIEW_CSV = f"{WORK_DIR}/card-cross-review.csv"
OPEN_QUESTIONS_CSV = f"{WORK_DIR}/open-questions.csv"
SUMMARY_MD = f"{WORK_DIR}/summary.md"

MARKUP_HEADER = (
    "row_id,source_checksum,source,source_title,source_type,key_objects,current_business_area,"
    "current_business_meaning,current_card,current_card_title,reconstructed_business_area,"
    "reconstructed_business_meaning,expected_card,expected_card_title,review_method,review_method_label,"
    "review_status,review_status_label,confidence,confidence_label,reason,evidence_refs,reviewer_notes"
)
CONFLICTS_HEADER = "row_id,conflict_type,conflict_type_label,current_binding,expected_binding,reason,recommended_action"
CARD_CROSS_REVIEW_HEADER = "card_slug,card_title,confirmed_count,corrected_count,weak_count,conflict_count,unlinked_count,coverage_status"
OPEN_QUESTIONS_HEADER = "row_id,expected_card,expected_card_title,review_status,confidence,question,needed_evidence,evidence_refs"

REVIEW_METHOD_LABELS = {
    "draft_heuristic": "Черновая эвристика",
    "manual": "Ручная разметка",
    "llm_assisted": "Разметка с агентом",
    "cross_review": "Перекрестная проверка",
}
REVIEW_STATUS_LABELS = {
    "confirmed": "Подтверждено",
    "corrected": "Уточнено",
    "weak_heuristic": "Слабая эвристика",
    "needs_evidence": "Нужны доказательства",
    "technical_supporting": "Техническая опора",
    "card_mismatch": "Конфликт карточки",
    "duplicate_or_merged": "Дубль или объединение",
    "external_postponed": "Отложено: внешний источник",
}
CONFIDENCE_LABELS = {"high": "Высокая", "medium": "Средняя", "low": "Низкая"}
CONFLICT_LABELS = {
    "card_mismatch": "Конфликт карточки",
    "missing_card": "Нет карточки",
    "needs_evidence": "Нужны доказательства",
    "technical_supporting": "Техническая опора",
    "duplicate_or_merged": "Дубль или объединение",
    "external_postponed": "Отложено: внешний источник",
}
STRONG_STATUSES = {"confirmed", "corrected", "card_mismatch"}
OPEN_STATUSES = {"weak_heuristic", "needs_evidence"}
FINAL_TEMPORARY_STATUSES = {"weak_heuristic", "needs_evidence"}


def _root(args: argparse.Namespace) -> Path:
    return Path(args.repo_path).resolve() if getattr(args, "repo_path", "") else Path.cwd()


def _source_path(root: Path) -> Path:
    return repo_path(root, DETAILED_REGISTER_CSV)


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_manifest(root: Path, source_rows: list[dict[str, str]]) -> dict[str, Any]:
    path = _source_path(root)
    return {
        "source_path": DETAILED_REGISTER_CSV,
        "source_rows": len(source_rows),
        "source_checksum": _checksum(path),
        "reference_layers": _reference_layers(root),
        "built_at": utc_now_iso(),
    }


def _reference_layers(root: Path) -> dict[str, Any]:
    layers: dict[str, Any] = {}
    for relative in (
        "analysis/subject-cards/registry.csv",
        "analysis/functional-gaps/index.csv",
        "analysis/custom-metadata/index.csv",
        "analysis/indexes/final-diff-inventory.csv",
        "analysis/indexes/final-feature-map.csv",
        "analysis/tz-rework-registry/classification.csv",
    ):
        layers[relative] = {"exists": repo_path(root, relative).exists(), "rows": len(read_csv_rows(repo_path(root, relative)))}
    for relative in ("analysis/cache/noise/clean-rebase-v8unpack/repo", "analysis/features"):
        path = repo_path(root, relative)
        layers[relative] = {"exists": path.exists(), "files": sum(1 for item in path.rglob("*") if item.is_file()) if path.exists() else 0}
    return layers


def _subject_index(root: Path) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    object_to_slug: dict[str, str] = {}
    slug_to_title: dict[str, str] = {}
    title_to_slug: dict[str, str] = {}
    for row in read_csv_rows(repo_path(root, "analysis/subject-cards/registry.csv")):
        slug = (row.get("slug") or "").strip()
        if not slug:
            continue
        title = (row.get("title") or slug).strip()
        slug_to_title[slug] = title
        title_to_slug[title] = slug
        for obj in split_refs(row.get("primary_objects")):
            object_to_slug.setdefault(obj, slug)
    return object_to_slug, slug_to_title, title_to_slug


def _subject_card_paths(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in read_csv_rows(repo_path(root, "analysis/subject-cards/registry.csv")):
        slug = (row.get("slug") or "").strip()
        card_path = (row.get("card_path") or "").strip()
        if slug and card_path:
            result[slug] = card_path
    return result


def _subject_card_ref(expected_card: str, card_paths: dict[str, str]) -> str:
    if expected_card.startswith("missing:"):
        return f"analysis/subject-cards/registry.csv#missing={expected_card}"
    return card_paths.get(expected_card, f"analysis/subject-cards/registry.csv#slug={expected_card}")


def _ensure_subject_card_ref(row: dict[str, str], card_paths: dict[str, str]) -> dict[str, str]:
    expected_card = (row.get("expected_card") or "").strip()
    if not expected_card:
        return row
    row["evidence_refs"] = join_refs([*split_refs(row.get("evidence_refs")), _subject_card_ref(expected_card, card_paths)])
    return row


def _load_existing_markup(root: Path) -> dict[str, dict[str, str]]:
    result = {row.get("row_id", ""): row for row in read_csv_rows(repo_path(root, MARKUP_CSV)) if row.get("row_id")}
    work_dir = repo_path(root, WORK_DIR)
    for path in sorted(work_dir.glob("markup-manual-batch-*.csv")):
        for row in read_csv_rows(path):
            row_id = row.get("row_id", "")
            if not row_id or row_id in result:
                continue
            result[row_id] = _normalize_legacy_manual_batch_row(row)
    return result


def _normalize_legacy_manual_batch_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "row_id": row.get("row_id", ""),
        "source_checksum": row.get("source_sha256", ""),
        "source": row.get("source", ""),
        "source_title": row.get("title", ""),
        "source_type": row.get("customization_type", ""),
        "key_objects": row.get("key_objects", ""),
        "current_business_area": row.get("current_business_area", ""),
        "current_business_meaning": row.get("current_business_meaning", ""),
        "current_card": row.get("current_card", ""),
        "current_card_title": row.get("current_card_ru", ""),
        "reconstructed_business_area": row.get("reconstructed_business_area", ""),
        "reconstructed_business_meaning": row.get("reconstructed_business_meaning", ""),
        "expected_card": row.get("expected_card", ""),
        "expected_card_title": row.get("expected_card_ru", ""),
        "review_method": row.get("review_method", ""),
        "review_method_label": row.get("review_method_ru", ""),
        "review_status": row.get("review_status", ""),
        "review_status_label": row.get("review_status_ru", ""),
        "confidence": row.get("confidence", ""),
        "confidence_label": row.get("confidence_ru", ""),
        "reason": row.get("reason", ""),
        "evidence_refs": row.get("evidence_refs", ""),
        "reviewer_notes": row.get("reviewer_notes", ""),
    }


def _guess_card(row: dict[str, str], object_to_slug: dict[str, str], slug_to_title: dict[str, str]) -> tuple[str, str]:
    for obj in split_refs(row.get("key_objects")):
        slug = object_to_slug.get(obj)
        if slug:
            return slug, slug_to_title.get(slug, slug)
    return "", ""


def _markup_row(source_row: dict[str, str], checksum: str, existing: dict[str, str], object_to_slug: dict[str, str], slug_to_title: dict[str, str]) -> dict[str, str]:
    row_id = source_row.get("row_id", "")
    expected_card, expected_title = _guess_card(source_row, object_to_slug, slug_to_title)
    base = {
        "row_id": row_id,
        "source_checksum": checksum,
        "source": source_row.get("source", ""),
        "source_title": source_row.get("title", ""),
        "source_type": source_row.get("customization_type") or source_row.get("source_type", ""),
        "key_objects": source_row.get("key_objects", ""),
        "current_business_area": source_row.get("business_area", ""),
        "current_business_meaning": source_row.get("business_meaning", ""),
        "current_card": "",
        "current_card_title": "",
        "reconstructed_business_area": source_row.get("business_area", ""),
        "reconstructed_business_meaning": source_row.get("business_meaning", ""),
        "expected_card": expected_card,
        "expected_card_title": expected_title,
        "review_method": "draft_heuristic",
        "review_method_label": REVIEW_METHOD_LABELS["draft_heuristic"],
        "review_status": "weak_heuristic",
        "review_status_label": REVIEW_STATUS_LABELS["weak_heuristic"],
        "confidence": "low",
        "confidence_label": CONFIDENCE_LABELS["low"],
        "reason": "Черновое заполнение из текущего детального реестра; требуется ручная обратная проверка смысла доработки и карточки.",
        "evidence_refs": f"{DETAILED_REGISTER_CSV}#row_id={row_id}",
        "reviewer_notes": "",
    }
    if not existing:
        return base
    merged = {**base, **{key: value for key, value in existing.items() if value}}
    merged["source_checksum"] = checksum
    for key, labels in (("review_method", REVIEW_METHOD_LABELS), ("review_status", REVIEW_STATUS_LABELS), ("confidence", CONFIDENCE_LABELS)):
        label_key = f"{key}_label"
        if merged.get(key) in labels:
            merged[label_key] = labels[merged[key]]
    return merged


def _write_manifest(root: Path, manifest: dict[str, Any]) -> None:
    path = repo_path(root, SOURCE_MANIFEST)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_reverse_review(root: Path) -> dict[str, Any]:
    source_rows = read_detailed_register_rows(_source_path(root))
    if not source_rows:
        return {"status": "fail", "errors": [f"Missing or empty source: {DETAILED_REGISTER_CSV}"], "rows": 0}
    manifest = _source_manifest(root, source_rows)
    object_to_slug, slug_to_title, _title_to_slug = _subject_index(root)
    card_paths = _subject_card_paths(root)
    existing = _load_existing_markup(root)
    rows = [
        _ensure_subject_card_ref(
            _markup_row(row, manifest["source_checksum"], existing.get(row.get("row_id", ""), {}), object_to_slug, slug_to_title),
            card_paths,
        )
        for row in source_rows
    ]
    write_csv_rows(repo_path(root, MARKUP_CSV), MARKUP_HEADER, rows)
    _write_manifest(root, manifest)
    _write_reports(root, rows, slug_to_title)
    return {"status": "ok", "rows": len(rows), "markup": MARKUP_CSV, "summary": SUMMARY_MD}


def _write_reports(root: Path, rows: list[dict[str, str]], slug_to_title: dict[str, str]) -> None:
    conflicts: list[dict[str, str]] = []
    questions: list[dict[str, str]] = []
    for row in rows:
        status = row.get("review_status", "")
        expected_card = row.get("expected_card", "")
        if status in {"card_mismatch", "duplicate_or_merged", "technical_supporting"} or expected_card.startswith("missing:"):
            conflict_type = "missing_card" if expected_card.startswith("missing:") else status
            conflicts.append(
                {
                    "row_id": row.get("row_id", ""),
                    "conflict_type": conflict_type,
                    "conflict_type_label": CONFLICT_LABELS.get(conflict_type, conflict_type),
                    "current_binding": row.get("current_card", ""),
                    "expected_binding": expected_card,
                    "reason": row.get("reason", ""),
                    "recommended_action": "Уточнить или создать карточку доработки" if expected_card.startswith("missing:") else "Сверить строку с карточкой",
                }
            )
        if status in OPEN_STATUSES:
            questions.append(
                {
                    "row_id": row.get("row_id", ""),
                    "expected_card": expected_card,
                    "expected_card_title": row.get("expected_card_title", ""),
                    "review_status": status,
                    "confidence": row.get("confidence", ""),
                    "question": row.get("reason", ""),
                    "needed_evidence": "Доказательство дельты заказчика и привязки к карточке доработки",
                    "evidence_refs": row.get("evidence_refs", ""),
                }
            )
    card_rows = _card_rows(rows, slug_to_title)
    write_csv_rows(repo_path(root, CONFLICTS_CSV), CONFLICTS_HEADER, conflicts)
    write_csv_rows(repo_path(root, CARD_CROSS_REVIEW_CSV), CARD_CROSS_REVIEW_HEADER, card_rows)
    write_csv_rows(repo_path(root, OPEN_QUESTIONS_CSV), OPEN_QUESTIONS_HEADER, questions)
    repo_path(root, OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    repo_path(root, SUMMARY_MD).write_text(_summary(rows, conflicts, questions), encoding="utf-8", newline="\n")


def _card_rows(rows: list[dict[str, str]], slug_to_title: dict[str, str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    cards = sorted({row.get("expected_card", "") for row in rows if row.get("expected_card")})
    for card in cards:
        related = [row for row in rows if row.get("expected_card") == card]
        counts = Counter(row.get("review_status", "") for row in related)
        weak = sum(counts.get(status, 0) for status in OPEN_STATUSES)
        conflict = sum(counts.get(status, 0) for status in ("card_mismatch", "duplicate_or_merged")) + (len(related) if card.startswith("missing:") else 0)
        result.append(
            {
                "card_slug": card,
                "card_title": slug_to_title.get(card, related[0].get("expected_card_title", "")),
                "confirmed_count": str(counts.get("confirmed", 0)),
                "corrected_count": str(counts.get("corrected", 0)),
                "weak_count": str(weak),
                "conflict_count": str(conflict),
                "unlinked_count": "0",
                "coverage_status": "нужна карточка" if card.startswith("missing:") else ("требует проверки" if weak or conflict else "подтверждено"),
            }
        )
    if not result:
        result.append({"card_slug": "", "card_title": "Без привязки", "confirmed_count": "0", "corrected_count": "0", "weak_count": "0", "conflict_count": "0", "unlinked_count": str(len(rows)), "coverage_status": "нет привязок"})
    return result


def _summary(rows: list[dict[str, str]], conflicts: list[dict[str, str]], questions: list[dict[str, str]]) -> str:
    statuses = Counter(row.get("review_status", "") for row in rows)
    confidence = Counter(row.get("confidence", "") for row in rows)
    methods = Counter(row.get("review_method", "") for row in rows)
    lines = [
        "# Обратная проверка детального реестра",
        "",
        f"Сформировано: {utc_now_iso()}.",
        f"Строк разметки: {len(rows)}.",
        f"Конфликтов: {len(conflicts)}.",
        f"Открытых вопросов: {len(questions)}.",
        "",
        "## Статусы",
        "",
    ]
    for status, count in sorted(statuses.items()):
        lines.append(f"- {REVIEW_STATUS_LABELS.get(status, status)}: {count}")
    lines.extend(["", "## Уверенность", ""])
    for item, count in sorted(confidence.items()):
        lines.append(f"- {CONFIDENCE_LABELS.get(item, item)}: {count}")
    lines.extend(["", "## Способ разметки", ""])
    for item, count in sorted(methods.items()):
        lines.append(f"- {REVIEW_METHOD_LABELS.get(item, item)}: {count}")
    lines.extend(["", "Подтвержденный ручной результат отделен от слабых эвристик через статус и способ разметки.", ""])
    return "\n".join(lines)


def _read_markup(root: Path) -> tuple[list[str], list[dict[str, str]]]:
    path = repo_path(root, MARKUP_CSV)
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames or [], [dict(row) for row in reader]


def validate_reverse_review(root: Path, mode: str = "draft") -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if mode not in {"draft", "final"}:
        return {"status": "fail", "errors": [f"Unknown mode: {mode}"], "warnings": warnings, "rows": 0}
    source_rows = read_detailed_register_rows(_source_path(root))
    if not source_rows:
        return {"status": "fail", "errors": [f"Missing or empty source: {DETAILED_REGISTER_CSV}"], "warnings": warnings, "rows": 0}
    header, rows = _read_markup(root)
    if not rows:
        return {"status": "fail", "errors": [f"Missing required path: {MARKUP_CSV}"], "warnings": warnings, "rows": 0}
    expected_header = MARKUP_HEADER.split(",")
    if header != expected_header:
        errors.append(f"{MARKUP_CSV} header mismatch: expected {expected_header}, got {header}")
    _validate_manifest(root, source_rows, mode, errors, warnings)
    source_ids = [row.get("row_id", "") for row in source_rows]
    markup_ids = [row.get("row_id", "") for row in rows]
    _validate_coverage(source_ids, markup_ids, errors)
    _validate_rows(root, rows, mode, errors)
    for relative in (CONFLICTS_CSV, CARD_CROSS_REVIEW_CSV, OPEN_QUESTIONS_CSV, SUMMARY_MD):
        if not repo_path(root, relative).exists():
            errors.append(f"Missing required path: {relative}")
    return {"status": "fail" if errors else "ok", "errors": errors, "warnings": warnings, "rows": len(rows)}


def _validate_manifest(root: Path, source_rows: list[dict[str, str]], mode: str, errors: list[str], warnings: list[str]) -> None:
    path = repo_path(root, SOURCE_MANIFEST)
    if not path.exists():
        errors.append(f"Missing required path: {SOURCE_MANIFEST}")
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    current = _source_manifest(root, source_rows)
    for key in ("source_path", "source_rows", "source_checksum"):
        if manifest.get(key) != current.get(key):
            message = f"{SOURCE_MANIFEST} differs from current source {key}: expected {current.get(key)!r}, got {manifest.get(key)!r}"
            if mode == "final":
                errors.append(message)
            else:
                warnings.append(message)


def _validate_coverage(source_ids: list[str], markup_ids: list[str], errors: list[str]) -> None:
    source_counter = Counter(source_ids)
    markup_counter = Counter(markup_ids)
    for row_id, count in source_counter.items():
        if count != 1:
            errors.append(f"{DETAILED_REGISTER_CSV} has duplicate row_id: {row_id}")
    for row_id, count in markup_counter.items():
        if count != 1:
            errors.append(f"{MARKUP_CSV} has duplicate row_id: {row_id}")
    missing = sorted(set(source_counter) - set(markup_counter))
    extra = sorted(set(markup_counter) - set(source_counter))
    if missing:
        errors.append(f"{MARKUP_CSV} misses source row_id: {', '.join(missing[:10])}")
    if extra:
        errors.append(f"{MARKUP_CSV} has extra row_id: {', '.join(extra[:10])}")


def _validate_rows(root: Path, rows: list[dict[str, str]], mode: str, errors: list[str]) -> None:
    _object_to_slug, slug_to_title, _title_to_slug = _subject_index(root)
    valid_cards = set(slug_to_title)
    card_paths = _subject_card_paths(root)
    for row in rows:
        row_id = row.get("row_id", "?")
        method = row.get("review_method", "")
        status = row.get("review_status", "")
        confidence = row.get("confidence", "")
        expected_card = row.get("expected_card", "")
        if method not in REVIEW_METHOD_LABELS:
            errors.append(f"{MARKUP_CSV} row {row_id} has unknown review_method: {method}")
        if status not in REVIEW_STATUS_LABELS:
            errors.append(f"{MARKUP_CSV} row {row_id} has unknown review_status: {status}")
        if confidence not in CONFIDENCE_LABELS:
            errors.append(f"{MARKUP_CSV} row {row_id} has unknown confidence: {confidence}")
        if method == "draft_heuristic" and status == "confirmed":
            errors.append(f"{MARKUP_CSV} row {row_id} draft_heuristic cannot be confirmed")
        if method in {"manual", "llm_assisted", "cross_review"} and not expected_card:
            errors.append(f"{MARKUP_CSV} row {row_id} must fill expected_card during manual reverse review")
        if expected_card and expected_card not in valid_cards and not expected_card.startswith("missing:"):
            errors.append(f"{MARKUP_CSV} row {row_id} expected_card is not a known card or missing:<slug>: {expected_card}")
        if expected_card and _subject_card_ref(expected_card, card_paths) not in split_refs(row.get("evidence_refs")):
            errors.append(f"{MARKUP_CSV} row {row_id} evidence_refs must include subject card file reference for {expected_card}")
        for field in ("reconstructed_business_area", "reconstructed_business_meaning", "reason"):
            value = row.get(field, "")
            if not value.strip():
                errors.append(f"{MARKUP_CSV} row {row_id} has empty field: {field}")
            elif field.startswith("reconstructed") and DETAIL_FORBIDDEN_CUSTOMER_MARKERS.search(value):
                errors.append(f"{MARKUP_CSV} row {row_id} exposes technical marker in {field}: {value}")
        if status in STRONG_STATUSES and not row.get("evidence_refs", "").strip():
            errors.append(f"{MARKUP_CSV} row {row_id} status {status} requires evidence_refs")
        if status in STRONG_STATUSES and _looks_like_form_row(row) and "analysis/cache/noise/clean-rebase-v8unpack/repo" not in row.get("evidence_refs", ""):
            errors.append(f"{MARKUP_CSV} row {row_id} strong form-related status requires v8unpack evidence")
        if status in STRONG_STATUSES and row.get("reconstructed_business_meaning", "").strip() == row.get("current_business_meaning", "").strip():
            errors.append(f"{MARKUP_CSV} row {row_id} strong status repeats current generated business meaning")
        if mode == "final":
            if method == "draft_heuristic":
                errors.append(f"{MARKUP_CSV} row {row_id} remains draft_heuristic in final mode")
            if status in FINAL_TEMPORARY_STATUSES:
                errors.append(f"{MARKUP_CSV} row {row_id} remains temporary status in final mode: {status}")


def _looks_like_form_row(row: dict[str, str]) -> bool:
    text = " ".join(row.get(key, "") for key in ("source_title", "source_type", "current_business_meaning", "reconstructed_business_meaning", "reason")).lower()
    return any(marker in text for marker in ("форма", "формы", "командн", "клиентск", "элемент формы"))


def build_command(args: argparse.Namespace) -> int:
    print(json.dumps(build_reverse_review(_root(args)), ensure_ascii=False, indent=2))
    return 0


def validate_command(args: argparse.Namespace) -> int:
    result = validate_reverse_review(_root(args), getattr(args, "mode", "draft"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1
