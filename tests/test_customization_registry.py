from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from one_c_autoresearch.common import read_jsonl
from one_c_autoresearch.customization_registry import (
    BUILD_METADATA_JSON,
    EVIDENCE_JSONL,
    ITEMS_JSONL,
    LINEAGE_JSONL,
    build_registry,
    bootstrap_registry,
    parse_designer_report,
    resolve_v8unpack_uuids,
    validate_registry,
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


class CustomizationRegistryTest(unittest.TestCase):
    def seed_inputs(self, root: Path) -> None:
        write(
            root / "analysis/indexes/final-diff-inventory.csv",
            "diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes,reverse_status,reverse_confidence,reverse_scenario_id\n"
            "V8D-1,test,M,Catalog/Товары/Catalog.json,Catalog,Catalog.Товары,metadata,F-1,custom,high,kept,Changed,,,\n"
            "V8D-2,test,M,CommonModule/Демо/CommonModule.bsl,CommonModule,CommonModule.Демо,bsl,F-2,custom,medium,kept,BSL changed,analysis/source.bsl,,confirmed_in_scenario,medium,BF-001\n",
        )
        write_jsonl(
            root / "analysis/custom-metadata/index.jsonl",
            [
                {
                    "schema_version": "custom-metadata-inventory/v1",
                    "item_id": "CMI-1",
                    "item_key": "Catalog|Catalog.Товары|attribute|Цвет|Attributes/Цвет",
                    "metadata_full_name": "Catalog.Товары",
                    "part_kind": "attribute",
                    "part_name": "Цвет",
                    "part_path": "Attributes/Цвет",
                    "change_type": "added",
                    "status": "non_typical",
                    "reconciliation": {"links": {"final_diff_ids": ["V8D-1"], "subject_card_slugs": ["bf-test"]}},
                }
            ],
        )
        write(
            root / "analysis/external-processing/inventory.csv",
            "external_id,kind,status,source_file,original_name,sha256,unpacked_dir,log_file,file_count,return_code\n"
            "EXT-1,epf,ok,/tmp/one.epf,Распределение зарплаты 2026.epf,abc,analysis/external-processing/source/EXT-1,,1,0\n"
            "EXT-2,epf,ok,/tmp/two.epf,Распределение зарплаты v2.epf,def,analysis/external-processing/source/EXT-2,,1,0\n",
        )
        write(
            root / "analysis/external-processing/external-tools-source-reconciliation.csv",
            "status,screen_kind,screen_name,external_id,file_kind,file_status,unpacked_dir,original_name\n"
            "found,Обработка,Распределение зарплаты,EXT-1,epf,ok,analysis/external-processing/source/EXT-1,Распределение зарплаты 2026.epf\n"
            "found,Обработка,Распределение зарплаты,EXT-2,epf,ok,analysis/external-processing/source/EXT-2,Распределение зарплаты v2.epf\n",
        )
        write(
            root / "analysis/external-processing/review/index.csv",
            "external_review_id,external_id,source_state,status,priority,title,source_kind,inventory_ref,reconciliation_ref,source_dir,original_name,business_purpose,technical_scope,migration_relevance,subject_card_links,functional_gap_links,open_questions,queue_task_id,notes\n"
            "EPR-EXT-1,EXT-1,found,needs_followup,70,Распределение зарплаты,Обработка,EXT-1,row1,analysis/external-processing/source/EXT-1,one.epf,Распределение зарплаты по заказам,Document.ОтражениеЗарплатыВУчете,,bf-payroll,gap-payroll,,Q-1,\n"
            "EPR-EXT-2,EXT-2,found,needs_followup,70,Распределение зарплаты 2026 v2,Обработка,EXT-2,row2,analysis/external-processing/source/EXT-2,two.epf,Распределение зарплаты по заказам,Document.ОтражениеЗарплатыВУчете,,bf-payroll,gap-payroll,,Q-2,\n"
            "EPR-EXT-3,EXT-3,failed_unpack,blocked,100,Неизвестная обработка,Обработка,EXT-3,row3,analysis/external-processing/source/EXT-3,missing.epf,Требует исходник,,,bf-missing,,,Q-3,\n",
        )
        (root / "analysis/external-processing/source/EXT-1").mkdir(parents=True)
        (root / "analysis/external-processing/source/EXT-2").mkdir(parents=True)

    def test_bootstrap_builds_registry_and_groups_external_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed_inputs(root)

            result = bootstrap_registry(root)

            self.assertEqual(result["status"], "ok")
            items = [row for _, row in read_jsonl(root / ITEMS_JSONL)]
            evidence = [row for _, row in read_jsonl(root / EVIDENCE_JSONL)]
            external_items = [row for row in items if row["source_kinds"] == ["external_processing"]]
            self.assertEqual(len(external_items), 2)
            self.assertTrue(all(row["scope_status"] == "included" for row in items))
            self.assertTrue(all(row["bp30_coverage_status"] == "unknown" for row in external_items))
            payroll_evidence = [row for row in evidence if row["customization_id"] == external_items[0]["customization_id"] and row["evidence_type"] == "external_processing"]
            self.assertTrue(any(row["external_id"] == "EXT-1" for row in payroll_evidence))
            self.assertTrue(any(row["evidence_type"] == "diff" and row["diff_id"] == "V8D-1" for row in evidence))
            self.assertTrue(any(row["evidence_type"] == "diff" and row["diff_id"] == "V8D-2" for row in evidence))
            self.assertTrue(any(row["evidence_type"] == "bsl_source" and row["source_path"] == "analysis/source.bsl" for row in evidence))
            self.assertEqual(validate_registry(root)["status"], "ok")

    def test_validation_requires_customer_reason_for_scope_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed_inputs(root)
            build_registry(root)
            items = [row for _, row in read_jsonl(root / ITEMS_JSONL)]
            items[0]["scope_status"] = "excluded_by_customer"
            write_jsonl(root / ITEMS_JSONL, items)

            result = validate_registry(root)

            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("excluded without customer agreement reason" in error for error in result["errors"]))

    def test_validation_requires_source_reason_for_false_positive_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed_inputs(root)
            build_registry(root)
            items = [row for _, row in read_jsonl(root / ITEMS_JSONL)]
            items[0]["scope_status"] = "excluded_false_positive"
            write_jsonl(root / ITEMS_JSONL, items)

            result = validate_registry(root)

            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("false positive without source-backed reason" in error for error in result["errors"]))

    def test_bootstrap_fails_closed_without_required_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = bootstrap_registry(Path(temp))

            self.assertEqual(result["status"], "fail")
            self.assertIn("analysis/indexes/final-diff-inventory.csv", result["missing_prerequisites"])

    def test_context_helpers_parse_report_and_uuid_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "report.txt"
            report.write_text("Объект: Catalog.ГруппыПользователей\nМодуль - Различаются значения\n", encoding="utf-16")
            v8 = root / "v8" / "Catalog" / "ФизическиеЛица"
            v8.mkdir(parents=True)
            (v8 / "Catalog.json").write_text('{"uuid":"11111111-2222-3333-4444-555555555555"}', encoding="utf-8")

            report_rows, meta = parse_designer_report(root, str(report))
            uuid_rows = resolve_v8unpack_uuids(root, str(root / "v8"))

            self.assertEqual(meta["encoding"], "utf-16")
            self.assertEqual(report_rows[0]["object_name"], "Catalog.ГруппыПользователей")
            self.assertEqual(uuid_rows[0]["metadata_object"], "Catalog.ФизическиеЛица")

    def test_validation_rejects_found_external_source_with_missing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed_inputs(root)
            build_registry(root)
            target = root / "analysis/external-processing/source/EXT-1"
            target.rmdir()

            result = validate_registry(root)

            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("folder is missing" in error for error in result["errors"]))

    def test_validation_rejects_active_superseded_customization(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.seed_inputs(root)
            build_registry(root)
            items = [row for _, row in read_jsonl(root / ITEMS_JSONL)]
            source_id = items[0]["customization_id"]
            target_id = items[1]["customization_id"]
            write_jsonl(
                root / LINEAGE_JSONL,
                [
                    {
                        "event_type": "supersede",
                        "source_ids": [source_id],
                        "target_ids": [target_id],
                        "reason": "test",
                        "evidence_refs": ["unit"],
                        "reviewer": "test",
                        "timestamp": "2026-01-01T00:00:00Z",
                    }
                ],
            )

            result = validate_registry(root)

            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("still active" in error for error in result["errors"]))

    def test_validation_accepts_lineage_event_types_for_inactive_source(self) -> None:
        for event_type in ("split", "merge", "restore"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.seed_inputs(root)
                build_registry(root)
                items = [row for _, row in read_jsonl(root / ITEMS_JSONL)]
                source_id = items[0]["customization_id"]
                target_id = items[1]["customization_id"]
                items[0]["status"] = "superseded" if event_type != "restore" else "needs_manual_review"
                write_jsonl(root / ITEMS_JSONL, items)
                (root / BUILD_METADATA_JSON).write_text(
                    json.dumps({"schema_version": "customization-registry/v1", "context": {}, "build": {}, "validation": {}}, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                write_jsonl(
                    root / LINEAGE_JSONL,
                    [
                        {
                            "event_type": event_type,
                            "source_ids": [source_id],
                            "target_ids": [target_id],
                            "reason": "test",
                            "evidence_refs": ["unit"],
                            "reviewer": "test",
                            "timestamp": "2026-01-01T00:00:00Z",
                        }
                    ],
                )

                self.assertEqual(validate_registry(root)["status"], "ok")


if __name__ == "__main__":
    unittest.main()
