from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


TEXT_EXTENSIONS = {".md", ".toml", ".jsonl", ".py", ".txt", ".csv", ".gitignore"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def repo_path(root: Path, relative: str) -> Path:
    parts = [part for part in re.split(r"[\\/]+", relative) if part]
    return root.joinpath(*parts)


def rel_id(relative: str) -> str:
    return "/".join(part for part in re.split(r"[\\/]+", relative) if part)


def read_toml(path: Path) -> dict[str, Any]:
    if tomllib is None:
        raise RuntimeError("Python 3.11+ is required for tomllib support")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def toml_value(manifest: dict[str, Any], section: str, key: str) -> str:
    value = manifest.get(section, {}).get(key, "")
    return "" if value is None else str(value)


def toml_enabled(manifest: dict[str, Any], section: str) -> bool:
    return toml_value(manifest, section, "enabled").strip().lower() == "true"


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def read_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    tasks: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8-sig") as fh:
        for line_number, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            tasks.append((line_number, json.loads(line)))
    return tasks


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_json(path: Path, payload: Any, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        if mode is not None:
            path.chmod(mode)
    finally:
        if tmp.exists():
            tmp.unlink()


def sha256(value: Any) -> str:
    data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()) if path.is_file() else ""


def path_sha256(path: Path) -> str:
    if path.is_file():
        return file_sha256(path)
    if path.is_dir():
        return sha256([(item.relative_to(path).as_posix(), file_sha256(item)) for item in sorted(path.rglob("*")) if item.is_file()])
    return ""


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def run_git(root: Path, args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.quotePath=false", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=check,
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


@dataclass
class CheckSet:
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, check_id: str, status: str, message: str, details: Any | None = None) -> None:
        check: dict[str, Any] = {"id": check_id, "status": status, "message": message}
        if details is not None:
            check["details"] = details
        self.checks.append(check)

    def counts(self) -> tuple[int, int, int]:
        ok = sum(1 for check in self.checks if check["status"] == "ok")
        warn = sum(1 for check in self.checks if check["status"] == "warn")
        fail = sum(1 for check in self.checks if check["status"] == "fail")
        return ok, warn, fail


def copy_tree_contents(source: Path, target: Path, force: bool = False) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        destination = target / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=force)
        else:
            if destination.exists() and not force:
                raise FileExistsError(f"Target file already exists: {destination}")
            shutil.copy2(child, destination)


def git_check_ignored(root: Path, relative: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", "--", relative],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def current_module_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "one_c_autoresearch", *args]
