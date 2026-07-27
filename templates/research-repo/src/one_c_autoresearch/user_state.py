from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import atomic_bytes, sha256


def workspace_id(repo: Path) -> str:
    return sha256(str(repo.resolve()).encode())[:16]


def state_root(base: Path | None = None) -> Path:
    return (base or (Path.home() / ".local/state/one-c-autoresearch")).resolve()


def connection_path(repo: Path, base: Path | None = None) -> Path:
    path = state_root(base) / "projects" / workspace_id(repo) / "connections.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def load_connections(repo: Path, base: Path | None = None) -> dict[str, dict[str, Any]]:
    path = connection_path(repo, base)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_connections(repo: Path, values: dict[str, dict[str, Any]], base: Path | None = None) -> None:
    path = connection_path(repo, base)
    atomic_bytes(path, json.dumps(values, ensure_ascii=False, sort_keys=True).encode())
    path.chmod(0o600)


def agent_profiles_path(repo: Path, base: Path | None = None) -> Path:
    path = state_root(base) / "projects" / workspace_id(repo) / "agent-profiles.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _validate_agent_profiles(values: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    from .agents import INSTRUCTION_CATALOG

    for name, profile in values.items():
        if "environment_preset" not in profile:
            raise ValueError(f"invalid user-scope agent profile: {name}; environment_preset is required, resave the profile")
        if not name or set(profile) != {"provider", "model", "reasoning_effort", "instructions_version", "environment_preset"} or profile["provider"] != "codex-cli" or profile["reasoning_effort"] not in {"low", "medium", "high", "xhigh", "max", "ultra"} or profile["instructions_version"] not in INSTRUCTION_CATALOG or profile["environment_preset"] != "local-read-only" or not str(profile["model"]).strip():
            raise ValueError(f"invalid user-scope agent profile: {name}")
    return values


def load_agent_profiles(repo: Path, base: Path | None = None) -> dict[str, dict[str, Any]]:
    path = agent_profiles_path(repo, base)
    return _validate_agent_profiles(json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {})


def save_agent_profiles(repo: Path, values: dict[str, dict[str, Any]], base: Path | None = None) -> None:
    _validate_agent_profiles(values)
    path = agent_profiles_path(repo, base)
    atomic_bytes(path, json.dumps(values, ensure_ascii=False, sort_keys=True).encode())
    path.chmod(0o600)


def replace_agent_profile(
    repo: Path,
    name: str,
    profile: dict[str, Any],
    base: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Перезаписывает один профиль, в том числе единственный старый профиль."""

    path = agent_profiles_path(repo, base)
    try:
        values = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid user-scope agent profile file; recreate it") from exc
    if not isinstance(values, dict):
        raise ValueError("invalid user-scope agent profile file; recreate it")
    values[name] = profile
    save_agent_profiles(repo, values, base)
    return values
