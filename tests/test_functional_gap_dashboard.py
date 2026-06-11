from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from one_c_autoresearch.functional_gap_dashboard import (  # noqa: E402
    build_functional_gap_dashboard,
    build_functional_gap_dashboard_snapshot,
    dashboard_html,
)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_project(root: Path) -> None:
    (root / "project.toml").write_text(
        "[project]\n"
        "project_id = \"test\"\n"
        "product = \"1C\"\n"
        "next_vendor_version = \"Целевой релиз 2.0\"\n",
        encoding="utf-8",
    )


def write_dashboard_fixture(root: Path, *, with_scenarios: bool = True) -> None:
    write_project(root)
    card_dir = root / "analysis" / "functional-gaps" / "cards" / "example-document"
    card_dir.mkdir(parents=True, exist_ok=True)
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    gap_card = {
        "schema_version": "functional-gap-card/v2",
        "subject_card_slug": "example-document",
        "title": "Проверочная карточка",
        "status": "ready_for_review",
        "gap_readiness": "ready_for_gap_review",
        "selected_decision": "split",
        "selected_decision_summary": "Разделить: <b>типовое</b> и адаптация.",
        "target_release": "Целевой релиз 2.0",
        "manual_decision_locked": True,
    }
    (card_dir / "gap-card.json").write_text(json.dumps(gap_card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (card_dir / "review.md").write_text("# Ревью\n", encoding="utf-8")
    write_csv(
        card_dir / "hypotheses.csv",
        ["hypothesis_id", "gap_type", "status", "confidence", "summary", "evidence_ref", "next_check", "decision"],
        [
            {
                "hypothesis_id": "FGH-0001",
                "gap_type": "split",
                "status": "selected",
                "confidence": "high",
                "summary": "Часть покрывается типовым механизмом.",
                "evidence_ref": "analysis/functional-gaps/cards/example-document/functional-equivalence.csv#S-001",
                "next_check": "",
                "decision": "split",
            }
        ],
    )
    write_csv(
        card_dir / "checks.csv",
        ["check_id", "check_type", "status", "source", "question", "result", "blocking"],
        [
            {
                "check_id": "FGC-0001",
                "check_type": "target_release_static",
                "status": "done",
                "source": "analysis/functional-gaps/cards/example-document/target-findings.csv",
                "question": "Проверить объект целевого релиза.",
                "result": "Найден типовой объект.",
                "blocking": "false",
            }
        ],
    )
    write_csv(
        card_dir / "target-findings.csv",
        ["finding_id", "finding_type", "target_object", "target_path", "match_basis", "confidence", "evidence_ref", "notes"],
        [
            {
                "finding_id": "FGF-0001",
                "finding_type": "standard_mechanism",
                "target_object": "Catalog.ДокументыПредприятия",
                "target_path": "sources/next_vendor/Catalogs/ДокументыПредприятия.xml",
                "match_basis": "static",
                "confidence": "high",
                "evidence_ref": "sources/next_vendor/Catalogs/ДокументыПредприятия.xml",
                "notes": "Типовой объект.",
            }
        ],
    )
    write_csv(
        card_dir / "object-mapping.csv",
        ["mapping_id", "source_object", "source_path", "target_object", "target_path", "mapping_type", "confidence", "decision", "notes"],
        [
            {
                "mapping_id": "FGM-0001",
                "source_object": "Catalog.НД_СМК",
                "source_path": "analysis/subject-cards/cards/example-document/subject-card.json",
                "target_object": "Catalog.ДокументыПредприятия",
                "target_path": "sources/next_vendor/Catalogs/ДокументыПредприятия.xml",
                "mapping_type": "functional_candidate",
                "confidence": "medium",
                "decision": "split",
                "notes": "Сопоставление для перехода.",
            }
        ],
    )
    if with_scenarios:
        write_csv(
            card_dir / "functional-equivalence.csv",
            [
                "scenario_id",
                "scenario",
                "status",
                "standard_mechanism",
                "target_object",
                "evidence_ref",
                "gap_or_limit",
                "next_action",
                "confidence",
                "notes",
            ],
            [
                {
                    "scenario_id": "S-001",
                    "scenario": "Сценарий <img src=x onerror=alert(1)>",
                    "status": "standard_setting",
                    "standard_mechanism": "Дополнительные реквизиты",
                    "target_object": "Catalog.ДокументыПредприятия",
                    "evidence_ref": "analysis/functional-gaps/cards/example-document/target-findings.csv#FGF-0001",
                    "gap_or_limit": "",
                    "next_action": "Настроить вид документа",
                    "confidence": "high",
                    "notes": "</script><script>alert(1)</script>",
                }
            ],
        )
    write_csv(
        root / "analysis" / "functional-gaps" / "open-questions.csv",
        ["question_id", "subject_card_slug", "check_id", "question", "needed_source", "blocking", "status", "notes"],
        [
            {
                "question_id": "FGO-0001",
                "subject_card_slug": "example-document",
                "check_id": "FGC-0001",
                "question": "Нужна проверка настройки?",
                "needed_source": "analyst",
                "blocking": "false",
                "status": "open",
                "notes": "",
            }
        ],
    )
    map_payload = {
        "target_release": "Целевой релиз 2.0",
        "generated_at": "2026-06-01T00:00:00Z",
        "summary": {
            "cards_total": 1,
            "ready_for_review": 1,
            "reviewed": 0,
            "open_blocking_checks": 0,
        },
        "cards": [
            {
                "subject_card_slug": "example-document",
                "title": "Проверочная карточка",
                "status": "ready_for_review",
                "gap_readiness": "ready_for_gap_review",
                "selected_decision": "split",
                "selected_decision_summary": "Разделить: <b>типовое</b> и адаптация.",
                "target_release": "Целевой релиз 2.0",
                "gap_card_path": "analysis/functional-gaps/cards/example-document/gap-card.json",
                "review_path": "analysis/functional-gaps/cards/example-document/review.md",
                "hypotheses_count": 1,
                "checks_count": 1,
                "open_checks_count": 0,
                "open_blocking_checks_count": 0,
                "target_findings_count": 1,
                "object_mappings_count": 1,
                "target_findings": [],
                "object_mappings": [],
                "checks": [],
                "hypotheses": [],
            }
        ],
    }
    (root / "outputs" / "functional-gap-map.json").write_text(
        json.dumps(map_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def embedded_snapshot(html: str) -> dict[str, object]:
    match = re.search(r'<script id="functional-gap-dashboard-data" type="application/json">(.*?)</script>', html, re.S)
    assert match, "dashboard HTML must embed snapshot JSON"
    return json.loads(match.group(1))


def test_build_functional_gap_dashboard_writes_snapshot_and_self_contained_html(tmp_path: Path) -> None:
    write_dashboard_fixture(tmp_path)

    result = build_functional_gap_dashboard(tmp_path)

    data_path = tmp_path / "outputs" / "functional-gap-dashboard" / "data.json"
    html_path = tmp_path / "outputs" / "functional-gap-dashboard" / "index.html"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    html = html_path.read_text(encoding="utf-8")
    assert result["status"] == "ok"
    assert result["cards"] == 1
    assert data["schema_version"] == "functional-gap-dashboard/v1"
    assert data["summary"]["scenario_statuses"]["standard_setting"] == 1
    assert data["cards"][0]["scenario_matrix_missing"] is False
    assert data["cards"][0]["artifact_links"]["gap_card_href"] == "../analysis/functional-gaps/cards/example-document/gap-card.json"
    assert "fetch(" not in html
    assert embedded_snapshot(html) == data
    assert "<img src=x" not in html
    assert "</script><script>" not in html
    assert "\\u003cimg src=x" in html


def test_snapshot_keeps_card_visible_when_functional_equivalence_is_missing(tmp_path: Path) -> None:
    write_dashboard_fixture(tmp_path, with_scenarios=False)

    snapshot = build_functional_gap_dashboard_snapshot(tmp_path)

    assert snapshot["cards"][0]["scenario_matrix_missing"] is True
    assert snapshot["cards"][0]["scenarios"] == []
    assert any("functional-equivalence.csv" in warning["message"] for warning in snapshot["source_warnings"])


def test_snapshot_fails_on_malformed_functional_equivalence_csv(tmp_path: Path) -> None:
    write_dashboard_fixture(tmp_path)
    scenario_path = tmp_path / "analysis" / "functional-gaps" / "cards" / "example-document" / "functional-equivalence.csv"
    scenario_path.write_text("scenario_id,scenario,status\nS-001,broken,standard_setting\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        build_functional_gap_dashboard_snapshot(tmp_path)

    assert "example-document" in str(exc.value)
    assert "functional-equivalence.csv" in str(exc.value)


def test_dashboard_html_contains_transition_diagram_and_filter_targets(tmp_path: Path) -> None:
    write_dashboard_fixture(tmp_path)
    snapshot = build_functional_gap_dashboard_snapshot(tmp_path)

    html = dashboard_html(snapshot)

    assert "Текущая доработка" in html
    assert "Решение перехода" in html
    assert "Механизмы целевого релиза и остаточные разрывы" in html
    assert "selected-decision-filter" in html
    assert "scenario-status-filter" in html
    assert "selected-card-hidden" in html
