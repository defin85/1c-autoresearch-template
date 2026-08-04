from __future__ import annotations

import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from .contracts import JsonValue, atomic_bytes, parse_json_object, sha256, validate_source_search_profile


def _validate_profile_policy(value: object) -> None:
    validate_source_search_profile(value)


@lru_cache(maxsize=1)
def codex_model_capabilities() -> dict[str, dict[str, JsonValue]]:
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("codex executable is unavailable")
    result = subprocess.run(
        [executable, "debug", "models"], capture_output=True, check=True,
        text=True, timeout=15,
    )
    catalog = parse_json_object(result.stdout)
    models = catalog.get("models")
    if not isinstance(models, list):
        return {}
    capabilities: dict[str, dict[str, JsonValue]] = {}
    for item in models:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        context_window = item.get("context_window")
        if (
            not isinstance(slug, str)
            or item.get("visibility") != "list"
            or item.get("supported_in_api", True) is not True
            or not isinstance(context_window, int)
            or isinstance(context_window, bool)
            or context_window <= 0
        ):
            continue
        capabilities[slug] = {
            "input_context_tokens": context_window,
            "context_estimator_version": "utf8-v1",
            "capability_fingerprint": "sha256:" + sha256(json.dumps({
                "provider": "codex-cli",
                "model": slug,
                "context_window": context_window,
                "max_context_window": item.get("max_context_window"),
                "effective_context_window_percent": item.get("effective_context_window_percent"),
                "comp_hash": item.get("comp_hash"),
            }, sort_keys=True, separators=(",", ":")).encode()),
        }
    return capabilities


def enrich_agent_profile(profile: dict[str, JsonValue]) -> dict[str, JsonValue]:
    capability = codex_model_capabilities().get(str(profile.get("model", "")))
    if capability is None:
        return profile
    return {**profile, **capability}


def workspace_id(repo: Path) -> str:
    return sha256(str(repo.resolve()).encode())[:16]


def state_root(base: Path | None = None) -> Path:
    return (base or (Path.home() / ".local/state/one-c-autoresearch")).resolve()


def connection_path(repo: Path, base: Path | None = None) -> Path:
    path = state_root(base) / "projects" / workspace_id(repo) / "connections.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def load_connections(repo: Path, base: Path | None = None) -> dict[str, dict[str, JsonValue]]:
    path = connection_path(repo, base)
    if not path.is_file():
        return {}
    values = parse_json_object(path.read_text(encoding="utf-8"))
    if not all(isinstance(value, dict) for value in values.values()):
        raise ValueError("invalid connection file")
    return {name: value for name, value in values.items() if isinstance(value, dict)}


def save_connections(repo: Path, values: dict[str, dict[str, JsonValue]], base: Path | None = None) -> None:
    path = connection_path(repo, base)
    atomic_bytes(path, json.dumps(values, ensure_ascii=False, sort_keys=True).encode())
    path.chmod(0o600)


def agent_profiles_path(repo: Path, base: Path | None = None) -> Path:
    path = state_root(base) / "projects" / workspace_id(repo) / "agent-profiles.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _validate_agent_profiles(values: dict[str, dict[str, JsonValue]]) -> dict[str, dict[str, JsonValue]]:
    from .agents import INSTRUCTION_CATALOG

    for name, profile in values.items():
        if "environment_preset" not in profile:
            raise ValueError(f"invalid user-scope agent profile: {name}; environment_preset is required, resave the profile")
        required = {"provider", "model", "reasoning_effort", "instructions_version", "environment_preset"}
        capability = {"input_context_tokens", "context_estimator_version", "capability_fingerprint"}
        optional = {"source_search"}
        if not name or not required <= set(profile) or set(profile) - required - capability - optional or profile["provider"] != "codex-cli" or profile["reasoning_effort"] not in {"low", "medium", "high", "xhigh", "max", "ultra"} or profile["instructions_version"] not in INSTRUCTION_CATALOG or profile["environment_preset"] != "local-read-only" or not str(profile["model"]).strip():
            raise ValueError(f"invalid user-scope agent profile: {name}")
        if "source_search" in profile:
            _validate_profile_policy(profile["source_search"])
    return values


def load_agent_profiles(repo: Path, base: Path | None = None) -> dict[str, dict[str, JsonValue]]:
    path = agent_profiles_path(repo, base)
    raw = parse_json_object(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not all(isinstance(value, dict) for value in raw.values()):
        raise ValueError("invalid user-scope agent profile file")
    values = _validate_agent_profiles({name: value for name, value in raw.items() if isinstance(value, dict)})
    return {name: enrich_agent_profile(profile) for name, profile in values.items()}


def save_agent_profiles(repo: Path, values: dict[str, dict[str, JsonValue]], base: Path | None = None) -> None:
    _ = _validate_agent_profiles(values)
    path = agent_profiles_path(repo, base)
    atomic_bytes(path, json.dumps(values, ensure_ascii=False, sort_keys=True).encode())
    path.chmod(0o600)


def replace_agent_profile(
    repo: Path,
    name: str,
    profile: dict[str, JsonValue],
    base: Path | None = None,
) -> dict[str, dict[str, JsonValue]]:
    """Перезаписывает один профиль, в том числе единственный старый профиль."""

    path = agent_profiles_path(repo, base)
    try:
        raw = parse_json_object(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid user-scope agent profile file; recreate it") from exc
    if not all(isinstance(value, dict) for value in raw.values()):
        raise ValueError("invalid user-scope agent profile file; recreate it")
    values = {key: value for key, value in raw.items() if isinstance(value, dict)}
    values[name] = enrich_agent_profile(profile)
    save_agent_profiles(repo, values, base)
    return values
