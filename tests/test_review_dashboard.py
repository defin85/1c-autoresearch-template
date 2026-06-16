from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from one_c_autoresearch.review_dashboard import build_dashboard_data, dashboard_html  # noqa: E402


def test_subject_card_claim_rows_have_dedicated_dashboard_columns() -> None:
    html = dashboard_html(
        {
            "schema_version": "review-dashboard/v1",
            "dashboard_scope": {"mode": "full"},
            "project": {},
            "summary": {},
            "features": [],
            "subject_cards": [],
            "subject_maps": [
                {
                    "slug": "claim-card",
                    "title": "Карточка с утверждениями",
                    "generation_mode": "subject_card",
                    "status": "ready_for_review",
                    "status_label": "Готово к ревью",
                    "confidence_label": "Высокая",
                    "sections": {
                        "attributes": [
                            {
                                "claim": "Реквизит описан утверждением.",
                                "source": "analysis/example.csv",
                                "line": "E-1",
                                "confidence_label": "Высокая",
                            }
                        ]
                    },
                    "counts": {"attributes": 1},
                }
            ],
            "subject_registry": [],
            "subject_card_coverage": [],
            "functional_gap_map": {"summary": {}, "cards": []},
            "detail_maps": [],
            "open_questions": [],
            "output_open_questions": [],
            "infobase_checks": [],
            "risk_features": [],
            "outputs": [],
            "final_audit": {},
        }
    )

    assert "claimSectionColumns" in html
    assert '["claim","Что подтверждает"]' in html


def test_subject_card_claim_sections_use_analytical_labels() -> None:
    html = dashboard_html(
        {
            "schema_version": "review-dashboard/v1",
            "dashboard_scope": {"mode": "full"},
            "project": {},
            "summary": {},
            "features": [],
            "subject_cards": [],
            "subject_maps": [
                {
                    "slug": "claim-card",
                    "title": "Карточка с утверждениями",
                    "generation_mode": "subject_card",
                    "status": "ready_for_review",
                    "status_label": "Готово к ревью",
                    "confidence_label": "Высокая",
                    "sections": {
                        "attributes": [
                            {
                                "claim": "Реквизит описан утверждением.",
                                "source": "analysis/example.csv",
                                "line": "E-1",
                                "confidence_label": "Высокая",
                            }
                        ]
                    },
                    "counts": {"attributes": 1},
                }
            ],
            "subject_registry": [],
            "subject_card_coverage": [],
            "functional_gap_map": {"summary": {}, "cards": []},
            "detail_maps": [],
            "open_questions": [],
            "output_open_questions": [],
            "infobase_checks": [],
            "risk_features": [],
            "outputs": [],
            "final_audit": {},
        }
    )

    assert 'attributes: "Выводы по данным и реквизитам"' in html
    assert "const subjectClaimSectionLabels" in html


def test_subject_card_renders_linked_detail_map_attributes_summary() -> None:
    html = dashboard_html(
        {
            "schema_version": "review-dashboard/v1",
            "dashboard_scope": {"mode": "full"},
            "project": {},
            "summary": {},
            "features": [],
            "subject_cards": [],
            "subject_maps": [
                {
                    "slug": "bf-card",
                    "title": "Карточка доработки",
                    "generation_mode": "subject_card",
                    "status": "ready_for_review",
                    "status_label": "Готово к ревью",
                    "confidence_label": "Высокая",
                    "linked_detail_maps": ["detail-a"],
                    "sections": {},
                    "counts": {},
                }
            ],
            "subject_registry": [],
            "subject_card_coverage": [],
            "functional_gap_map": {"summary": {}, "cards": []},
            "detail_maps": [
                {
                    "slug": "detail-a",
                    "title": "Техническая карта",
                    "status": "ready_for_review",
                    "status_label": "Готово к ревью",
                    "sections": {
                        "attributes": [
                            {
                                "object": "Catalogs.Тест",
                                "name": "Ответственный",
                                "data_type": "CatalogRef.Пользователи",
                                "relation": "Используется в маршруте согласования",
                                "source": "Catalogs/Тест.xml",
                                "line": "42",
                            }
                        ]
                    },
                    "counts": {"attributes": 1},
                }
            ],
            "open_questions": [],
            "output_open_questions": [],
            "infobase_checks": [],
            "risk_features": [],
            "outputs": [],
            "final_audit": {},
        }
    )

    assert "linkedDetailAttributesSection" in html
    assert "linkedDetailAttributeRows" in html
    assert "Реквизиты из технических карт" in html
    assert '["detail_map_title","Техническая карта"]' in html


def test_dashboard_data_counts_infobase_question_registry(tmp_path: Path) -> None:
    (tmp_path / "analysis/indexes").mkdir(parents=True)
    (tmp_path / "analysis/reverse-map").mkdir(parents=True)
    (tmp_path / "outputs").mkdir(parents=True)
    (tmp_path / "analysis/indexes/feature-map.csv").write_text(
        "feature_id,title,domain,source_bucket,classification,confidence,status,owner,summary,evidence_pack_path,open_questions_path,outputs,notes\n"
        "BF-001,Тестовая доработка,НСИ,target_cf,confirmed business feature,high,complete,codex,Описание,analysis/features/BF-001,analysis/features/BF-001/open-questions.md,outputs/customization-map.md,\n",
        encoding="utf-8",
    )
    for relative in (
        "analysis/indexes/diff-inventory.csv",
        "analysis/indexes/final-diff-inventory.csv",
        "analysis/indexes/final-feature-map.csv",
        "analysis/reverse-map/coverage.csv",
        "analysis/reverse-map/decisions.csv",
        "analysis/reverse-map/unresolved.csv",
        "analysis/reverse-map/infobase-checks.csv",
        "outputs/open-questions.csv",
    ):
        (tmp_path / relative).write_text("\n", encoding="utf-8")
    (tmp_path / "outputs/infobase-questions.csv").write_text(
        "question_id,subject_card_slug,feature_id,object_or_setting,check_target,reason,closing_result,risk_if_open,source_ref,status\n"
        "IBQ-1,card-a,BF-001,Catalog.Тест,Проверить данные ИБ,Статический анализ не видит данные,Закрыть проверкой,Риск миграции,analysis/subject-cards/cards/card-a/gaps.csv#IB-001,open\n",
        encoding="utf-8",
    )

    data = build_dashboard_data(tmp_path, tmp_path / "outputs/review")

    assert data["summary"]["infobase_question_count"] == 1
    assert data["summary"]["open_infobase_question_count"] == 1
    assert data["infobase_questions"][0]["question_id"] == "IBQ-1"
