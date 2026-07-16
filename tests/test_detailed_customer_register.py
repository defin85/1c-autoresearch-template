from __future__ import annotations

import csv
import json
from pathlib import Path

from one_c_autoresearch.detailed_customer_register import (
    DETAILED_REGISTER_COLUMN_LABELS,
    DETAILED_REGISTER_CSV,
    DETAILED_REGISTER_HEADER,
    DETAILED_REGISTER_MD,
    DETAILED_REGISTER_OUTPUT_HEADER,
    build_detailed_customer_register,
    read_detailed_register_rows,
    validate_detailed_customer_register,
    _write_xlsx_book,
)
from one_c_autoresearch.common import read_jsonl
from one_c_autoresearch.customization_registry import EVIDENCE_JSONL, ITEMS_JSONL, LINKS_JSONL


def write_workbook(path: Path, rows: list[tuple[str, str, str]]) -> None:
    _write_xlsx_book(path, {"Лист1": [["№ п/п", "Наименование", "Тип"], *[[number, title, kind] for number, title, kind in rows]]})


def write_common_inputs(root: Path) -> None:
    (root / "analysis/tz-rework-registry").mkdir(parents=True)
    (root / "analysis/tz-rework-registry/classification.csv").write_text(
        "registry_row_id,source_row_number,registry_number,source_name,classification,status,confidence,external_kind,external_code,matched_name,source_image,match_basis,configuration_objects,evidence_refs,notes\n"
        "TZR-0001,2,1,Внешний отчет,external_tool,classified,high,Отчет,EXT-1,Внешний отчет,,exact_name,,analysis/source.csv,ok\n"
        "TZR-0002,3,2,Смешанная строка,mixed,needs_review,medium,Печатная форма,,Смешанная строка,,name,Document.Демо,analysis/source.csv,needs\n",
        encoding="utf-8",
    )
    (root / "analysis/tz-rework-registry/open-questions.csv").write_text(
        "registry_row_id,registry_number,source_name,classification,status,reason,needed_evidence,evidence_refs\n"
        "TZR-0002,2,Смешанная строка,mixed,needs_review,Нужен код,код элемента,analysis/source.csv\n",
        encoding="utf-8",
    )
    (root / "analysis/custom-metadata").mkdir(parents=True)
    (root / "analysis/custom-metadata/object-summary.json").write_text(
        json.dumps(
            {
                "objects": [
                    {"metadata_full_name": "Catalog.Неизмененный", "counts": {"unchanged": 2}},
                    {"metadata_full_name": "Catalog.НоваяНастройка", "counts": {"added": 1, "unchanged": 1}},
                    {"metadata_full_name": "Document.Демо", "counts": {"modified": 1}},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "analysis/custom-metadata/index.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"metadata_full_name": "Catalog.Неизмененный", "status": "unchanged", "change_type": "unchanged"}, ensure_ascii=False),
                json.dumps(
                    {
                        "metadata_full_name": "Catalog.НоваяНастройка",
                        "status": "non_typical",
                        "change_type": "added",
                        "reconciliation": {"links": {"subject_card_slugs": ["demo"]}},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "metadata_full_name": "Document.Демо",
                        "status": "non_typical",
                        "change_type": "modified",
                        "reconciliation": {"links": {"subject_card_slugs": ["demo"]}},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "analysis/subject-cards").mkdir(parents=True)
    (root / "analysis/subject-cards/registry.csv").write_text(
        "slug,title,subject_type,status,confidence,origin_layer,owner_feature,linked_features,linked_detail_maps,primary_objects,coverage_scope,why_separate_card,merge_into,split_from,card_path,evidence_count,gap_count,review_notes\n"
        "demo,Демо,business_process,ready_for_review,high,manual,BF-X,BF-X,,Catalog.НоваяНастройка;Document.Демо,Сводка,,,,analysis/subject-cards/cards/demo/subject-card.json,0,0,\n",
        encoding="utf-8",
    )
    (root / "analysis/functional-gaps").mkdir(parents=True)
    (root / "analysis/functional-gaps/index.csv").write_text(
        "subject_card_slug,title,status,gap_readiness,hypotheses_count,open_checks_count,gap_card_path,selected_decision,review_notes\n"
        "demo,Демо,ready_for_review,needs_target_release_check,1,0,analysis/functional-gaps/cards/demo/gap-card.json,adapt,\n",
        encoding="utf-8",
    )
    (root / ITEMS_JSONL).parent.mkdir(parents=True, exist_ok=True)
    (root / ITEMS_JSONL).write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema_version": "customization-registry/v1",
                        "customization_id": "CUS-CFG",
                        "title": "Доработка Catalog.НоваяНастройка",
                        "source_bp20_objects": ["Catalog.НоваяНастройка"],
                        "source_kinds": ["custom_metadata"],
                        "change_kind": "metadata",
                        "business_area": "НСИ",
                        "business_meaning": "НСИ",
                        "status": "ready_for_review",
                        "confidence": "medium",
                        "bp30_coverage_status": "unknown",
                        "migration_decision": "needs_customer_decision",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "schema_version": "customization-registry/v1",
                        "customization_id": "CUS-EXT",
                        "title": "Внешний отчет",
                        "source_bp20_objects": [],
                        "source_kinds": ["external_processing"],
                        "change_kind": "external_processing",
                        "business_area": "Отчетность",
                        "business_meaning": "Отчет",
                        "status": "needs_source",
                        "confidence": "low",
                        "bp30_coverage_status": "needs_customer_decision",
                        "migration_decision": "needs_customer_decision",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / EVIDENCE_JSONL).write_text(
        "\n".join(
            [
                json.dumps({"schema_version": "customization-registry/v1", "customization_id": "CUS-CFG", "evidence_type": "custom_metadata"}, ensure_ascii=False),
                json.dumps({"schema_version": "customization-registry/v1", "customization_id": "CUS-EXT", "evidence_type": "external_processing", "external_id": "EXT-1"}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / LINKS_JSONL).write_text(
        json.dumps({"schema_version": "customization-registry/v1", "customization_id": "CUS-EXT", "target_type": "external_processing", "target_id": "EXT-1", "relation": "primary_source"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def read_output_rows(root: Path) -> list[dict[str, str]]:
    return read_detailed_register_rows(root / DETAILED_REGISTER_CSV)


def test_detailed_register_builds_xlsx_and_configuration_rows(tmp_path: Path) -> None:
    workbook = tmp_path / "source.xlsx"
    write_workbook(workbook, [("1", "Внешний отчет", "Внешний отчет"), ("2", "Смешанная строка", "Внешняя печатная форма + доработка конфигурации")])
    write_common_inputs(tmp_path)

    result = build_detailed_customer_register(tmp_path, workbook)
    rows = read_output_rows(tmp_path)

    assert result["status"] == "ok"
    with (tmp_path / DETAILED_REGISTER_CSV).open(encoding="utf-8-sig", newline="") as fh:
        assert next(csv.reader(fh)) == DETAILED_REGISTER_OUTPUT_HEADER
    assert {row["source"] for row in rows} == {"Реестр ТЗ", "Перечень метаданных"}
    assert all("EXT-1" not in row["external_code"] for row in rows)
    trace = [row for _, row in read_jsonl(tmp_path / "outputs/detailed-customer-customization-register.trace.jsonl")]
    assert any(row["row_id"] == "DCCR-TZR-0001" and row["customization_ids"] == ["CUS-EXT"] for row in trace)
    assert any(row["source_objects"] == ["Catalog.НоваяНастройка"] and row["customization_ids"] == ["CUS-CFG"] for row in trace)
    assert "Справочник.НоваяНастройка" in {row["key_objects"] for row in rows}
    assert "Справочник.Неизмененный" not in {row["key_objects"] for row in rows}
    assert "Документ.Демо" not in {row["key_objects"] for row in rows if row["source"] == "Перечень метаданных"}
    assert any(row["open_question"] == "код элемента" for row in rows)
    assert validate_detailed_customer_register(tmp_path, workbook)["status"] == "ok"


def test_detailed_register_public_artifacts_use_russian_headers_and_do_not_truncate(tmp_path: Path) -> None:
    workbook = tmp_path / "source.xlsx"
    long_title = "Очень длинная внешняя печатная форма для проверки полного текста без сокращения в публичных артефактах"
    write_workbook(workbook, [("1", long_title, "Внешний отчет")])
    write_common_inputs(tmp_path)

    build_detailed_customer_register(tmp_path, workbook)

    text = (tmp_path / DETAILED_REGISTER_MD).read_text(encoding="utf-8")
    assert "Бизнес-область" in text
    assert "Решение по переходу" in text
    assert "…" not in text
    with (tmp_path / DETAILED_REGISTER_CSV).open(encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh))
        data = next(csv.DictReader(fh, fieldnames=header))
    assert header == DETAILED_REGISTER_OUTPUT_HEADER
    assert data["Наименование"] == long_title
    assert "…" not in json.dumps(data, ensure_ascii=False)


def test_detailed_register_validation_detects_stale_source_type(tmp_path: Path) -> None:
    workbook = tmp_path / "source.xlsx"
    write_workbook(workbook, [("1", "Внешний отчет", "Внешний отчет")])
    write_common_inputs(tmp_path)
    (tmp_path / "analysis/tz-rework-registry/input.csv").write_text(
        "registry_row_id,source_row_number,registry_number,source_name,source_type,source_path\n"
        f"TZR-0001,2,1,Внешний отчет,,{workbook}\n",
        encoding="utf-8",
    )

    result = validate_detailed_customer_register(tmp_path, workbook)

    assert result["status"] == "fail"
    assert any("source_type differs" in error for error in result["errors"])


def test_detailed_register_validation_blocks_technical_markers(tmp_path: Path) -> None:
    workbook = tmp_path / "source.xlsx"
    write_workbook(workbook, [("1", "Внешний отчет", "Внешний отчет")])
    write_common_inputs(tmp_path)
    build_detailed_customer_register(tmp_path, workbook)
    rows = read_output_rows(tmp_path)
    rows[0]["title"] = "CMI-00001"
    from one_c_autoresearch.subject_cards import write_csv_rows

    write_csv_rows(
        tmp_path / DETAILED_REGISTER_CSV,
        ",".join(DETAILED_REGISTER_OUTPUT_HEADER),
        [{DETAILED_REGISTER_COLUMN_LABELS[column]: row.get(column, "") for column in DETAILED_REGISTER_HEADER.split(",")} for row in rows],
    )

    result = validate_detailed_customer_register(tmp_path, workbook)

    assert result["status"] == "fail"
    assert any("technical marker" in error for error in result["errors"])
