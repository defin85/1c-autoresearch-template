from __future__ import annotations

import json
import os
import shutil
import tempfile
import tomllib
import subprocess
import re
import time
import unicodedata
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import IO, BinaryIO, NotRequired, Protocol, TypedDict

from .contracts import JsonValue, ROLES, atomic_json, canonical_json, confined, external_id, file_manifest, json_object, normalize_relative, owned_function, parse_json, parse_json_object, reject_secrets, repository_lock, require_tracked_clean as validate_tracked_clean, sha256

from .source_routing import ComponentMember, ExtensionObservation, ExtensionScope, FormProbe, FormRecord, RoutingGroup, RoutingManifest
from .platform_support import terminate_process


EXPORTERS = ("ibcmd", "designer")
REPRESENTATIONS = ("xml-hierarchical", "v8unpack", "edt-project")
PROFILES = {f"{exporter}+form-aware/v1": exporter for exporter in EXPORTERS}
LEGACY_PROFILES = {f"{exporter}+{representation}/v1": (exporter, representation) for exporter in EXPORTERS for representation in REPRESENTATIONS}
NORMALIZER_VERSION = "3"
V8UNPACK_VERSION = "1.2.6"
EDT_VERSION = "2024.2.5+16"
MINIMUM_FREE_BYTES = 1024**3
MAX_EXTENSION_COUNT = 1_000
MAX_EXTENSION_LIST_BYTES = 1024 * 1024
NOISE_NAMES = {"ConfigDumpInfo.xml", ".metadata", "build", "cache", "logs", "tmp"}
_UNSET = object()
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CancelCallback = Callable[[], bool]
ProgressCallback = Callable[[dict[str, object]], None]
class CommandRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        stdin: IO[str] | int | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
        text: bool = False,
        check: bool = False,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


def _default_run(
    command: list[str],
    *,
    stdin: IO[str] | int | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
    text: bool = False,
    check: bool = False,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if text:
        return subprocess.run(command, stdin=stdin, stdout=stdout, stderr=stderr, text=True, check=check, timeout=timeout, env=env, input=input)
    result = subprocess.run(
        command, stdin=stdin, stdout=stdout, stderr=stderr, text=False, check=check,
        timeout=timeout, env=env, input=input.encode() if input is not None else None,
    )
    return subprocess.CompletedProcess(
        command,
        result.returncode,
        result.stdout.decode(),
        result.stderr.decode(),
    )


class ExtensionInfo(TypedDict):
    uuid: str
    name: str
    version: str
    active: bool


class ConnectionProfile(TypedDict, total=False):
    platform_path: str
    kind: str
    server: str
    reference: str
    path: str
    dbms: str
    db_server: str
    db_name: str
    db_user: str
    db_password: str
    infobase_user: str
    infobase_password: str
    tested: bool
    profile_id: str
    tested_fingerprint: str
    extensions: list[ExtensionInfo]
    client_connection: str
    configuration: dict[str, str]
    tool_versions: dict[str, str]


class RoleBinding(TypedDict):
    connection_profile: str
    configuration_name: str
    root_uuid: str
    version: str


class ExtensionDecision(TypedDict):
    uuid: str
    decision: str
    rationale: str


class InfobasesContract(TypedDict):
    schema_version: str
    acquisition_profile: str
    roles: dict[str, RoleBinding]
    extension_decisions: list[ExtensionDecision]


class ArtifactDeclaration(TypedDict):
    role: str
    kind: str
    semantic_key: str
    filename: str
    declared_size_bytes: int
    sha256: NotRequired[str]
    external_artifact_id: NotRequired[str]


class ArtifactsContract(TypedDict):
    schema_version: str
    artifacts: list[ArtifactDeclaration]


class SourceContract(TypedDict):
    schema_version: str
    acquisition_profile_id: str
    roles: dict[str, RoleBinding]
    artifacts: list[ArtifactDeclaration]
    extension_decisions: NotRequired[list[ExtensionDecision]]


class ComponentManifest(TypedDict):
    schema_version: str
    component_id: str
    kind: str
    routing_group_id: str
    probe_contract_version: str
    probe_fingerprint: str
    form_counts: dict[str, int]
    routing_reason: str
    exporter: str
    representation_schema: str
    exporter_version: str
    converter_version: str
    payload_file_count: int
    payload_fingerprint: str
    uuid: NotRequired[str]
    name: NotRequired[str]
    version: NotRequired[str]
    active: NotRequired[bool]


class RoutingPreview(TypedDict):
    schema_version: str
    bindings: dict[str, str]
    extension_scope: ExtensionScope
    blockers: list[RoutingBlocker]
    probe_results: list[FormProbe]
    routing_manifest: RoutingManifest
    required_tools: list[str]
    routing_plan_fingerprint: str


class RoutingBlocker(TypedDict):
    code: str
    uuid: str
    roles: dict[str, ExtensionObservation]
    action: str


def routing_preview(value: dict[str, JsonValue]) -> RoutingPreview:
    scope = json_object(value.get("extension_scope"))
    return {
        "schema_version": _string(value.get("schema_version")),
        "bindings": _string_map(value.get("bindings")),
        "extension_scope": {
            "extensions": [
                {
                    "uuid": _string(row.get("uuid")),
                    "decision": _string(row.get("decision")),
                    "rationale": _string(row.get("rationale")),
                    "dormant": _boolean(row.get("dormant")),
                    "roles": {
                        role: {
                            "present": _boolean(observation.get("present")),
                            "name": _string(observation.get("name")),
                            "version": _string(observation.get("version")),
                            "active": _boolean(observation.get("active")),
                        }
                        for role, observation in ((name, json_object(item)) for name, item in json_object(row.get("roles")).items())
                    },
                }
                for row in _objects(scope.get("extensions"))
            ],
            "included": _strings(scope.get("included")),
            "excluded": _strings(scope.get("excluded")),
            "dormant": _strings(scope.get("dormant")),
            "unreviewed": _strings(scope.get("unreviewed")),
        },
        "blockers": [
            {
                "code": _string(row.get("code")),
                "uuid": _string(row.get("uuid")),
                "roles": {
                    role: {
                        "present": _boolean(observation.get("present")),
                        "name": _string(observation.get("name")),
                        "version": _string(observation.get("version")),
                        "active": _boolean(observation.get("active")),
                    }
                    for role, observation in ((name, json_object(item)) for name, item in json_object(row.get("roles")).items())
                },
                "action": _string(row.get("action")),
            }
            for row in _objects(value.get("blockers"))
        ],
        "probe_results": [_form_probe(row) for row in _objects(value.get("probe_results"))],
        "routing_manifest": _routing_manifest(json_object(value.get("routing_manifest"))),
        "required_tools": _strings(value.get("required_tools")),
        "routing_plan_fingerprint": _string(value.get("routing_plan_fingerprint")),
    }


class ComponentBase(TypedDict):
    component_id: str
    kind: str
    path: str
    fingerprint: str


class RoutedComponentRecord(ComponentBase):
    representation_schema: str
    routing_group_id: str
    bsl_file_count: int


ComponentRecord = ComponentBase | RoutedComponentRecord


class SourcePointer(TypedDict, total=False):
    schema_version: str
    generation_id: str
    acquisition_profile_id: str
    representation_schema: str
    normalizer_version: str
    source_contract_fingerprint: str
    source_contract_sha256: str
    roles: dict[str, str]
    routing_manifest_path: str
    routing_manifest_fingerprint: str
    components: list[ComponentRecord]
    source_comparison_epoch_fingerprint: str


def _toml_object(payload: object) -> dict[str, JsonValue]:
    return parse_json_object(json.dumps(payload))


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected integer")
    return value


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("expected boolean")
    return value


def _objects(value: object) -> list[dict[str, JsonValue]]:
    normalized = parse_json(canonical_json(value).decode())
    if not isinstance(normalized, list):
        raise ValueError("expected object array")
    return [json_object(item) for item in normalized]


def _strings(value: object) -> list[str]:
    normalized = parse_json(canonical_json(value).decode())
    if not isinstance(normalized, list):
        raise ValueError("expected string array")
    return [_string(item) for item in normalized]


def _string_map(value: object) -> dict[str, str]:
    return {key: _string(item) for key, item in json_object(value).items()}


def _integer_map(value: object) -> dict[str, int]:
    return {key: _integer(item) for key, item in json_object(value).items()}


def _role_binding(value: object) -> RoleBinding:
    row = json_object(value)
    return {
        "connection_profile": _string(row.get("connection_profile")),
        "configuration_name": _string(row.get("configuration_name")),
        "root_uuid": _string(row.get("root_uuid")),
        "version": _string(row.get("version")),
    }


def _extension_decision(value: object) -> ExtensionDecision:
    row = json_object(value)
    return {
        "uuid": _string(row.get("uuid")),
        "decision": _string(row.get("decision")),
        "rationale": _string(row.get("rationale")),
    }


def _infobases_contract(value: dict[str, JsonValue]) -> InfobasesContract:
    return {
        "schema_version": _string(value.get("schema_version", "1")),
        "acquisition_profile": _string(value.get("acquisition_profile", "")),
        "roles": {name: _role_binding(binding) for name, binding in json_object(value.get("roles", {})).items()},
        "extension_decisions": [_extension_decision(item) for item in _objects(value.get("extension_decisions", []))],
    }


def _artifact_declaration(value: object) -> ArtifactDeclaration:
    row = json_object(value)
    required = {"role", "kind", "semantic_key", "filename", "declared_size_bytes"}
    if not required <= row.keys() or set(row) - required - {"sha256", "external_artifact_id"}:
        raise ValueError("external artifact declared_size_bytes must be a positive integer")
    declared_size = row["declared_size_bytes"]
    if not isinstance(declared_size, int) or isinstance(declared_size, bool):
        raise ValueError("external artifact declared_size_bytes must be a positive integer")
    result = ArtifactDeclaration(
        role=_string(row.get("role")),
        kind=_string(row.get("kind")),
        semantic_key=_string(row.get("semantic_key")),
        filename=_string(row.get("filename")),
        declared_size_bytes=declared_size,
    )
    if "sha256" in row:
        result["sha256"] = _string(row["sha256"])
    if "external_artifact_id" in row:
        result["external_artifact_id"] = _string(row["external_artifact_id"])
    return result


def _artifacts_contract(value: dict[str, JsonValue]) -> ArtifactsContract:
    return {
        "schema_version": _string(value.get("schema_version", "1")),
        "artifacts": [_artifact_declaration(item) for item in _objects(value.get("artifacts", []))],
    }


def _json_contract(value: object) -> dict[str, JsonValue]:
    return parse_json_object(canonical_json(value).decode())


def _json_connections(value: dict[str, ConnectionProfile]) -> dict[str, dict[str, JsonValue]]:
    parsed = _json_contract(value)
    if not all(isinstance(profile, dict) for profile in parsed.values()):
        raise ValueError("connections must contain objects")
    return {name: profile for name, profile in parsed.items() if isinstance(profile, dict)}


def source_pointer(value: dict[str, JsonValue]) -> SourcePointer:
    if not isinstance(value.get("schema_version"), str) or not isinstance(value.get("generation_id"), str):
        raise ValueError("invalid active source pointer")
    result: SourcePointer = {}
    string_fields = (
        "schema_version", "generation_id", "acquisition_profile_id", "representation_schema",
        "normalizer_version", "source_contract_fingerprint", "source_contract_sha256",
        "routing_manifest_path", "routing_manifest_fingerprint", "source_comparison_epoch_fingerprint",
    )
    for field in string_fields:
        if field in value:
            result[field] = _string(value[field])
    if "roles" in value:
        result["roles"] = _string_map(value["roles"])
    if "components" in value:
        result["components"] = [_component_record(item) for item in _objects(value["components"])]
    return result


def _component_record(value: object) -> ComponentRecord:
    row = json_object(value)
    base = ComponentBase(
        component_id=_string(row.get("component_id")),
        kind=_string(row.get("kind")),
        path=_string(row.get("path")),
        fingerprint=_string(row.get("fingerprint")),
    )
    if "representation_schema" not in row:
        return base
    return RoutedComponentRecord(
        **base,
        representation_schema=_string(row["representation_schema"]),
        routing_group_id=_string(row.get("routing_group_id")),
        bsl_file_count=_integer(row.get("bsl_file_count")),
    )


def _source_contract(value: dict[str, JsonValue]) -> SourceContract:
    if not isinstance(value.get("roles"), dict) or not isinstance(value.get("artifacts"), list) or not isinstance(value.get("extension_decisions", []), list):
        raise ValueError("invalid source contract")
    result = SourceContract(
        schema_version=_string(value.get("schema_version")),
        acquisition_profile_id=_string(value.get("acquisition_profile_id")),
        roles={name: _role_binding(binding) for name, binding in json_object(value["roles"]).items()},
        artifacts=[_artifact_declaration(item) for item in _objects(value["artifacts"])],
    )
    if "extension_decisions" in value:
        result["extension_decisions"] = [_extension_decision(item) for item in _objects(value["extension_decisions"])]
    return result


def _component_manifest(value: dict[str, JsonValue]) -> ComponentManifest:
    if not isinstance(value.get("component_id"), str) or not isinstance(value.get("form_counts"), dict):
        raise ValueError("invalid component manifest")
    result = ComponentManifest(
        schema_version=_string(value.get("schema_version")),
        component_id=_string(value["component_id"]),
        kind=_string(value.get("kind")),
        routing_group_id=_string(value.get("routing_group_id")),
        probe_contract_version=_string(value.get("probe_contract_version")),
        probe_fingerprint=_string(value.get("probe_fingerprint")),
        form_counts=_integer_map(value["form_counts"]),
        routing_reason=_string(value.get("routing_reason")),
        exporter=_string(value.get("exporter")),
        representation_schema=_string(value.get("representation_schema")),
        exporter_version=_string(value.get("exporter_version")),
        converter_version=_string(value.get("converter_version")),
        payload_file_count=_integer(value.get("payload_file_count")),
        payload_fingerprint=_string(value.get("payload_fingerprint")),
    )
    for key in ("uuid", "name", "version"):
        if key in value:
            result[key] = _string(value[key])
    if "active" in value:
        result["active"] = _boolean(value["active"])
    return result


def _contract_without_artifact_ids(contract: SourceContract) -> SourceContract:
    artifacts: list[ArtifactDeclaration] = []
    for item in contract["artifacts"]:
        clean = item.copy()
        _ = clean.pop("external_artifact_id", None)
        artifacts.append(clean)
    return {**contract, "artifacts": artifacts}


def _routing_manifest(value: dict[str, JsonValue]) -> RoutingManifest:
    if not isinstance(value.get("groups"), list) or not isinstance(value.get("routing_manifest_fingerprint"), str):
        raise ValueError("invalid active source routing manifest")
    return {
        "schema_version": _string(value.get("schema_version")),
        "routing_contract_version": _string(value.get("routing_contract_version")),
        "groups": [_routing_group(item) for item in _objects(value["groups"])],
        "routing_manifest_fingerprint": _string(value["routing_manifest_fingerprint"]),
    }


def _form_probe(value: object) -> FormProbe:
    row = json_object(value)
    records: list[FormRecord] = []
    for item in _objects(row.get("records")):
        record = FormRecord(
            component_id=_string(item.get("component_id")),
            form_id=_string(item.get("form_id")),
            classification=_string(item.get("classification")),
            marker=_string(item.get("marker")),
        )
        if "unknown_structure_sha256" in item:
            record["unknown_structure_sha256"] = _string(item["unknown_structure_sha256"])
        records.append(record)
    return {
        "schema_version": _string(row.get("schema_version")),
        "probe_contract_version": _string(row.get("probe_contract_version")),
        "component_id": _string(row.get("component_id")),
        "records": records,
        "form_counts": _integer_map(row.get("form_counts")),
        "probe_fingerprint": _string(row.get("probe_fingerprint")),
    }


def _routing_group(value: object) -> RoutingGroup:
    row = json_object(value)
    return {
        "routing_group_id": _string(row.get("routing_group_id")),
        "kind": _string(row.get("kind")),
        "members": [
            {"role": _string(item.get("role")), "component_id": _string(item.get("component_id"))}
            for item in _objects(row.get("members"))
        ],
        "absent_roles": _strings(row.get("absent_roles")),
        "probe_contract_version": _string(row.get("probe_contract_version")),
        "probe_fingerprints": [
            {"component_id": _string(item.get("component_id")), "probe_fingerprint": _string(item.get("probe_fingerprint"))}
            for item in _objects(row.get("probe_fingerprints"))
        ],
        "form_counts": _integer_map(row.get("form_counts")),
        "routing_reason": _string(row.get("routing_reason")),
        "exporter": _string(row.get("exporter")),
        "representation_schema": _string(row.get("representation_schema")),
        "exporter_version": _string(row.get("exporter_version")),
        "converter_version": _string(row.get("converter_version")),
    }


def _public_profiles(value: dict[str, ConnectionProfile]) -> dict[str, ConnectionProfile]:
    result: dict[str, ConnectionProfile] = {}
    for name, profile in value.items():
        public = profile.copy()
        _ = public.pop("db_password", None)
        _ = public.pop("infobase_password", None)
        result[name] = public
    return result


def _valid_tested_extension(value: object) -> bool:
    try:
        item = json_object(value)
    except ValueError:
        return False
    return (
        re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", str(item.get("uuid", "")).lower()) is not None
        and bool(str(item.get("name", "")).strip())
        and type(item.get("active")) is bool
    )


def current_profile_test(profile: Mapping[str, object], profile_id: str) -> bool:
    sealed = {key: value for key, value in profile.items() if key not in {"db_password", "infobase_password", "tested_fingerprint"}}
    extensions = profile.get("extensions")
    try:
        normalized_extensions = parse_json(json.dumps(extensions))
    except (TypeError, ValueError):
        normalized_extensions = None
    valid_extensions = isinstance(normalized_extensions, list) and all(_valid_tested_extension(item) for item in normalized_extensions)
    return bool(profile.get("tested")) and profile.get("profile_id") == profile_id and valid_extensions and profile.get("tested_fingerprint") == "sha256:" + sha256(canonical_json(sealed))


def _immutable_role_fingerprint(path: str) -> str:
    return "sha256:" + sha256(canonical_json(file_manifest(Path(path))))


def load_contract(repo: Path) -> tuple[InfobasesContract, ArtifactsContract]:
    with (repo / "research/infobases.toml").open("rb") as stream:
        raw_infobases: object = tomllib.load(stream)
        infobases = _infobases_contract(_toml_object(raw_infobases))
    with (repo / "research/external-artifacts.toml").open("rb") as stream:
        raw_artifacts: object = tomllib.load(stream)
        artifacts = _artifacts_contract(_toml_object(raw_artifacts))
    reject_secrets(_json_contract(infobases), "research/infobases.toml")
    reject_secrets(_json_contract(artifacts), "research/external-artifacts.toml")
    infobases["extension_decisions"] = normalize_extension_decisions(infobases.get("extension_decisions", []))
    return infobases, artifacts


def extension_scope_status(repo: Path, connections: dict[str, ConnectionProfile]) -> dict[str, object]:
    from .source_routing import extension_scope
    infobases, _artifacts = load_contract(repo)
    selected = {
        binding["connection_profile"]: connections.get(binding["connection_profile"], {"extensions": []})
        for binding in infobases["roles"].values()
    }
    scope = extension_scope(_json_contract({"roles": infobases["roles"], "extension_decisions": infobases["extension_decisions"]}), _json_connections(selected))
    blockers = [
        {
            "code": "extension_scope_required",
            "uuid": row["uuid"],
            "roles": row["roles"],
            "action": "review_extension_scope",
        }
        for row in scope["extensions"]
        if row["uuid"] in scope["unreviewed"]
    ]
    return {
        "ready": not blockers,
        "infobases_fingerprint": "sha256:" + sha256((repo / "research/infobases.toml").read_bytes()),
        "extension_scope": scope,
        "blockers": blockers,
    }


def normalize_extension_decisions(raw: object) -> list[ExtensionDecision]:
    normalized = parse_json(canonical_json(raw).decode())
    if not isinstance(normalized, list):
        raise ValueError("extension_decisions must be an array of tables")
    decisions: list[ExtensionDecision] = []
    seen: set[str] = set()
    for item in _objects(normalized):
        if set(item) != {"uuid", "decision", "rationale"}:
            raise ValueError("extension decision must contain exactly uuid, decision, and rationale")
        uuid = str(item["uuid"])
        decision = str(item["decision"])
        rationale = unicodedata.normalize("NFC", str(item["rationale"])).strip()
        if not UUID_PATTERN.fullmatch(uuid):
            raise ValueError("extension decision UUID must be canonical lowercase UUID")
        if uuid in seen:
            raise ValueError(f"duplicate extension decision UUID: {uuid}")
        if decision not in {"include", "exclude"}:
            raise ValueError(f"unsupported extension decision: {decision}")
        if decision == "include" and rationale:
            raise ValueError("included extension rationale must be empty")
        if decision == "exclude" and not rationale:
            raise ValueError("excluded extension rationale is required")
        seen.add(uuid)
        decisions.append({"uuid": uuid, "decision": decision, "rationale": rationale})
    return sorted(decisions, key=lambda item: item["uuid"])


def serialize_infobases(infobases: InfobasesContract) -> bytes:
    decisions = normalize_extension_decisions(infobases.get("extension_decisions", []))
    lines = [
        f'schema_version = {json.dumps(str(infobases.get("schema_version", "1")))}',
        f'acquisition_profile = {json.dumps(str(infobases["acquisition_profile"]))}',
        *(["extension_decisions = []"] if not decisions else []),
        "",
    ]
    for decision in decisions:
        lines.extend((
            "[[extension_decisions]]",
            f'uuid = {json.dumps(decision["uuid"])}',
            f'decision = {json.dumps(decision["decision"])}',
            f'rationale = {json.dumps(decision["rationale"], ensure_ascii=False)}',
            "",
        ))
    for role in ROLES:
        binding = infobases["roles"][role]
        lines.extend((
            f"[roles.{role}]",
            f'connection_profile = {json.dumps(binding["connection_profile"])}',
            f'configuration_name = {json.dumps(binding["configuration_name"], ensure_ascii=False)}',
            f'root_uuid = {json.dumps(binding["root_uuid"])}',
            f'version = {json.dumps(binding["version"])}',
            "",
        ))
    return "\n".join(lines).encode()


def validate_role_contract(repo: Path, tested_profiles: dict[str, ConnectionProfile]) -> SourceContract:
    infobases, artifacts = load_contract(repo)
    profile_id = infobases.get("acquisition_profile")
    if profile_id not in PROFILES:
        raise ValueError(f"unsupported acquisition profile: {profile_id}")
    roles = infobases.get("roles", {})
    if tuple(roles) != ROLES:
        raise ValueError(f"exactly three ordered roles are required: {ROLES}")
    identities: set[str] = set(); roots: set[str] = set()
    raw_project: object = tomllib.loads((repo / "project.toml").read_text(encoding="utf-8"))
    project_document = _toml_object(raw_project)
    project = project_document.get("project")
    if not isinstance(project, dict):
        raise ValueError("project.toml project table is missing")
    expected_versions = {"vendor_baseline": project.get("baseline_version"), "target_cf": project.get("target_version"), "next_vendor": project.get("next_vendor_version")}
    for role in ROLES:
        binding = roles[role]
        required = ("connection_profile", "configuration_name", "root_uuid", "version")
        if any(not str(binding.get(key, "")).strip() for key in required):
            raise ValueError(f"incomplete infobase binding: {role}")
        if binding["version"] != expected_versions[role]:
            raise ValueError(f"role version does not match project.toml: {role}")
        roots.add(str(binding["root_uuid"]).lower())
        tested = tested_profiles.get(binding["connection_profile"])
        if not tested or not current_profile_test(tested, profile_id):
            raise ValueError(f"connection was not tested for current acquisition profile: {role}")
        extensions = tested.get("extensions")
        if not isinstance(extensions, list):
            raise ValueError(f"connection extension inventory is invalid: {role}")
        if len({str(item["uuid"]).lower() for item in extensions}) != len(extensions):
            raise ValueError(f"duplicate extension UUID in current enumeration: {role}")
        identity = normalize_connection_identity(tested)
        if identity in identities:
            raise ValueError("three distinct normalized connection identities are required")
        identities.add(identity)
    if len(roots) != 1:
        raise ValueError("all roles must share one configuration root UUID lineage")
    seen_binding: set[tuple[str, str, str]] = set(); seen_id: dict[str, tuple[str, str]] = {}
    for item in artifacts.get("artifacts", []):
        role, kind, key = item.get("role"), item.get("kind"), item.get("semantic_key")
        if role not in ROLES or kind not in {"epf", "erf", "source-tree", "other"} or not key or key != unicodedata.normalize("NFC", key):
            raise ValueError("invalid external artifact declaration")
        filename = item.get("filename")
        if normalize_relative(filename) != filename:
            raise ValueError("invalid external artifact filename")
        declared_size = item.get("declared_size_bytes")
        if isinstance(declared_size, bool) or declared_size <= 0:
            raise ValueError("external artifact declared_size_bytes must be a positive integer")
        expected_sha256 = item.get("sha256")
        if expected_sha256 is not None and not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", str(expected_sha256)):
            raise ValueError("invalid external artifact SHA-256")
        binding_key = (role, kind, key)
        if binding_key in seen_binding:
            raise ValueError(f"duplicate external artifact binding: {binding_key}")
        seen_binding.add(binding_key)
        identifier = external_id(kind, key)
        preimage = (kind, key)
        if identifier in seen_id and seen_id[identifier] != preimage:
            raise ValueError(f"external artifact ID collision: {identifier}")
        seen_id[identifier] = preimage
        item["external_artifact_id"] = identifier
    return {
        "schema_version": "2",
        "acquisition_profile_id": profile_id,
        "roles": roles,
        "artifacts": artifacts.get("artifacts", []),
        "extension_decisions": infobases["extension_decisions"],
    }


def normalize_connection_identity(profile: ConnectionProfile) -> str:
    kind = profile.get("kind")
    if kind == "server":
        server = str(profile.get("server", "")).strip().lower()
        reference = str(profile.get("reference", "")).strip().lower()
        if not server or not reference:
            raise ValueError("server connection identity is incomplete")
        return f"server:{server}/{reference}"
    if kind == "file":
        path = Path(str(profile.get("path", ""))).expanduser().resolve()
        return f"file:{path}"
    raise ValueError(f"unsupported connection kind: {kind}")


def _ibcmd_db_args(connection: ConnectionProfile) -> list[str]:
    required = ("dbms", "db_server", "db_name", "db_user", "db_password")
    if any(key not in connection for key in required):
        raise ValueError("incomplete ibcmd DBMS connection profile")
    values = [connection.get(key) for key in required]
    if not all(isinstance(value, str) for value in values):
        raise ValueError("incomplete ibcmd DBMS connection profile")
    dbms, server, name, user, password = values
    return [f"--dbms={dbms}", f"--db-server={server}", f"--db-name={name}", f"--db-user={user}", f"--db-pwd={password}"]


def run_command(
    run: CommandRunner,
    command: list[str],
    *,
    cancelled: CancelCallback | None = None,
    stdin: IO[str] | int | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
    text: bool = False,
    check: bool = False,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if cancelled and cancelled():
        raise InterruptedError("source acquisition cancelled")
    if run is not _default_run and run is not subprocess.run or cancelled is None:
        result = run(command, stdin=stdin, stdout=stdout, stderr=stderr, text=text, check=check, timeout=timeout, env=env, input=input)
        if cancelled and cancelled():
            raise InterruptedError("source acquisition cancelled")
        return result
    started = time.monotonic()
    if input is not None:
        stdin = subprocess.PIPE
    process = subprocess.Popen(
        command, stdin=stdin, stdout=stdout, stderr=stderr, text=True, env=env,
        start_new_session=os.name == "posix",
    )

    def stop(force: bool = False) -> None:
        try:
            terminate_process(process, force=force)
        except ProcessLookupError:
            pass

    first_communicate = True
    while True:
        try:
            stdout_value, stderr_value = process.communicate(input=input if first_communicate else None, timeout=0.2)
            return subprocess.CompletedProcess(command, process.returncode, stdout_value, stderr_value)
        except subprocess.TimeoutExpired:
            first_communicate = False
            if cancelled():
                stop()
                try:
                    _ = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    stop(force=True); _ = process.communicate()
                raise InterruptedError("source acquisition cancelled")
            if timeout is not None and time.monotonic() - started >= timeout:
                stop(force=True); _ = process.communicate()
                raise subprocess.TimeoutExpired(command, timeout)


# Kept for callers from releases before the helper became public.
_run_command = run_command


def _toolchain_versions(profile_id: str, platform: Path, *, timeout_seconds: float, run: CommandRunner, cancelled: CancelCallback | None = None) -> dict[str, str]:
    from .source_tools import classify_version
    if profile_id in PROFILES:
        exporter, representation = PROFILES[profile_id], None
    else:
        exporter, representation = LEGACY_PROFILES[profile_id]
    ibcmd = run_command(run, [str(platform / "ibcmd"), "--version"], cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
    status, platform_version, _reason = classify_version("ibcmd", platform / "ibcmd", ibcmd.returncode, ibcmd.stdout)
    if status != "ready":
        raise RuntimeError("ibcmd version preflight failed")
    versions = {"platform": platform_version, "exporter": exporter}
    if exporter == "designer" and not (platform / "1cv8").is_file():
        raise RuntimeError("Designer executable preflight failed")
    if representation == "v8unpack":
        executable = shutil.which("v8unpack")
        if not executable:
            raise RuntimeError("v8unpack executable is unavailable")
        result = run_command(run, [executable, "-h"], cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
        status, version, _reason = classify_version("v8unpack", Path(executable), result.returncode, result.stdout)
        if status != "ready":
            raise RuntimeError(f"v8unpack {V8UNPACK_VERSION} is required")
        versions["converter"] = f"v8unpack {version}"
    elif representation == "edt-project":
        executable = shutil.which("1cedtcli")
        resolved = str(Path(executable).resolve()) if executable else ""
        status, version, _reason = classify_version("edt", Path(resolved), 0, "")
        if status != "ready":
            raise RuntimeError(f"1C:EDT {EDT_VERSION} is required")
        versions["converter"] = f"1C:EDT {version}"
    return versions


def preflight_connection(profile_id: str, platform: Path, connection: ConnectionProfile, *, timeout_seconds: float = 120, run: CommandRunner = _default_run, cancelled: CancelCallback | None = None, identity_staging: Path | None = None) -> ConnectionProfile:
    if profile_id not in PROFILES:
        raise ValueError("unsupported acquisition profile")
    started = time.monotonic()
    def remaining() -> float:
        value = timeout_seconds - (time.monotonic() - started)
        if value <= 0:
            raise TimeoutError("extension discovery overall operation bound exceeded")
        return value
    exporter = PROFILES[profile_id]
    executable = platform / "ibcmd"
    toolchain_versions = _toolchain_versions(profile_id, platform, timeout_seconds=remaining(), run=run, cancelled=cancelled)
    with tempfile.TemporaryDirectory(prefix="ibcmd-preflight-") as data_dir, tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8") as credentials:
        _ = credentials.write(f"{connection.get('infobase_user', '')}\n{connection.get('infobase_password', '')}\n"); credentials.flush(); _ = credentials.seek(0)
        command = [str(executable), "extension", "list", f"--data={data_dir}", *_ibcmd_db_args(connection)]
        listed = run_command(run, command, cancelled=cancelled, stdin=credentials, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=remaining(), env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
    if listed.returncode:
        raise RuntimeError("infobase authentication or extension enumeration preflight failed")
    if len(listed.stdout.encode("utf-8")) > MAX_EXTENSION_LIST_BYTES:
        raise ValueError("extension discovery response-size bound exceeded")
    cleaned = listed.stdout.replace("Authentication in the infobase is required to perform the operation", "").replace("User:", "").replace("Password:", "")
    extensions: list[ExtensionInfo] = []
    current: dict[str, str] = {}
    for line in (line.strip() for line in cleaned.splitlines() if line.strip()):
        if ":" not in line: raise RuntimeError("unsupported ibcmd extension list output")
        key, value = (part.strip() for part in line.split(":", 1)); value = value.strip('"')
        if key == "name" and current:
            extensions.append({"uuid": "", "name": current["name"], "version": current.get("version", ""), "active": current.get("active") == "yes"}); current = {}
        current[key] = value
    if current:
        extensions.append({"uuid": "", "name": current["name"], "version": current.get("version", ""), "active": current.get("active") == "yes"})
    if len(extensions) > MAX_EXTENSION_COUNT:
        raise ValueError("extension discovery extension-count bound exceeded")
    context = tempfile.TemporaryDirectory(prefix="extension-identities-") if identity_staging is None else nullcontext(str(identity_staging))
    with context as temporary:
        _ = Path(temporary).mkdir(parents=True, exist_ok=True, mode=0o700)
        configuration_root = Path(temporary) / "configuration"
        command = adapter_plan(f"{exporter}+xml-hierarchical/v1", platform, connection, configuration_root, timeout_seconds=int(remaining()))[0]
        exported = run_command(run, command, cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=remaining(), env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
        if exported.returncode:
            raise RuntimeError("configuration identity export failed")
        configuration = configuration_identity(configuration_root)
        for index, extension in enumerate(extensions):
            output = Path(temporary) / str(index)
            command = adapter_plan(f"{exporter}+xml-hierarchical/v1", platform, connection, output, extension["name"], timeout_seconds=int(remaining()))[0]
            exported = run_command(run, command, cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=remaining(), env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
            if exported.returncode:
                raise RuntimeError(f"extension identity export failed: {extension['name']}")
            identity = configuration_identity(output)
            if identity["name"] != extension["name"] or extension["version"] and identity["version"] != extension["version"]:
                raise RuntimeError(f"extension identity export does not match enumeration: {extension['name']}")
            extension["uuid"] = identity["uuid"]
            if identity_staging is not None:
                destination = identity_staging / "extensions" / identity["uuid"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(output, destination)
    if len({item["uuid"] for item in extensions}) != len(extensions):
        raise RuntimeError("extension enumeration produced duplicate UUIDs")
    tested = connection.copy()
    for key in ("db_password", "infobase_password", "tested", "extensions", "tested_fingerprint"):
        _ = tested.pop(key, None)
    tested.update({"profile_id": profile_id, "tested": True, "configuration": configuration, "extensions": extensions, "tool_versions": toolchain_versions})
    tested["tested_fingerprint"] = "sha256:" + sha256(canonical_json(tested))
    return tested


def configuration_uuid(root: Path) -> str:
    return configuration_identity(root)["uuid"]


def configuration_identity(root: Path) -> dict[str, str]:
    path = next((candidate for candidate in (root / "Configuration.xml", root / "src/Configuration/Configuration.mdo") if candidate.is_file()), None)
    if path is None:
        raise ValueError("exported configuration root is missing")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("DTD and external entities are forbidden in exported XML")
    import xml.etree.ElementTree as ET
    element = ET.fromstring(raw)
    configuration = element if element.tag.rsplit("}", 1)[-1] == "Configuration" else next((item for item in element if item.tag.rsplit("}", 1)[-1] == "Configuration"), None)
    if configuration is None:
        raise ValueError("exported configuration root UUID is missing or invalid")
    identifier = str(configuration.attrib.get("uuid", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", identifier):
        raise ValueError("exported configuration root UUID is missing or invalid")
    properties = next((item for item in configuration if item.tag.rsplit("}", 1)[-1] == "Properties"), None)
    values = {item.tag.rsplit("}", 1)[-1].lower(): (item.text or "").strip() for item in (properties if properties is not None else configuration)}
    if not values.get("name"):
        raise ValueError("exported configuration name is missing")
    return {"uuid": identifier, "name": values["name"], "version": values.get("version", "")}


def adapter_plan(profile_id: str, platform: Path, connection: ConnectionProfile, output: Path, extension_name: str | None = None, *, representation: str | None = None, workspace: Path | None = None, project_name: str | None = None, timeout_seconds: float = 1800) -> list[list[str]]:
    if profile_id in PROFILES:
        exporter = PROFILES[profile_id]
        if representation not in {"xml-hierarchical", "v8unpack"}:
            raise ValueError("form-aware adapter requires a routed representation")
    elif profile_id in LEGACY_PROFILES:
        exporter, representation = LEGACY_PROFILES[profile_id]
    else:
        raise ValueError(f"unsupported acquisition profile: {profile_id}")
    output = output.resolve()
    extension = [f"--extension={extension_name}"] if extension_name else []
    if exporter == "ibcmd":
        intermediate = output.with_name(output.name + ".xml-staging") if representation == "edt-project" else output if representation != "v8unpack" else output.with_suffix(".cfe" if extension_name else ".cf")
        verb = "export" if representation != "v8unpack" else "save"
        required = ("dbms", "db_server", "db_name", "db_user", "db_password", "infobase_user", "infobase_password")
        if any(key not in connection for key in required):
            raise ValueError("incomplete ibcmd connection profile")
        infobase_user, infobase_password = connection.get("infobase_user"), connection.get("infobase_password")
        if not isinstance(infobase_user, str) or not isinstance(infobase_password, str):
            raise ValueError("incomplete ibcmd connection profile")
        commands = [[str(platform / "ibcmd"), "config", verb, *_ibcmd_db_args(connection), f"--user={infobase_user}", f"--password={infobase_password}", *(["--threads=1"] if verb == "export" else []), *extension, str(intermediate)]]
    else:
        intermediate = output.with_name(output.name + ".xml-staging") if representation == "edt-project" else output if representation != "v8unpack" else output.with_suffix(".cfe" if extension_name else ".cf")
        switch = "/DumpConfigToFiles" if representation != "v8unpack" else "/DumpCfg"
        client_connection = connection.get("client_connection")
        infobase_user = connection.get("infobase_user")
        infobase_password = connection.get("infobase_password")
        if not all(isinstance(value, str) for value in (client_connection, infobase_user, infobase_password)):
            raise ValueError("incomplete Designer connection profile")
        assert isinstance(client_connection, str) and isinstance(infobase_user, str) and isinstance(infobase_password, str)
        commands = [[str(platform / "1cv8"), "DESIGNER", client_connection, f"/N{infobase_user}", f"/P{infobase_password}", switch, str(intermediate)]]
        if representation != "v8unpack":
            commands[0] += ["-Format", "Hierarchical"]
        if extension_name:
            commands[0] += ["-Extension", extension_name]
    if representation == "v8unpack":
        commands.append(["v8unpack", "-E", str(intermediate), str(output), "--temp", str(output.with_name(f".{output.name}.v8unpack-temp")), "--processes", "1"])
    elif representation == "edt-project":
        workspace = (workspace or output.with_name(f".{output.name}.edt-workspace")).resolve()
        project_name = project_name or ("base" if not extension_name else f"ext-{sha256(extension_name.encode())[:16]}")
        platform_version = ".".join(platform.name.split(".")[:3])
        commands.append(["1cedtcli", "-data", str(workspace), "-timeout", str(timeout_seconds), "-command", "import", "--version", platform_version, "--base-project-name", "base", "--configuration-files", str(intermediate), "--project-name", project_name, "--build", "false"])
        commands.append(["1cedtcli", "-data", str(workspace), "-timeout", str(timeout_seconds), "-command", "sort-project", "--project-name-list", project_name])
        commands.append(["1cedtcli", "-data", str(workspace), "-timeout", str(timeout_seconds), "-command", "clean-up-source", "--project-name", project_name])
        commands.append(["1cedtcli", "-data", str(workspace), "-timeout", str(timeout_seconds), "-command", "validate", "--file", str(workspace.parent / f"{project_name}-validation.tsv"), "--project-name-list", project_name])
    return commands


def clean_payload(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.name in NOISE_NAMES:
            shutil.rmtree(path) if path.is_dir() else path.unlink()


def reject_links_and_special_files(root: Path) -> None:
    import stat
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if path.is_symlink() or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError("source payload contains a link or special file")


def normalize_payload(root: Path) -> None:
    volatile_period = re.compile(br"(<pl:(?:begin|end)>)(\d{4}-\d{2}-\d{2})(T\d{2}:\d{2}:\d{2}</pl:(?:begin|end)>)")
    for path in root.rglob("*.xml"):
        raw = path.read_bytes()
        normalized = volatile_period.sub(br"\g<1>2000-01-01\g<3>", raw)
        if normalized != raw:
            _ = path.write_bytes(normalized)


def _reject_secret_content(root: Path) -> None:
    secret_value = br"(?:'[^'\r\n]{8,}'|\"[^\"\r\n]{8,}\"|[A-Za-z0-9_./+=-]{8,})"
    patterns = (
        re.compile(br"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(br"(?i)\b(?:password|passwd|pwd|token|secret)\s*[:=]\s*" + secret_value),
    )
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.stat().st_size <= 16 * 1024 * 1024:
            raw = path.read_bytes()
            if any(pattern.search(raw) for pattern in patterns):
                raise ValueError(f"external artifact contains a detected secret: {path.relative_to(root).as_posix()}")


def _extract_source_tree(source: Path, destination: Path) -> None:
    import tarfile
    import zipfile
    destination.mkdir(parents=True)
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for item in sorted(archive.infolist(), key=lambda value: value.filename):
                relative = normalize_relative(item.filename.rstrip("/")) if item.filename.rstrip("/") else ""
                if not relative:
                    continue
                mode = item.external_attr >> 16
                if mode and (mode & 0o170000) not in {0, 0o040000, 0o100000}:
                    raise ValueError("source-tree archive contains a link or special file")
                target = confined(destination, relative)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as input_stream, target.open("xb") as output_stream:
                        _ = shutil.copyfileobj(input_stream, output_stream)
    elif tarfile.is_tarfile(source):
        with tarfile.open(source, "r:*") as archive:
            for item in sorted(archive.getmembers(), key=lambda value: value.name):
                relative = normalize_relative(item.name.rstrip("/")) if item.name.rstrip("/") else ""
                if not relative:
                    continue
                target = confined(destination, relative)
                if item.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif item.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    input_stream = archive.extractfile(item)
                    if input_stream is None:
                        raise ValueError("source-tree archive member is unreadable")
                    with input_stream, target.open("xb") as output_stream:
                        _ = shutil.copyfileobj(input_stream, output_stream)
                else:
                    raise ValueError("source-tree archive contains a link or special file")
    else:
        raise ValueError("source-tree upload must be a ZIP or TAR archive")
    if not file_manifest(destination):
        raise ValueError("source-tree archive is empty")
    _reject_secret_content(destination)


def stream_upload(stream: BinaryIO, drafts_root: Path, relative_name: str, declared_length: int, declared_sha256: str | None, expected_draft_fingerprint: str | None = None, cancelled: CancelCallback | None = None) -> dict[str, str | int]:
    relative = normalize_relative(relative_name)
    target = confined(drafts_root, relative)
    if shutil.disk_usage(drafts_root).free < declared_length + 1024**3:
        raise OSError("external upload requires declared bytes plus 1 GiB free space")
    if expected_draft_fingerprint is not None and draft_fingerprint(drafts_root) != expected_draft_fingerprint:
        raise RuntimeError("stale upload draft fingerprint")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.partial")
    import hashlib
    digest = hashlib.sha256(); written = 0
    try:
        with temporary.open("xb") as output:
            while True:
                if cancelled and cancelled():
                    raise InterruptedError("upload cancelled")
                chunk = stream.read(min(1024 * 1024, declared_length - written + 1))
                if not chunk:
                    break
                written += len(chunk)
                if written > declared_length:
                    raise ValueError("upload exceeds declared length")
                digest.update(chunk); _ = output.write(chunk)
            output.flush(); _ = os.fsync(output.fileno())
        actual = digest.hexdigest()
        if written != declared_length or declared_sha256 and actual != declared_sha256.lower():
            raise ValueError("external upload length or SHA-256 mismatch")
        os.replace(temporary, target)
        return {"path": relative, "size_bytes": written, "sha256": actual, "draft_fingerprint": draft_fingerprint(drafts_root)}
    finally:
        temporary.unlink(missing_ok=True)


def draft_fingerprint(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    return "sha256:" + sha256(canonical_json(file_manifest(root)))


def routing_bindings(repo: Path, connections: dict[str, ConnectionProfile], upload_drafts: Path | None) -> dict[str, str]:
    from .source_routing import PROBE_CONTRACT_VERSION
    workflow_state = owned_function(".workflow", "state_fingerprint")(repo)
    if not isinstance(workflow_state, str):
        raise RuntimeError("workflow state fingerprint is invalid")
    safe_connections = {
        name: {key: value for key, value in profile.items() if key not in {"db_password", "infobase_password"}}
        for name, profile in sorted(connections.items())
    }
    return {
        "workflow": "sha256:" + sha256((repo / "research/workflow.toml").read_bytes()),
        "workflow_state": workflow_state,
        "infobases": "sha256:" + sha256((repo / "research/infobases.toml").read_bytes()),
        "external_artifacts": "sha256:" + sha256((repo / "research/external-artifacts.toml").read_bytes()),
        "connections": "sha256:" + sha256(canonical_json(safe_connections)),
        "upload_draft": draft_fingerprint(upload_drafts) if upload_drafts else "sha256:" + sha256(canonical_json([])),
        "probe_contract": PROBE_CONTRACT_VERSION,
    }


def _execute_plan(commands: list[list[str]], *, run: CommandRunner, timeout_seconds: float, cancelled: CancelCallback | None, subject: str) -> None:
    for command in commands:
        result = run_command(run, command, cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
        if result.returncode:
            raise RuntimeError(f"source_route_failed:{subject}")


def _check_cancelled(cancelled: CancelCallback | None, phase: str) -> None:
    if cancelled and cancelled():
        raise InterruptedError(f"source acquisition cancelled during {phase}")


def _verified_artifact_source(upload_drafts: Path | None, member: ComponentMember) -> Path:
    artifact = member.get("artifact")
    if artifact is None:
        raise ValueError(f"external artifact declaration is unavailable: {member.get('name', '')}")
    if not upload_drafts:
        raise ValueError(f"external artifact upload draft is unavailable: {member['name']}")
    filename = normalize_relative(str(artifact["filename"]))
    source = confined(upload_drafts, f"{member['role']}/{member['name']}/{filename}")
    if not source.is_file() or source.stat().st_size != artifact["declared_size_bytes"]:
        raise ValueError(f"external artifact upload does not match declaration: {member['name']}")
    import hashlib
    hasher = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if artifact.get("sha256") and digest != str(artifact["sha256"]).removeprefix("sha256:").lower():
        raise ValueError(f"external artifact upload does not match declaration: {member['name']}")
    return source


def build_routing_preview(repo: Path, platform: Path, connections: dict[str, ConnectionProfile], *, timeout_seconds: float = 1800, run: CommandRunner = _default_run, upload_drafts: Path | None = None, cancelled: CancelCallback | None = None, progress: ProgressCallback | None = None, identity_staging: dict[str, Path] | None = None) -> RoutingPreview:
    from .source_routing import analyze_forms, component_members, extension_scope, plan_groups
    tested = _public_profiles(connections)
    contract = validate_role_contract(repo, tested)
    scope = extension_scope(_json_contract(contract), _json_connections(connections))
    bindings = routing_bindings(repo, connections, upload_drafts)
    if scope["unreviewed"]:
        manifest = plan_groups([], PROFILES[contract["acquisition_profile_id"]], {})
        blockers: list[RoutingBlocker] = [{
            "code": "extension_scope_required",
            "uuid": uuid,
            "roles": next(item["roles"] for item in scope["extensions"] if item["uuid"] == uuid),
            "action": "review_extension_scope",
        } for uuid in scope["unreviewed"]]
        preview: RoutingPreview = {
            "schema_version": "1",
            "bindings": bindings,
            "extension_scope": scope,
            "blockers": blockers,
            "probe_results": [],
            "routing_manifest": manifest,
            "required_tools": [],
            "routing_plan_fingerprint": "",
        }
        preview["routing_plan_fingerprint"] = "sha256:" + sha256(canonical_json({key: value for key, value in preview.items() if key != "routing_plan_fingerprint"}))
        return preview
    exporter = PROFILES[contract["acquisition_profile_id"]]
    members = component_members(_json_contract(contract), _json_connections(connections))
    with tempfile.TemporaryDirectory(prefix="source-routing-preview-") as temporary:
        root = Path(temporary)
        for index, member in enumerate(members):
            if progress:
                progress({"phase": "probe", "completed": index, "total": len(members), "subject": member["routing_group_id"]})
            _check_cancelled(cancelled, "probe")
            if member["kind"] in {"source-tree", "other"}:
                continue
            output = root / member["role"] / member["routing_group_id"].replace(":", "-")
            output.parent.mkdir(parents=True, exist_ok=True)
            if member["kind"] in {"configuration", "extension"}:
                staged = identity_staging.get(member["role"]) if identity_staging else None
                extension = member.get("extension")
                if member["kind"] == "extension" and extension is None:
                    raise ValueError("extension routing member has no extension identity")
                if staged:
                    if member["kind"] == "configuration":
                        relative = "configuration"
                    else:
                        assert extension is not None
                        relative = f"extensions/{extension['uuid']}"
                    staged = staged / relative
                if staged and staged.is_dir():
                    _ = shutil.copytree(staged, output)
                else:
                    connection = connections[contract["roles"][member["role"]]["connection_profile"]]
                    _execute_plan(
                        adapter_plan(contract["acquisition_profile_id"], platform, connection, output, member["name"], representation="xml-hierarchical", timeout_seconds=timeout_seconds),
                        run=run,
                        timeout_seconds=timeout_seconds,
                        cancelled=cancelled,
                        subject=member["routing_group_id"],
                    )
            else:
                source = _verified_artifact_source(upload_drafts, member)
                _execute_plan(
                    [["v8unpack", "-E", str(source), str(output), "--temp", str(output.with_name(f".{output.name}.temp")), "--processes", "1"]],
                    run=run,
                    timeout_seconds=timeout_seconds,
                    cancelled=cancelled,
                    subject=member["routing_group_id"],
                )
            if not output.is_dir() or not any(path.is_file() for path in output.rglob("*")):
                raise ValueError(f"source_probe_empty:{member['routing_group_id']}")
            reject_links_and_special_files(output)
            clean_payload(output)
            normalize_payload(output)
            _reject_secret_content(output)
            if member["kind"] in {"configuration", "extension"}:
                identity = configuration_identity(output)
                extension = member.get("extension")
                if member["kind"] == "extension" and extension is None:
                    raise ValueError("extension routing member has no extension identity")
                if member["kind"] == "configuration":
                    expected_uuid = contract["roles"][member["role"]]["root_uuid"].lower()
                    expected_name = contract["roles"][member["role"]]["configuration_name"]
                    expected_version = contract["roles"][member["role"]]["version"]
                else:
                    assert extension is not None
                    uuid_value, name_value, version_value = extension.get("uuid"), extension.get("name"), extension.get("version")
                    if not isinstance(uuid_value, str) or not isinstance(name_value, str) or not isinstance(version_value, str):
                        raise ValueError(f"source_probe_identity_mismatch:{member['routing_group_id']}")
                    expected_uuid, expected_name, expected_version = uuid_value.lower(), name_value, version_value
                if identity["uuid"] != expected_uuid or identity["name"] != expected_name or expected_version and identity["version"] != expected_version:
                    raise ValueError(f"source_probe_identity_mismatch:{member['routing_group_id']}")
            member["probe"] = analyze_forms(output, member["component_id"])
        if progress:
            progress({"phase": "route", "completed": len(members), "total": len(members), "subject": ""})
        _check_cancelled(cancelled, "route")
        tool_versions = _toolchain_versions(contract["acquisition_profile_id"], platform, timeout_seconds=min(timeout_seconds, 120), run=run, cancelled=cancelled)
        manifest = plan_groups(members, exporter, tool_versions)
        if any(group["representation_schema"] == "v8unpack/v1" for group in manifest["groups"]):
            converter = _toolchain_versions(f"{exporter}+v8unpack/v1", platform, timeout_seconds=min(timeout_seconds, 120), run=run, cancelled=cancelled)
            for group in manifest["groups"]:
                if group["representation_schema"] == "v8unpack/v1":
                    group["converter_version"] = converter["converter"]
            _ = manifest.pop("routing_manifest_fingerprint", None)
            manifest["routing_manifest_fingerprint"] = "sha256:" + sha256(canonical_json(manifest))
    probe_results = sorted((member["probe"] for member in members if "probe" in member), key=lambda item: item["component_id"])
    required_tools = sorted({group["exporter"] for group in manifest["groups"] if group["exporter"] in {"ibcmd", "designer"}} | ({"v8unpack"} if any(group["representation_schema"] == "v8unpack/v1" for group in manifest["groups"]) else set()))
    result: RoutingPreview = {
        "schema_version": "1",
        "bindings": bindings,
        "extension_scope": scope,
        "blockers": [],
        "probe_results": probe_results,
        "routing_manifest": manifest,
        "required_tools": required_tools,
        "routing_plan_fingerprint": "",
    }
    result["routing_plan_fingerprint"] = "sha256:" + sha256(canonical_json({key: value for key, value in result.items() if key != "routing_plan_fingerprint"}))
    return result


def acquire(repo: Path, platform: Path, connections: dict[str, ConnectionProfile], *, routing_preview: RoutingPreview, normalizer_version: str = NORMALIZER_VERSION, timeout_seconds: float = 1800, run: CommandRunner = _default_run, upload_drafts: Path | None = None, cancelled: CancelCallback | None = None, progress: ProgressCallback | None = None, activate: bool = True) -> SourcePointer:
    if shutil.disk_usage(repo).free < MINIMUM_FREE_BYTES:
        raise OSError("source acquisition requires at least 1 GiB free space")
    staging_parent = repo / "sources/.staging"; staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="extension-verification-", dir=staging_parent) as temporary:
        return _acquire_verified(
            repo, platform, connections, routing_preview=routing_preview,
            normalizer_version=normalizer_version, timeout_seconds=timeout_seconds,
            run=run, upload_drafts=upload_drafts, cancelled=cancelled,
            progress=progress, activate=activate, identity_root=Path(temporary),
        )


def _acquire_verified(repo: Path, platform: Path, connections: dict[str, ConnectionProfile], *, routing_preview: RoutingPreview, normalizer_version: str, timeout_seconds: float, run: CommandRunner, upload_drafts: Path | None, cancelled: CancelCallback | None, progress: ProgressCallback | None, activate: bool, identity_root: Path) -> SourcePointer:
    deadline = time.monotonic() + timeout_seconds
    remaining = lambda: deadline - time.monotonic()
    tested = _public_profiles(connections)
    contract = validate_role_contract(repo, tested)
    if routing_preview.get("blockers"):
        raise RuntimeError("extension_scope_required")
    for index, role in enumerate(ROLES, 1):
        if progress:
            progress({"phase": "connections", "completed": index - 1, "total": len(ROLES), "subject": role})
        available = remaining()
        if available <= 0:
            raise TimeoutError("extension-discovery overall-time bound exhausted")
        profile_name = contract["roles"][role]["connection_profile"]
        fresh = preflight_connection(contract["acquisition_profile_id"], platform, connections[profile_name], timeout_seconds=min(available, 120), run=run, cancelled=cancelled, identity_staging=identity_root / role)
        if canonical_json(fresh.get("extensions", [])) != canonical_json(connections[profile_name].get("extensions", [])):
            raise RuntimeError("extension_inventory_stale")
    included = {item["uuid"] for item in contract.get("extension_decisions", []) if item["decision"] == "include"}
    for role in ROLES:
        root = identity_root / role / "extensions"
        if root.is_dir():
            for path in root.iterdir():
                if path.name not in included:
                    shutil.rmtree(path)
    staged_by_role = {role: identity_root / role for role in ROLES}
    available = remaining()
    if available <= 0:
        raise TimeoutError("extension-discovery overall-time bound exhausted")
    fresh_preview = build_routing_preview(repo, platform, connections, timeout_seconds=available, run=run, upload_drafts=upload_drafts, cancelled=cancelled, progress=progress, identity_staging=staged_by_role)
    if remaining() <= 0:
        raise TimeoutError("extension-discovery overall-time bound exhausted")
    if fresh_preview.get("blockers") or routing_preview.get("routing_plan_fingerprint") != fresh_preview["routing_plan_fingerprint"]:
        raise RuntimeError("extension_inventory_stale" if fresh_preview.get("extension_scope") != routing_preview.get("extension_scope") else "routing_preview_stale")
    from .source_routing import component_members, source_comparison_epoch
    members = component_members(_json_contract(contract), _json_connections(connections))
    groups = {item["routing_group_id"]: item for item in fresh_preview["routing_manifest"]["groups"]}
    probes = {item["component_id"]: item for item in fresh_preview["probe_results"]}
    pointer_path = repo / "research/active-source-generation.json"
    generation_value = parse_json_object(pointer_path.read_text(encoding="utf-8")).get("generation_id") if pointer_path.is_file() else None
    expected_generation = generation_value if isinstance(generation_value, str) else None
    staging_parent = repo / "sources/.staging"; staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="acquire-", dir=staging_parent) as temporary:
        staging = Path(temporary)
        work_root = staging / ".work"; work_root.mkdir()
        for role in ROLES:
            (staging / role).mkdir()
        for index, member in enumerate(members, 1):
            identity: dict[str, str] | None = None
            if progress:
                progress({"phase": "export", "completed": index - 1, "total": len(members), "subject": member["component_id"]})
            _check_cancelled(cancelled, "export")
            group = groups[member["routing_group_id"]]
            role = member["role"]
            if member["kind"] == "configuration":
                output = staging / role / "configuration"
            elif member["kind"] == "extension":
                output = staging / role / "extensions" / member["routing_group_id"].split(":", 1)[1]
            else:
                name = member.get("name")
                if name is None:
                    raise ValueError("external routing member has no name")
                output = staging / role / "external" / name
            output.parent.mkdir(parents=True, exist_ok=True)
            work_output = work_root / role / member["routing_group_id"].replace(":", "-")
            if member["kind"] in {"configuration", "extension"}:
                representation = group["representation_schema"].removesuffix("/v1")
                extension = member.get("extension")
                if member["kind"] == "extension" and extension is None:
                    raise ValueError("extension routing member has no extension identity")
                if member["kind"] == "configuration":
                    verified = identity_root / role / "configuration"
                else:
                    assert extension is not None
                    verified = identity_root / role / f"extensions/{extension['uuid']}"
                if representation == "xml-hierarchical" and verified.is_dir():
                    _ = shutil.copytree(verified, work_output)
                else:
                    connection = connections[contract["roles"][role]["connection_profile"]]
                    _execute_plan(
                        adapter_plan(contract["acquisition_profile_id"], platform, connection, work_output, member["name"], representation=representation, timeout_seconds=timeout_seconds),
                        run=run,
                        timeout_seconds=timeout_seconds,
                        cancelled=cancelled,
                        subject=member["routing_group_id"],
                    )
                if not work_output.is_dir() or not any(path.is_file() for path in work_output.rglob("*")):
                    raise ValueError(f"source_export_empty:{member['routing_group_id']}")
                reject_links_and_special_files(work_output)
                _ = shutil.copytree(work_output, output)
                normalize_payload(output)
                identity = configuration_identity(output)
                if member["kind"] == "configuration":
                    expected = contract["roles"][role]
                    if (identity["uuid"], identity["name"], identity["version"]) != (str(expected["root_uuid"]).lower(), expected["configuration_name"], expected["version"]):
                        raise ValueError(f"source_identity_mismatch:{member['routing_group_id']}")
                else:
                    assert extension is not None
                    if identity["uuid"] != str(extension["uuid"]).lower() or identity["name"] != extension["name"] or extension.get("version") and identity["version"] != extension["version"]:
                        raise ValueError(f"source_identity_mismatch:{member['routing_group_id']}")
            else:
                source = _verified_artifact_source(upload_drafts, member)
                output.mkdir()
                if member["kind"] in {"epf", "erf"}:
                    extracted = output / "source"
                    _execute_plan(
                        [["v8unpack", "-E", str(source), str(extracted), "--temp", str(work_output.with_suffix(".temp")), "--processes", "1"]],
                        run=run,
                        timeout_seconds=timeout_seconds,
                        cancelled=cancelled,
                        subject=member["routing_group_id"],
                    )
                    if not extracted.is_dir() or not any(path.is_file() for path in extracted.rglob("*")):
                        raise ValueError(f"source_export_empty:{member['routing_group_id']}")
                    reject_links_and_special_files(extracted)
                    clean_payload(extracted); normalize_payload(extracted); _reject_secret_content(extracted)
                elif member["kind"] == "source-tree":
                    _extract_source_tree(source, output / "source")
                else:
                    artifact = member.get("artifact")
                    filename = artifact.get("filename") if artifact is not None else None
                    if not isinstance(filename, str):
                        raise ValueError("external routing member has no artifact filename")
                    _ = shutil.copy2(source, output / normalize_relative(filename))
                    _reject_secret_content(output)
            _check_cancelled(cancelled, "validation")
            clean_payload(output)
            payload = file_manifest(output)
            probe = probes.get(member["component_id"])
            metadata: dict[str, object] = {
                "schema_version": "2",
                "component_id": member["component_id"],
                "kind": member["kind"],
                "routing_group_id": member["routing_group_id"],
                "probe_contract_version": probe.get("probe_contract_version", "") if probe else "",
                "probe_fingerprint": probe.get("probe_fingerprint", "") if probe else "",
                "form_counts": probe.get("form_counts", {"managed": 0, "ordinary": 0, "inconclusive": 0}) if probe else {"managed": 0, "ordinary": 0, "inconclusive": 0},
                "routing_reason": group["routing_reason"],
                "exporter": group["exporter"],
                "representation_schema": group["representation_schema"],
                "exporter_version": group["exporter_version"],
                "converter_version": group["converter_version"],
                "payload_file_count": len(payload),
                "payload_fingerprint": "sha256:" + sha256(canonical_json(payload)),
            }
            if member["kind"] in {"configuration", "extension"}:
                assert identity is not None
                metadata.update({"uuid": identity["uuid"], "name": identity["name"], "version": identity["version"]})
            if member["kind"] == "extension":
                extension = member.get("extension")
                if extension is None:
                    raise ValueError("extension routing member has no extension identity")
                metadata["active"] = bool(extension["active"])
            atomic_json(output / "component-manifest.json", metadata)
            if progress:
                progress({"phase": "export", "completed": index, "total": len(members), "subject": member["component_id"]})
        shutil.rmtree(work_root)
        if validate_role_contract(repo, tested) != contract or routing_bindings(repo, connections, upload_drafts) != fresh_preview["bindings"]:
            raise RuntimeError("routing_preview_stale")
        _check_cancelled(cancelled, "publication")
        if progress:
            progress({"phase": "publication", "completed": 0, "total": 1, "subject": ""})
        def verify_freshness() -> None:
            if validate_role_contract(repo, tested) != contract or routing_bindings(repo, connections, upload_drafts) != fresh_preview["bindings"]:
                raise RuntimeError("routing_preview_stale")
        result = publish_routed(
            repo, staging, contract, fresh_preview["routing_manifest"],
            normalizer_version, expected_generation, source_comparison_epoch,
            activate=activate, freshness_check=verify_freshness,
        )
        if progress:
            progress({"phase": "publication", "completed": 1, "total": 1, "subject": ""})
        return result


def publish_routed(repo: Path, staged_roles: Path, contract: SourceContract, routing_manifest: RoutingManifest, normalizer_version: str, expected_generation: str | None, epoch_builder: Callable[[dict[str, JsonValue], RoutingManifest], str], *, activate: bool = True, freshness_check: Callable[[], None] | None = None) -> SourcePointer:
    manifest_preimage = {key: value for key, value in routing_manifest.items() if key != "routing_manifest_fingerprint"}
    if routing_manifest.get("routing_manifest_fingerprint") != "sha256:" + sha256(canonical_json(manifest_preimage)):
        raise ValueError("routing manifest fingerprint mismatch")
    with repository_lock(repo):
        if freshness_check:
            freshness_check()
        pointer_path = repo / "research/active-source-generation.json"
        current = parse_json_object(pointer_path.read_text(encoding="utf-8")).get("generation_id") if pointer_path.is_file() else None
        if current != expected_generation:
            raise RuntimeError("stale active source generation")
        role_manifests: dict[str, list[dict[str, str | int]]] = {}
        role_fingerprints: dict[str, str] = {}
        for role in ROLES:
            role_root = confined(staged_roles, role)
            if not (role_root / "configuration").is_dir():
                raise ValueError(f"staged role is incomplete: {role}")
            clean_payload(role_root)
            role_manifests[role] = file_manifest(role_root)
            role_fingerprints[role] = "sha256:" + sha256(canonical_json(role_manifests[role]))
        safe_contract = _json_contract(contract)
        reject_secrets(safe_contract, "source contract")
        contract_fingerprint = "sha256:" + sha256(canonical_json(safe_contract))
        routing_fingerprint = routing_manifest["routing_manifest_fingerprint"]
        preimage = {
            "source_contract_fingerprint": contract_fingerprint,
            "normalizer_version": normalizer_version,
            "roles": role_fingerprints,
            "routing_manifest_fingerprint": routing_fingerprint,
        }
        generation_id = sha256(canonical_json(preimage))
        destination = repo / "sources/generations" / generation_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = Path(tempfile.mkdtemp(prefix=f".{generation_id}.", dir=destination.parent))
            try:
                for role in ROLES:
                    _ = shutil.copytree(staged_roles / role, temporary / role)
                _ = (temporary / "source-contract.json").write_bytes(canonical_json(safe_contract) + b"\n")
                _ = (temporary / "routing-manifest.json").write_bytes(canonical_json(routing_manifest) + b"\n")
                for path in temporary.rglob("*"):
                    if path.is_file():
                        with path.open("rb") as stream:
                            _ = os.fsync(stream.fileno())
                os.replace(temporary, destination)
                parent_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    _ = os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
        if (destination / "source-contract.json").read_bytes() != canonical_json(safe_contract) + b"\n" or (destination / "routing-manifest.json").read_bytes() != canonical_json(routing_manifest) + b"\n":
            raise RuntimeError("existing routed source generation is inconsistent")
        if any(file_manifest(destination / role) != role_manifests[role] for role in ROLES):
            raise RuntimeError("existing routed source generation role payload is inconsistent")
        components: list[ComponentRecord] = []
        for role in ROLES:
            for manifest_path in sorted((destination / role).rglob("component-manifest.json")):
                component = parse_json_object(manifest_path.read_text(encoding="utf-8"))
                path = manifest_path.parent
                manifest = file_manifest(path)
                components.append({
                    "component_id": str(component["component_id"]),
                    "kind": str(component["kind"]),
                    "path": path.relative_to(destination).as_posix(),
                    "representation_schema": str(component["representation_schema"]),
                    "routing_group_id": str(component["routing_group_id"]),
                    "fingerprint": "sha256:" + sha256(canonical_json(manifest)),
                    "bsl_file_count": sum(str(item["path"]).endswith(".bsl") for item in manifest),
                })
        pointer: SourcePointer = {
            "schema_version": "2",
            "generation_id": generation_id,
            "acquisition_profile_id": contract["acquisition_profile_id"],
            "normalizer_version": normalizer_version,
            "source_contract_fingerprint": contract_fingerprint,
            "roles": role_fingerprints,
            "routing_manifest_path": "routing-manifest.json",
            "routing_manifest_fingerprint": routing_fingerprint,
            "components": components,
        }
        pointer["source_comparison_epoch_fingerprint"] = epoch_builder(parse_json_object(canonical_json(pointer).decode()), routing_manifest)
        if activate:
            atomic_json(pointer_path, pointer)
        return pointer


def publish(repo: Path, staged_roles: Path, contract: SourceContract, normalizer_version: str = NORMALIZER_VERSION, expected_generation: str | None | object = _UNSET) -> SourcePointer:
    profile_id = contract["acquisition_profile_id"]
    if profile_id not in LEGACY_PROFILES:
        raise ValueError("routed source publication requires a routing manifest")
    representation = LEGACY_PROFILES[profile_id][1]
    with repository_lock(repo):
        pointer_path = repo / "research/active-source-generation.json"
        current_generation = parse_json_object(pointer_path.read_text(encoding="utf-8")).get("generation_id") if pointer_path.is_file() else None
        if expected_generation is not _UNSET and current_generation != expected_generation:
            raise RuntimeError("stale active source generation")
        role_fingerprints: dict[str, str] = {}
        role_manifests: dict[str, list[dict[str, str | int]]] = {}
        for role in ROLES:
            root = confined(staged_roles, role)
            if not root.is_dir() or not (root / "configuration").is_dir():
                raise ValueError(f"staged role is incomplete: {role}")
            clean_payload(root)
            manifest = file_manifest(root)
            if not manifest:
                raise ValueError(f"staged role is empty: {role}")
            role_manifests[role] = manifest
            role_fingerprints[role] = "sha256:" + sha256(canonical_json(manifest))
        safe_contract = _json_contract(contract)
        reject_secrets(safe_contract, "source contract")
        contract_hash = sha256(canonical_json(safe_contract))
        preimage = {"schema_version": "1", "acquisition_profile_version": "1", "normalizer_version": normalizer_version, "source_contract_sha256": contract_hash, "roles": role_fingerprints}
        generation_id = sha256(canonical_json(preimage))
        destination = repo / "sources/generations" / generation_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = Path(tempfile.mkdtemp(prefix=f".{generation_id}.", dir=destination.parent))
            try:
                for role in ROLES:
                    _ = shutil.copytree(staged_roles / role, temporary / role)
                _ = (temporary / "source-contract.json").write_bytes(canonical_json(safe_contract) + b"\n")
                for path in temporary.rglob("*"):
                    if path.is_file():
                        with path.open("rb") as stream: _ = os.fsync(stream.fileno())
                os.replace(temporary, destination)
                parent_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    _ = os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
        expected_contract = canonical_json(safe_contract) + b"\n"
        if (destination / "source-contract.json").read_bytes() != expected_contract or any(file_manifest(destination / role) != role_manifests[role] for role in ROLES):
            raise RuntimeError("existing source generation does not match its deterministic identity")
        components: list[ComponentRecord] = []
        for role in ROLES:
            role_root = destination / role
            for kind, parent in (("configuration", role_root / "configuration"), ("extension", role_root / "extensions"), ("external", role_root / "external")):
                paths = [parent] if kind == "configuration" else sorted(path for path in parent.iterdir() if path.is_dir()) if parent.is_dir() else []
                for path in paths:
                    identity = "configuration" if kind == "configuration" else path.name
                    components.append({"component_id": f"{role}:{kind}:{identity}" if kind != "configuration" else f"{role}:configuration", "kind": kind, "path": path.relative_to(destination).as_posix(), "fingerprint": "sha256:" + sha256(canonical_json(file_manifest(path)))})
        pointer: SourcePointer = {"schema_version": "1", "generation_id": generation_id, "acquisition_profile_id": profile_id, "representation_schema": representation, "normalizer_version": normalizer_version, "source_contract_sha256": contract_hash, "roles": role_fingerprints, "components": components}
        atomic_json(repo / "research/active-source-generation.json", pointer)
        return pointer


def validate_active(
    repo: Path,
    *,
    deep: bool = False,
    require_tracked_clean: bool = False,
    candidate: SourcePointer | None = None,
    current: bool = True,
) -> SourcePointer:
    if candidate is None and current:
        _ = owned_function(".stage_recompute", "recover_active_publication")(repo)
    deep = deep or require_tracked_clean
    pointer_path = repo / "research/active-source-generation.json"
    pointer = candidate or source_pointer(parse_json_object(pointer_path.read_text(encoding="utf-8")))
    if pointer.get("schema_version") == "2":
        return _validate_active_routed(
            repo, pointer, deep=deep,
            require_tracked_clean=require_tracked_clean and candidate is None and current,
            current=current,
        )
    if pointer.get("schema_version") != "1":
        raise ValueError("unsupported active source schema version")
    generation_id = str(pointer.get("generation_id", ""))
    if not generation_id or generation_id != Path(generation_id).name:
        raise ValueError("invalid active source generation ID")
    root = confined(repo / "sources/generations", generation_id)
    if require_tracked_clean and candidate is None:
        validate_tracked_clean(repo, [root, pointer_path])
    contract_path = root / "source-contract.json"
    contract_json = parse_json_object(contract_path.read_text(encoding="utf-8"))
    reject_secrets(contract_json, "active source contract")
    contract = _source_contract(contract_json)
    if sha256(canonical_json(contract_json)) != pointer.get("source_contract_sha256"):
        raise ValueError("active source contract hash mismatch")
    actual: dict[str, str] = {}
    for role in ROLES:
        role_root = confined(root, role)
        if not role_root.is_dir():
            raise ValueError(f"active source role is missing: {role}")
        actual[role] = _immutable_role_fingerprint(str(role_root.resolve())) if deep else str(pointer.get("roles", {}).get(role, ""))
        if not actual[role].startswith("sha256:"):
            raise ValueError(f"active source role fingerprint is missing: {role}")
    if deep and actual != pointer.get("roles"):
        raise ValueError("active source role fingerprint mismatch")
    components = pointer.get("components")
    normalizer_version = pointer.get("normalizer_version")
    contract_sha256 = pointer.get("source_contract_sha256")
    role_fingerprints = pointer.get("roles")
    if not isinstance(components, list) or not isinstance(normalizer_version, str) or not isinstance(contract_sha256, str) or not isinstance(role_fingerprints, dict):
        raise ValueError("active source component catalog is missing or stale")
    if any(not str(item.get("component_id", "")) or not confined(root, str(item.get("path", ""))).is_dir() for item in components):
        raise ValueError("active source component catalog is missing or stale")
    if deep and any(item.get("fingerprint") != "sha256:" + sha256(canonical_json(file_manifest(confined(root, item["path"])))) for item in components):
        raise ValueError("active source component catalog is missing or stale")
    preimage = {"schema_version": "1", "acquisition_profile_version": "1", "normalizer_version": normalizer_version, "source_contract_sha256": contract_sha256, "roles": role_fingerprints}
    if sha256(canonical_json(preimage)) != generation_id:
        raise ValueError("active source generation ID mismatch")
    if current:
        current_infobases, current_artifacts = load_contract(repo)
        current_contract: dict[str, object] = {"schema_version": "1", "acquisition_profile_id": current_infobases.get("acquisition_profile"), "roles": current_infobases.get("roles", {}), "artifacts": current_artifacts.get("artifacts", [])}
        if current_infobases["extension_decisions"]:
            current_contract["extension_decisions"] = current_infobases["extension_decisions"]
        active_contract = _contract_without_artifact_ids(contract)
        if canonical_json(current_contract) != canonical_json(active_contract):
            raise ValueError("tracked source declarations changed; reacquisition is required")
    return pointer


def _validate_active_routed(
    repo: Path,
    pointer: SourcePointer,
    *,
    deep: bool,
    require_tracked_clean: bool,
    current: bool,
) -> SourcePointer:
    generation_id = str(pointer.get("generation_id", ""))
    if len(generation_id) != 64 or generation_id != Path(generation_id).name:
        raise ValueError("invalid active source generation ID")
    root = confined(repo / "sources/generations", generation_id)
    pointer_path = repo / "research/active-source-generation.json"
    if require_tracked_clean:
        validate_tracked_clean(repo, [root, pointer_path])
    contract_json = parse_json_object((root / "source-contract.json").read_text(encoding="utf-8"))
    reject_secrets(contract_json, "active source contract")
    contract = _source_contract(contract_json)
    contract_fingerprint = "sha256:" + sha256(canonical_json(contract_json))
    if contract_fingerprint != pointer.get("source_contract_fingerprint"):
        raise ValueError("active source contract fingerprint mismatch")
    manifest = _routing_manifest(parse_json_object((root / "routing-manifest.json").read_text(encoding="utf-8")))
    manifest_preimage = {key: value for key, value in manifest.items() if key != "routing_manifest_fingerprint"}
    manifest_fingerprint = "sha256:" + sha256(canonical_json(manifest_preimage))
    if manifest_fingerprint != manifest.get("routing_manifest_fingerprint") or manifest_fingerprint != pointer.get("routing_manifest_fingerprint"):
        raise ValueError("active source routing manifest fingerprint mismatch")
    groups = {group["routing_group_id"]: group for group in manifest.get("groups", [])}
    if len(groups) != len(manifest.get("groups", [])):
        raise ValueError("duplicate source routing group")
    group_keys = {"routing_group_id", "kind", "members", "absent_roles", "probe_contract_version", "probe_fingerprints", "form_counts", "routing_reason", "exporter", "representation_schema", "exporter_version", "converter_version"}
    reasons = {"managed_only", "ordinary_form_present", "inconclusive_form_payload", "no_forms", "binary_container_requires_v8unpack", "declared_source_tree", "raw_artifact"}
    representations = {"xml-hierarchical/v1", "v8unpack/v1", "source-tree/v1", "raw-binary/v1"}
    for group in groups.values():
        member_roles = [member.get("role") for member in group.get("members", [])]
        if (
            set(group) != group_keys
            or group.get("routing_reason") not in reasons
            or group.get("representation_schema") not in representations
            or group.get("exporter") not in {"ibcmd", "designer", "verified-upload"}
            or sorted(member_roles + group.get("absent_roles", [])) != sorted(ROLES)
            or len(member_roles) != len(set(member_roles))
            or set(group.get("form_counts", {})) != {"managed", "ordinary", "inconclusive"}
        ):
            raise ValueError("invalid source routing group contract")
    actual_roles: dict[str, str] = {}
    catalog: dict[str, ComponentManifest] = {}
    for role in ROLES:
        role_root = confined(root, role)
        if not role_root.is_dir():
            raise ValueError(f"active source role is missing: {role}")
        actual_roles[role] = _immutable_role_fingerprint(str(role_root.resolve())) if deep else pointer.get("roles", {}).get(role, "")
    if any(not str(value).startswith("sha256:") for value in actual_roles.values()) or deep and actual_roles != pointer.get("roles"):
        raise ValueError("active source role fingerprint mismatch")
    for item in pointer.get("components", []):
        component_root = confined(root, str(item.get("path", "")))
        component = _component_manifest(parse_json_object((component_root / "component-manifest.json").read_text(encoding="utf-8")))
        payload_manifest = [entry for entry in file_manifest(component_root) if entry["path"] != "component-manifest.json"] if deep else []
        group = groups.get(component.get("routing_group_id"))
        component_keys = {"schema_version", "component_id", "kind", "routing_group_id", "probe_contract_version", "probe_fingerprint", "form_counts", "routing_reason", "exporter", "representation_schema", "exporter_version", "converter_version", "payload_file_count", "payload_fingerprint"}
        if component.get("kind") in {"configuration", "extension"}:
            component_keys |= {"uuid", "name", "version"}
        if component.get("kind") == "extension":
            component_keys.add("active")
        counts_valid = set(component["form_counts"]) == {"managed", "ordinary", "inconclusive"} and all(not isinstance(value, bool) and value >= 0 for value in component["form_counts"].values())
        probe_valid = component.get("probe_contract_version") in {"", "form-probe/v1"} and (component.get("probe_fingerprint") == "" or re.fullmatch(r"sha256:[0-9a-f]{64}", str(component.get("probe_fingerprint"))) is not None)
        form_route = component.get("routing_reason") in {"managed_only", "ordinary_form_present", "inconclusive_form_payload", "no_forms"}
        bsl_file_count = item.get("bsl_file_count")
        if (
            set(component) != component_keys
            or component.get("schema_version") != "2"
            or not counts_valid
            or not probe_valid
            or component.get("kind") in {"configuration", "extension"} and (
                not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", str(component.get("uuid", "")))
                or not str(component.get("name", ""))
                or not isinstance(component.get("version"), str)
            )
            or component.get("kind") == "extension" and not isinstance(component.get("active"), bool)
            or (form_route and component.get("kind") not in {"configuration", "extension"})
            or (component.get("representation_schema") == "xml-hierarchical/v1" and component.get("routing_reason") not in {"managed_only", "no_forms"})
            or (component.get("representation_schema") == "v8unpack/v1" and component.get("routing_reason") not in {"ordinary_form_present", "inconclusive_form_payload", "binary_container_requires_v8unpack"})
            or item.get("component_id") != component.get("component_id")
            or item.get("routing_group_id") != component.get("routing_group_id")
            or item.get("representation_schema") != component.get("representation_schema")
            or bsl_file_count is not None and (
                not isinstance(bsl_file_count, int)
                or isinstance(bsl_file_count, bool)
                or bsl_file_count < 0
                or deep and bsl_file_count != sum(str(entry["path"]).endswith(".bsl") for entry in payload_manifest)
            )
            or not group
            or not any(member["component_id"] == component["component_id"] for member in group["members"])
            or component["representation_schema"] != group["representation_schema"]
            or component["routing_reason"] != group["routing_reason"]
            or component["exporter"] != group["exporter"]
            or component["exporter_version"] != group["exporter_version"]
            or component["converter_version"] != group["converter_version"]
            or deep and component["payload_file_count"] != len(payload_manifest)
            or deep and component["payload_fingerprint"] != "sha256:" + sha256(canonical_json(payload_manifest))
            or deep and item.get("fingerprint") != "sha256:" + sha256(canonical_json(file_manifest(component_root)))
        ):
            raise ValueError("active source component catalog is missing or stale")
        catalog[component["component_id"]] = component
    expected_members = {member["component_id"] for group in groups.values() for member in group["members"]}
    if set(catalog) != expected_members or len(catalog) != len(pointer.get("components", [])):
        raise ValueError("active source routing coverage mismatch")
    for group in groups.values():
        group_components = [catalog[member["component_id"]] for member in group["members"]]
        expected_probes = sorted(
            ({"component_id": component["component_id"], "probe_fingerprint": component["probe_fingerprint"]} for component in group_components if component["probe_fingerprint"]),
            key=lambda item: item["component_id"],
        )
        counts = {kind: sum(component["form_counts"][kind] for component in group_components) for kind in ("managed", "ordinary", "inconclusive")}
        if expected_probes != group["probe_fingerprints"] or counts != group["form_counts"]:
            raise ValueError("active source routing evidence mismatch")
    if deep and any(path.is_symlink() or path.name.startswith(".work") or path.suffix.lower() in {".cf", ".cfe", ".epf", ".erf"} for path in root.rglob("*")):
        raise ValueError("active source generation contains temporary or binary payload")
    preimage = {
        "source_contract_fingerprint": contract_fingerprint,
            "normalizer_version": pointer.get("normalizer_version"),
            "roles": pointer.get("roles"),
        "routing_manifest_fingerprint": manifest_fingerprint,
    }
    if sha256(canonical_json(preimage)) != generation_id:
        raise ValueError("active source generation ID mismatch")
    from .source_routing import source_comparison_epoch
    if pointer.get("source_comparison_epoch_fingerprint") != source_comparison_epoch(_json_contract(pointer), manifest):
        raise ValueError("active source comparison epoch mismatch")
    if current:
        current_infobases, current_artifacts = load_contract(repo)
        current_contract = {
            "schema_version": contract.get("schema_version"),
            "acquisition_profile_id": current_infobases.get("acquisition_profile"),
            "roles": current_infobases.get("roles", {}),
            "artifacts": current_artifacts.get("artifacts", []),
            "extension_decisions": current_infobases["extension_decisions"],
        }
        active_contract = _contract_without_artifact_ids(contract)
        if canonical_json(current_contract) != canonical_json(active_contract):
            raise ValueError("tracked source declarations changed; reacquisition is required")
    return pointer
