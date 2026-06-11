from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from one_c_autoresearch.functional_gaps import (  # noqa: E402
    FUNCTIONAL_GAP_BEHAVIOR_PROBES_HEADER,
    build_functional_gap_card,
    build_functional_gap_map,
    inspect_target_for_functional_gap,
    refresh_functional_gap_card,
    validate_functional_gaps,
)
from one_c_autoresearch.functional_gap_probes import load_target_profile  # noqa: E402


def write_subject_card(root: Path, primary_objects: list[str] | None = None) -> None:
    card_dir = root / "analysis" / "subject-cards" / "cards" / "example-document"
    card_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "subject-card/v1",
        "slug": "example-document",
        "title": "Проверочная предметная карточка",
        "subject_type": "business_process",
        "status": "ready_for_review",
        "confidence": "medium",
        "origin_layer": "manual",
        "coverage_scope": "covered",
        "why_separate_card": "Проверочная карточка имеет самостоятельный бизнес-смысл.",
        "linked_features": ["BF-000"],
        "linked_detail_maps": ["example-detail"],
        "primary_objects": primary_objects if primary_objects is not None else ["Документ.ПроверочныйДокумент"],
        "source_artifacts": ["analysis/subject-cards/cards/example-document/subject-card.json"],
        "runtime_data_needed": "Проверить наличие исторических документов.",
        "summary": "Проверочная доработка внутреннего документа.",
        "identification": "Определяется по объектам проверки.",
        "key_conclusion": "Нужна проверка, закрывает ли новый релиз типовой сценарий.",
        "upgrade_risk": "Может потребоваться адаптация маршрута.",
        "sections": {
            "business_purpose": [
                {
                    "text": "Поддерживает внутренний документ.",
                    "source": "analysis/features/BF-000/findings.md",
                }
            ],
            "implementation": [],
            "checks": [],
            "open_questions": [],
        },
    }
    (card_dir / "subject-card.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (card_dir / "evidence.csv").write_text(
        "evidence_id,section,claim,source_type,source_path,line,linked_diff_id,linked_feature_id,confidence,notes\n"
        "E-1,business_purpose,Поддерживает внутренний документ.,static,analysis/features/BF-000/findings.md,1,,BF-000,medium,\n",
        encoding="utf-8",
    )
    (card_dir / "gaps.csv").write_text(
        "gap_id,section,question,needed_source,status,blocking,notes\n",
        encoding="utf-8",
    )


def write_detail_map(root: Path, slug: str = "example-detail", object_ref: str = "Catalog.ПроверочныйДокумент") -> None:
    detail_dir = root / "analysis" / "detail-maps" / slug
    detail_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "detail-map/v1",
        "slug": slug,
        "title": "Проверочная техническая карта",
        "sections": {
            "attributes": [
                {
                    "object": object_ref,
                    "name": "Реквизит",
                    "source": "Catalogs/ПроверочныйДокумент.xml",
                    "line": "1",
                }
            ]
        },
    }
    (detail_dir / "detail-map.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_project(root: Path, next_vendor: str = "") -> None:
    paths = f"next_vendor = \"{next_vendor}\"\n" if next_vendor else ""
    (root / "project.toml").write_text(
        "[project]\n"
        "project_id = \"test\"\n"
        "product = \"1C:Документооборот\"\n"
        "next_vendor_version = \"ДО 3.0\"\n\n"
        "[paths]\n"
        f"{paths}\n"
        "[rlm]\n"
        "next_vendor = \"do_next_vendor\"\n",
        encoding="utf-8",
    )


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def first_line(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig").splitlines()[0]


def write_target_profile(root: Path, profile_id: str = "target") -> None:
    profile_dir = root / "analysis" / "functional-gaps" / "profiles"
    profile_dir.mkdir(parents=True)
    (profile_dir / f"{profile_id}.toml").write_text(
        f'profile_id = "{profile_id}"\n'
        'profile_title = "Проверочная целевая конфигурация"\n'
        'profile_status = "experimental"\n\n'
        '[[capabilities.route_script_execution.standard_evidence]]\n'
        'kind = "source_call"\n'
        'target = "ВыполнитьСценарийМаршрута"\n'
        'context = "типовой запуск сценария маршрута"\n',
        encoding="utf-8",
    )


def test_build_creates_empty_behavior_probes_artifact(tmp_path: Path) -> None:
    write_project(tmp_path)
    write_subject_card(tmp_path)

    build_functional_gap_card(tmp_path, "example-document")

    probes_path = tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document" / "behavior-probes.csv"
    assert probes_path.exists()
    assert first_line(probes_path) == FUNCTIONAL_GAP_BEHAVIOR_PROBES_HEADER
    assert validate_functional_gaps(tmp_path, card="example-document")["status"] == "ok"


def test_load_target_profile_reads_project_specific_profile(tmp_path: Path) -> None:
    write_target_profile(tmp_path)

    profile = load_target_profile(tmp_path, "target")

    assert profile is not None
    assert profile.profile_id == "target"
    assert profile.profile_status == "experimental"
    assert "route_script_execution" in profile.capabilities


def test_inspect_target_runs_behavior_probes_only_with_explicit_profile(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path, primary_objects=[])
    write_target_profile(tmp_path)
    target_file = tmp_path / "sources" / "next_vendor" / "CommonModules" / "Маршруты" / "Ext" / "Module.bsl"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("ВыполнитьСценарийМаршрута(Объект);\n", encoding="utf-8")
    build_functional_gap_card(tmp_path, "example-document")

    inspect_target_for_functional_gap(tmp_path, "example-document")
    probes_without_profile = read_rows(tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document" / "behavior-probes.csv")

    inspect_target_for_functional_gap(tmp_path, "example-document", target_profile="target")
    probes_with_profile = read_rows(tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document" / "behavior-probes.csv")

    assert all(row["target_profile"] == "" for row in probes_without_profile)
    assert any(
        row["capability_id"] == "route_script_execution"
        and row["target_profile"] == "target"
        and row["result"] == "standard_supported"
        for row in probes_with_profile
    )


def test_refresh_preserves_manual_decision_and_updates_target_source(tmp_path: Path) -> None:
    write_project(tmp_path)
    write_subject_card(tmp_path)

    build_functional_gap_card(tmp_path, "example-document")
    gap_path = tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document" / "gap-card.json"
    payload = json.loads(gap_path.read_text(encoding="utf-8"))
    payload["selected_decision"] = "adapt"
    payload["selected_decision_summary"] = "Ручное решение аналитика."
    payload["manual_decision_locked"] = True
    gap_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    checks_path = gap_path.parent / "checks.csv"
    checks = read_rows(checks_path)
    for row in checks:
        if row["check_id"] == "FGC-0004":
            row["status"] = "done"
            row["result"] = "Решение принято аналитиком."
    write_rows(checks_path, checks)

    (tmp_path / "sources" / "next_vendor").mkdir(parents=True)
    write_project(tmp_path, next_vendor="sources/next_vendor")

    result = refresh_functional_gap_card(tmp_path, "example-document")

    refreshed = json.loads(gap_path.read_text(encoding="utf-8"))
    refreshed_checks = read_rows(checks_path)
    assert result["status"] == "ok"
    assert refreshed["schema_version"] == "functional-gap-card/v2"
    assert refreshed["selected_decision"] == "adapt"
    assert refreshed["selected_decision_summary"] == "Ручное решение аналитика."
    assert refreshed["manual_decision_locked"] is True
    assert refreshed["inputs"]["next_vendor_path"] == "sources/next_vendor"
    assert any(row["check_id"] == "FGC-0004" and row["status"] == "done" for row in refreshed_checks)


def test_inspect_target_writes_findings_and_object_mapping(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path, primary_objects=["Документ.ПроверочныйДокумент", "Справочник.Отсутствующий"])
    target_file = tmp_path / "sources" / "next_vendor" / "Documents" / "ПроверочныйДокумент.xml"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("<Meta>Документ.ПроверочныйДокумент</Meta>\n", encoding="utf-8")

    build_functional_gap_card(tmp_path, "example-document")
    result = inspect_target_for_functional_gap(tmp_path, "example-document")

    card_dir = tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document"
    findings = read_rows(card_dir / "target-findings.csv")
    mappings = read_rows(card_dir / "object-mapping.csv")
    assert result["status"] == "ok"
    assert any(row["finding_type"] == "same_object" and row["target_object"] == "Документ.ПроверочныйДокумент" for row in findings)
    assert any(row["finding_type"] == "no_match" and row["target_object"] == "" for row in findings)
    assert any(row["source_object"] == "Документ.ПроверочныйДокумент" and row["mapping_type"] == "same_name" for row in mappings)
    assert any(row["source_object"] == "Справочник.Отсутствующий" and row["mapping_type"] == "no_target_match" for row in mappings)
    assert validate_functional_gaps(tmp_path, card="example-document")["status"] == "ok"


def test_inspect_target_uses_linked_detail_map_objects_when_subject_card_has_no_primary_objects(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path, primary_objects=[])
    write_detail_map(tmp_path, object_ref="Catalog.ПроверочныйДокумент")
    target_file = tmp_path / "sources" / "next_vendor" / "Catalogs" / "ПроверочныйДокумент.xml"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("<Meta>Catalog.ПроверочныйДокумент</Meta>\n", encoding="utf-8")

    build_functional_gap_card(tmp_path, "example-document")
    result = inspect_target_for_functional_gap(tmp_path, "example-document")

    card_dir = tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document"
    findings = read_rows(card_dir / "target-findings.csv")
    mappings = read_rows(card_dir / "object-mapping.csv")
    assert result["target_findings"] == 1
    assert findings[0]["finding_type"] == "same_object"
    assert findings[0]["target_object"] == "Catalog.ПроверочныйДокумент"
    assert mappings[0]["source_object"] == "Catalog.ПроверочныйДокумент"


def test_inspect_target_preserves_manual_review_notes(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path)
    target_file = tmp_path / "sources" / "next_vendor" / "Documents" / "ПроверочныйДокумент.xml"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("<Meta>Документ.ПроверочныйДокумент</Meta>\n", encoding="utf-8")
    build_functional_gap_card(tmp_path, "example-document")
    review_path = tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document" / "review.md"
    review_path.write_text("# Ручное ревью\n\nMANUAL ANALYST NOTE\n", encoding="utf-8")

    inspect_target_for_functional_gap(tmp_path, "example-document")

    assert "MANUAL ANALYST NOTE" in review_path.read_text(encoding="utf-8")


def test_inspect_target_refreshes_generated_review_status(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path)
    target_file = tmp_path / "sources" / "next_vendor" / "Documents" / "ПроверочныйДокумент.xml"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("<Meta>Документ.ПроверочныйДокумент</Meta>\n", encoding="utf-8")
    build_functional_gap_card(tmp_path, "example-document")
    review_path = tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document" / "review.md"

    inspect_target_for_functional_gap(tmp_path, "example-document")

    review_text = review_path.read_text(encoding="utf-8")
    assert "`FGC-0001` статическая проверка целевого релиза / закрыта" in review_text


def test_inspect_target_keeps_static_check_open_when_no_objects_found(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path, primary_objects=[])
    (tmp_path / "sources" / "next_vendor").mkdir(parents=True)
    build_functional_gap_card(tmp_path, "example-document")

    inspect_target_for_functional_gap(tmp_path, "example-document")

    checks = read_rows(tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document" / "checks.csv")
    target_check = next(row for row in checks if row["check_id"] == "FGC-0001")
    assert target_check["status"] == "open"
    assert "Не найдены объекты" in target_check["result"]


def test_refresh_invalidates_target_inspection_when_target_source_changes(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path)
    target_file = tmp_path / "sources" / "next_vendor" / "Documents" / "ПроверочныйДокумент.xml"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("<Meta>Документ.ПроверочныйДокумент</Meta>\n", encoding="utf-8")
    build_functional_gap_card(tmp_path, "example-document")
    inspect_target_for_functional_gap(tmp_path, "example-document")
    target_file.unlink()

    refresh_functional_gap_card(tmp_path, "example-document")

    card_dir = tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document"
    findings = read_rows(card_dir / "target-findings.csv")
    mappings = read_rows(card_dir / "object-mapping.csv")
    checks = read_rows(card_dir / "checks.csv")
    static_check = next(row for row in checks if row["check_id"] == "FGC-0001")
    assert findings == []
    assert mappings == []
    assert static_check["status"] == "open"
    assert static_check["result"] == ""
    assert validate_functional_gaps(tmp_path, card="example-document")["status"] == "ok"


def test_similar_match_is_not_promoted_to_target_object_mapping(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path, primary_objects=["Catalog.ВнутренниеДокументы"])
    target_file = tmp_path / "sources" / "next_vendor" / "CommonModules" / "ВнутренниеДокументыЭДО" / "Ext" / "Module.bsl"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("Процедура ОбработатьВнутренниеДокументы() КонецПроцедуры\n", encoding="utf-8")
    build_functional_gap_card(tmp_path, "example-document")

    inspect_target_for_functional_gap(tmp_path, "example-document")

    findings = read_rows(tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document" / "target-findings.csv")
    mappings = read_rows(tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document" / "object-mapping.csv")
    finding = next(row for row in findings if row["target_path"].endswith("CommonModules/ВнутренниеДокументыЭДО/Ext/Module.bsl"))
    mapping = next(row for row in mappings if row["source_object"] == "Catalog.ВнутренниеДокументы")
    assert finding["finding_type"] == "similar_object"
    assert finding["target_object"] == ""
    assert mapping["mapping_type"] == "shared_infrastructure"
    assert mapping["target_object"] == ""


def test_refresh_synchronizes_json_generated_lists_with_csv(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path)
    (tmp_path / "sources" / "next_vendor").mkdir(parents=True)
    build_functional_gap_card(tmp_path, "example-document")
    card_dir = tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document"
    gap_path = card_dir / "gap-card.json"

    hypotheses = read_rows(card_dir / "hypotheses.csv")
    hypotheses[0]["status"] = "selected"
    hypotheses[0]["decision"] = "Ручной выбор гипотезы."
    write_rows(card_dir / "hypotheses.csv", hypotheses)
    checks = read_rows(card_dir / "checks.csv")
    checks[0]["status"] = "done"
    checks[0]["result"] = "Проверка закрыта вручную."
    write_rows(card_dir / "checks.csv", checks)

    refresh_functional_gap_card(tmp_path, "example-document")

    payload = json.loads(gap_path.read_text(encoding="utf-8"))
    assert payload["hypotheses"][0]["status"] == "selected"
    assert payload["hypotheses"][0]["decision"] == "Ручной выбор гипотезы."
    assert payload["required_checks"][0]["status"] == "done"
    assert payload["required_checks"][0]["result"] == "Проверка закрыта вручную."


def test_target_source_hash_ignores_mtime_only_changes(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path)
    target_file = tmp_path / "sources" / "next_vendor" / "Documents" / "ПроверочныйДокумент.xml"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("<Meta>Документ.ПроверочныйДокумент</Meta>\n", encoding="utf-8")
    build_functional_gap_card(tmp_path, "example-document")

    os.utime(target_file, (1, 1))

    validation = validate_functional_gaps(tmp_path, card="example-document")
    assert validation["status"] == "ok"


def test_map_build_aggregates_gap_cards(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path)
    (tmp_path / "sources" / "next_vendor").mkdir(parents=True)
    build_functional_gap_card(tmp_path, "example-document")
    inspect_target_for_functional_gap(tmp_path, "example-document")

    result = build_functional_gap_map(tmp_path)

    md_path = tmp_path / "outputs" / "functional-gap-map.md"
    json_path = tmp_path / "outputs" / "functional-gap-map.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert md_path.exists()
    assert "Сводка по переходу на ДО 3.0" in md_path.read_text(encoding="utf-8")
    assert data["target_release"] == "ДО 3.0"
    assert data["summary"]["cards_total"] == 1
    assert data["cards"][0]["subject_card_slug"] == "example-document"


def test_map_build_shows_undecided_cards_without_business_decision_label(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path)
    (tmp_path / "sources" / "next_vendor").mkdir(parents=True)
    build_functional_gap_card(tmp_path, "example-document")

    build_functional_gap_map(tmp_path)

    text = (tmp_path / "outputs" / "functional-gap-map.md").read_text(encoding="utf-8")
    assert "## Доработки без итогового решения" in text
    assert "`example-document` Проверочная предметная карточка - итоговое решение не выбрано" in text
    assert "## Доработки с бизнес-решением\n\nНет карточек." in text


def test_map_build_fails_on_unreadable_gap_card(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    bad_dir = tmp_path / "analysis" / "functional-gaps" / "cards" / "broken-card"
    bad_dir.mkdir(parents=True)
    (bad_dir / "gap-card.json").write_text("{not json", encoding="utf-8")

    try:
        build_functional_gap_map(tmp_path)
    except ValueError as exc:
        assert "broken-card/gap-card.json" in str(exc)
    else:
        raise AssertionError("map-build must fail on unreadable gap-card.json")


def test_force_refresh_can_overwrite_manual_decision(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path)
    (tmp_path / "sources" / "next_vendor").mkdir(parents=True)
    build_functional_gap_card(tmp_path, "example-document")
    gap_path = tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document" / "gap-card.json"
    payload = json.loads(gap_path.read_text(encoding="utf-8"))
    payload["selected_decision"] = "adapt"
    payload["selected_decision_summary"] = "Ручное решение аналитика."
    payload["manual_decision_locked"] = True
    gap_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    refresh_functional_gap_card(tmp_path, "example-document", force=True)

    refreshed = json.loads(gap_path.read_text(encoding="utf-8"))
    assert refreshed["selected_decision"] == ""
    assert refreshed["selected_decision_summary"] == ""
    assert refreshed["manual_decision_locked"] is False


def test_inspect_target_preserves_locked_curated_target_analysis_without_force(tmp_path: Path) -> None:
    write_project(tmp_path, next_vendor="sources/next_vendor")
    write_subject_card(tmp_path, primary_objects=["Catalog.ПроверочныйДокумент"])
    target_file = tmp_path / "sources" / "next_vendor" / "Catalogs" / "ПроверочныйДокумент.xml"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("<Meta>Catalog.ПроверочныйДокумент</Meta>\n", encoding="utf-8")
    build_functional_gap_card(tmp_path, "example-document")
    card_dir = tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document"
    gap_path = card_dir / "gap-card.json"
    payload = json.loads(gap_path.read_text(encoding="utf-8"))
    payload["manual_decision_locked"] = True
    payload["selected_decision"] = "split"
    gap_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_rows(
        card_dir / "target-findings.csv",
        [
            {
                "finding_id": "FGF-9999",
                "finding_type": "standard_mechanism",
                "target_object": "Catalog.КураторскаяНаходка",
                "target_path": "sources/next_vendor/Catalogs/КураторскаяНаходка.xml",
                "match_basis": "manual-functional-equivalence",
                "confidence": "high",
                "evidence_ref": "analysis/functional-gaps/cards/example-document/functional-equivalence.csv",
                "notes": "Ручная функциональная эквивалентность.",
            }
        ],
    )
    write_rows(
        card_dir / "object-mapping.csv",
        [
            {
                "mapping_id": "FGM-9999",
                "source_object": "Catalog.ПроверочныйДокумент",
                "source_path": "analysis/subject-cards/cards/example-document/subject-card.json#primary_objects",
                "target_object": "Catalog.КураторскаяНаходка",
                "target_path": "sources/next_vendor/Catalogs/КураторскаяНаходка.xml",
                "mapping_type": "functional_candidate",
                "confidence": "high",
                "decision": "split",
                "notes": "Ручное сопоставление.",
            }
        ],
    )

    result = inspect_target_for_functional_gap(tmp_path, "example-document")

    findings = read_rows(card_dir / "target-findings.csv")
    mappings = read_rows(card_dir / "object-mapping.csv")
    assert result["target_findings"] == 1
    assert findings[0]["finding_id"] == "FGF-9999"
    assert mappings[0]["mapping_id"] == "FGM-9999"
