from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

from .contracts import JsonValue, SECRET_KEYS, parse_json_object
from .workflow import status, validate_project_contract, validate_workflow
from .sources import validate_active


def packaged_secret_failure(relative: str, path: Path) -> bool:
    _ = relative
    name = path.name.lower()
    candidate = name == ".env" or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"} or any(word in name for word in ("credential", "secrets"))
    if not candidate or not path.is_file() or path.stat().st_size > 2_000_000:
        return False
    raw = path.read_bytes()
    return b"PRIVATE KEY-----" in raw.upper() or re.search(br"(?i)['\"]?\b(?:password|passwd|pwd|token|secret)\b['\"]?\s*[:=]\s*['\"]?[^\s'\";]{8,}", raw) is not None


def _strings(manifest: dict[str, JsonValue], key: str) -> list[str]:
    values = manifest.get(key, [])
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError(f"invalid forbidden-authorities field: {key}")
    return [value for value in values if isinstance(value, str)]


def legacy_failures(repo: Path, manifest: dict[str, JsonValue], tracked: list[str]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for relative in tracked:
        path = repo / relative
        if not path.exists():
            continue
        if relative in _strings(manifest, "exact_paths") or any(relative.startswith(prefix) for prefix in _strings(manifest, "path_prefixes")):
            failures.append({"code": "legacy.path", "path": relative, "message": "forbidden legacy authority"})
        if relative.startswith("src/one_c_autoresearch/") and Path(relative).name in _strings(manifest, "modules"):
            failures.append({"code": "legacy.module", "path": relative, "message": "forbidden legacy writer/reader"})
        if relative.startswith("openspec/") or not path.is_file() or path.stat().st_size > 2_000_000 or path.suffix.lower() not in {".py", ".tsx", ".ts"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        command_tokens = _strings(manifest, "command_tokens") if relative.endswith("/cli.py") else []
        for token in command_tokens:
            if f'add_parser("{token}")' in text or f"add_parser('{token}')" in text:
                failures.append({"code": "legacy.command-token", "path": relative, "message": f"forbidden command token: {token}"})
        for token in _strings(manifest, "route_tokens") + _strings(manifest, "registration_tokens"):
            if any(literal in text for literal in (f'"{token}"', f"'{token}'", f'/{token}')):
                failures.append({"code": "legacy.command-token", "path": relative, "message": f"forbidden command token: {token}"})
    return failures


def check(repo: Path, strict: bool = False) -> dict[str, object]:
    repo = repo.resolve(); failures: list[dict[str, str]] = []; warnings: list[dict[str, str]] = []
    required = ("project.toml", "research/workflow.toml", "research/infobases.toml", "research/external-artifacts.toml", "research/indexing.toml", "research/forbidden-authorities.json", "research/active-consolidation-generation.json")
    missing = [path for path in required if not (repo / path).is_file()]
    if missing:
        failure = {"code": "contract.unsupported", "path": missing[0], "message": "unsupported repository contract; recreate the repository instead of migrating it"}
        return {"ok": False, "strict": strict, "failures": [failure], "warnings": [], "snapshot": None, "summary": {"fail": 1, "warn": 0}}
    manifest = parse_json_object((repo / "research/forbidden-authorities.json").read_text(encoding="utf-8"))
    project = parse_json_object(json.dumps(tomllib.loads((repo / "project.toml").read_text(encoding="utf-8"))))
    for section in _strings(manifest, "project_sections"):
        if section in project: failures.append({"code": "legacy.project-section", "path": "project.toml", "message": f"forbidden section [{section}]"})
    try: _ = validate_project_contract(repo)
    except (ValueError, tomllib.TOMLDecodeError) as exc: failures.append({"code": "project.invalid", "path": "project.toml", "message": str(exc)})
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=repo, stdout=subprocess.PIPE, check=True).stdout.decode().split("\0")
    failures.extend(legacy_failures(repo, manifest, list(filter(None, tracked))))
    for relative in filter(None, tracked):
        path = repo / relative
        if not path.exists():
            continue
        if packaged_secret_failure(relative, path):
            failures.append({"code": "secret.packaged", "path": relative, "message": "tracked packaged credential or private key"})
        if relative.startswith("openspec/"): continue
        if path.is_file() and path.stat().st_size <= 2_000_000 and path.suffix.lower() in {".toml", ".json", ".yaml", ".yml"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            if SECRET_KEYS.search(text) and any(token in text.lower() for token in ('"password"', 'password =', '"token"', 'token =', 'private_key')):
                failures.append({"code": "secret.tracked", "path": relative, "message": "tracked secret-like field"})
    try: _ = validate_workflow(repo)
    except ValueError as exc: failures.append({"code": "workflow.invalid", "path": "research/workflow.toml", "message": str(exc)})
    try:
        from .consolidation import load_active as load_consolidation
        _ = load_consolidation(repo)
        if (repo / "research/active-dif-classification-generation.json").is_file():
            from .dif_classifications import load_active as load_classifications
            _ = load_classifications(repo)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        failures.append({"code": "generated-state.invalid", "path": "research/", "message": str(exc)})
    try: snapshot = status(repo)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        snapshot = None; failures.append({"code": "workflow.unreadable", "path": "research/", "message": str(exc)})
    if strict and snapshot and snapshot["state"] != "complete": failures.append({"code": "workflow.incomplete", "path": "research/", "message": "all eight gates must be complete for strict publication"})
    if strict:
        try:
            _ = validate_active(repo, deep=True, require_tracked_clean=True)
            from .diffs import validate_active as validate_active_diffs
            from .consolidation import load_active as active_consolidation
            from .contracts import require_tracked_clean
            _ = validate_active_diffs(repo, require_tracked_clean_state=True)
            _ = active_consolidation(repo)
            require_tracked_clean(repo, [repo / "outputs/projections.json"])
        except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc: failures.append({"code": "sources.not-publishable", "path": "sources/", "message": str(exc)})
    return {"ok": not failures, "strict": strict, "failures": failures, "warnings": warnings, "snapshot": snapshot, "summary": {"fail": len(failures), "warn": len(warnings)}}
