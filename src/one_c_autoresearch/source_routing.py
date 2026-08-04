from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, runtime_checkable

from .contracts import JsonValue, ROLES, canonical_json, external_id, json_array, json_object, normalize_relative, sha256


PROBE_CONTRACT_VERSION = "form-probe/v1"
ROUTING_CONTRACT_VERSION = "form-routing/v1"
FORM_KINDS = ("managed", "ordinary", "inconclusive")
MAX_FORMS, MAX_FORM_BYTES = 100_000, 64 * 1024 * 1024
FORM_COUNTS_ZERO = {kind: 0 for kind in FORM_KINDS}
MANAGED_NAMESPACES = {
    "http://v8.1c.ru/8.2/managed-application/logform",
    "http://v8.1c.ru/8.3/xcf/logform",
}
ORDINARY_NAMESPACES = {
    "http://v8.1c.ru/8.1/data/ui/form",
    "http://v8.1c.ru/8.3/xcf/form",
}
TAG_PATTERN: re.Pattern[bytes] = re.compile(br"</?\s*([A-Za-z_][A-Za-z0-9_.:-]*)")
ATTRIBUTE_PATTERN: re.Pattern[bytes] = re.compile(br"\s([A-Za-z_][A-Za-z0-9_.:-]*)\s*=")


@runtime_checkable
class _BytePattern(Protocol):
    def findall(self, value: bytes) -> list[bytes]: ...


def _byte_matches(pattern: object, value: bytes) -> list[bytes]:
    if not isinstance(pattern, _BytePattern):
        raise RuntimeError("invalid byte pattern")
    return pattern.findall(value)


class FormRecord(TypedDict):
    component_id: str
    form_id: str
    classification: str
    marker: str
    unknown_structure_sha256: NotRequired[str]


class FormProbe(TypedDict):
    schema_version: str
    probe_contract_version: str
    component_id: str
    records: list[FormRecord]
    form_counts: dict[str, int]
    probe_fingerprint: str


class ComponentMember(TypedDict):
    routing_group_id: str
    role: str
    component_id: str
    kind: str
    name: str | None
    extension: NotRequired[dict[str, JsonValue]]
    artifact: NotRequired[dict[str, JsonValue]]
    probe: NotRequired[FormProbe]


class ExtensionObservation(TypedDict):
    present: bool
    name: str
    version: str
    active: bool


class ExtensionScopeRow(TypedDict):
    uuid: str
    decision: str
    rationale: str
    dormant: bool
    roles: dict[str, ExtensionObservation]


class ExtensionScope(TypedDict):
    extensions: list[ExtensionScopeRow]
    included: list[str]
    excluded: list[str]
    dormant: list[str]
    unreviewed: list[str]


class RoutingMember(TypedDict):
    role: str
    component_id: str


class ProbeFingerprint(TypedDict):
    component_id: str
    probe_fingerprint: str


class RoutingGroup(TypedDict):
    routing_group_id: str
    kind: str
    members: list[RoutingMember]
    absent_roles: list[str]
    probe_contract_version: str
    probe_fingerprints: list[ProbeFingerprint]
    form_counts: dict[str, int]
    routing_reason: str
    exporter: str
    representation_schema: str
    exporter_version: str
    converter_version: str


class RoutingManifest(TypedDict):
    schema_version: str
    routing_contract_version: str
    groups: list[RoutingGroup]
    routing_manifest_fingerprint: str


def _object(value: object) -> dict[str, JsonValue]:
    return json_object(value)


def _objects(value: object) -> list[dict[str, JsonValue]]:
    return [json_object(item) for item in json_array(value)]


def _string(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _form_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("Form.xml")
        if path.is_file() and "Forms" in path.relative_to(root).parts
    )


def analyze_forms(root: Path, component_id: str) -> FormProbe:
    records: list[FormRecord] = []
    paths = _form_files(root)
    if len(paths) > MAX_FORMS:
        raise ValueError("form probe limit exceeded")
    for path in paths:
        relative = normalize_relative(path.relative_to(root).as_posix())
        if path.is_symlink():
            raise ValueError(f"form payload is a link: {relative}")
        if path.stat().st_size > MAX_FORM_BYTES:
            raise ValueError(f"form payload limit exceeded: {relative}")
        raw = path.read_bytes()
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError(f"unsafe form XML: {relative}")
        try:
            element = ET.fromstring(raw)
        except ET.ParseError as exc:
            classification, marker = "inconclusive", "xml-unparseable/v1"
            tag_matches = _byte_matches(TAG_PATTERN, raw)
            attribute_matches = _byte_matches(ATTRIBUTE_PATTERN, raw)
            tags = [match.decode("ascii", "ignore").lower() for match in tag_matches]
            attributes = [match.decode("ascii", "ignore").lower() for match in attribute_matches]
            unknown = sha256(canonical_json({"code": exc.code, "position": exc.position, "tags": tags, "attributes": attributes}))
        else:
            namespace = _namespace(element.tag)
            if namespace in MANAGED_NAMESPACES:
                classification, marker, unknown = "managed", "managed-logform-namespace/v1", ""
            elif namespace in ORDINARY_NAMESPACES:
                classification, marker, unknown = "ordinary", "ordinary-form-namespace/v1", ""
            else:
                classification, marker = "inconclusive", "unknown-form-namespace/v1"
                unknown = sha256(canonical_json({"namespace": namespace, "root": element.tag.rsplit("}", 1)[-1]}))
        record: FormRecord = {
            "component_id": component_id,
            "form_id": relative,
            "classification": classification,
            "marker": marker,
        }
        if unknown:
            record["unknown_structure_sha256"] = unknown
        records.append(record)
    records.sort(key=lambda item: (item["component_id"], item["form_id"]))
    counts = {kind: sum(item["classification"] == kind for item in records) for kind in FORM_KINDS}
    preimage = {
        "schema_version": "1",
        "probe_contract_version": PROBE_CONTRACT_VERSION,
        "component_id": component_id,
        "records": records,
        "form_counts": counts,
    }
    return {
        "schema_version": "1",
        "probe_contract_version": PROBE_CONTRACT_VERSION,
        "component_id": component_id,
        "records": records,
        "form_counts": counts,
        "probe_fingerprint": "sha256:" + sha256(canonical_json(preimage)),
    }


def component_members(contract: dict[str, JsonValue], connections: dict[str, dict[str, JsonValue]]) -> list[ComponentMember]:
    members: list[ComponentMember] = []
    decisions = _objects(contract.get("extension_decisions", []))
    included = {_string(item["uuid"]) for item in decisions if item["decision"] == "include"}
    for role in ROLES:
        roles = _object(contract["roles"])
        profile = connections[_string(_object(roles[role])["connection_profile"])]
        members.append({"routing_group_id": "configuration", "role": role, "component_id": f"{role}:configuration", "kind": "configuration", "name": None})
        for extension in _objects(profile.get("extensions", [])):
            uuid = str(extension["uuid"]).lower()
            if uuid not in included:
                continue
            members.append({"routing_group_id": f"extension:{uuid}", "role": role, "component_id": f"{role}:extension:{uuid}", "kind": "extension", "name": _string(extension["name"]), "extension": extension})
    for artifact in _objects(contract.get("artifacts", [])):
        identifier = artifact.get("external_artifact_id") or external_id(_string(artifact["kind"]), _string(artifact["semantic_key"]))
        artifact_role = _string(artifact["role"])
        artifact_kind = _string(artifact["kind"])
        members.append({"routing_group_id": f"external:{identifier}", "role": artifact_role, "component_id": f"{artifact_role}:external:{identifier}", "kind": artifact_kind, "name": str(identifier), "artifact": artifact})
    return sorted(members, key=lambda item: (_string(item["routing_group_id"]), ROLES.index(_string(item["role"]))))


def extension_scope(contract: dict[str, JsonValue], connections: dict[str, dict[str, JsonValue]]) -> ExtensionScope:
    decisions = {_string(item["uuid"]): item for item in _objects(contract.get("extension_decisions", []))}
    observed: dict[str, dict[str, ExtensionObservation]] = {}
    for role in ROLES:
        profile = connections[_string(_object(_object(contract["roles"])[role])["connection_profile"])]
        for extension in _objects(profile.get("extensions", [])):
            uuid = str(extension["uuid"]).lower()
            observed.setdefault(uuid, {})[role] = {
                "present": True,
                "name": str(extension["name"]),
                "version": str(extension.get("version", "")),
                "active": bool(extension["active"]),
            }
    rows: list[ExtensionScopeRow] = []
    for uuid in sorted(set(observed) | set(decisions)):
        decision = decisions.get(uuid)
        rows.append({
            "uuid": uuid,
            "decision": _string(decision["decision"]) if decision else "",
            "rationale": _string(decision["rationale"]) if decision else "",
            "dormant": uuid not in observed,
            "roles": {
                role: observed.get(uuid, {}).get(role, {"present": False, "name": "", "version": "", "active": False})
                for role in ROLES
            },
        })
    return {
        "extensions": rows,
        "included": [row["uuid"] for row in rows if row["decision"] == "include" and not row["dormant"]],
        "excluded": [row["uuid"] for row in rows if row["decision"] == "exclude" and not row["dormant"]],
        "dormant": [row["uuid"] for row in rows if row["dormant"]],
        "unreviewed": [row["uuid"] for row in rows if not row["decision"] and not row["dormant"]],
    }


def plan_groups(members: Iterable[ComponentMember], exporter: str, tool_versions: dict[str, str]) -> RoutingManifest:
    if exporter not in {"ibcmd", "designer"}:
        raise ValueError("unsupported routing exporter")
    grouped: dict[str, list[ComponentMember]] = {}
    for member in members:
        grouped.setdefault(str(member["routing_group_id"]), []).append(member)
    groups: list[RoutingGroup] = []
    for group_id, values in sorted(grouped.items()):
        by_role = {str(item["role"]): item for item in values}
        if set(by_role) - set(ROLES) or len(by_role) != len(values):
            raise ValueError(f"invalid routing group membership: {group_id}")
        available = [by_role[role] for role in ROLES if role in by_role]
        kinds = {_string(item["kind"]) for item in available}
        if len(kinds) != 1:
            raise ValueError(f"mixed component kinds in routing group: {group_id}")
        kind = kinds.pop()
        counts: dict[str, int] = {
            form_kind: sum(
                int(str(_object(_object(item.get("probe", {})).get("form_counts", {})).get(form_kind, 0)))
                for item in available
            )
            for form_kind in FORM_KINDS
        }
        if kind in {"epf", "erf"}:
            representation, reason, selected_exporter = "v8unpack/v1", "binary_container_requires_v8unpack", "verified-upload"
        elif kind == "source-tree":
            representation, reason, selected_exporter = "source-tree/v1", "declared_source_tree", "verified-upload"
        elif kind == "other":
            representation, reason, selected_exporter = "raw-binary/v1", "raw_artifact", "verified-upload"
        elif counts["inconclusive"]:
            representation, reason, selected_exporter = "v8unpack/v1", "inconclusive_form_payload", exporter
        elif counts["ordinary"]:
            representation, reason, selected_exporter = "v8unpack/v1", "ordinary_form_present", exporter
        elif counts["managed"]:
            representation, reason, selected_exporter = "xml-hierarchical/v1", "managed_only", exporter
        else:
            representation, reason, selected_exporter = "xml-hierarchical/v1", "no_forms", exporter
        probe_fingerprints: list[ProbeFingerprint] = []
        for item in available:
            if probe_value := item.get("probe"):
                probe_fingerprints.append({"component_id": item["component_id"], "probe_fingerprint": probe_value["probe_fingerprint"]})
        probe_fingerprints.sort(key=lambda item: _string(item["component_id"]))
        groups.append(
            {
                "routing_group_id": group_id,
                "kind": kind,
                "members": [
                    {"role": role, "component_id": by_role[role]["component_id"]}
                    for role in ROLES
                    if role in by_role
                ],
                "absent_roles": [role for role in ROLES if role not in by_role],
                "probe_contract_version": PROBE_CONTRACT_VERSION if probe_fingerprints else "",
                "probe_fingerprints": probe_fingerprints,
                "form_counts": counts,
                "routing_reason": reason,
                "exporter": selected_exporter,
                "representation_schema": representation,
                "exporter_version": tool_versions.get("platform", "") if selected_exporter in {"ibcmd", "designer"} else "",
                "converter_version": tool_versions.get("converter", "") if representation == "v8unpack/v1" else "",
            }
        )
    preimage = {
        "schema_version": "2",
        "routing_contract_version": ROUTING_CONTRACT_VERSION,
        "groups": groups,
    }
    return {
        "schema_version": "2",
        "routing_contract_version": ROUTING_CONTRACT_VERSION,
        "groups": groups,
        "routing_manifest_fingerprint": "sha256:" + sha256(canonical_json(preimage)),
    }


def source_comparison_epoch(pointer: dict[str, JsonValue], routing_manifest: RoutingManifest) -> str:
    groups: list[dict[str, JsonValue]] = [
        {
            key: group[key]
            for key in (
                "routing_group_id",
                "exporter",
                "representation_schema",
                "exporter_version",
                "converter_version",
            )
        }
        for group in sorted(routing_manifest["groups"], key=lambda item: item["routing_group_id"])
    ]
    preimage = {
        "schema_version": "2",
        "acquisition_profile_version": "1",
        "normalizer_version": pointer["normalizer_version"],
        "source_contract_fingerprint": pointer.get("source_contract_fingerprint", ""),
        "routing_contract_version": routing_manifest["routing_contract_version"],
        "groups": groups,
    }
    return "sha256:" + sha256(canonical_json(preimage))
