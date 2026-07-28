from pathlib import Path

import pytest

from one_c_autoresearch.contracts import canonical_json
from one_c_autoresearch.source_routing import analyze_forms, component_members, extension_scope, plan_groups, source_comparison_epoch


MANAGED = b'<Form xmlns="http://v8.1c.ru/8.3/xcf/logform"><Title>A</Title></Form>'
ORDINARY = b'<Form xmlns="http://v8.1c.ru/8.3/xcf/form"><Dialog>A</Dialog></Form>'
UNKNOWN = b'<Form xmlns="urn:future:form"><Node>A</Node></Form>'
FIXTURES = Path(__file__).parent / "fixtures/source-routing"


def component(tmp_path: Path, payloads: list[bytes]) -> Path:
    root = tmp_path / str(len(list(tmp_path.iterdir())))
    for index, payload in enumerate(payloads):
        path = root / f"Catalogs/Товары/Forms/Форма{index}/Ext/Form.xml"
        path.parent.mkdir(parents=True)
        path.write_bytes(payload)
    return root


def probe(root: Path, identifier: str) -> dict:
    return analyze_forms(root, identifier)


def test_form_probe_is_structural_bounded_and_repeatable(tmp_path: Path):
    root = component(tmp_path, [MANAGED, ORDINARY, UNKNOWN])
    first = probe(root, "target_cf:configuration")
    assert first == probe(root, "target_cf:configuration")
    assert first["form_counts"] == {"managed": 1, "ordinary": 1, "inconclusive": 1}
    assert "Node>A" not in str(first)
    root.joinpath("Catalogs/Товары/Forms/Форма0/Ext/Form.xml").write_bytes(
        MANAGED.replace(b"<Title>A</Title>", b"<Title>unrelated change</Title>")
    )
    assert first["probe_fingerprint"] == probe(root, "target_cf:configuration")["probe_fingerprint"]
    for index in range(25):
        root.joinpath("Catalogs/Товары/Forms/Форма0/Ext/Form.xml").write_bytes(
            MANAGED.replace(b"<Title>A</Title>", f"<Title>{index}</Title>".encode())
        )
        assert first["probe_fingerprint"] == probe(root, "target_cf:configuration")["probe_fingerprint"]


def test_unknown_payload_hash_distinguishes_content_and_links_are_rejected(tmp_path: Path):
    first = tmp_path / "first" / "Forms" / "A"; first.mkdir(parents=True)
    second = tmp_path / "second" / "Forms" / "A"; second.mkdir(parents=True)
    (first / "Form.xml").write_bytes(b"<broken>one")
    (second / "Form.xml").write_bytes(b"<damage>one")
    left = analyze_forms(tmp_path / "first", "c")["records"][0]["unknown_structure_sha256"]
    right = analyze_forms(tmp_path / "second", "c")["records"][0]["unknown_structure_sha256"]
    assert left != right
    (second / "Form.xml").write_bytes(b"<broken>two")
    assert left == analyze_forms(tmp_path / "second", "c")["records"][0]["unknown_structure_sha256"]
    linked = tmp_path / "linked" / "Forms" / "A"; linked.mkdir(parents=True)
    (linked / "Form.xml").symlink_to(first / "Form.xml")
    with pytest.raises(ValueError, match="link"):
        analyze_forms(tmp_path / "linked", "c")


def test_sanitized_exporter_fixtures_cover_closed_form_kinds():
    assert analyze_forms(FIXTURES / "managed", "c")["form_counts"]["managed"] == 1
    assert analyze_forms(FIXTURES / "ordinary", "c")["form_counts"]["ordinary"] == 1
    assert analyze_forms(FIXTURES / "unknown", "c")["form_counts"]["inconclusive"] == 1
    assert analyze_forms(FIXTURES / "mixed", "c")["form_counts"] == {"managed": 1, "ordinary": 1, "inconclusive": 0}
    assert analyze_forms(FIXTURES / "form-free", "c")["form_counts"] == {"managed": 0, "ordinary": 0, "inconclusive": 0}
    assert analyze_forms(FIXTURES / "extension", "c")["form_counts"]["ordinary"] == 1
    assert analyze_forms(FIXTURES / "epf-unpacked", "c")["form_counts"]["ordinary"] == 1
    assert analyze_forms(FIXTURES / "erf-unpacked", "c")["form_counts"]["managed"] == 1
    value = analyze_forms(FIXTURES / "mixed", "c")
    assert canonical_json(value) == canonical_json(analyze_forms(FIXTURES / "mixed", "c"))


def test_group_plan_escalates_all_roles_and_records_absence(tmp_path: Path):
    managed = probe(component(tmp_path, [MANAGED]), "vendor_baseline:configuration")
    ordinary = probe(component(tmp_path, [ORDINARY]), "target_cf:configuration")
    members = [
        {"routing_group_id": "configuration", "role": "vendor_baseline", "component_id": managed["component_id"], "kind": "configuration", "probe": managed},
        {"routing_group_id": "configuration", "role": "target_cf", "component_id": ordinary["component_id"], "kind": "configuration", "probe": ordinary},
    ]
    plan = plan_groups(members, "ibcmd", {"platform": "8.3.27.1989", "converter": "v8unpack 1.2.6"})
    group = plan["groups"][0]
    assert group["representation_schema"] == "v8unpack/v1"
    assert group["routing_reason"] == "ordinary_form_present"
    assert group["absent_roles"] == ["next_vendor"]


def test_external_routes_and_epoch_excludes_probe_fingerprints():
    members = [{"routing_group_id": "external:EXT-1", "role": "target_cf", "component_id": "target_cf:external:EXT-1", "kind": "epf"}]
    plan = plan_groups(members, "designer", {"platform": "8.5.4.1306", "converter": "v8unpack 1.2.6"})
    assert plan["groups"][0]["exporter"] == "verified-upload"
    assert plan["groups"][0]["routing_reason"] == "binary_container_requires_v8unpack"
    pointer = {"normalizer_version": "3", "source_contract_fingerprint": "sha256:" + "1" * 64}
    first = source_comparison_epoch(pointer, plan)
    plan["groups"][0]["probe_fingerprints"] = ["sha256:" + "a" * 64]
    assert source_comparison_epoch(pointer, plan) == first
    pointer["source_contract_fingerprint"] = "sha256:" + "2" * 64
    assert source_comparison_epoch(pointer, plan) != first


def test_component_members_group_extensions_by_uuid_and_external_by_ext_id():
    roles = {role: {"connection_profile": role} for role in ("vendor_baseline", "target_cf", "next_vendor")}
    connections = {
        "vendor_baseline": {"extensions": [{"uuid": "11111111-1111-1111-1111-111111111111", "name": "A"}]},
        "target_cf": {"extensions": []},
        "next_vendor": {"extensions": [{"uuid": "11111111-1111-1111-1111-111111111111", "name": "Renamed"}]},
    }
    contract = {"roles": roles, "extension_decisions": [{"uuid": "11111111-1111-1111-1111-111111111111", "decision": "include", "rationale": ""}], "artifacts": [{"role": "target_cf", "kind": "epf", "semantic_key": "Отчёт", "external_artifact_id": "EXT-ABC"}]}
    members = component_members(contract, connections)
    extension = [item for item in members if item["routing_group_id"].startswith("extension:")]
    assert {item["role"] for item in extension} == {"vendor_baseline", "next_vendor"}
    assert [item["routing_group_id"] for item in members if item["kind"] == "epf"] == ["external:EXT-ABC"]
    for item in extension:
        item["probe"] = analyze_forms(FIXTURES / "extension", item["component_id"])
    plan = plan_groups(extension, "ibcmd", {"platform": "8.3.27.1989", "converter": "v8unpack 1.2.6"})
    assert plan["groups"][0]["representation_schema"] == "v8unpack/v1"
    assert canonical_json(plan) == canonical_json(plan_groups(extension, "ibcmd", {"platform": "8.3.27.1989", "converter": "v8unpack 1.2.6"}))


def test_extension_scope_filters_members_and_preserves_dormant_decisions():
    roles = {role: {"connection_profile": role} for role in ("vendor_baseline", "target_cf", "next_vendor")}
    connections = {
        "vendor_baseline": {"extensions": [{"uuid": "11111111-1111-1111-1111-111111111111", "name": "A", "version": "1", "active": False}]},
        "target_cf": {"extensions": [{"uuid": "22222222-2222-2222-2222-222222222222", "name": "B", "version": "2", "active": True}]},
        "next_vendor": {"extensions": []},
    }
    contract = {
        "roles": roles,
        "artifacts": [],
        "extension_decisions": [
            {"uuid": "11111111-1111-1111-1111-111111111111", "decision": "include", "rationale": ""},
            {"uuid": "33333333-3333-3333-3333-333333333333", "decision": "exclude", "rationale": "old"},
        ],
    }
    scope = extension_scope(contract, connections)
    assert scope["included"] == ["11111111-1111-1111-1111-111111111111"]
    assert scope["unreviewed"] == ["22222222-2222-2222-2222-222222222222"]
    assert scope["dormant"] == ["33333333-3333-3333-3333-333333333333"]
    assert [item["routing_group_id"] for item in component_members(contract, connections) if item["kind"] == "extension"] == ["extension:11111111-1111-1111-1111-111111111111"]
    connections["vendor_baseline"]["extensions"][0].update(name="Renamed", version="9", active=True)
    assert extension_scope(contract, connections)["included"] == scope["included"]
    connections["vendor_baseline"]["extensions"] = [{
        "uuid": "33333333-3333-3333-3333-333333333333",
        "name": "Returned",
        "version": "3",
        "active": True,
    }]
    returned = extension_scope(contract, connections)
    assert "33333333-3333-3333-3333-333333333333" in returned["excluded"]
    assert "33333333-3333-3333-3333-333333333333" not in returned["dormant"]
