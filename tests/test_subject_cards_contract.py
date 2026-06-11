from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from one_c_autoresearch.doctor import Doctor  # noqa: E402


def write_subject_card_contract_repo(root: Path, section_row: dict[str, str]) -> None:
    subject_root = root / "analysis" / "subject-cards"
    card_dir = subject_root / "cards" / "example-card"
    card_dir.mkdir(parents=True)
    (subject_root / "README.md").write_text("# Subject cards\n", encoding="utf-8")
    (subject_root / "_templates").mkdir()
    (subject_root / "_templates" / "subject-card.json").write_text("{}\n", encoding="utf-8")
    (subject_root / "candidates.csv").write_text(
        "candidate_id,title,proposed_slug,source,discovery_basis,linked_features,linked_detail_maps,primary_objects,subject_type,confidence,proposed_action,status,notes\n",
        encoding="utf-8",
    )
    (subject_root / "classification.csv").write_text(
        "candidate_id,proposed_slug,decision,subject_type,registry_slug,merge_into,split_from,why_separate_card,status,confidence,notes\n",
        encoding="utf-8",
    )
    (subject_root / "registry.csv").write_text(
        "slug,title,subject_type,status,confidence,origin_layer,owner_feature,linked_features,linked_detail_maps,primary_objects,coverage_scope,why_separate_card,merge_into,split_from,card_path,evidence_count,gap_count,review_notes\n"
        "example-card,Проверочная карточка,business_process,ready_for_review,high,manual,BF-000,BF-000,,Catalog.Example,covered,Отдельная проверочная карточка,,,analysis/subject-cards/cards/example-card/subject-card.json,1,0,\n",
        encoding="utf-8",
    )
    (subject_root / "coverage.csv").write_text(
        "source_kind,source_id,feature_id,detail_map_slug,subject_card_slug,relation,confidence,notes\n",
        encoding="utf-8",
    )
    (card_dir / "subject-card.json").write_text(
        json.dumps(
            {
                "schema_version": "subject-card/v1",
                "slug": "example-card",
                "title": "Проверочная карточка",
                "status": "ready_for_review",
                "confidence": "high",
                "subject_type": "business_process",
                "origin_layer": "manual",
                "primary_objects": ["Catalog.Example"],
                "coverage_scope": "covered",
                "why_separate_card": "Отдельная проверочная карточка.",
                "summary": "Краткое описание.",
                "identification": "Определяется по тестовому объекту.",
                "key_conclusion": "Карточка нужна для проверки контракта.",
                "upgrade_risk": "Низкий.",
                "linked_features": ["BF-000"],
                "linked_detail_maps": [],
                "source_artifacts": ["analysis/subject-cards/cards/example-card/subject-card.json"],
                "sections": {
                    "attributes": [section_row],
                    "form_rules": [],
                    "validations": [],
                    "lifecycle": [],
                    "rights": [],
                    "scheduled_jobs": [],
                    "ui": [],
                    "integrations": [],
                    "sources": [],
                    "open_questions": [],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (card_dir / "evidence.csv").write_text(
        "evidence_id,section,claim,source_type,source_path,line,linked_diff_id,linked_feature_id,confidence,notes\n",
        encoding="utf-8",
    )
    (card_dir / "gaps.csv").write_text(
        "gap_id,section,question,needed_source,status,blocking,notes\n",
        encoding="utf-8",
    )
    (card_dir / "review.md").write_text("# Ревью\n", encoding="utf-8")


def test_subject_card_sections_accept_claim_level_rows(tmp_path: Path) -> None:
    write_subject_card_contract_repo(
        tmp_path,
        {
            "claim": "Реквизит описан аналитическим утверждением.",
            "source": "analysis/example.csv",
            "line": "E-1",
            "confidence": "high",
        },
    )

    doctor = Doctor(tmp_path, mode="research")
    doctor.test_subject_cards_contract()

    failures = [check for check in doctor.checks.checks if check["status"] == "fail"]
    assert not failures


def test_subject_card_sections_reject_detail_map_rows(tmp_path: Path) -> None:
    write_subject_card_contract_repo(
        tmp_path,
        {
            "object": "Catalog.Example",
            "kind": "Attribute",
            "name": "ExampleAttribute",
            "source": "analysis/example.csv",
            "line": "E-1",
            "confidence": "high",
        },
    )

    doctor = Doctor(tmp_path, mode="research")
    doctor.test_subject_cards_contract()

    assert any(check["id"] == "subject_cards.section_claim" and check["status"] == "fail" for check in doctor.checks.checks)
