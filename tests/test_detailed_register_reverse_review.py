from __future__ import annotations

import csv
import json
from pathlib import Path

from one_c_autoresearch.detailed_customer_register import DETAILED_REGISTER_HEADER
from one_c_autoresearch.detailed_register_reverse_review import (
    MARKUP_CSV,
    build_reverse_review,
    validate_reverse_review,
)
from one_c_autoresearch.subject_cards import write_csv_rows


def write_inputs(root: Path) -> None:
    rows = [
        {
            "row_id": "DCCR-001",
            "number": "1",
            "source": "Перечень метаданных",
            "source_registry_number": "",
            "title": "Документ демо",
            "source_type": "Доработка конфигурации",
            "customization_type": "Доработка конфигурации",
            "detailed_class": "Доработка типового объекта",
            "external_kind": "",
            "external_code": "",
            "matched_name": "",
            "key_objects": "Document.Демо",
            "business_area": "Документы и операции",
            "business_meaning": "Текущий смысл",
            "transition_decision": "Адаптировать",
            "target_release_coverage": "Нужна проверка",
            "risk": "средний",
            "verification_status": "Статика",
            "open_question": "",
            "recommendation": "Проверить",
        }
    ]
    write_csv_rows(root / "outputs/detailed-customer-customization-register.csv", DETAILED_REGISTER_HEADER, rows)
    (root / "analysis/subject-cards").mkdir(parents=True)
    (root / "analysis/subject-cards/registry.csv").write_text(
        "slug,title,subject_type,status,confidence,origin_layer,owner_feature,linked_features,linked_detail_maps,primary_objects,coverage_scope,why_separate_card,merge_into,split_from,card_path,evidence_count,gap_count,review_notes\n"
        "demo-card,Демо,business_process,ready_for_review,high,manual,BF-X,BF-X,,Document.Демо,Сводка,,,,analysis/subject-cards/cards/demo-card/subject-card.json,0,0,\n",
        encoding="utf-8",
    )
    for relative in (
        "analysis/functional-gaps/index.csv",
        "analysis/custom-metadata/index.csv",
        "analysis/indexes/final-diff-inventory.csv",
        "analysis/indexes/final-feature-map.csv",
        "analysis/tz-rework-registry/classification.csv",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("id,value\n", encoding="utf-8")
    (root / "analysis/cache/noise/clean-rebase-v8unpack/repo").mkdir(parents=True)


def read_markup(root: Path) -> list[dict[str, str]]:
    with (root / MARKUP_CSV).open(encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def test_reverse_review_builds_draft_markup_and_reports(tmp_path: Path) -> None:
    write_inputs(tmp_path)

    result = build_reverse_review(tmp_path)
    rows = read_markup(tmp_path)

    assert result["status"] == "ok"
    assert result["rows"] == 1
    assert rows[0]["row_id"] == "DCCR-001"
    assert rows[0]["current_business_meaning"] == "Текущий смысл"
    assert rows[0]["expected_card"] == "demo-card"
    assert rows[0]["review_method"] == "draft_heuristic"
    assert rows[0]["review_status"] == "weak_heuristic"
    assert (tmp_path / "analysis/detailed-register-reverse-review/source-manifest.json").exists()
    assert (tmp_path / "analysis/detailed-register-reverse-review/card-cross-review.csv").exists()
    assert (tmp_path / "analysis/detailed-register-reverse-review/open-questions.csv").exists()
    assert (tmp_path / "analysis/detailed-register-reverse-review/summary.md").exists()
    assert validate_reverse_review(tmp_path, mode="draft")["status"] == "ok"
    assert validate_reverse_review(tmp_path, mode="final")["status"] == "fail"


def test_reverse_review_requires_expected_card_for_manual_rows(tmp_path: Path) -> None:
    write_inputs(tmp_path)
    build_reverse_review(tmp_path)
    rows = read_markup(tmp_path)
    rows[0].update(
        {
            "review_method": "manual",
            "review_status": "corrected",
            "confidence": "high",
            "reconstructed_business_area": "Документы",
            "reconstructed_business_meaning": "Доработка заказчика по документу",
            "expected_card": "",
            "reason": "Проверено по исходникам",
            "evidence_refs": "analysis/source.md#L1",
        }
    )
    write_csv_rows(tmp_path / MARKUP_CSV, ",".join(rows[0].keys()), rows)

    result = validate_reverse_review(tmp_path, mode="draft")

    assert result["status"] == "fail"
    assert any("expected_card" in error for error in result["errors"])


def test_reverse_review_accepts_missing_card_marker_for_future_card(tmp_path: Path) -> None:
    write_inputs(tmp_path)
    build_reverse_review(tmp_path)
    rows = read_markup(tmp_path)
    rows[0].update(
        {
            "review_method": "manual",
            "review_status": "needs_evidence",
            "confidence": "medium",
            "reconstructed_business_area": "Документы",
            "reconstructed_business_meaning": "Доработка заказчика по документу",
            "expected_card": "missing:budushchaya-kartochka",
            "reason": "Нужна отдельная карточка доработки",
            "evidence_refs": "analysis/subject-cards/registry.csv#missing=missing:budushchaya-kartochka",
        }
    )
    write_csv_rows(tmp_path / MARKUP_CSV, ",".join(rows[0].keys()), rows)

    result = validate_reverse_review(tmp_path, mode="draft")

    assert result["status"] == "ok", json.dumps(result, ensure_ascii=False)
