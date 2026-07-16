import csv
import json
import zipfile
import xml.etree.ElementTree as ET

from one_c_autoresearch.common import read_jsonl
from one_c_autoresearch.customization_registry import EVIDENCE_JSONL, ITEMS_JSONL, LINKS_JSONL
from one_c_autoresearch.customer_register import FORBIDDEN_CUSTOMER_MARKERS, build_customer_register, validate_customer_register, _join_objects


def test_customer_register_forbids_internal_markers() -> None:
    forbidden = [
        "FGF-0002",
        "FGM-0010",
        "analysis/functional-gaps/cards/demo",
        "DataProcessor.id",
        "DataProcessor.json",
        "object.id.json",
    ]
    for value in forbidden:
        assert FORBIDDEN_CUSTOMER_MARKERS.search(value)


def test_customer_register_filters_technical_object_traces() -> None:
    assert _join_objects(["DataProcessor.id", "Catalog.Контрагенты", "analysis/cache/demo.json"]) == "Справочник.Контрагенты"


def test_customer_register_uses_review_and_target_findings(tmp_path) -> None:
    (tmp_path / "analysis/subject-cards/cards/demo").mkdir(parents=True)
    (tmp_path / "analysis/functional-gaps/cards/demo").mkdir(parents=True)
    (tmp_path / "analysis/subject-cards/registry.csv").write_text(
        "slug,title,subject_type,status,confidence,origin_layer,owner_feature,linked_features,linked_detail_maps,primary_objects,coverage_scope,why_separate_card,merge_into,split_from,card_path,evidence_count,gap_count,review_notes\n"
        "demo,Демо,business_process,ready_for_review,high,manual,BF-X,BF-X,,Catalog.Демо,Сводка,,,,analysis/subject-cards/cards/demo/subject-card.json,0,0,\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis/subject-cards/cards/demo/subject-card.json").write_text(
        json.dumps({"title": "Демо", "summary": "Сводка", "subject_type": "business_process"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/gap-card.json").write_text(
        json.dumps({"selected_decision": "adapt"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/object-mapping.csv").write_text(
        "mapping_id,source_object,target_object,mapping_type,object_role,coverage_status\n"
        "FGM-0001,Catalog.Демо,,shared_infrastructure,target_candidate_object,partially_covered\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/target-findings.csv").write_text(
        "finding_id,finding_type,target_object,target_path,match_basis,confidence,evidence_ref,object_role,functional_relevance,notes\n"
        "FGF-0001,object,Catalog.Демо,,semantic,medium,,target_candidate_object,partial,Есть кандидат\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/checks.csv").write_text(
        "check_id,check_type,status,source,question,result,blocking\n"
        "FGC-0001,static,done,review,question,result,false\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis/functional-gaps/cards/demo/review.md").write_text(
        "## Вывод\n\nОстаточный разрыв из ревью.\n",
        encoding="utf-8",
    )
    (tmp_path / ITEMS_JSONL).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / ITEMS_JSONL).write_text(
        json.dumps(
            {
                "schema_version": "customization-registry/v1",
                "customization_id": "CUS-TEST",
                "title": "Семантическая доработка",
                "source_bp20_objects": ["Catalog.Демо"],
                "source_kinds": ["custom_metadata"],
                "change_kind": "metadata",
                "business_area": "Демо",
                "business_meaning": "Демо",
                "status": "ready_for_review",
                "confidence": "medium",
                "bp30_coverage_status": "unknown",
                "migration_decision": "needs_customer_decision",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / EVIDENCE_JSONL).write_text(
        json.dumps({"schema_version": "customization-registry/v1", "customization_id": "CUS-TEST", "evidence_type": "custom_metadata"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / LINKS_JSONL).write_text(
        json.dumps({"schema_version": "customization-registry/v1", "customization_id": "CUS-TEST", "target_type": "subject_card", "target_id": "demo", "relation": "primary_contour"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    build_customer_register(tmp_path)

    with (tmp_path / "outputs/customer-customization-register.csv").open(encoding="utf-8-sig", newline="") as fh:
        row = next(csv.DictReader(fh))
    assert "проверенных механизмов: 1" in row["Покрытие целевого релиза"]
    assert "CUS-TEST" not in json.dumps(row, ensure_ascii=False)
    trace = [row for _, row in read_jsonl(tmp_path / "outputs/customer-customization-register.trace.jsonl")]
    assert trace[0]["customization_ids"] == ["CUS-TEST"]
    assert row["Остаточный разрыв"] == "Остаточный разрыв из ревью."
    assert "…" not in json.dumps(row, ensure_ascii=False)
    markdown = (tmp_path / "outputs/customer-customization-register.md").read_text(encoding="utf-8")
    assert "Статус проверки" in markdown
    assert (
        "| № | Код | Доработка | Бизнес-область | Что менялось | Ключевые объекты | Решение по переходу | "
        "Покрытие целевого релиза | Остаточный разрыв | Риск | Статус проверки | Рекомендация |"
    ) in markdown
    with zipfile.ZipFile(tmp_path / "outputs/customer-customization-register.xlsx") as zf:
        namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        sheet = workbook.find(".//main:sheet", namespace)
        assert sheet is not None
        assert sheet.attrib["name"] == "Реестр"
        shared_strings = [
            "".join(node.itertext())
            for node in ET.fromstring(zf.read("xl/sharedStrings.xml")).findall("main:si", namespace)
        ]
        worksheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        first_row = worksheet.find(".//main:row[@r='1']", namespace)
        assert first_row is not None
        header = [shared_strings[int(cell.find("main:v", namespace).text or "0")] for cell in first_row.findall("main:c", namespace)]
        assert header == [
            "№",
            "Код",
            "Доработка",
            "Бизнес-область",
            "Что менялось",
            "Ключевые объекты",
            "Решение по переходу",
            "Покрытие целевого релиза",
            "Остаточный разрыв",
            "Риск",
            "Статус проверки",
            "Рекомендация",
        ]


def test_customer_register_validation_identifies_missing_slug(tmp_path) -> None:
    (tmp_path / "analysis/subject-cards").mkdir(parents=True)
    (tmp_path / "outputs").mkdir()
    (tmp_path / "analysis/subject-cards/registry.csv").write_text(
        "slug,title,subject_type,status,confidence,origin_layer,owner_feature,linked_features,linked_detail_maps,primary_objects,coverage_scope,why_separate_card,merge_into,split_from,card_path,evidence_count,gap_count,review_notes\n"
        "missing-slug,Нет строки,business_process,ready_for_review,high,manual,BF-X,BF-X,,,,,,analysis/subject-cards/cards/missing-slug/subject-card.json,0,0,\n",
        encoding="utf-8",
    )
    (tmp_path / "outputs/customer-customization-register.csv").write_text(
        "№,Код,Доработка,Бизнес-область,Что менялось,Ключевые объекты,Решение по переходу,Покрытие целевого релиза,Остаточный разрыв,Риск,Статус проверки,Рекомендация\n",
        encoding="utf-8",
    )
    (tmp_path / "outputs/customer-customization-register.md").write_text("# Реестр\n", encoding="utf-8")

    result = validate_customer_register(tmp_path)

    assert result["status"] == "fail"
    assert any("missing-slug" in error for error in result["errors"])
