from __future__ import annotations

import json
import gc
from pathlib import Path
import tracemalloc

import pytest

from one_c_autoresearch.extension_analyzer import (
    AnalyzerDiagnosticError,
    KINDS,
    analyze_role_union,
    compare_snapshots,
    comparison_id,
    parse_bsl_methods,
    parse_component,
    resolve_dependency,
    target_coverage,
)


UUID = "471acdde-293c-497c-bd55-e6ab48d98dc4"
FIXTURES = Path(__file__).parent / "fixtures/extension-semantic"


def _component(root: Path, representation: str, *, active: bool = True) -> Path:
    root.mkdir(parents=True)
    (root / "component-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "kind": "extension",
                "uuid": UUID,
                "name": "Demo",
                "version": "1.0",
                "active": active,
                "representation_schema": representation,
            }
        ),
        encoding="utf-8",
    )
    if representation == "xml-hierarchical/v1":
        metadata = root / "Catalogs/Products.xml"
        metadata.parent.mkdir(parents=True)
        metadata.write_text(
            """<?xml version="1.0"?>
<MetaDataObject><Catalog uuid="11111111-1111-1111-1111-111111111111">
<Properties><ObjectBelonging>Own</ObjectBelonging><Name>Products</Name></Properties>
</Catalog></MetaDataObject>""",
            encoding="utf-8",
        )
        module = root / "Catalogs/Products/Ext/ObjectModule.bsl"
    else:
        metadata = root / "Catalog/Products/Catalog.json"
        metadata.parent.mkdir(parents=True)
        (metadata.parent / "Catalog.id.json").write_text(
            '{"uuid":"11111111-1111-1111-1111-111111111111"}',
            encoding="utf-8",
        )
        metadata.write_text('{"name":"Products"}', encoding="utf-8")
        (root / "ConfigurationExtension.json").write_text(
            '{"compatibility_version":"","name":"Demo"}',
            encoding="utf-8",
        )
        module = root / "Catalog/Products/Catalog.obj.bsl"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        '&После("ПриЗаписи")\nПроцедура AfterWrite(Value, Optional = 1) Экспорт\nКонецПроцедуры\n',
        encoding="utf-8",
    )
    return root


def test_lexical_bsl_parser_ignores_comments_and_strings_and_rejects_preprocessor() -> None:
    text = """
// &Перед("Ложная")
Value = "&Вместо(""Ложная"")";
&После("ПриЗаписи")
Асинх Процедура Обработать(Значение, Опция = 1) Экспорт
КонецПроцедуры
"""
    assert parse_bsl_methods(text, "ObjectModule") == [
        {
            "annotation_kind": "after",
            "annotation_target": "призаписи",
            "async": True,
            "export": True,
            "method_kind": "procedure",
            "module_context": "objectmodule",
            "name": "обработать",
            "parameters": ["значение", "опция"],
        }
    ]
    with pytest.raises(ValueError, match="unsupported_bsl_structure"):
        parse_bsl_methods("#Если Сервер Тогда\nПроцедура X()\nКонецПроцедуры\n#КонецЕсли", "m")
    with pytest.raises(ValueError, match="unsupported_bsl_structure"):
        parse_bsl_methods("Процедура X(\n Value)\nКонецПроцедуры", "m")
    with pytest.raises(ValueError, match="unsupported_bsl_structure"):
        parse_bsl_methods('&После(Target)\nПроцедура X()\nКонецПроцедуры', "m")


@pytest.mark.parametrize("representation", ["xml-hierarchical/v1", "v8unpack/v1"])
def test_supported_adapters_normalize_to_same_contract(tmp_path: Path, representation: str) -> None:
    rows = parse_component(_component(tmp_path / representation.split("/")[0], representation), representation, UUID)
    assert {(row["object_scope"], row["intervention_kind"]) for row in rows} >= {
        ("owned", "metadata_change"),
        ("owned", "object_definition"),
        ("owned", "method_extension"),
    }
    assert all(row["extension_uuid"] == UUID and row["evidence"] for row in rows)


def test_equivalent_representations_have_equal_semantic_fingerprints() -> None:
    xml = parse_component(FIXTURES / "xml-hierarchical", "xml-hierarchical/v1", UUID)
    unpack = parse_component(FIXTURES / "v8unpack", "v8unpack/v1", UUID)
    assert [(x["intervention_key"], x["structural_fingerprint"]) for x in xml] == [
        (x["intervention_key"], x["structural_fingerprint"]) for x in unpack
    ]


def test_derived_manifest_drift_does_not_create_root_diffs(tmp_path: Path) -> None:
    before_root = _component(tmp_path / "before", "xml-hierarchical/v1")
    after_root = _component(tmp_path / "after", "xml-hierarchical/v1")
    after_manifest = json.loads((after_root / "component-manifest.json").read_text(encoding="utf-8"))
    after_manifest["payload_fingerprint"] = "sha256:" + "f" * 64
    (after_root / "component-manifest.json").write_text(json.dumps(after_manifest), encoding="utf-8")
    before = {UUID: parse_component(before_root, "xml-hierarchical/v1", UUID)}
    after = {UUID: parse_component(after_root, "xml-hierarchical/v1", UUID)}
    assert compare_snapshots(before, after, "CMP-X") == []


def test_method_body_change_is_not_hidden_by_unchanged_signature(tmp_path: Path) -> None:
    before_root = _component(tmp_path / "before", "xml-hierarchical/v1")
    after_root = _component(tmp_path / "after", "xml-hierarchical/v1")
    module = after_root / "Catalogs/Products/Ext/ObjectModule.bsl"
    module.write_text(module.read_text(encoding="utf-8").replace("КонецПроцедуры", "Value = 2;\nКонецПроцедуры"), encoding="utf-8")

    rows = compare_snapshots(
        {UUID: parse_component(before_root, "xml-hierarchical/v1", UUID)},
        {UUID: parse_component(after_root, "xml-hierarchical/v1", UUID)},
        "CMP-X",
    )

    assert [(row["change_type"], row["intervention_kind"]) for row in rows] == [("modified", "method_extension")]


def test_adopted_object_with_exact_role_base_identity_is_compatible(tmp_path: Path) -> None:
    root = _component(tmp_path / "adopted", "xml-hierarchical/v1")
    metadata = root / "Catalogs/Products.xml"
    metadata.write_text(
        """<MetaDataObject><Catalog uuid="11111111-1111-1111-1111-111111111111">
<Properties><ObjectBelonging>Adopted</ObjectBelonging><Name>Products</Name></Properties>
</Catalog></MetaDataObject>""",
        encoding="utf-8",
    )
    detail = compare_snapshots({}, {UUID: parse_component(root, "xml-hierarchical/v1", UUID)}, "CMP-X")[0]
    dependency = resolve_dependency(
        detail,
        "target_cf",
        "adopted_object",
        detail["affected_base_identity"],
        [{"identity": "catalog.products", "evidence": []}],
    )
    assert detail["affected_base_identity"] == "catalog.products"
    assert dependency["outcome"] == "present_compatible"
    assert dependency["diagnostic_code"] == ""


def test_dependency_outcomes_cover_changed_missing_unresolved_and_cross_extension(tmp_path: Path) -> None:
    root = _component(tmp_path / "dependency-outcomes", "xml-hierarchical/v1")
    detail = compare_snapshots(
        {},
        {UUID: parse_component(root, "xml-hierarchical/v1", UUID)},
        "CMP-X",
    )[0]
    detail["affected_base_identity"] = "catalog.products"
    assert resolve_dependency(
        detail, "target_cf", "adopted_object", "catalog.products",
        [{"identity": "catalog.renamed", "evidence": []}],
    )["outcome"] == "present_changed"
    assert resolve_dependency(
        detail, "target_cf", "adopted_object", "catalog.products", [],
    )["outcome"] == "missing"
    unresolved = resolve_dependency(
        detail, "target_cf", "adopted_object", "catalog.products",
        [{"identity": "catalog.one"}, {"identity": "catalog.two"}],
    )
    assert (unresolved["outcome"], unresolved["diagnostic_code"]) == (
        "unresolved",
        "unresolved_dependency",
    )
    cross = resolve_dependency(
        detail, "target_cf", "adopted_object", "catalog.products", [],
        [{"identity": "catalog.products"}],
    )
    assert (cross["outcome"], cross["diagnostic_code"]) == (
        "unresolved",
        "unresolved_cross_extension",
    )


def test_explicit_metadata_reference_becomes_base_reference(tmp_path: Path) -> None:
    root = _component(tmp_path / "reference", "xml-hierarchical/v1")
    (root / "Catalogs/Products.xml").write_text(
        """<MetaDataObject xmlns:xr="urn:xr" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<Catalog uuid="11111111-1111-1111-1111-111111111111"><Properties>
<ObjectBelonging>Own</ObjectBelonging><Name>Products</Name>
<DefaultRole xsi:type="xr:MDObjectRef">Role.Users</DefaultRole>
</Properties></Catalog></MetaDataObject>""",
        encoding="utf-8",
    )
    rows = parse_component(root, "xml-hierarchical/v1", UUID)
    reference = next(row for row in rows if row["intervention_kind"] == "base_reference")
    assert reference["affected_base_identity"] == "role.users"


def test_closed_intervention_kinds_and_adopted_form_method_are_emitted(tmp_path: Path) -> None:
    root = _component(tmp_path / "all-kinds", "xml-hierarchical/v1")
    catalog = root / "Catalogs/Products.xml"
    catalog.write_text(
        """<MetaDataObject xmlns:xr="urn:xr" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<Catalog uuid="11111111-1111-1111-1111-111111111111"><Properties>
<ObjectBelonging>Adopted</ObjectBelonging><Name>Products</Name>
<DefaultRole xsi:type="xr:MDObjectRef">Role.Administrators</DefaultRole>
</Properties></Catalog></MetaDataObject>""",
        encoding="utf-8",
    )
    files = {
        "Catalogs/Products/Forms/Card.xml": "<MetaDataObject><Form uuid=\"22222222-2222-2222-2222-222222222222\"><Properties><Name>Card</Name></Properties></Form></MetaDataObject>",
        "Catalogs/Products/Commands/Run.xml": "<MetaDataObject><Command uuid=\"33333333-3333-3333-3333-333333333333\"><Properties><Name>Run</Name></Properties></Command></MetaDataObject>",
        "Roles/Users.xml": "<MetaDataObject><Role uuid=\"44444444-4444-4444-4444-444444444444\"><Properties><Name>Users</Name></Properties></Role></MetaDataObject>",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (root / "Catalogs/Products/Ext/ObjectModule.bsl").write_text(
        '&Вместо("ПриЗаписи")\nПроцедура ВместоЗаписи()\nКонецПроцедуры\n'
        '&После("ПриЗаписи")\nПроцедура ПослеЗаписи()\nКонецПроцедуры\n',
        encoding="utf-8",
    )
    module = root / "CommonModules/Helpers/Ext/Module.bsl"
    module.parent.mkdir(parents=True)
    module.write_text("Value = 1;\n", encoding="utf-8")
    rows = parse_component(root, "xml-hierarchical/v1", UUID)
    assert {row["intervention_kind"] for row in rows} == KINDS
    assert {
        row["intervention_kind"]
        for row in rows
        if row["object_scope"] == "adopted"
    } >= {"form_change", "method_interception"}


def test_v8unpack_version_string_is_not_a_metadata_reference(tmp_path: Path) -> None:
    root = _component(tmp_path / "version", "v8unpack/v1")
    metadata = root / "Catalog/Products/Catalog.json"
    metadata.write_text('{"name":"Products","version":"1.0"}', encoding="utf-8")
    assert not [
        row for row in parse_component(root, "v8unpack/v1", UUID)
        if row["intervention_kind"] == "base_reference"
    ]


def test_v8unpack_adoption_uses_uuid_and_name_and_conflicts_remain_unresolved(
    tmp_path: Path,
) -> None:
    root = _component(tmp_path / "adopted-v8", "v8unpack/v1")
    uuid_key = "uuid:11111111-1111-1111-1111-111111111111"
    exact = {"identity": "catalog.products", "evidence": []}
    rows = parse_component(
        root,
        "v8unpack/v1",
        UUID,
        base_index={uuid_key: [exact], "catalog.products": [exact]},
    )
    assert next(
        row for row in rows if row["intervention_kind"] == "object_definition"
    )["object_scope"] == "adopted"
    analysis = analyze_role_union(
        {"vendor_baseline": {}, "target_cf": {UUID: root}, "next_vendor": {}},
        "sha256:" + "a" * 64,
        main_config_indexes={
            "target_cf": {
                uuid_key: [{"identity": "catalog.renamed", "evidence": []}],
                "catalog.products": [exact],
            }
        },
    )
    assert any(
        row["outcome"] == "unresolved"
        and row["diagnostic_code"] == "unresolved_dependency"
        for row in analysis["dependency_rows"]
    )


def test_role_union_compare_and_target_coverage(tmp_path: Path) -> None:
    customer = _component(tmp_path / "customer", "xml-hierarchical/v1")
    target = _component(tmp_path / "target", "v8unpack/v1")
    analysis = analyze_role_union(
        {
            "vendor_baseline": {},
            "target_cf": {UUID: customer},
            "next_vendor": {UUID: target},
        },
        "sha256:" + "a" * 64,
    )
    customer_diffs = [row for row in analysis["detail_rows"] if row["after_role"] == "target_cf"]
    target_diffs = [row for row in analysis["detail_rows"] if row["after_role"] == "next_vendor"]
    assert customer_diffs and {row["change_type"] for row in customer_diffs} == {"added"}
    assert all(target_coverage(row, target_diffs)["coverage"] == "covered_by_vendor" for row in customer_diffs)
    assert analysis == analyze_role_union(
        {
            "next_vendor": {UUID: target},
            "target_cf": {UUID: customer},
            "vendor_baseline": {},
        },
        "sha256:" + "a" * 64,
    )
    assert set(analysis) == {
        "adapter_versions",
        "component_bindings",
            "dependency_rows",
            "detail_rows",
            "extension_analyzer_version",
            "path_coverage",
            "physical_rows",
            "semantic_rows",
        }


def test_role_subsets_added_deleted_unchanged_and_next_only(tmp_path: Path) -> None:
    baseline = _component(tmp_path / "baseline", "xml-hierarchical/v1")
    customer = _component(tmp_path / "customer", "xml-hierarchical/v1")
    unchanged = analyze_role_union(
        {
            "vendor_baseline": {UUID: baseline},
            "target_cf": {UUID: customer},
            "next_vendor": {},
        },
        "sha256:" + "a" * 64,
    )
    assert not [
        row for row in unchanged["detail_rows"] if row["after_role"] == "target_cf"
    ]
    deleted = analyze_role_union(
        {
            "vendor_baseline": {UUID: baseline},
            "target_cf": {},
            "next_vendor": {},
        },
        "sha256:" + "a" * 64,
    )
    assert {
        row["change_type"]
        for row in deleted["detail_rows"]
        if row["after_role"] == "target_cf"
    } == {"deleted"}
    next_only = analyze_role_union(
        {
            "vendor_baseline": {},
            "target_cf": {},
            "next_vendor": {UUID: customer},
        },
        "sha256:" + "a" * 64,
    )
    assert not [
        row for row in next_only["detail_rows"] if row["after_role"] == "target_cf"
    ]
    assert [
        row for row in next_only["detail_rows"] if row["after_role"] == "next_vendor"
    ]


@pytest.mark.parametrize(
    ("field", "value", "sublocation"),
    [
        ("name", "Renamed", "extension.technical_name"),
        ("version", "2.0", "extension.version"),
        ("active", False, "extension.active"),
    ],
)
def test_extension_root_state_changes_are_independent(
    tmp_path: Path,
    field: str,
    value: object,
    sublocation: str,
) -> None:
    before_root = _component(tmp_path / "before", "xml-hierarchical/v1")
    after_root = _component(tmp_path / "after", "xml-hierarchical/v1")
    manifest_path = after_root / "component-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    rows = compare_snapshots(
        {UUID: parse_component(before_root, "xml-hierarchical/v1", UUID)},
        {UUID: parse_component(after_root, "xml-hierarchical/v1", UUID)},
        "CMP-X",
    )
    assert [row["structural_sublocation"] for row in rows] == [sublocation]


def test_known_binary_owner_is_semantic_and_unknown_binary_fails(tmp_path: Path) -> None:
    root = _component(tmp_path / "binary", "xml-hierarchical/v1")
    payload = root / "Catalogs/Products/Forms/Card.bin"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"\x00known")
    rows = parse_component(root, "xml-hierarchical/v1", UUID)
    assert any(
        row["intervention_kind"] == "form_change"
        and row["structural_sublocation"].endswith("card.bin")
        for row in rows
    )
    (root / "unknown.bin").write_bytes(b"\x00unknown")
    with pytest.raises(AnalyzerDiagnosticError, match="unaccounted_component_path"):
        parse_component(root, "xml-hierarchical/v1", UUID)


def test_adapter_reads_each_regular_file_once_and_honors_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = _component(tmp_path / "customer", "xml-hierarchical/v1")
    reads: dict[Path, int] = {}
    original = Path.read_bytes

    def counted(path: Path) -> bytes:
        resolved = path.resolve()
        if component.resolve() in resolved.parents:
            reads[resolved] = reads.get(resolved, 0) + 1
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", counted)
    analyze_role_union(
        {"vendor_baseline": {}, "target_cf": {UUID: component}, "next_vendor": {}},
        "sha256:" + "a" * 64,
    )
    assert reads and set(reads.values()) == {1}
    with pytest.raises(InterruptedError, match="cancelled"):
        analyze_role_union(
            {"vendor_baseline": {}, "target_cf": {UUID: component}, "next_vendor": {}},
            "sha256:" + "a" * 64,
            cancelled=lambda: True,
        )


def test_fail_closed_for_unsafe_xml_and_unknown_representation(tmp_path: Path) -> None:
    root = _component(tmp_path / "bad", "xml-hierarchical/v1")
    (root / "evil.xml").write_text('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>', encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe_xml"):
        parse_component(root, "xml-hierarchical/v1", UUID)
    with pytest.raises(ValueError, match="unsupported_representation"):
        parse_component(root, "edt-project/v1", UUID)
    linked = _component(tmp_path / "linked", "xml-hierarchical/v1")
    (linked / "unsafe").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe_component_path"):
        parse_component(linked, "xml-hierarchical/v1", UUID)


def test_invalid_text_malformed_xml_and_source_directives_fail_or_remain_data(
    tmp_path: Path,
) -> None:
    malformed = _component(tmp_path / "malformed", "xml-hierarchical/v1")
    (malformed / "Catalogs/Products.xml").write_text("<MetaDataObject>", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_xml"):
        parse_component(malformed, "xml-hierarchical/v1", UUID)
    invalid = _component(tmp_path / "invalid-text", "xml-hierarchical/v1")
    (invalid / "Catalogs/Products/Ext/ObjectModule.bsl").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ValueError, match="invalid_bsl_text"):
        parse_component(invalid, "xml-hierarchical/v1", UUID)
    inert = _component(tmp_path / "inert", "xml-hierarchical/v1")
    marker = tmp_path / "must-not-exist"
    (inert / "Catalogs/Products/Ext/ObjectModule.bsl").write_text(
        f'ЗапуститьПриложение("touch {marker}");\n',
        encoding="utf-8",
    )
    parse_component(inert, "xml-hierarchical/v1", UUID)
    assert not marker.exists()


def test_peak_raw_payload_memory_is_bounded_by_current_file(tmp_path: Path) -> None:
    peaks = []
    for count in (4, 16):
        root = _component(tmp_path / f"payloads-{count}", "xml-hierarchical/v1")
        payload_root = root / "Catalogs/Products/Forms"
        payload_root.mkdir(parents=True)
        for index in range(count):
            (payload_root / f"payload-{index:02}.bin").write_bytes(b"x" * 256_000)
        gc.collect()
        tracemalloc.start()
        first = parse_component(root, "xml-hierarchical/v1", UUID)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert first == parse_component(root, "xml-hierarchical/v1", UUID)
        peaks.append(peak)
    assert peaks[1] < peaks[0] * 3


def test_diagnostic_samples_are_bounded_and_report_total(tmp_path: Path) -> None:
    root = _component(tmp_path / "many-errors", "xml-hierarchical/v1")
    for index in range(150):
        (root / f"unknown-{index:03}.bin").write_bytes(b"x")
    with pytest.raises(AnalyzerDiagnosticError) as caught:
        parse_component(root, "xml-hierarchical/v1", UUID)
    assert caught.value.code == "unaccounted_component_path"
    assert caught.value.total_count == 150
    assert len(caught.value.samples) == 100
    assert caught.value.samples == sorted(caught.value.samples)


def test_closed_detail_dependency_and_path_coverage_contracts(tmp_path: Path) -> None:
    customer = _component(tmp_path / "customer", "xml-hierarchical/v1")
    cid = comparison_id("customer-customization", "vendor_baseline", "target_cf", "sha256:" + "a" * 64)
    raw = {
        "stable_diff_id": "DIF-RAW",
        "comparison_id": cid,
        "change_type": "added",
        "path": f"target_cf/extensions/{UUID}/Catalogs/Products.xml",
    }
    analysis = analyze_role_union(
        {"vendor_baseline": {}, "target_cf": {UUID: customer}, "next_vendor": {}},
        "sha256:" + "a" * 64,
        raw_rows=[raw],
    )
    detail = next(row for row in analysis["detail_rows"] if row["stable_diff_id"] in analysis["path_coverage"][0]["semantic_diff_ids"])
    assert set(detail) == {
        "schema_version", "stable_diff_id", "comparison_id", "before_role", "after_role",
        "extension_uuid", "change_type", "object_scope", "intervention_kind",
        "intervention_key", "extension_object_identity", "affected_base_identity",
        "symbol_identity", "structural_sublocation", "before_structural_fingerprint",
        "after_structural_fingerprint", "before_evidence_fingerprint",
        "after_evidence_fingerprint", "evidence", "dependency_ids", "diagnostic_codes",
    }
    dependency = resolve_dependency(detail, "target_cf", "base_object", "Catalog.Products", [])
    assert set(dependency) == {
        "schema_version", "dependency_id", "stable_diff_id", "role", "extension_uuid",
        "dependency_kind", "normalized_source_reference", "resolved_base_identity",
        "outcome", "evidence", "diagnostic_code",
    }
    assert dependency["outcome"] == "missing"
    assert analysis["path_coverage"] == [{
        "raw_diff_id": "DIF-RAW",
        "comparison_id": cid,
        "change_type": "added",
        "path": raw["path"],
        "semantic_diff_ids": analysis["path_coverage"][0]["semantic_diff_ids"],
        "noise_reason": "",
    }]
