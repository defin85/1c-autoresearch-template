from __future__ import annotations

import json
import argparse
import tempfile
import unittest
from pathlib import Path

from one_c_autoresearch.configuration_source_parser import (
    DEFAULT_SNAPSHOT_DIR,
    ParsedConfigurationSnapshot,
    default_snapshot_path,
    module_hash,
    parse_configuration_source,
    parse_command,
    read_snapshot,
    validate_snapshot,
    write_snapshot,
)


FIXTURES = Path(__file__).parent / "fixtures" / "configuration_source_parser"


def item_by_part(snapshot: ParsedConfigurationSnapshot, part_kind: str) -> list[dict]:
    return [item.to_dict() for item in snapshot.items if item.part_kind == part_kind]


class ConfigurationSourceParserTest(unittest.TestCase):
    def test_xml_bsl_parser_emits_objects_parts_modules_and_forms(self) -> None:
        snapshot = parse_configuration_source(FIXTURES / "xml_bsl", "xml-bsl")

        self.assertEqual(validate_snapshot(snapshot), [])
        self.assertEqual(snapshot.source_format, "xml-bsl")
        self.assertTrue(item_by_part(snapshot, "object"))
        self.assertTrue(item_by_part(snapshot, "attribute"))
        self.assertTrue(item_by_part(snapshot, "tabular_section"))
        self.assertTrue(item_by_part(snapshot, "command"))
        self.assertTrue(item_by_part(snapshot, "module"))

        forms = item_by_part(snapshot, "form")
        self.assertTrue(any(item["part_name"] == "ФормаЭлемента" and item["structure_level"] == "structured" for item in forms))
        self.assertTrue(any(item["part_name"] == "ОбычнаяФорма" and item["structure_level"] == "binary" for item in forms))
        self.assertTrue(any(item["part_name"] == "ПолеАртикул" for item in item_by_part(snapshot, "form_element")))
        self.assertFalse(any(item["part_kind"] == "form_element" and "ОбычнаяФорма" in item["part_path"] for item in snapshot.to_dict()["items"]))
        self.assertTrue(any(diag["code"] == "unknown_visible_part" for diag in snapshot.layout_diagnostics))
        self.assertTrue(any(item["part_kind"] == "unknown" and item["part_path"] == "Catalogs/Товары/Ext/Help.xml" for item in snapshot.to_dict()["items"]))

    def test_xml_bsl_parser_reads_ibcmd_ext_form_structure_and_module_coordinates(self) -> None:
        snapshot = parse_configuration_source(FIXTURES / "xml_bsl", "xml-bsl")

        self.assertEqual(validate_snapshot(snapshot), [])
        forms = [
            item
            for item in item_by_part(snapshot, "form")
            if item["metadata_full_name"] == "Catalog.Товары" and item["part_name"] == "ФормаЭлемента"
        ]
        self.assertEqual(len(forms), 1)
        form = forms[0]
        self.assertEqual(form["structure_level"], "structured")
        self.assertTrue(any(ref["path"] == "Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form.xml" for ref in form["source_refs"]))

        elements = [
            item
            for item in item_by_part(snapshot, "form_element")
            if item["metadata_full_name"] == "Catalog.Товары" and item["part_path"].startswith("Forms/ФормаЭлемента/Elements/")
        ]
        self.assertTrue(any(item["part_name"] == "ГруппаКоманднаяПанель" for item in elements))
        self.assertTrue(any(item["part_name"] == "КнопкаЗаписать" for item in elements))

        modules = [
            item
            for item in item_by_part(snapshot, "module")
            if any(ref["path"] == "Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form/Module.bsl" for ref in item["source_refs"])
        ]
        self.assertEqual(len(modules), 1)
        self.assertEqual(modules[0]["metadata_kind"], "Catalog")
        self.assertEqual(modules[0]["metadata_name"], "Товары")
        self.assertEqual(modules[0]["metadata_full_name"], "Catalog.Товары")
        self.assertEqual(modules[0]["part_name"], "ФормаЭлемента.Module")
        self.assertEqual(modules[0]["part_path"], "Forms/ФормаЭлемента/Modules/Module")

    def test_xml_bsl_parser_disambiguates_nested_child_object_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source_root = Path(temp)
            (source_root / "Catalogs").mkdir()
            (source_root / "Catalogs" / "Товары.xml").write_text(
                """
<MetaDataObject>
  <Catalog>
    <Properties><Name>Товары</Name></Properties>
    <ChildObjects>
      <Attribute><Properties><Name>ОбщийРеквизит</Name></Properties></Attribute>
      <TabularSection>
        <Properties><Name>Строки</Name></Properties>
        <ChildObjects>
          <Attribute><Properties><Name>ОбщийРеквизит</Name></Properties></Attribute>
        </ChildObjects>
      </TabularSection>
    </ChildObjects>
  </Catalog>
</MetaDataObject>
""",
                encoding="utf-8",
            )

            snapshot = parse_configuration_source(source_root, "xml-bsl")

        self.assertEqual(validate_snapshot(snapshot), [])
        attributes = [
            item
            for item in item_by_part(snapshot, "attribute")
            if item["part_name"] == "ОбщийРеквизит"
        ]
        self.assertEqual(
            sorted(item["part_path"] for item in attributes),
            ["Attributes/ОбщийРеквизит", "TabularSections/Строки/Attributes/ОбщийРеквизит"],
        )

    def test_xml_bsl_parser_does_not_inherit_form_element_names_from_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source_root = Path(temp)
            form_dir = source_root / "Catalogs" / "Товары" / "Forms" / "ФормаЭлемента"
            (source_root / "Catalogs").mkdir(parents=True)
            (source_root / "Catalogs" / "Товары.xml").write_text(
                "<MetaDataObject><Catalog><Properties><Name>Товары</Name></Properties></Catalog></MetaDataObject>",
                encoding="utf-8",
            )
            form_dir.mkdir(parents=True)
            (form_dir.with_suffix(".xml")).write_text(
                "<MetaDataObject><Form><Properties><Name>ФормаЭлемента</Name></Properties></Form></MetaDataObject>",
                encoding="utf-8",
            )
            (form_dir / "Ext").mkdir()
            (form_dir / "Ext" / "Form.xml").write_text(
                """
<Form>
  <Item>
    <Item>
      <Properties><Name>Поле</Name></Properties>
    </Item>
  </Item>
  <Item>
    <Properties><Name>Поле</Name></Properties>
  </Item>
</Form>
""",
                encoding="utf-8",
            )

            snapshot = parse_configuration_source(source_root, "xml-bsl")

        self.assertEqual(validate_snapshot(snapshot), [])
        elements = [
            item
            for item in item_by_part(snapshot, "form_element")
            if item["metadata_full_name"] == "Catalog.Товары"
        ]
        self.assertEqual(len(elements), 2)
        self.assertEqual(len({item["item_key"] for item in elements}), 2)

    def test_xml_bsl_parser_prefers_readable_form_structure_over_adjacent_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source_root = Path(temp)
            form_dir = source_root / "Catalogs" / "Товары" / "Forms" / "ФормаЭлемента"
            (source_root / "Catalogs").mkdir(parents=True)
            (source_root / "Catalogs" / "Товары.xml").write_text(
                "<MetaDataObject><Catalog><Properties><Name>Товары</Name></Properties></Catalog></MetaDataObject>",
                encoding="utf-8",
            )
            form_dir.mkdir(parents=True)
            (form_dir.with_suffix(".xml")).write_text(
                "<MetaDataObject><Form><Properties><Name>ФормаЭлемента</Name></Properties></Form></MetaDataObject>",
                encoding="utf-8",
            )
            (form_dir / "Ext").mkdir()
            (form_dir / "Ext" / "Form.xml").write_text(
                "<Form><Item><Properties><Name>Поле</Name></Properties></Item></Form>",
                encoding="utf-8",
            )
            (form_dir / "Ext" / "Form.bin").write_bytes(b"\x00\x01")

            snapshot = parse_configuration_source(source_root, "xml-bsl")

        self.assertEqual(validate_snapshot(snapshot), [])
        form = next(item for item in item_by_part(snapshot, "form") if item["part_name"] == "ФормаЭлемента")
        self.assertEqual(form["structure_level"], "structured")
        self.assertTrue(any(ref["path"].endswith("/Ext/Form.xml") for ref in form["source_refs"]))
        self.assertTrue(any(ref["path"].endswith("/Ext/Form.bin") for ref in form["source_refs"]))
        self.assertTrue(any(item["part_name"] == "Поле" for item in item_by_part(snapshot, "form_element")))
        self.assertFalse(any(diag["code"] == "binary_form_payload" for diag in form["diagnostics"]))

    def test_xml_bsl_parser_scopes_command_modules_to_parent_object(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source_root = Path(temp)
            for document_name in ("Первый", "Второй"):
                document_dir = source_root / "Documents" / document_name
                command_dir = document_dir / "Commands" / "ОбщаяКоманда" / "Ext"
                document_dir.mkdir(parents=True)
                (source_root / "Documents" / f"{document_name}.xml").write_text(
                    f"<MetaDataObject><Document><Properties><Name>{document_name}</Name></Properties></Document></MetaDataObject>",
                    encoding="utf-8",
                )
                command_dir.mkdir(parents=True)
                (document_dir / "Commands" / "ОбщаяКоманда.xml").write_text(
                    "<MetaDataObject><Command><Properties><Name>ОбщаяКоманда</Name></Properties></Command></MetaDataObject>",
                    encoding="utf-8",
                )
                (command_dir / "CommandModule.bsl").write_text("Процедура X()\nКонецПроцедуры\n", encoding="utf-8")

            snapshot = parse_configuration_source(source_root, "xml-bsl")

        self.assertEqual(validate_snapshot(snapshot), [])
        modules = [
            item
            for item in item_by_part(snapshot, "module")
            if item["part_name"] == "ОбщаяКоманда.CommandModule"
        ]
        self.assertEqual(
            sorted((item["metadata_full_name"], item["part_path"]) for item in modules),
            [
                ("Document.Второй", "Commands/ОбщаяКоманда/Modules/CommandModule"),
                ("Document.Первый", "Commands/ОбщаяКоманда/Modules/CommandModule"),
            ],
        )

    def test_v8unpack_parser_emits_same_model_and_role_rights(self) -> None:
        snapshot = parse_configuration_source(FIXTURES / "v8unpack", "v8unpack")

        self.assertEqual(validate_snapshot(snapshot), [])
        self.assertEqual(snapshot.source_format, "v8unpack")
        self.assertTrue(item_by_part(snapshot, "object"))
        self.assertTrue(item_by_part(snapshot, "module"))
        self.assertTrue(item_by_part(snapshot, "form"))
        self.assertTrue(item_by_part(snapshot, "form_element"))
        self.assertTrue(item_by_part(snapshot, "role_right"))
        self.assertTrue(any(item["part_kind"] == "unknown" for item in snapshot.to_dict()["items"]))
        self.assertFalse(any(item["part_kind"] == "unknown" and item["part_path"].endswith(".id.json") for item in snapshot.to_dict()["items"]))

    def test_v8unpack_parser_accounts_for_unsupported_form_related_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source_root = Path(temp)
            form_dir = source_root / "Catalog" / "Товары" / "CatalogForm" / "ФормаЭлемента"
            form_dir.mkdir(parents=True)
            (source_root / "Catalog" / "Товары" / "Catalog.json").write_text('{"name":"Товары"}', encoding="utf-8")
            (form_dir / "CatalogForm.json").write_text('{"name":"ФормаЭлемента"}', encoding="utf-8")
            (form_dir / "unsupported.weird").write_text("unsupported", encoding="utf-8")

            snapshot = parse_configuration_source(source_root, "v8unpack")

        self.assertTrue(any(item["part_kind"] == "unknown" and item["part_path"].endswith("unsupported.weird") for item in snapshot.to_dict()["items"]))
        self.assertTrue(any(diag["code"] == "unknown_visible_part" for item in snapshot.to_dict()["items"] for diag in item["diagnostics"]))

    def test_v8unpack_parser_skips_adjacent_binary_form_when_readable_form_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source_root = Path(temp)
            form_dir = source_root / "Catalog" / "Товары" / "CatalogForm" / "ФормаЭлемента"
            form_dir.mkdir(parents=True)
            (source_root / "Catalog" / "Товары" / "Catalog.json").write_text('{"name":"Товары"}', encoding="utf-8")
            (form_dir / "CatalogForm.json").write_text('{"name":"ФормаЭлемента"}', encoding="utf-8")
            (form_dir / "CatalogForm.bin").write_bytes(b"\x00\x01")

            snapshot = parse_configuration_source(source_root, "v8unpack")

        self.assertEqual(validate_snapshot(snapshot), [])
        self.assertTrue(any(item["part_kind"] == "form" and item["part_path"].endswith("CatalogForm.json") for item in snapshot.to_dict()["items"]))
        self.assertFalse(any(ref["path"].endswith("CatalogForm.bin") for item in snapshot.to_dict()["items"] for ref in item["source_refs"]))
        self.assertIn("Catalog/Товары/CatalogForm/ФормаЭлемента/CatalogForm.bin", snapshot.traversal["files_parsed"])

    def test_parser_snapshot_exposes_form_coverage_counts(self) -> None:
        snapshot = parse_configuration_source(FIXTURES / "xml_bsl", "xml-bsl")

        self.assertEqual(validate_snapshot(snapshot), [])
        coverage_counts = snapshot.traversal["coverage_counts"]
        self.assertEqual(coverage_counts["binary_forms"], 1)
        self.assertGreaterEqual(coverage_counts["form_elements"], 3)
        self.assertEqual(coverage_counts["form_modules"], 1)
        self.assertEqual(coverage_counts["structured_forms"], 1)
        self.assertGreaterEqual(coverage_counts["unknown_visible_parts"], 1)

    def test_parser_fixtures_cover_required_part_kinds(self) -> None:
        snapshots = [
            parse_configuration_source(FIXTURES / "xml_bsl", "xml-bsl"),
            parse_configuration_source(FIXTURES / "v8unpack", "v8unpack"),
        ]
        part_kinds = {item.part_kind for snapshot in snapshots for item in snapshot.items}

        for required in {
            "object",
            "module",
            "form",
            "form_element",
            "template",
            "role_right",
            "attribute",
            "tabular_section",
            "command",
            "scheduled_job",
            "subscription",
            "service",
            "predefined_value",
            "unknown",
        }:
            self.assertIn(required, part_kinds)

    def test_xml_bsl_parser_reads_namespaced_predefined_data_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source_root = Path(temp)
            (source_root / "ChartsOfAccounts").mkdir(parents=True)
            (source_root / "ChartsOfAccounts" / "Хозрасчетный.xml").write_text(
                "<MetaDataObject><ChartOfAccounts><Properties><Name>Хозрасчетный</Name></Properties></ChartOfAccounts></MetaDataObject>",
                encoding="utf-8",
            )
            predefined_dir = source_root / "ChartsOfAccounts" / "Хозрасчетный" / "Ext"
            predefined_dir.mkdir(parents=True)
            (predefined_dir / "Predefined.xml").write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<PredefinedData xmlns="http://v8.1c.ru/8.3/xcf/predef" xsi:type="ChartOfAccountsPredefinedItems" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Item id="parent-id">
    <Name>ОсновныеСредства</Name>
    <Code>01</Code>
    <Description>Основные средства</Description>
    <ChildItems>
      <Item id="child-id">
        <Name>ОСвОрганизации</Name>
        <Code>01.01</Code>
        <Description>Основные средства в организации</Description>
      </Item>
    </ChildItems>
  </Item>
  <Item id="duplicate-a">
    <Name>Повтор</Name>
    <Code>10</Code>
  </Item>
  <Item id="duplicate-b">
    <Name>Повтор</Name>
    <Code>11</Code>
  </Item>
</PredefinedData>
""",
                encoding="utf-8",
            )

            snapshot = parse_configuration_source(source_root, "xml-bsl")

        self.assertEqual(validate_snapshot(snapshot), [])
        predefined = [
            item.to_dict()
            for item in snapshot.items
            if item.part_kind == "predefined_value"
            and item.metadata_full_name == "ChartOfAccounts.Хозрасчетный"
        ]
        self.assertEqual(len(predefined), 4)
        self.assertTrue(any(item["part_name"] == "ОсновныеСредства" and item["part_path"] == "Predefined/ОсновныеСредства" for item in predefined))
        self.assertTrue(any(item["part_name"] == "ОСвОрганизации" and item["part_path"] == "Predefined/ОСвОрганизации" for item in predefined))
        self.assertEqual(
            sorted(item["part_path"] for item in predefined if item["part_name"] == "Повтор"),
            ["Predefined/Повтор/10", "Predefined/Повтор/11"],
        )

    def test_auto_detection_fails_closed_on_ambiguous_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            (temp_path / "Catalogs").mkdir()
            (temp_path / "Catalogs" / "Товары.xml").write_text(
                "<MetaDataObject><Catalog><Properties><Name>Товары</Name></Properties></Catalog></MetaDataObject>",
                encoding="utf-8",
            )
            (temp_path / "Role" / "Роль").mkdir(parents=True)
            (temp_path / "Role" / "Роль" / "Role.json").write_text('{"name":"Роль"}', encoding="utf-8")

            snapshot = parse_configuration_source(temp_path, "auto")

        errors = validate_snapshot(snapshot)
        self.assertTrue(any("ambiguous_source_layout" in error for error in errors))

    def test_missing_explicit_layout_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            snapshot = parse_configuration_source(Path(temp), "xml-bsl")

        errors = validate_snapshot(snapshot)
        self.assertTrue(any("xml_bsl_layout_not_found" in error for error in errors))

    def test_snapshot_validation_detects_duplicate_keys(self) -> None:
        snapshot = parse_configuration_source(FIXTURES / "xml_bsl", "xml-bsl")
        snapshot.items.append(snapshot.items[0])

        errors = validate_snapshot(snapshot)
        self.assertTrue(any("Duplicate item_key" in error for error in errors))

    def test_parser_generated_duplicate_semantic_coordinates_fail_validation(self) -> None:
        xml = "<MetaDataObject><Catalog><Properties><Name>Товары</Name></Properties></Catalog></MetaDataObject>"
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            (temp_path / "Catalogs").mkdir()
            (temp_path / "Catalogs" / "Первый.xml").write_text(xml, encoding="utf-8")
            (temp_path / "Catalogs" / "Второй.xml").write_text(xml, encoding="utf-8")

            snapshot = parse_configuration_source(temp_path, "xml-bsl")

        errors = validate_snapshot(snapshot)
        self.assertTrue(any("Duplicate item_key" in error for error in errors))
        self.assertTrue(any("duplicate_semantic_coordinate" in error for error in errors))

    def test_visible_unsupported_xml_bsl_files_are_diagnosed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            (temp_path / "Catalogs").mkdir()
            (temp_path / "Catalogs" / "Товары.xml").write_text(
                "<MetaDataObject><Catalog><Properties><Name>Товары</Name></Properties></Catalog></MetaDataObject>",
                encoding="utf-8",
            )
            (temp_path / "Catalogs" / "stray.txt").write_text("visible unsupported file", encoding="utf-8")

            snapshot = parse_configuration_source(temp_path, "xml-bsl")

        self.assertEqual(validate_snapshot(snapshot), [])
        self.assertTrue(any(diag["code"] == "unknown_visible_part" for diag in snapshot.layout_diagnostics))

    def test_snapshot_validation_detects_missing_source_reference(self) -> None:
        snapshot_data = parse_configuration_source(FIXTURES / "xml_bsl", "xml-bsl").to_dict()
        snapshot_data["items"][0]["source_refs"][0]["path"] = "Catalogs/Товары/Missing.xml"
        snapshot = ParsedConfigurationSnapshot.from_dict(snapshot_data)

        errors = validate_snapshot(snapshot)

        self.assertTrue(any("source reference is not covered by traversal" in error for error in errors))

    def test_snapshot_validation_requires_coverage_counts(self) -> None:
        snapshot_data = parse_configuration_source(FIXTURES / "xml_bsl", "xml-bsl").to_dict()
        del snapshot_data["traversal"]["coverage_counts"]
        snapshot = ParsedConfigurationSnapshot.from_dict(snapshot_data)

        errors = validate_snapshot(snapshot)

        self.assertTrue(any("lacks coverage_counts" in error for error in errors))

    def test_parse_command_fails_closed_on_unknown_visible_part(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            source_root = temp_path / "source"
            (source_root / "Catalog" / "Товары").mkdir(parents=True)
            (source_root / "Catalog" / "Товары" / "Catalog.json").write_text('{"name":"Товары"}', encoding="utf-8")
            (source_root / "Catalog" / "Товары" / "extra.weird").write_text("unsupported", encoding="utf-8")
            output = temp_path / "out.snapshot.json"

            result = parse_command(
                argparse.Namespace(
                    repo_path=str(temp_path),
                    source_root=str(source_root),
                    source_format="v8unpack",
                    output=str(output),
                    parse_only=False,
                )
            )

            self.assertEqual(result, 1)
            self.assertFalse(output.exists())

    def test_parse_command_writes_validated_snapshots_with_supported_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            for fixture_name, source_format in (("xml_bsl", "xml-bsl"), ("v8unpack", "v8unpack")):
                output = temp_path / f"{fixture_name}.snapshot.json"

                result = parse_command(
                    argparse.Namespace(
                        repo_path=str(temp_path),
                        source_root=str(FIXTURES / fixture_name),
                        source_format=source_format,
                        output=str(output),
                        parse_only=False,
                    )
                )

                self.assertEqual(result, 0)
                self.assertTrue(output.exists())
                self.assertEqual(validate_snapshot(read_snapshot(output)), [])

    def test_parser_output_is_deterministic_except_generation_time(self) -> None:
        first = parse_configuration_source(FIXTURES / "xml_bsl", "xml-bsl")
        second = parse_configuration_source(FIXTURES / "xml_bsl", "xml-bsl")

        self.assertEqual([item.to_dict() for item in first.items], [item.to_dict() for item in second.items])
        self.assertEqual(first.layout_diagnostics, second.layout_diagnostics)
        self.assertEqual(first.traversal, second.traversal)

    def test_bsl_hash_normalizes_line_endings(self) -> None:
        self.assertEqual(module_hash("Процедура X()\r\nКонецПроцедуры\r\n"), module_hash("Процедура X()\nКонецПроцедуры\n"))

    def test_persisted_snapshot_contract_round_trips(self) -> None:
        snapshot = parse_configuration_source(FIXTURES / "xml_bsl", "xml-bsl")
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            output = temp_path / "snapshot.json"

            write_snapshot(snapshot, output)
            loaded = read_snapshot(output)

            self.assertEqual(loaded.schema_version, snapshot.schema_version)
            self.assertEqual([item.to_dict() for item in loaded.items], [item.to_dict() for item in snapshot.items])
            self.assertIn(DEFAULT_SNAPSHOT_DIR, default_snapshot_path(temp_path, FIXTURES / "xml_bsl", "xml-bsl").as_posix())
            self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["items"])


if __name__ == "__main__":
    unittest.main()
