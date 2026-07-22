from __future__ import annotations

import json
import os
import subprocess
import shutil
from functools import lru_cache
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .contracts import ROLES, atomic_json, canonical_json, confined, file_manifest, normalize_relative, sha256


def discover_executable(repo: Path) -> str | None:
    found = shutil.which("rlm-bsl-index")
    sibling = repo.resolve().parent / "rlm-tools-bsl/.venv/bin/rlm-bsl-index"
    return found or (str(sibling) if sibling.is_file() and os.access(sibling, os.X_OK) else None)


@lru_cache(maxsize=64)
def _component_fingerprint(path: str) -> str:
    # ponytail: source-generation paths are immutable; restart invalidates this cheap process cache.
    return "sha256:" + sha256(canonical_json(file_manifest(Path(path))))


def cli_version(executable: str) -> str:
    result = subprocess.run([executable, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, env={"PATH": os.environ.get("PATH", "")})
    import re
    match = re.fullmatch(r"rlm-bsl-index\s+(\d+\.\d+\.\d+)\s*", result.stdout)
    if result.returncode or not match:
        raise RuntimeError("cannot determine exact rlm-bsl-index version")
    return match.group(1)


def validate_engine_version(repo: Path, executable: str) -> str:
    configured = str(tomllib.loads((repo / "research/indexing.toml").read_text(encoding="utf-8"))["engine_version"])
    actual = cli_version(executable)
    if actual != configured:
        raise RuntimeError(f"rlm-bsl-index version mismatch: required {configured}, found {actual}")
    return actual


def discover(repo: Path) -> list[dict[str, Any]]:
    pointer = json.loads((repo / "research/active-source-generation.json").read_text(encoding="utf-8"))
    generation = str(pointer["generation_id"])
    root = confined(repo / "sources/generations", generation)
    config = tomllib.loads((repo / "research/indexing.toml").read_text(encoding="utf-8"))
    if pointer.get("representation_schema") not in {"xml-hierarchical", "v8unpack", "edt-project"}:
        raise ValueError(f"unsupported source representation for indexing: {pointer.get('representation_schema')}")
    result: list[dict[str, Any]] = []
    for role in ROLES:
        role_root = confined(root, role)
        candidates = [(f"{role}:configuration", role_root / "configuration")]
        extensions = role_root / "extensions"
        if extensions.is_dir():
            candidates.extend((f"{role}:extension:{item.name.lower()}", item) for item in extensions.iterdir() if item.is_dir())
        external = role_root / "external"
        if external.is_dir():
            candidates.extend((f"{role}:external:{item.name}", item / "source") for item in external.iterdir() if item.is_dir())
        for component_id, component_root in sorted(candidates):
            if not component_root.is_dir():
                continue
            relative = component_root.relative_to(root).as_posix()
            bsl_count = sum(1 for path in component_root.rglob("*.bsl") if path.is_file())
            result.append({
                "component_id": component_id,
                "path": relative,
                "fingerprint": _component_fingerprint(str(component_root.resolve())),
                "representation": pointer["representation_schema"],
                "source_generation_id": generation,
                "engine": config["engine"],
                "engine_version": str(config["engine_version"]),
                "bsl_file_count": bsl_count,
            })
    return sorted(result, key=lambda item: item["component_id"])


def index_key(repo: Path, component: dict[str, Any]) -> str:
    project_id = tomllib.loads((repo / "project.toml").read_text(encoding="utf-8"))["project"]["id"]
    preimage = {key: component[key] for key in ("component_id", "path", "fingerprint", "representation", "source_generation_id", "engine", "engine_version")}
    preimage["repository"] = f"{project_id}:{repo.resolve()}"
    return sha256(canonical_json(preimage))


def required_component_ids(repo: Path, paths: list[str], roles: tuple[str, ...]) -> list[str]:
    available = {item["component_id"] for item in discover(repo)}
    result = set()
    for value in paths:
        parts = value.replace("\\", "/").split("/")
        if parts[0] == "configuration":
            suffix = "configuration"
        elif len(parts) >= 2 and parts[0] == "extensions":
            suffix = f"extension:{parts[1].lower()}"
        elif len(parts) >= 2 and parts[0] == "external":
            suffix = f"external:{parts[1]}"
        else:
            raise ValueError(f"source evidence path does not identify one component: {value}")
        result.update(f"{role}:{suffix}" for role in roles if f"{role}:{suffix}" in available)
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
    result = subprocess.run([executable, "index", "info", str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, env={"PATH": os.environ.get("PATH", "")})
    output = result.stdout[-64 * 1024:]
    return {"ready": result.returncode == 0 and "Status:   fresh" in output, "exit_code": result.returncode, "output": output}


def cli_build(executable: str, path: Path, _component_id: str, timeout_seconds: int = 1800) -> dict[str, Any]:
    result = subprocess.run([executable, "index", "build", str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", "")})
    probe = cli_probe(executable, path)
    return {"ready": result.returncode == 0 and probe["ready"], "exit_code": result.returncode, "output": result.stdout[-64 * 1024:]}


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
            status = "stale" if any(saved.get("component_id") == component["component_id"] for saved in prior_states) else "missing"
        else:
            status = saved.get("status", "failed") if saved.get("index_key") == key else "stale"
        rows.append({**component, "index_key": key, "status": status, "last_validation": saved.get("last_validation"), "result": saved.get("result")})
    return rows


def ensure(repo: Path, builder: Callable[[Path, str], dict[str, Any]], *, selected: list[str] | None = None, rebuild: bool = False, confirmed: bool = False, state_root: Path | None = None, probe: Callable[[Path], dict[str, Any]] | None = None) -> list[dict[str, Any]]:
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
