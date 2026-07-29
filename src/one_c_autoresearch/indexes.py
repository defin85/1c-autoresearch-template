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

from .contracts import ROLES, atomic_json, canonical_json, confined, file_manifest, normalize_relative, sha256

CAPABILITIES = (
    "text-search",
    "symbol-definition",
    "symbol-references",
    "callers",
    "callees",
    "metadata-navigation",
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
ADAPTER_TIMEOUT_SECONDS = 120


def _backend_environment(private_home: Path) -> dict[str, str]:
    private_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C.UTF-8",
        "HOME": str(private_home),
        "XDG_CONFIG_HOME": str(private_home / "config"),
        "XDG_CACHE_HOME": str(private_home / "cache"),
        "XDG_DATA_HOME": str(private_home / "data"),
        "XDG_STATE_HOME": str(private_home / "state"),
        "BSL_MCP_BROKER": "0",
    }


def _bounded_atomic_json(path: Path, value: dict[str, Any]) -> None:
    if len(canonical_json(value)) > ADAPTER_STATE_LIMIT:
        raise RuntimeError("index adapter state exceeds the byte limit")
    atomic_json(path, value)


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
    if version != "2" or set(raw) != {"schema_version", "backends", "routes"}:
        raise ValueError("invalid indexing schema version 2")
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
        if capability not in CAPABILITIES:
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
    return {"schema_version": "2", "source_schema_version": "2", "backends": backends, "routes": routes}


def serialize_config(config: dict[str, Any]) -> bytes:
    if config.get("schema_version") != "2":
        raise ValueError("only normalized indexing schema version 2 can be written")
    lines = ['schema_version = "2"', ""]
    for backend in config["backends"]:
        lines.extend((
            "[[backends]]",
            f'adapter_id = {json.dumps(backend["adapter_id"])}',
            f'engine_version = {json.dumps(backend["engine_version"])}',
            "",
        ))
    lines.append("[routes]")
    for capability in CAPABILITIES:
        if capability in config["routes"]:
            values = ", ".join(json.dumps(item) for item in config["routes"][capability])
            lines.append(f'{json.dumps(capability)} = [{values}]')
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
    }


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
    if capability not in CAPABILITIES:
        raise ValueError("unknown indexing capability")
    route = config["routes"].get(capability)
    if not route:
        raise RuntimeError("source_search.capability_unconfigured")
    by_backend = {
        str(item.get("adapter_id")): item
        for item in states
        if item.get("component_id") == component["component_id"]
    }
    skipped: list[dict[str, str]] = []
    for adapter_id in route:
        state = by_backend.get(adapter_id)
        if state is None or state.get("status") in {"missing", "stale", "failed", "unavailable"}:
            skipped.append({"adapter_id": adapter_id, "reason": "index_not_ready" if state else "backend_unavailable"})
            continue
        if capability not in state.get("capabilities", []):
            skipped.append({"adapter_id": adapter_id, "reason": "capability_unsupported"})
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
) -> subprocess.CompletedProcess[str]:
    if timeout_seconds <= 0 or timeout_seconds > 1800:
        raise ValueError("invalid adapter timeout")
    with tempfile.TemporaryDirectory(prefix="one-c-index-home-") as private_home, tempfile.TemporaryFile() as stdin:
        environment = _backend_environment(Path(private_home))
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
        major = str(contract["contract_version"]).split(".", 1)[0]
        workspace = contract["mcp"]["profiles"]["workspace"]["tools"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("bsl-analyzer returned an invalid machine contract") from exc
    if result.returncode or major != "1" or str(contract.get("build_version")) != configured_version:
        raise RuntimeError("bsl-analyzer contract or build version mismatch")
    surfaces = {
        str(tool.get("name")): {
            str(action.get("name"))
            for action in tool.get("actions", [])
            if isinstance(action, dict)
        }
        for tool in workspace
        if isinstance(tool, dict)
    }
    required = {
        "search": {"search_code", "status"},
        "graph": {"resolve", "callers", "callees", "status", "schema"},
        "metadata": {"tree", "object", "status"},
        "diagnostics": {"schema", "status"},
    }
    if "symbol_info" not in surfaces or any(not actions <= surfaces.get(tool, set()) for tool, actions in required.items()):
        raise RuntimeError("bsl-analyzer workspace contract lacks required surfaces")
    return contract


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
            capabilities = list(CAPABILITIES)
            contract_version = str(contract["contract_version"])
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
        }
    except Exception as exc:
        return {
            "available": False,
            "failure_code": "backend.contract_incompatible",
            "failure_summary": str(exc)[:500],
            "capabilities": [],
        }


@lru_cache(maxsize=64)
def _component_fingerprint(path: str) -> str:
    # ponytail: source-generation paths are immutable; restart invalidates this cheap process cache.
    return "sha256:" + sha256(canonical_json(file_manifest(Path(path))))


def cli_version(executable: str) -> str:
    result = _bounded_run([executable, "--version"])
    import re
    match = re.fullmatch(r"rlm-bsl-index\s+(\d+\.\d+\.\d+)\s*", result.stdout)
    if result.returncode or not match:
        raise RuntimeError("cannot determine exact rlm-bsl-index version")
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


def cli_probe(executable: str, path: Path) -> dict[str, Any]:
    result = _bounded_run([executable, "index", "info", str(path)])
    output = result.stdout
    return {"ready": result.returncode == 0 and "Status:   fresh" in output, "exit_code": result.returncode, "output": output}


def cli_build(executable: str, path: Path, _component_id: str, timeout_seconds: int = 1800) -> dict[str, Any]:
    result = _bounded_run(
        [executable, "index", "build", str(path)],
        timeout_seconds=timeout_seconds,
    )
    probe = cli_probe(executable, path)
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
        or not (
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
) -> list[dict[str, Any]]:
    command = [
        executable,
        "mcp",
        "serve",
        "--profile",
        "workspace",
        "--mode",
        "stdio",
        "--source-dir",
        str(source_dir),
    ]
    private_home = tempfile.TemporaryDirectory(prefix="one-c-bsl-home-")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=_backend_environment(Path(private_home.name)),
            start_new_session=True,
            bufsize=1,
        )
    except Exception:
        private_home.cleanup()
        raise
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("bsl-analyzer stdio is unavailable")
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
                structured = _versioned_structured_content(result)
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
        private_home.cleanup()


def _operational_path(repo: Path, state_root: Path | None = None) -> Path:
    base = state_root or Path(
        os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")
    ) / "one-c-autoresearch/indexes-v2"
    return base / repository_instance_fingerprint(repo).split(":", 1)[1]


def _operational_root(repo: Path, state_root: Path | None = None) -> Path:
    root = _operational_path(repo, state_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


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
) -> dict[str, Any]:
    probe = probe_backend(repo, backend)
    if not probe["available"]:
        raise RuntimeError(str(probe.get("failure_code")))
    identity = target_identity(
        repo,
        component,
        backend,
        probe["capabilities"],
        str(probe.get("executable_fingerprint", "")),
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
            outcome = _bounded_run(
                [executable, "index", "build", str(mirror)],
                timeout_seconds=timeout_seconds,
                cancelled=cancelled,
            )
            if outcome.returncode or not cli_probe(executable, mirror)["ready"]:
                raise RuntimeError("rlm-tools-bsl index build failed")
        else:
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
            )
        if cancelled and cancelled():
            raise InterruptedError("index build was cancelled")
        if file_manifest(source_root) != source_manifest:
            raise RuntimeError("canonical source changed during index build")
        mirror_manifest = file_manifest(mirror)
        source_paths = {row["path"] for row in source_manifest}
        if [item for item in mirror_manifest if item["path"] in source_paths] != source_manifest:
            raise RuntimeError("index adapter changed the private source mirror")
        index_manifest = [item for item in mirror_manifest if item["path"] not in source_paths]
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
) -> dict[str, Any] | None:
    probe = probe_backend(repo, backend)
    if not probe["available"]:
        return None
    identity = target_identity(
        repo,
        component,
        backend,
        probe["capabilities"],
        str(probe.get("executable_fingerprint", "")),
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
        return {
            "status": "ready",
            "instance_path": str(mirror),
            "index_fingerprint": current["index_fingerprint"],
            "target_fingerprint": target_fingerprint(identity),
            "last_validation": saved.get("last_validation"),
            "capabilities": probe["capabilities"],
            "contract_version": probe["contract_version"],
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


def query_backend(
    repo: Path,
    decision: dict[str, Any],
    request: dict[str, Any],
    *,
    state_root: Path | None = None,
    timeout_seconds: int = ADAPTER_TIMEOUT_SECONDS,
    cancelled: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
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
    ready = ready_backend_state(repo, component, backend, state_root=state_root)
    if ready is None:
        raise RuntimeError("source_search.index_stale")
    if ready["index_fingerprint"] != decision["state"]["index_fingerprint"]:
        raise RuntimeError("source_search.index_stale")
    executable = backend_executable(repo, adapter_id)
    if not executable:
        raise RuntimeError("source_search.backend_unavailable")
    mirror = Path(ready["instance_path"])
    query = str(request["query"])
    limit = int(request["max_results"])
    if adapter_id == "bsl-analyzer":
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
            ready = ready_backend_state(repo, component, backend, state_root=state_root)
            if ready and not rebuild:
                results.append({
                    "component_id": component_id,
                    "adapter_id": adapter_id,
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
            ready = ready_backend_state(
                repo,
                component,
                backends[adapter_id],
                state_root=state_root,
            )
            results.append({
                "component_id": component_id,
                "adapter_id": adapter_id,
                "status": "ready" if ready else "not_ready",
                "index_fingerprint": ready["index_fingerprint"] if ready else "",
                "validated_at": validated_at,
            })
    return results


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
            capabilities = list(backend_probe.get("capabilities", []))
            promoted = ready_backend_state(repo, component, backend, state_root=state_root)
            if promoted:
                status = "ready"
                index_fingerprint = promoted["index_fingerprint"]
                readiness_reason = None
                recovery_action = None
            else:
                status = "unavailable" if not backend_probe["available"] else "missing"
                index_fingerprint = ""
                if status == "unavailable":
                    readiness_reason = str(
                        backend_probe.get("failure_code", "backend.unavailable")
                    )
                    recovery_action = "fix_backend_installation"
                else:
                    identity = target_identity(
                        repo,
                        component,
                        backend,
                        capabilities,
                        str(backend_probe.get("executable_fingerprint", "")),
                    )
                    target_root = _operational_path(
                        repo, state_root,
                    ) / "targets" / target_fingerprint(identity).split(":", 1)[1]
                    readiness_reason = (
                        "index.stale"
                        if (target_root / "current.json").is_file()
                        else "index.missing"
                    )
                    recovery_action = (
                        "rebuild_index"
                        if readiness_reason == "index.stale"
                        else "ensure_index"
                    )
            rows.append({
                **component,
                "adapter_id": adapter_id,
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
