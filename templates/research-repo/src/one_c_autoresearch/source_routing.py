from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

from .contracts import ROLES, canonical_json, external_id, normalize_relative, sha256


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


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _form_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("Form.xml")
        if path.is_file() and "Forms" in path.relative_to(root).parts
    )


def analyze_forms(root: Path, component_id: str) -> dict[str, Any]:
    records = []
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
            tags = [match.decode("ascii", "ignore").lower() for match in re.findall(br"</?\s*([A-Za-z_][A-Za-z0-9_.:-]*)", raw)]
            attributes = [match.decode("ascii", "ignore").lower() for match in re.findall(br"\s([A-Za-z_][A-Za-z0-9_.:-]*)\s*=", raw)]
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
        record = {
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
    result = {
        "schema_version": "1",
        "probe_contract_version": PROBE_CONTRACT_VERSION,
        "component_id": component_id,
        "records": records,
        "form_counts": counts,
    }
    result["probe_fingerprint"] = "sha256:" + sha256(canonical_json(result))
    return result


def component_members(contract: dict[str, Any], connections: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    members = []
    included = {item["uuid"] for item in contract.get("extension_decisions", []) if item["decision"] == "include"}
    for role in ROLES:
        profile = connections[contract["roles"][role]["connection_profile"]]
        members.append({"routing_group_id": "configuration", "role": role, "component_id": f"{role}:configuration", "kind": "configuration", "name": None})
        for extension in profile.get("extensions", []):
            uuid = str(extension["uuid"]).lower()
            if uuid not in included:
                continue
            members.append({"routing_group_id": f"extension:{uuid}", "role": role, "component_id": f"{role}:extension:{uuid}", "kind": "extension", "name": extension["name"], "extension": extension})
    for artifact in contract.get("artifacts", []):
        identifier = artifact.get("external_artifact_id") or external_id(artifact["kind"], artifact["semantic_key"])
        members.append({"routing_group_id": f"external:{identifier}", "role": artifact["role"], "component_id": f"{artifact['role']}:external:{identifier}", "kind": artifact["kind"], "name": identifier, "artifact": artifact})
    return sorted(members, key=lambda item: (item["routing_group_id"], ROLES.index(item["role"])))


def extension_scope(contract: dict[str, Any], connections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    decisions = {item["uuid"]: item for item in contract.get("extension_decisions", [])}
    observed: dict[str, dict[str, dict[str, Any]]] = {}
    for role in ROLES:
        profile = connections[contract["roles"][role]["connection_profile"]]
        for extension in profile.get("extensions", []):
            uuid = str(extension["uuid"]).lower()
            observed.setdefault(uuid, {})[role] = {
                "present": True,
                "name": str(extension["name"]),
                "version": str(extension.get("version", "")),
                "active": bool(extension["active"]),
            }
    rows = []
    for uuid in sorted(set(observed) | set(decisions)):
        decision = decisions.get(uuid)
        rows.append({
            "uuid": uuid,
            "decision": decision["decision"] if decision else "",
            "rationale": decision["rationale"] if decision else "",
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


def plan_groups(members: Iterable[dict[str, Any]], exporter: str, tool_versions: dict[str, str]) -> dict[str, Any]:
    if exporter not in {"ibcmd", "designer"}:
        raise ValueError("unsupported routing exporter")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for member in members:
        grouped.setdefault(str(member["routing_group_id"]), []).append(member)
    groups = []
    for group_id, values in sorted(grouped.items()):
        by_role = {str(item["role"]): item for item in values}
        if set(by_role) - set(ROLES) or len(by_role) != len(values):
            raise ValueError(f"invalid routing group membership: {group_id}")
        available = [by_role[role] for role in ROLES if role in by_role]
        kinds = {item["kind"] for item in available}
        if len(kinds) != 1:
            raise ValueError(f"mixed component kinds in routing group: {group_id}")
        kind = kinds.pop()
        counts = {
            form_kind: sum(int(item.get("probe", {}).get("form_counts", {}).get(form_kind, 0)) for item in available)
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
        probe_fingerprints = sorted([
            {"component_id": item["component_id"], "probe_fingerprint": item["probe"]["probe_fingerprint"]}
            for item in available
            if item.get("probe")
        ], key=lambda item: item["component_id"])
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
    manifest = {
        "schema_version": "2",
        "routing_contract_version": ROUTING_CONTRACT_VERSION,
        "groups": groups,
    }
    manifest["routing_manifest_fingerprint"] = "sha256:" + sha256(canonical_json(manifest))
    return manifest


def source_comparison_epoch(pointer: dict[str, Any], routing_manifest: dict[str, Any]) -> str:
    groups = [
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
