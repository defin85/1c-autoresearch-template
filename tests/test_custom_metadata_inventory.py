from __future__ import annotations

import argparse
import json
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from one_c_autoresearch.configuration_source_parser import (
    PARSER_IMPLEMENTATION_VERSION,
    PARSER_SCHEMA_VERSION,
    ParsedConfigurationSnapshot,
    ParsedMetadataItem,
    SourceReference,
    make_item_key,
    write_snapshot,
)
from one_c_autoresearch.custom_metadata_inventory import (
    BUILD_METADATA_JSON,
    CHANGE_TYPE_ADDED,
    CHANGE_TYPE_MODIFIED,
    CHANGE_TYPE_UNCHANGED,
    COMPARISON_MODE_FULL,
    COMPARISON_MODE_REDUCED,
    CUSTOM_METADATA_SCHEMA_VERSION,
    INDEX_JSONL,
    InventoryError,
    PART_KIND_OBJECT,
    build_command,
    build_inventory,
    compare_snapshots,
    export_command,
    inventory_item_key,
    validate_inventory,
    write_inventory,
)


def parsed_item(
    metadata_kind: str,
    metadata_name: str,
    part_kind: str,
    part_name: str = "",
    part_path: str = "",
    content: str = "same",
    source_format: str = "xml-bsl",
    source_path: str | None = None,
    structure_level: str = "structured",
) -> ParsedMetadataItem:
    metadata_full_name = f"{metadata_kind}.{metadata_name}"
    item_key = make_item_key(metadata_kind, metadata_full_name, part_kind, part_name, part_path)
    path = source_path or f"{metadata_kind}s/{metadata_name}.xml"
    return ParsedMetadataItem(
        item_key=item_key,
        source_format=source_format,
        metadata_kind=metadata_kind,
        metadata_name=metadata_name,
        metadata_full_name=metadata_full_name,
        part_kind=part_kind,
        part_name=part_name,
        part_path=part_path,
        source_refs=[SourceReference(path=path, source_kind="fixture")],
        structural_payload={"structure_level": structure_level, "content": content},
        content_hash=f"hash:{content}",
    )


def snapshot_fixture(items: list[ParsedMetadataItem], source_format: str = "xml-bsl") -> ParsedConfigurationSnapshot:
    sorted_items = sorted(items, key=lambda item: item.item_key)
    files = sorted({ref.path for item in sorted_items for ref in item.source_refs})
    unknown_files = sorted({ref.path for item in sorted_items if item.part_kind == "unknown" for ref in item.source_refs})
    coverage_counts = {
        "binary_forms": sum(1 for item in sorted_items if item.part_kind == "form" and item.structural_payload.get("structure_level") == "binary"),
        "form_elements": sum(1 for item in sorted_items if item.part_kind == "form_element"),
        "form_modules": sum(1 for item in sorted_items if item.part_kind == "module" and ("Forms/" in item.part_path or "Form" in item.part_path)),
        "structured_forms": sum(1 for item in sorted_items if item.part_kind == "form" and item.structural_payload.get("structure_level") == "structured"),
        "unknown_visible_parts": len(unknown_files),
    }
    return ParsedConfigurationSnapshot(
        schema_version=PARSER_SCHEMA_VERSION,
        parser_version=PARSER_IMPLEMENTATION_VERSION,
        source_format=source_format,
        source_root="/fixtures/source",
        source_root_identity={"fixture": True},
        items=sorted_items,
        traversal={"coverage_counts": coverage_counts, "files_seen": files, "files_parsed": files, "unknown_files": unknown_files},
        generated_at="2026-01-01T00:00:00Z",
    )


def read_xlsx_values(path: Path) -> list[list[str]]:
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as zf:
        shared_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        shared_strings = ["".join(text.text or "" for text in item.findall(".//main:t", namespace)) for item in shared_root.findall("main:si", namespace)]
        sheet_root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    rows: list[list[str]] = []
    for row in sheet_root.findall(".//main:row", namespace):
        row_values: list[str] = []
        for cell in row.findall("main:c", namespace):
            value_node = cell.find("main:v", namespace)
            if value_node is None:
                row_values.append("")
            elif cell.attrib.get("t") == "s":
                row_values.append(shared_strings[int(value_node.text or "0")])
            else:
                row_values.append(value_node.text or "")
        rows.append(row_values)
    return rows


def typical_snapshot_fixture(source_format: str = "xml-bsl") -> ParsedConfigurationSnapshot:
    return snapshot_fixture(
        [
            parsed_item("Catalog", "Товары", PART_KIND_OBJECT, content="catalog", source_format=source_format),
            parsed_item("Catalog", "Товары", "attribute", "Артикул", "Attributes/Артикул", content="article", source_format=source_format),
            parsed_item("Catalog", "Товары", "form", "ФормаЭлемента", "Forms/ФормаЭлемента", content="form-v1", source_format=source_format, structure_level="binary"),
            parsed_item("Document", "Заказ", PART_KIND_OBJECT, content="doc", source_format=source_format),
        ],
        source_format=source_format,
    )


def customer_snapshot_fixture(source_format: str = "xml-bsl") -> ParsedConfigurationSnapshot:
    return snapshot_fixture(
        [
            parsed_item("Catalog", "Товары", PART_KIND_OBJECT, content="catalog", source_format=source_format),
            parsed_item("Catalog", "Товары", "attribute", "Артикул", "Attributes/Артикул", content="article", source_format=source_format),
            parsed_item("Catalog", "Товары", "attribute", "Цвет", "Attributes/Цвет", content="color", source_format=source_format),
            parsed_item("Catalog", "Товары", "predefined_value", "Основной", "Predefined/Основной", content="predef", source_format=source_format),
            parsed_item("Catalog", "Товары", "form", "ФормаЭлемента", "Forms/ФормаЭлемента", content="form-v2", source_format=source_format, structure_level="binary"),
            parsed_item("CommonModule", "КастомИнструменты", PART_KIND_OBJECT, content="object", source_format=source_format),
            parsed_item("CommonModule", "КастомИнструменты", "module", "Module", "Modules/Module", content="module", source_format=source_format),
            parsed_item("CommonForm", "КастомФорма", PART_KIND_OBJECT, content="object", source_format=source_format),
            parsed_item("CommonForm", "КастомФорма", "form", "Форма", "Forms/Форма", content="form", source_format=source_format),
            parsed_item("CommonForm", "КастомФорма", "form_element", "Поле", "Forms/Форма/Elements/Поле", content="field", source_format=source_format),
        ],
        source_format=source_format,
    )


class CustomMetadataInventoryTest(unittest.TestCase):
    def test_full_mode_includes_object_and_part_differences(self) -> None:
        rows = compare_snapshots(typical_snapshot_fixture(), customer_snapshot_fixture(), COMPARISON_MODE_FULL)
        by_coordinate = {(row.metadata_full_name, row.part_kind, row.part_name): row for row in rows}

        self.assertEqual(by_coordinate[("Catalog.Товары", "attribute", "Цвет")].change_type, CHANGE_TYPE_ADDED)
        self.assertEqual(by_coordinate[("Catalog.Товары", "predefined_value", "Основной")].change_type, CHANGE_TYPE_ADDED)
        self.assertEqual(by_coordinate[("Catalog.Товары", "form", "ФормаЭлемента")].change_type, CHANGE_TYPE_MODIFIED)
        self.assertEqual(by_coordinate[("Document.Заказ", PART_KIND_OBJECT, "")].change_type, "removed")
        self.assertEqual(by_coordinate[("Catalog.Товары", "attribute", "Артикул")].change_type, CHANGE_TYPE_UNCHANGED)
        self.assertTrue(all(row.comparison_mode == COMPARISON_MODE_FULL for row in rows))

    def test_reduced_mode_includes_new_top_level_objects_with_contents_only(self) -> None:
        rows = compare_snapshots(typical_snapshot_fixture(), customer_snapshot_fixture(), COMPARISON_MODE_REDUCED)
        coordinates = {(row.metadata_full_name, row.part_kind, row.part_name) for row in rows}

        self.assertEqual(
            coordinates,
            {
                ("CommonModule.КастомИнструменты", PART_KIND_OBJECT, ""),
                ("CommonModule.КастомИнструменты", "module", "Module"),
                ("CommonForm.КастомФорма", PART_KIND_OBJECT, ""),
                ("CommonForm.КастомФорма", "form", "Форма"),
                ("CommonForm.КастомФорма", "form_element", "Поле"),
            },
        )
        self.assertTrue(all(row.change_type == CHANGE_TYPE_ADDED for row in rows))
        self.assertTrue(all(row.comparison_mode == COMPARISON_MODE_REDUCED for row in rows))

    def test_parser_diagnostics_are_propagated_and_incomplete_snapshots_fail(self) -> None:
        customer = customer_snapshot_fixture()
        diagnostic_item = next(item for item in customer.items if item.part_kind == "module")
        diagnostic_item.diagnostics.append({"code": "fixture_warning", "severity": "warning", "message": "Fixture parser warning"})

        rows = compare_snapshots(typical_snapshot_fixture(), customer, COMPARISON_MODE_REDUCED)

        self.assertTrue(any(diag.get("code") == "fixture_warning" for row in rows for diag in row.diagnostics))
        with self.assertRaisesRegex(InventoryError, "Parser snapshot has no items"):
            compare_snapshots(snapshot_fixture([]), customer, COMPARISON_MODE_FULL)

    def test_item_keys_are_source_format_neutral(self) -> None:
        xml_item = parsed_item("Catalog", "Товары", "attribute", "Цвет", "Attributes/Цвет", source_format="xml-bsl")
        unpacked_item = parsed_item("Catalog", "Товары", "attribute", "Цвет", "Attributes/Цвет", source_format="v8unpack")

        self.assertEqual(inventory_item_key(xml_item), inventory_item_key(unpacked_item))
        rows = compare_snapshots(
            snapshot_fixture([xml_item], source_format="xml-bsl"),
            snapshot_fixture([unpacked_item], source_format="v8unpack"),
            COMPARISON_MODE_FULL,
        )
        self.assertEqual(rows[0].source_format, "mixed")

    def test_build_command_writes_inventory_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            typical_path = root / "typical.snapshot.json"
            customer_path = root / "customer.snapshot.json"
            write_snapshot(typical_snapshot_fixture(), typical_path)
            write_snapshot(customer_snapshot_fixture(), customer_path)

            result = build_command(
                argparse.Namespace(
                    repo_path=str(root),
                    typical_snapshot=str(typical_path),
                    customer_snapshot=str(customer_path),
                    typical_source_root="",
                    customer_source_root="",
                    source_format="xml-bsl",
                    comparison_mode=COMPARISON_MODE_FULL,
                    output_dir="analysis/custom-metadata",
                    strict_reconciliation=False,
                )
            )

            self.assertEqual(result, 0)
            for relative in ("index.jsonl", "index.csv", "object-summary.json", "source-balance.json", "build-metadata.json"):
                self.assertTrue((root / "analysis/custom-metadata" / relative).exists())
            self.assertEqual(validate_inventory(root, root / "analysis/custom-metadata"), [])
            build_metadata = json.loads((root / "analysis/custom-metadata" / BUILD_METADATA_JSON).read_text(encoding="utf-8"))
            self.assertEqual(build_metadata["snapshots"]["typical"]["schema_version"], PARSER_SCHEMA_VERSION)
            self.assertEqual(build_metadata["snapshots"]["customer"]["schema_version"], PARSER_SCHEMA_VERSION)

    def test_build_inventory_enriches_reconciliation_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "analysis/indexes").mkdir(parents=True)
            (root / "analysis/indexes/diff-inventory.csv").write_text(
                "diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes\n"
                "D-1,parser,added,path,Catalog,Catalog.Товары,metadata,F-1,custom,high,mapped_to_feature,summary,analysis/features/F-1/evidence.csv#E-1,\n",
                encoding="utf-8",
            )
            (root / "analysis/indexes/feature-map.csv").write_text(
                "feature_id,title,domain,source_bucket,classification,confidence,status,owner,summary,evidence_pack_path,open_questions_path,outputs,notes\n"
                "F-1,Feature,domain,bucket,custom,high,confirmed,codex,summary,analysis/features/F-1/evidence.csv,analysis/features/F-1/open-questions.md,,\n",
                encoding="utf-8",
            )

            result = build_inventory(root, typical_snapshot_fixture(), customer_snapshot_fixture(), COMPARISON_MODE_FULL, root / "analysis/custom-metadata")

            linked = [row for row in result["rows"] if row.metadata_full_name == "Catalog.Товары"]
            self.assertTrue(any(row.reconciliation.get("status") == "linked" for row in linked))
            artifact_paths = {
                path
                for row in linked
                for path in row.reconciliation.get("links", {}).get("artifact_paths", [])
            }
            self.assertIn("analysis/indexes/diff-inventory.csv#D-1", artifact_paths)
            self.assertIn("analysis/features/F-1/evidence.csv#E-1", artifact_paths)
            self.assertIn("analysis/indexes/feature-map.csv#F-1", artifact_paths)

    def test_exporters_read_inventory_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = compare_snapshots(typical_snapshot_fixture(), customer_snapshot_fixture(), COMPARISON_MODE_FULL)
            rows[0].reconciliation = {"status": "linked", "links": {"feature_ids": ["F-1"]}}
            write_inventory(root, root / "analysis/custom-metadata", rows, COMPARISON_MODE_FULL, typical_snapshot_fixture(), customer_snapshot_fixture())

            markdown_result = export_command(argparse.Namespace(repo_path=str(root), output_dir="analysis/custom-metadata", format="markdown"))
            spreadsheet_result = export_command(argparse.Namespace(repo_path=str(root), output_dir="analysis/custom-metadata", format="spreadsheet"))

            self.assertEqual(markdown_result, 0)
            self.assertEqual(spreadsheet_result, 0)
            markdown = (root / "analysis/custom-metadata/reports/custom-metadata.md").read_text(encoding="utf-8")
            self.assertIn("## Catalog", markdown)
            self.assertIn("### Catalog.Товары", markdown)
            self.assertIn("#### Feature F-1 / linked", markdown)
            self.assertTrue((root / "analysis/custom-metadata/reports/custom-metadata.xlsx").exists())

    def test_spreadsheet_export_uses_user_facing_russian_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = compare_snapshots(typical_snapshot_fixture(), customer_snapshot_fixture(), COMPARISON_MODE_FULL)
            write_inventory(root, root / "analysis/custom-metadata", rows, COMPARISON_MODE_FULL, typical_snapshot_fixture(), customer_snapshot_fixture())

            result = export_command(argparse.Namespace(repo_path=str(root), output_dir="analysis/custom-metadata", format="spreadsheet"))

            self.assertEqual(result, 0)
            values = read_xlsx_values(root / "analysis/custom-metadata/reports/custom-metadata.xlsx")
            self.assertEqual(values[0][:10], ["ID строки", "Вид объекта 1С", "Имя объекта", "Полное имя объекта", "Часть объекта", "Имя части", "Путь части", "Тип изменения", "Статус", "Статус сверки"])
            added_predefined_rows = [row for row in values[1:] if row[1] == "Справочник" and row[4] == "Предопределенный элемент"]
            self.assertEqual(len(added_predefined_rows), 1)
            self.assertEqual(added_predefined_rows[0][3], "Справочник.Товары")
            self.assertEqual(added_predefined_rows[0][7], "Добавлено")
            self.assertEqual(added_predefined_rows[0][8], "Нетиповое")

    def test_exporters_write_reports_next_to_selected_inventory_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory_dir = root / "tmp-inventory"
            rows = compare_snapshots(typical_snapshot_fixture(), customer_snapshot_fixture(), COMPARISON_MODE_FULL)
            write_inventory(root, inventory_dir, rows, COMPARISON_MODE_FULL, typical_snapshot_fixture(), customer_snapshot_fixture())

            result = export_command(argparse.Namespace(repo_path=str(root), output_dir="tmp-inventory", format="markdown"))

            self.assertEqual(result, 0)
            self.assertTrue((inventory_dir / "reports/custom-metadata.md").exists())
            self.assertFalse((root / "analysis/custom-metadata/reports/custom-metadata.md").exists())

    def test_validation_detects_duplicate_and_invalid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_dir = root / "analysis/custom-metadata"
            rows = compare_snapshots(typical_snapshot_fixture(), customer_snapshot_fixture(), COMPARISON_MODE_FULL)
            write_inventory(root, output_dir, rows, COMPARISON_MODE_FULL, typical_snapshot_fixture(), customer_snapshot_fixture())
            row = rows[0].to_dict()
            row["schema_version"] = "bad"
            with (output_dir / INDEX_JSONL).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

            errors = validate_inventory(root, output_dir)

            self.assertTrue(any("duplicate item_key" in error for error in errors))
            self.assertTrue(any("unsupported schema_version" in error for error in errors))

    def test_validation_requires_snapshot_pair_build_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_dir = root / "analysis/custom-metadata"
            rows = compare_snapshots(typical_snapshot_fixture(), customer_snapshot_fixture(), COMPARISON_MODE_FULL)
            write_inventory(root, output_dir, rows, COMPARISON_MODE_FULL, typical_snapshot_fixture(), customer_snapshot_fixture())
            self.assertEqual(validate_inventory(root, output_dir), [])

            (output_dir / BUILD_METADATA_JSON).unlink()
            errors = validate_inventory(root, output_dir)
            self.assertTrue(any("Missing custom metadata artifact: " in error and BUILD_METADATA_JSON in error for error in errors))

            write_inventory(root, output_dir, rows, COMPARISON_MODE_FULL, typical_snapshot_fixture(), customer_snapshot_fixture())
            build_metadata = json.loads((output_dir / BUILD_METADATA_JSON).read_text(encoding="utf-8"))
            build_metadata["snapshots"]["customer"]["schema_version"] = "bad"
            (output_dir / BUILD_METADATA_JSON).write_text(json.dumps(build_metadata, ensure_ascii=False) + "\n", encoding="utf-8")
            errors = validate_inventory(root, output_dir)
            self.assertTrue(any("customer snapshot has incompatible schema_version" in error for error in errors))

    def test_strict_reconciliation_reduced_ignores_only_mode_filtered_part_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "analysis/indexes").mkdir(parents=True)
            (root / "analysis/indexes/diff-inventory.csv").write_text(
                "diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes\n"
                "D-1,parser,added,Catalog/Товары/Attributes/Цвет.json,Catalog,Catalog.Товары,metadata,F-1,custom,high,mapped_to_feature,summary,,\n"
                "D-2,parser,added,Document/Новый/Document.json,Document,Document.Новый,metadata,F-2,custom,high,mapped_to_feature,summary,,\n",
                encoding="utf-8",
            )
            reduced_rows = compare_snapshots(typical_snapshot_fixture(), customer_snapshot_fixture(), COMPARISON_MODE_REDUCED)
            write_inventory(root, root / "reduced", reduced_rows, COMPARISON_MODE_REDUCED, typical_snapshot_fixture(), customer_snapshot_fixture())
            full_rows = compare_snapshots(typical_snapshot_fixture(), customer_snapshot_fixture(), COMPARISON_MODE_FULL)
            write_inventory(root, root / "full", full_rows, COMPARISON_MODE_FULL, typical_snapshot_fixture(), customer_snapshot_fixture())

            reduced_errors = validate_inventory(root, root / "reduced", strict_reconciliation=True)
            self.assertFalse(any("D-1" in error for error in reduced_errors))
            self.assertTrue(any("D-2" in error for error in reduced_errors))
            self.assertTrue(validate_inventory(root, root / "full", strict_reconciliation=True))

            (root / "full/reconciliation-exceptions.csv").write_text("diff_id,reason\nD-2,accepted external row\n", encoding="utf-8")
            self.assertEqual(validate_inventory(root, root / "full", strict_reconciliation=True), [])
            (root / "reduced/reconciliation-exceptions.csv").write_text("diff_id,reason\nD-2,accepted external row\n", encoding="utf-8")
            self.assertEqual(validate_inventory(root, root / "reduced", strict_reconciliation=True), [])

    def test_strict_reconciliation_requires_matching_part_level_inventory_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "analysis/indexes").mkdir(parents=True)
            (root / "analysis/indexes/diff-inventory.csv").write_text(
                "diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes\n"
                "D-ATTR,parser,added,Catalog/Товары/Attributes/Цвет.json,Catalog,Catalog.Товары,metadata,F-1,custom,high,mapped_to_feature,summary,,\n",
                encoding="utf-8",
            )
            object_only_typical = snapshot_fixture([parsed_item("Catalog", "Товары", PART_KIND_OBJECT, content="catalog")])
            object_only_customer = snapshot_fixture([parsed_item("Catalog", "Товары", PART_KIND_OBJECT, content="catalog")])
            object_only_rows = compare_snapshots(object_only_typical, object_only_customer, COMPARISON_MODE_FULL)
            write_inventory(root, root / "object-only", object_only_rows, COMPARISON_MODE_FULL, object_only_typical, object_only_customer)

            object_only_errors = validate_inventory(root, root / "object-only", strict_reconciliation=True)

            self.assertTrue(any("D-ATTR" in error for error in object_only_errors))

            full_rows = compare_snapshots(typical_snapshot_fixture(), customer_snapshot_fixture(), COMPARISON_MODE_FULL)
            write_inventory(root, root / "full", full_rows, COMPARISON_MODE_FULL, typical_snapshot_fixture(), customer_snapshot_fixture())

            self.assertEqual(validate_inventory(root, root / "full", strict_reconciliation=True), [])


if __name__ == "__main__":
    unittest.main()
