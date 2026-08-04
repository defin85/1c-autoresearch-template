from __future__ import annotations

import csv
import os
import re
import subprocess
import tempfile
import json
import importlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, TypedDict, runtime_checkable

from .contracts import JsonValue, atomic_json, canonical_json, comparison_id, confined, content_id, diff_id, normalize_relative, parse_json, parse_json_object, repository_lock, require_tracked_clean, sha256
from .extension_analyzer import Dependency, Detail, DiffEvidence, MainMatch
from .source_routing import RoutingGroup, RoutingManifest, RoutingMember
from .sources import ComponentRecord


INVENTORY_HEADER = ("stable_diff_id", "comparison_id", "source_generation", "before_role", "after_role", "change_type", "path", "object_kind", "object_name", "area", "before_fingerprint", "after_fingerprint", "content_fingerprint")
ID_MAP_HEADER = ("stable_diff_id", "comparison_id", "change_type", "path", "active", "first_seen_generation", "last_seen_generation", "predecessor_diff_id", "successor_diff_id", "content_fingerprint", "notes")
COVERAGE_HEADER = ("customer_diff_id", "source_generation", "target_diff_ids", "coverage_status", "evidence_ref", "notes")
PATH_COVERAGE_HEADER = ("raw_diff_id", "comparison_id", "change_type", "path", "semantic_diff_ids", "noise_reason")
COMPARISONS = (("customer-customization", "vendor_baseline", "target_cf"), ("target-release", "vendor_baseline", "next_vendor"))
STATUSES = {"A": "added", "D": "deleted", "M": "modified", "T": "type_changed"}
BASE_FILES = ("diff-inventory.csv", "diff-id-map.csv", "target-coverage.csv")
ANALYZER_FILES = ("extension-analyzer-manifest.json", "extension-diff.jsonl", "extension-dependencies.jsonl", "extension-physical-diff.csv", "extension-path-coverage.csv")
VERSION_FILES = {"1": BASE_FILES, "2": BASE_FILES + ANALYZER_FILES}
DETAIL_KEYS = {
    "schema_version", "stable_diff_id", "comparison_id", "before_role", "after_role", "extension_uuid",
    "change_type", "object_scope", "intervention_kind", "intervention_key", "extension_object_identity",
    "affected_base_identity", "symbol_identity", "structural_sublocation", "before_structural_fingerprint",
    "after_structural_fingerprint", "before_evidence_fingerprint", "after_evidence_fingerprint", "evidence",
    "dependency_ids", "diagnostic_codes",
}
DEPENDENCY_KEYS = {
    "schema_version", "dependency_id", "stable_diff_id", "role", "extension_uuid", "dependency_kind",
    "normalized_source_reference", "resolved_base_identity", "outcome", "evidence", "diagnostic_code",
}
MANIFEST_KEYS = {
    "schema_version", "extension_analyzer_version", "source_generation_id", "source_comparison_epoch_fingerprint",
    "routing_manifest_fingerprint", "extension_adapter_versions", "component_bindings", "record_counts",
    "data_files", "output_fingerprint",
}
BINDING_KEYS = {"role", "component_id", "component_path", "component_fingerprint", "extension_uuid", "active", "representation_schema", "adapter_id", "adapter_version"}
EVIDENCE_KEYS = {"role", "side", "path", "fingerprint"}
SEMANTIC_CHANGE_TYPES = {"added", "deleted", "modified"}
INTERVENTION_KINDS = {
    "object_definition", "metadata_change", "form_change", "command_change", "role_change",
    "module_addition", "method_extension", "method_interception", "base_reference",
}
DEPENDENCY_OUTCOMES = {"present_compatible", "present_changed", "missing", "unresolved"}
TARGET_COVERAGE_STATUSES = {"covered_by_vendor", "still_required", "changed_in_target", "needs_semantic_review"}


class ComponentBinding(TypedDict):
    role: str
    component_id: str
    component_path: str
    component_fingerprint: str
    extension_uuid: str
    active: bool
    representation_schema: str
    adapter_id: str
    adapter_version: str


class AnalyzerManifest(TypedDict):
    schema_version: str
    extension_analyzer_version: str
    source_generation_id: str
    source_comparison_epoch_fingerprint: str
    routing_manifest_fingerprint: str
    extension_adapter_versions: list[str]
    component_bindings: list[ComponentBinding]
    record_counts: dict[str, int]
    data_files: dict[str, str]
    output_fingerprint: str


class AnalyzerResult(TypedDict):
    adapter_versions: list[str]
    component_bindings: list[ComponentBinding]
    dependency_rows: list[Dependency]
    detail_rows: list[Detail]
    extension_analyzer_version: str
    path_coverage: list[dict[str, str]]
    physical_rows: list[dict[str, str]]
    semantic_rows: list[dict[str, str]]


class DiffPointer(TypedDict):
    schema_version: str
    generation_id: str
    source_generation_id: str
    files: dict[str, str]
    row_counts: dict[str, int]


class SourcePointer(TypedDict, total=False):
    schema_version: str
    generation_id: str
    acquisition_profile_id: str
    representation_schema: str
    normalizer_version: str
    routing_manifest_path: str
    routing_manifest_fingerprint: str
    components: list[ComponentRecord]
    source_comparison_epoch_fingerprint: str


@runtime_checkable
class _StageRecompute(Protocol):
    def recover_active_publication(self, repo: Path) -> object: ...


def _recover_active_publication(repo: Path) -> None:
    module = importlib.import_module(".stage_recompute", __package__)
    if not isinstance(module, _StageRecompute):
        raise RuntimeError("invalid stage recompute module")
    _ = module.recover_active_publication(repo)


def _evidence(value: object) -> DiffEvidence:
    row = parse_json_object(canonical_json(value).decode("utf-8"))
    return {
        "role": _string(row["role"]),
        "side": _string(row["side"]),
        "path": _string(row["path"]),
        "fingerprint": _string(row["fingerprint"]),
    }


def _detail(value: object) -> Detail:
    row = parse_json_object(canonical_json(value).decode("utf-8"))
    string_keys = DETAIL_KEYS - {"dependency_ids", "diagnostic_codes", "evidence"}
    strings = {key: _string(row[key]) for key in string_keys}
    return Detail(
        **strings,
        dependency_ids=_strings(row["dependency_ids"]),
        diagnostic_codes=_strings(row["diagnostic_codes"]),
        evidence=[_evidence(item) for item in _objects(row["evidence"])],
    )


def _dependency(value: object) -> Dependency:
    row = parse_json_object(canonical_json(value).decode("utf-8"))
    string_keys = DEPENDENCY_KEYS - {"evidence"}
    strings = {key: _string(row[key]) for key in string_keys}
    return Dependency(**strings, evidence=[_evidence(item) for item in _objects(row["evidence"])])


def _binding(value: object) -> ComponentBinding:
    row = parse_json_object(canonical_json(value).decode("utf-8"))
    active = row["active"]
    if not isinstance(active, bool):
        raise ValueError("expected boolean")
    return ComponentBinding(
        **{key: _string(row[key]) for key in BINDING_KEYS - {"active"}},
        active=active,
    )


def _analyzer_manifest(value: object) -> AnalyzerManifest:
    row = parse_json_object(canonical_json(value).decode("utf-8"))
    return {
        "schema_version": _string(row["schema_version"]),
        "extension_analyzer_version": _string(row["extension_analyzer_version"]),
        "source_generation_id": _string(row["source_generation_id"]),
        "source_comparison_epoch_fingerprint": _string(row["source_comparison_epoch_fingerprint"]),
        "routing_manifest_fingerprint": _string(row["routing_manifest_fingerprint"]),
        "extension_adapter_versions": _strings(row["extension_adapter_versions"]),
        "component_bindings": [_binding(item) for item in _objects(row["component_bindings"])],
        "record_counts": _integer_map(row["record_counts"]),
        "data_files": _string_map(row["data_files"]),
        "output_fingerprint": _string(row["output_fingerprint"]),
    }


def _routing_manifest(value: object) -> RoutingManifest:
    row = parse_json_object(canonical_json(value).decode("utf-8"))
    groups: list[RoutingGroup] = []
    for value_group in _objects(row["groups"]):
        members: list[RoutingMember] = [
            {"role": _string(member["role"]), "component_id": _string(member["component_id"])}
            for member in _objects(value_group["members"])
        ]
        groups.append({
            "routing_group_id": _string(value_group["routing_group_id"]),
            "kind": _string(value_group.get("kind", "")),
            "members": members,
            "absent_roles": _strings(value_group.get("absent_roles", [])),
            "probe_contract_version": _string(value_group.get("probe_contract_version", "")),
            "probe_fingerprints": [
                {"component_id": _string(item["component_id"]), "probe_fingerprint": _string(item["probe_fingerprint"])}
                for item in _objects(value_group.get("probe_fingerprints", []))
            ],
            "form_counts": _integer_map(value_group.get("form_counts", {})),
            "routing_reason": _string(value_group.get("routing_reason", "")),
            "exporter": _string(value_group.get("exporter", "")),
            "representation_schema": _string(value_group["representation_schema"]),
            "exporter_version": _string(value_group.get("exporter_version", "")),
            "converter_version": _string(value_group.get("converter_version", "")),
        })
    return {
        "schema_version": _string(row["schema_version"]),
        "routing_contract_version": _string(row.get("routing_contract_version", "")),
        "groups": groups,
        "routing_manifest_fingerprint": _string(row["routing_manifest_fingerprint"]),
    }


def _analyzer_result(value: object) -> AnalyzerResult:
    row = parse_json_object(canonical_json(value).decode("utf-8"))
    return {
        "adapter_versions": _strings(row["adapter_versions"]),
        "component_bindings": [_binding(item) for item in _objects(row["component_bindings"])],
        "dependency_rows": [_dependency(item) for item in _objects(row["dependency_rows"])],
        "detail_rows": [_detail(item) for item in _objects(row["detail_rows"])],
        "extension_analyzer_version": _string(row["extension_analyzer_version"]),
        "path_coverage": [_string_map(item) for item in _objects(row["path_coverage"])],
        "physical_rows": [_string_map(item) for item in _objects(row["physical_rows"])],
        "semantic_rows": [_string_map(item) for item in _objects(row["semantic_rows"])],
    }


def _strings(value: object) -> list[str]:
    normalized = parse_json(canonical_json(value).decode("utf-8"))
    if not isinstance(normalized, list):
        raise ValueError("expected string array")
    if not all(isinstance(item, str) for item in normalized):
        raise ValueError("expected string array")
    return [item for item in normalized if isinstance(item, str)]


def _objects(value: object) -> list[dict[str, JsonValue]]:
    normalized = parse_json(canonical_json(value).decode("utf-8"))
    if not isinstance(normalized, list) or not all(isinstance(item, dict) for item in normalized):
        raise ValueError("expected object array")
    return [item for item in normalized if isinstance(item, dict)]


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected integer")
    return value


def _string_map(value: object) -> dict[str, str]:
    normalized = parse_json(canonical_json(value).decode("utf-8"))
    if not isinstance(normalized, dict) or not all(isinstance(item, str) for item in normalized.values()):
        raise ValueError("expected string map")
    return {key: item for key, item in normalized.items() if isinstance(item, str)}


def _integer_map(value: object) -> dict[str, int]:
    normalized = parse_json(canonical_json(value).decode("utf-8"))
    if not isinstance(normalized, dict) or not all(isinstance(item, int) and not isinstance(item, bool) for item in normalized.values()):
        raise ValueError("expected integer map")
    return {key: item for key, item in normalized.items() if isinstance(item, int) and not isinstance(item, bool)}


def _component(value: object) -> ComponentRecord:
    row = parse_json_object(canonical_json(value).decode("utf-8"))
    base: ComponentRecord = {
        "component_id": _string(row["component_id"]),
        "kind": _string(row["kind"]),
        "path": _string(row["path"]),
        "fingerprint": _string(row["fingerprint"]),
    }
    if "representation_schema" in row:
        base = {
            **base,
            "representation_schema": _string(row["representation_schema"]),
            "routing_group_id": _string(row["routing_group_id"]),
            "bsl_file_count": _integer(row["bsl_file_count"]),
        }
    return base


def source_pointer(value: dict[str, JsonValue]) -> SourcePointer:
    result: SourcePointer = {}
    for key in (
        "schema_version", "generation_id", "acquisition_profile_id",
        "representation_schema", "normalizer_version", "routing_manifest_path",
        "routing_manifest_fingerprint", "source_comparison_epoch_fingerprint",
    ):
        if key in value:
            result[key] = _string(value[key])
    if "components" in value:
        result["components"] = [_component(item) for item in _objects(value["components"])]
    return result


def diff_pointer(value: dict[str, JsonValue]) -> DiffPointer:
    return {
        "schema_version": _string(value["schema_version"]),
        "generation_id": _string(value["generation_id"]),
        "source_generation_id": _string(value["source_generation_id"]),
        "files": _string_map(value["files"]),
        "row_counts": _integer_map(value["row_counts"]),
    }


_source_pointer = source_pointer
def _valid_fingerprint(value: object, *, optional: bool = False) -> bool:
    return bool(optional and value == "") or bool(re.fullmatch(r"sha256:[0-9a-f]{64}", str(value)))


def _fingerprint(path: Path) -> str:
    return "" if not path.is_file() else "sha256:" + sha256(path.read_bytes())


def _relative(raw: bytes, roots: tuple[Path, Path]) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Git returned a non-UTF-8 path") from exc
    path = Path(text)
    candidate = path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
    for root in roots:
        try:
            return normalize_relative(candidate.relative_to(root.resolve()).as_posix())
        except ValueError:
            pass
    raise ValueError(f"Git path escapes role roots: {text}")


def compare(before_root: Path, after_root: Path, metadata: dict[str, str]) -> list[dict[str, str]]:
    command = ["git", "-c", "core.quotepath=false", "-c", "core.fileMode=false", "diff", "--no-index", "--name-status", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", "--", str(before_root.resolve()), str(after_root.resolve())]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env={"PATH": os.environ.get("PATH", "")})
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"Git comparison failed with exit {result.returncode}")
    fields = result.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        _ = fields.pop()
    if len(fields) % 2:
        raise ValueError("invalid NUL-delimited Git output")
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, str]] = []
    cmp_id = metadata.get("comparison_id") or comparison_id(metadata["comparison_kind"], metadata["before_role"], metadata["after_role"], metadata["acquisition_profile_id"], metadata["representation_schema"], metadata["normalizer_version"])
    for status_raw, path_raw in zip(fields[::2], fields[1::2], strict=True):
        try:
            status = status_raw.decode("ascii")
            change_type = STATUSES[status]
        except (UnicodeDecodeError, KeyError) as exc:
            raise ValueError(f"unsupported Git status: {status_raw!r}") from exc
        payload_relative = _relative(path_raw, (before_root, after_root))
        if payload_relative == "component-manifest.json" and not metadata.get("include_component_manifest"):
            continue
        relative = f"{metadata.get('path_prefix', '').rstrip('/')}/{payload_relative}".lstrip("/")
        key = (change_type, relative)
        if key in seen:
            raise ValueError(f"duplicate Git row: {key}")
        seen.add(key)
        before = before_root / payload_relative
        after = after_root / payload_relative
        before_fp, after_fp = _fingerprint(before), _fingerprint(after)
        if before_fp and before_fp == after_fp and before.is_file() == after.is_file():
            continue
        content = "sha256:" + sha256(canonical_json({"before": before_fp, "after": after_fp}))
        rows.append({
            "stable_diff_id": diff_id(cmp_id, relative, change_type, metadata.get("schema_version", "1")), "comparison_id": cmp_id,
            "source_generation": metadata["source_generation"], "before_role": metadata["before_role"],
            "after_role": metadata["after_role"], "change_type": change_type, "path": relative,
            "object_kind": "file", "object_name": Path(relative).name, "area": relative.partition("/")[0],
            "before_fingerprint": before_fp, "after_fingerprint": after_fp, "content_fingerprint": content,
        })
    return sorted(rows, key=lambda row: (row["comparison_id"], row["path"], row["change_type"]))


def _write_csv(path: Path, header: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    def field(value: str) -> str:
        return f'"{value.replace(chr(34), chr(34) * 2)}"' if any(char in value for char in ',"\r\n') else value

    with path.open("w", encoding="utf-8", newline="") as stream:
        _ = stream.write(",".join(field(name) for name in header) + "\n")
        for row in rows:
            _ = stream.write(",".join(field(row[name]) for name in header) + "\n")
        stream.flush(); os.fsync(stream.fileno())


def _write_jsonl(path: Path, rows: Sequence[object]) -> None:
    with path.open("wb") as stream:
        for row in sorted(rows, key=canonical_json):
            _ = stream.write(canonical_json(row) + b"\n")
        stream.flush(); os.fsync(stream.fileno())


def read_csv(path: Path, header: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != header:
            raise ValueError(f"invalid diff CSV schema: {path.name}")
        raw_rows = list(reader)
        if any(value is None for row in raw_rows for value in row.values()):
            raise ValueError(f"invalid diff CSV row: {path.name}")
        rows = [{key: _string(value) for key, value in row.items()} for row in raw_rows]
    expected = path.read_bytes()
    with tempfile.TemporaryDirectory() as temporary:
        canonical = Path(temporary) / path.name
        _write_csv(canonical, header, rows)
        if canonical.read_bytes() != expected:
            raise ValueError(f"non-canonical diff CSV: {path.name}")
    return rows


def _read_jsonl(path: Path, keys: set[str]) -> list[dict[str, JsonValue]]:
    rows: list[dict[str, JsonValue]] = []
    for line in path.read_bytes().splitlines(keepends=True):
        if not line.endswith(b"\n"):
            raise ValueError(f"non-canonical JSONL: {path.name}")
        try:
            row = parse_json_object(line.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSONL: {path.name}") from exc
        if set(row) != keys or canonical_json(row) + b"\n" != line:
            raise ValueError(f"invalid or non-canonical JSONL record: {path.name}")
        rows.append(row)
    if rows != sorted(rows, key=canonical_json):
        raise ValueError(f"unsorted JSONL: {path.name}")
    return rows


def validate_active(
    repo: Path,
    *,
    require_tracked_clean_state: bool = False,
    candidate: DiffPointer | None = None,
    source_candidate: SourcePointer | None = None,
) -> DiffPointer:
    if candidate is None:
        _recover_active_publication(repo)
    pointer = candidate or diff_pointer(parse_json_object((repo / "research/active-diff-generation.json").read_text(encoding="utf-8")))
    source = source_candidate or source_pointer(parse_json_object((repo / "research/active-source-generation.json").read_text(encoding="utf-8")))
    generation = str(pointer.get("generation_id", "")); source_id = str(source.get("generation_id", ""))
    if pointer.get("source_generation_id") != source_id or not generation:
        raise ValueError("active diff generation is stale")
    version = str(pointer.get("schema_version", ""))
    if version not in VERSION_FILES:
        raise ValueError("unsupported active diff schema version")
    root = confined(repo / "analysis/indexes/generations", generation)
    if require_tracked_clean_state and candidate is None:
        require_tracked_clean(repo, [root, repo / "research/active-diff-generation.json"])
    headers = {"diff-inventory.csv": INVENTORY_HEADER, "diff-id-map.csv": ID_MAP_HEADER, "target-coverage.csv": COVERAGE_HEADER}
    rows: dict[str, list[dict[str, str]]] = {}
    actual_names = tuple(sorted(path.name for path in root.iterdir() if path.is_file()))
    if actual_names != tuple(sorted(VERSION_FILES[version])):
        raise ValueError("active diff generation has a mixed or incomplete file set")
    actual_hashes = {name: sha256((root / name).read_bytes()) for name in VERSION_FILES[version]}
    for name, header in headers.items():
        rows[name] = read_csv(root / name, header)
        if pointer.get("row_counts", {}).get(name) != len(rows[name]): raise ValueError(f"diff row count mismatch: {name}")
    if actual_hashes != pointer.get("files"):
        raise ValueError("active diff file hash mismatch")
    if sha256(canonical_json({"files": actual_hashes, "schema_version": version, "source_generation_id": source_id})) != generation:
        raise ValueError("active diff generation ID mismatch")
    details: list[Detail] = []
    from .extension_analyzer import target_coverage as expected_target_coverage
    if version == "2":
        from .extension_analyzer import (
            ADAPTERS,
            ANALYZER_VERSION,
            dependency_id as expected_dependency_id,
        )

        details = [_detail(row) for row in _read_jsonl(root / "extension-diff.jsonl", DETAIL_KEYS)]
        dependencies = [_dependency(row) for row in _read_jsonl(root / "extension-dependencies.jsonl", DEPENDENCY_KEYS)]
        physical = read_csv(root / "extension-physical-diff.csv", INVENTORY_HEADER)
        path_coverage = read_csv(root / "extension-path-coverage.csv", PATH_COVERAGE_HEADER)
        for name, values in (
            ("extension-diff.jsonl", details),
            ("extension-dependencies.jsonl", dependencies),
            ("extension-physical-diff.csv", physical),
            ("extension-path-coverage.csv", path_coverage),
        ):
            if pointer.get("row_counts", {}).get(name) != len(values):
                raise ValueError(f"diff row count mismatch: {name}")
        manifest_path = root / "extension-analyzer-manifest.json"
        manifest = _analyzer_manifest(parse_json_object(manifest_path.read_text(encoding="utf-8")))
        if set(manifest) != MANIFEST_KEYS or manifest_path.read_bytes() != canonical_json(manifest) + b"\n":
            raise ValueError("invalid extension analyzer manifest")
        manifest_preimage = {key: value for key, value in manifest.items() if key != "output_fingerprint"}
        if manifest["output_fingerprint"] != "sha256:" + sha256(canonical_json(manifest_preimage)):
            raise ValueError("extension analyzer output fingerprint mismatch")
        data_files = {name: actual_hashes[name] for name in ANALYZER_FILES if name != "extension-analyzer-manifest.json"}
        expected_counts = {
            "extension_diff": len(details),
            "extension_dependencies": len(dependencies),
            "extension_physical_diff": len(physical),
            "extension_path_coverage": len(path_coverage),
        }
        if manifest.get("data_files") != data_files or manifest.get("record_counts") != expected_counts:
            raise ValueError("extension analyzer manifest bindings mismatch")
        if (
            manifest.get("source_generation_id") != source_id
            or manifest.get("source_comparison_epoch_fingerprint") != source.get("source_comparison_epoch_fingerprint")
            or manifest.get("routing_manifest_fingerprint") != source.get("routing_manifest_fingerprint")
        ):
            raise ValueError("extension analyzer source binding mismatch")
        if (
            manifest.get("extension_analyzer_version") != ANALYZER_VERSION
            or manifest["extension_adapter_versions"] != sorted(ADAPTERS.values())
            or manifest["extension_adapter_versions"] != sorted(set(manifest["extension_adapter_versions"]))
            or manifest["component_bindings"] != sorted(manifest["component_bindings"], key=canonical_json)
            or any(set(item) != BINDING_KEYS for item in manifest["component_bindings"])
            or set(manifest["record_counts"]) != {"extension_diff", "extension_dependencies", "extension_physical_diff", "extension_path_coverage"}
            or set(manifest["data_files"]) != set(ANALYZER_FILES) - {"extension-analyzer-manifest.json"}
        ):
            raise ValueError("invalid extension analyzer manifest shape or ordering")
        source_components = {item["component_id"]: item for item in source.get("components", [])}
        expected_component_ids = {
            item["component_id"] for item in source.get("components", [])
            if item.get("kind") == "extension"
        }
        if {item["component_id"] for item in manifest["component_bindings"]} != expected_component_ids:
            raise ValueError("incomplete extension component bindings")
        binding_by_role_uuid: dict[tuple[str, str], ComponentBinding] = {}
        for binding in manifest["component_bindings"]:
            component = source_components.get(binding["component_id"])
            adapter = ADAPTERS.get(binding["representation_schema"], "")
            expected_adapter = tuple(adapter.split("@", 1)) if "@" in adapter else None
            if (
                not component
                or component.get("kind") != "extension"
                or component.get("path") != binding["component_path"]
                or component.get("fingerprint") != binding["component_fingerprint"]
                or expected_adapter != (binding["adapter_id"], binding["adapter_version"])
                or binding["role"] not in {"vendor_baseline", "target_cf", "next_vendor"}
                or not binding["component_id"].startswith(f"{binding['role']}:extension:")
                or binding["component_id"].rsplit(":", 1)[-1] != binding["extension_uuid"]
            ):
                raise ValueError("invalid extension component binding")
            binding_by_role_uuid[(binding["role"], binding["extension_uuid"])] = binding
        detail_ids = {row["stable_diff_id"] for row in details}
        if len(detail_ids) != len(details) or any(
            row["schema_version"] != "2"
            or row["change_type"] not in SEMANTIC_CHANGE_TYPES
            or row["object_scope"] not in {"owned", "adopted"}
            or row["intervention_kind"] not in INTERVENTION_KINDS
            or not str(row["stable_diff_id"]).startswith("DIF-")
            or not str(row["intervention_key"]).startswith("EIN-")
            or not _valid_fingerprint(row["before_structural_fingerprint"], optional=True)
            or not _valid_fingerprint(row["after_structural_fingerprint"], optional=True)
            or not _valid_fingerprint(row["before_evidence_fingerprint"], optional=True)
            or not _valid_fingerprint(row["after_evidence_fingerprint"], optional=True)
            or row["evidence"] != sorted(row["evidence"], key=canonical_json)
            or row["dependency_ids"] != sorted(row["dependency_ids"])
            or row["diagnostic_codes"] != sorted(row["diagnostic_codes"])
            or not row["evidence"]
            or any(
                set(item) != EVIDENCE_KEYS
                or item["role"] not in {"vendor_baseline", "target_cf", "next_vendor"}
                or item["side"] not in {"before", "after"}
                or not _valid_fingerprint(item["fingerprint"])
                for item in row["evidence"]
            )
            or any(row[key] is None for key in DETAIL_KEYS)
            for row in details
        ):
            raise ValueError("invalid extension semantic detail ordering")
        source_root = repo / "sources/generations" / source_id
        for detail in details:
            expected_key = content_id("EIN-", {
                "affected_base_identity": detail["affected_base_identity"],
                "extension_analyzer_version": ANALYZER_VERSION,
                "extension_object_identity": detail["extension_object_identity"],
                "extension_uuid": detail["extension_uuid"],
                "intervention_kind": detail["intervention_kind"],
                "object_scope": detail["object_scope"],
                "structural_sublocation": detail["structural_sublocation"],
                "symbol_identity": detail["symbol_identity"],
            })
            if detail["intervention_key"] != expected_key:
                raise ValueError("invalid extension intervention identity")
            for evidence in detail["evidence"]:
                binding = binding_by_role_uuid.get((evidence["role"], detail["extension_uuid"]))
                try:
                    relative = normalize_relative(evidence["path"])
                except ValueError as exc:
                    raise ValueError("invalid extension evidence path") from exc
                evidence_path = confined(source_root / binding["component_path"], relative) if binding else None
                if (
                    evidence_path is None
                    or not evidence_path.is_file()
                    or "sha256:" + sha256(evidence_path.read_bytes()) != evidence["fingerprint"]
                ):
                    raise ValueError("extension evidence is missing or has wrong fingerprint")
        if (
            len({row["dependency_id"] for row in dependencies}) != len(dependencies)
            or any(
                row["schema_version"] != "2"
                or row["role"] not in {"vendor_baseline", "target_cf", "next_vendor"}
                or row["outcome"] not in DEPENDENCY_OUTCOMES
                or not str(row["dependency_id"]).startswith("DEP-")
                or row["evidence"] != sorted(row["evidence"], key=canonical_json)
                or any(
                    set(item) != EVIDENCE_KEYS
                    or item["role"] != row["role"]
                    or item["side"] not in {"before", "after"}
                    or not _valid_fingerprint(item["fingerprint"])
                    for item in row["evidence"]
                )
                or any(row[key] is None for key in DEPENDENCY_KEYS)
                for row in dependencies
            )
        ):
            raise ValueError("duplicate extension dependency identity")
        if any(set(row["dependency_ids"]) != {item["dependency_id"] for item in dependencies if item["stable_diff_id"] == row["stable_diff_id"]} for row in details) or any(row["stable_diff_id"] not in detail_ids for row in dependencies):
            raise ValueError("extension dependency ownership mismatch")
        configuration_by_role = {
            role: source_components.get(f"{role}:configuration")
            for role in ("vendor_baseline", "target_cf", "next_vendor")
        }
        for dependency in dependencies:
            if dependency["dependency_id"] != expected_dependency_id(
                next(row["intervention_key"] for row in details if row["stable_diff_id"] == dependency["stable_diff_id"]),
                dependency["role"],
                dependency["dependency_kind"],
                dependency["normalized_source_reference"],
            ):
                raise ValueError("invalid extension dependency identity")
            component = configuration_by_role.get(dependency["role"])
            for evidence in dependency["evidence"]:
                try:
                    relative = normalize_relative(evidence["path"])
                except ValueError as exc:
                    raise ValueError("invalid extension dependency evidence path") from exc
                evidence_path = confined(source_root / component["path"], relative) if component else None
                if (
                    evidence_path is None
                    or not evidence_path.is_file()
                    or "sha256:" + sha256(evidence_path.read_bytes()) != evidence["fingerprint"]
                ):
                    raise ValueError("extension dependency evidence is missing or has wrong fingerprint")
        physical_ids = {row["stable_diff_id"] for row in physical}
        if detail_ids & physical_ids:
            raise ValueError("raw and semantic extension identities overlap")
        if len(path_coverage) != len(physical) or {row["raw_diff_id"] for row in path_coverage} != physical_ids:
            raise ValueError("extension path coverage is incomplete")
        physical_by_id = {row["stable_diff_id"]: row for row in physical}
        for row in path_coverage:
            owners = _strings(parse_json(row["semantic_diff_ids"]))
            raw = physical_by_id[row["raw_diff_id"]]
            if owners != sorted(set(owners)) or bool(owners) == bool(row["noise_reason"]):
                raise ValueError("invalid extension path disposition")
            if (
                any(owner not in detail_ids for owner in owners)
                or row["noise_reason"] not in {"", "component_manifest"}
                or (row["comparison_id"], row["change_type"], row["path"])
                != (raw["comparison_id"], raw["change_type"], raw["path"])
                or (row["noise_reason"] == "component_manifest" and not row["path"].endswith("/component-manifest.json"))
            ):
                raise ValueError("invalid extension path owner or noise reason")
            if row["noise_reason"] == "component_manifest":
                parts = Path(row["path"]).parts
                try:
                    extension_index = parts.index("extensions")
                    extension_uuid = parts[extension_index + 1]
                except (ValueError, IndexError) as exc:
                    raise ValueError("invalid component manifest noise path") from exc
                before_binding = binding_by_role_uuid.get((raw["before_role"], extension_uuid))
                after_binding = binding_by_role_uuid.get((raw["after_role"], extension_uuid))

                def component_manifest(binding: ComponentBinding | None) -> dict[str, JsonValue] | None:
                    if binding is None:
                        return None
                    path = source_root / binding["component_path"] / "component-manifest.json"
                    return parse_json_object(path.read_text(encoding="utf-8"))

                before_manifest = component_manifest(before_binding)
                after_manifest = component_manifest(after_binding)
                authoritative = {"name", "version", "active", "uuid", "kind", "representation_schema"}
                if (
                    before_manifest is None
                    or after_manifest is None
                    or any(
                        before_manifest.get(key) != after_manifest.get(key)
                        for key in authoritative
                    )
                ):
                    raise ValueError("component manifest noise contains authoritative drift")
    seen: set[str] = set(); customer: set[str] = set()
    routed = version == "2"
    detail_by_id = {row["stable_diff_id"]: row for row in details}
    manifest = _analyzer_manifest(parse_json_object((root / "extension-analyzer-manifest.json").read_text(encoding="utf-8"))) if routed else None
    routed_ids = {
        (kind, before, after): content_id("CMP-", {
            "after_role": after,
            "before_role": before,
            "comparison_kind": kind,
            "extension_adapter_versions": manifest["extension_adapter_versions"] if manifest else [],
            "extension_analyzer_version": manifest["extension_analyzer_version"] if manifest else "",
            "schema_version": "2",
            "source_comparison_epoch_fingerprint": _string(source.get("source_comparison_epoch_fingerprint")),
        })
        for kind, before, after in COMPARISONS
    } if routed else {}
    for row in rows["diff-inventory.csv"]:
        if row["source_generation"] != source_id or row["before_role"] != "vendor_baseline" or row["after_role"] not in {"target_cf", "next_vendor"}:
            raise ValueError("mixed or invalid diff roles")
        kind = "customer-customization" if row["after_role"] == "target_cf" else "target-release"
        cmp_id = routed_ids[(kind, row["before_role"], row["after_role"])] if routed else comparison_id(kind, row["before_role"], row["after_role"], _string(source.get("acquisition_profile_id")), _string(source.get("representation_schema")), _string(source.get("normalizer_version")))
        if row["object_kind"] == "extension_intervention":
            detail = detail_by_id.get(row["stable_diff_id"])
            expected = content_id("DIF-", {"change_type": row["change_type"], "comparison_id": cmp_id, "intervention_key": detail["intervention_key"] if detail else "", "schema_version": "2"})
        else:
            expected = diff_id(cmp_id, row["path"], row["change_type"], "2" if routed else "1")
        if row["comparison_id"] != cmp_id or row["stable_diff_id"] != expected or expected in seen:
            raise ValueError(f"invalid or duplicate stable DIF: {row['stable_diff_id']}")
        if routed and row["path"].startswith("extensions/") and row["object_kind"] != "extension_intervention":
            raise ValueError("raw extension row entered canonical inventory")
        seen.add(expected)
        if row["after_role"] == "target_cf": customer.add(expected)
    active_map = [row for row in rows["diff-id-map.csv"] if row["active"] == "true"]
    if routed and {
        row["stable_diff_id"]
        for row in rows["diff-inventory.csv"]
        if row["object_kind"] == "extension_intervention"
    } != set(detail_by_id):
        raise ValueError("semantic detail and inventory identities differ")
    if len({row["stable_diff_id"] for row in rows["diff-id-map.csv"]}) != len(rows["diff-id-map.csv"]) or {row["stable_diff_id"] for row in active_map} != seen or len(active_map) != len(seen):
        raise ValueError("diff ID map must contain exactly one active row per inventory DIF")
    if any(row["active"] not in {"true", "false"} or len(row["first_seen_generation"]) != 64 or len(row["last_seen_generation"]) != 64 for row in rows["diff-id-map.csv"]):
        raise ValueError("invalid diff ID history")
    coverage = [row["customer_diff_id"] for row in rows["target-coverage.csv"]]
    if set(coverage) != customer or len(coverage) != len(customer):
        raise ValueError("target coverage must contain exactly one row per customer DIF")
    if any(row["coverage_status"] not in TARGET_COVERAGE_STATUSES for row in rows["target-coverage.csv"]):
        raise ValueError("invalid target coverage status")
    if routed:
        target_details = [row for row in details if row["after_role"] == "next_vendor"]
        for row in rows["target-coverage.csv"]:
            detail = detail_by_id.get(row["customer_diff_id"])
            if detail and detail["after_role"] == "target_cf":
                expected = expected_target_coverage(detail, target_details)
                if (
                    row["coverage_status"] != expected["coverage"]
                    or row["target_diff_ids"] != expected["target_stable_diff_id"]
                ):
                    raise ValueError("invalid semantic target coverage")
    return pointer


def build(repo: Path, source_pointer: SourcePointer, cancelled: Callable[[], bool] | None = None, *, activate: bool = True) -> DiffPointer:
    if source_pointer.get("schema_version") == "2":
        from .sources import validate_active as validate_source
        if activate and validate_source(repo, deep=False) != source_pointer:
            raise RuntimeError("stale or invalid source generation")
    with repository_lock(repo):
        current = parse_json_object((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
        if activate and canonical_json(current) != canonical_json(source_pointer):
            raise RuntimeError("stale source generation")
        if cancelled and cancelled():
            raise InterruptedError("diff build cancelled before comparison")
        return _build_locked(repo, source_pointer, cancelled=cancelled, activate=activate)


def _build_locked(repo: Path, source_pointer: SourcePointer, cancelled: Callable[[], bool] | None = None, *, activate: bool = True) -> DiffPointer:
    if source_pointer.get("schema_version") == "2":
        return _build_routed(repo, source_pointer, cancelled=cancelled, activate=activate)
    raise ValueError("new diff builds require routed source schema version 2")


def _publish_inventory(repo: Path, source_pointer: SourcePointer, inventory: list[dict[str, str]]) -> DiffPointer:
    source_id = _string(source_pointer.get("generation_id"))
    routed = source_pointer.get("schema_version") == "2"
    id_preimages: dict[str, bytes] = {}
    for row in inventory:
        preimage = canonical_json({"change_type": row["change_type"], "comparison_id": row["comparison_id"], "path": row["path"], "schema_version": "2" if routed else "1"})
        if row["stable_diff_id"] in id_preimages and id_preimages[row["stable_diff_id"]] != preimage:
            raise ValueError(f"truncated DIF hash collision: {row['stable_diff_id']}")
        id_preimages[row["stable_diff_id"]] = preimage
    by_payload: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in inventory:
        fp = row["after_fingerprint"] if row["change_type"] == "added" else row["before_fingerprint"]
        by_payload.setdefault((row["comparison_id"], fp), []).append(row)
    predecessors: dict[str, str] = {}; successors: dict[str, str] = {}
    for candidates in by_payload.values():
        deleted = [row for row in candidates if row["change_type"] == "deleted"]
        added = [row for row in candidates if row["change_type"] == "added"]
        if len(deleted) == len(added) == 1:
            predecessors[added[0]["stable_diff_id"]] = deleted[0]["stable_diff_id"]
            successors[deleted[0]["stable_diff_id"]] = added[0]["stable_diff_id"]
    previous: dict[str, dict[str, str]] = {}
    pointer_path = repo / "research/active-diff-generation.json"
    current_comparisons = {row["comparison_id"] for row in inventory}
    if not current_comparisons:
        if routed:
            epoch = _string(source_pointer.get("source_comparison_epoch_fingerprint"))
            current_comparisons = {content_id("CMP-", {"after_role": after, "before_role": before, "comparison_kind": kind, "schema_version": "2", "source_comparison_epoch_fingerprint": epoch}) for kind, before, after in COMPARISONS}
        else:
            current_comparisons = {comparison_id(kind, before, after, _string(source_pointer.get("acquisition_profile_id")), _string(source_pointer.get("representation_schema")), _string(source_pointer.get("normalizer_version"))) for kind, before, after in COMPARISONS}
    if pointer_path.is_file():
        old_pointer = parse_json_object(pointer_path.read_text(encoding="utf-8"))
        old_map = repo / "analysis/indexes/generations" / str(old_pointer.get("generation_id", "")) / "diff-id-map.csv"
        if old_map.is_file():
            with old_map.open(encoding="utf-8", newline="") as stream:
                previous = {row["stable_diff_id"]: row for row in csv.DictReader(stream) if row.get("comparison_id") in current_comparisons}
    id_map = [{"stable_diff_id": row["stable_diff_id"], "comparison_id": row["comparison_id"], "change_type": row["change_type"], "path": row["path"], "active": "true", "first_seen_generation": previous.get(row["stable_diff_id"], {}).get("first_seen_generation", source_id), "last_seen_generation": source_id, "predecessor_diff_id": predecessors.get(row["stable_diff_id"], ""), "successor_diff_id": successors.get(row["stable_diff_id"], ""), "content_fingerprint": row["content_fingerprint"], "notes": ""} for row in inventory]
    active_ids = {row["stable_diff_id"] for row in inventory}
    id_map.extend({**row, "active": "false"} for identifier, row in previous.items() if identifier not in active_ids)
    id_map.sort(key=lambda row: (row["comparison_id"], row["path"], row["change_type"], row["stable_diff_id"]))
    target_by_path = {row["path"]: row for row in inventory if row["after_role"] == "next_vendor"}
    coverage: list[dict[str, str]] = []
    for row in inventory:
        if row["after_role"] != "target_cf":
            continue
        target = target_by_path.get(row["path"])
        if target and target["change_type"] == row["change_type"] and target["after_fingerprint"] == row["after_fingerprint"]:
            status = "covered_by_vendor"
        elif target is None:
            status = "still_required"
        else:
            status = "changed_in_target"
        evidence = {
            "baseline": row["before_fingerprint"],
            "customer": row["after_fingerprint"],
            "target": target["after_fingerprint"] if target else row["before_fingerprint"],
        }
        coverage.append({
            "customer_diff_id": row["stable_diff_id"],
            "source_generation": source_id,
            "target_diff_ids": target["stable_diff_id"] if target else "",
            "coverage_status": status,
            "evidence_ref": canonical_json(evidence).decode("utf-8"),
            "notes": "",
        })
    staging_parent = repo / "analysis/indexes/.staging"; staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=staging_parent) as temporary:
        staging = Path(temporary)
        _write_csv(staging / "diff-inventory.csv", INVENTORY_HEADER, inventory)
        _write_csv(staging / "diff-id-map.csv", ID_MAP_HEADER, id_map)
        _write_csv(staging / "target-coverage.csv", COVERAGE_HEADER, coverage)
        hashes = {name: sha256((staging / name).read_bytes()) for name in ("diff-inventory.csv", "diff-id-map.csv", "target-coverage.csv")}
        generation_id = sha256(canonical_json({"files": hashes, "schema_version": "1", "source_generation_id": source_id}))
        destination = repo / "analysis/indexes/generations" / generation_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            os.replace(staging, destination)
            parent_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        if any(sha256((destination / name).read_bytes()) != digest for name, digest in hashes.items()):
            raise RuntimeError("existing diff generation does not match its deterministic identity")
        pointer: DiffPointer = {"schema_version": "1", "generation_id": generation_id, "source_generation_id": source_id, "files": hashes, "row_counts": {"diff-inventory.csv": len(inventory), "diff-id-map.csv": len(id_map), "target-coverage.csv": len(coverage)}}
        atomic_json(repo / "research/active-diff-generation.json", pointer)
        return pointer


def _publish_routed_inventory(repo: Path, source_pointer: SourcePointer, inventory: list[dict[str, str]], analysis: AnalyzerResult, *, activate: bool = True) -> DiffPointer:
    from .extension_analyzer import target_coverage as semantic_target_coverage

    source_id = _string(source_pointer.get("generation_id"))
    inventory = sorted(inventory, key=lambda row: (row["comparison_id"], row["path"], row["change_type"], row["stable_diff_id"]))
    details = sorted(analysis["detail_rows"], key=canonical_json)
    dependencies = sorted(analysis["dependency_rows"], key=canonical_json)
    physical = sorted(analysis["physical_rows"], key=lambda row: (row["comparison_id"], row["path"], row["change_type"], row["stable_diff_id"]))
    path_coverage = sorted(analysis["path_coverage"], key=lambda row: (row["comparison_id"], row["path"], row["change_type"], row["raw_diff_id"]))
    detail_by_id = {row["stable_diff_id"]: row for row in details}
    preimages: dict[str, bytes] = {}
    for row in inventory:
        detail = detail_by_id.get(row["stable_diff_id"])
        preimage = canonical_json(
            {"change_type": row["change_type"], "comparison_id": row["comparison_id"], "intervention_key": detail["intervention_key"], "schema_version": "2"}
            if row["object_kind"] == "extension_intervention" and detail
            else {"change_type": row["change_type"], "comparison_id": row["comparison_id"], "path": row["path"], "schema_version": "2"}
        )
        previous_preimage = preimages.get(row["stable_diff_id"])
        if previous_preimage is not None and previous_preimage != preimage:
            fingerprints = sorted((sha256(previous_preimage), sha256(preimage)))
            raise ValueError(f"truncated DIF hash collision: {row['stable_diff_id']} preimages={fingerprints}")
        preimages[row["stable_diff_id"]] = preimage
    if len(preimages) != len(inventory):
        raise ValueError("duplicate routed DIF identity")
    for row in inventory:
        expected = content_id("DIF-", parse_json_object(preimages[row["stable_diff_id"]].decode()))
        if expected != row["stable_diff_id"]:
            raise ValueError(f"invalid routed DIF identity: {row['stable_diff_id']}")
    pointer_path = repo / "research/active-diff-generation.json"
    previous: dict[str, dict[str, str]] = {}
    current_comparisons = {row["comparison_id"] for row in inventory}
    if pointer_path.is_file():
        old_pointer = parse_json_object(pointer_path.read_text(encoding="utf-8"))
        old_map = repo / "analysis/indexes/generations" / str(old_pointer.get("generation_id", "")) / "diff-id-map.csv"
        if old_map.is_file():
            with old_map.open(encoding="utf-8", newline="") as stream:
                previous = {row["stable_diff_id"]: row for row in csv.DictReader(stream) if row.get("comparison_id") in current_comparisons}
    by_payload: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in inventory:
        fingerprint = row["after_fingerprint"] if row["change_type"] == "added" else row["before_fingerprint"]
        by_payload.setdefault((row["comparison_id"], fingerprint), []).append(row)
    predecessors: dict[str, str] = {}
    successors: dict[str, str] = {}
    for candidates in by_payload.values():
        deleted = [row for row in candidates if row["change_type"] == "deleted"]
        added = [row for row in candidates if row["change_type"] == "added"]
        if len(deleted) == len(added) == 1:
            predecessors[added[0]["stable_diff_id"]] = deleted[0]["stable_diff_id"]
            successors[deleted[0]["stable_diff_id"]] = added[0]["stable_diff_id"]
    id_map = [{
        "stable_diff_id": row["stable_diff_id"], "comparison_id": row["comparison_id"], "change_type": row["change_type"],
        "path": row["path"], "active": "true",
        "first_seen_generation": previous.get(row["stable_diff_id"], {}).get("first_seen_generation", source_id),
        "last_seen_generation": source_id, "predecessor_diff_id": predecessors.get(row["stable_diff_id"], ""),
        "successor_diff_id": successors.get(row["stable_diff_id"], ""),
        "content_fingerprint": row["content_fingerprint"], "notes": "",
    } for row in inventory]
    active_ids = {row["stable_diff_id"] for row in inventory}
    id_map.extend({**row, "active": "false"} for identifier, row in previous.items() if identifier not in active_ids)
    id_map.sort(key=lambda row: (row["comparison_id"], row["path"], row["change_type"], row["stable_diff_id"]))
    target_by_path = {row["path"]: row for row in inventory if row["after_role"] == "next_vendor" and row["object_kind"] != "extension_intervention"}
    target_by_key = {row["intervention_key"]: row for row in details if row["after_role"] == "next_vendor"}
    coverage: list[dict[str, str]] = []
    for row in inventory:
        if row["after_role"] != "target_cf":
            continue
        if row["object_kind"] == "extension_intervention":
            detail = detail_by_id[row["stable_diff_id"]]
            target = target_by_key.get(detail["intervention_key"])
            result = semantic_target_coverage(
                detail,
                [candidate for candidate in details if candidate["after_role"] == "next_vendor"],
            )
            status = result["coverage"]
            target_ids = result["target_stable_diff_id"]
            evidence = {"customer": detail["after_structural_fingerprint"], "target": target["after_structural_fingerprint"] if target else detail["before_structural_fingerprint"]}
        else:
            target = target_by_path.get(row["path"])
            status = "covered_by_vendor" if target and target["change_type"] == row["change_type"] and target["after_fingerprint"] == row["after_fingerprint"] else "still_required" if target is None else "changed_in_target"
            target_ids = target["stable_diff_id"] if target else ""
            evidence = {"baseline": row["before_fingerprint"], "customer": row["after_fingerprint"], "target": target["after_fingerprint"] if target else row["before_fingerprint"]}
        coverage.append({
            "customer_diff_id": row["stable_diff_id"], "source_generation": source_id, "target_diff_ids": target_ids,
            "coverage_status": status, "evidence_ref": canonical_json(evidence).decode(), "notes": "",
        })
    staging_parent = repo / "analysis/indexes/.staging"; staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=staging_parent) as temporary:
        staging = Path(temporary)
        _write_csv(staging / "diff-inventory.csv", INVENTORY_HEADER, inventory)
        _write_csv(staging / "diff-id-map.csv", ID_MAP_HEADER, id_map)
        _write_csv(staging / "target-coverage.csv", COVERAGE_HEADER, coverage)
        _write_jsonl(staging / "extension-diff.jsonl", details)
        _write_jsonl(staging / "extension-dependencies.jsonl", dependencies)
        _write_csv(staging / "extension-physical-diff.csv", INVENTORY_HEADER, physical)
        _write_csv(staging / "extension-path-coverage.csv", PATH_COVERAGE_HEADER, path_coverage)
        data_files = {name: sha256((staging / name).read_bytes()) for name in ANALYZER_FILES if name != "extension-analyzer-manifest.json"}
        manifest = {
            "schema_version": "2",
            "extension_analyzer_version": analysis["extension_analyzer_version"],
            "source_generation_id": source_id,
            "source_comparison_epoch_fingerprint": _string(source_pointer.get("source_comparison_epoch_fingerprint")),
            "routing_manifest_fingerprint": _string(source_pointer.get("routing_manifest_fingerprint")),
            "extension_adapter_versions": sorted(analysis["adapter_versions"]),
            "component_bindings": sorted(analysis["component_bindings"], key=canonical_json),
            "record_counts": {
                "extension_diff": len(details), "extension_dependencies": len(dependencies),
                "extension_physical_diff": len(physical), "extension_path_coverage": len(path_coverage),
            },
            "data_files": data_files,
        }
        manifest["output_fingerprint"] = "sha256:" + sha256(canonical_json(manifest))
        _ = (staging / "extension-analyzer-manifest.json").write_bytes(canonical_json(manifest) + b"\n")
        for name, header in (
            ("diff-inventory.csv", INVENTORY_HEADER),
            ("diff-id-map.csv", ID_MAP_HEADER),
            ("target-coverage.csv", COVERAGE_HEADER),
            ("extension-physical-diff.csv", INVENTORY_HEADER),
            ("extension-path-coverage.csv", PATH_COVERAGE_HEADER),
        ):
            _ = read_csv(staging / name, header)
        _ = _read_jsonl(staging / "extension-diff.jsonl", DETAIL_KEYS)
        _ = _read_jsonl(staging / "extension-dependencies.jsonl", DEPENDENCY_KEYS)
        if set(manifest) != MANIFEST_KEYS or (staging / "extension-analyzer-manifest.json").read_bytes() != canonical_json(manifest) + b"\n":
            raise ValueError("invalid staged extension analyzer manifest")
        hashes = {name: sha256((staging / name).read_bytes()) for name in VERSION_FILES["2"]}
        generation_id = sha256(canonical_json({"files": hashes, "schema_version": "2", "source_generation_id": source_id}))
        destination = repo / "analysis/indexes/generations" / generation_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            os.replace(staging, destination)
            parent_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        if any(sha256((destination / name).read_bytes()) != digest for name, digest in hashes.items()):
            raise RuntimeError("existing diff generation does not match its deterministic identity")
        pointer: DiffPointer = {
            "schema_version": "2", "generation_id": generation_id, "source_generation_id": source_id, "files": hashes,
            "row_counts": {
                "diff-inventory.csv": len(inventory), "diff-id-map.csv": len(id_map), "target-coverage.csv": len(coverage),
                "extension-diff.jsonl": len(details), "extension-dependencies.jsonl": len(dependencies),
                "extension-physical-diff.csv": len(physical), "extension-path-coverage.csv": len(path_coverage),
            },
        }
        current_source = parse_json_object((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
        if activate and current_source != source_pointer:
            raise RuntimeError("stale source generation before diff publication")
        _ = validate_active(repo, candidate=pointer, source_candidate=source_pointer)
        if activate:
            atomic_json(pointer_path, pointer)
            canonical_pointer = repo / "research/active-generation.json"
            if not canonical_pointer.exists():
                atomic_json(canonical_pointer, {
                    "schema_version": "1",
                    "canonical_generation_id": None,
                    "source_generation_id": source_id,
                    "diff_generation_id": generation_id,
                })
        return pointer


def _build_routed(repo: Path, source_pointer: SourcePointer, cancelled: Callable[[], bool] | None = None, *, activate: bool = True) -> DiffPointer:
    from .extension_analyzer import analyze_role_union, build_main_config_index, comparison_id_v2

    source_id = _string(source_pointer.get("generation_id"))
    source_root = repo / "sources/generations" / source_id
    manifest = _routing_manifest(parse_json_object((source_root / _string(source_pointer.get("routing_manifest_path"))).read_text(encoding="utf-8")))
    epoch = _string(source_pointer.get("source_comparison_epoch_fingerprint"))
    components = {item["component_id"]: item for item in source_pointer.get("components", [])}
    inventory: list[dict[str, str]] = []
    raw_extensions: list[dict[str, str]] = []
    role_components: dict[str, dict[str, Path | tuple[Path, str]]] = {role: {} for role in ("vendor_baseline", "target_cf", "next_vendor")}
    main_config_indexes: dict[str, dict[str, list[MainMatch]]] = {}
    for group in manifest["groups"]:
        if cancelled and cancelled():
            raise InterruptedError("diff build cancelled during component binding")
        by_role = {member["role"]: components[member["component_id"]] for member in group["members"]}
        if group["routing_group_id"] == "configuration":
            for role, component in by_role.items():
                main_config_indexes[role] = build_main_config_index(source_root / component["path"], cancelled)
        elif group["routing_group_id"].startswith("extension:"):
            uuid = group["routing_group_id"].split(":", 1)[1].lower()
            for role, component in by_role.items():
                role_components[role][uuid] = (source_root / component["path"], group["representation_schema"])
    empty_parent = repo / "analysis/indexes/.staging"
    empty_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=empty_parent) as empty:
        empty_root = Path(empty)
        for kind, before, after in COMPARISONS:
            cmp_id = comparison_id_v2(kind, before, after, epoch)
            for group in manifest["groups"]:
                if cancelled and cancelled():
                    raise InterruptedError("diff build cancelled during physical comparison")
                by_role = {member["role"]: components[member["component_id"]] for member in group["members"]}
                prefix = "configuration" if group["routing_group_id"] == "configuration" else group["routing_group_id"].replace("extension:", "extensions/", 1).replace("external:", "external/", 1)
                before_root = source_root / by_role[before]["path"] if before in by_role else empty_root
                after_root = source_root / by_role[after]["path"] if after in by_role else empty_root
                rows = compare(before_root, after_root, {
                    "comparison_kind": kind,
                    "before_role": before,
                    "after_role": after,
                    "acquisition_profile_id": _string(source_pointer.get("acquisition_profile_id")),
                    "representation_schema": group["representation_schema"],
                    "normalizer_version": _string(source_pointer.get("normalizer_version")),
                    "source_generation": source_id,
                    "comparison_id": cmp_id,
                    "path_prefix": prefix,
                    "schema_version": "2",
                    "include_component_manifest": "true" if group["routing_group_id"].startswith("extension:") else "",
                })
                if group["routing_group_id"].startswith("extension:"):
                    raw_extensions.extend(rows)
                else:
                    inventory.extend(rows)
    analysis = _analyzer_result(analyze_role_union(
        role_components,
        epoch,
        source_generation_id=source_id,
        main_config_indexes=main_config_indexes,
        raw_rows=raw_extensions,
        cancelled=cancelled,
    ))
    pointer_components = {item["component_id"]: item for item in source_pointer.get("components", [])}
    for binding in analysis["component_bindings"]:
        component = pointer_components.get(binding["component_id"])
        if not component or component["path"] != binding["component_path"]:
            raise ValueError(f"extension component binding mismatch: {binding['component_id']}")
        binding["component_fingerprint"] = component["fingerprint"]
    for row in analysis["semantic_rows"]:
        row["path"] = f"extensions/{row['area'].split('/', 1)[1]}/{row['path']}"
    inventory.extend(analysis["semantic_rows"])
    if cancelled and cancelled():
        raise InterruptedError("diff build cancelled before publication")
    return _publish_routed_inventory(repo, source_pointer, inventory, analysis, activate=activate)


_ = _publish_inventory
