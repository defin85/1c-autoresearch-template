from __future__ import annotations

import json
import os
import selectors
import subprocess
import shutil
import sys
import tempfile
import time
import uuid
from functools import lru_cache
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from .platform_support import lock_file, sync_directory, terminate_process
from collections.abc import Iterator
from typing import Callable, NotRequired, Protocol, TypedDict, runtime_checkable

from .contracts import (
    JsonValue, ROLES, atomic_json, canonical_json, confined, file_manifest,
    normalize_relative, parse_json_object, repository_lock, sha256,
)
from .search_runtime import Closable

RLM_CAPABILITIES = ("code-search-lexical",)
BACKEND_CATALOG = {
    "rlm-tools-bsl": {
        "executable": "rlm-bsl-index",
        "adapter_version": "rlm-index/v1",
        "representations": ("xml-hierarchical", "v8unpack", "edt-project"),
    },
    "bsl-analyzer": {
        "executable": "bsl-analyzer",
        "adapter_version": "bsl-analyzer-workspace/v1",
        "representations": ("xml-hierarchical", "v8unpack", "edt-project"),
    },
}
ADAPTER_OUTPUT_LIMIT = 256 * 1024
ADAPTER_STATE_LIMIT = 16 * 1024 * 1024
ADAPTER_INDEX_LIMIT = 8 * 1024 * 1024 * 1024
PROJECT_STORAGE_LIMIT = 32 * 1024 * 1024 * 1024
ADAPTER_TIMEOUT_SECONDS = 120
BSL_SEARCH_SURFACE_V1: dict[str, dict[str, dict[str, set[str]]]] = {
    "workspace": {
        "allowed": {
            "search": {"search_code", "status"},
            "symbol_info": set(),
            "graph": {
                "overview", "schema", "status", "node", "source",
                "neighbors", "callers", "callees", "resolve",
            },
            "metadata": {"info", "tree", "object", "form", "status"},
            "diagnostics": {"catalog", "schema", "status", "file", "workspace"},
        },
        "denied": {
            "query": {"validate", "execute", "schema"},
            "execute": {"check", "run", "eval"},
            "event_log": set(),
            "debug": {
                "attach", "disconnect", "set_breakpoint", "remove_breakpoint",
                "continue", "step", "wait_stop", "stack_trace", "locals", "eval",
            },
        },
    },
    "reference": {
        "allowed": {
            "search": {"find_docs", "search_docs", "status"},
            "syntax_help": set(),
            "its_help": set(),
        },
        "denied": {},
    },
}
BSL_SYNTAX_OUTPUT_SCHEMA_FINGERPRINT = (
    "blake3:77ab89c5868110d089b431d81c0ec5c3f1cbb4d755e1396f963ab4bbff72fa8e"
)
COMPLETE_SEARCH_CAPABILITIES = (
    "code-search-lexical", "code-search-hybrid",
    "symbol-info", "symbol-info-positional",
    "graph-overview", "graph-schema", "graph-resolve", "graph-node",
    "graph-source", "graph-neighbors", "graph-callers", "graph-callees",
    "metadata-info", "metadata-tree", "metadata-object", "metadata-form",
    "diagnostics-catalog", "diagnostics-schema", "diagnostics-file",
    "diagnostics-workspace", "reference-docs-find", "reference-docs-search",
    "reference-syntax-help", "reference-its-help",
)
COMPLETE_SEARCH_OPERATIONS = {
    "code.search_lexical", "code.search_hybrid", "symbol.info", "symbol.info_at",
    *{
        f"{family}.{name}"
        for family, names in {
            "graph": (
                "overview", "schema", "resolve", "node", "source", "neighbors",
                "callers", "callees",
            ),
            "metadata": ("info", "tree", "object", "form"),
            "diagnostics": ("catalog", "schema", "file", "workspace"),
            "reference": ("find_docs", "search_docs", "syntax_help", "its_help"),
        }.items()
        for name in names
    },
}
SCHEMA3_SERVICE_PROFILES = ("lexical", "hybrid")


class BackendRow(TypedDict):
    adapter_id: str
    engine_version: str


class IndexConfig(TypedDict):
    schema_version: str
    machine_contract_version: str
    backends: list[BackendRow]
    routes: dict[str, list[str]]
    service_profiles: dict[str, str]


class ConfigCandidate(TypedDict, total=False):
    schema_version: str
    machine_contract_version: str
    backends: list[BackendRow]
    routes: dict[str, list[str]]
    service_profiles: dict[str, str]


class ConfigPlan(TypedDict):
    schema_version: str
    target_schema_version: str
    current_file_fingerprint: str
    normalized_file_fingerprint: str
    normalized_file: str
    affected_backends: list[str]
    affected_capabilities: list[str]
    rebuild_backends: list[str]
    degraded_routes: list[str]
    reuse_invalidated: bool
    plan_fingerprint: str


class NativeTextEnvelope(TypedDict):
    schema_version: str
    text: str
    returned_bytes: int


class Component(TypedDict):
    component_id: str
    path: str
    fingerprint: str
    representation: str
    source_generation_id: str
    engine: str
    engine_version: str
    bsl_file_count: int


class TargetIdentity(TypedDict):
    adapter_id: str
    adapter_version: str
    engine_version: str
    repository_instance_fingerprint: str
    component_id: str
    component_relative_path: str
    representation: str
    component_fingerprint: str
    source_generation_id: str
    capability_fingerprint: str
    modality: NotRequired[str]
    embedding_identity: NotRequired[str]


class PromotedIdentity(TargetIdentity):
    target_fingerprint: str
    index_manifest_fingerprint: str
    index_fingerprint: str


class BackendProbe(TypedDict):
    available: bool
    capabilities: list[str]
    executable: NotRequired[str]
    engine_version: NotRequired[str]
    contract_version: NotRequired[str]
    configured_contract_version: NotRequired[str]
    executable_fingerprint: NotRequired[str]
    capability_fingerprint: NotRequired[str]
    surface_manifest: NotRequired[dict[str, JsonValue]]
    failure_code: NotRequired[str]
    failure_summary: NotRequired[str]


class BackendState(TypedDict, total=False):
    adapter_id: str
    component_id: str
    status: str
    modality: str
    capabilities: list[str]
    adapter_version: str
    capability_fingerprint: str
    index_fingerprint: str
    target_fingerprint: str
    last_validation: str | None
    contract_version: str
    index_key: str
    instance_path: str
    index_dir: str
    legacy_adopted: bool
    embedding_identity: str
    reference_identity: str
    readiness_reason: str | None
    recovery_action: str | None
    validated_at: str


class BackendDecision(TypedDict):
    capability: str
    preferred_backend_id: str
    selected_backend_id: str
    fallback: bool
    fallback_reason: str | None
    skipped: list[dict[str, str]]
    state: BackendState
    route_fingerprint: str


class CoverageBlocker(TypedDict):
    component_id: str
    capability: str
    backend_ids: list[str]


class CoverageDegraded(TypedDict):
    component_id: str
    capability: str
    selected_backend_id: str
    fallback_reason: str | None
    skipped: list[dict[str, str]]


class Coverage(TypedDict):
    blockers: list[CoverageBlocker]
    degraded: list[CoverageDegraded]


class RawHit(TypedDict, total=False):
    component_relative_path: str
    line: int
    symbol: str
    kind: str
    rank: str


class NormalizedHit(TypedDict):
    backend_id: str
    adapter_version: str
    index_fingerprint: str
    component_id: str
    source_generation_id: str
    component_relative_path: str
    kind: str
    rank: str
    line: NotRequired[int]
    symbol: NotRequired[str]


class CliProbe(TypedDict):
    ready: bool
    exit_code: int
    output: str


@runtime_checkable
class ProcessInputStream(Protocol):
    def write(self, value: str, /) -> int: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class ProcessOutputStream(Protocol):
    def readline(self) -> str: ...


@runtime_checkable
class FileDescriptorStream(Protocol):
    def fileno(self) -> int: ...


def _process_input_stream(value: object) -> ProcessInputStream:
    if not isinstance(value, ProcessInputStream):
        raise RuntimeError("bsl-analyzer broker proxy stdio is unavailable")
    return value


def _process_output_stream(value: object) -> ProcessOutputStream:
    if not isinstance(value, ProcessOutputStream):
        raise RuntimeError("bsl-analyzer broker proxy stdio is unavailable")
    return value


class BuildOutcome(TypedDict, total=False):
    ready: bool
    exit_code: int
    output: str
    error: str


def _build_outcome(value: object) -> BuildOutcome | None:
    raw_value: object = value
    if not isinstance(value, dict):
        return None
    row = parse_json_object(canonical_json(raw_value).decode())
    result: BuildOutcome = {}
    ready = row.get("ready")
    if isinstance(ready, bool):
        result["ready"] = ready
    exit_code = row.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        result["exit_code"] = exit_code
    for key in ("output", "error"):
        item = row.get(key)
        if isinstance(item, str):
            result[key] = item
    return result


class LegacyStatus(Component):
    index_key: str
    status: str
    last_validation: str | None
    result: BuildOutcome | None


class ToolInstance(TypedDict):
    version: str
    configured_version: NotRequired[str]
    contract_version: NotRequired[str]
    configured_contract_version: NotRequired[str]
    status: str
    path: str
    reason_code: str


class ToolInventory(TypedDict):
    tool_id: str
    status: str
    purpose: str
    required: bool
    route_capabilities: list[str]
    instances: list[ToolInstance]


class GcCandidate(TypedDict):
    target: str
    instance: str
    bytes: int


class GcPlan(TypedDict):
    schema_version: str
    candidates: list[GcCandidate]
    reclaimed_bytes: int
    plan_fingerprint: str


class PointerComponent(TypedDict, total=False):
    component_id: str
    path: str
    kind: str
    representation_schema: str
    fingerprint: str
    bsl_file_count: int


class SourcePointer(TypedDict, total=False):
    schema_version: str
    generation_id: str
    representation_schema: str
    components: list[PointerComponent]


def _source_pointer(path: Path) -> SourcePointer:
    value = parse_json_object(path.read_text(encoding="utf-8"))
    generation_id = value.get("generation_id")
    if not isinstance(generation_id, str):
        raise ValueError("active source generation is invalid")
    components = value.get("components", [])
    if not isinstance(components, list):
        raise ValueError("active source components are invalid")
    result: SourcePointer = {"generation_id": generation_id}
    for key in ("schema_version", "representation_schema"):
        item = value.get(key)
        if isinstance(item, str):
            result[key] = item
    result["components"] = []
    for item in components:
        if not isinstance(item, dict):
            raise ValueError("active source component is invalid")
        component: PointerComponent = {}
        for key in ("component_id", "path", "kind", "representation_schema", "fingerprint"):
            field = item.get(key)
            if isinstance(field, str):
                component[key] = field
        bsl_file_count = item.get("bsl_file_count")
        if isinstance(bsl_file_count, int) and not isinstance(bsl_file_count, bool):
            component["bsl_file_count"] = bsl_file_count
        result["components"].append(component)
    return result


class ContractAction(TypedDict):
    name: str


class ContractTool(TypedDict):
    name: str
    actions: NotRequired[list[ContractAction]]
    output_schema_version: NotRequired[str]
    output_schema_fingerprint: NotRequired[str]


class ContractProfile(TypedDict):
    tools: list[ContractTool]


class BslContract(TypedDict):
    contract_version: str
    build_version: str
    mcp: dict[str, dict[str, ContractProfile]]
    transports: NotRequired[dict[str, dict[str, dict[str, JsonValue]]]]


def _toml_json(path: Path) -> dict[str, JsonValue]:
    with path.open("rb") as stream:
        return _json_object(tomllib.load(stream))


def _json_object(value: object) -> dict[str, JsonValue]:
    return parse_json_object(canonical_json(value).decode())


def _index_config(candidate: ConfigCandidate) -> IndexConfig:
    backends: list[BackendRow] = [
        {"adapter_id": row["adapter_id"], "engine_version": row["engine_version"]}
        for row in candidate.get("backends", [])
    ]
    routes = {name: list(values) for name, values in candidate.get("routes", {}).items()}
    result: IndexConfig = {
        "schema_version": "3",
        "machine_contract_version": "1.3",
        "backends": backends,
        "routes": routes,
        "service_profiles": dict(candidate.get("service_profiles", {})),
    }
    return result


class BslSurfaceContractError(RuntimeError):
    def __init__(
        self, state: str, summary: str, *,
        expected_contract_version: str = "", actual_contract_version: str = "",
    ):
        super().__init__(summary)
        self.state: str = state
        self.expected_contract_version: str = expected_contract_version
        self.actual_contract_version: str = actual_contract_version


def native_text_envelope(
    result: dict[str, JsonValue], *, max_bytes: int = ADAPTER_OUTPUT_LIMIT
) -> NativeTextEnvelope:
    content = result.get("content")
    if (
        result.get("isError")
        or "structuredContent" in result
        or not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or content[0].get("type") != "text"
        or not isinstance(content[0].get("text"), str)
    ):
        raise RuntimeError("bsl-analyzer returned an invalid text-only response")
    text = content[0]["text"]
    assert isinstance(text, str)
    if len(text.encode()) > max_bytes:
        raise RuntimeError("bsl-analyzer text-only response exceeds the byte limit")
    return {
        "schema_version": "native-text-envelope/v1",
        "text": text,
        "returned_bytes": len(text.encode()),
    }


def _backend_environment(
    private_home: Path, index_dir: Path | None = None
) -> dict[str, str]:
    private_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C.UTF-8",
        "HOME": str(private_home),
        "XDG_CONFIG_HOME": str(private_home / "config"),
        "XDG_CACHE_HOME": str(private_home / "cache"),
        "XDG_DATA_HOME": str(private_home / "data"),
        "XDG_STATE_HOME": str(private_home / "state"),
    }
    if index_dir is not None:
        index_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        environment["RLM_INDEX_DIR"] = str(index_dir)
    return environment


def _bounded_atomic_json(path: Path, value: object) -> None:
    encoded = canonical_json(value)
    if len(encoded) > ADAPTER_STATE_LIMIT:
        raise RuntimeError("index adapter state exceeds the byte limit")
    atomic_json(path, parse_json_object(encoded.decode()))


def _tree_size(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("index storage symlink is forbidden")
        if path.is_file():
            total += path.stat().st_size
    return total


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ValueError("index storage symlink is forbidden")
        if path.is_dir():
            sync_directory(path)
            continue
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    sync_directory(root)


def _fsync_directory(path: Path) -> None:
    sync_directory(path)


def repository_instance_fingerprint(repo: Path) -> str:
    project = _toml_json(repo / "project.toml").get("project")
    if not isinstance(project, dict):
        raise ValueError("project.toml project table is missing")
    project_id = project.get("id")
    return "sha256:" + sha256(canonical_json({
        "project_id": str(project_id),
        "repository_root": str(repo.resolve()),
    }))


def load_config(repo: Path, required_capabilities: tuple[str, ...] = ()) -> IndexConfig:
    raw = _toml_json(repo / "research/indexing.toml")
    expected = {
        "schema_version", "machine_contract_version", "backends", "routes",
        "service_profiles",
    }
    if raw.get("schema_version") != "3" or set(raw) != expected:
        raise ValueError("indexing.schema3_required")
    if raw["machine_contract_version"] != "1.3":
        raise ValueError("indexing.schema3_required")
    backend_rows = raw.get("backends")
    routes_raw = raw.get("routes")
    if not isinstance(backend_rows, list) or not backend_rows or not isinstance(routes_raw, dict):
        raise ValueError("indexing backends and routes are required")
    backends: list[BackendRow] = []
    identifiers: list[str] = []
    for row_value in backend_rows:
        row = row_value
        if not isinstance(row, dict) or set(row) != {"adapter_id", "engine_version"}:
            raise ValueError("invalid indexing backend")
        adapter_id = str(row["adapter_id"])
        engine_version = str(row["engine_version"])
        if adapter_id not in BACKEND_CATALOG or not engine_version.strip():
            raise ValueError("unknown or unversioned indexing backend")
        identifiers.append(adapter_id)
        backends.append({"adapter_id": adapter_id, "engine_version": engine_version})
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate indexing backend")
    routes: dict[str, list[str]] = {}
    for capability, members in routes_raw.items():
        if capability not in COMPLETE_SEARCH_CAPABILITIES:
            raise ValueError("unknown indexing capability")
        if (
            not isinstance(members, list)
            or not members
            or any(not isinstance(item, str) for item in members)
            or len(members) != len(set(members))
            or set(members) - set(identifiers)
        ):
            raise ValueError("invalid indexing capability route")
        routes[capability] = [item for item in members if isinstance(item, str)]
    missing = set(COMPLETE_SEARCH_CAPABILITIES) - set(routes)
    if missing:
        raise ValueError("indexing.schema3_required")
    missing = set(required_capabilities) - set(routes)
    if missing:
        raise ValueError(f"missing required indexing routes: {sorted(missing)}")
    profiles_value = raw["service_profiles"]
    if (
        not isinstance(profiles_value, dict)
        or set(profiles_value) != set(SCHEMA3_SERVICE_PROFILES)
        or any(
            not isinstance(profiles_value.get(name), str) or not str(profiles_value.get(name)).strip()
            for name in SCHEMA3_SERVICE_PROFILES
        )
        or len(set(str(value) for value in profiles_value.values())) != len(profiles_value)
    ):
        raise ValueError("indexing.schema3_required")
    return {
        "schema_version": "3",
        "machine_contract_version": "1.3",
        "backends": backends,
        "routes": routes,
        "service_profiles": {
            name: str(profiles_value[name]) for name in SCHEMA3_SERVICE_PROFILES
        },
    }


def serialize_config(config: IndexConfig) -> bytes:
    if config.get("schema_version") != "3" or config.get("machine_contract_version") != "1.3":
        raise ValueError("indexing.schema3_required")
    if set(config["routes"]) != set(COMPLETE_SEARCH_CAPABILITIES):
        raise ValueError("indexing.schema3_required")
    lines = ['schema_version = "3"', 'machine_contract_version = "1.3"']
    lines.append("")
    for backend in config["backends"]:
        lines.extend((
            "[[backends]]",
            f'adapter_id = {json.dumps(backend["adapter_id"])}',
            f'engine_version = {json.dumps(backend["engine_version"])}',
            "",
        ))
    lines.append("[routes]")
    for capability in COMPLETE_SEARCH_CAPABILITIES:
        values = ", ".join(json.dumps(item) for item in config["routes"][capability])
        lines.append(f'{json.dumps(capability)} = [{values}]')
    lines.extend(("", "[service_profiles]"))
    for name in SCHEMA3_SERVICE_PROFILES:
        lines.append(f"{name} = {json.dumps(config['service_profiles'][name])}")
    return ("\n".join(lines) + "\n").encode()


def _preview_candidate(
    _repo: Path,
    candidate: ConfigCandidate,
    required_capabilities: tuple[str, ...],
) -> tuple[IndexConfig, bytes, str]:
    if candidate.get("schema_version") != "3":
        raise ValueError("indexing.schema3_required")
    with tempfile.TemporaryDirectory() as temporary:
        shadow = Path(temporary)
        (shadow / "research").mkdir()
        normalized_candidate = _index_config(candidate)
        _ = (shadow / "research/indexing.toml").write_bytes(serialize_config(normalized_candidate))
        normalized = load_config(shadow, required_capabilities)
    return normalized, serialize_config(normalized), "3"


def config_fingerprint(repo: Path) -> str:
    return "sha256:" + sha256((repo / "research/indexing.toml").read_bytes())


def preview_config(
    repo: Path,
    candidate: ConfigCandidate,
    expected_file_fingerprint: str,
    required_capabilities: tuple[str, ...] = (),
) -> ConfigPlan:
    if expected_file_fingerprint != config_fingerprint(repo):
        raise RuntimeError("stale indexing configuration fingerprint")
    normalized, encoded, target_schema_version = _preview_candidate(
        repo,
        candidate,
        required_capabilities,
    )
    current = load_config(repo)
    current_backend_ids = {item["adapter_id"] for item in current["backends"]}
    candidate_backend_ids = {item["adapter_id"] for item in normalized["backends"]}
    affected = sorted(current_backend_ids | candidate_backend_ids)
    plan: ConfigPlan = {
        "schema_version": "indexing-configuration-plan/v1",
        "target_schema_version": target_schema_version,
        "current_file_fingerprint": expected_file_fingerprint,
        "normalized_file_fingerprint": "sha256:" + sha256(encoded),
        "normalized_file": encoded.decode(),
        "affected_backends": affected,
        "affected_capabilities": sorted(set(current["routes"]) | set(normalized["routes"])),
        "rebuild_backends": sorted(
            adapter_id for adapter_id in affected
            if next((item for item in current["backends"] if item["adapter_id"] == adapter_id), None)
            != next((item for item in normalized["backends"] if item["adapter_id"] == adapter_id), None)
        ),
        "degraded_routes": sorted(
            capability
            for capability in set(current["routes"]) | set(normalized["routes"])
            if current["routes"].get(capability) != normalized["routes"].get(capability)
        ),
        "reuse_invalidated": current != normalized,
        "plan_fingerprint": "",
    }
    plan["plan_fingerprint"] = "sha256:" + sha256(canonical_json({key: value for key, value in plan.items() if key != "plan_fingerprint"}))
    return plan


def apply_config(
    repo: Path,
    candidate: ConfigCandidate,
    expected_file_fingerprint: str,
    expected_plan_fingerprint: str,
    required_capabilities: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    plan = preview_config(repo, candidate, expected_file_fingerprint, required_capabilities)
    if plan["plan_fingerprint"] != expected_plan_fingerprint:
        raise RuntimeError("stale indexing configuration plan")
    from .contracts import atomic_bytes
    atomic_bytes(repo / "research/indexing.toml", plan["normalized_file"].encode())
    return {
        "operation": "indexes.configure",
        "configuration_fingerprint": config_fingerprint(repo),
        "plan_fingerprint": expected_plan_fingerprint,
        "rebuild_backends": plan["rebuild_backends"],
        "reuse_invalidated": plan["reuse_invalidated"],
        "build_started": False,
    }


def capability_fingerprint(
    adapter_id: str,
    engine_version: str,
    capabilities: list[str],
    executable_fingerprint: str = "",
) -> str:
    return "sha256:" + sha256(canonical_json({
        "adapter_id": adapter_id,
        "adapter_version": BACKEND_CATALOG[adapter_id]["adapter_version"],
        "engine_version": engine_version,
        "executable_fingerprint": executable_fingerprint,
        "capabilities": sorted(capabilities),
        "normalization_schema": "source-navigation-hit/v1",
    }))


def target_identity(
    repo: Path,
    component: Component,
    backend: BackendRow,
    capabilities: list[str],
    executable_fingerprint: str = "",
    modality: str = "",
    embedding_identity: str = "",
) -> TargetIdentity:
    adapter_id = backend["adapter_id"]
    identity: TargetIdentity = {
        "adapter_id": adapter_id,
        "adapter_version": str(BACKEND_CATALOG[adapter_id]["adapter_version"]),
        "engine_version": backend["engine_version"],
        "repository_instance_fingerprint": repository_instance_fingerprint(repo),
        "component_id": component["component_id"],
        "component_relative_path": component["path"],
        "representation": component["representation"],
        "component_fingerprint": component["fingerprint"],
        "source_generation_id": component["source_generation_id"],
        "capability_fingerprint": capability_fingerprint(
            adapter_id,
            backend["engine_version"],
            capabilities,
            executable_fingerprint,
        ),
    }
    if modality:
        identity["modality"] = modality
    if embedding_identity:
        identity["embedding_identity"] = embedding_identity
    return identity


def _index_profile_identity(
    repo: Path, backend: BackendRow, modality: str
) -> str:
    config = load_config(repo)
    if backend["adapter_id"] != "bsl-analyzer":
        return ""
    if modality == "lexical":
        return ""
    if modality != "hybrid":
        raise ValueError("invalid BSL Analyzer index modality")
    from . import search_services
    profiles = config["service_profiles"]
    profile_id = str(profiles["hybrid"])
    profile, _secret = search_services.private_profile(repo, profile_id, None)
    if profile.get("kind") != "embedding" or not profile.get("enabled"):
        raise RuntimeError("search_services.hybrid_unavailable")
    semantic_identity = profile.get("semantic_identity")
    if not isinstance(semantic_identity, str):
        raise RuntimeError("search_services.hybrid_unavailable")
    return semantic_identity


def target_fingerprint(identity: TargetIdentity) -> str:
    return "sha256:" + sha256(canonical_json(identity))


def promoted_identity(identity: TargetIdentity, index_manifest: list[dict[str, str | int]]) -> PromotedIdentity:
    manifest_fingerprint = "sha256:" + sha256(canonical_json(index_manifest))
    return {
        **identity,
        "target_fingerprint": target_fingerprint(identity),
        "index_manifest_fingerprint": manifest_fingerprint,
        "index_fingerprint": "sha256:" + sha256(canonical_json({
            "target": target_fingerprint(identity),
            "manifest": manifest_fingerprint,
        })),
    }


def select_backend(
    config: IndexConfig,
    capability: str,
    component: Component,
    states: list[BackendState],
) -> BackendDecision:
    if capability not in COMPLETE_SEARCH_CAPABILITIES:
        raise ValueError("unknown indexing capability")
    route = config["routes"].get(capability)
    if not route:
        raise RuntimeError("source_search.capability_unconfigured")
    matching_states = [
        item for item in states
        if item.get("component_id") == component["component_id"]
    ]
    skipped: list[dict[str, str]] = []
    for adapter_id in route:
        adapter_states = [
            item for item in matching_states
            if item.get("adapter_id") == adapter_id
        ]
        candidates = [
            item for item in adapter_states
            if capability in item.get("capabilities", [])
        ]
        state = next(
            (item for item in candidates if item.get("status") == "ready"),
            candidates[0] if candidates else None,
        )
        if state is None:
            if adapter_states and any(
                item.get("status") in {"missing", "stale", "failed", "unavailable"}
                for item in adapter_states
            ):
                skipped.append({"adapter_id": adapter_id, "reason": "index_not_ready"})
            else:
                skipped.append({"adapter_id": adapter_id, "reason": "capability_unsupported" if adapter_states else "backend_unavailable"})
            continue
        if state.get("status") in {"missing", "stale", "failed", "unavailable"}:
            skipped.append({"adapter_id": adapter_id, "reason": "index_not_ready"})
            continue
        representation = str(component["representation"]).split("/", 1)[0]
        if representation not in BACKEND_CATALOG[adapter_id]["representations"]:
            skipped.append({"adapter_id": adapter_id, "reason": "representation_unsupported"})
            continue
        if state.get("status") == "ready":
            decision: BackendDecision = {
                "capability": capability,
                "preferred_backend_id": route[0],
                "selected_backend_id": adapter_id,
                "fallback": adapter_id != route[0],
                "fallback_reason": skipped[0]["reason"] if skipped else None,
                "skipped": skipped,
                "state": state,
                "route_fingerprint": "",
            }
            decision["route_fingerprint"] = "sha256:" + sha256(canonical_json({
                "capability": capability,
                "route": route,
                "selected_backend_id": adapter_id,
                "fallback_reason": decision["fallback_reason"],
                "skipped": skipped,
                "adapter_version": state.get("adapter_version"),
                "capability_fingerprint": state.get("capability_fingerprint"),
                "index_fingerprint": state.get("index_fingerprint"),
            }))
            return decision
    raise RuntimeError("source_search.route_unavailable")


def route_coverage(
    config: IndexConfig,
    components: list[Component],
    states: list[BackendState],
) -> Coverage:
    blockers: list[CoverageBlocker] = []
    degraded: list[CoverageDegraded] = []
    by_component = {
        component["component_id"]: component
        for component in components
        if component["bsl_file_count"] > 0
    }
    for component_id, component in by_component.items():
        for capability, route in config["routes"].items():
            try:
                decision = select_backend(config, capability, component, states)
            except RuntimeError:
                blockers.append({
                    "component_id": component_id,
                    "capability": capability,
                    "backend_ids": list(route),
                })
                continue
            if decision["fallback"]:
                degraded.append({
                    "component_id": component_id,
                    "capability": capability,
                    "selected_backend_id": decision["selected_backend_id"],
                    "fallback_reason": decision["fallback_reason"],
                    "skipped": decision["skipped"],
                })
    return {"blockers": blockers, "degraded": degraded}


def normalize_hit(raw: RawHit, decision: BackendDecision, component: Component) -> NormalizedHit:
    allowed = {"component_relative_path", "line", "symbol", "kind", "rank"}
    if set(raw) - allowed:
        raise ValueError("invalid backend search hit")
    relative = normalize_relative(str(raw.get("component_relative_path", "")))
    if Path(relative).is_absolute():
        raise ValueError("backend hit path must be component-relative")
    kind = str(raw.get("kind", "text"))
    if kind not in {"text", "symbol", "reference", "caller", "callee", "metadata"}:
        raise ValueError("unknown backend hit kind")
    state = decision["state"]
    adapter_version = state.get("adapter_version")
    index_fingerprint = state.get("index_fingerprint")
    if not isinstance(adapter_version, str) or not isinstance(index_fingerprint, str):
        raise ValueError("ready backend state is incomplete")
    result: NormalizedHit = {
        "backend_id": decision["selected_backend_id"],
        "adapter_version": adapter_version,
        "index_fingerprint": index_fingerprint,
        "component_id": component["component_id"],
        "source_generation_id": component["source_generation_id"],
        "component_relative_path": relative,
        "kind": kind,
        "rank": str(raw.get("rank", ""))[:100],
    }
    line_value = raw.get("line")
    if line_value is not None:
        line = int(line_value)
        if line <= 0:
            raise ValueError("invalid backend hit line")
        result["line"] = line
    symbol_value = raw.get("symbol")
    if symbol_value is not None:
        result["symbol"] = str(symbol_value)[:500]
    return result


def discover_executable(repo: Path) -> str | None:
    found = shutil.which("rlm-bsl-index")
    sibling = next(
        (
            parent / "rlm-tools-bsl/.venv/bin/rlm-bsl-index"
            for parent in repo.resolve().parents
            if (parent / "rlm-tools-bsl/.venv/bin/rlm-bsl-index").is_file()
            and os.access(parent / "rlm-tools-bsl/.venv/bin/rlm-bsl-index", os.X_OK)
        ),
        None,
    )
    return found or (str(sibling) if sibling else None)


def backend_executable(repo: Path, adapter_id: str) -> str | None:
    if adapter_id == "rlm-tools-bsl":
        return discover_executable(repo)
    return shutil.which(str(BACKEND_CATALOG[adapter_id]["executable"]))


def _bounded_run(
    command: list[str],
    *,
    timeout_seconds: int | float = ADAPTER_TIMEOUT_SECONDS,
    input: str | None = None,
    cancelled: Callable[[], bool] | None = None,
    index_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    if timeout_seconds <= 0 or timeout_seconds > 1800:
        raise ValueError("invalid adapter timeout")
    with tempfile.TemporaryDirectory(prefix="one-c-index-home-") as private_home, tempfile.TemporaryFile() as stdin:
        environment = _backend_environment(Path(private_home), index_dir)
        if input is not None:
            _ = stdin.write(input.encode())
            _ = stdin.seek(0)
        process: subprocess.Popen[bytes] = subprocess.Popen(
            command,
            stdin=stdin if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
        if process.stdout is None:
            raise RuntimeError("index adapter output is unavailable")
        selector = selectors.DefaultSelector()
        _ = selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout_seconds
        output = bytearray()
        try:
            while True:
                if cancelled and cancelled():
                    raise InterruptedError("index adapter was cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("index adapter timed out")
                events = selector.select(min(remaining, 0.1))
                if events:
                    chunk = os.read(process.stdout.fileno(), 64 * 1024)
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > ADAPTER_OUTPUT_LIMIT:
                        raise RuntimeError("index adapter output limit exceeded")
                elif process.poll() is not None:
                    break
            return subprocess.CompletedProcess(
                command,
                process.wait(),
                output.decode("utf-8", errors="replace"),
                "",
            )
        except (InterruptedError, TimeoutError, RuntimeError):
            terminate_process(process)
            try:
                _ = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                terminate_process(process, force=True)
                _ = process.wait()
            raise
        finally:
            selector.close()


def _bsl_contract(executable: str, configured_version: str) -> BslContract:
    result = _bounded_run([executable, "contract"])
    try:
        contract_json = parse_json_object(result.stdout)
        mcp_value = contract_json.get("mcp")
        if not isinstance(mcp_value, dict):
            raise ValueError("mcp is missing")
        mcp = _json_object(mcp_value)
        profiles_value = mcp.get("profiles")
        if not isinstance(profiles_value, dict):
            raise ValueError("profiles are missing")
        profile_rows = _json_object(profiles_value)
        typed_profiles: dict[str, ContractProfile] = {}
        for profile_name, profile_value in profile_rows.items():
            if not isinstance(profile_value, dict):
                raise ValueError("invalid profile")
            profile_row = _json_object(profile_value)
            tools_value = profile_row.get("tools")
            if not isinstance(tools_value, list):
                raise ValueError("profile tools are missing")
            tools: list[ContractTool] = []
            for tool_value in tools_value:
                if not isinstance(tool_value, dict) or not isinstance(tool_value.get("name"), str):
                    raise ValueError("invalid profile tool")
                tool_row = _json_object(tool_value)
                tool_name = tool_row.get("name")
                if not isinstance(tool_name, str):
                    raise ValueError("invalid profile tool")
                tool: ContractTool = {"name": tool_name}
                actions_value = tool_row.get("actions")
                if actions_value is not None:
                    if not isinstance(actions_value, list):
                        raise ValueError("invalid tool actions")
                    actions: list[ContractAction] = []
                    for action_value in actions_value:
                        if not isinstance(action_value, dict):
                            raise ValueError("invalid tool actions")
                        action = _json_object(action_value)
                        action_name = action.get("name")
                        if not isinstance(action_name, str):
                            raise ValueError("invalid tool actions")
                        actions.append({"name": action_name})
                    tool["actions"] = actions
                for key in ("output_schema_version", "output_schema_fingerprint"):
                    item = tool_row.get(key)
                    if isinstance(item, str):
                        tool[key] = item
                tools.append(tool)
            typed_profiles[profile_name] = {"tools": tools}
        contract: BslContract = {
            "contract_version": str(contract_json.get("contract_version", "")),
            "build_version": str(contract_json.get("build_version", "")),
            "mcp": {"profiles": typed_profiles},
        }
        transports_value = contract_json.get("transports")
        if isinstance(transports_value, dict):
            transport_rows = _json_object(transports_value)
            transports: dict[str, dict[str, dict[str, JsonValue]]] = {}
            for transport_name, transport_value in transport_rows.items():
                if not isinstance(transport_value, dict):
                    raise ValueError("invalid transport")
                transport_group = _json_object(transport_value)
                typed_group: dict[str, dict[str, JsonValue]] = {}
                for name, settings in transport_group.items():
                    if not isinstance(settings, dict):
                        raise ValueError("invalid transport settings")
                    typed_group[name] = _json_object(settings)
                transports[transport_name] = typed_group
            contract["transports"] = transports
        profiles = contract["mcp"]["profiles"]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("bsl-analyzer returned an invalid machine contract") from exc
    if (
        result.returncode
        or str(contract["contract_version"]) != "1.3"
        or str(contract.get("build_version")) != configured_version
    ):
        raise BslSurfaceContractError(
            "incompatible", "bsl-analyzer contract or build version mismatch",
            expected_contract_version="1.3",
            actual_contract_version=str(contract["contract_version"]),
        )
    actual = {
        str(profile): {
            str(tool["name"]): {
                str(action["name"]) for action in tool.get("actions", [])
            }
            for tool in value["tools"]
        }
        for profile, value in profiles.items()
    }
    expected = {
        profile: {
            tool: actions
            for disposition in ("allowed", "denied")
            for tool, actions in groups[disposition].items()
        }
        for profile, groups in BSL_SEARCH_SURFACE_V1.items()
    }
    syntax = next(
        tool for tool in profiles["reference"]["tools"]
        if tool.get("name") == "syntax_help"
    )
    transport = contract.get("transports", {}).get("workspace", {}).get(
        "broker-required", {}
    )
    unknown = {
        profile: {
            tool: sorted(actions - expected.get(profile, {}).get(tool, set()))
            for tool, actions in tools.items()
            if tool not in expected.get(profile, {})
            or actions - expected.get(profile, {}).get(tool, set())
        }
        for profile, tools in actual.items()
        if profile not in expected or any(
            tool not in expected.get(profile, {})
            or actions - expected.get(profile, {}).get(tool, set())
            for tool, actions in tools.items()
        )
    }
    if unknown:
        raise BslSurfaceContractError(
            "incompatible", "bsl-analyzer search surface has unreviewed additions"
        )
    if actual != expected:
        raise BslSurfaceContractError(
            "partial", "bsl-analyzer search surface is incomplete"
        )
    if (
        str(syntax.get("output_schema_version")) != "1"
        or syntax.get("output_schema_fingerprint")
        != BSL_SYNTAX_OUTPUT_SCHEMA_FINGERPRINT
        or transport != {
            "backend_pid_required": True,
            "auto_launch": False,
            "stdio_fallback": False,
            "peer_identity": "supervised-pid+platform-trust",
        }
    ):
        raise BslSurfaceContractError(
            "incompatible", "bsl-analyzer search surface contract drift"
        )
    return contract


def _bsl_surface_manifest(
    contract: BslContract, executable_fingerprint: str
) -> dict[str, JsonValue]:
    identity: dict[str, JsonValue] = {
        "schema_version": "bsl-search-surface/v1",
        "state": "complete",
        "executable_fingerprint": executable_fingerprint,
        "build_version": str(contract["build_version"]),
        "machine_contract_version": str(contract["contract_version"]),
        "structured_native_versions": {"reference.syntax_help": 1},
        "native_output_schema_fingerprints": {
            "reference.syntax_help": BSL_SYNTAX_OUTPUT_SCHEMA_FINGERPRINT,
        },
        "text_envelope_version": "native-text-envelope/v1",
        "normalizer_version": "bsl-search-normalizer/v1",
        "operations": sorted(
            f"{profile}.{tool}.{action}" if action else f"{profile}.{tool}"
            for profile, groups in BSL_SEARCH_SURFACE_V1.items()
            for tool, actions in groups["allowed"].items()
            for action in (actions or {""})
        ),
    }
    identity["schema_fingerprint"] = "sha256:" + sha256(canonical_json({
        "structured_native_versions": identity["structured_native_versions"],
        "native_output_schema_fingerprints": identity[
            "native_output_schema_fingerprints"
        ],
        "text_envelope_version": identity["text_envelope_version"],
    }))
    identity["normalizer_fingerprint"] = "sha256:" + sha256(
        canonical_json({"version": identity["normalizer_version"]})
    )
    identity["surface_fingerprint"] = "sha256:" + sha256(canonical_json({
        **identity,
        "surface": {
            profile: {
                disposition: {
                    tool: sorted(actions)
                    for tool, actions in groups[disposition].items()
                }
                for disposition in ("allowed", "denied")
            }
            for profile, groups in BSL_SEARCH_SURFACE_V1.items()
        },
    }))
    return identity


def probe_backend(repo: Path, backend: BackendRow) -> BackendProbe:
    adapter_id = backend["adapter_id"]
    executable = backend_executable(repo, adapter_id)
    if not executable:
        return {"available": False, "failure_code": "backend.executable_unavailable", "capabilities": []}
    try:
        executable_fingerprint = "sha256:" + sha256(
            Path(executable).resolve().read_bytes()
        )
        surface_manifest: dict[str, JsonValue] | None = None
        capabilities: list[str]
        if adapter_id == "rlm-tools-bsl":
            actual = cli_version(executable)
            if actual != backend["engine_version"]:
                raise RuntimeError("rlm-tools-bsl build version mismatch")
            capabilities = list(RLM_CAPABILITIES)
            contract_version = "provider-query/v1"
        else:
            contract = _bsl_contract(executable, backend["engine_version"])
            capabilities = list(COMPLETE_SEARCH_CAPABILITIES)
            contract_version = str(contract["contract_version"])
            surface_manifest = _bsl_surface_manifest(
                contract, executable_fingerprint
            )
        probe_result: BackendProbe = {
            "available": True,
            "executable": executable,
            "engine_version": backend["engine_version"],
            "contract_version": contract_version,
            "capabilities": capabilities,
            "executable_fingerprint": executable_fingerprint,
            "capability_fingerprint": capability_fingerprint(
                adapter_id,
                backend["engine_version"],
                capabilities,
                executable_fingerprint,
            ),
        }
        if surface_manifest is not None:
            probe_result["surface_manifest"] = surface_manifest
        return probe_result
    except Exception as exc:
        surface_state = (
            exc.state if isinstance(exc, BslSurfaceContractError)
            else "incompatible"
        )
        failure_result: BackendProbe = {
            "available": False,
            "failure_code": "backend.contract_incompatible",
            "failure_summary": str(exc)[:500],
            "capabilities": [],
        }
        try:
            failure_result["engine_version"] = cli_version(executable)
        except RuntimeError:
            pass
        if isinstance(exc, BslSurfaceContractError):
            failure_result["contract_version"] = exc.actual_contract_version
            failure_result["configured_contract_version"] = exc.expected_contract_version
        if adapter_id == "bsl-analyzer":
            failure_result["surface_manifest"] = {
                "schema_version": "bsl-search-surface/v1",
                "state": surface_state,
            }
        return failure_result


def backend_tool_inventory(repo: Path) -> list[ToolInventory]:
    config = load_config(repo)
    configured = {item["adapter_id"]: item for item in config["backends"]}
    required = {
        adapter_id: sorted(
            capability
            for capability, route in config["routes"].items()
            if adapter_id in route
        )
        for adapter_id in BACKEND_CATALOG
    }
    tools: list[ToolInventory] = []
    for adapter_id in sorted(BACKEND_CATALOG):
        executable = backend_executable(repo, adapter_id)
        backend = configured.get(adapter_id)
        instances: list[ToolInstance] = []
        status = "unavailable"
        if executable and backend:
            probe = probe_backend(repo, backend)
            status = "ready" if probe["available"] else "incompatible"
            instance: ToolInstance = {
                "version": str(probe.get("engine_version", backend["engine_version"])),
                "configured_version": backend["engine_version"],
                "status": status,
                "path": executable,
                "reason_code": str(probe.get("failure_code", "")),
            }
            for key in ("contract_version", "configured_contract_version"):
                value = probe.get(key)
                if isinstance(value, str) and value:
                    instance[key] = value
            instances.append(instance)
        elif executable:
            try:
                version = cli_version(executable)
            except RuntimeError:
                version = ""
            instances.append({
                "version": version,
                "status": "detected",
                "path": executable,
                "reason_code": "backend_not_configured",
            })
            status = "detected"
        tools.append({
            "tool_id": adapter_id,
            "status": status,
            "purpose": "source_indexer",
            "required": bool(required[adapter_id]),
            "route_capabilities": required[adapter_id],
            "instances": instances,
        })
    return tools


@lru_cache(maxsize=64)
def _component_fingerprint(path: str) -> str:
    # ponytail: source-generation paths are immutable; restart invalidates this cheap process cache.
    return "sha256:" + sha256(canonical_json(file_manifest(Path(path))))


def cli_version(executable: str) -> str:
    result = _bounded_run([executable, "--version"])
    import re
    semver = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    match = re.fullmatch(
        rf"(?:rlm-bsl-index|bsl-analyzer)\s+({semver})\s*", result.stdout,
    )
    if result.returncode or not match:
        raise RuntimeError("cannot determine exact indexer version")
    return match.group(1)


def validate_engine_version(repo: Path, executable: str) -> str:
    configured = next(
        item["engine_version"]
        for item in load_config(repo)["backends"]
        if item["adapter_id"] == "rlm-tools-bsl"
    )
    actual = cli_version(executable)
    if actual != configured:
        raise RuntimeError(f"rlm-bsl-index version mismatch: required {configured}, found {actual}")
    return actual


def discover(repo: Path) -> list[Component]:
    pointer = _source_pointer(repo / "research/active-source-generation.json")
    generation_value = pointer.get("generation_id")
    if not isinstance(generation_value, str):
        raise ValueError("active source generation is invalid")
    generation = generation_value
    root = confined(repo / "sources/generations", generation)
    config = load_config(repo)
    legacy_backend = next((item for item in config["backends"] if item["adapter_id"] == "rlm-tools-bsl"), config["backends"][0])
    candidates: list[tuple[str, Path, str, str | None, int | None]]
    if pointer.get("schema_version") == "2":
        candidates = []
        for item in pointer.get("components", []):
            component_id = item.get("component_id")
            component_path = item.get("path")
            kind = item.get("kind")
            representation = item.get("representation_schema")
            if not all(isinstance(value, str) for value in (component_id, component_path, kind, representation)):
                raise ValueError("active source component is invalid")
            assert isinstance(component_id, str) and isinstance(component_path, str) and isinstance(kind, str) and isinstance(representation, str)
            base = confined(root, component_path)
            candidates.append((component_id, base / "source" if kind in {"epf", "erf", "source-tree"} else base, representation, item.get("fingerprint"), item.get("bsl_file_count")))
    elif pointer.get("schema_version") == "1" and pointer.get("representation_schema") in {"xml-hierarchical", "v8unpack", "edt-project"}:
        candidates = []
        for role in ROLES:
            role_root = confined(root, role)
            representation = pointer.get("representation_schema")
            if not isinstance(representation, str):
                raise ValueError("active source representation is invalid")
            candidates.append((f"{role}:configuration", role_root / "configuration", representation, None, None))
            extensions = role_root / "extensions"
            if extensions.is_dir():
                candidates.extend((f"{role}:extension:{item.name.lower()}", item, representation, None, None) for item in extensions.iterdir() if item.is_dir())
            external = role_root / "external"
            if external.is_dir():
                candidates.extend((f"{role}:external:{item.name}", item / "source", representation, None, None) for item in external.iterdir() if item.is_dir())
    else:
        raise ValueError(f"unsupported source representation for indexing: {pointer.get('representation_schema')}")
    result: list[Component] = []
    for component_id, component_root, representation, fingerprint, bsl_file_count in sorted(candidates):
        if not component_root.is_dir():
            continue
        relative = component_root.relative_to(root).as_posix()
        bsl_count = bsl_file_count if isinstance(bsl_file_count, int) else sum(1 for path in component_root.rglob("*.bsl") if path.is_file())
        result.append({
            "component_id": component_id,
            "path": relative,
            "fingerprint": fingerprint or _component_fingerprint(str(component_root.resolve())),
            "representation": representation,
            "source_generation_id": generation,
            "engine": legacy_backend["adapter_id"],
            "engine_version": legacy_backend["engine_version"],
            "bsl_file_count": bsl_count,
        })
    return sorted(result, key=lambda item: item["component_id"])


def index_key(repo: Path, component: Component) -> str:
    preimage = {key: component[key] for key in ("component_id", "path", "fingerprint", "representation", "source_generation_id", "engine", "engine_version")}
    preimage["repository_instance_fingerprint"] = repository_instance_fingerprint(repo)
    return sha256(canonical_json(preimage))


def required_component_ids(repo: Path, paths: list[str], roles: tuple[str, ...]) -> list[str]:
    available = {item["component_id"] for item in discover(repo)}
    result: set[str] = set()
    for value in paths:
        parts = value.replace("\\", "/").split("/")
        selected_roles = roles
        if parts[0] in ROLES:
            selected_roles = (parts.pop(0),)
        if parts[0] == "configuration":
            suffix = "configuration"
        elif len(parts) >= 2 and parts[0] == "extensions":
            suffix = f"extension:{parts[1].lower()}"
        elif len(parts) >= 2 and parts[0] == "external":
            suffix = f"external:{parts[1]}"
        else:
            raise ValueError(f"source evidence path does not identify one component: {value}")
        result.update(f"{role}:{suffix}" for role in selected_roles if f"{role}:{suffix}" in available)
    return sorted(result)


def canonical_evidence(repo: Path, component_id: str, relative_path: str) -> dict[str, str]:
    component = next((item for item in discover(repo) if item["component_id"] == component_id), None)
    if not component:
        raise ValueError("unknown source index component")
    generation_root = repo / "sources/generations" / component["source_generation_id"]
    component_root = confined(generation_root, component["path"])
    path = confined(component_root, normalize_relative(relative_path))
    if not path.is_file() or path.is_symlink():
        raise ValueError("index navigation does not resolve to a canonical source file")
    _role, kind, *identity = component_id.split(":")
    prefix = "configuration" if kind == "configuration" else f"extensions/{identity[0]}" if kind == "extension" else f"external/{identity[0]}/source"
    return {"path": f"{prefix}/{path.relative_to(component_root).as_posix()}", "fingerprint": "sha256:" + sha256(path.read_bytes()), "source_generation_id": component["source_generation_id"], "component_id": component_id}


def cli_probe(
    executable: str, path: Path, index_dir: Path | None = None
) -> CliProbe:
    result = _bounded_run(
        [executable, "index", "info", str(path)], index_dir=index_dir
    )
    output = result.stdout
    return {"ready": result.returncode == 0 and "Status:   fresh" in output, "exit_code": result.returncode, "output": output}


def cli_build(executable: str, path: Path, _component_id: str, timeout_seconds: int | float = 1800, index_dir: Path | None = None) -> CliProbe:
    result = _bounded_run(
        [executable, "index", "build", str(path)],
        timeout_seconds=timeout_seconds,
        index_dir=index_dir,
    )
    probe = cli_probe(executable, path, index_dir)
    return {"ready": result.returncode == 0 and probe["ready"], "exit_code": result.returncode, "output": result.stdout}


def _versioned_structured_content(result: dict[str, JsonValue]) -> dict[str, JsonValue]:
    structured = result.get("structuredContent")
    version = (
        str(structured.get("schema_version", ""))
        if isinstance(structured, dict)
        else ""
    )
    if (
        result.get("isError")
        or not isinstance(structured, dict)
        or version and not (
            version == "1"
            or version.startswith("1.")
            or version.endswith("/v1")
        )
    ):
        raise RuntimeError("bsl-analyzer returned an unsupported structured schema")
    return dict(structured)


def _bsl_mcp(
    executable: str,
    source_dir: Path,
    calls: list[tuple[str, dict[str, JsonValue], bool]],
    timeout_seconds: int | float = ADAPTER_TIMEOUT_SECONDS,
    cancelled: Callable[[], bool] | None = None,
    environment: dict[str, str] | None = None,
    owned_resource: Closable | None = None,
) -> list[dict[str, JsonValue]]:
    from .search_runtime import supervised_workspace_proxy

    environment = environment or _backend_environment(source_dir.parent / ".runtime-home")
    serving_copy = source_dir.parent.parent.name != "staging"
    proxy_context = supervised_workspace_proxy(
        Path(executable),
        source_dir,
        environment,
        timeout_seconds=timeout_seconds,
        serving_copy=serving_copy,
        owned_resource=owned_resource,
    )
    proxy = proxy_context.__enter__()
    try:
        process: subprocess.Popen[str] = subprocess.Popen(
            proxy["command"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            env=proxy["environment"],
            start_new_session=True,
            bufsize=1,
        )
    except BaseException:
        _ = proxy_context.__exit__(*sys.exc_info())
        raise
    stdin_stream = _process_input_stream(process.stdin)
    stdout_stream = _process_output_stream(process.stdout)
    selector = selectors.DefaultSelector()
    selector_target = stdout_stream.fileno() if isinstance(
        stdout_stream, FileDescriptorStream
    ) else 0
    _ = selector.register(selector_target, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout_seconds
    output_bytes = 0
    last_diagnostic = ""
    next_id = 1

    def send(method: str, params: dict[str, JsonValue] | None = None, *, notify: bool = False) -> dict[str, JsonValue]:
        nonlocal next_id, output_bytes, last_diagnostic
        identifier = next_id
        next_id += 1
        payload: dict[str, JsonValue] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = identifier
        if params is not None:
            payload["params"] = params
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        _ = stdin_stream.write(encoded + "\n")
        stdin_stream.flush()
        if notify:
            return {}
        while True:
            if cancelled and cancelled():
                raise InterruptedError("bsl-analyzer MCP was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError("bsl-analyzer MCP request timed out")
            line = stdout_stream.readline()
            if not line:
                detail = f": {last_diagnostic}" if last_diagnostic else ""
                raise RuntimeError(f"bsl-analyzer MCP exited before replying{detail}")
            output_bytes += len(line.encode())
            if output_bytes > ADAPTER_OUTPUT_LIMIT:
                raise RuntimeError("bsl-analyzer MCP output limit exceeded")
            try:
                response = parse_json_object(line)
            except ValueError:
                last_diagnostic = line.strip()[-500:]
                continue
            if response.get("id") != identifier:
                continue
            if response.get("error"):
                raise RuntimeError("bsl-analyzer MCP request failed")
            response_result = response.get("result")
            return dict(response_result) if isinstance(response_result, dict) else {}

    results: list[dict[str, JsonValue]] = []
    try:
        _ = send(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "one-c-autoresearch", "version": "1"},
            },
        )
        _ = send("notifications/initialized", notify=True)
        for tool, arguments, wait_ready in calls:
            result: dict[str, JsonValue] = {}
            while True:
                result = send("tools/call", {"name": tool, "arguments": arguments})
                if wait_ready:
                    structured = _versioned_structured_content(result)
                else:
                    structured_value = result.get("structuredContent")
                    if isinstance(structured_value, dict):
                        structured = dict(structured_value)
                    else:
                        _ = native_text_envelope(result)
                        structured = {}
                if (
                    not wait_ready
                    or structured.get("state") == "ready"
                    and structured.get("stale") is not True
                    and structured.get("superseded") is not True
                ):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("bsl-analyzer index did not become ready")
                time.sleep(min(0.25, remaining))
            results.append(result)
        return results
    finally:
        selector.close()
        try:
            stdin_stream.close()
        except OSError:
            pass
        try:
            _ = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            terminate_process(process)
            try:
                _ = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                terminate_process(process, force=True)
                _ = process.wait()
        _ = proxy_context.__exit__(None, None, None)


def _operational_path(repo: Path, state_root: Path | None = None) -> Path:
    base = state_root or Path(
        os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")
    ) / "one-c-autoresearch/indexes-v2"
    return base / repository_instance_fingerprint(repo).split(":", 1)[1]


def _operational_root(repo: Path, state_root: Path | None = None) -> Path:
    root = _operational_path(repo, state_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def storage_diagnostics(
    repo: Path, state_root: Path | None = None, *, calculate_usage: bool = True,
) -> dict[str, JsonValue]:
    root = _operational_root(repo, state_root)
    if not calculate_usage:
        return {"root": str(root)}
    used = _tree_size(root)
    return {
        "root": str(root),
        "used_bytes": used,
        "quota_bytes": PROJECT_STORAGE_LIMIT,
        "available_bytes": max(0, PROJECT_STORAGE_LIMIT - used),
    }


def preview_index_gc(
    repo: Path, state_root: Path | None = None
) -> GcPlan:
    root = _operational_root(repo, state_root)
    candidates: list[GcCandidate] = []
    for target in sorted((root / "targets").iterdir()) if (root / "targets").is_dir() else []:
        current_path = target / "current.json"
        current = ""
        if current_path.is_file():
            current = str(parse_json_object(current_path.read_text())["instance"])
        instances = target / "instances"
        for instance in sorted(instances.iterdir()) if instances.is_dir() else []:
            relative = instance.relative_to(target).as_posix()
            if relative == current or instance.is_symlink() or not instance.is_dir():
                continue
            candidates.append({
                "target": target.name,
                "instance": instance.name,
                "bytes": _tree_size(instance),
            })
    plan: GcPlan = {
        "schema_version": "index-storage-gc-plan/v1",
        "candidates": candidates,
        "reclaimed_bytes": sum(item["bytes"] for item in candidates),
        "plan_fingerprint": "",
    }
    plan["plan_fingerprint"] = "sha256:" + sha256(canonical_json({key: value for key, value in plan.items() if key != "plan_fingerprint"}))
    return plan


def apply_index_gc(
    repo: Path,
    expected_plan_fingerprint: str,
    *,
    confirmed: bool,
    state_root: Path | None = None,
) -> dict[str, JsonValue]:
    if not confirmed:
        raise ValueError("index storage cleanup requires confirmation")
    with repository_lock(repo):
        plan = preview_index_gc(repo, state_root)
        if plan["plan_fingerprint"] != expected_plan_fingerprint:
            raise RuntimeError("stale index storage cleanup plan")
        root = _operational_root(repo, state_root)
        for item in plan["candidates"]:
            path = confined(
                root / "targets" / item["target"] / "instances",
                item["instance"],
            )
            shutil.rmtree(path)
        return {
            "operation": "indexes.gc",
            "removed_instances": len(plan["candidates"]),
            "reclaimed_bytes": plan["reclaimed_bytes"],
        }


def _copy_private_source(source: Path, target: Path) -> list[dict[str, str | int]]:
    source_manifest = file_manifest(source)
    target.mkdir(parents=True)
    for item in source_manifest:
        relative_path = item["path"]
        if not isinstance(relative_path, str):
            raise ValueError("source manifest path is invalid")
        destination = confined(target, relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(confined(source, relative_path).read_bytes())
    if file_manifest(target) != source_manifest:
        raise RuntimeError("private index mirror differs from canonical source")
    return source_manifest


def _acquire_target_lease(lease_path: Path, token: str) -> None:
    from .events import process_identity, process_identity_alive

    lock_path = lease_path.with_suffix(".lock")
    with lock_path.open("a+b") as lock:
        lock_file(lock)
        if lease_path.is_file():
            try:
                saved = parse_json_object(lease_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
            identity_value = saved.get("process_identity")
            process_identity_value = dict(identity_value) if isinstance(identity_value, dict) else None
            if process_identity_alive(process_identity_value):
                raise RuntimeError("index target build is already leased")
            lease_path.unlink(missing_ok=True)
        descriptor = os.open(lease_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as lease:
            json.dump(
                {
                    "token": token,
                    "process_identity": process_identity(),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                lease,
            )


def build_backend_index(
    repo: Path,
    component: Component,
    backend: BackendRow,
    *,
    state_root: Path | None = None,
    timeout_seconds: int | float = 1800,
    cancelled: Callable[[], bool] | None = None,
    modality: str = "lexical",
) -> BackendState:
    probe = probe_backend(repo, backend)
    if not probe["available"]:
        raise RuntimeError(str(probe.get("failure_code")))
    executable_value = probe.get("executable")
    if not isinstance(executable_value, str):
        raise RuntimeError("backend executable is missing from probe")
    embedding_identity = _index_profile_identity(repo, backend, modality)
    identity = target_identity(
        repo,
        component,
        backend,
        probe["capabilities"],
        str(probe.get("executable_fingerprint", "")),
        modality if backend["adapter_id"] == "bsl-analyzer" else "",
        embedding_identity,
    )
    target_key = target_fingerprint(identity).split(":", 1)[1]
    target_root = _operational_root(repo, state_root) / "targets" / target_key
    target_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lease_path = target_root / "lease.json"
    token = uuid.uuid4().hex
    _acquire_target_lease(lease_path, token)
    staging = target_root / "staging" / token
    mirror = staging / "source"
    source_root = confined(
        repo / "sources/generations" / component["source_generation_id"],
        component["path"],
    )
    try:
        if cancelled and cancelled():
            raise InterruptedError("index build was cancelled")
        source_manifest = _copy_private_source(source_root, mirror)
        executable = executable_value
        rlm_index_dir: Path | None = None
        if backend["adapter_id"] == "rlm-tools-bsl":
            rlm_index_dir = staging / "index"
            outcome = _bounded_run(
                [executable, "index", "build", str(mirror)],
                timeout_seconds=timeout_seconds,
                cancelled=cancelled,
                index_dir=rlm_index_dir,
            )
            if outcome.returncode or not cli_probe(
                executable, mirror, rlm_index_dir
            )["ready"]:
                raise RuntimeError("rlm-tools-bsl index build failed")
        else:
            from . import search_services
            environment = search_services.analyzer_environment(
                _backend_environment(staging / "home"),
                modality="lexical",
            )
            broker = None
            if modality == "hybrid":
                profiles = load_config(repo)["service_profiles"]
                profile_id = str(profiles["hybrid"])
                profile, _secret = search_services.private_profile(
                    repo, profile_id, None
                )
                if profile["kind"] != "embedding":
                    raise RuntimeError("search_services.hybrid_unavailable")
                broker = search_services.EmbeddingBroker(
                    repo, profile_id, operation="build"
                )
                broker_environment = broker.start()
                environment = search_services.analyzer_environment(
                    environment,
                    modality="hybrid",
                    profile=profile,
                    broker_environment=broker_environment,
                )
                environment["ONE_C_EMBEDDING_IDENTITY"] = embedding_identity
            _ = _bsl_mcp(
                executable,
                mirror,
                [
                    (
                        "search",
                        {
                            "action": "search_code",
                            "query": "__one_c_autoresearch_index_probe__",
                            "limit": 1,
                            "max_output_tokens": 128,
                        },
                        False,
                    ),
                    ("graph", {"action": "status"}, True),
                ],
                timeout_seconds=timeout_seconds,
                cancelled=cancelled,
                environment=environment,
                owned_resource=broker,
            )
        if cancelled and cancelled():
            raise InterruptedError("index build was cancelled")
        if file_manifest(source_root) != source_manifest:
            raise RuntimeError("canonical source changed during index build")
        mirror_manifest = file_manifest(mirror)
        source_paths = {row["path"] for row in source_manifest}
        if [item for item in mirror_manifest if item["path"] in source_paths] != source_manifest:
            raise RuntimeError("index adapter changed the private source mirror")
        index_manifest = (
            [
                {**item, "path": f"index/{item['path']}"}
                for item in file_manifest(rlm_index_dir)
            ]
            if backend["adapter_id"] == "rlm-tools-bsl" and rlm_index_dir is not None
            else [
                item
                for item in mirror_manifest
                if item["path"] not in source_paths
            ]
        )
        if not index_manifest:
            raise RuntimeError("index adapter produced no rebuildable state")
        if sum(int(item["size_bytes"]) for item in index_manifest) > ADAPTER_INDEX_LIMIT:
            raise RuntimeError("index adapter payload exceeds the byte limit")
        promoted = promoted_identity(identity, index_manifest)
        instance_key = promoted["index_fingerprint"].split(":", 1)[1]
        instances = target_root / "instances"
        instances.mkdir(exist_ok=True)
        instance = instances / instance_key
        validated_at = datetime.now(timezone.utc).isoformat()
        instance_state = {
            "schema_version": "source-index-instance/v1",
            "status": "ready",
            "identity": promoted,
            "source_manifest": source_manifest,
            "index_manifest": index_manifest,
            "last_validation": validated_at,
        }
        _bounded_atomic_json(staging / "state.json", instance_state)
        if _tree_size(_operational_root(repo, state_root)) > PROJECT_STORAGE_LIMIT:
            raise RuntimeError("index project storage quota exceeded")
        _fsync_tree(staging)
        lease = parse_json_object(lease_path.read_text(encoding="utf-8"))
        if lease.get("token") != token:
            raise RuntimeError("index target lease was fenced")
        if cancelled and cancelled():
            raise InterruptedError("index build was cancelled")
        if instance.exists():
            shutil.rmtree(staging)
            saved = parse_json_object((instance / "state.json").read_text(encoding="utf-8"))
            if (
                saved.get("identity") != promoted
                or saved.get("source_manifest") != source_manifest
                or saved.get("index_manifest") != index_manifest
            ):
                raise RuntimeError("promoted index instance identity conflicts")
        else:
            os.replace(staging, instance)
            _fsync_directory(instances)
        _bounded_atomic_json(
            target_root / "current.json",
            {
                "schema_version": "source-index-pointer/v1",
                "target_fingerprint": target_fingerprint(identity),
                "index_fingerprint": promoted["index_fingerprint"],
                "instance": f"instances/{instance_key}",
            },
        )
        result: BackendState = {
            "adapter_id": backend["adapter_id"],
            "component_id": component["component_id"],
            "status": "ready",
            "index_fingerprint": promoted["index_fingerprint"],
            "target_fingerprint": target_fingerprint(identity),
            "last_validation": validated_at,
        }
        if backend["adapter_id"] == "bsl-analyzer":
            result["modality"] = modality
        return result
    except Exception:
        if staging.exists():
            quarantine = target_root / "quarantine"
            quarantine.mkdir(exist_ok=True)
            os.replace(staging, quarantine / token)
        raise
    finally:
        try:
            lease = parse_json_object(lease_path.read_text(encoding="utf-8"))
            if lease.get("token") == token:
                lease_path.unlink()
        except (OSError, ValueError):
            pass


def ready_backend_state(
    repo: Path,
    component: Component,
    backend: BackendRow,
    *,
    state_root: Path | None = None,
    modality: str = "lexical",
    verify_manifests: bool = True,
) -> BackendState | None:
    probe = probe_backend(repo, backend)
    if not probe["available"]:
        return None
    contract_value = probe.get("contract_version")
    if not isinstance(contract_value, str):
        return None
    try:
        embedding_identity = _index_profile_identity(repo, backend, modality)
    except (KeyError, RuntimeError, ValueError):
        return None
    identity = target_identity(
        repo,
        component,
        backend,
        probe["capabilities"],
        str(probe.get("executable_fingerprint", "")),
        modality if backend["adapter_id"] == "bsl-analyzer" else "",
        embedding_identity,
    )
    target_root = _operational_path(repo, state_root) / "targets" / target_fingerprint(identity).split(":", 1)[1]
    pointer = target_root / "current.json"
    if not pointer.is_file():
        if backend["adapter_id"] != "rlm-tools-bsl":
            return None
        legacy = next(
            (
                item for item in statuses(repo, state_root=state_root)
                if item["component_id"] == component["component_id"]
                and item["status"] == "ready"
            ),
            None,
        )
        if legacy is None:
            return None
        capability_value = probe.get("capability_fingerprint")
        if not isinstance(capability_value, str):
            return None
        source_root = confined(
            repo / "sources/generations" / component["source_generation_id"],
            component["path"],
        )
        index_fingerprint = "sha256:" + sha256(canonical_json({
            "legacy_index_key": legacy["index_key"],
            "capability_fingerprint": capability_value,
        }))
        return {
            "status": "ready",
            "instance_path": str(source_root),
            "index_dir": "",
            "index_fingerprint": index_fingerprint,
            "target_fingerprint": target_fingerprint(identity),
            "last_validation": legacy.get("last_validation"),
            "capabilities": probe["capabilities"],
            "contract_version": contract_value,
            "legacy_adopted": True,
        }
    try:
        current = parse_json_object(pointer.read_text(encoding="utf-8"))
        instance_value = current.get("instance")
        if not isinstance(instance_value, str):
            return None
        instance = confined(target_root, instance_value)
        saved = parse_json_object((instance / "state.json").read_text(encoding="utf-8"))
        saved_identity = saved.get("identity")
        source_manifest_value = saved.get("source_manifest")
        index_manifest_value = saved.get("index_manifest")
        if not isinstance(saved_identity, dict) or not isinstance(source_manifest_value, list) or not isinstance(index_manifest_value, list):
            return None
        if (
            saved.get("status") != "ready"
            or saved_identity.get("target_fingerprint") != target_fingerprint(identity)
            or saved_identity.get("index_fingerprint") != current.get("index_fingerprint")
        ):
            return None
        mirror = instance / "source"
        source_root = confined(
            repo / "sources/generations" / component["source_generation_id"],
            component["path"],
        )
        if not source_root.is_dir():
            return None
        index_dir = instance / "index"
        if verify_manifests and file_manifest(source_root) != source_manifest_value:
            return None
        if verify_manifests and backend["adapter_id"] == "rlm-tools-bsl":
            expected_index: list[dict[str, str | int]] = []
            for item_value in index_manifest_value:
                if not isinstance(item_value, dict) or not isinstance(item_value.get("path"), str):
                    return None
                item: dict[str, str | int] = {}
                for key, value in item_value.items():
                    if not isinstance(value, (str, int)) or isinstance(value, bool):
                        return None
                    item[key] = value
                item["path"] = str(item["path"]).removeprefix("index/")
                expected_index.append(item)
            if not index_dir.is_dir() or file_manifest(index_dir) != expected_index:
                return None
        current_fingerprint = current.get("index_fingerprint")
        if not isinstance(current_fingerprint, str):
            return None
        validation_value = saved.get("last_validation")
        last_validation = validation_value if isinstance(validation_value, str) else None
        state: BackendState = {
            "status": "ready",
            "instance_path": str(mirror),
            "index_dir": str(index_dir) if index_dir.is_dir() else "",
            "index_fingerprint": current_fingerprint,
            "target_fingerprint": target_fingerprint(identity),
            "last_validation": last_validation,
            "capabilities": probe["capabilities"],
            "contract_version": contract_value,
        }
        if embedding_identity:
            state["embedding_identity"] = embedding_identity
        return state
    except (OSError, KeyError, TypeError, ValueError):
        return None


def _nested_dicts(value: JsonValue) -> Iterator[dict[str, JsonValue]]:
    if isinstance(value, dict):
        yield dict(value)
        for child in value.values():
            yield from _nested_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nested_dicts(child)


def _request_limit(request: dict[str, JsonValue]) -> int:
    value = request.get("max_results", 1)
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError("source_search.max_results_invalid")
    return int(value)




def _bsl_workspace_request(request: dict[str, JsonValue]) -> tuple[str, dict[str, JsonValue]]:
    operation = str(request["operation"])
    limit = min(_request_limit(request), 100)
    budget = max(128, min(8192, limit * 256))
    arguments: dict[str, JsonValue] = {"max_output_tokens": budget}
    if operation.startswith("code."):
        arguments.update(
            action="search_code", query=str(request["query"]), limit=min(limit, 50)
        )
        return "search", arguments
    if operation.startswith("symbol."):
        for key in ("symbol", "path", "line", "column", "include", "locale"):
            if request.get(key) is not None:
                arguments[key] = request[key]
        return "symbol_info", arguments
    if operation.startswith("graph."):
        action = operation.removeprefix("graph.")
        arguments["action"] = action
        for source, target in (
            ("query", "query"), ("id", "id"), ("ids", "ids"), ("depth", "depth"),
            ("direction", "dir"), ("detail", "detail"), ("edge_kinds", "edge_kinds"),
            ("provenance", "provenance"),
        ):
            if request.get(source) is not None:
                arguments[target] = request[source]
        arguments["top" if action == "resolve" else "max_nodes"] = min(limit, 50)
        return "graph", arguments
    if operation.startswith("metadata."):
        arguments.update(action=operation.removeprefix("metadata."), mode="source")
        for key in ("filter", "meta_type", "name_mask", "object_type", "object_name", "form_name"):
            if request.get(key) is not None:
                arguments[key] = request[key]
        arguments["max_items"] = min(limit, 1000)
        return "metadata", arguments
    if operation.startswith("diagnostics."):
        arguments["action"] = operation.removeprefix("diagnostics.")
        for key in (
            "path", "codes", "min_severity", "detail", "range_start", "range_end",
            "locale",
        ):
            if request.get(key) is not None:
                arguments[key] = request[key]
        arguments.update(max_files=min(limit, 100), max_findings=limit)
        return "diagnostics", arguments
    raise ValueError("source_search.operation_forbidden")


def _bsl_component_path(value: str, mirror: Path) -> str:
    candidate = Path(value.replace("\\", "/"))
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(mirror.resolve())
        except ValueError as exc:
            raise ValueError("source_search.backend_result_out_of_scope") from exc
    return normalize_relative(candidate.as_posix())


def _bsl_workspace_items(
    result: dict[str, JsonValue], operation: str, mirror: Path, limit: int
) -> tuple[list[dict[str, JsonValue]], set[str]]:
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        envelope = native_text_envelope(result)
        try:
            parsed = parse_json_object(envelope["text"])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and str(parsed.get("schema_version", "")).startswith("1"):
            structured = parsed
        else:
            return [{
                "kind": "native-text",
                "schema_version": envelope["schema_version"],
                "text": envelope["text"],
            }], set()
    else:
        _ = _versioned_structured_content(result)
    modalities = {
        str(row[key]).lower()
        for row in _nested_dicts(structured)
        for key in ("modality", "mode")
        if isinstance(row.get(key), str)
        and str(row[key]).lower() in {"l", "lexical", "s", "semantic", "h", "hybrid"}
    }
    items: list[dict[str, JsonValue]] = []
    seen: set[bytes] = set()
    forbidden_text = {"text", "snippet", "body", "source", "content", "code"}
    for row in _nested_dicts(structured):
        item: dict[str, JsonValue] | None = None
        path = row.get("path") or row.get("relativePath") or row.get("file")
        if isinstance(path, str) and path:
            item = {
                "kind": "canonical-hit",
                "component_relative_path": _bsl_component_path(path, mirror),
            }
            line = row.get("line_start", row.get("startLine", row.get("line")))
            if isinstance(line, int) and line >= 0:
                item["line"] = line + 1
            symbol = row.get("symbol") or row.get("symbolName") or row.get("name")
            if isinstance(symbol, str) and symbol:
                item["symbol"] = symbol[:4096]
        elif all(isinstance(row.get(key), str) and row[key] for key in ("source", "target")):
            item = {
                "kind": "navigation-edge",
                "source": str(row["source"])[:4096],
                "target": str(row["target"])[:4096],
                "edge_kind": str(row.get("edge_kind", row.get("kind", "unknown")))[:4096],
                "provenance": str(row.get("provenance", "unresolved"))[:4096],
            }
        elif isinstance(row.get("id"), str) and row["id"]:
            item = {
                "kind": "navigation-node",
                "id": str(row["id"])[:4096],
                "label": str(
                    row.get("label") or row.get("name") or row.get("symbol") or row["id"]
                )[:4096],
                "provenance": str(row.get("provenance", "unresolved"))[:4096],
            }
        elif isinstance(row.get("code"), str) and (
            isinstance(row.get("message"), str) or isinstance(row.get("description"), str)
        ):
            item = {
                "kind": "derived-finding",
                "code": str(row["code"])[:4096],
                "message": str(row.get("message") or row.get("description"))[:4096],
                "severity": str(row.get("severity", "info")).lower()
                if str(row.get("severity", "info")).lower() in {"info", "warning", "error"}
                else "info",
            }
        elif any(
            key in row for key in ("definition", "signature", "type", "summary", "description")
        ):
            safe = {
                key: value
                for key, value in row.items()
                if key not in forbidden_text and not isinstance(value, (dict, list))
            }
            if safe:
                item = {
                    "kind": "derived-finding",
                    "code": operation,
                    "message": canonical_json(safe).decode()[:4096],
                    "severity": "info",
                }
        if item is not None:
            encoded = canonical_json(item)
            if encoded not in seen:
                seen.add(encoded)
                items.append(item)
        if len(items) >= limit:
            break
    return items, modalities


def _bsl_workspace_query(
    executable: str,
    mirror: Path,
    request: dict[str, JsonValue],
    timeout_seconds: int | float,
    cancelled: Callable[[], bool] | None = None,
    environment: dict[str, str] | None = None,
    owned_resource: Closable | None = None,
) -> dict[str, JsonValue]:
    operation = str(request["operation"])
    tool, arguments = _bsl_workspace_request(request)
    result = _bsl_mcp(
        executable, mirror, [(tool, arguments, False)], timeout_seconds, cancelled,
        environment,
        owned_resource,
    )[0]
    items, modalities = _bsl_workspace_items(
        result, operation, mirror, _request_limit(request)
    )
    if operation == "code.search_lexical" and modalities not in (
        {"l"}, {"lexical"},
    ):
        raise RuntimeError("source_search.lexical_modality_unavailable")
    if operation == "code.search_hybrid" and not (
        {"h"} <= modalities
        or {"hybrid"} <= modalities
        or (modalities & {"l", "lexical"} and modalities & {"s", "semantic"})
    ):
        raise RuntimeError("source_search.semantic_modality_unavailable")
    return {
        "items": items,
        "truncated": len(items) >= _request_limit(request),
        "modalities": sorted(modalities),
    }


def query_backend(
    repo: Path,
    decision: BackendDecision,
    request: dict[str, JsonValue],
    *,
    state_root: Path | None = None,
    timeout_seconds: int | float = ADAPTER_TIMEOUT_SECONDS,
    cancelled: Callable[[], bool] | None = None,
) -> list[RawHit] | dict[str, JsonValue]:
    adapter_id = decision["selected_backend_id"]
    component_value = request.get("component_id")
    operation_value = request.get("operation")
    if not isinstance(component_value, str) or not isinstance(operation_value, str):
        raise ValueError("source_search.request_invalid")
    component = next(
        item
        for item in discover(repo)
        if item["component_id"] == component_value
    )
    backend = next(
        item for item in load_config(repo)["backends"]
        if item["adapter_id"] == adapter_id
    )
    modality = (
        "hybrid"
        if operation_value == "code.search_hybrid"
        else "lexical"
    )
    ready = ready_backend_state(
        repo, component, backend, state_root=state_root, modality=modality
    )
    if ready is None:
        raise RuntimeError("source_search.index_stale")
    ready_fingerprint = ready.get("index_fingerprint")
    decision_fingerprint = decision["state"].get("index_fingerprint")
    instance_path = ready.get("instance_path")
    if not isinstance(ready_fingerprint, str) or not isinstance(decision_fingerprint, str) or not isinstance(instance_path, str):
        raise RuntimeError("source_search.index_stale")
    if ready_fingerprint != decision_fingerprint:
        raise RuntimeError("source_search.index_stale")
    executable = backend_executable(repo, adapter_id)
    if not executable:
        raise RuntimeError("source_search.backend_unavailable")
    mirror = Path(instance_path)
    query = str(request.get("query", ""))
    limit = _request_limit(request)
    if adapter_id == "bsl-analyzer":
        if operation_value.startswith(
            ("code.", "symbol.", "graph.", "metadata.", "diagnostics.")
        ):
            operation = operation_value
            base_environment = _backend_environment(
                mirror.parent / ".runtime-home"
            )
            if operation != "code.search_hybrid":
                from .search_services import analyzer_environment
                environment = analyzer_environment(
                    base_environment, modality="lexical"
                )
                return _bsl_workspace_query(
                    executable, mirror, request, timeout_seconds, cancelled,
                    environment,
                )
            from . import search_services
            profile_id = str(load_config(repo).get("service_profiles", {}).get("hybrid", ""))
            profile, _secret = search_services.private_profile(
                repo, profile_id, state_root
            )
            if profile["kind"] != "embedding":
                raise RuntimeError("search_services.hybrid_unavailable")
            broker = search_services.EmbeddingBroker(
                repo, profile_id, operation="query", base=state_root
            )
            broker_environment = broker.start()
            environment = search_services.analyzer_environment(
                base_environment,
                modality="hybrid",
                profile=profile,
                broker_environment=broker_environment,
            )
            semantic_identity = profile.get("semantic_identity")
            if not isinstance(semantic_identity, str):
                raise RuntimeError("search_services.hybrid_unavailable")
            environment["ONE_C_EMBEDDING_IDENTITY"] = semantic_identity
            result = _bsl_workspace_query(
                executable, mirror, request, timeout_seconds, cancelled,
                environment, broker,
            )
            result["embedding_identity"] = semantic_identity
            return result
        raise ValueError("source_search.operation_forbidden")
    index_dir_value = ready.get("index_dir")
    result = _bounded_run(
        [
            executable,
            "provider",
            "query",
            str(mirror),
            query,
            "--limit",
            str(limit),
            "--json",
        ],
        timeout_seconds=timeout_seconds,
        cancelled=cancelled,
        index_dir=Path(index_dir_value) if isinstance(index_dir_value, str) and index_dir_value else None,
    )
    if cancelled and cancelled():
        raise InterruptedError("source search was cancelled")
    try:
        payload = parse_json_object(result.stdout)
    except ValueError as exc:
        raise RuntimeError("rlm-tools-bsl returned invalid JSON") from exc
    if result.returncode or payload.get("status") != "available":
        raise RuntimeError("source_search.backend_query_failed")
    if operation_value != "code.search_lexical":
        raise ValueError("source_search.operation_forbidden")
    hits: list[RawHit] = []
    candidates_value = payload.get("candidates", [])
    if not isinstance(candidates_value, list):
        raise RuntimeError("source_search.backend_result_invalid")
    for row in candidates_value[:limit]:
        if not isinstance(row, dict) or not isinstance(row.get("relativePath"), str):
            raise RuntimeError("source_search.backend_result_invalid")
        relative_path = row.get("relativePath")
        assert isinstance(relative_path, str)
        hit: RawHit = {
            "component_relative_path": relative_path,
            "kind": "text",
        }
        start_line = row.get("startLine")
        if isinstance(start_line, int) and not isinstance(start_line, bool) and start_line >= 0:
            hit["line"] = start_line + 1
        symbol_name = row.get("symbolName")
        if isinstance(symbol_name, str) and symbol_name:
            hit["symbol"] = symbol_name
        hits.append(hit)
    return hits


def ensure_configured(
    repo: Path,
    *,
    component_ids: list[str] | None = None,
    backend_ids: list[str] | None = None,
    rebuild: bool = False,
    confirmed: bool = False,
    state_root: Path | None = None,
    timeout_seconds: int | float = 1800,
    cancelled: Callable[[], bool] | None = None,
) -> list[BackendState]:
    if rebuild and not confirmed:
        raise ValueError("index rebuild requires explicit confirmation")
    components = {item["component_id"]: item for item in discover(repo)}
    backends = {item["adapter_id"]: item for item in load_config(repo)["backends"]}
    selected_components = sorted(component_ids or components)
    selected_backends = list(backend_ids or backends)
    if set(selected_components) - set(components) or set(selected_backends) - set(backends):
        raise ValueError("unknown index component or backend selection")
    results: list[BackendState] = []
    for component_id in selected_components:
        component = components[component_id]
        if component["bsl_file_count"] == 0:
            for adapter_id in selected_backends:
                results.append({
                    "component_id": component_id,
                    "adapter_id": adapter_id,
                    "status": "not_indexable",
                })
            continue
        for adapter_id in selected_backends:
            backend = backends[adapter_id]
            modalities = (
                ("lexical", "hybrid")
                if backend["adapter_id"] == "bsl-analyzer"
                else ("lexical",)
            )
            for modality in modalities:
                ready = ready_backend_state(
                    repo, component, backend, state_root=state_root,
                    modality=modality,
                )
                if ready and not rebuild:
                    ready["component_id"] = component_id
                    ready["adapter_id"] = adapter_id
                    if len(modalities) > 1:
                        ready["modality"] = modality
                    results.append(ready)
                    continue
                results.append(
                    build_backend_index(
                        repo,
                        component,
                        backend,
                        state_root=state_root,
                        timeout_seconds=timeout_seconds,
                        cancelled=cancelled,
                        modality=modality,
                    )
                )
    if "bsl-analyzer" in selected_backends:
        results.append(
            ensure_reference_index(
                repo,
                backends["bsl-analyzer"],
                state_root=state_root,
                rebuild=rebuild,
                cancelled=cancelled,
            )
        )
    return results


def validate_configured(
    repo: Path,
    *,
    component_ids: list[str] | None = None,
    backend_ids: list[str] | None = None,
    state_root: Path | None = None,
) -> list[BackendState]:
    components = {item["component_id"]: item for item in discover(repo)}
    backends = {item["adapter_id"]: item for item in load_config(repo)["backends"]}
    selected_components = sorted(component_ids or components)
    selected_backends = list(backend_ids or backends)
    if set(selected_components) - set(components) or set(selected_backends) - set(backends):
        raise ValueError("unknown index component or backend selection")
    validated_at = datetime.now(timezone.utc).isoformat()
    results: list[BackendState] = []
    for component_id in selected_components:
        component = components[component_id]
        for adapter_id in selected_backends:
            if component["bsl_file_count"] == 0:
                results.append({
                    "component_id": component_id,
                    "adapter_id": adapter_id,
                    "status": "not_indexable",
                    "validated_at": validated_at,
                })
                continue
            backend = backends[adapter_id]
            modalities = (
                ("lexical", "hybrid")
                if backend["adapter_id"] == "bsl-analyzer"
                else ("lexical",)
            )
            for modality in modalities:
                ready = ready_backend_state(
                    repo, component, backend, state_root=state_root,
                    modality=modality,
                )
                ready_fingerprint = ready.get("index_fingerprint") if ready else None
                result: BackendState = {
                    "component_id": component_id,
                    "adapter_id": adapter_id,
                    "status": "ready" if ready else "not_ready",
                    "index_fingerprint": ready_fingerprint if isinstance(ready_fingerprint, str) else "",
                    "validated_at": validated_at,
                }
                if len(modalities) > 1:
                    result["modality"] = modality
                results.append(result)
    if "bsl-analyzer" in selected_backends:
        results.append(reference_index_status(
            repo, backends["bsl-analyzer"], state_root=state_root
        ))
    return results


def _reference_index_target_impl(
    repo: Path,
    backend: BackendRow,
    state_root: Path | None = None,
) -> tuple[Path, str, BackendProbe, Path]:
    executable = backend_executable(repo, "bsl-analyzer")
    probe = probe_backend(repo, backend)
    surface_identity = str(
        probe.get("surface_manifest", {}).get("surface_fingerprint", "")
    )
    executable_fingerprint = str(probe.get("executable_fingerprint", ""))
    if (
        not executable
        or not probe.get("available")
        or not surface_identity.startswith("sha256:")
        or not executable_fingerprint.startswith("sha256:")
    ):
        raise RuntimeError("reference_search.surface_incompatible")
    identity = "sha256:" + sha256(canonical_json({
        "schema_version": "reference-identity/v1",
        "executable_fingerprint": executable_fingerprint,
        "surface_identity": surface_identity,
        "corpus_fingerprint": "sha256:" + sha256(canonical_json({
            "source": "selected-build-bundled",
            "executable_fingerprint": executable_fingerprint,
        })),
    }))
    root = _operational_root(repo, state_root) / "reference" / identity.split(":", 1)[1]
    return Path(executable), identity, probe, root


_reference_index_target = _reference_index_target_impl


def reference_index_target(
    repo: Path,
    backend: BackendRow,
    state_root: Path | None = None,
) -> tuple[Path, str, BackendProbe, Path]:
    return _reference_index_target(repo, backend, state_root)


def reference_index_status(
    repo: Path,
    backend: BackendRow,
    *,
    state_root: Path | None = None,
) -> BackendState:
    from . import reference_search
    try:
        _executable, identity, probe, root = reference_index_target(
            repo, backend, state_root
        )
        executable_fingerprint = probe.get("executable_fingerprint")
        if not isinstance(executable_fingerprint, str):
            raise RuntimeError("reference_search.surface_incompatible")
        _ = reference_search.current_reference_index(
            root, executable_fingerprint
        )
        return {
            "component_id": "reference:bundled",
            "adapter_id": "bsl-analyzer",
            "modality": "reference",
            "status": "ready",
            "reference_identity": identity,
            "index_fingerprint": identity,
        }
    except (OSError, KeyError, RuntimeError, ValueError) as exc:
        return {
            "component_id": "reference:bundled",
            "adapter_id": "bsl-analyzer",
            "modality": "reference",
            "status": "not_ready",
            "readiness_reason": str(exc),
            "recovery_action": "ensure_index",
        }


def ensure_reference_index(
    repo: Path,
    backend: BackendRow,
    *,
    state_root: Path | None = None,
    rebuild: bool = False,
    cancelled: Callable[[], bool] | None = None,
) -> BackendState:
    from . import reference_search
    current = reference_index_status(repo, backend, state_root=state_root)
    if current.get("status") == "ready" and not rebuild:
        return current
    staging: Path | None = None
    try:
        executable, identity, probe, root = reference_index_target(
            repo, backend, state_root
        )
        executable_fingerprint = probe.get("executable_fingerprint")
        engine_version = probe.get("engine_version")
        if not isinstance(executable_fingerprint, str) or not isinstance(engine_version, str):
            raise RuntimeError("reference_search.surface_incompatible")
        corpus = {
            "source": "selected-build-bundled",
            "corpus_fingerprint": "sha256:" + sha256(canonical_json({
                "source": "selected-build-bundled",
                "executable_fingerprint": executable_fingerprint,
            })),
            "build_version": engine_version,
        }
        staging = reference_search.stage_reference_index(
            root, executable_fingerprint, corpus
        )
        _ = reference_search.execute_reference(
            executable,
            staging,
            {
                "operation": "reference.find_docs",
                "query": "Процедура",
                "max_results": 1,
            },
            cancelled=cancelled,
        )
        _ = reference_search.promote_reference_index(staging, {
            "state": "ready",
            "cancelled": False,
            "download_attempts": 0,
            "network_attempts": 0,
            "corpus_fingerprint": corpus["corpus_fingerprint"],
        })
        return {
            "component_id": "reference:bundled",
            "adapter_id": "bsl-analyzer",
            "modality": "reference",
            "status": "ready",
            "reference_identity": identity,
            "index_fingerprint": identity,
        }
    except Exception as exc:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        return {
            "component_id": "reference:bundled",
            "adapter_id": "bsl-analyzer",
            "modality": "reference",
            "status": "failed",
            "readiness_reason": str(exc)[:200],
            "recovery_action": "ensure_index",
        }


def statuses(repo: Path, state_root: Path | None = None, probe: Callable[[Path], CliProbe] | None = None) -> list[LegacyStatus]:
    state_root = state_root or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "one-c-autoresearch/indexes"
    prior_states: list[dict[str, JsonValue]] = []
    state_paths: Iterator[Path] = iter(state_root.glob("*/state.json")) if state_root.is_dir() else iter(())
    for path in state_paths:
        try:
            prior_states.append(parse_json_object(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    rows: list[LegacyStatus] = []
    for component in discover(repo):
        key = index_key(repo, component); state = state_root / key / "state.json"
        saved: dict[str, JsonValue] = {}
        if state.is_file():
            try:
                saved = parse_json_object(state.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        if component["bsl_file_count"] == 0:
            status = "not_indexable"
        elif probe is not None and probe(repo / "sources/generations" / component["source_generation_id"] / component["path"]).get("ready"):
            status = "ready"
        elif not state.is_file():
            status = "stale" if any(
                prior.get("component_id") == component["component_id"]
                for prior in prior_states
            ) else "missing"
        else:
            status_value = saved.get("status", "failed") if saved.get("index_key") == key else "stale"
            status = status_value if isinstance(status_value, str) else "failed"
        validation_value = saved.get("last_validation")
        result_value = saved.get("result")
        build_result = _build_outcome(result_value)
        rows.append({**component, "index_key": key, "status": status, "last_validation": validation_value if isinstance(validation_value, str) else None, "result": build_result})
    return rows


def backend_statuses(
    repo: Path, state_root: Path | None = None, *, verify_manifests: bool = True,
) -> list[dict[str, JsonValue]]:
    config = load_config(repo)
    rows: list[dict[str, JsonValue]] = []
    for backend in config["backends"]:
        adapter_id = backend["adapter_id"]
        backend_probe = probe_backend(repo, backend)
        for component in discover(repo):
            modalities = (
                ("lexical", "hybrid")
                if adapter_id == "bsl-analyzer"
                else ("lexical",)
            )
            for modality in modalities:
                capabilities = list(backend_probe.get("capabilities", []))
                if len(modalities) > 1:
                    capabilities = [
                        capability for capability in capabilities
                        if capability != (
                            "code-search-hybrid"
                            if modality == "lexical"
                            else "code-search-lexical"
                        )
                    ]
                    if modality == "hybrid":
                        capabilities = [
                            capability for capability in capabilities
                            if capability == "code-search-hybrid"
                        ]
                promoted = ready_backend_state(
                    repo, component, backend, state_root=state_root,
                    modality=modality,
                    verify_manifests=verify_manifests,
                )
                status = (
                    "ready" if promoted else
                    "unavailable" if not backend_probe["available"] else "missing"
                )
                promoted_fingerprint = promoted.get("index_fingerprint") if promoted else None
                index_fingerprint = promoted_fingerprint if isinstance(promoted_fingerprint, str) else ""
                readiness_reason = None
                recovery_action = None
                if status == "unavailable":
                    readiness_reason = str(
                        backend_probe.get("failure_code", "backend.unavailable")
                    )
                    recovery_action = "fix_backend_installation"
                elif status == "missing":
                    try:
                        embedding_identity = _index_profile_identity(
                            repo, backend, modality
                        )
                        identity = target_identity(
                            repo, component, backend, capabilities,
                            str(backend_probe.get("executable_fingerprint", "")),
                            modality if adapter_id == "bsl-analyzer" else "",
                            embedding_identity,
                        )
                        target_root = _operational_path(
                            repo, state_root,
                        ) / "targets" / target_fingerprint(identity).split(":", 1)[1]
                        readiness_reason = (
                            "index.stale"
                            if (target_root / "current.json").is_file()
                            else "index.missing"
                        )
                    except (KeyError, RuntimeError, ValueError):
                        readiness_reason = "embedding_profile_unavailable"
                    recovery_action = (
                        "rebuild_index"
                        if readiness_reason == "index.stale"
                        else "ensure_index"
                    )
                rows.append(_json_object({
                    **component,
                    "adapter_id": adapter_id,
                    "engine": adapter_id,
                    **({"modality": modality} if len(modalities) > 1 else {}),
                    "adapter_version": str(BACKEND_CATALOG[adapter_id]["adapter_version"]),
                    "engine_version": backend["engine_version"],
                    "capabilities": capabilities,
                    "route_priorities": {
                        capability: config["routes"][capability].index(adapter_id)
                        for capability in capabilities
                        if adapter_id in config["routes"].get(capability, [])
                    },
                    "status": status,
                    "readiness_reason": readiness_reason,
                    "recovery_action": recovery_action,
                    "index_fingerprint": index_fingerprint,
                    "target_fingerprint": promoted.get("target_fingerprint") if promoted else None,
                    "contract_version": backend_probe.get("contract_version"),
                    "capability_fingerprint": backend_probe.get("capability_fingerprint"),
                    "surface_identity": backend_probe.get(
                        "surface_manifest", {}
                    ).get("surface_fingerprint"),
                    "embedding_identity": (
                        promoted.get("embedding_identity") if promoted else None
                    ),
                    "last_validation": (
                        promoted.get("last_validation") if promoted else None
                    ),
                    "legacy_adopted": bool(promoted and promoted.get("legacy_adopted")),
                    "failure_code": backend_probe.get("failure_code") if status == "unavailable" else None,
                    "failure_summary": backend_probe.get("failure_summary") if status == "unavailable" else None,
                }))
    return rows


def ensure(repo: Path, builder: Callable[[Path, str], BuildOutcome], *, selected: list[str] | None = None, rebuild: bool = False, confirmed: bool = False, state_root: Path | None = None, probe: Callable[[Path], CliProbe] | None = None) -> list[LegacyStatus]:
    pointer = parse_json_object((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    if pointer.get("schema_version") == "1":
        raise ValueError("new index builds require routed source schema version 2")
    if rebuild and not confirmed:
        raise ValueError("index rebuild requires explicit confirmation")
    state_root = state_root or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "one-c-autoresearch/indexes"
    available = {item["component_id"]: item for item in statuses(repo, state_root, probe)}
    wanted = sorted(selected or available)
    if set(wanted) - set(available):
        raise ValueError("unknown component selection")
    results: list[LegacyStatus] = []
    generation_id = pointer.get("generation_id")
    if not isinstance(generation_id, str):
        raise ValueError("active source generation is invalid")
    source_root = repo / "sources/generations" / generation_id
    for component_id in wanted:
        item = available[component_id]
        if item["status"] == "not_indexable" or item["status"] == "ready" and not rebuild:
            results.append(item); continue
        state_path = state_root / item["index_key"] / "state.json"
        identity = {key: item[key] for key in ("component_id", "path", "fingerprint", "representation", "source_generation_id", "engine", "engine_version")}
        atomic_json(state_path, {"schema_version": "1", "index_key": item["index_key"], "status": "building", **identity})
        outcome: BuildOutcome
        try:
            outcome = builder(confined(source_root, item["path"]), item["component_id"])
            status = "ready" if outcome.get("ready") else "failed"
            validated = datetime.now(timezone.utc).isoformat()
            atomic_json(state_path, {"schema_version": "1", "index_key": item["index_key"], "status": status, "result": outcome, "last_validation": validated, **identity})
        except Exception as exc:
            status = "failed"; outcome = {"ready": False, "error": str(exc)}; validated = datetime.now(timezone.utc).isoformat()
            atomic_json(state_path, {"schema_version": "1", "index_key": item["index_key"], "status": status, "result": outcome, "last_validation": validated, **identity})
        results.append({**item, "status": status, "result": outcome, "last_validation": validated})
    return results
