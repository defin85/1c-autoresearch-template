from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import NotRequired, TypedDict

from .contracts import JsonValue, canonical_json, content_id, normalize_relative, parse_json_object, sha256, validate_unique_ids


ANALYZER_VERSION = "extension-semantic/v1"
ADAPTERS = {
    "xml-hierarchical/v1": "xml-hierarchical@1",
    "v8unpack/v1": "v8unpack@1",
}
ROLES = ("vendor_baseline", "target_cf", "next_vendor")
KINDS = {
    "object_definition",
    "metadata_change",
    "form_change",
    "command_change",
    "role_change",
    "module_addition",
    "method_extension",
    "method_interception",
    "base_reference",
}
_ANNOTATION = {"перед": "before", "после": "after", "вместо": "instead"}
_DECLARATION = re.compile(
    r"(?im)^\s*(?:(асинх|async)\s+)?(процедура|функция|procedure|function)\s+" +
    r"([^\W\d]\w*)\s*\(([^\r\n)]*)\)\s*(экспорт|export)?\s*$"
)
_ANNOTATION_LINE = re.compile(r'(?im)^\s*&\s*(Перед|После|Вместо)\s*\(\s*"((?:""|[^"])*)"\s*\)\s*$')
_PREPROCESSOR = re.compile(r"(?im)^\s*#\s*(?:если|иначеесли|иначе|конецесли|if|elsif|else|endif)\b")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


BslMethod = TypedDict(
    "BslMethod",
    {
        "annotation_kind": str,
        "annotation_target": str,
        "async": bool,
        "export": bool,
        "method_kind": str,
        "module_context": str,
        "name": str,
        "parameters": list[str],
    },
)


class Evidence(TypedDict):
    path: str
    fingerprint: str


class MainMatch(TypedDict):
    identity: str
    structural_fingerprint: NotRequired[str]
    source_reference: NotRequired[str]
    evidence: NotRequired[list[Evidence]]


class Snapshot(TypedDict):
    affected_base_identity: str
    diagnostic_codes: list[str]
    dependency_structural_fingerprint: str
    dependency_source_reference: str
    evidence: list[Evidence]
    evidence_sensitive: bool
    evidence_fingerprint: str
    extension_object_identity: str
    extension_uuid: str
    intervention_kind: str
    mapping_identity: str
    object_scope: str
    structural_fingerprint: str
    structural_sublocation: str
    symbol_identity: str
    technical_identity: str
    intervention_key: str


class DiffEvidence(Evidence):
    role: str
    side: str


class Detail(TypedDict):
    affected_base_identity: str
    after_evidence_fingerprint: str
    after_structural_fingerprint: str
    after_role: str
    before_evidence_fingerprint: str
    before_structural_fingerprint: str
    before_role: str
    change_type: str
    comparison_id: str
    dependency_ids: list[str]
    diagnostic_codes: list[str]
    evidence: list[DiffEvidence]
    extension_object_identity: str
    extension_uuid: str
    intervention_key: str
    intervention_kind: str
    object_scope: str
    schema_version: str
    stable_diff_id: str
    structural_sublocation: str
    symbol_identity: str


class Dependency(TypedDict):
    dependency_id: str
    dependency_kind: str
    diagnostic_code: str
    evidence: list[DiffEvidence]
    extension_uuid: str
    normalized_source_reference: str
    outcome: str
    resolved_base_identity: str
    role: str
    schema_version: str
    stable_diff_id: str


class AnalyzerDiagnosticError(ValueError):
    code: str
    total_count: int
    samples: list[str]

    def __init__(self, code: str, items: list[str]) -> None:
        ordered = sorted(items)
        self.code = code
        self.total_count = len(ordered)
        self.samples = ordered[:100]
        super().__init__(
            f"{code}:"
            + canonical_json(
                {"code": code, "samples": self.samples, "total_count": self.total_count}
            ).decode()
        )


def _text(value: object) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def _fold(value: object) -> str:
    return " ".join(_text(value).casefold().split())


def _fingerprint(value: object) -> str:
    return "sha256:" + sha256(canonical_json(value))


def _strip_bsl_literals(text: str) -> str:
    result: list[str] = []
    index = 0
    quoted = False
    while index < len(text):
        char = text[index]
        if quoted:
            if char == '"' and index + 1 < len(text) and text[index + 1] == '"':
                result.extend("  ")
                index += 2
                continue
            if char == '"':
                quoted = False
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue
        if char == '"':
            quoted = True
            result.append(" ")
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index)
            if newline < 0:
                result.extend(" " * (len(text) - index))
                break
            result.extend(" " * (newline - index))
            index = newline
            continue
        result.append(char)
        index += 1
    if quoted:
        raise ValueError("invalid_bsl_text: unterminated string")
    return "".join(result)


def _strip_bsl_comments(text: str) -> str:
    return re.sub(r"//[^\r\n]*", lambda match: " " * len(match.group()), text)


def parse_bsl_methods(text: str, module_context: str) -> list[BslMethod]:
    """Extract declaration identities without evaluating BSL."""
    clean = _strip_bsl_literals(text)
    if _PREPROCESSOR.search(clean):
        raise ValueError("unsupported_bsl_structure: preprocessor")
    comment_free = _strip_bsl_comments(text)
    annotations = list(_ANNOTATION_LINE.finditer(comment_free))
    declaration_matches = list(_DECLARATION.finditer(clean))
    recognized_declarations = clean
    for match in reversed(declaration_matches):
        recognized_declarations = recognized_declarations[: match.start()] + recognized_declarations[match.end() :]
    if re.search(r"(?im)^\s*(?:асинх\s+|async\s+)?(?:процедура|функция|procedure|function)\b", recognized_declarations):
        raise ValueError("unsupported_bsl_structure: declaration")
    recognized_annotations = comment_free
    for match in reversed(annotations):
        recognized_annotations = recognized_annotations[: match.start()] + recognized_annotations[match.end() :]
    if re.search(r"(?im)^\s*&\s*(?:перед|после|вместо)\b", recognized_annotations):
        raise ValueError("unsupported_bsl_structure: annotation")
    rows: list[BslMethod] = []
    for match in declaration_matches:
        annotation = next(
            (
                item
                for item in reversed(annotations)
                if item.end() <= match.start() and not clean[item.end() : match.start()].strip()
            ),
            None,
        )
        parameters: list[str] = []
        for raw in match.group(4).split(","):
            name = raw.split("=", 1)[0].strip()
            if name:
                parts = name.split()
                by_value = len(parts) == 2 and _fold(parts[0]) in {"знач", "val"}
                identifier = parts[-1] if by_value else name
                if not re.fullmatch(r"[^\W\d]\w*", identifier, re.UNICODE):
                    raise ValueError("unsupported_bsl_structure: parameter")
                parameters.append(("val:" if by_value else "") + _fold(identifier))
        rows.append(
            {
                "annotation_kind": _ANNOTATION[_fold(annotation.group(1))] if annotation else "",
                "annotation_target": _fold(annotation.group(2).replace('""', '"')) if annotation else "",
                "async": bool(match.group(1)),
                "export": bool(match.group(5)),
                "method_kind": "procedure" if _fold(match.group(2)) in {"процедура", "procedure"} else "function",
                "module_context": _fold(module_context),
                "name": _fold(match.group(3)),
                "parameters": parameters,
            }
        )
    return sorted(rows, key=lambda item: canonical_json(item))


def _base_method_structure(method: BslMethod, *, extension: bool = False) -> dict[str, object]:
    return {
        "async": method["async"],
        "export": method["export"],
        "method_kind": method["method_kind"],
        "module_context": method["module_context"],
        "name": method["annotation_target"] if extension else method["name"],
        "parameters": method["parameters"],
    }


def intervention_key(snapshot: Snapshot) -> str:
    preimage = {
        "affected_base_identity": snapshot.get("affected_base_identity", ""),
        "extension_analyzer_version": ANALYZER_VERSION,
        "extension_object_identity": snapshot["extension_object_identity"],
        "extension_uuid": snapshot["extension_uuid"],
        "intervention_kind": snapshot["intervention_kind"],
        "object_scope": snapshot["object_scope"],
        "structural_sublocation": snapshot.get("structural_sublocation", ""),
        "symbol_identity": snapshot.get("symbol_identity", ""),
    }
    return content_id("EIN-", preimage)


def comparison_id(
    comparison_kind: str,
    before_role: str,
    after_role: str,
    source_comparison_epoch_fingerprint: str,
    adapter_versions: list[str] | None = None,
) -> str:
    if before_role not in ROLES or after_role not in ROLES:
        raise ValueError("invalid extension comparison role")
    return content_id(
        "CMP-",
        {
            "after_role": after_role,
            "before_role": before_role,
            "comparison_kind": comparison_kind,
            "extension_adapter_versions": sorted(adapter_versions or ADAPTERS.values()),
            "extension_analyzer_version": ANALYZER_VERSION,
            "schema_version": "2",
            "source_comparison_epoch_fingerprint": source_comparison_epoch_fingerprint,
        },
    )


comparison_id_v2 = comparison_id


def semantic_diff_id(comparison: str, change_type: str, key: str) -> str:
    if change_type not in {"added", "deleted", "modified"}:
        raise ValueError("invalid semantic extension change type")
    return content_id(
        "DIF-",
        {
            "change_type": change_type,
            "comparison_id": comparison,
            "intervention_key": key,
            "schema_version": "2",
        },
    )


def dependency_id(intervention: str, role: str, kind: str, source_reference: str) -> str:
    return content_id(
        "DEP-",
        {
            "dependency_kind": kind,
            "extension_analyzer_version": ANALYZER_VERSION,
            "intervention_key": intervention,
            "normalized_source_reference": _fold(source_reference),
            "role": role,
        },
    )


def resolve_dependency(
    detail: Detail,
    role: str,
    dependency_kind: str,
    source_reference: str,
    base_matches: list[MainMatch],
    extension_matches: list[MainMatch] | None = None,
    expected_structural_fingerprint: str = "",
) -> Dependency:
    """Resolve one normalized reference against the same-role main configuration."""
    reference = _fold(source_reference)
    matches = sorted(base_matches, key=canonical_json)
    diagnostic = ""
    if reference.startswith("unresolved:"):
        outcome, resolved, evidence, diagnostic = "unresolved", "", [], "unresolved_dependency"
    elif len(matches) == 1:
        match = matches[0]
        resolved = _fold(match.get("identity"))
        matched_reference = _fold(match.get("source_reference") or resolved)
        outcome = (
            "present_compatible"
            if matched_reference == reference
            and (
                not expected_structural_fingerprint
                or match.get("structural_fingerprint") == expected_structural_fingerprint
            )
            else "present_changed"
        )
        evidence: list[DiffEvidence] = sorted(
            ({"path": item["path"], "fingerprint": item["fingerprint"], "role": role, "side": "before" if role == "vendor_baseline" else "after"} for item in match.get("evidence", [])),
            key=canonical_json,
        )
    elif not matches and extension_matches:
        outcome, resolved, evidence, diagnostic = "unresolved", "", [], "unresolved_cross_extension"
    elif not matches:
        outcome, resolved, evidence = "missing", "", []
    else:
        outcome, resolved, evidence, diagnostic = "unresolved", "", [], "unresolved_dependency"
    identifier = dependency_id(detail["intervention_key"], role, dependency_kind, reference)
    return {
        "dependency_id": identifier,
        "dependency_kind": dependency_kind,
        "diagnostic_code": diagnostic,
        "evidence": evidence,
        "extension_uuid": detail["extension_uuid"],
        "normalized_source_reference": reference,
        "outcome": outcome,
        "resolved_base_identity": resolved,
        "role": role,
        "schema_version": "2",
        "stable_diff_id": detail["stable_diff_id"],
    }


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_value(root: ET.Element, name: str) -> str:
    values = [_text(item.text) for item in root.iter() if _local(item.tag) == name and _text(item.text)]
    return values[0] if values else ""


def _xml_references(root: ET.Element) -> list[str]:
    references: set[str] = set()
    for item in root.iter():
        value = _text(item.text)
        typed_reference = any(_local(key) == "type" and _text(raw).endswith("MDObjectRef") for key, raw in item.attrib.items())
        if typed_reference and re.fullmatch(r"[^\s.<>]+\.[^\s<>]+", value):
            references.add(_fold(value))
    return sorted(references)


def _xml_scalar_properties(root: ET.Element) -> dict[str, str]:
    properties = next((item for item in root if _local(item.tag) == "Properties"), None)
    if properties is None:
        return {}
    ignored = {"comment", "name", "objectbelonging", "synonym"}
    return {
        _fold(_local(item.tag)): _fold(item.text)
        for item in properties
        if len(item) == 0 and _fold(_local(item.tag)) not in ignored and _text(item.text)
    }


def _json_references(value: JsonValue) -> list[str]:
    references: set[str] = set()

    def visit(item: JsonValue) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif isinstance(item, str):
            candidate = item.strip('"')
            if re.fullmatch(r"[^\W\d][\w]*\.[^\W\d][\w.]*", candidate, re.UNICODE):
                references.add(_fold(candidate))

    visit(value)
    return sorted(references)


def _v8_structure(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    ignored = {
        "comment", "name2", "configinfo", "copyinfo", "file_uuid", "v8unpack",
        "code_encoding_obj", "code_info_obj", "code_encoding_mgr", "code_info_mgr",
    }
    return {key: value[key] for key in sorted(value) if key not in ignored}


def _scalar_properties(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    ignored = {
        "comment", "name", "name2", "header", "obj_version", "configinfo", "copyinfo",
        "file_uuid", "v8unpack", "code_encoding_obj", "code_info_obj",
    }
    return {
        _fold(key): _fold(value[key]) if isinstance(value[key], str) else value[key]
        for key in sorted(value)
        if key not in ignored and isinstance(value[key], (str, int, float, bool))
    }


def _object_context(path: str) -> tuple[str, str, str]:
    parts = path.split("/")
    ignored = {"metadata", "objects", "modules"}
    useful = [part for part in parts[:-1] if part.casefold() not in ignored]
    object_type = useful[0] if useful else "Configuration"
    if object_type.endswith("ies"):
        object_type = object_type[:-3] + "y"
    elif object_type.endswith("s"):
        object_type = object_type[:-1]
    object_name = useful[1] if len(useful) > 1 else Path(path).stem
    filename = Path(path).name.casefold()
    module = (
        "ObjectModule"
        if filename.endswith(".obj.bsl")
        else "ManagerModule"
        if filename.endswith(".mgr.bsl")
        else Path(path).stem
    )
    return _fold(object_type), _fold(object_name), _fold(module)


def _record(
    *,
    extension_uuid: str,
    scope: str,
    kind: str,
    object_identity: str,
    structure: object,
    evidence: Evidence | list[Evidence],
    base_identity: str = "",
    symbol_identity: str = "",
    sublocation: str = "",
    diagnostics: list[str] | None = None,
    mapping_identity: str = "",
    technical_identity: str = "",
    evidence_sensitive: bool = True,
    dependency_structure: object | None = None,
    dependency_source_reference: str = "",
) -> Snapshot:
    if scope not in {"owned", "adopted"} or kind not in KINDS:
        raise ValueError("invalid normalized extension intervention")
    evidence_items: list[Evidence] = [evidence] if isinstance(evidence, dict) else sorted(evidence, key=canonical_json)
    row: Snapshot = {
        "affected_base_identity": base_identity,
        "diagnostic_codes": sorted(set(diagnostics or [])),
        "dependency_structural_fingerprint": _fingerprint(dependency_structure) if dependency_structure else "",
        "dependency_source_reference": dependency_source_reference,
        "evidence": evidence_items,
        "evidence_sensitive": evidence_sensitive,
        "evidence_fingerprint": _fingerprint(evidence_items),
        "extension_object_identity": object_identity,
        "extension_uuid": extension_uuid,
        "intervention_kind": kind,
        "intervention_key": "",
        "mapping_identity": mapping_identity,
        "object_scope": scope,
        "structural_fingerprint": _fingerprint(structure),
        "structural_sublocation": sublocation,
        "symbol_identity": symbol_identity,
        "technical_identity": technical_identity,
    }
    row["intervention_key"] = intervention_key(row)
    return row


def _safe_xml(payload: bytes, path: str) -> ET.Element:
    head = payload[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ValueError(f"unsafe_xml: {path}")
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"invalid_xml: {path}") from exc


def _manifest(
    root: Path,
    extension_uuid: str,
    representation: str,
    payload: bytes | None = None,
) -> tuple[dict[str, JsonValue], Evidence]:
    path = root / "component-manifest.json"
    payload = path.read_bytes() if payload is None else payload
    try:
        manifest = parse_json_object(payload.decode())
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid_component_manifest") from exc
    if (
        manifest.get("kind") != "extension"
        or _fold(manifest.get("uuid")) != extension_uuid
        or manifest.get("representation_schema") not in {representation, representation.removesuffix("/v1")}
    ):
        raise ValueError("component_manifest_mismatch")
    return manifest, {"path": "component-manifest.json", "fingerprint": "sha256:" + sha256(payload)}


def parse_component(
    root: Path,
    representation: str,
    extension_uuid: str,
    manifest: dict[str, JsonValue] | None = None,
    manifest_payload: bytes | None = None,
    cancelled: Callable[[], bool] | None = None,
    base_index: dict[str, list[MainMatch]] | None = None,
) -> list[Snapshot]:
    """Normalize one validated extension component using its bound adapter."""
    if representation not in ADAPTERS:
        raise ValueError(f"unsupported_representation:{representation}")
    root = root.resolve()
    uuid = _fold(extension_uuid)
    if not _UUID.fullmatch(uuid):
        raise ValueError("invalid_extension_uuid")
    if cancelled and cancelled():
        raise InterruptedError("extension analysis cancelled")
    bound, manifest_evidence = _manifest(root, uuid, representation, manifest_payload)
    if manifest is not None and manifest != bound:
        raise ValueError("component_manifest_mismatch")
    root_fields = {
        "extension.active": bool(bound.get("active")),
        "extension.technical_name": _text(bound.get("name")),
        "extension.version": _text(bound.get("version")),
    }
    rows = [
        _record(
            extension_uuid=uuid,
            scope="owned",
            kind="metadata_change",
            object_identity=f"extension:{uuid}",
            structure={"field": field, "value": value},
            evidence=manifest_evidence,
            sublocation=field,
            evidence_sensitive=False,
        )
        for field, value in root_fields.items()
    ]
    object_contexts: dict[str, tuple[str, str, str, str, str, str, str, dict[str, JsonValue] | None]] = {}
    contexts_by_identity: dict[tuple[str, str], tuple[str, str, str, str, str, str, str, dict[str, JsonValue] | None]] = {}

    def parent_context(relative: str) -> tuple[str, str, str, str, str, str, str, dict[str, JsonValue] | None] | None:
        for parent in Path(relative).parents:
            context = object_contexts.get(parent.as_posix().rstrip("/") + "/")
            if context:
                return context
        return None

    def register_context(context: tuple[str, str, str, str, str, str, str, dict[str, JsonValue] | None]) -> None:
        object_contexts[context[0]] = context
        contexts_by_identity[(context[4], context[5])] = context
    v8_ids: dict[tuple[str, str], tuple[str, Evidence]] = {}
    unaccounted_paths: list[str] = []
    entries = list(root.rglob("*"))
    unsafe = next((path for path in entries if path.is_symlink()), None)
    if unsafe is not None:
        raise ValueError(f"unsafe_component_path:{unsafe.relative_to(root).as_posix()}")
    paths = [path for path in entries if not path.is_dir() and path.name != "component-manifest.json"]

    def parse_order(path: Path) -> tuple[int, str]:
        name = path.name
        priority = 0 if name.endswith(".id.json") else 2 if name.endswith(".elem.json") else 1 if name.endswith((".json", ".xml")) else 3 if name.endswith(".bsl") else 4
        return priority, path.relative_to(root).as_posix()

    for path in sorted(paths, key=parse_order):
        if cancelled and cancelled():
            raise InterruptedError("extension analysis cancelled")
        if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
            raise ValueError(f"unsafe_component_path:{path.relative_to(root).as_posix()}")
        relative = normalize_relative(path.relative_to(root).as_posix())
        payload = path.read_bytes()
        evidence: Evidence = {"path": relative, "fingerprint": "sha256:" + sha256(payload)}
        object_evidence: list[Evidence]
        if representation == "xml-hierarchical/v1" and relative == "Configuration.xml":
            node = _safe_xml(payload, relative)
            configuration = next(iter(node), node)
            structure = {
                "compatibility_version": _fold(_xml_value(configuration, "ConfigurationExtensionCompatibilityMode")),
                "name": _fold(_xml_value(configuration, "Name") or bound.get("name")),
            }
            rows.append(
                _record(
                    extension_uuid=uuid,
                    scope="owned",
                    kind="metadata_change",
                    object_identity=f"extension:{uuid}",
                    structure=structure,
                    evidence=evidence,
                    sublocation="extension.definition",
                )
            )
        elif representation == "xml-hierarchical/v1" and path.suffix.casefold() == ".xml":
            node = _safe_xml(payload, relative)
            object_node = next(iter(node), node)
            object_type = _local(object_node.tag)
            name = _xml_value(object_node, "Name") or path.stem
            scope = "adopted" if _fold(_xml_value(object_node, "ObjectBelonging")) == "adopted" else "owned"
            parent = parent_context(relative)
            if parent and ({part.casefold() for part in Path(relative).parts} & {"forms", "commands"}):
                scope = parent[1]
            own_uuid = _fold(object_node.attrib.get("uuid", ""))
            qualified = f"{object_type}.{name}"
            explicit = (
                _xml_value(object_node, "ExtendedConfigurationObject")
                or _xml_value(object_node, "ExtendedObject")
                or _xml_value(object_node, "AdoptedObject")
            )
            diagnostics: list[str] = []
            base_identity = ""
            if scope == "adopted":
                if explicit and "." in explicit:
                    base_identity = _fold(explicit)
                elif _UUID.fullmatch(own_uuid):
                    # In canonical Designer XML, ObjectBelonging=Adopted plus
                    # the metadata UUID is the explicit mapping; type+name is
                    # still the qualified base identity used for role lookup.
                    base_identity = _fold(qualified)
                else:
                    unresolved = {"path": relative, "qualified_name": qualified, "reference": explicit}
                    base_identity = "unresolved:" + sha256(canonical_json(unresolved))
                    diagnostics.append("unresolved_adoption_identity")
            object_identity = f"uuid:{own_uuid}" if scope == "owned" and _UUID.fullmatch(own_uuid) else _fold(qualified)
            dependency_structure: dict[str, JsonValue] = {"metadata_type": _fold(object_type), "name": _fold(name), "uuid": own_uuid}
            register_context(
                (
                    relative.removesuffix(".xml") + "/",
                    scope,
                    object_identity,
                    base_identity,
                    _fold(object_type),
                    _fold(name),
                    f"uuid:{own_uuid}" if scope == "adopted" and _UUID.fullmatch(own_uuid) else "",
                    dependency_structure if scope == "adopted" else None,
                )
            )
            lowered = {part.casefold() for part in Path(relative).parts}
            kind = (
                "form_change"
                if lowered & {"form", "forms"}
                else "command_change"
                if lowered & {"command", "commands"}
                else "role_change"
                if lowered & {"role", "roles"}
                else "object_definition"
            )
            structure = {
                "base_identity": base_identity,
                "metadata_type": _fold(object_type),
                "name": _fold(name),
                "object_identity": object_identity,
                "scope": scope,
                "uuid": own_uuid,
            }
            rows.append(
                _record(
                    extension_uuid=uuid,
                    scope=scope,
                    kind=kind,
                    object_identity=object_identity,
                    structure=structure,
                    evidence=evidence,
                    base_identity=base_identity,
                    sublocation=_fold(qualified),
                    diagnostics=diagnostics,
                    mapping_identity=f"uuid:{own_uuid}" if scope == "adopted" and _UUID.fullmatch(own_uuid) else "",
                    technical_identity=_fold(qualified),
                    dependency_structure=dependency_structure if scope == "adopted" else None,
                )
            )
            for property_name, property_value in _xml_scalar_properties(object_node).items():
                rows.append(
                    _record(
                        extension_uuid=uuid,
                        scope=scope,
                        kind="metadata_change",
                        object_identity=object_identity,
                        structure={"field": property_name, "value": property_value},
                        evidence=evidence,
                        base_identity=base_identity,
                        sublocation=f"{_fold(qualified)}.property:{property_name}",
                        mapping_identity=f"uuid:{own_uuid}" if scope == "adopted" and _UUID.fullmatch(own_uuid) else "",
                        dependency_structure=dependency_structure if scope == "adopted" else None,
                    )
                )
            for reference in _xml_references(object_node):
                rows.append(
                    _record(
                        extension_uuid=uuid,
                        scope=scope,
                        kind="base_reference",
                        object_identity=object_identity,
                        structure={"reference": reference},
                        evidence=evidence,
                        base_identity=reference,
                        sublocation=f"{_fold(qualified)}.reference:{reference}",
                        mapping_identity=f"uuid:{own_uuid}" if scope == "adopted" and _UUID.fullmatch(own_uuid) else "",
                        dependency_structure=dependency_structure if scope == "adopted" else None,
                    )
                )
        elif representation == "v8unpack/v1" and path.name.endswith(".json"):
            try:
                value = parse_json_object(payload.decode())
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError(f"invalid_v8unpack_json:{relative}") from exc
            if relative == "ConfigurationExtension.json":
                rows.append(
                    _record(
                        extension_uuid=uuid,
                        scope="owned",
                        kind="metadata_change",
                        object_identity=f"extension:{uuid}",
                        structure={
                            "compatibility_version": _fold(value.get("compatibility_version")),
                            "name": _fold(value.get("name") or bound.get("name")),
                        },
                        evidence=evidence,
                        sublocation="extension.definition",
                    )
                )
                continue
            if path.name.endswith(".id.json"):
                own_uuid = _fold(value.get("uuid"))
                if not _UUID.fullmatch(own_uuid):
                    raise ValueError(f"invalid_v8unpack_identity:{relative}")
                object_type = path.name.removesuffix(".id.json")
                v8_ids[(path.parent.relative_to(root).as_posix(), object_type)] = (own_uuid, evidence)
                continue
            if path.name.endswith(".elem.json"):
                parent = parent_context(relative)
                object_identity = parent[2] if parent else _fold(path.parent.relative_to(root).as_posix())
                rows.append(
                    _record(
                        extension_uuid=uuid,
                        scope=parent[1] if parent else "owned",
                        kind="form_change",
                        object_identity=object_identity,
                        structure=_v8_structure(value),
                        evidence=evidence,
                        base_identity=parent[3] if parent else "",
                        sublocation=_fold(relative.removesuffix(".elem.json")),
                    )
                )
                continue
            object_type = path.name.removesuffix(".json")
            name = _text(value.get("name") or path.parent.name)
            qualified = f"{object_type}.{name}"
            identity = v8_ids.get((path.parent.relative_to(root).as_posix(), object_type))
            if identity is None:
                raise ValueError(f"missing_v8unpack_identity:{relative}")
            own_uuid, identity_evidence = identity
            belonging = _fold(value.get("object_belonging") or value.get("ObjectBelonging"))
            base_matches = (base_index or {}).get(f"uuid:{own_uuid}", [])
            scope = "adopted" if belonging == "adopted" or (not belonging and base_matches) else "owned"
            base_identity = _fold(qualified) if scope == "adopted" else ""
            object_identity = _fold(qualified) if scope == "adopted" else f"uuid:{own_uuid}"
            dependency_structure = {"metadata_type": _fold(object_type), "name": _fold(name), "uuid": own_uuid}
            register_context(
                (
                    path.parent.relative_to(root).as_posix().rstrip("/") + "/",
                    scope,
                    object_identity,
                    base_identity,
                    _fold(object_type),
                    _fold(name),
                    f"uuid:{own_uuid}" if scope == "adopted" else "",
                    dependency_structure if scope == "adopted" else None,
                )
            )
            lowered = {part.casefold() for part in Path(relative).parts}
            kind = (
                "form_change"
                if lowered & {"form", "forms"}
                else "command_change"
                if lowered & {"command", "commands"}
                else "role_change"
                if lowered & {"role", "roles"}
                else "object_definition"
            )
            structure = {
                "base_identity": base_identity,
                "metadata_type": _fold(object_type),
                "name": _fold(name),
                "object_identity": object_identity,
                "scope": scope,
                "uuid": own_uuid,
            }
            object_evidence = [identity_evidence, evidence]
            rows.append(
                _record(
                    extension_uuid=uuid,
                    scope=scope,
                    kind=kind,
                    object_identity=object_identity,
                    structure=structure,
                    evidence=object_evidence,
                    base_identity=base_identity,
                    sublocation=_fold(qualified),
                    mapping_identity=f"uuid:{own_uuid}" if scope == "adopted" else "",
                    technical_identity=_fold(qualified),
                    dependency_structure=dependency_structure if scope == "adopted" else None,
                )
            )
            for property_name, property_value in _scalar_properties(value).items():
                rows.append(
                    _record(
                        extension_uuid=uuid,
                        scope=scope,
                        kind="metadata_change",
                        object_identity=object_identity,
                        structure={"field": property_name, "value": property_value},
                        evidence=evidence,
                        base_identity=base_identity,
                        mapping_identity=f"uuid:{own_uuid}" if scope == "adopted" else "",
                        sublocation=f"{_fold(qualified)}.property:{property_name}",
                        dependency_structure=dependency_structure if scope == "adopted" else None,
                    )
                )
            for reference in _json_references(value):
                rows.append(
                    _record(
                        extension_uuid=uuid,
                        scope=scope,
                        kind="base_reference",
                        object_identity=object_identity,
                        structure={"reference": reference},
                        evidence=evidence,
                        base_identity=reference,
                        sublocation=f"{_fold(qualified)}.reference:{reference}",
                        mapping_identity=f"uuid:{own_uuid}" if scope == "adopted" else "",
                        dependency_structure=dependency_structure if scope == "adopted" else None,
                    )
                )
        elif path.suffix.casefold() == ".bsl":
            try:
                text = payload.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError(f"invalid_bsl_text:{relative}") from exc
            object_type, object_name, module = _object_context(relative)
            object_identity = f"{object_type}.{object_name}"
            parent = parent_context(relative) or contexts_by_identity.get((object_type, object_name))
            scope = parent[1] if parent else "owned"
            object_identity = parent[2] if parent else object_identity
            base_identity = parent[3] if parent else ""
            methods = parse_bsl_methods(text, module)
            if not methods:
                rows.append(
                    _record(
                        extension_uuid=uuid,
                        scope=scope,
                        kind="module_addition",
                        object_identity=object_identity,
                        structure={"module_context": module, "payload_fingerprint": sha256(payload)},
                        evidence=evidence,
                        base_identity=base_identity,
                        sublocation=module,
                    )
                )
            for method in methods:
                kind = "method_interception" if method["annotation_kind"] == "instead" else "method_extension"
                symbol = _fingerprint(method)
                rows.append(
                    _record(
                        extension_uuid=uuid,
                        scope=scope,
                        kind=kind,
                        object_identity=object_identity,
                        structure=method,
                        evidence=evidence,
                        base_identity=base_identity,
                        symbol_identity=symbol,
                        sublocation=f"{module}.{method['name']}",
                        mapping_identity=parent[6] if parent else "",
                        dependency_structure=_base_method_structure(method, extension=True) if scope == "adopted" else None,
                        dependency_source_reference=(
                            f"{base_identity}#{module}.{method['annotation_target']}"
                            if scope == "adopted" and method["annotation_target"]
                            else ""
                        ),
                    )
                )
        else:
            lowered = {part.casefold() for part in Path(relative).parts}
            parent = parent_context(relative)
            if representation == "v8unpack/v1" and relative == "version.bin":
                rows.append(
                    _record(
                        extension_uuid=uuid,
                        scope="owned",
                        kind="metadata_change",
                        object_identity=f"extension:{uuid}",
                        structure={"payload_fingerprint": "sha256:" + sha256(payload)},
                        evidence=evidence,
                        sublocation="extension.version-binary",
                    )
                )
            elif parent and lowered & {"form", "forms", "template", "templates"}:
                rows.append(
                    _record(
                        extension_uuid=uuid,
                        scope=parent[1],
                        kind="form_change" if lowered & {"form", "forms"} else "metadata_change",
                        object_identity=parent[2],
                        structure={"payload_fingerprint": "sha256:" + sha256(payload), "structural_owner": parent[2]},
                        evidence=evidence,
                        base_identity=parent[3],
                        sublocation=_fold(relative),
                        mapping_identity=parent[6],
                        dependency_structure=parent[7],
                    )
                )
            else:
                unaccounted_paths.append(relative)
    if unaccounted_paths:
        raise AnalyzerDiagnosticError("unaccounted_component_path", unaccounted_paths)
    owned_identities = {
        row["technical_identity"]
        for row in rows
        if row["object_scope"] == "owned"
        and row["intervention_kind"] in {
            "object_definition",
            "form_change",
            "command_change",
            "role_change",
        }
        and row["technical_identity"]
    }
    rows = [
        row
        for row in rows
        if not (
            row["intervention_kind"] == "base_reference"
            and row["affected_base_identity"] in owned_identities
        )
    ]
    by_key: dict[str, Snapshot] = {}
    for row in rows:
        prior = by_key.get(row["intervention_key"])
        if prior and prior != row:
            raise ValueError(f"duplicate_intervention_identity:{row['intervention_key']}")
        by_key[row["intervention_key"]] = row
    return sorted(rows, key=lambda item: item["intervention_key"])


def build_main_config_index(root: Path, cancelled: Callable[[], bool] | None = None) -> dict[str, list[MainMatch]]:
    """Build a payload-free identity index for one role-matched main configuration."""
    root = root.resolve()
    result: dict[str, list[MainMatch]] = {}
    identifiers: dict[tuple[str, str], tuple[str, Evidence]] = {}
    paths = [path for path in root.rglob("*") if path.is_file()]
    paths.sort(key=lambda path: (0 if path.name.endswith(".id.json") else 1, path.relative_to(root).as_posix()))
    for path in paths:
        if cancelled and cancelled():
            raise InterruptedError("extension analysis cancelled")
        if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
            raise ValueError(f"unsafe_component_path:{path.relative_to(root).as_posix()}")
        relative = normalize_relative(path.relative_to(root).as_posix())
        payload = path.read_bytes()
        evidence: Evidence = {"path": relative, "fingerprint": "sha256:" + sha256(payload)}
        object_evidence: list[Evidence]
        if path.name.endswith(".id.json"):
            try:
                value = parse_json_object(payload.decode())
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError(f"invalid_v8unpack_json:{relative}") from exc
            identifier = _fold(value.get("uuid"))
            if not _UUID.fullmatch(identifier):
                raise ValueError(f"invalid_v8unpack_identity:{relative}")
            object_type = path.name.removesuffix(".id.json")
            identifiers[(path.parent.relative_to(root).as_posix(), object_type)] = (identifier, evidence)
            continue
        if path.suffix.casefold() == ".xml":
            node = _safe_xml(payload, relative)
            object_node = next(iter(node), node)
            object_type = _fold(_local(object_node.tag))
            name = _fold(_xml_value(object_node, "Name"))
            identifier = _fold(object_node.attrib.get("uuid", ""))
            structure = {"metadata_type": object_type, "name": name, "uuid": identifier}
            object_evidence = [evidence]
        elif path.suffix.casefold() == ".json" and not path.name.endswith(".elem.json"):
            try:
                value = parse_json_object(payload.decode())
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError(f"invalid_v8unpack_json:{relative}") from exc
            object_type = path.name.removesuffix(".json")
            found = identifiers.get((path.parent.relative_to(root).as_posix(), object_type))
            if found is None:
                continue
            identifier, identity_evidence = found
            name = _fold(value.get("name") or path.parent.name)
            structure = {
                "metadata_type": _fold(object_type),
                "name": name,
                "uuid": identifier,
            }
            object_evidence = [identity_evidence, evidence]
            object_type = _fold(object_type)
        elif path.suffix.casefold() == ".bsl":
            try:
                methods = parse_bsl_methods(payload.decode("utf-8-sig"), _object_context(relative)[2])
            except UnicodeDecodeError as exc:
                raise ValueError(f"invalid_bsl_text:{relative}") from exc
            except ValueError as exc:
                if not str(exc).startswith("unsupported_bsl_structure:"):
                    raise
                continue
            object_type, object_name, module = _object_context(relative)
            identity = f"{object_type}.{object_name}"
            for method in methods:
                entry: MainMatch = {
                    "identity": identity,
                    "source_reference": f"{identity}#{module}.{method['name']}",
                    "structural_fingerprint": _fingerprint(_base_method_structure(method)),
                    "evidence": [evidence],
                }
                result.setdefault(f"{identity}#{module}.{method['name']}", []).append(entry)
            continue
        else:
            continue
        if not name:
            continue
        identity = f"{object_type}.{name}"
        entry = MainMatch(
            identity=identity,
            structural_fingerprint=_fingerprint(structure),
            evidence=object_evidence,
        )
        result.setdefault(identity, []).append(entry)
        if _UUID.fullmatch(identifier):
            result.setdefault(f"uuid:{identifier}", []).append(entry)
    return {key: sorted(values, key=canonical_json) for key, values in sorted(result.items())}


def _role_snapshots(
    role_components: dict[str, dict[str, Path | tuple[Path, str]]],
    representations: dict[tuple[str, str], str] | None = None,
    cancelled: Callable[[], bool] | None = None,
    main_config_indexes: dict[str, dict[str, list[MainMatch]]] | None = None,
) -> tuple[
    dict[str, dict[str, list[Snapshot]]],
    dict[tuple[str, str], tuple[Path, str, dict[str, JsonValue], bytes]],
]:
    """Analyze the lowercase UUID union; absent role members become empty snapshots."""
    if set(role_components) != set(ROLES):
        raise ValueError("extension role set must be exact")
    uuids = sorted({_fold(uuid) for members in role_components.values() for uuid in members})
    result: dict[str, dict[str, list[Snapshot]]] = {role: {} for role in ROLES}
    loaded: dict[tuple[str, str], tuple[Path, str, dict[str, JsonValue], bytes]] = {}
    for role in ROLES:
        normalized_members = {_fold(uuid): value for uuid, value in role_components[role].items()}
        if len(normalized_members) != len(role_components[role]):
            raise ValueError("duplicate extension UUID")
        for uuid in uuids:
            member = normalized_members.get(uuid)
            if member is None:
                result[role][uuid] = []
                continue
            if cancelled and cancelled():
                raise InterruptedError("extension analysis cancelled")
            if isinstance(member, tuple):
                root, representation = member
            else:
                root = member
                representation = (representations or {}).get((role, uuid), "")
            raw = (root / "component-manifest.json").read_bytes()
            try:
                manifest = parse_json_object(raw.decode())
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError("invalid_component_manifest") from exc
            saved_representation = manifest.get("representation_schema")
            if not representation and not isinstance(saved_representation, str):
                raise ValueError("invalid_component_manifest")
            representation = representation or saved_representation
            assert isinstance(representation, str)
            if not representation.endswith("/v1"):
                representation += "/v1"
            loaded[(role, uuid)] = (root, representation, manifest, raw)
            result[role][uuid] = parse_component(
                root,
                representation,
                uuid,
                manifest,
                raw,
                cancelled,
                (main_config_indexes or {}).get(role, {}),
            )
    return result, loaded


def compare_snapshots(
    before: dict[str, list[Snapshot]],
    after: dict[str, list[Snapshot]],
    comparison: str,
    before_role: str = "vendor_baseline",
    after_role: str = "target_cf",
) -> list[Detail]:
    rows: list[Detail] = []
    for uuid in sorted(set(before) | set(after)):
        left = {item["intervention_key"]: item for item in before.get(uuid, [])}
        right = {item["intervention_key"]: item for item in after.get(uuid, [])}
        for key in sorted(set(left) | set(right)):
            old, new = left.get(key), right.get(key)
            change = "added" if old is None else "deleted" if new is None else "modified"
            if (
                old
                and new
                and old["structural_fingerprint"] == new["structural_fingerprint"]
                and (
                    not old.get("evidence_sensitive", True)
                    or old["evidence_fingerprint"] == new["evidence_fingerprint"]
                )
            ):
                continue
            snapshot = new or old
            assert snapshot is not None
            old_evidence = old["evidence"] if old else []
            new_evidence = new["evidence"] if new else []
            evidence: list[DiffEvidence] = [
                {
                    "fingerprint": item["fingerprint"],
                    "path": item["path"],
                    "role": before_role,
                    "side": "before",
                }
                for item in old_evidence
            ]
            evidence.extend(
                [
                {
                    "fingerprint": item["fingerprint"],
                    "path": item["path"],
                    "role": after_role,
                    "side": "after",
                }
                    for item in new_evidence
                ]
            )
            rows.append(
                {
                    "affected_base_identity": snapshot["affected_base_identity"],
                    "after_evidence_fingerprint": new["evidence_fingerprint"] if new else "",
                    "after_structural_fingerprint": new["structural_fingerprint"] if new else "",
                    "after_role": after_role,
                    "before_evidence_fingerprint": old["evidence_fingerprint"] if old else "",
                    "before_structural_fingerprint": old["structural_fingerprint"] if old else "",
                    "before_role": before_role,
                    "change_type": change,
                    "comparison_id": comparison,
                    "dependency_ids": [],
                    "diagnostic_codes": sorted(set((old or {}).get("diagnostic_codes", []) + (new or {}).get("diagnostic_codes", []))),
                    "evidence": sorted(evidence, key=canonical_json),
                    "extension_object_identity": snapshot["extension_object_identity"],
                    "extension_uuid": uuid,
                    "intervention_key": key,
                    "intervention_kind": snapshot["intervention_kind"],
                    "object_scope": snapshot["object_scope"],
                    "schema_version": "2",
                    "stable_diff_id": semantic_diff_id(comparison, change, key),
                    "structural_sublocation": snapshot["structural_sublocation"],
                    "symbol_identity": snapshot["symbol_identity"],
                }
            )
    rows = sorted(rows, key=lambda item: item["stable_diff_id"])
    validate_unique_ids(
        [
            {
                "id": row["stable_diff_id"],
                "preimage": {
                    "change_type": row["change_type"],
                    "comparison_id": row["comparison_id"],
                    "intervention_key": row["intervention_key"],
                    "schema_version": "2",
                },
            }
            for row in rows
        ],
        "id",
        "preimage",
    )
    return rows


def analyze_role_union(
    role_components: dict[str, dict[str, Path | tuple[Path, str]]],
    source_comparison_epoch_fingerprint: str,
    representations: dict[tuple[str, str], str] | None = None,
    *,
    source_generation_id: str = "",
    main_config_indexes: dict[str, dict[str, list[MainMatch]]] | None = None,
    raw_rows: list[dict[str, str]] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Return deterministic core facts ready for version-2 serializers."""
    snapshots, loaded = _role_snapshots(
        role_components,
        representations,
        cancelled,
        main_config_indexes,
    )
    customer_comparison = comparison_id_v2(
        "customer-customization", "vendor_baseline", "target_cf", source_comparison_epoch_fingerprint
    )
    target_comparison = comparison_id_v2(
        "target-release", "vendor_baseline", "next_vendor", source_comparison_epoch_fingerprint
    )
    customer = compare_snapshots(
        snapshots["vendor_baseline"],
        snapshots["target_cf"],
        customer_comparison,
        "vendor_baseline",
        "target_cf",
    )
    target = compare_snapshots(
        snapshots["vendor_baseline"],
        snapshots["next_vendor"],
        target_comparison,
        "vendor_baseline",
        "next_vendor",
    )
    bindings: list[dict[str, str | bool]] = []
    for role in ROLES:
        for uuid in sorted(role_components[role]):
            _root, representation, manifest, raw = loaded[(role, _fold(uuid))]
            bindings.append(
                {
                    "active": bool(manifest.get("active")),
                    "adapter_id": ADAPTERS[representation].split("@", 1)[0],
                    "adapter_version": ADAPTERS[representation].split("@", 1)[1],
                    "component_fingerprint": "sha256:" + sha256(raw),
                    "component_id": f"{role}:extension:{_fold(uuid)}",
                    "component_path": f"{role}/extensions/{_fold(uuid)}",
                    "extension_uuid": _fold(uuid),
                    "representation_schema": representation,
                    "role": role,
                }
            )
    details = sorted(customer + target, key=lambda item: (item["comparison_id"], item["stable_diff_id"]))
    dependencies: list[Dependency] = []
    indexes = main_config_indexes or {}
    extension_identities = {
        role: {
            _fold(item.get("extension_object_identity"))
            for values in snapshots[role].values()
            for item in values
        }
        for role in ROLES
    }
    for detail in details:
        reference = detail["affected_base_identity"]
        if not reference:
            continue
        roles = (
            [detail["after_role"]]
            if detail["change_type"] == "added"
            else [detail["before_role"]]
            if detail["change_type"] == "deleted"
            else [detail["before_role"], detail["after_role"]]
        )
        for role in roles:
            snapshot: Snapshot | None = next(
                (
                    item
                    for item in snapshots[role].get(detail["extension_uuid"], [])
                    if item["intervention_key"] == detail["intervention_key"]
                ),
                None,
            )
            dependency_reference = (snapshot["dependency_source_reference"] if snapshot else "") or reference
            name_matches = indexes.get(role, {}).get(_fold(dependency_reference), [])
            mapping_matches = indexes.get(role, {}).get(snapshot["mapping_identity"] if snapshot else "", [])
            matches_by_value = {
                canonical_json(item): item
                for item in (
                    name_matches
                    if dependency_reference != reference
                    else name_matches + mapping_matches
                )
            }
            dependency = resolve_dependency(
                detail,
                role,
                "base_reference" if detail["intervention_kind"] == "base_reference" else "adopted_object",
                dependency_reference,
                list(matches_by_value.values()),
                [{"identity": reference}] if _fold(reference) in extension_identities[role] else [],
                snapshot["dependency_structural_fingerprint"] if snapshot else "",
            )
            dependencies.append(dependency)
            detail["dependency_ids"].append(dependency["dependency_id"])
            if dependency["diagnostic_code"]:
                detail["diagnostic_codes"] = sorted(set(detail["diagnostic_codes"] + [dependency["diagnostic_code"]]))
        detail["dependency_ids"].sort()
    semantic_rows: list[dict[str, str]] = []
    for detail in details:
        evidence = detail["evidence"]
        first_path = evidence[0]["path"] if evidence else ""
        before = detail["before_structural_fingerprint"]
        after = detail["after_structural_fingerprint"]
        semantic_rows.append(
            {
                "after_fingerprint": after,
                "after_role": detail["after_role"],
                "area": f"extensions/{detail['extension_uuid']}",
                "before_fingerprint": before,
                "before_role": detail["before_role"],
                "change_type": detail["change_type"],
                "comparison_id": detail["comparison_id"],
                "content_fingerprint": _fingerprint({"before": before, "after": after}),
                "object_kind": "extension_intervention",
                "object_name": detail["intervention_key"],
                "path": first_path,
                "source_generation": source_generation_id,
                "stable_diff_id": detail["stable_diff_id"],
            }
        )
    coverage: list[dict[str, str]] = []
    unaccounted_paths: list[str] = []
    semantic_owners: dict[tuple[str, str], set[str]] = {}
    for detail in details:
        for item in detail["evidence"]:
            relative = f"extensions/{detail['extension_uuid']}/{item['path']}"
            for path in (relative, f"{item['role']}/{relative}"):
                semantic_owners.setdefault(
                    (detail["comparison_id"], path),
                    set(),
                ).add(detail["stable_diff_id"])
    for raw in sorted(raw_rows or [], key=lambda item: (item["comparison_id"], item["path"], item["stable_diff_id"])):
        owners = sorted(semantic_owners.get((raw["comparison_id"], raw["path"]), set()))
        noise = ""
        if not owners and raw["path"].endswith("/component-manifest.json"):
            noise = "component_manifest"
        if not owners and not noise:
            unaccounted_paths.append(raw["path"])
            continue
        coverage.append(
            {
                "change_type": raw["change_type"],
                "comparison_id": raw["comparison_id"],
                "noise_reason": noise,
                "path": raw["path"],
                "raw_diff_id": raw["stable_diff_id"],
                "semantic_diff_ids": canonical_json(owners).decode(),
            }
        )
    if unaccounted_paths:
        raise AnalyzerDiagnosticError("unaccounted_extension_path", unaccounted_paths)
    return {
        "adapter_versions": sorted(ADAPTERS.values()),
        "component_bindings": sorted(bindings, key=canonical_json),
        "dependency_rows": sorted(dependencies, key=lambda item: item["dependency_id"]),
        "detail_rows": details,
        "extension_analyzer_version": ANALYZER_VERSION,
        "path_coverage": coverage,
        "physical_rows": sorted(raw_rows or [], key=lambda item: (item["comparison_id"], item["path"], item["stable_diff_id"])),
        "semantic_rows": sorted(semantic_rows, key=lambda item: (item["comparison_id"], item["stable_diff_id"])),
    }


def target_coverage(customer: Detail, target_diffs: list[Detail]) -> dict[str, str]:
    candidates = [row for row in target_diffs if row["intervention_key"] == customer["intervention_key"]]
    if customer.get("diagnostic_codes") or any(row.get("diagnostic_codes") for row in candidates):
        coverage = "needs_semantic_review"
    elif not candidates:
        coverage = "still_required"
    elif any(
        row["change_type"] == customer["change_type"]
        and row["after_structural_fingerprint"] == customer["after_structural_fingerprint"]
        for row in candidates
    ):
        coverage = "covered_by_vendor"
    else:
        coverage = "changed_in_target"
    return {
        "coverage": coverage,
        "stable_diff_id": customer["stable_diff_id"],
        "target_stable_diff_id": candidates[0]["stable_diff_id"] if candidates else "",
    }
