from __future__ import annotations

import json
import os
import fcntl
import selectors
import signal
import subprocess
import shutil
import tempfile
import time
import uuid
from functools import lru_cache
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .contracts import (
    ROLES, atomic_json, canonical_json, confined, file_manifest,
    normalize_relative, repository_lock, sha256,
)

CAPABILITIES = (
    "text-search",
    "symbol-definition",
    "symbol-references",
    "callers",
    "callees",
    "metadata-navigation",
)
BSL_CAPABILITIES = tuple(
    capability for capability in CAPABILITIES
    if capability != "symbol-references"
)
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
BSL_SEARCH_SURFACE_V1 = {
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


class BslSurfaceContractError(RuntimeError):
    def __init__(self, state: str, summary: str):
        super().__init__(summary)
        self.state = state


def native_text_envelope(
    result: dict[str, Any], *, max_bytes: int = ADAPTER_OUTPUT_LIMIT
) -> dict[str, Any]:
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


def _bounded_atomic_json(path: Path, value: dict[str, Any]) -> None:
    if len(canonical_json(value)) > ADAPTER_STATE_LIMIT:
        raise RuntimeError("index adapter state exceeds the byte limit")
    atomic_json(path, value)


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
        descriptor = os.open(
            path, os.O_RDONLY | (os.O_DIRECTORY if path.is_dir() else 0)
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def repository_instance_fingerprint(repo: Path) -> str:
    project_id = tomllib.loads((repo / "project.toml").read_text(encoding="utf-8"))["project"]["id"]
    return "sha256:" + sha256(canonical_json({
        "project_id": str(project_id),
        "repository_root": str(repo.resolve()),
    }))


def load_config(repo: Path, required_capabilities: tuple[str, ...] = ()) -> dict[str, Any]:
    raw = tomllib.loads((repo / "research/indexing.toml").read_text(encoding="utf-8"))
    version = str(raw.get("schema_version", "1"))
    if version == "1":
        if set(raw) - {"schema_version", "engine", "engine_version"} or set(raw) < {"engine", "engine_version"}:
            raise ValueError("invalid indexing schema version 1")
        if raw["engine"] != "rlm-tools-bsl" or not str(raw["engine_version"]).strip():
            raise ValueError("invalid legacy indexing backend")
        backends = [{"adapter_id": "rlm-tools-bsl", "engine_version": str(raw["engine_version"])}]
        routes = {capability: ["rlm-tools-bsl"] for capability in CAPABILITIES}
        return {"schema_version": "2", "source_schema_version": "1", "backends": backends, "routes": routes}
    expected = (
        {"schema_version", "backends", "routes"}
        if version == "2"
        else {
            "schema_version", "machine_contract_version", "backends", "routes",
            "service_profiles",
        }
    )
    if version not in {"2", "3"} or set(raw) != expected:
        raise ValueError(f"invalid indexing schema version {version}")
    if version == "3" and str(raw["machine_contract_version"]) != "1.3":
        raise ValueError("indexing schema version 3 requires machine contract 1.3")
    backend_rows = raw.get("backends")
    routes_raw = raw.get("routes")
    if not isinstance(backend_rows, list) or not backend_rows or not isinstance(routes_raw, dict):
        raise ValueError("indexing backends and routes are required")
    backends: list[dict[str, str]] = []
    identifiers: list[str] = []
    for row in backend_rows:
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
        allowed_capabilities = (
            COMPLETE_SEARCH_CAPABILITIES if version == "3" else CAPABILITIES
        )
        if capability not in allowed_capabilities:
            raise ValueError("unknown indexing capability")
        if (
            not isinstance(members, list)
            or not members
            or any(not isinstance(item, str) for item in members)
            or len(members) != len(set(members))
            or set(members) - set(identifiers)
        ):
            raise ValueError("invalid indexing capability route")
        routes[capability] = list(members)
    missing = set(required_capabilities) - set(routes)
    if missing:
        raise ValueError(f"missing required indexing routes: {sorted(missing)}")
    result: dict[str, Any] = {
        "schema_version": version,
        "source_schema_version": version,
        "backends": backends,
        "routes": routes,
    }
    if version == "3":
        if not {"code-search-lexical", "code-search-hybrid"} <= set(routes):
            raise ValueError("indexing schema version 3 requires explicit lexical and hybrid routes")
        profiles = raw["service_profiles"]
        if (
            not isinstance(profiles, dict)
            or set(profiles) != set(SCHEMA3_SERVICE_PROFILES)
            or any(
                not isinstance(profiles[name], str) or not profiles[name].strip()
                for name in SCHEMA3_SERVICE_PROFILES
            )
            or len(set(profiles.values())) != len(profiles)
        ):
            raise ValueError("invalid indexing schema version 3 service profiles")
        result["machine_contract_version"] = "1.3"
        result["service_profiles"] = dict(profiles)
    return result


def serialize_config(config: dict[str, Any]) -> bytes:
    version = str(config.get("schema_version"))
    if version not in {"2", "3"}:
        raise ValueError("only normalized indexing schema versions 2 and 3 can be written")
    lines = [f'schema_version = "{version}"']
    if version == "3":
        lines.append('machine_contract_version = "1.3"')
    lines.append("")
    for backend in config["backends"]:
        lines.extend((
            "[[backends]]",
            f'adapter_id = {json.dumps(backend["adapter_id"])}',
            f'engine_version = {json.dumps(backend["engine_version"])}',
            "",
        ))
    lines.append("[routes]")
    capabilities = CAPABILITIES if version == "2" else COMPLETE_SEARCH_CAPABILITIES
    for capability in capabilities:
        if capability in config["routes"]:
            values = ", ".join(json.dumps(item) for item in config["routes"][capability])
            lines.append(f'{json.dumps(capability)} = [{values}]')
    if version == "3":
        lines.extend(("", "[service_profiles]"))
        for name in SCHEMA3_SERVICE_PROFILES:
            lines.append(f"{name} = {json.dumps(config['service_profiles'][name])}")
    return ("\n".join(lines) + "\n").encode()


def _preview_candidate(
    repo: Path,
    candidate: dict[str, Any],
    required_capabilities: tuple[str, ...],
) -> tuple[dict[str, Any], bytes, str]:
    if not isinstance(candidate, dict):
        raise ValueError("indexing configuration must be an object")
    if candidate == {"schema_version": "1"}:
        current = load_config(repo)
        if (
            current["backends"] != [{
                "adapter_id": "rlm-tools-bsl",
                "engine_version": current["backends"][0]["engine_version"],
            }]
            or set(current["routes"]) != set(CAPABILITIES)
            or any(route != ["rlm-tools-bsl"] for route in current["routes"].values())
        ):
            raise ValueError("indexing configuration is not exactly representable as schema version 1")
        engine_version = current["backends"][0]["engine_version"]
        normalized = current
        encoded = (
            'schema_version = "1"\n'
            'engine = "rlm-tools-bsl"\n'
            f"engine_version = {json.dumps(engine_version)}\n"
        ).encode()
        return normalized, encoded, "1"
    if candidate.get("schema_version") != "2":
        raise ValueError("indexing configuration preview requires schema version 2 or an exact version 1 downgrade")
    with tempfile.TemporaryDirectory() as temporary:
        shadow = Path(temporary)
        (shadow / "research").mkdir()
        (shadow / "research/indexing.toml").write_bytes(serialize_config(candidate))
        normalized = load_config(shadow, required_capabilities)
    return normalized, serialize_config(normalized), "2"


def config_fingerprint(repo: Path) -> str:
    return "sha256:" + sha256((repo / "research/indexing.toml").read_bytes())


def preview_config(
    repo: Path,
    candidate: dict[str, Any],
    expected_file_fingerprint: str,
    required_capabilities: tuple[str, ...] = (),
) -> dict[str, Any]:
    if expected_file_fingerprint != config_fingerprint(repo):
        raise RuntimeError("stale indexing configuration fingerprint")
    normalized, encoded, target_schema_version = _preview_candidate(
        repo,
        candidate,
        required_capabilities,
    )
    normalized.pop("source_schema_version", None)
    current = load_config(repo)
    current_backend_ids = {item["adapter_id"] for item in current["backends"]}
    candidate_backend_ids = {item["adapter_id"] for item in normalized["backends"]}
    affected = sorted(current_backend_ids | candidate_backend_ids)
    plan = {
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
        "reuse_invalidated": current != {**normalized, "source_schema_version": current.get("source_schema_version", "2")},
    }
    return {
        **plan,
        "plan_fingerprint": "sha256:" + sha256(canonical_json(plan)),
    }


def apply_config(
    repo: Path,
    candidate: dict[str, Any],
    expected_file_fingerprint: str,
    expected_plan_fingerprint: str,
    required_capabilities: tuple[str, ...] = (),
) -> dict[str, Any]:
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


def _schema3_candidate(candidate: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    if (
        not isinstance(candidate, dict)
        or candidate.get("schema_version") != "3"
        or set(candidate) != {
            "schema_version", "machine_contract_version", "backends", "routes",
            "service_profiles",
        }
    ):
        raise ValueError("schema 3 migration requires an indexing schema 3 candidate")
    with tempfile.TemporaryDirectory() as temporary:
        shadow = Path(temporary)
        (shadow / "research").mkdir()
        encoded = serialize_config(candidate)
        (shadow / "research/indexing.toml").write_bytes(encoded)
        normalized = load_config(shadow)
    normalized.pop("source_schema_version", None)
    return normalized, serialize_config(normalized)


def _validate_schema3_profiles(
    profile_operations: dict[str, list[str]],
) -> dict[str, list[str]]:
    if not isinstance(profile_operations, dict) or set(profile_operations) != set(
        SCHEMA3_SERVICE_PROFILES
    ):
        raise ValueError("lexical and hybrid profile operations are required")
    normalized: dict[str, list[str]] = {}
    for profile, operations in profile_operations.items():
        if (
            not isinstance(operations, list)
            or not operations
            or any(not isinstance(item, str) for item in operations)
            or len(operations) != len(set(operations))
        ):
            raise ValueError("invalid schema 3 profile operations")
        if "find_references" in operations:
            raise ValueError(
                "find_references is not admitted for new v2 profiles; "
                "use symbol or graph operations"
            )
        if set(operations) - COMPLETE_SEARCH_OPERATIONS:
            raise ValueError("unknown schema 3 profile operation")
        normalized[profile] = list(operations)
    return normalized


def preview_schema3_migration(
    repo: Path,
    candidate: dict[str, Any],
    expected_file_fingerprint: str,
    profile_operations: dict[str, list[str]],
    v1_inflight_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    if config_fingerprint(repo) != expected_file_fingerprint:
        raise RuntimeError("stale indexing configuration fingerprint")
    current = load_config(repo)
    if current["source_schema_version"] != "2":
        raise ValueError("schema 3 migration requires indexing schema version 2")
    normalized, encoded = _schema3_candidate(candidate)
    operations = _validate_schema3_profiles(profile_operations)
    if any(not isinstance(item, str) or not item for item in v1_inflight_ids):
        raise ValueError("invalid v1 in-flight invocation identifier")
    if len(v1_inflight_ids) != len(set(v1_inflight_ids)):
        raise ValueError("duplicate v1 in-flight invocation identifier")
    bsl = next(
        (row for row in normalized["backends"] if row["adapter_id"] == "bsl-analyzer"),
        None,
    )
    if bsl is None:
        raise ValueError("schema 3 requires a bsl-analyzer backend")
    probe = probe_backend(repo, bsl)
    manifest = probe.get("surface_manifest", {})
    if (
        not probe.get("available")
        or probe.get("contract_version") != "1.3"
        or manifest.get("state") != "complete"
        or manifest.get("machine_contract_version") != "1.3"
    ):
        raise RuntimeError("schema 3 requires the complete bsl-analyzer contract 1.3")
    plan = {
        "schema_version": "indexing-schema3-migration-plan/v1",
        "current_file_fingerprint": expected_file_fingerprint,
        "normalized_file_fingerprint": "sha256:" + sha256(encoded),
        "normalized_file": encoded.decode(),
        "machine_contract_version": "1.3",
        "profile_bindings": normalized["service_profiles"],
        "profile_operations": operations,
        "explicit_routes": {
            name: normalized["routes"][f"code-search-{name}"]
            for name in SCHEMA3_SERVICE_PROFILES
        },
        "stale_semantic_targets": ["code-search-hybrid"],
        "v2_reuse_invalidated": True,
        "preserved_v1_inflight": list(v1_inflight_ids),
        "backup_required": True,
        "build_started": False,
    }
    return {**plan, "plan_fingerprint": "sha256:" + sha256(canonical_json(plan))}


def apply_schema3_migration(
    repo: Path,
    candidate: dict[str, Any],
    expected_file_fingerprint: str,
    expected_plan_fingerprint: str,
    profile_operations: dict[str, list[str]],
    v1_inflight_ids: tuple[str, ...] = (),
    state_root: Path | None = None,
) -> dict[str, Any]:
    with repository_lock(repo):
        plan = preview_schema3_migration(
            repo, candidate, expected_file_fingerprint, profile_operations,
            v1_inflight_ids,
        )
        if plan["plan_fingerprint"] != expected_plan_fingerprint:
            raise RuntimeError("stale indexing schema 3 migration plan")
        backup = capture_schema2_backup(repo, state_root)
        from .contracts import atomic_bytes
        atomic_bytes(repo / "research/indexing.toml", plan["normalized_file"].encode())
        marker = _operational_root(repo, state_root) / "schema3/stale-semantic-targets.json"
        _bounded_atomic_json(marker, {
            "schema_version": "indexing-stale-targets/v1",
            "targets": plan["stale_semantic_targets"],
        })
        marker.chmod(0o600)
    return {
        "operation": "indexes.migrate_schema3",
        "configuration_fingerprint": config_fingerprint(repo),
        "plan_fingerprint": expected_plan_fingerprint,
        "backup": backup,
        "stale_semantic_targets": plan["stale_semantic_targets"],
        "preserved_v1_inflight": plan["preserved_v1_inflight"],
        "build_started": False,
    }


def preview_schema3_rollback(
    repo: Path,
    expected_file_fingerprint: str,
    state_root: Path | None = None,
    *,
    v2_inflight_ids: tuple[str, ...] = (),
    purge_v2_state: bool = False,
) -> dict[str, Any]:
    if config_fingerprint(repo) != expected_file_fingerprint:
        raise RuntimeError("stale indexing configuration fingerprint")
    if load_config(repo)["source_schema_version"] != "3":
        raise ValueError("schema 3 rollback requires indexing schema version 3")
    backup = _operational_path(
        repo, state_root
    ) / "migrations/pre-schema3-indexing.toml"
    if not backup.is_file() or backup.is_symlink():
        raise RuntimeError("schema 2 rollback backup is unavailable")
    payload = backup.read_bytes()
    if not prior_runtime_schema2_ready(backup):
        raise RuntimeError("schema 2 rollback backup is invalid")
    if any(not isinstance(item, str) or not item for item in v2_inflight_ids):
        raise ValueError("invalid v2 in-flight invocation identifier")
    plan = {
        "schema_version": "indexing-schema3-rollback-plan/v1",
        "current_file_fingerprint": expected_file_fingerprint,
        "restore_file_fingerprint": "sha256:" + sha256(payload),
        "v2_inflight_ids": list(v2_inflight_ids),
        "admission_closure_required": True,
        "inflight_handling_required": bool(v2_inflight_ids),
        "v2_state_action": "purge" if purge_v2_state else "retain",
        "readiness_check_required": True,
    }
    return {**plan, "plan_fingerprint": "sha256:" + sha256(canonical_json(plan))}


def prior_runtime_schema2_ready(backup: Path) -> bool:
    try:
        with tempfile.TemporaryDirectory() as temporary:
            shadow = Path(temporary)
            (shadow / "research").mkdir()
            (shadow / "research/indexing.toml").write_bytes(backup.read_bytes())
            return load_config(shadow)["source_schema_version"] == "2"
    except (OSError, ValueError, KeyError):
        return False


def apply_schema3_rollback(
    repo: Path,
    expected_file_fingerprint: str,
    expected_plan_fingerprint: str,
    readiness_check: Callable[[Path], bool],
    state_root: Path | None = None,
    *,
    v2_inflight_ids: tuple[str, ...] = (),
    purge_v2_state: bool = False,
    admission_closed: bool = False,
    inflight_handling: str | None = None,
) -> dict[str, Any]:
    with repository_lock(repo):
        plan = preview_schema3_rollback(
            repo, expected_file_fingerprint, state_root,
            v2_inflight_ids=v2_inflight_ids, purge_v2_state=purge_v2_state,
        )
        if plan["plan_fingerprint"] != expected_plan_fingerprint:
            raise RuntimeError("stale indexing schema 3 rollback plan")
        if not admission_closed:
            raise RuntimeError("v2 admission must be closed before rollback")
        if v2_inflight_ids and inflight_handling not in {"drained", "cancelled"}:
            raise RuntimeError("v2 invocations must be drained or cancelled before rollback")
        backup = _operational_path(
            repo, state_root
        ) / "migrations/pre-schema3-indexing.toml"
        if not readiness_check(backup):
            raise RuntimeError("schema 2 readiness check failed")
        from .contracts import atomic_bytes
        atomic_bytes(repo / "research/indexing.toml", backup.read_bytes())
        marker = _operational_path(
            repo, state_root
        ) / "schema3/stale-semantic-targets.json"
        if purge_v2_state:
            marker.unlink(missing_ok=True)
            try:
                marker.parent.rmdir()
            except OSError:
                pass
    return {
        "operation": "indexes.rollback_schema3",
        "configuration_fingerprint": config_fingerprint(repo),
        "plan_fingerprint": expected_plan_fingerprint,
        "restored_schema_version": "2",
        "prior_runtime_ready": True,
        "v2_state_action": plan["v2_state_action"],
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
    component: dict[str, Any],
    backend: dict[str, str],
    capabilities: list[str],
    executable_fingerprint: str = "",
    modality: str = "",
    embedding_identity: str = "",
) -> dict[str, Any]:
    adapter_id = backend["adapter_id"]
    return {
        "adapter_id": adapter_id,
        "adapter_version": BACKEND_CATALOG[adapter_id]["adapter_version"],
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
        **({"modality": modality} if modality else {}),
        **(
            {"embedding_identity": embedding_identity}
            if embedding_identity else {}
        ),
    }


def _index_profile_identity(
    repo: Path, backend: dict[str, str], modality: str
) -> str:
    config = load_config(repo)
    if (
        backend["adapter_id"] != "bsl-analyzer"
        or config["source_schema_version"] != "3"
    ):
        return ""
    if modality == "lexical":
        return ""
    if modality != "hybrid":
        raise ValueError("invalid BSL Analyzer index modality")
    from . import search_services
    profile_id = str(config["service_profiles"]["hybrid"])
    profile, _secret = search_services._private_profile(repo, profile_id, None)
    if profile.get("kind") != "embedding" or not profile.get("enabled"):
        raise RuntimeError("search_services.hybrid_unavailable")
    return str(profile["semantic_identity"])


def target_fingerprint(identity: dict[str, Any]) -> str:
    return "sha256:" + sha256(canonical_json(identity))


def promoted_identity(identity: dict[str, Any], index_manifest: list[dict[str, Any]]) -> dict[str, Any]:
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
    config: dict[str, Any],
    capability: str,
    component: dict[str, Any],
    states: list[dict[str, Any]],
) -> dict[str, Any]:
    if capability not in {*CAPABILITIES, *COMPLETE_SEARCH_CAPABILITIES}:
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
            decision = {
                "capability": capability,
                "preferred_backend_id": route[0],
                "selected_backend_id": adapter_id,
                "fallback": adapter_id != route[0],
                "fallback_reason": skipped[0]["reason"] if skipped else None,
                "skipped": skipped,
                "state": state,
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
    config: dict[str, Any],
    components: list[dict[str, Any]],
    states: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    degraded: list[dict[str, Any]] = []
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


def normalize_hit(raw: dict[str, Any], decision: dict[str, Any], component: dict[str, Any]) -> dict[str, Any]:
    allowed = {"component_relative_path", "line", "symbol", "kind", "rank"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("invalid backend search hit")
    relative = normalize_relative(str(raw.get("component_relative_path", "")))
    if Path(relative).is_absolute():
        raise ValueError("backend hit path must be component-relative")
    kind = str(raw.get("kind", "text"))
    if kind not in {"text", "symbol", "reference", "caller", "callee", "metadata"}:
        raise ValueError("unknown backend hit kind")
    state = decision["state"]
    result = {
        "backend_id": decision["selected_backend_id"],
        "adapter_version": state["adapter_version"],
        "index_fingerprint": state["index_fingerprint"],
        "component_id": component["component_id"],
        "source_generation_id": component["source_generation_id"],
        "component_relative_path": relative,
        "kind": kind,
        "rank": str(raw.get("rank", ""))[:100],
    }
    if raw.get("line") is not None:
        line = int(raw["line"])
        if line <= 0:
            raise ValueError("invalid backend hit line")
        result["line"] = line
    if raw.get("symbol") is not None:
        result["symbol"] = str(raw["symbol"])[:500]
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
    return shutil.which(BACKEND_CATALOG[adapter_id]["executable"])


def _bounded_run(
    command: list[str],
    *,
    timeout_seconds: int = ADAPTER_TIMEOUT_SECONDS,
    input: str | None = None,
    cancelled: Callable[[], bool] | None = None,
    index_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    if timeout_seconds <= 0 or timeout_seconds > 1800:
        raise ValueError("invalid adapter timeout")
    with tempfile.TemporaryDirectory(prefix="one-c-index-home-") as private_home, tempfile.TemporaryFile() as stdin:
        environment = _backend_environment(Path(private_home), index_dir)
        if input is not None:
            stdin.write(input.encode())
            stdin.seek(0)
        process = subprocess.Popen(
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
        selector.register(process.stdout, selectors.EVENT_READ)
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
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        finally:
            selector.close()


def _bsl_contract(executable: str, configured_version: str) -> dict[str, Any]:
    result = _bounded_run([executable, "contract"])
    try:
        contract = json.loads(result.stdout)
        profiles = contract["mcp"]["profiles"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("bsl-analyzer returned an invalid machine contract") from exc
    if (
        result.returncode
        or str(contract["contract_version"]) != "1.3"
        or str(contract.get("build_version")) != configured_version
    ):
        raise RuntimeError("bsl-analyzer contract or build version mismatch")
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
    contract: dict[str, Any], executable_fingerprint: str
) -> dict[str, Any]:
    identity = {
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


def probe_backend(repo: Path, backend: dict[str, str]) -> dict[str, Any]:
    adapter_id = backend["adapter_id"]
    executable = backend_executable(repo, adapter_id)
    if not executable:
        return {"available": False, "failure_code": "backend.executable_unavailable", "capabilities": []}
    try:
        executable_fingerprint = "sha256:" + sha256(
            Path(executable).resolve().read_bytes()
        )
        if adapter_id == "rlm-tools-bsl":
            actual = cli_version(executable)
            if actual != backend["engine_version"]:
                raise RuntimeError("rlm-tools-bsl build version mismatch")
            capabilities = list(CAPABILITIES)
            contract_version = "provider-query/v1"
        else:
            contract = _bsl_contract(executable, backend["engine_version"])
            capabilities = list((*BSL_CAPABILITIES, *COMPLETE_SEARCH_CAPABILITIES))
            contract_version = str(contract["contract_version"])
            surface_manifest = _bsl_surface_manifest(
                contract, executable_fingerprint
            )
        return {
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
            **(
                {"surface_manifest": surface_manifest}
                if adapter_id == "bsl-analyzer"
                else {}
            ),
        }
    except Exception as exc:
        surface_state = (
            exc.state if isinstance(exc, BslSurfaceContractError)
            else "incompatible"
        )
        return {
            "available": False,
            "failure_code": "backend.contract_incompatible",
            "failure_summary": str(exc)[:500],
            "capabilities": [],
            **(
                {"surface_manifest": {
                    "schema_version": "bsl-search-surface/v1",
                    "state": surface_state,
                }}
                if adapter_id == "bsl-analyzer"
                else {}
            ),
        }


def backend_tool_inventory(repo: Path) -> list[dict[str, Any]]:
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
    tools: list[dict[str, Any]] = []
    for adapter_id in sorted(BACKEND_CATALOG):
        executable = backend_executable(repo, adapter_id)
        backend = configured.get(adapter_id)
        instances: list[dict[str, Any]] = []
        status = "unavailable"
        if executable and backend:
            probe = probe_backend(repo, backend)
            status = "ready" if probe["available"] else "incompatible"
            instances.append({
                "version": str(probe.get("engine_version", backend["engine_version"])),
                "status": status,
                "path": executable,
                "reason_code": str(probe.get("failure_code", "")),
            })
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
    match = re.fullmatch(r"(?:rlm-bsl-index|bsl-analyzer)\s+(\d+\.\d+\.\d+)\s*", result.stdout)
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


def discover(repo: Path) -> list[dict[str, Any]]:
    pointer = json.loads((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    generation = str(pointer["generation_id"])
    root = confined(repo / "sources/generations", generation)
    config = load_config(repo)
    legacy_backend = next((item for item in config["backends"] if item["adapter_id"] == "rlm-tools-bsl"), config["backends"][0])
    if pointer.get("schema_version") == "2":
        candidates = [
            (item["component_id"], confined(root, item["path"]) / "source" if item["kind"] in {"epf", "erf", "source-tree"} else confined(root, item["path"]), item["representation_schema"], item.get("fingerprint"), item.get("bsl_file_count"))
            for item in pointer.get("components", [])
        ]
    elif pointer.get("schema_version") == "1" and pointer.get("representation_schema") in {"xml-hierarchical", "v8unpack", "edt-project"}:
        candidates = []
        for role in ROLES:
            role_root = confined(root, role)
            candidates.append((f"{role}:configuration", role_root / "configuration", pointer["representation_schema"], None, None))
            extensions = role_root / "extensions"
            if extensions.is_dir():
                candidates.extend((f"{role}:extension:{item.name.lower()}", item, pointer["representation_schema"], None, None) for item in extensions.iterdir() if item.is_dir())
            external = role_root / "external"
            if external.is_dir():
                candidates.extend((f"{role}:external:{item.name}", item / "source", pointer["representation_schema"], None, None) for item in external.iterdir() if item.is_dir())
    else:
        raise ValueError(f"unsupported source representation for indexing: {pointer.get('representation_schema')}")
    result: list[dict[str, Any]] = []
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


def index_key(repo: Path, component: dict[str, Any]) -> str:
    preimage = {key: component[key] for key in ("component_id", "path", "fingerprint", "representation", "source_generation_id", "engine", "engine_version")}
    preimage["repository_instance_fingerprint"] = repository_instance_fingerprint(repo)
    return sha256(canonical_json(preimage))


def required_component_ids(repo: Path, paths: list[str], roles: tuple[str, ...]) -> list[str]:
    available = {item["component_id"] for item in discover(repo)}
    result = set()
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
    role, kind, *identity = component_id.split(":")
    prefix = "configuration" if kind == "configuration" else f"extensions/{identity[0]}" if kind == "extension" else f"external/{identity[0]}/source"
    return {"path": f"{prefix}/{path.relative_to(component_root).as_posix()}", "fingerprint": "sha256:" + sha256(path.read_bytes()), "source_generation_id": component["source_generation_id"], "component_id": component_id}


def cli_probe(
    executable: str, path: Path, index_dir: Path | None = None
) -> dict[str, Any]:
    result = _bounded_run(
        [executable, "index", "info", str(path)], index_dir=index_dir
    )
    output = result.stdout
    return {"ready": result.returncode == 0 and "Status:   fresh" in output, "exit_code": result.returncode, "output": output}


def cli_build(executable: str, path: Path, _component_id: str, timeout_seconds: int = 1800, index_dir: Path | None = None) -> dict[str, Any]:
    result = _bounded_run(
        [executable, "index", "build", str(path)],
        timeout_seconds=timeout_seconds,
        index_dir=index_dir,
    )
    probe = cli_probe(executable, path, index_dir)
    return {"ready": result.returncode == 0 and probe["ready"], "exit_code": result.returncode, "output": result.stdout}


def _versioned_structured_content(result: dict[str, Any]) -> dict[str, Any]:
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
    return structured


def _bsl_mcp(
    executable: str,
    source_dir: Path,
    calls: list[tuple[str, dict[str, Any], bool]],
    timeout_seconds: int = ADAPTER_TIMEOUT_SECONDS,
    cancelled: Callable[[], bool] | None = None,
    environment: dict[str, str] | None = None,
    owned_resource: Any | None = None,
) -> list[dict[str, Any]]:
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
        process = subprocess.Popen(
            proxy["command"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=proxy["environment"],
            start_new_session=True,
            bufsize=1,
        )
    except BaseException:
        proxy_context.__exit__(*__import__("sys").exc_info())
        raise
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("bsl-analyzer broker proxy stdio is unavailable")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout_seconds
    output_bytes = 0
    next_id = 1

    def send(method: str, params: dict[str, Any] | None = None, *, notify: bool = False) -> dict[str, Any]:
        nonlocal next_id, output_bytes
        identifier = next_id
        next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = identifier
        if params is not None:
            payload["params"] = params
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        process.stdin.write(encoded + "\n")
        process.stdin.flush()
        if notify:
            return {}
        while True:
            if cancelled and cancelled():
                raise InterruptedError("bsl-analyzer MCP was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError("bsl-analyzer MCP request timed out")
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("bsl-analyzer MCP exited before replying")
            output_bytes += len(line.encode())
            if output_bytes > ADAPTER_OUTPUT_LIMIT:
                raise RuntimeError("bsl-analyzer MCP output limit exceeded")
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") != identifier:
                continue
            if response.get("error"):
                raise RuntimeError("bsl-analyzer MCP request failed")
            return response.get("result") or {}

    results: list[dict[str, Any]] = []
    try:
        send(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "one-c-autoresearch", "version": "1"},
            },
        )
        send("notifications/initialized", notify=True)
        for tool, arguments, wait_ready in calls:
            attempts = 80 if wait_ready else 1
            for attempt in range(attempts):
                result = send("tools/call", {"name": tool, "arguments": arguments})
                structured = (
                    _versioned_structured_content(result)
                    if wait_ready
                    else result.get("structuredContent")
                )
                if not wait_ready and not isinstance(structured, dict):
                    native_text_envelope(result)
                if (
                    not wait_ready
                    or structured.get("state") == "ready"
                    and structured.get("stale") is not True
                    and structured.get("superseded") is not True
                ):
                    break
                if attempt + 1 == attempts:
                    raise TimeoutError("bsl-analyzer index did not become ready")
                time.sleep(0.1)
            results.append(result)
        return results
    finally:
        selector.close()
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        proxy_context.__exit__(None, None, None)


def _operational_path(repo: Path, state_root: Path | None = None) -> Path:
    base = state_root or Path(
        os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")
    ) / "one-c-autoresearch/indexes-v2"
    return base / repository_instance_fingerprint(repo).split(":", 1)[1]


def _operational_root(repo: Path, state_root: Path | None = None) -> Path:
    root = _operational_path(repo, state_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def capture_schema2_backup(
    repo: Path, state_root: Path | None = None
) -> dict[str, str]:
    path = repo / "research/indexing.toml"
    payload = path.read_bytes()
    if str(tomllib.loads(payload.decode()).get("schema_version")) != "2":
        raise ValueError("pre-migration backup requires indexing schema version 2")
    load_config(repo)
    backup = _operational_root(repo, state_root) / "migrations/pre-schema3-indexing.toml"
    if backup.exists():
        if backup.is_symlink() or backup.read_bytes() != payload:
            raise RuntimeError("pre-migration indexing backup conflicts with current schema 2")
    else:
        from .contracts import atomic_bytes
        atomic_bytes(backup, payload)
        backup.chmod(0o600)
    return {
        "path": str(backup),
        "fingerprint": "sha256:" + sha256(payload),
        "schema_version": "2",
    }


def storage_diagnostics(
    repo: Path, state_root: Path | None = None
) -> dict[str, Any]:
    root = _operational_root(repo, state_root)
    used = _tree_size(root)
    return {
        "root": str(root),
        "used_bytes": used,
        "quota_bytes": PROJECT_STORAGE_LIMIT,
        "available_bytes": max(0, PROJECT_STORAGE_LIMIT - used),
    }


def preview_index_gc(
    repo: Path, state_root: Path | None = None
) -> dict[str, Any]:
    root = _operational_root(repo, state_root)
    candidates: list[dict[str, Any]] = []
    for target in sorted((root / "targets").iterdir()) if (root / "targets").is_dir() else []:
        current_path = target / "current.json"
        current = ""
        if current_path.is_file():
            current = str(json.loads(current_path.read_text())["instance"])
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
    plan = {
        "schema_version": "index-storage-gc-plan/v1",
        "candidates": candidates,
        "reclaimed_bytes": sum(item["bytes"] for item in candidates),
    }
    return {
        **plan,
        "plan_fingerprint": "sha256:" + sha256(canonical_json(plan)),
    }


def apply_index_gc(
    repo: Path,
    expected_plan_fingerprint: str,
    *,
    confirmed: bool,
    state_root: Path | None = None,
) -> dict[str, Any]:
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


def _copy_private_source(source: Path, target: Path) -> list[dict[str, Any]]:
    source_manifest = file_manifest(source)
    target.mkdir(parents=True)
    for item in source_manifest:
        destination = confined(target, item["path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(confined(source, item["path"]).read_bytes())
    if file_manifest(target) != source_manifest:
        raise RuntimeError("private index mirror differs from canonical source")
    return source_manifest


def _acquire_target_lease(lease_path: Path, token: str) -> None:
    from .events import process_identity, process_identity_alive

    lock_path = lease_path.with_suffix(".lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if lease_path.is_file():
            try:
                saved = json.loads(lease_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                saved = {}
            if process_identity_alive(saved.get("process_identity")):
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
    component: dict[str, Any],
    backend: dict[str, str],
    *,
    state_root: Path | None = None,
    timeout_seconds: int = 1800,
    cancelled: Callable[[], bool] | None = None,
    modality: str = "lexical",
) -> dict[str, Any]:
    probe = probe_backend(repo, backend)
    if not probe["available"]:
        raise RuntimeError(str(probe.get("failure_code")))
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
        executable = str(probe["executable"])
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
                profile_id = str(load_config(repo)["service_profiles"]["hybrid"])
                profile, _secret = search_services._private_profile(
                    repo, profile_id, None
                )
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
            _bsl_mcp(
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
            if backend["adapter_id"] == "rlm-tools-bsl"
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
        lease = json.loads(lease_path.read_text(encoding="utf-8"))
        if lease.get("token") != token:
            raise RuntimeError("index target lease was fenced")
        if cancelled and cancelled():
            raise InterruptedError("index build was cancelled")
        if instance.exists():
            shutil.rmtree(staging)
            saved = json.loads((instance / "state.json").read_text(encoding="utf-8"))
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
        return {
            "adapter_id": backend["adapter_id"],
            **({"modality": modality} if backend["adapter_id"] == "bsl-analyzer" else {}),
            "component_id": component["component_id"],
            "status": "ready",
            "index_fingerprint": promoted["index_fingerprint"],
            "target_fingerprint": target_fingerprint(identity),
            "last_validation": validated_at,
        }
    except Exception:
        if staging.exists():
            quarantine = target_root / "quarantine"
            quarantine.mkdir(exist_ok=True)
            os.replace(staging, quarantine / token)
        raise
    finally:
        try:
            lease = json.loads(lease_path.read_text(encoding="utf-8"))
            if lease.get("token") == token:
                lease_path.unlink()
        except (OSError, json.JSONDecodeError):
            pass


def ready_backend_state(
    repo: Path,
    component: dict[str, Any],
    backend: dict[str, str],
    *,
    state_root: Path | None = None,
    modality: str = "lexical",
) -> dict[str, Any] | None:
    probe = probe_backend(repo, backend)
    if not probe["available"]:
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
        source_root = confined(
            repo / "sources/generations" / component["source_generation_id"],
            component["path"],
        )
        index_fingerprint = "sha256:" + sha256(canonical_json({
            "legacy_index_key": legacy["index_key"],
            "capability_fingerprint": probe["capability_fingerprint"],
        }))
        return {
            "status": "ready",
            "instance_path": str(source_root),
            "index_dir": "",
            "index_fingerprint": index_fingerprint,
            "target_fingerprint": target_fingerprint(identity),
            "last_validation": legacy.get("last_validation"),
            "capabilities": probe["capabilities"],
            "contract_version": probe["contract_version"],
            "legacy_adopted": True,
        }
    try:
        current = json.loads(pointer.read_text(encoding="utf-8"))
        instance = confined(target_root, current["instance"])
        saved = json.loads((instance / "state.json").read_text(encoding="utf-8"))
        if (
            saved.get("status") != "ready"
            or saved.get("identity", {}).get("target_fingerprint") != target_fingerprint(identity)
            or saved.get("identity", {}).get("index_fingerprint") != current.get("index_fingerprint")
        ):
            return None
        mirror = instance / "source"
        source_root = confined(
            repo / "sources/generations" / component["source_generation_id"],
            component["path"],
        )
        if file_manifest(source_root) != saved["source_manifest"]:
            return None
        index_dir = instance / "index"
        if backend["adapter_id"] == "rlm-tools-bsl":
            expected_index = [
                {**item, "path": item["path"].removeprefix("index/")}
                for item in saved["index_manifest"]
            ]
            if not index_dir.is_dir() or file_manifest(index_dir) != expected_index:
                return None
        return {
            "status": "ready",
            "instance_path": str(mirror),
            "index_dir": str(index_dir) if index_dir.is_dir() else "",
            "index_fingerprint": current["index_fingerprint"],
            "target_fingerprint": target_fingerprint(identity),
            "last_validation": saved.get("last_validation"),
            "capabilities": probe["capabilities"],
            "contract_version": probe["contract_version"],
            **(
                {"embedding_identity": embedding_identity}
                if embedding_identity else {}
            ),
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _nested_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nested_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nested_dicts(child)


def _bsl_hits(
    executable: str,
    mirror: Path,
    operation: str,
    query: str,
    limit: int,
    timeout_seconds: int,
    cancelled: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    budget = max(128, min(8192, limit * 256))
    if operation == "search_text":
        results = _bsl_mcp(
            executable,
            mirror,
            [("search", {"action": "search_code", "query": query, "limit": min(limit, 50), "max_output_tokens": budget}, False)],
            timeout_seconds,
            cancelled,
        )
        kind = "text"
    elif operation in {"find_symbol", "find_references"}:
        results = _bsl_mcp(
            executable,
            mirror,
            [("symbol_info", {"symbol": query, "max_output_tokens": budget}, False)],
            timeout_seconds,
            cancelled,
        )
        kind = "symbol" if operation == "find_symbol" else "reference"
    elif operation in {"find_callers", "find_callees"}:
        resolved = _bsl_mcp(
            executable,
            mirror,
            [("graph", {"action": "resolve", "query": query, "top": min(limit, 50)}, False)],
            timeout_seconds,
            cancelled,
        )[0]
        node_id = next(
            (
                str(row["id"])
                for row in _nested_dicts(
                    _versioned_structured_content(resolved)
                )
                if row.get("id")
            ),
            "",
        )
        if not node_id:
            return []
        action = "callers" if operation == "find_callers" else "callees"
        results = _bsl_mcp(
            executable,
            mirror,
            [(
                "graph",
                {
                    "action": action,
                    "id": node_id,
                    "max_nodes": min(limit, 50),
                    "max_output_tokens": budget,
                },
                False,
            )],
            timeout_seconds,
            cancelled,
        )
        kind = "caller" if operation == "find_callers" else "callee"
    elif operation == "navigate_metadata":
        results = _bsl_mcp(
            executable,
            mirror,
            [(
                "metadata",
                {
                    "action": "tree",
                    "filter": query,
                    "max_items": min(limit, 1000),
                    "max_output_tokens": budget,
                    "mode": "source",
                },
                False,
            )],
            timeout_seconds,
            cancelled,
        )
        kind = "metadata"
    else:
        raise ValueError("source_search.operation_forbidden")
    hits: list[dict[str, Any]] = []
    for row in _nested_dicts([
        _versioned_structured_content(result)
        for result in results
    ]):
        path = row.get("path") or row.get("relativePath") or row.get("file")
        if not isinstance(path, str) or not path:
            continue
        hit: dict[str, Any] = {
            "component_relative_path": path.replace("\\", "/"),
            "kind": kind,
        }
        line = row.get("line_start", row.get("startLine", row.get("line")))
        if isinstance(line, int) and line >= 0:
            hit["line"] = line + 1
        symbol = row.get("symbol") or row.get("symbolName") or row.get("name")
        if isinstance(symbol, str) and symbol:
            hit["symbol"] = symbol
        hits.append(hit)
        if len(hits) >= limit:
            break
    return hits


def _bsl_workspace_request(request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    operation = str(request["operation"])
    limit = min(int(request.get("max_results", 1)), 100)
    budget = max(128, min(8192, limit * 256))
    arguments: dict[str, Any] = {"max_output_tokens": budget}
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
    result: dict[str, Any], operation: str, mirror: Path, limit: int
) -> tuple[list[dict[str, Any]], set[str]]:
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        envelope = native_text_envelope(result)
        try:
            parsed = json.loads(envelope["text"])
        except json.JSONDecodeError:
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
        _versioned_structured_content(result)
    modalities = {
        str(row[key]).lower()
        for row in _nested_dicts(structured)
        for key in ("modality", "mode")
        if isinstance(row.get(key), str)
        and str(row[key]).lower() in {"l", "lexical", "s", "semantic", "h", "hybrid"}
    }
    items: list[dict[str, Any]] = []
    seen: set[bytes] = set()
    forbidden_text = {"text", "snippet", "body", "source", "content", "code"}
    for row in _nested_dicts(structured):
        item: dict[str, Any] | None = None
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
    request: dict[str, Any],
    timeout_seconds: int,
    cancelled: Callable[[], bool] | None = None,
    environment: dict[str, str] | None = None,
    owned_resource: Any | None = None,
) -> dict[str, Any]:
    operation = str(request["operation"])
    tool, arguments = _bsl_workspace_request(request)
    result = _bsl_mcp(
        executable, mirror, [(tool, arguments, False)], timeout_seconds, cancelled,
        environment,
        owned_resource,
    )[0]
    items, modalities = _bsl_workspace_items(
        result, operation, mirror, int(request.get("max_results", 1))
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
        "truncated": len(items) >= int(request.get("max_results", 1)),
        "modalities": sorted(modalities),
    }


def query_backend(
    repo: Path,
    decision: dict[str, Any],
    request: dict[str, Any],
    *,
    state_root: Path | None = None,
    timeout_seconds: int = ADAPTER_TIMEOUT_SECONDS,
    cancelled: Callable[[], bool] | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    adapter_id = decision["selected_backend_id"]
    component = next(
        item
        for item in discover(repo)
        if item["component_id"] == request["component_id"]
    )
    backend = next(
        item for item in load_config(repo)["backends"]
        if item["adapter_id"] == adapter_id
    )
    modality = (
        "hybrid"
        if request.get("operation") == "code.search_hybrid"
        else "lexical"
    )
    ready = ready_backend_state(
        repo, component, backend, state_root=state_root, modality=modality
    )
    if ready is None:
        raise RuntimeError("source_search.index_stale")
    if ready["index_fingerprint"] != decision["state"]["index_fingerprint"]:
        raise RuntimeError("source_search.index_stale")
    executable = backend_executable(repo, adapter_id)
    if not executable:
        raise RuntimeError("source_search.backend_unavailable")
    mirror = Path(ready["instance_path"])
    query = str(request.get("query", ""))
    limit = int(request["max_results"])
    if adapter_id == "bsl-analyzer":
        if str(request["operation"]).startswith(
            ("code.", "symbol.", "graph.", "metadata.", "diagnostics.")
        ):
            operation = str(request["operation"])
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
            profile, _secret = search_services._private_profile(
                repo, profile_id, state_root
            )
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
            environment["ONE_C_EMBEDDING_IDENTITY"] = profile[
                "semantic_identity"
            ]
            result = _bsl_workspace_query(
                executable, mirror, request, timeout_seconds, cancelled,
                environment, broker,
            )
            result["embedding_identity"] = profile["semantic_identity"]
            return result
        return _bsl_hits(
            executable,
            mirror,
            str(request["operation"]),
            query,
            limit,
            timeout_seconds,
            cancelled,
        )
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
        index_dir=Path(ready["index_dir"]) if ready.get("index_dir") else None,
    )
    if cancelled and cancelled():
        raise InterruptedError("source search was cancelled")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("rlm-tools-bsl returned invalid JSON") from exc
    if result.returncode or payload.get("status") != "available":
        raise RuntimeError("source_search.backend_query_failed")
    kind_by_operation = {
        "search_text": "text",
        "find_symbol": "symbol",
        "find_references": "reference",
        "find_callers": "caller",
        "find_callees": "callee",
        "navigate_metadata": "metadata",
    }
    hits = []
    for row in payload.get("candidates", [])[:limit]:
        if not isinstance(row, dict) or not isinstance(row.get("relativePath"), str):
            raise RuntimeError("source_search.backend_result_invalid")
        hit: dict[str, Any] = {
            "component_relative_path": row["relativePath"],
            "kind": kind_by_operation[str(request["operation"])],
        }
        if isinstance(row.get("startLine"), int) and row["startLine"] >= 0:
            hit["line"] = row["startLine"] + 1
        if isinstance(row.get("symbolName"), str) and row["symbolName"]:
            hit["symbol"] = row["symbolName"]
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
    timeout_seconds: int = 1800,
    cancelled: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    if rebuild and not confirmed:
        raise ValueError("index rebuild requires explicit confirmation")
    components = {item["component_id"]: item for item in discover(repo)}
    backends = {item["adapter_id"]: item for item in load_config(repo)["backends"]}
    selected_components = sorted(component_ids or components)
    selected_backends = list(backend_ids or backends)
    if set(selected_components) - set(components) or set(selected_backends) - set(backends):
        raise ValueError("unknown index component or backend selection")
    results: list[dict[str, Any]] = []
    for component_id in selected_components:
        component = components[component_id]
        if component["bsl_file_count"] == 0:
            results.extend(
                {
                    "component_id": component_id,
                    "adapter_id": adapter_id,
                    "status": "not_indexable",
                }
                for adapter_id in selected_backends
            )
            continue
        for adapter_id in selected_backends:
            backend = backends[adapter_id]
            modalities = (
                ("lexical", "hybrid")
                if backend["adapter_id"] == "bsl-analyzer"
                and load_config(repo)["source_schema_version"] == "3"
                else ("lexical",)
            )
            for modality in modalities:
                ready = ready_backend_state(
                    repo, component, backend, state_root=state_root,
                    modality=modality,
                )
                if ready and not rebuild:
                    results.append({
                        "component_id": component_id,
                        "adapter_id": adapter_id,
                        **({"modality": modality} if len(modalities) > 1 else {}),
                        **ready,
                    })
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
    if (
        load_config(repo)["source_schema_version"] == "3"
        and "bsl-analyzer" in selected_backends
    ):
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
) -> list[dict[str, Any]]:
    components = {item["component_id"]: item for item in discover(repo)}
    backends = {item["adapter_id"]: item for item in load_config(repo)["backends"]}
    selected_components = sorted(component_ids or components)
    selected_backends = list(backend_ids or backends)
    if set(selected_components) - set(components) or set(selected_backends) - set(backends):
        raise ValueError("unknown index component or backend selection")
    validated_at = datetime.now(timezone.utc).isoformat()
    results: list[dict[str, Any]] = []
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
                and load_config(repo)["source_schema_version"] == "3"
                else ("lexical",)
            )
            for modality in modalities:
                ready = ready_backend_state(
                    repo, component, backend, state_root=state_root,
                    modality=modality,
                )
                results.append({
                    "component_id": component_id,
                    "adapter_id": adapter_id,
                    **({"modality": modality} if len(modalities) > 1 else {}),
                    "status": "ready" if ready else "not_ready",
                    "index_fingerprint": ready["index_fingerprint"] if ready else "",
                    "validated_at": validated_at,
                })
    if (
        load_config(repo)["source_schema_version"] == "3"
        and "bsl-analyzer" in selected_backends
    ):
        results.append(reference_index_status(
            repo, backends["bsl-analyzer"], state_root=state_root
        ))
    return results


def _reference_index_target(
    repo: Path,
    backend: dict[str, Any],
    state_root: Path | None = None,
) -> tuple[Path, str, dict[str, Any], Path]:
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


def reference_index_status(
    repo: Path,
    backend: dict[str, Any],
    *,
    state_root: Path | None = None,
) -> dict[str, Any]:
    from . import reference_search
    try:
        _executable, identity, probe, root = _reference_index_target(
            repo, backend, state_root
        )
        reference_search.current_reference_index(
            root, probe["executable_fingerprint"]
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
    backend: dict[str, Any],
    *,
    state_root: Path | None = None,
    rebuild: bool = False,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    from . import reference_search
    current = reference_index_status(repo, backend, state_root=state_root)
    if current["status"] == "ready" and not rebuild:
        return current
    staging: Path | None = None
    try:
        executable, identity, probe, root = _reference_index_target(
            repo, backend, state_root
        )
        corpus = {
            "source": "selected-build-bundled",
            "corpus_fingerprint": "sha256:" + sha256(canonical_json({
                "source": "selected-build-bundled",
                "executable_fingerprint": probe["executable_fingerprint"],
            })),
            "build_version": probe["engine_version"],
        }
        staging = reference_search.stage_reference_index(
            root, probe["executable_fingerprint"], corpus
        )
        reference_search.execute_reference(
            executable,
            staging,
            {
                "operation": "reference.find_docs",
                "query": "Процедура",
                "max_results": 1,
            },
            cancelled=cancelled,
        )
        reference_search.promote_reference_index(staging, {
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


def statuses(repo: Path, state_root: Path | None = None, probe: Callable[[Path], dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    state_root = state_root or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "one-c-autoresearch/indexes"
    prior_states = []
    for path in state_root.glob("*/state.json") if state_root.is_dir() else []:
        try:
            prior_states.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    rows = []
    for component in discover(repo):
        key = index_key(repo, component); state = state_root / key / "state.json"
        saved: dict[str, Any] = {}
        if state.is_file():
            try:
                saved = json.loads(state.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
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
            status = saved.get("status", "failed") if saved.get("index_key") == key else "stale"
        rows.append({**component, "index_key": key, "status": status, "last_validation": saved.get("last_validation"), "result": saved.get("result")})
    return rows


def backend_statuses(repo: Path, state_root: Path | None = None) -> list[dict[str, Any]]:
    config = load_config(repo)
    rows: list[dict[str, Any]] = []
    for backend in config["backends"]:
        adapter_id = backend["adapter_id"]
        backend_probe = probe_backend(repo, backend)
        for component in discover(repo):
            modalities = (
                ("lexical", "hybrid")
                if adapter_id == "bsl-analyzer"
                and config["source_schema_version"] == "3"
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
                )
                status = (
                    "ready" if promoted else
                    "unavailable" if not backend_probe["available"] else "missing"
                )
                index_fingerprint = promoted["index_fingerprint"] if promoted else ""
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
                rows.append({
                    **component,
                    "adapter_id": adapter_id,
                    **({"modality": modality} if len(modalities) > 1 else {}),
                    "adapter_version": BACKEND_CATALOG[adapter_id]["adapter_version"],
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
                })
    return rows


def ensure(repo: Path, builder: Callable[[Path, str], dict[str, Any]], *, selected: list[str] | None = None, rebuild: bool = False, confirmed: bool = False, state_root: Path | None = None, probe: Callable[[Path], dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    pointer = json.loads((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    if pointer.get("schema_version") == "1":
        raise ValueError("new index builds require routed source schema version 2")
    if rebuild and not confirmed:
        raise ValueError("index rebuild requires explicit confirmation")
    state_root = state_root or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "one-c-autoresearch/indexes"
    available = {item["component_id"]: item for item in statuses(repo, state_root, probe)}
    wanted = sorted(selected or available)
    if set(wanted) - set(available):
        raise ValueError("unknown component selection")
    results = []
    source_root = repo / "sources/generations" / json.loads((repo / "research/active-source-generation.json").read_text())["generation_id"]
    for component_id in wanted:
        item = available[component_id]
        if item["status"] == "not_indexable" or item["status"] == "ready" and not rebuild:
            results.append(item); continue
        state_path = state_root / item["index_key"] / "state.json"
        identity = {key: item[key] for key in ("component_id", "path", "fingerprint", "representation", "source_generation_id", "engine", "engine_version")}
        atomic_json(state_path, {"schema_version": "1", "index_key": item["index_key"], "status": "building", **identity})
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
