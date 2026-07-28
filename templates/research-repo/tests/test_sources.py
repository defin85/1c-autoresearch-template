import io
import json
import subprocess
import sys
import time
import tomllib
import zipfile
from types import SimpleNamespace
from pathlib import Path

import pytest

from one_c_autoresearch import diffs
from one_c_autoresearch.contracts import canonical_json, external_id, repository_lock, sha256
from one_c_autoresearch.sources import LEGACY_PROFILES, NORMALIZER_VERSION, PROFILES, _acquire_verified, _extract_source_tree, _run_command, _toolchain_versions, acquire, adapter_plan, build_routing_preview, clean_payload, configuration_identity, normalize_extension_decisions, normalize_payload, preflight_connection, publish, publish_routed, routing_bindings, serialize_infobases, stream_upload, validate_active, validate_role_contract
from one_c_autoresearch.service import ApplicationService


def test_extension_decisions_are_strict_normalized_and_deterministic():
    first = {"uuid": "22222222-2222-2222-2222-222222222222", "decision": "exclude", "rationale": "  Техническое  "}
    second = {"uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "decision": "include", "rationale": ""}
    assert normalize_extension_decisions([first, second]) == [{**first, "rationale": "Техническое"}, second]
    contract = {
        "schema_version": "1",
        "acquisition_profile": "ibcmd+form-aware/v1",
        "extension_decisions": [first, second],
        "roles": {
            role: {"connection_profile": role, "configuration_name": "Cfg", "root_uuid": "00000000-0000-0000-0000-000000000001", "version": "1"}
            for role in ("vendor_baseline", "target_cf", "next_vendor")
        },
    }
    assert tomllib.loads(serialize_infobases(contract).decode())["extension_decisions"] == normalize_extension_decisions([first, second])
    invalid = [
        [{"uuid": second["uuid"], "decision": "include", "rationale": "", "extra": ""}],
        [second, second],
        [{**second, "uuid": second["uuid"].upper()}],
        [{**second, "decision": "maybe"}],
        [{**second, "rationale": "unexpected"}],
        [{**first, "rationale": " "}],
    ]
    for value in invalid:
        with pytest.raises(ValueError):
            normalize_extension_decisions(value)


def test_preflight_extension_discovery_bounds_fail_before_identity_export(tmp_path: Path):
    connection = {"dbms": "PostgreSQL", "db_server": "localhost", "db_name": "db", "db_user": "u", "db_password": "p", "infobase_user": "u", "infobase_password": "p"}
    def response(payload: str):
        def fake_run(command, **_kwargs):
            if command[-1] == "--version":
                return SimpleNamespace(returncode=0, stdout="8.3.27.1989", stderr="")
            if command[1:3] == ["extension", "list"]:
                return SimpleNamespace(returncode=0, stdout=payload, stderr="")
            raise AssertionError("identity export must not start after an exhausted bound")
        return fake_run
    with pytest.raises(ValueError, match="response-size"):
        preflight_connection("ibcmd+form-aware/v1", tmp_path, connection, run=response("x" * (1024 * 1024 + 1)))
    many = "".join(f'name: \"E{index}\"\nactive: yes\n' for index in range(1001))
    with pytest.raises(ValueError, match="extension-count"):
        preflight_connection("ibcmd+form-aware/v1", tmp_path, connection, run=response(many))


def test_routing_preview_blocks_unreviewed_extension_without_export(tmp_path: Path):
    research = tmp_path / "research"; research.mkdir()
    (research / "workflow.toml").write_text("schema_version='1'\n", encoding="utf-8")
    (research / "external-artifacts.toml").write_text("schema_version='1'\nartifacts=[]\n", encoding="utf-8")
    (tmp_path / "project.toml").write_text('[project]\nid="p"\nproduct="p"\nbaseline_version="1"\ntarget_version="1"\nnext_vendor_version="2"\n', encoding="utf-8")
    (research / "infobases.toml").write_text('schema_version="1"\nacquisition_profile="ibcmd+form-aware/v1"\nextension_decisions=[]\n' + "".join(
        f'[roles.{role}]\nconnection_profile="{role}"\nconfiguration_name="Cfg"\nroot_uuid="00000000-0000-0000-0000-000000000001"\nversion="{version}"\n'
        for role, version in (("vendor_baseline", "1"), ("target_cf", "1"), ("next_vendor", "2"))
    ), encoding="utf-8")
    extension = {"uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": "Service", "version": "1", "active": False}
    profiles = {}
    for role in ("vendor_baseline", "target_cf", "next_vendor"):
        profile = {"kind": "server", "server": "localhost", "reference": role, "profile_id": "ibcmd+form-aware/v1", "tested": True, "extensions": [extension] if role == "target_cf" else []}
        profile["tested_fingerprint"] = "sha256:" + sha256(canonical_json(profile))
        profiles[role] = profile
    preview = build_routing_preview(tmp_path, tmp_path, profiles, run=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("export must not start")))
    assert preview["blockers"] == [{
        "code": "extension_scope_required",
        "uuid": extension["uuid"],
        "roles": preview["extension_scope"]["extensions"][0]["roles"],
        "action": "review_extension_scope",
    }]
    assert preview["routing_manifest"]["groups"] == []


def test_exact_form_aware_profiles_and_legacy_internal_adapters(tmp_path: Path):
    assert set(PROFILES) == {"ibcmd+form-aware/v1", "designer+form-aware/v1"}
    connection = {"dbms": "PostgreSQL", "db_server": "localhost", "db_name": "demo", "db_user": "postgres", "db_password": "secret", "infobase_user": "user", "infobase_password": "secret", "client_connection": "/Sserver/demo"}
    for profile in LEGACY_PROFILES:
        plan = adapter_plan(profile, Path("/opt/1cv8"), connection, tmp_path / "out")
        assert len(plan) == (1 if profile.endswith("xml-hierarchical/v1") else 2 if profile.endswith("v8unpack/v1") else 5)
    assert len(adapter_plan("ibcmd+form-aware/v1", Path("/opt/1cv8"), connection, tmp_path / "out", representation="xml-hierarchical")) == 1
    with pytest.raises(ValueError, match="routed representation"):
        adapter_plan("ibcmd+form-aware/v1", Path("/opt/1cv8"), connection, tmp_path / "out")
    assert "save" in adapter_plan("ibcmd+v8unpack/v1", Path("/opt/1cv8"), connection, tmp_path / "out")[0]
    assert adapter_plan("ibcmd+v8unpack/v1", Path("/opt/1cv8"), connection, tmp_path / "out")[1][1] == "-E"
    assert adapter_plan("ibcmd+v8unpack/v1", Path("/opt/1cv8"), connection, tmp_path / "out")[1][-2:] == ["--processes", "1"]
    assert "/DumpConfigToFiles" in adapter_plan("designer+edt-project/v1", Path("/opt/1cv8"), connection, tmp_path / "out")[0]


def test_edt_adapter_exports_hierarchical_xml_before_import(tmp_path: Path):
    connection = {"dbms": "PostgreSQL", "db_server": "localhost", "db_name": "db", "db_user": "u", "db_password": "p", "infobase_user": "i", "infobase_password": "q"}
    plan = adapter_plan("ibcmd+edt-project/v1", Path("/opt/1c"), connection, tmp_path / "edt")
    assert plan[0][1:3] == ["config", "export"]
    assert plan[0][-1].endswith(".xml-staging")
    assert plan[1][1] == "-data" and plan[1][5:7] == ["-command", "import"]
    assert plan[1][plan[1].index("--configuration-files") + 1] == plan[0][-1]
    assert plan[1][plan[1].index("--project-name") + 1] == "base"
    assert [command[command.index("-command") + 1] for command in plan[2:]] == ["sort-project", "clean-up-source", "validate"]


def test_normalizer_removes_volatile_form_period_dates(tmp_path: Path):
    path = tmp_path / "Form.xml"; path.write_text('<pl:begin>2026-07-21T00:00:00</pl:begin><pl:end>2026-07-21T23:59:59</pl:end>', encoding="utf-8")
    normalize_payload(tmp_path)
    assert path.read_text(encoding="utf-8") == '<pl:begin>2000-01-01T00:00:00</pl:begin><pl:end>2000-01-01T23:59:59</pl:end>'


def test_edt_project_metadata_is_payload_not_workspace_noise(tmp_path: Path):
    for name in (".project", ".settings"):
        path = tmp_path / name; path.mkdir() if name == ".settings" else path.write_text("project", encoding="utf-8")
    (tmp_path / ".metadata").mkdir()
    clean_payload(tmp_path)
    assert (tmp_path / ".project").is_file() and (tmp_path / ".settings").is_dir()
    assert not (tmp_path / ".metadata").exists()


def test_source_tree_unpack_is_confined_and_secret_scanned(tmp_path: Path):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("module/code.bsl", "Процедура Тест()\nКонецПроцедуры")
    _extract_source_tree(archive, tmp_path / "out")
    assert (tmp_path / "out/module/code.bsl").is_file()
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("../escape", "bad")
    with pytest.raises(ValueError):
        _extract_source_tree(archive, tmp_path / "escape-test")
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("config.txt", "token=very-private-token")
    with pytest.raises(ValueError, match="detected secret"):
        _extract_source_tree(archive, tmp_path / "secret-test")
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("help.html", "password:pwd</strong>")
    _extract_source_tree(archive, tmp_path / "placeholder-test")


def test_configuration_identity_rejects_entities_and_reads_name_version(tmp_path: Path):
    (tmp_path / "Configuration.xml").write_text('<MetaDataObject><Configuration uuid="11111111-1111-1111-1111-111111111111"><Properties><Name>Cfg</Name><Version>1.2</Version></Properties></Configuration></MetaDataObject>', encoding="utf-8")
    assert configuration_identity(tmp_path) == {"uuid": "11111111-1111-1111-1111-111111111111", "name": "Cfg", "version": "1.2"}
    (tmp_path / "Configuration.xml").write_text('<!DOCTYPE x [<!ENTITY y "z">]><x/>', encoding="utf-8")
    with pytest.raises(ValueError, match="entities"):
        configuration_identity(tmp_path)
    edt = tmp_path / "edt/src/Configuration"; edt.mkdir(parents=True)
    (edt / "Configuration.mdo").write_text('<mdclass:Configuration xmlns:mdclass="urn:test" uuid="22222222-2222-2222-2222-222222222222"><Properties><name>CfgEdt</name><version>2.0</version></Properties></mdclass:Configuration>', encoding="utf-8")
    assert configuration_identity(tmp_path / "edt")["name"] == "CfgEdt"


def test_preflight_binds_each_extension_to_exported_uuid(tmp_path: Path):
    connection = {"kind": "server", "server": "localhost", "reference": "db", "dbms": "PostgreSQL", "db_server": "localhost", "db_name": "db", "db_user": "u", "db_password": "secret", "infobase_user": "i", "infobase_password": "secret"}

    def fake_run(command, **_kwargs):
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="8.3.27.1989")
        if command[1:3] == ["extension", "list"]:
            return SimpleNamespace(returncode=0, stdout='name: "Extension"\nversion: "1.0"\nactive: yes\n')
        output = Path(command[-1]); output.mkdir(parents=True)
        (output / "Configuration.xml").write_text('<MetaDataObject><Configuration uuid="11111111-1111-1111-1111-111111111111"><Properties><Name>Extension</Name><Version>1.0</Version></Properties></Configuration></MetaDataObject>', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    tested = preflight_connection("ibcmd+form-aware/v1", tmp_path, connection, run=fake_run)
    assert tested["configuration"] == {"uuid": "11111111-1111-1111-1111-111111111111", "name": "Extension", "version": "1.0"}
    assert tested["extensions"] == [{"name": "Extension", "version": "1.0", "active": True, "uuid": "11111111-1111-1111-1111-111111111111"}]
    assert "secret" not in json.dumps(tested)


def test_toolchain_preflight_rejects_unpinned_converter_and_missing_designer(tmp_path: Path, monkeypatch):
    def fake_run(command, **_kwargs):
        output = "8.3.27.1989" if command[-1] == "--version" else "usage: v8unpack 9.9.9"
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr("one_c_autoresearch.sources.shutil.which", lambda name: f"/usr/bin/{name}")
    with pytest.raises(RuntimeError, match="v8unpack 1.2.6"):
        _toolchain_versions("ibcmd+v8unpack/v1", tmp_path, timeout_seconds=10, run=fake_run)
    monkeypatch.setattr("one_c_autoresearch.sources.shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="v8unpack executable"):
        _toolchain_versions("ibcmd+v8unpack/v1", tmp_path, timeout_seconds=10, run=fake_run)
    monkeypatch.setattr("one_c_autoresearch.sources.shutil.which", lambda name: f"/usr/bin/{name}")
    with pytest.raises(RuntimeError, match="Designer executable"):
        _toolchain_versions("designer+xml-hierarchical/v1", tmp_path, timeout_seconds=10, run=fake_run)


def test_cancellation_terminates_running_exporter():
    started = time.monotonic()
    with pytest.raises(InterruptedError, match="cancelled"):
        _run_command(subprocess.run, [sys.executable, "-c", "import time; time.sleep(30)"], cancelled=lambda: time.monotonic() - started > 0.1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert time.monotonic() - started < 5
    completed = _run_command(subprocess.run, [sys.executable, "-c", "import sys; print(sys.stdin.read())"], cancelled=lambda: False, input="request", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
    assert completed.stdout.strip() == "request"


def test_acquisition_rejects_exhausted_space_without_pointer_change(tmp_path: Path, monkeypatch):
    research = tmp_path / "research"; research.mkdir()
    pointer = research / "active-source-generation.json"
    pointer.write_text('{"generation_id":"prior"}\n', encoding="utf-8")
    monkeypatch.setattr("one_c_autoresearch.sources.shutil.disk_usage", lambda _: type("D", (), {"free": 0})())
    with pytest.raises(OSError, match="1 GiB"):
        acquire(tmp_path, tmp_path, {}, routing_preview={})
    assert json.loads(pointer.read_text(encoding="utf-8"))["generation_id"] == "prior"


def test_routed_publication_rechecks_freshness_inside_repository_lock(tmp_path: Path):
    staged = tmp_path / "staged"
    for role in ("vendor_baseline", "target_cf", "next_vendor"):
        root = staged / role / "configuration"
        root.mkdir(parents=True)
        (root / "Configuration.xml").write_text(role, encoding="utf-8")
    manifest = {"schema_version": "2", "routing_contract_version": "test", "groups": []}
    manifest["routing_manifest_fingerprint"] = "sha256:" + sha256(canonical_json(manifest))
    called = False

    def freshness_check():
        nonlocal called
        called = True
        with pytest.raises(RuntimeError, match="writer is busy"):
            with repository_lock(tmp_path):
                pass
        raise RuntimeError("routing_preview_stale")

    with pytest.raises(RuntimeError, match="routing_preview_stale"):
        publish_routed(
            tmp_path,
            staged,
            {"acquisition_profile_id": "ibcmd+form-aware/v1"},
            manifest,
            NORMALIZER_VERSION,
            None,
            lambda *_args: "sha256:" + "0" * 64,
            freshness_check=freshness_check,
        )
    assert called
    assert not (tmp_path / "research/active-source-generation.json").exists()
    assert not (tmp_path / "sources/generations").exists()


def test_preexport_extension_verification_has_one_overall_deadline(tmp_path: Path, monkeypatch):
    research = tmp_path / "research"
    research.mkdir()
    tmp_path.joinpath("project.toml").write_text(
        '[project]\nid="p"\nproduct="p"\nbaseline_version="1"\ntarget_version="1"\nnext_vendor_version="2"\n',
        encoding="utf-8",
    )
    research.joinpath("workflow.toml").write_text("schema_version='1'\n", encoding="utf-8")
    research.joinpath("external-artifacts.toml").write_text("schema_version='1'\nartifacts=[]\n", encoding="utf-8")
    research.joinpath("infobases.toml").write_text(
        'schema_version="1"\nacquisition_profile="ibcmd+form-aware/v1"\nextension_decisions=[]\n'
        + "".join(
            f'[roles.{role}]\nconnection_profile="{role}"\nconfiguration_name="Cfg"\n'
            'root_uuid="00000000-0000-0000-0000-000000000001"\n'
            f'version="{"2" if role == "next_vendor" else "1"}"\n'
            for role in ("vendor_baseline", "target_cf", "next_vendor")
        ),
        encoding="utf-8",
    )
    profiles = {}
    for role in ("vendor_baseline", "target_cf", "next_vendor"):
        profile = {
            "kind": "server",
            "server": "localhost",
            "reference": role,
            "profile_id": "ibcmd+form-aware/v1",
            "tested": True,
            "extensions": [],
            "configuration": {
                "uuid": "00000000-0000-0000-0000-000000000001",
                "name": "Cfg",
                "version": "2" if role == "next_vendor" else "1",
            },
        }
        profile["tested_fingerprint"] = "sha256:" + sha256(canonical_json(profile))
        profiles[role] = profile
    clock = {"value": 0.0}
    monkeypatch.setattr("one_c_autoresearch.sources.time.monotonic", lambda: clock["value"])

    def slow_preflight(*_args, **kwargs):
        assert kwargs["timeout_seconds"] <= 1
        clock["value"] += 0.6
        return {"extensions": []}

    monkeypatch.setattr("one_c_autoresearch.sources.preflight_connection", slow_preflight)
    identity_root = tmp_path / "identity"
    identity_root.mkdir()
    with pytest.raises(TimeoutError, match="overall-time"):
        _acquire_verified(
            tmp_path,
            tmp_path,
            profiles,
            routing_preview={"blockers": []},
            normalizer_version=NORMALIZER_VERSION,
            timeout_seconds=1,
            run=subprocess.run,
            upload_drafts=None,
            cancelled=None,
            progress=None,
            activate=True,
            identity_root=identity_root,
        )
    assert not (research / "active-source-generation.json").exists()


def test_streamed_upload_and_atomic_source_generation(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("one_c_autoresearch.sources.shutil.disk_usage", lambda _: type("D", (), {"free": 2 * 1024**3})())
    drafts = tmp_path / "drafts"; drafts.mkdir()
    result = stream_upload(io.BytesIO(b"payload"), drafts, "artifact.epf", 7, "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5")
    assert result["size_bytes"] == 7
    with pytest.raises(ValueError):
        stream_upload(io.BytesIO(b"too long"), drafts, "bad.epf", 2, None)
    staged = tmp_path / "staged"
    for role in ("vendor_baseline", "target_cf", "next_vendor"):
        root = staged / role / "configuration"; root.mkdir(parents=True); (root / "Configuration.xml").write_text(role, encoding="utf-8")
    contract = {"schema_version": "1", "acquisition_profile_id": "ibcmd+xml-hierarchical/v1", "roles": {role: {} for role in ("vendor_baseline", "target_cf", "next_vendor")}, "artifacts": []}
    pointer = publish(tmp_path, staged, contract)
    assert pointer["normalizer_version"] == NORMALIZER_VERSION
    assert [item["component_id"] for item in pointer["components"]] == [f"{role}:configuration" for role in ("vendor_baseline", "target_cf", "next_vendor")]
    assert (tmp_path / "sources/generations" / pointer["generation_id"] / "source-contract.json").is_file()
    assert publish(tmp_path, staged, contract)["generation_id"] == pointer["generation_id"]
    (tmp_path / "sources/generations" / pointer["generation_id"] / "vendor_baseline/configuration/Configuration.xml").write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not match"):
        publish(tmp_path, staged, contract)


def test_external_artifact_declaration_is_closed_and_uses_declared_size(tmp_path: Path, monkeypatch):
    (tmp_path / "research").mkdir()
    (tmp_path / "research/workflow.toml").write_text("schema_version='1'\n", encoding="utf-8")
    (tmp_path / "project.toml").write_text('[project]\nid="p"\nproduct="p"\nbaseline_version="1"\ntarget_version="1"\nnext_vendor_version="2"\n', encoding="utf-8")
    (tmp_path / "research/infobases.toml").write_text('schema_version="1"\nacquisition_profile="ibcmd+form-aware/v1"\nextension_decisions=[{uuid="11111111-1111-1111-1111-111111111111",decision="include",rationale=""}]\n' + ''.join(f'[roles.{role}]\nconnection_profile="{role}"\nconfiguration_name="Cfg"\nroot_uuid="00000000-0000-0000-0000-000000000001"\nversion="{version}"\n' for role, version in (("vendor_baseline", "1"), ("target_cf", "1"), ("next_vendor", "2"))), encoding="utf-8")
    profiles = {role: {"kind": "server", "server": "localhost", "reference": role, "profile_id": "ibcmd+form-aware/v1", "tested": True, "extensions": [], "dbms": "PostgreSQL", "db_server": "localhost", "db_name": role, "db_user": "u", "db_password": "p", "infobase_user": "u", "infobase_password": "p"} for role in ("vendor_baseline", "target_cf", "next_vendor")}
    for profile in profiles.values():
        profile["tested_fingerprint"] = "sha256:" + sha256(canonical_json({key: value for key, value in profile.items() if key not in {"db_password", "infobase_password"}}))
    (tmp_path / "research/external-artifacts.toml").write_text('schema_version="1"\n[[artifacts]]\nrole="target_cf"\nkind="epf"\nsemantic_key="report"\nfilename="report.epf"\ndeclared_size_bytes=7\nsha256="239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5"\n', encoding="utf-8")
    assert validate_role_contract(tmp_path, profiles)["artifacts"][0]["declared_size_bytes"] == 7
    path = tmp_path / "research/external-artifacts.toml"
    path.write_text(path.read_text(encoding="utf-8").replace("declared_size_bytes=7", "size_bytes=7"), encoding="utf-8")
    with pytest.raises(ValueError, match="declared_size_bytes"):
        validate_role_contract(tmp_path, profiles)

    path.write_text('schema_version="1"\n' + ''.join(f'[[artifacts]]\nrole="target_cf"\nkind="{kind}"\nsemantic_key="{key}"\nfilename="{key}.{kind}"\ndeclared_size_bytes=1\n' for kind, key in (("epf", "one"), ("erf", "two"))), encoding="utf-8")
    monkeypatch.setattr("one_c_autoresearch.sources.external_id", lambda *_args: "EXT-COLLISION")
    with pytest.raises(ValueError, match="ID collision"):
        validate_role_contract(tmp_path, profiles)


def test_source_setup_patch_is_stale_safe_and_requires_epoch_confirmation(tmp_path: Path):
    research = tmp_path / "research"; research.mkdir()
    path = research / "infobases.toml"
    path.write_text('schema_version="1"\nacquisition_profile="ibcmd+xml-hierarchical/v1"\n' + ''.join(f'[roles.{role}]\nconnection_profile="{role}"\nconfiguration_name="Cfg"\nroot_uuid="00000000-0000-0000-0000-000000000001"\nversion="{version}"\n' for role, version in (("vendor_baseline", "1"), ("target_cf", "1"), ("next_vendor", "2"))), encoding="utf-8")
    (research / "external-artifacts.toml").write_text('schema_version="1"\nartifacts=[]\n', encoding="utf-8")
    service = ApplicationService.__new__(ApplicationService); service.repo = tmp_path
    payload = {"acquisition_profile": "designer+form-aware/v1", "connection_profiles": {role: f"new-{role}" for role in ("vendor_baseline", "target_cf", "next_vendor")}, "expected_manifest_fingerprint": "sha256:" + sha256(path.read_bytes()), "confirm_new_epoch": False}
    with pytest.raises(ValueError, match="confirmation"):
        service._configure_sources(payload)
    payload["confirm_new_epoch"] = True
    result = service._configure_sources(payload)
    assert result["comparison_epoch_changed"] and 'acquisition_profile = "designer+form-aware/v1"' in path.read_text(encoding="utf-8")
    with pytest.raises(RuntimeError, match="stale"):
        service._configure_sources(payload)


def test_routing_bindings_detect_connection_declaration_and_draft_changes(tmp_path: Path):
    research = tmp_path / "research"; research.mkdir()
    research.joinpath("workflow.toml").write_text("schema_version='1'\n", encoding="utf-8")
    research.joinpath("infobases.toml").write_text("schema_version='1'\n", encoding="utf-8")
    research.joinpath("external-artifacts.toml").write_text("schema_version='1'\nartifacts=[]\n", encoding="utf-8")
    drafts = tmp_path / "drafts"; drafts.mkdir()
    connections = {"p": {"tested": True, "db_password": "secret"}}
    initial = routing_bindings(tmp_path, connections, drafts)
    changed_connection = routing_bindings(tmp_path, {"p": {"tested": False, "db_password": "changed"}}, drafts)
    assert changed_connection["connections"] != initial["connections"]
    research.joinpath("infobases.toml").write_text("schema_version='1'\n# changed\n", encoding="utf-8")
    assert routing_bindings(tmp_path, connections, drafts)["infobases"] != initial["infobases"]
    research.joinpath("external-artifacts.toml").write_text("schema_version='1'\n# changed\n", encoding="utf-8")
    assert routing_bindings(tmp_path, connections, drafts)["external_artifacts"] != initial["external_artifacts"]
    (drafts / "payload").write_bytes(b"changed")
    assert routing_bindings(tmp_path, connections, drafts)["upload_draft"] != initial["upload_draft"]


def test_acquire_exports_all_three_roles_before_one_publication(tmp_path: Path, monkeypatch):
    (tmp_path / "research").mkdir()
    (tmp_path / "research/workflow.toml").write_text("schema_version='1'\n", encoding="utf-8")
    (tmp_path / "project.toml").write_text('[project]\nid="p"\nproduct="p"\nbaseline_version="1"\ntarget_version="1"\nnext_vendor_version="2"\n', encoding="utf-8")
    (tmp_path / "research/infobases.toml").write_text('schema_version="1"\nacquisition_profile="ibcmd+form-aware/v1"\nextension_decisions=[{uuid="11111111-1111-1111-1111-111111111111",decision="include",rationale=""}]\n' + ''.join(f'[roles.{role}]\nconnection_profile="{role}"\nconfiguration_name="Cfg"\nroot_uuid="00000000-0000-0000-0000-000000000001"\nversion="{version}"\n' for role, version in (("vendor_baseline", "1"), ("target_cf", "1"), ("next_vendor", "2"))), encoding="utf-8")
    (tmp_path / "research/external-artifacts.toml").write_text('schema_version="1"\nartifacts=[]\n', encoding="utf-8")
    extension = {"name": "Extension", "version": "1.0", "active": True, "uuid": "11111111-1111-1111-1111-111111111111"}
    profiles = {role: {"kind": "server", "server": "localhost", "reference": role, "profile_id": "ibcmd+form-aware/v1", "tested": True, "dbms": "PostgreSQL", "db_server": "localhost", "db_name": role, "db_user": "u", "db_password": "p", "infobase_user": "u", "infobase_password": "p", "extensions": [extension], "tool_versions": {"platform": "8.3.27.1989", "exporter": "ibcmd"}} for role in ("vendor_baseline", "target_cf", "next_vendor")}
    for profile in profiles.values():
        profile["configuration"] = {"uuid": "00000000-0000-0000-0000-000000000001", "name": "Cfg", "version": "2" if profile["reference"] == "next_vendor" else "1"}
        tested = {key: value for key, value in profile.items() if key not in {"db_password", "infobase_password"}}
        profile["tested_fingerprint"] = "sha256:" + sha256(canonical_json(tested))
    mode = {"partial_role": False}
    def fake_run(command, **_kwargs):
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="8.3.27.1989", stderr="")
        if command[1:3] == ["extension", "list"]:
            return SimpleNamespace(returncode=0, stdout='name: "Extension"\nversion: "1.0"\nactive: yes\n', stderr="")
        output = Path(command[-1]); output.mkdir(parents=True)
        if mode["partial_role"] and (".work" in output.parts or any(part.startswith("extension-verification-") for part in output.parts)) and "next_vendor" in output.parts:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        is_extension = "--extension=Extension" in command
        version = "1.0" if is_extension else "2" if "next_vendor" in " ".join(map(str, command)) else "1"
        uuid = extension["uuid"] if is_extension else "00000000-0000-0000-0000-000000000001"
        name = "Extension" if is_extension else "Cfg"
        (output / "Configuration.xml").write_text(f'<MetaDataObject><Configuration uuid="{uuid}"><Properties><Name>{name}</Name><Version>{version}</Version></Properties></Configuration></MetaDataObject>', encoding="utf-8")
        (output / "ConfigDumpInfo.xml").write_text("<noise/>", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    progress = []
    preview = build_routing_preview(tmp_path, Path("/opt/1cv8"), profiles, run=fake_run, progress=progress.append)
    assert progress[-1] == {"phase": "route", "completed": 6, "total": 6, "subject": ""}
    pointer = acquire(tmp_path, Path("/opt/1cv8"), profiles, routing_preview=preview, run=fake_run)
    root = tmp_path / "sources/generations" / pointer["generation_id"]
    assert pointer["schema_version"] == "2"
    assert validate_active(tmp_path, deep=True) == pointer
    assert all((root / role / "configuration/Configuration.xml").is_file() for role in profiles)
    assert all((root / role / "extensions" / extension["uuid"] / "Configuration.xml").is_file() for role in profiles)
    assert not any(path.name == "ConfigDumpInfo.xml" for path in root.rglob("*"))
    assert not any(path.name.endswith((".cf", ".cfe")) or path.name.startswith(".work") for path in root.rglob("*"))
    pointer_path = tmp_path / "research/active-source-generation.json"
    prior = pointer_path.read_bytes()
    import one_c_autoresearch.sources as source_module
    original = source_module._check_cancelled
    for phase in ("probe", "route", "export", "validation", "publication"):
        monkeypatch.setattr(source_module, "_check_cancelled", original)
        current_preview = build_routing_preview(tmp_path, Path("/opt/1cv8"), profiles, run=fake_run)
        def injected(cancelled, current, expected=phase):
            if current == expected:
                raise InterruptedError(f"injected {current}")
            original(cancelled, current)
        monkeypatch.setattr(source_module, "_check_cancelled", injected)
        with pytest.raises(InterruptedError, match=f"injected {phase}"):
            acquire(tmp_path, Path("/opt/1cv8"), profiles, routing_preview=current_preview, run=fake_run)
        assert pointer_path.read_bytes() == prior
        assert not any((tmp_path / "sources/.staging").iterdir())
    monkeypatch.setattr(source_module, "_check_cancelled", original)
    partial_preview = build_routing_preview(tmp_path, Path("/opt/1cv8"), profiles, run=fake_run)
    mode["partial_role"] = True
    with pytest.raises(ValueError, match="exported configuration root is missing"):
        acquire(tmp_path, Path("/opt/1cv8"), profiles, routing_preview=partial_preview, run=fake_run)
    assert pointer_path.read_bytes() == prior
    assert not any((tmp_path / "sources/.staging").iterdir())
    mode["partial_role"] = False
    contract_path = tmp_path / "research/infobases.toml"
    contract_path.write_text(
        contract_path.read_text(encoding="utf-8").replace(
            'decision="include",rationale=""',
            'decision="exclude",rationale="out of scope"',
        ),
        encoding="utf-8",
    )
    excluded_preview = build_routing_preview(tmp_path, Path("/opt/1cv8"), profiles, run=fake_run)
    excluded = acquire(tmp_path, Path("/opt/1cv8"), profiles, routing_preview=excluded_preview, run=fake_run)
    excluded_root = tmp_path / "sources/generations" / excluded["generation_id"]
    assert not any(item["kind"] == "extension" for item in excluded["components"])
    assert not any(path.name == extension["uuid"] for path in excluded_root.rglob("*"))
    diff = diffs.build(tmp_path, excluded)
    diff_root = tmp_path / "analysis/indexes/generations" / diff["generation_id"]
    assert extension["uuid"] not in (diff_root / "diff-inventory.csv").read_text(encoding="utf-8")
    assert extension["uuid"] not in (diff_root / "extension-physical-diff.csv").read_text(encoding="utf-8")
    assert extension["uuid"] not in (diff_root / "extension-diff.jsonl").read_text(encoding="utf-8")


def test_routing_preview_probes_all_roles_and_is_repeatable(tmp_path: Path):
    research = tmp_path / "research"; research.mkdir()
    (tmp_path / "project.toml").write_text('[project]\nid="p"\nproduct="p"\nbaseline_version="1"\ntarget_version="1"\nnext_vendor_version="2"\n', encoding="utf-8")
    research.joinpath("workflow.toml").write_text("schema_version='1'\n", encoding="utf-8")
    research.joinpath("infobases.toml").write_text('schema_version="1"\nacquisition_profile="ibcmd+form-aware/v1"\n' + ''.join(f'[roles.{role}]\nconnection_profile="{role}"\nconfiguration_name="Cfg"\nroot_uuid="00000000-0000-0000-0000-000000000001"\nversion="{version}"\n' for role, version in (("vendor_baseline", "1"), ("target_cf", "1"), ("next_vendor", "2"))), encoding="utf-8")
    research.joinpath("external-artifacts.toml").write_text('schema_version="1"\nartifacts=[]\n', encoding="utf-8")
    profiles = {role: {"kind": "server", "server": "localhost", "reference": role, "profile_id": "ibcmd+form-aware/v1", "tested": True, "extensions": [], "dbms": "PostgreSQL", "db_server": "localhost", "db_name": role, "db_user": "u", "db_password": "p", "infobase_user": "u", "infobase_password": "p"} for role in ("vendor_baseline", "target_cf", "next_vendor")}
    for profile in profiles.values():
        profile["tested_fingerprint"] = "sha256:" + sha256(canonical_json({key: value for key, value in profile.items() if key not in {"db_password", "infobase_password"}}))

    def fake_run(command, **_kwargs):
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="8.3.27.1989")
        output = Path(command[-1]); output.mkdir(parents=True)
        version = "2" if "next_vendor" in output.parts else "1"
        (output / "Configuration.xml").write_text(f'<MetaDataObject><Configuration uuid="00000000-0000-0000-0000-000000000001"><Properties><Name>Cfg</Name><Version>{version}</Version></Properties></Configuration></MetaDataObject>', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    first = build_routing_preview(tmp_path, tmp_path, profiles, run=fake_run)
    second = build_routing_preview(tmp_path, tmp_path, profiles, run=fake_run)
    assert first == second
    group = first["routing_manifest"]["groups"][0]
    assert group["routing_group_id"] == "configuration"
    assert group["routing_reason"] == "no_forms"
    assert group["representation_schema"] == "xml-hierarchical/v1"


@pytest.mark.parametrize("kind", ["epf", "erf"])
def test_external_processor_preserves_ordinary_form_and_manifest_is_repeatable(tmp_path: Path, monkeypatch, kind: str):
    monkeypatch.setattr("one_c_autoresearch.sources.shutil.which", lambda name: f"/usr/bin/{name}")
    research = tmp_path / "research"; research.mkdir()
    (tmp_path / "project.toml").write_text('[project]\nid="p"\nproduct="p"\nbaseline_version="1"\ntarget_version="1"\nnext_vendor_version="2"\n', encoding="utf-8")
    research.joinpath("workflow.toml").write_text("schema_version='1'\n", encoding="utf-8")
    research.joinpath("infobases.toml").write_text('schema_version="1"\nacquisition_profile="ibcmd+form-aware/v1"\n' + ''.join(f'[roles.{role}]\nconnection_profile="{role}"\nconfiguration_name="Cfg"\nroot_uuid="00000000-0000-0000-0000-000000000001"\nversion="{version}"\n' for role, version in (("vendor_baseline", "1"), ("target_cf", "1"), ("next_vendor", "2"))), encoding="utf-8")
    payload = f"sanitized-{kind}".encode()
    digest = sha256(payload)
    research.joinpath("external-artifacts.toml").write_text(f'schema_version="1"\n[[artifacts]]\nrole="target_cf"\nkind="{kind}"\nsemantic_key="report"\nfilename="report.{kind}"\ndeclared_size_bytes={len(payload)}\nsha256="{digest}"\n', encoding="utf-8")
    identifier = external_id(kind, "report")
    drafts = tmp_path / "drafts"; uploaded = drafts / "target_cf" / identifier / f"report.{kind}"; uploaded.parent.mkdir(parents=True); uploaded.write_bytes(payload)
    mode = {"reject_container": False}

    def fake_run(command, **_kwargs):
        if command[-1] == "--version": return SimpleNamespace(returncode=0, stdout="8.3.27.1989", stderr="")
        if command[0].endswith("v8unpack") and command[-1] == "-h": return SimpleNamespace(returncode=0, stdout="v8unpack 1.2.6", stderr="")
        if command[1:3] == ["extension", "list"]: return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[0].endswith("v8unpack"):
            if mode["reject_container"]:
                return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
            output = Path(command[3]); output.mkdir(parents=True, exist_ok=True)
            form = output / "Forms/Main/Ext/Form.xml"; form.parent.mkdir(parents=True); form.write_text('<Form xmlns="http://v8.1c.ru/8.3/xcf/form"/>', encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        output = Path(command[-1]); output.mkdir(parents=True, exist_ok=True)
        version = "2" if "next_vendor" in output.parts else "1"
        (output / "Configuration.xml").write_text(f'<MetaDataObject><Configuration uuid="00000000-0000-0000-0000-000000000001"><Properties><Name>Cfg</Name><Version>{version}</Version></Properties></Configuration></MetaDataObject>', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    profiles = {}
    platform = tmp_path / "platform"
    for role in ("vendor_baseline", "target_cf", "next_vendor"):
        raw = {"kind": "server", "server": "localhost", "reference": role, "dbms": "PostgreSQL", "db_server": "localhost", "db_name": role, "db_user": "u", "db_password": "p", "infobase_user": "u", "infobase_password": "p"}
        profiles[role] = {**raw, **preflight_connection("ibcmd+form-aware/v1", platform, raw, run=fake_run)}
    preview = build_routing_preview(tmp_path, platform, profiles, run=fake_run, upload_drafts=drafts)
    external = next(group for group in preview["routing_manifest"]["groups"] if group["routing_group_id"].startswith("external:"))
    assert external["representation_schema"] == "v8unpack/v1" and external["absent_roles"] == ["vendor_baseline", "next_vendor"]
    first = acquire(tmp_path, platform, profiles, routing_preview=preview, run=fake_run, upload_drafts=drafts)
    form = tmp_path / "sources/generations" / first["generation_id"] / "target_cf/external" / identifier / "source/Forms/Main/Ext/Form.xml"
    assert form.is_file()
    second_preview = build_routing_preview(tmp_path, platform, profiles, run=fake_run, upload_drafts=drafts)
    second = acquire(tmp_path, platform, profiles, routing_preview=second_preview, run=fake_run, upload_drafts=drafts)
    assert first["generation_id"] == second["generation_id"]
    prior = (tmp_path / "research/active-source-generation.json").read_bytes()
    mode["reject_container"] = True
    with pytest.raises(ValueError, match="source_probe_empty"):
        build_routing_preview(tmp_path, platform, profiles, run=fake_run, upload_drafts=drafts)
    assert (tmp_path / "research/active-source-generation.json").read_bytes() == prior


@pytest.mark.parametrize("profile_id", sorted(PROFILES))
def test_every_adapter_repeats_the_same_source_fingerprint(tmp_path: Path, monkeypatch, profile_id: str):
    platform = tmp_path / "8.3.27.1989"; platform.mkdir(); (platform / "1cv8").write_text("", encoding="utf-8")
    monkeypatch.setattr("one_c_autoresearch.sources.shutil.which", lambda name: f"/opt/1C/1CE/components/1c-edt-2024.2.5+16-x86_64/{name}" if name == "1cedtcli" else f"/usr/bin/{name}")
    (tmp_path / "research").mkdir()
    (tmp_path / "research/workflow.toml").write_text("schema_version='1'\n", encoding="utf-8")
    (tmp_path / "project.toml").write_text('[project]\nid="p"\nproduct="p"\nbaseline_version="1"\ntarget_version="1"\nnext_vendor_version="2"\n', encoding="utf-8")
    (tmp_path / "research/infobases.toml").write_text(f'schema_version="1"\nacquisition_profile="{profile_id}"\n' + ''.join(f'[roles.{role}]\nconnection_profile="{role}"\nconfiguration_name="Cfg"\nroot_uuid="00000000-0000-0000-0000-000000000001"\nversion="{version}"\n' for role, version in (("vendor_baseline", "1"), ("target_cf", "1"), ("next_vendor", "2"))), encoding="utf-8")
    (tmp_path / "research/external-artifacts.toml").write_text('schema_version="1"\nartifacts=[]\n', encoding="utf-8")

    def identity(output: Path):
        version = "2" if "next_vendor" in output.parts else "1"
        output.mkdir(parents=True, exist_ok=True)
        (output / "Configuration.xml").write_text(f'<MetaDataObject><Configuration uuid="00000000-0000-0000-0000-000000000001"><Properties><Name>Cfg</Name><Version>{version}</Version></Properties></Configuration></MetaDataObject>', encoding="utf-8")
        form = output / "Catalogs/Goods/Forms/Main/Ext/Form.xml"; form.parent.mkdir(parents=True)
        namespace = "http://v8.1c.ru/8.3/xcf/form" if "target_cf" in output.parts else "http://v8.1c.ru/8.3/xcf/logform"
        form.write_text(f'<Form xmlns="{namespace}"/>', encoding="utf-8")

    def fake_run(command, **_kwargs):
        if command[-1] == "--version": return SimpleNamespace(returncode=0, stdout="8.3.27.1989", stderr="")
        if command[0].endswith("v8unpack") and command[-1] == "-h": return SimpleNamespace(returncode=0, stdout="usage: v8unpack 1.2.6", stderr="")
        if command[1:3] == ["extension", "list"]: return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[0].endswith("v8unpack"):
            identity(Path(command[3])); return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if "-command" in command:
            if command[command.index("-command") + 1] == "import":
                workspace = Path(command[command.index("-data") + 1]); project = command[command.index("--project-name") + 1]
                identity(workspace / project)
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if command[0].endswith("1cv8"):
            switch = "/DumpCfg" if "/DumpCfg" in command else "/DumpConfigToFiles"; output = Path(command[command.index(switch) + 1])
        else:
            output = Path(command[-1])
        identity(output); return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    connections = {}
    for role in ("vendor_baseline", "target_cf", "next_vendor"):
        raw = {"kind": "server", "server": "localhost", "reference": role, "dbms": "PostgreSQL", "db_server": "localhost", "db_name": role, "db_user": "u", "db_password": "p", "infobase_user": "u", "infobase_password": "p", "client_connection": f"/Slocalhost/{role}"}
        connections[role] = {**raw, **preflight_connection(profile_id, platform, raw, run=fake_run)}
    preview = build_routing_preview(tmp_path, platform, connections, run=fake_run)
    assert preview["routing_manifest"]["groups"][0]["representation_schema"] == "v8unpack/v1"
    first = acquire(tmp_path, platform, connections, routing_preview=preview, run=fake_run)
    assert {item["representation_schema"] for item in first["components"]} == {"v8unpack/v1"}
    diff_pointer = diffs.build(tmp_path, first)
    inventory = (tmp_path / "analysis/indexes/generations" / diff_pointer["generation_id"] / "diff-inventory.csv").read_text(encoding="utf-8")
    assert "configuration/Catalogs/Goods/Forms/Main/Ext/Form.xml" in inventory
    assert "component-manifest.json" not in inventory
    with pytest.raises(RuntimeError, match="routing_preview_stale"):
        acquire(tmp_path, platform, connections, routing_preview=preview, run=fake_run)
    second = acquire(tmp_path, platform, connections, routing_preview=build_routing_preview(tmp_path, platform, connections, run=fake_run), run=fake_run)
    assert second["generation_id"] == first["generation_id"] and second["roles"] == first["roles"]
