from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .common import repo_path, utc_now_iso


PARSER_SCHEMA_VERSION = "configuration-source-parser/v1"
PARSER_IMPLEMENTATION_VERSION = "python-v1"
DEFAULT_SNAPSHOT_DIR = "analysis/cache/custom-metadata/parser"

SOURCE_FORMAT_XML_BSL = "xml-bsl"
SOURCE_FORMAT_V8UNPACK = "v8unpack"
SOURCE_FORMAT_AUTO = "auto"
SOURCE_FORMATS = {SOURCE_FORMAT_XML_BSL, SOURCE_FORMAT_V8UNPACK}
SOURCE_FORMAT_CHOICES = sorted([*SOURCE_FORMATS, SOURCE_FORMAT_AUTO])

PART_KIND_OBJECT = "object"
PART_KIND_MODULE = "module"
PART_KIND_FORM = "form"
PART_KIND_FORM_ELEMENT = "form_element"
PART_KIND_TEMPLATE = "template"
PART_KIND_ROLE_RIGHT = "role_right"
PART_KIND_ATTRIBUTE = "attribute"
PART_KIND_TABULAR_SECTION = "tabular_section"
PART_KIND_COMMAND = "command"
PART_KIND_SCHEDULED_JOB = "scheduled_job"
PART_KIND_SUBSCRIPTION = "subscription"
PART_KIND_SERVICE = "service"
PART_KIND_PREDEFINED_VALUE = "predefined_value"
PART_KIND_UNKNOWN = "unknown"
PART_KINDS = {
    PART_KIND_OBJECT,
    PART_KIND_MODULE,
    PART_KIND_FORM,
    PART_KIND_FORM_ELEMENT,
    PART_KIND_TEMPLATE,
    PART_KIND_ROLE_RIGHT,
    PART_KIND_ATTRIBUTE,
    PART_KIND_TABULAR_SECTION,
    PART_KIND_COMMAND,
    PART_KIND_SCHEDULED_JOB,
    PART_KIND_SUBSCRIPTION,
    PART_KIND_SERVICE,
    PART_KIND_PREDEFINED_VALUE,
    PART_KIND_UNKNOWN,
}

STRUCTURE_LEVEL_STRUCTURED = "structured"
STRUCTURE_LEVEL_BINARY = "binary"
STRUCTURE_LEVEL_UNKNOWN = "unknown"
STRUCTURE_LEVELS = {STRUCTURE_LEVEL_STRUCTURED, STRUCTURE_LEVEL_BINARY, STRUCTURE_LEVEL_UNKNOWN}

XML_CHILD_OBJECT_PART_KINDS = {
    "Attribute": PART_KIND_ATTRIBUTE,
    "TabularSection": PART_KIND_TABULAR_SECTION,
    "Command": PART_KIND_COMMAND,
    "ScheduledJob": PART_KIND_SCHEDULED_JOB,
    "Subscription": PART_KIND_SUBSCRIPTION,
    "EventSubscription": PART_KIND_SUBSCRIPTION,
    "Template": PART_KIND_TEMPLATE,
    "PredefinedItem": PART_KIND_PREDEFINED_VALUE,
    "PredefinedDataItem": PART_KIND_PREDEFINED_VALUE,
    "WebService": PART_KIND_SERVICE,
    "HTTPService": PART_KIND_SERVICE,
}

XML_PART_PATH_SEGMENTS = {
    PART_KIND_ATTRIBUTE: "Attributes",
    PART_KIND_TABULAR_SECTION: "TabularSections",
    PART_KIND_COMMAND: "Commands",
    PART_KIND_SCHEDULED_JOB: "ScheduledJobs",
    PART_KIND_SUBSCRIPTION: "Subscriptions",
    PART_KIND_TEMPLATE: "Templates",
    PART_KIND_PREDEFINED_VALUE: "Predefined",
    PART_KIND_SERVICE: "Services",
}

XML_COLLECTION_PART_KINDS = {
    "Attributes": PART_KIND_ATTRIBUTE,
    "TabularSections": PART_KIND_TABULAR_SECTION,
    "Commands": PART_KIND_COMMAND,
    "ScheduledJobs": PART_KIND_SCHEDULED_JOB,
    "Subscriptions": PART_KIND_SUBSCRIPTION,
    "EventSubscriptions": PART_KIND_SUBSCRIPTION,
    "Templates": PART_KIND_TEMPLATE,
    "Predefined": PART_KIND_PREDEFINED_VALUE,
    "WebServices": PART_KIND_SERVICE,
    "HTTPServices": PART_KIND_SERVICE,
}

XML_OBJECT_DIRS = {
    "AccumulationRegisters": "AccumulationRegister",
    "AccountingRegisters": "AccountingRegister",
    "BusinessProcesses": "BusinessProcess",
    "Catalogs": "Catalog",
    "ChartsOfAccounts": "ChartOfAccounts",
    "ChartsOfCalculationTypes": "ChartOfCalculationTypes",
    "ChartsOfCharacteristicTypes": "ChartOfCharacteristicTypes",
    "CommandGroups": "CommandGroup",
    "CommonCommands": "CommonCommand",
    "CommonForms": "CommonForm",
    "CommonModules": "CommonModule",
    "CommonPictures": "CommonPicture",
    "CommonTemplates": "CommonTemplate",
    "Constants": "Constant",
    "DataProcessors": "DataProcessor",
    "DefinedTypes": "DefinedType",
    "DocumentJournals": "DocumentJournal",
    "Documents": "Document",
    "Enums": "Enum",
    "ExchangePlans": "ExchangePlan",
    "EventSubscriptions": "EventSubscription",
    "FilterCriteria": "FilterCriterion",
    "FunctionalOptions": "FunctionalOption",
    "FunctionalOptionsParameters": "FunctionalOptionsParameter",
    "HTTPServices": "HTTPService",
    "InformationRegisters": "InformationRegister",
    "Languages": "Language",
    "Reports": "Report",
    "Roles": "Role",
    "ScheduledJobs": "ScheduledJob",
    "Sequences": "Sequence",
    "SessionParameters": "SessionParameter",
    "SettingsStorages": "SettingsStorage",
    "Styles": "Style",
    "Subsystems": "Subsystem",
    "Tasks": "Task",
    "WebServices": "WebService",
    "WSReferences": "WSReference",
    "XDTOPackages": "XDTOPackage",
    "Interfaces": "Interface",
}

V8UNPACK_OBJECT_DIRS = {
    "AccountingRegister",
    "AccumulationRegister",
    "BusinessProcess",
    "Catalog",
    "ChartOfAccounts",
    "ChartOfCalculationTypes",
    "ChartOfCharacteristicTypes",
    "CommandGroup",
    "CommonCommand",
    "CommonForm",
    "CommonModule",
    "CommonPicture",
    "CommonTemplate",
    "Configuration",
    "Constant",
    "DataProcessor",
    "DefinedType",
    "Document",
    "DocumentJournal",
    "Enum",
    "ExchangePlan",
    "FilterCriterion",
    "FunctionalOption",
    "HTTPService",
    "InformationRegister",
    "Interface",
    "Language",
    "Report",
    "Role",
    "ScheduledJob",
    "Sequence",
    "SessionParameter",
    "SettingsStorage",
    "Style",
    "EventSubscription",
    "Task",
    "WebService",
    "WSReference",
    "XDTOPackage",
}


class ParserError(Exception):
    pass


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_key_part(value: str) -> str:
    value = value.strip().replace("\\", "/")
    value = re.sub(r"\s+", " ", value)
    return value


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _element_to_payload(element: ET.Element) -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    for child in list(element):
        children.append(_element_to_payload(child))
    attributes = {key: value for key, value in sorted(element.attrib.items())}
    text = (element.text or "").strip()
    payload: dict[str, Any] = {"tag": _strip_namespace(element.tag)}
    if attributes:
        payload["attributes"] = attributes
    if text:
        payload["text"] = text
    if children:
        payload["children"] = children
    return payload


def _find_first_child(element: ET.Element, tag: str) -> ET.Element | None:
    for child in list(element):
        if _strip_namespace(child.tag) == tag:
            return child
    return None


def _find_descendant(element: ET.Element, tag: str) -> ET.Element | None:
    for child in element.iter():
        if child is not element and _strip_namespace(child.tag) == tag:
            return child
    return None


def _child_text(element: ET.Element, tag: str) -> str:
    child = _find_descendant(element, tag)
    return (child.text or "").strip() if child is not None else ""


def _direct_property_text(element: ET.Element, tag: str) -> str:
    direct = _find_first_child(element, tag)
    if direct is not None:
        return (direct.text or "").strip()
    properties = _find_first_child(element, "Properties")
    if properties is None:
        return ""
    child = _find_first_child(properties, tag)
    return (child.text or "").strip() if child is not None else ""


@dataclass(frozen=True)
class SourceReference:
    path: str
    source_kind: str
    line_start: int | None = None
    line_end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"path": self.path, "source_kind": self.source_kind}
        if self.line_start is not None:
            data["line_start"] = self.line_start
        if self.line_end is not None:
            data["line_end"] = self.line_end
        return data


def diagnostic(code: str, severity: str, message: str, source_refs: list[SourceReference] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if source_refs:
        data["source_refs"] = [ref.to_dict() for ref in source_refs]
    return data


@dataclass
class ParsedMetadataItem:
    item_key: str
    source_format: str
    metadata_kind: str
    metadata_name: str
    metadata_full_name: str
    part_kind: str
    part_name: str
    part_path: str
    source_refs: list[SourceReference]
    structural_payload: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "item_key": self.item_key,
            "source_format": self.source_format,
            "metadata_kind": self.metadata_kind,
            "metadata_name": self.metadata_name,
            "metadata_full_name": self.metadata_full_name,
            "part_kind": self.part_kind,
            "part_name": self.part_name,
            "part_path": self.part_path,
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "structural_payload": self.structural_payload,
            "content_hash": self.content_hash,
            "diagnostics": self.diagnostics,
        }
        structure_level = self.structural_payload.get("structure_level")
        if structure_level:
            data["structure_level"] = structure_level
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParsedMetadataItem":
        return cls(
            item_key=str(data.get("item_key") or ""),
            source_format=str(data.get("source_format") or ""),
            metadata_kind=str(data.get("metadata_kind") or ""),
            metadata_name=str(data.get("metadata_name") or ""),
            metadata_full_name=str(data.get("metadata_full_name") or ""),
            part_kind=str(data.get("part_kind") or ""),
            part_name=str(data.get("part_name") or ""),
            part_path=str(data.get("part_path") or ""),
            source_refs=[
                SourceReference(
                    path=str(ref.get("path") or ""),
                    source_kind=str(ref.get("source_kind") or ""),
                    line_start=ref.get("line_start"),
                    line_end=ref.get("line_end"),
                )
                for ref in data.get("source_refs", [])
                if isinstance(ref, dict)
            ],
            structural_payload=data.get("structural_payload") if isinstance(data.get("structural_payload"), dict) else {},
            content_hash=str(data.get("content_hash") or ""),
            diagnostics=data.get("diagnostics") if isinstance(data.get("diagnostics"), list) else [],
        )


@dataclass
class ParsedConfigurationSnapshot:
    schema_version: str
    parser_version: str
    source_format: str
    source_root: str
    source_root_identity: dict[str, Any]
    items: list[ParsedMetadataItem]
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    layout_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    traversal: dict[str, Any] = field(default_factory=dict)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "parser_version": self.parser_version,
            "source_format": self.source_format,
            "source_root": self.source_root,
            "source_root_identity": self.source_root_identity,
            "generated_at": self.generated_at,
            "layout_diagnostics": self.layout_diagnostics,
            "diagnostics": self.diagnostics,
            "traversal": self.traversal,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParsedConfigurationSnapshot":
        return cls(
            schema_version=str(data.get("schema_version") or ""),
            parser_version=str(data.get("parser_version") or ""),
            source_format=str(data.get("source_format") or ""),
            source_root=str(data.get("source_root") or ""),
            source_root_identity=data.get("source_root_identity") if isinstance(data.get("source_root_identity"), dict) else {},
            generated_at=str(data.get("generated_at") or ""),
            layout_diagnostics=data.get("layout_diagnostics") if isinstance(data.get("layout_diagnostics"), list) else [],
            diagnostics=data.get("diagnostics") if isinstance(data.get("diagnostics"), list) else [],
            traversal=data.get("traversal") if isinstance(data.get("traversal"), dict) else {},
            items=[
                ParsedMetadataItem.from_dict(item)
                for item in data.get("items", [])
                if isinstance(item, dict)
            ],
        )


def make_item_key(
    metadata_kind: str,
    metadata_full_name: str,
    part_kind: str,
    part_name: str = "",
    part_path: str = "",
) -> str:
    coordinates = [
        _safe_key_part(metadata_kind),
        _safe_key_part(metadata_full_name),
        _safe_key_part(part_kind),
        _safe_key_part(part_name),
        _safe_key_part(part_path),
    ]
    return "|".join(coordinates)


def structural_hash(payload: dict[str, Any]) -> str:
    return _sha256_text(_stable_json(payload))


def module_hash(text: str) -> str:
    return _sha256_text(_normalize_text(text))


def file_hash(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


class ParseContext:
    def __init__(self, source_root: Path, source_format: str) -> None:
        self.source_root = source_root
        self.source_format = source_format
        self.items: list[ParsedMetadataItem] = []
        self.diagnostics: list[dict[str, Any]] = []
        self.layout_diagnostics: list[dict[str, Any]] = []
        self.seen_files: set[str] = set()
        self.parsed_files: set[str] = set()
        self.unknown_files: set[str] = set()
        self.item_keys: set[str] = set()

    def source_ref(self, path: Path, source_kind: str) -> SourceReference:
        relative = _relative(self.source_root, path)
        self.seen_files.add(relative)
        return SourceReference(path=relative, source_kind=source_kind)

    def mark_parsed(self, path: Path) -> None:
        self.parsed_files.add(_relative(self.source_root, path))

    def add_unknown_file(self, path: Path, message: str) -> None:
        ref = self.source_ref(path, "unknown")
        self.unknown_files.add(ref.path)
        self.layout_diagnostics.append(diagnostic("unknown_visible_part", "warning", message, [ref]))

    def unique_item_key(self, key: str, source_refs: list[SourceReference]) -> str:
        if key not in self.item_keys:
            self.item_keys.add(key)
            return key
        self.layout_diagnostics.append(
            diagnostic(
                "duplicate_semantic_coordinate",
                "error",
                "Duplicate semantic coordinate was found",
                source_refs,
            )
        )
        return key

    def coverage_counts(self) -> dict[str, int]:
        unknown_visible_paths = set(self.unknown_files)
        for item in self.items:
            if item.part_kind == PART_KIND_UNKNOWN:
                unknown_visible_paths.update(ref.path for ref in item.source_refs)
        return {
            "binary_forms": sum(1 for item in self.items if item.part_kind == PART_KIND_FORM and item.structural_payload.get("structure_level") == STRUCTURE_LEVEL_BINARY),
            "form_elements": sum(1 for item in self.items if item.part_kind == PART_KIND_FORM_ELEMENT),
            "form_modules": sum(1 for item in self.items if item.part_kind == PART_KIND_MODULE and ("Forms/" in item.part_path or "Form" in item.part_path)),
            "structured_forms": sum(1 for item in self.items if item.part_kind == PART_KIND_FORM and item.structural_payload.get("structure_level") == STRUCTURE_LEVEL_STRUCTURED),
            "unknown_visible_parts": len(unknown_visible_paths),
        }

    def build_snapshot(self) -> ParsedConfigurationSnapshot:
        items = sorted(self.items, key=lambda item: item.item_key)
        files_seen = sorted(self.seen_files)
        files_parsed = sorted(self.parsed_files)
        source_identity = {
            "root_name": self.source_root.name,
            "file_count": len(files_seen),
            "files_hash": _sha256_text("\n".join(files_seen)),
        }
        return ParsedConfigurationSnapshot(
            schema_version=PARSER_SCHEMA_VERSION,
            parser_version=PARSER_IMPLEMENTATION_VERSION,
            source_format=self.source_format,
            source_root=self.source_root.as_posix(),
            source_root_identity=source_identity,
            generated_at=utc_now_iso(),
            layout_diagnostics=sorted(self.layout_diagnostics, key=lambda item: _stable_json(item)),
            diagnostics=sorted(self.diagnostics, key=lambda item: _stable_json(item)),
            traversal={
                "coverage_counts": self.coverage_counts(),
                "files_seen": files_seen,
                "files_parsed": files_parsed,
                "unknown_files": sorted(self.unknown_files),
            },
            items=items,
        )


class XmlBslMetadataParser:
    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root

    def parse(self) -> ParsedConfigurationSnapshot:
        ctx = ParseContext(self.source_root, SOURCE_FORMAT_XML_BSL)
        if not detect_xml_bsl_layout(self.source_root):
            ref = SourceReference(path=".", source_kind="directory")
            ctx.layout_diagnostics.append(diagnostic("xml_bsl_layout_not_found", "error", "XML/BSL source layout was not found", [ref]))
            return ctx.build_snapshot()
        for path in sorted(self.source_root.rglob("*")):
            if path.is_file():
                ctx.seen_files.add(_relative(self.source_root, path))
        for xml_path in sorted(self.source_root.rglob("*.xml")):
            if self._is_ext_form_structure_file(xml_path):
                continue
            if xml_path.name == "Predefined.xml":
                self._parse_predefined_xml_part(ctx, xml_path)
                continue
            if "/Ext/" in xml_path.as_posix():
                self._parse_unknown_xml_part(ctx, xml_path)
                continue
            self._parse_xml_file(ctx, xml_path)
        for bsl_path in sorted(self.source_root.rglob("*.bsl")):
            self._parse_bsl_module(ctx, bsl_path)
        for binary_path in sorted(self.source_root.rglob("*")):
            if not binary_path.is_file() or binary_path.suffix.lower() not in {".bin", ".c1brace"}:
                continue
            if _relative(self.source_root, binary_path) not in ctx.parsed_files:
                self._parse_binary_part(ctx, binary_path)
        for path in sorted(self.source_root.rglob("*")):
            if not path.is_file():
                continue
            relative_path = _relative(self.source_root, path)
            if relative_path not in ctx.parsed_files and relative_path not in ctx.unknown_files:
                ctx.add_unknown_file(path, f"Visible XML/BSL source file is not supported by the parser: {relative_path}")
        return ctx.build_snapshot()

    def _parse_xml_file(self, ctx: ParseContext, xml_path: Path) -> None:
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError as exc:
            ref = ctx.source_ref(xml_path, "xml")
            ctx.layout_diagnostics.append(diagnostic("invalid_xml", "error", f"XML does not parse: {exc}", [ref]))
            return
        ctx.mark_parsed(xml_path)
        root = tree.getroot()
        object_node = self._metadata_node(root)
        if object_node is None:
            self._parse_unknown_xml_part(ctx, xml_path)
            return
        metadata_kind = _strip_namespace(object_node.tag)
        metadata_name = _child_text(object_node, "Name") or xml_path.stem
        external_part = self._external_part_for_xml_path(xml_path, metadata_kind, metadata_name)
        if external_part is not None:
            parent_kind, parent_name, part_kind, part_name, part_path = external_part
            self._add_item(
                ctx,
                parent_kind,
                parent_name,
                f"{parent_kind}.{parent_name}",
                part_kind,
                part_name,
                part_path,
                [ctx.source_ref(xml_path, "xml")],
                _element_to_payload(object_node),
            )
            return
        if metadata_kind == "Subsystem":
            metadata_name = self._subsystem_metadata_name(xml_path, metadata_name)
        metadata_full_name = f"{metadata_kind}.{metadata_name}"
        if self._is_form_file(xml_path, metadata_kind):
            self._add_xml_form(ctx, xml_path, object_node, metadata_kind, metadata_name)
            return
        payload = _element_to_payload(object_node)
        ref = ctx.source_ref(xml_path, "xml")
        self._add_item(ctx, metadata_kind, metadata_name, metadata_full_name, PART_KIND_OBJECT, "", "", [ref], payload)
        self._add_xml_nested_parts(ctx, xml_path, object_node, metadata_kind, metadata_name, metadata_full_name)

    def _metadata_node(self, root: ET.Element) -> ET.Element | None:
        if _strip_namespace(root.tag) != "MetaDataObject":
            return root
        for child in list(root):
            tag = _strip_namespace(child.tag)
            if tag != "InternalInfo":
                return child
        return None

    def _is_form_file(self, xml_path: Path, metadata_kind: str) -> bool:
        parts = xml_path.parts
        return "Forms" in parts or metadata_kind in {"Form", "CommonForm"}

    def _is_ext_form_structure_file(self, xml_path: Path) -> bool:
        parts = xml_path.parts
        return xml_path.name == "Form.xml" and "Forms" in parts and "Ext" in parts

    def _external_part_for_xml_path(self, xml_path: Path, metadata_kind: str, metadata_name: str) -> tuple[str, str, str, str, str] | None:
        relative_parts = list(xml_path.relative_to(self.source_root).parts)
        for collection_name, part_kind in (("Commands", PART_KIND_COMMAND), ("Templates", PART_KIND_TEMPLATE)):
            if collection_name not in relative_parts:
                continue
            collection_index = relative_parts.index(collection_name)
            if collection_index < 2:
                continue
            parent_dir = relative_parts[collection_index - 2]
            parent_name = relative_parts[collection_index - 1]
            if parent_dir == "Ext" or parent_dir not in XML_OBJECT_DIRS:
                continue
            parent_kind = XML_OBJECT_DIRS.get(parent_dir, parent_dir.rstrip("s"))
            part_name = metadata_name or xml_path.stem
            segment = XML_PART_PATH_SEGMENTS.get(part_kind, collection_name)
            return parent_kind, parent_name, part_kind, part_name, f"{segment}/{part_name}"
        return None

    def _subsystem_metadata_name(self, xml_path: Path, metadata_name: str) -> str:
        relative_parts = list(xml_path.relative_to(self.source_root).parts)
        if not relative_parts or relative_parts[0] != "Subsystems":
            return metadata_name
        names = [part for index, part in enumerate(relative_parts[1:]) if index % 2 == 0]
        if names and names[-1].endswith(".xml"):
            names[-1] = Path(names[-1]).stem
        return ".".join(part for part in names if part) or metadata_name

    def _add_xml_form(self, ctx: ParseContext, xml_path: Path, form_node: ET.Element, metadata_kind: str, form_name: str) -> None:
        parent_kind, parent_name = self._parent_for_xml_part(xml_path)
        metadata_full_name = f"{parent_kind}.{parent_name}" if parent_kind and parent_name else f"{metadata_kind}.{form_name}"
        binary_path = xml_path.with_suffix("") / "Ext" / "Form.bin"
        structure_path = xml_path.with_suffix("") / "Ext" / "Form.xml"
        refs = [ctx.source_ref(xml_path, "xml")]
        payload = _element_to_payload(form_node)
        structure_loaded = False
        payload["form_type"] = _child_text(form_node, "FormType") or STRUCTURE_LEVEL_UNKNOWN
        diagnostics: list[dict[str, Any]] = []
        form_structure_sources: list[tuple[ET.Element, Path]] = [(form_node, xml_path)]
        if structure_path.exists():
            try:
                structure_tree = ET.parse(structure_path)
                structure_node = structure_tree.getroot()
                refs.append(ctx.source_ref(structure_path, "xml"))
                ctx.mark_parsed(structure_path)
                payload["form_structure"] = _element_to_payload(structure_node)
                form_structure_sources.append((structure_node, structure_path))
                structure_loaded = True
            except ET.ParseError as exc:
                ref = ctx.source_ref(structure_path, "xml")
                refs.append(ref)
                ctx.layout_diagnostics.append(diagnostic("invalid_xml", "error", f"XML does not parse: {exc}", [ref]))
        structure_level = STRUCTURE_LEVEL_STRUCTURED if structure_loaded or not binary_path.exists() else STRUCTURE_LEVEL_BINARY
        payload["structure_level"] = structure_level
        if binary_path.exists():
            ctx.mark_parsed(binary_path)
            refs.append(ctx.source_ref(binary_path, "binary"))
            if structure_level == STRUCTURE_LEVEL_BINARY:
                payload["binary_payload"] = {"path": _relative(ctx.source_root, binary_path), "size": binary_path.stat().st_size}
                content_hash = file_hash(binary_path)
                diagnostics.append(diagnostic("binary_form_payload", "warning", "Element-level form structure is unavailable from binary payload", refs))
            else:
                content_hash = structural_hash(payload)
        else:
            content_hash = structural_hash(payload)
        self._add_item(
            ctx,
            parent_kind or metadata_kind,
            parent_name or form_name,
            metadata_full_name,
            PART_KIND_FORM,
            form_name,
            f"Forms/{form_name}",
            refs,
            payload,
            content_hash=content_hash,
            diagnostics=diagnostics,
        )
        if structure_level == STRUCTURE_LEVEL_STRUCTURED:
            seen_element_paths: dict[str, int] = {}
            for source_node, source_path in form_structure_sources:
                for element_node in self._stable_form_elements(source_node):
                    element_name = self._stable_form_element_name(element_node)
                    if not element_name:
                        continue
                    element_payload = _element_to_payload(element_node)
                    base_part_path = f"Forms/{form_name}/Elements/{element_name}"
                    duplicate_index = seen_element_paths.get(base_part_path, 0)
                    seen_element_paths[base_part_path] = duplicate_index + 1
                    part_path = base_part_path if duplicate_index == 0 else f"{base_part_path}[{duplicate_index + 1}]"
                    self._add_item(
                        ctx,
                        parent_kind or metadata_kind,
                        parent_name or form_name,
                        metadata_full_name,
                        PART_KIND_FORM_ELEMENT,
                        element_name,
                        part_path,
                        [ctx.source_ref(source_path, "xml")],
                        element_payload,
                    )

    def _stable_form_elements(self, form_node: ET.Element) -> list[ET.Element]:
        form_element_tags = {
            "AutoCommandBar",
            "Button",
            "ButtonGroup",
            "CommandBar",
            "Decoration",
            "Field",
            "FormElement",
            "Group",
            "Item",
            "Page",
            "Popup",
            "Table",
            "UsualGroup",
        }
        elements: list[ET.Element] = []
        for node in form_node.iter():
            if node is form_node:
                continue
            if _strip_namespace(node.tag) in {"Element", *form_element_tags} and self._stable_form_element_name(node):
                elements.append(node)
        return elements

    def _stable_form_element_name(self, node: ET.Element) -> str:
        return _direct_property_text(node, "Name") or str(node.attrib.get("name") or node.attrib.get("id") or _direct_property_text(node, "ID") or "").strip()

    def _parent_for_xml_part(self, xml_path: Path) -> tuple[str, str]:
        parts = list(xml_path.parts)
        if "Forms" not in parts:
            return "", ""
        form_index = parts.index("Forms")
        if form_index < 2:
            return "", ""
        object_dir = parts[form_index - 2]
        object_name = parts[form_index - 1]
        return XML_OBJECT_DIRS.get(object_dir, object_dir.rstrip("s")), object_name

    def _add_xml_nested_parts(
        self,
        ctx: ParseContext,
        xml_path: Path,
        object_node: ET.Element,
        metadata_kind: str,
        metadata_name: str,
        metadata_full_name: str,
    ) -> None:
        self_part_kind = XML_CHILD_OBJECT_PART_KINDS.get(_strip_namespace(object_node.tag))
        self_part_name = _child_text(object_node, "Name")
        if self_part_kind and self_part_name:
            self._add_item(
                ctx,
                metadata_kind,
                metadata_name,
                metadata_full_name,
                self_part_kind,
                self_part_name,
                self._xml_part_path(self_part_kind, self_part_name, ""),
                [ctx.source_ref(xml_path, "xml")],
                _element_to_payload(object_node),
            )
        self._add_xml_collection_parts(ctx, xml_path, object_node, metadata_kind, metadata_name, metadata_full_name, "")
        self._add_xml_child_object_parts(ctx, xml_path, object_node, metadata_kind, metadata_name, metadata_full_name, "")

    def _xml_part_path(self, part_kind: str, part_name: str, parent_path: str) -> str:
        segment = XML_PART_PATH_SEGMENTS.get(part_kind, part_kind)
        local_path = f"{segment}/{part_name}"
        return f"{parent_path}/{local_path}" if parent_path else local_path

    def _add_xml_child_object_parts(
        self,
        ctx: ParseContext,
        xml_path: Path,
        parent_node: ET.Element,
        metadata_kind: str,
        metadata_name: str,
        metadata_full_name: str,
        parent_path: str,
    ) -> None:
        child_objects = _find_first_child(parent_node, "ChildObjects")
        if child_objects is None:
            return
        for node in list(child_objects):
            tag = _strip_namespace(node.tag)
            part_kind = XML_CHILD_OBJECT_PART_KINDS.get(tag)
            if not part_kind:
                continue
            part_name = _child_text(node, "Name")
            if not part_name:
                continue
            part_path = self._xml_part_path(part_kind, part_name, parent_path)
            self._add_item(
                ctx,
                metadata_kind,
                metadata_name,
                metadata_full_name,
                part_kind,
                part_name,
                part_path,
                [ctx.source_ref(xml_path, "xml")],
                _element_to_payload(node),
            )
            self._add_xml_collection_parts(ctx, xml_path, node, metadata_kind, metadata_name, metadata_full_name, part_path)
            self._add_xml_child_object_parts(ctx, xml_path, node, metadata_kind, metadata_name, metadata_full_name, part_path)

    def _add_xml_collection_parts(
        self,
        ctx: ParseContext,
        xml_path: Path,
        parent_node: ET.Element,
        metadata_kind: str,
        metadata_name: str,
        metadata_full_name: str,
        parent_path: str,
    ) -> None:
        for collection in list(parent_node):
            collection_tag = _strip_namespace(collection.tag)
            part_kind = XML_COLLECTION_PART_KINDS.get(collection_tag)
            if not part_kind:
                continue
            for node in list(collection):
                tag_part_kind = XML_CHILD_OBJECT_PART_KINDS.get(_strip_namespace(node.tag), part_kind)
                if tag_part_kind != part_kind:
                    continue
                part_name = _child_text(node, "Name")
                if not part_name:
                    continue
                part_path = self._xml_part_path(part_kind, part_name, parent_path)
                self._add_item(
                    ctx,
                    metadata_kind,
                    metadata_name,
                    metadata_full_name,
                    part_kind,
                    part_name,
                    part_path,
                    [ctx.source_ref(xml_path, "xml")],
                    _element_to_payload(node),
                )
                self._add_xml_collection_parts(ctx, xml_path, node, metadata_kind, metadata_name, metadata_full_name, part_path)
                self._add_xml_child_object_parts(ctx, xml_path, node, metadata_kind, metadata_name, metadata_full_name, part_path)

    def _parse_bsl_module(self, ctx: ParseContext, bsl_path: Path) -> None:
        parent_kind, parent_name, module_name, part_path = self._module_coordinates(bsl_path)
        metadata_full_name = f"{parent_kind}.{parent_name}" if parent_kind and parent_name else "Configuration.Configuration"
        text = bsl_path.read_text(encoding="utf-8-sig")
        ref = ctx.source_ref(bsl_path, "bsl")
        ctx.mark_parsed(bsl_path)
        payload = {
            "module_name": module_name,
            "line_count": len(_normalize_text(text).splitlines()),
            "normalized_line_endings": True,
        }
        self._add_item(
            ctx,
            parent_kind or "Configuration",
            parent_name or "Configuration",
            metadata_full_name,
            PART_KIND_MODULE,
            module_name,
            part_path,
            [ref],
            payload,
            content_hash=module_hash(text),
        )

    def _module_coordinates(self, bsl_path: Path) -> tuple[str, str, str, str]:
        relative_parts = list(bsl_path.relative_to(self.source_root).parts)
        module_name = bsl_path.stem
        if "Commands" in relative_parts and "Ext" in relative_parts:
            command_index = relative_parts.index("Commands")
            ext_index = relative_parts.index("Ext")
            if command_index >= 2 and ext_index > command_index + 1:
                object_dir = relative_parts[command_index - 2]
                object_name = relative_parts[command_index - 1]
                command_name = relative_parts[command_index + 1]
                return (
                    XML_OBJECT_DIRS.get(object_dir, object_dir.rstrip("s")),
                    object_name,
                    f"{command_name}.{module_name}",
                    f"Commands/{command_name}/Modules/{module_name}",
                )
        if "Forms" in relative_parts and "Ext" in relative_parts:
            form_index = relative_parts.index("Forms")
            ext_index = relative_parts.index("Ext")
            if form_index >= 2 and ext_index > form_index + 1:
                object_dir = relative_parts[form_index - 2]
                object_name = relative_parts[form_index - 1]
                form_name = relative_parts[form_index + 1]
                return (
                    XML_OBJECT_DIRS.get(object_dir, object_dir.rstrip("s")),
                    object_name,
                    f"{form_name}.{module_name}",
                    f"Forms/{form_name}/Modules/{module_name}",
                )
        if "Ext" in relative_parts:
            ext_index = relative_parts.index("Ext")
            if ext_index >= 2:
                object_dir = relative_parts[ext_index - 2]
                object_name = relative_parts[ext_index - 1]
                return XML_OBJECT_DIRS.get(object_dir, object_dir.rstrip("s")), object_name, module_name, f"Modules/{module_name}"
        if len(relative_parts) >= 2:
            object_dir = relative_parts[0]
            return XML_OBJECT_DIRS.get(object_dir, object_dir.rstrip("s")), bsl_path.stem, module_name, f"Modules/{module_name}"
        return "Configuration", "Configuration", module_name, f"Modules/{module_name}"

    def _parse_binary_part(self, ctx: ParseContext, binary_path: Path) -> None:
        parent_kind, parent_name, part_name = self._binary_coordinates(binary_path)
        refs = [ctx.source_ref(binary_path, "binary")]
        ctx.mark_parsed(binary_path)
        payload = {"path": _relative(ctx.source_root, binary_path), "size": binary_path.stat().st_size}
        part_kind = PART_KIND_FORM if binary_path.name == "Form.bin" else PART_KIND_UNKNOWN
        structure_level = STRUCTURE_LEVEL_BINARY if part_kind == PART_KIND_FORM else STRUCTURE_LEVEL_UNKNOWN
        payload["structure_level"] = structure_level
        diagnostics = []
        if part_kind == PART_KIND_UNKNOWN:
            diagnostics.append(diagnostic("unknown_visible_part", "warning", f"Unsupported binary part: {binary_path.name}", refs))
        self._add_item(
            ctx,
            parent_kind,
            parent_name,
            f"{parent_kind}.{parent_name}",
            part_kind,
            part_name,
            _relative(ctx.source_root, binary_path),
            refs,
            payload,
            content_hash=file_hash(binary_path),
            diagnostics=diagnostics,
        )

    def _binary_coordinates(self, path: Path) -> tuple[str, str, str]:
        parts = list(path.relative_to(self.source_root).parts)
        if "Ext" in parts:
            ext_index = parts.index("Ext")
            if ext_index >= 2:
                object_dir = parts[ext_index - 2]
                object_name = parts[ext_index - 1]
                return XML_OBJECT_DIRS.get(object_dir, object_dir.rstrip("s")), object_name, path.stem
        if len(parts) >= 2:
            return XML_OBJECT_DIRS.get(parts[0], parts[0].rstrip("s")), parts[1], path.stem
        return "Configuration", "Configuration", path.stem

    def _parse_unknown_xml_part(self, ctx: ParseContext, xml_path: Path) -> None:
        ref = ctx.source_ref(xml_path, "xml")
        relative_path = _relative(ctx.source_root, xml_path)
        ctx.unknown_files.add(relative_path)
        ctx.layout_diagnostics.append(diagnostic("unknown_visible_part", "warning", f"Unsupported XML metadata part: {relative_path}", [ref]))
        ctx.mark_parsed(xml_path)
        parent_kind, parent_name, part_name = self._unknown_xml_coordinates(xml_path)
        self._add_item(
            ctx,
            parent_kind,
            parent_name,
            f"{parent_kind}.{parent_name}",
            PART_KIND_UNKNOWN,
            part_name,
            relative_path,
            [ref],
            {"path": relative_path, "structure_level": STRUCTURE_LEVEL_UNKNOWN},
            diagnostics=[diagnostic("unknown_visible_part", "warning", f"Unsupported XML metadata part: {relative_path}", [ref])],
        )

    def _parse_predefined_xml_part(self, ctx: ParseContext, xml_path: Path) -> None:
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError as exc:
            ref = ctx.source_ref(xml_path, "xml")
            ctx.layout_diagnostics.append(diagnostic("invalid_xml", "error", f"XML does not parse: {exc}", [ref]))
            return
        ref = ctx.source_ref(xml_path, "xml")
        ctx.mark_parsed(xml_path)
        parent_kind, parent_name, fallback_name = self._unknown_xml_coordinates(xml_path)
        metadata_full_name = f"{parent_kind}.{parent_name}"
        nodes = self._predefined_nodes(tree.getroot())
        if not nodes:
            self._add_item(
                ctx,
                parent_kind,
                parent_name,
                metadata_full_name,
                PART_KIND_PREDEFINED_VALUE,
                fallback_name,
                _relative(ctx.source_root, xml_path),
                [ref],
                {"path": _relative(ctx.source_root, xml_path), "structure_level": STRUCTURE_LEVEL_UNKNOWN, "empty": True},
            )
            return
        name_counts = Counter(self._predefined_node_name(node, fallback_name) for node in nodes)
        for node in nodes:
            part_name = self._predefined_node_name(node, fallback_name)
            if not part_name:
                part_name = f"{fallback_name}:{len(ctx.items) + 1}"
            part_path_name = part_name
            if name_counts[part_name] > 1:
                code = _direct_property_text(node, "Code")
                identifier = str(node.attrib.get("id") or "").strip()
                suffix = code or identifier or str(len(ctx.items) + 1)
                part_path_name = f"{part_name}/{suffix}"
            self._add_item(
                ctx,
                parent_kind,
                parent_name,
                metadata_full_name,
                PART_KIND_PREDEFINED_VALUE,
                part_name,
                f"Predefined/{part_path_name}",
                [ref],
                _element_to_payload(node),
            )

    def _predefined_node_name(self, node: ET.Element, fallback_name: str) -> str:
        return _direct_property_text(node, "Name") or str(node.attrib.get("name") or node.attrib.get("id") or fallback_name).strip()

    def _predefined_nodes(self, root: ET.Element) -> list[ET.Element]:
        tags = {"Item", "PredefinedItem", "PredefinedDataItem", "PredefinedValue", "Predefined"}
        nodes: list[ET.Element] = []
        for node in root.iter():
            if node is root:
                continue
            if _strip_namespace(node.tag) in tags and (_direct_property_text(node, "Name") or node.attrib.get("name") or node.attrib.get("id")):
                nodes.append(node)
        return nodes

    def _unknown_xml_coordinates(self, xml_path: Path) -> tuple[str, str, str]:
        parts = list(xml_path.relative_to(self.source_root).parts)
        if "Forms" in parts:
            form_index = parts.index("Forms")
            if form_index >= 2:
                object_dir = parts[form_index - 2]
                object_name = parts[form_index - 1]
                form_name = parts[form_index + 1] if len(parts) > form_index + 1 else xml_path.stem
                return XML_OBJECT_DIRS.get(object_dir, object_dir.rstrip("s")), object_name, f"{form_name}.{xml_path.stem}"
        if "Ext" in parts:
            ext_index = parts.index("Ext")
            if ext_index >= 2:
                object_dir = parts[ext_index - 2]
                object_name = parts[ext_index - 1]
                return XML_OBJECT_DIRS.get(object_dir, object_dir.rstrip("s")), object_name, xml_path.stem
        if len(parts) >= 2:
            return XML_OBJECT_DIRS.get(parts[0], parts[0].rstrip("s")), parts[1], xml_path.stem
        return "Configuration", "Configuration", xml_path.stem

    def _add_item(
        self,
        ctx: ParseContext,
        metadata_kind: str,
        metadata_name: str,
        metadata_full_name: str,
        part_kind: str,
        part_name: str,
        part_path: str,
        source_refs: list[SourceReference],
        structural_payload: dict[str, Any],
        content_hash: str = "",
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        key = ctx.unique_item_key(make_item_key(metadata_kind, metadata_full_name, part_kind, part_name, part_path), source_refs)
        ctx.items.append(
            ParsedMetadataItem(
                item_key=key,
                source_format=ctx.source_format,
                metadata_kind=metadata_kind,
                metadata_name=metadata_name,
                metadata_full_name=metadata_full_name,
                part_kind=part_kind,
                part_name=part_name,
                part_path=part_path,
                source_refs=source_refs,
                structural_payload=structural_payload,
                content_hash=content_hash or structural_hash(structural_payload),
                diagnostics=diagnostics or [],
            )
        )


class V8UnpackMetadataParser:
    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root

    def parse(self) -> ParsedConfigurationSnapshot:
        ctx = ParseContext(self.source_root, SOURCE_FORMAT_V8UNPACK)
        if not detect_v8unpack_layout(self.source_root):
            ref = SourceReference(path=".", source_kind="directory")
            ctx.layout_diagnostics.append(diagnostic("v8unpack_layout_not_found", "error", "Supported v8unpack source layout was not found", [ref]))
            return ctx.build_snapshot()
        for path in sorted(self.source_root.rglob("*")):
            if path.is_file():
                ctx.seen_files.add(_relative(self.source_root, path))
        for object_dir in sorted(child for child in self.source_root.iterdir() if child.is_dir()):
            if object_dir.name not in V8UNPACK_OBJECT_DIRS:
                for path in sorted(object_dir.rglob("*")):
                    if path.is_file():
                        ctx.add_unknown_file(path, f"Unsupported v8unpack top-level object directory: {object_dir.name}")
                continue
            for object_root in sorted(child for child in object_dir.iterdir() if child.is_dir()):
                self._parse_object_directory(ctx, object_dir.name, object_root)
        for root_file in sorted(self.source_root.iterdir()):
            if root_file.is_file():
                self._parse_root_file(ctx, root_file)
        return ctx.build_snapshot()

    def _parse_object_directory(self, ctx: ParseContext, metadata_kind: str, object_root: Path) -> None:
        metadata_name = object_root.name
        metadata_full_name = f"{metadata_kind}.{metadata_name}"
        object_json = object_root / f"{metadata_kind}.json"
        object_payload: dict[str, Any] = {"name": metadata_name}
        refs: list[SourceReference] = []
        if object_json.exists():
            payload = self._read_json(ctx, object_json)
            if isinstance(payload, dict):
                object_payload.update(payload)
                metadata_name = str(payload.get("name") or metadata_name)
                metadata_full_name = f"{metadata_kind}.{metadata_name}"
            refs.append(ctx.source_ref(object_json, "json"))
            ctx.mark_parsed(object_json)
        else:
            refs.append(ctx.source_ref(object_root, "directory"))
        self._add_item(ctx, metadata_kind, metadata_name, metadata_full_name, PART_KIND_OBJECT, "", "", refs, object_payload)
        for path in sorted(object_root.rglob("*")):
            if not path.is_file() or _relative(ctx.source_root, path) in ctx.parsed_files:
                continue
            self._parse_v8_part(ctx, metadata_kind, metadata_name, metadata_full_name, object_root, path)

    def _parse_root_file(self, ctx: ParseContext, path: Path) -> None:
        if path.suffix == ".bsl":
            text = path.read_text(encoding="utf-8-sig")
            ref = ctx.source_ref(path, "bsl")
            ctx.mark_parsed(path)
            self._add_item(
                ctx,
                "Configuration",
                "Configuration",
                "Configuration.Configuration",
                PART_KIND_MODULE,
                path.stem,
                f"Modules/{path.name}",
                [ref],
                {"module_name": path.stem, "line_count": len(_normalize_text(text).splitlines()), "normalized_line_endings": True},
                content_hash=module_hash(text),
            )
            return
        ctx.add_unknown_file(path, f"Unsupported v8unpack root file: {path.name}")
        ctx.mark_parsed(path)

    def _parse_v8_part(
        self,
        ctx: ParseContext,
        metadata_kind: str,
        metadata_name: str,
        metadata_full_name: str,
        object_root: Path,
        path: Path,
    ) -> None:
        rel_to_object = path.relative_to(object_root).as_posix()
        if self._is_v8_form_path(path, rel_to_object) and path.suffix in {".bin", ".c1brace"} and self._has_adjacent_readable_v8_form(path):
            ctx.source_ref(path, "binary")
            ctx.mark_parsed(path)
            return
        name = path.stem
        part_kind = self._part_kind_from_path(path, rel_to_object)
        source_kind = self._source_kind(path)
        ref = ctx.source_ref(path, source_kind)
        payload: dict[str, Any] = {"path": _relative(ctx.source_root, path)}
        content_hash = ""
        diagnostics: list[dict[str, Any]] = []
        if path.suffix == ".json":
            loaded = self._read_json(ctx, path)
            if isinstance(loaded, dict):
                payload.update(loaded)
                name = str(loaded.get("name") or loaded.get("right") or name)
            content_hash = structural_hash(payload)
        elif path.suffix == ".bsl":
            text = path.read_text(encoding="utf-8-sig")
            payload.update({"module_name": name, "line_count": len(_normalize_text(text).splitlines()), "normalized_line_endings": True})
            content_hash = module_hash(text)
        else:
            payload.update({"size": path.stat().st_size})
            content_hash = file_hash(path)
        if part_kind == PART_KIND_FORM and path.suffix == ".bin":
            payload["structure_level"] = STRUCTURE_LEVEL_BINARY
            diagnostics.append(diagnostic("binary_form_payload", "warning", "Element-level form structure is unavailable from binary payload", [ref]))
        elif part_kind == PART_KIND_FORM:
            payload["structure_level"] = STRUCTURE_LEVEL_STRUCTURED
        elif part_kind == PART_KIND_UNKNOWN:
            payload["structure_level"] = STRUCTURE_LEVEL_UNKNOWN
            severity = "warning" if path.suffix in {".bin", ".c1brace", ".json"} else "error"
            diagnostics.append(diagnostic("unknown_visible_part", severity, f"Unsupported v8unpack part: {rel_to_object}", [ref]))
        ctx.mark_parsed(path)
        self._add_item(ctx, metadata_kind, metadata_name, metadata_full_name, part_kind, name, rel_to_object, [ref], payload, content_hash, diagnostics)

    def _read_json(self, ctx: ParseContext, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            ref = ctx.source_ref(path, "json")
            ctx.layout_diagnostics.append(diagnostic("invalid_json", "error", f"JSON does not parse: {exc}", [ref]))
            return {}

    def _part_kind_from_path(self, path: Path, rel_to_object: str) -> str:
        lowered = rel_to_object.lower()
        if "predefined" in lowered or "предустанов" in lowered or "предопредел" in lowered:
            return PART_KIND_PREDEFINED_VALUE
        if path.name.endswith(".id.json"):
            return PART_KIND_OBJECT
        if path.name.endswith(".elem.json"):
            return PART_KIND_FORM_ELEMENT
        if path.suffix == ".bsl":
            return PART_KIND_MODULE
        if "Form/" in rel_to_object or "Form." in rel_to_object or path.parent.name.endswith("Form"):
            return PART_KIND_FORM if path.suffix in {".json", ".bin"} else PART_KIND_UNKNOWN
        if "Template" in rel_to_object:
            return PART_KIND_TEMPLATE
        if "Role" in rel_to_object and path.suffix in {".json", ".c1brace"}:
            return PART_KIND_ROLE_RIGHT
        if path.suffix in {".bin", ".c1brace"}:
            return PART_KIND_UNKNOWN
        if path.suffix == ".json":
            return PART_KIND_OBJECT if path.parent.name == path.parent.parent.name else PART_KIND_UNKNOWN
        return PART_KIND_UNKNOWN

    def _is_v8_form_path(self, path: Path, rel_to_object: str) -> bool:
        return "Form/" in rel_to_object or "Form." in rel_to_object or path.parent.name.endswith("Form")

    def _has_adjacent_readable_v8_form(self, path: Path) -> bool:
        return any(sibling.suffix == ".json" and not sibling.name.endswith(".id.json") for sibling in path.parent.iterdir() if sibling.is_file())

    def _source_kind(self, path: Path) -> str:
        if path.suffix == ".json":
            return "json"
        if path.suffix == ".bsl":
            return "bsl"
        return "binary"

    def _add_item(
        self,
        ctx: ParseContext,
        metadata_kind: str,
        metadata_name: str,
        metadata_full_name: str,
        part_kind: str,
        part_name: str,
        part_path: str,
        source_refs: list[SourceReference],
        structural_payload: dict[str, Any],
        content_hash: str = "",
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        key = ctx.unique_item_key(make_item_key(metadata_kind, metadata_full_name, part_kind, part_name, part_path), source_refs)
        ctx.items.append(
            ParsedMetadataItem(
                item_key=key,
                source_format=ctx.source_format,
                metadata_kind=metadata_kind,
                metadata_name=metadata_name,
                metadata_full_name=metadata_full_name,
                part_kind=part_kind,
                part_name=part_name,
                part_path=part_path,
                source_refs=source_refs,
                structural_payload=structural_payload,
                content_hash=content_hash or structural_hash(structural_payload),
                diagnostics=diagnostics or [],
            )
        )


def detect_xml_bsl_layout(source_root: Path) -> bool:
    if not source_root.exists() or not source_root.is_dir():
        return False
    if (source_root / "Configuration.xml").exists():
        return True
    for child in source_root.iterdir():
        if child.is_dir() and child.name in XML_OBJECT_DIRS and any(child.glob("*.xml")):
            return True
    return False


def detect_v8unpack_layout(source_root: Path) -> bool:
    if not source_root.exists() or not source_root.is_dir():
        return False
    for child in source_root.iterdir():
        if not child.is_dir() or child.name not in V8UNPACK_OBJECT_DIRS:
            continue
        for object_root in child.iterdir():
            if object_root.is_dir() and any(object_root.glob("*.json")):
                return True
    return any(source_root.glob("Configuration.*.bsl"))


def select_source_format(source_root: Path, requested: str) -> tuple[str, list[dict[str, Any]]]:
    xml_detected = detect_xml_bsl_layout(source_root)
    v8_detected = detect_v8unpack_layout(source_root)
    diagnostics: list[dict[str, Any]] = []
    if requested == SOURCE_FORMAT_XML_BSL:
        if not xml_detected:
            diagnostics.append(diagnostic("xml_bsl_layout_not_found", "error", "XML/BSL source layout was not found"))
        return SOURCE_FORMAT_XML_BSL, diagnostics
    if requested == SOURCE_FORMAT_V8UNPACK:
        if not v8_detected:
            diagnostics.append(diagnostic("v8unpack_layout_not_found", "error", "Supported v8unpack source layout was not found"))
        return SOURCE_FORMAT_V8UNPACK, diagnostics
    detected = [fmt for fmt, found in ((SOURCE_FORMAT_XML_BSL, xml_detected), (SOURCE_FORMAT_V8UNPACK, v8_detected)) if found]
    if len(detected) == 1:
        return detected[0], diagnostics
    if not detected:
        diagnostics.append(diagnostic("source_layout_not_found", "error", "No supported source layout was found"))
    else:
        diagnostics.append(diagnostic("ambiguous_source_layout", "error", "More than one supported source layout was found; choose an explicit source format"))
    return SOURCE_FORMAT_AUTO, diagnostics


def parse_configuration_source(source_root: Path, source_format: str = SOURCE_FORMAT_AUTO) -> ParsedConfigurationSnapshot:
    root = source_root.resolve()
    selected, selection_diagnostics = select_source_format(root, source_format)
    if selected == SOURCE_FORMAT_XML_BSL:
        snapshot = XmlBslMetadataParser(root).parse()
    elif selected == SOURCE_FORMAT_V8UNPACK:
        snapshot = V8UnpackMetadataParser(root).parse()
    else:
        snapshot = ParsedConfigurationSnapshot(
            schema_version=PARSER_SCHEMA_VERSION,
            parser_version=PARSER_IMPLEMENTATION_VERSION,
            source_format=SOURCE_FORMAT_AUTO,
            source_root=root.as_posix(),
            source_root_identity={"root_name": root.name, "file_count": 0, "files_hash": _sha256_text("")},
            generated_at=utc_now_iso(),
            layout_diagnostics=[],
            diagnostics=[],
            traversal={"files_seen": [], "files_parsed": [], "unknown_files": []},
            items=[],
        )
    snapshot.layout_diagnostics.extend(selection_diagnostics)
    snapshot.layout_diagnostics = sorted(snapshot.layout_diagnostics, key=lambda item: _stable_json(item))
    return snapshot


def validate_snapshot(snapshot: ParsedConfigurationSnapshot, fail_on_warnings: bool = False) -> list[str]:
    errors: list[str] = []
    if snapshot.schema_version != PARSER_SCHEMA_VERSION:
        errors.append(f"Unsupported parser schema version: {snapshot.schema_version}")
    if snapshot.source_format not in SOURCE_FORMATS:
        errors.append(f"Invalid source format: {snapshot.source_format}")
    item_keys: set[str] = set()
    if not snapshot.items:
        errors.append("Parser snapshot has no items")
    sorted_keys = [item.item_key for item in snapshot.items]
    if sorted_keys != sorted(sorted_keys):
        errors.append("Parser snapshot items are not sorted by item_key")
    for item in snapshot.items:
        if item.item_key in item_keys:
            errors.append(f"Duplicate item_key: {item.item_key}")
        item_keys.add(item.item_key)
        required = {
            "item_key": item.item_key,
            "source_format": item.source_format,
            "metadata_kind": item.metadata_kind,
            "metadata_name": item.metadata_name,
            "metadata_full_name": item.metadata_full_name,
            "part_kind": item.part_kind,
            "content_hash": item.content_hash,
        }
        for field_name, value in required.items():
            if not value:
                errors.append(f"{item.item_key or '<missing key>'} lacks required field: {field_name}")
        if item.source_format not in SOURCE_FORMATS:
            errors.append(f"{item.item_key} has invalid source_format: {item.source_format}")
        if item.part_kind not in PART_KINDS:
            errors.append(f"{item.item_key} has invalid part_kind: {item.part_kind}")
        structure_level = item.structural_payload.get("structure_level")
        if structure_level and structure_level not in STRUCTURE_LEVELS:
            errors.append(f"{item.item_key} has invalid structure_level: {structure_level}")
        if not item.source_refs:
            errors.append(f"{item.item_key} has no source references")
        for ref in item.source_refs:
            if not ref.path or not ref.source_kind:
                errors.append(f"{item.item_key} has invalid source reference")
    all_diagnostics = [*snapshot.layout_diagnostics, *snapshot.diagnostics]
    for item in snapshot.items:
        all_diagnostics.extend(item.diagnostics)
    for diag in all_diagnostics:
        severity = str(diag.get("severity") or "")
        if severity == "error" or (fail_on_warnings and severity == "warning"):
            errors.append(f"{diag.get('code', 'diagnostic')}: {diag.get('message', '')}")
    files_seen = set(snapshot.traversal.get("files_seen") or [])
    files_parsed = set(snapshot.traversal.get("files_parsed") or [])
    unknown_files = set(snapshot.traversal.get("unknown_files") or [])
    coverage_counts = snapshot.traversal.get("coverage_counts")
    if not isinstance(coverage_counts, dict):
        errors.append("Parser traversal lacks coverage_counts")
    else:
        for counter_name in ("binary_forms", "form_elements", "form_modules", "structured_forms", "unknown_visible_parts"):
            if not isinstance(coverage_counts.get(counter_name), int):
                errors.append(f"Parser traversal coverage_counts lacks integer counter: {counter_name}")
    covered_source_refs = files_seen | files_parsed | unknown_files
    source_root = Path(snapshot.source_root) if snapshot.source_root else None
    if files_parsed and not files_parsed.issubset(files_seen):
        errors.append("Parser traversal includes parsed files outside files_seen")
    for item in snapshot.items:
        for ref in item.source_refs:
            if not ref.path or ref.path in covered_source_refs:
                continue
            if source_root is not None and not Path(ref.path).is_absolute() and (source_root / ref.path).exists():
                continue
            errors.append(f"{item.item_key} source reference is not covered by traversal: {ref.path}")
    unaccounted_files = sorted(files_seen - files_parsed - unknown_files)
    if unaccounted_files:
        sample = ", ".join(unaccounted_files[:10])
        suffix = "" if len(unaccounted_files) <= 10 else f", ... (+{len(unaccounted_files) - 10} more)"
        errors.append(f"Parser visible source files were not parsed or diagnosed: {sample}{suffix}")
    return errors


def write_snapshot(snapshot: ParsedConfigurationSnapshot, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def read_snapshot(path: Path) -> ParsedConfigurationSnapshot:
    return ParsedConfigurationSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))


def default_snapshot_path(root: Path, source_root: Path, source_format: str) -> Path:
    selected, _ = select_source_format(source_root.resolve(), source_format)
    safe_name = re.sub(r"[^0-9A-Za-zА-Яа-я_.-]+", "_", source_root.name).strip("_") or "configuration"
    safe_format = selected if selected in SOURCE_FORMATS else source_format
    return repo_path(root, DEFAULT_SNAPSHOT_DIR) / f"{safe_name}.{safe_format}.snapshot.json"


def parse_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    source_root = Path(args.source_root)
    if not source_root.is_absolute():
        source_root = repo_path(root, args.source_root)
    snapshot = parse_configuration_source(source_root, args.source_format)
    errors = [] if args.parse_only else validate_snapshot(snapshot)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    output = Path(args.output) if args.output else default_snapshot_path(root, source_root, args.source_format)
    if not output.is_absolute():
        output = repo_path(root, output.as_posix())
    write_snapshot(snapshot, output)
    print(f"configuration_source_snapshot: {output.relative_to(root).as_posix() if output.is_relative_to(root) else output.as_posix()}")
    print(f"items: {len(snapshot.items)}")
    print(f"source_format: {snapshot.source_format}")
    return 0


def validate_command(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve()
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_absolute():
        snapshot_path = repo_path(root, args.snapshot)
    snapshot = read_snapshot(snapshot_path)
    errors = validate_snapshot(snapshot, fail_on_warnings=args.strict)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"configuration_source_snapshot_valid: {snapshot_path.relative_to(root).as_posix() if snapshot_path.is_relative_to(root) else snapshot_path.as_posix()}")
    print(f"items: {len(snapshot.items)}")
    return 0
