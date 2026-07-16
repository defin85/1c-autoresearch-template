from pathlib import Path

from one_c_autoresearch.common import read_toml


def settings(root: Path | None = None) -> dict:
    root = (root or Path.cwd()).resolve()
    manifest = read_toml(root / "project.toml") if (root / "project.toml").is_file() else {}
    value = manifest.get("physical_cleanup") or {}
    return value if isinstance(value, dict) else {}


def path(name: str, default: str) -> Path:
    return Path(str(settings().get(name) or default))


def ref(name: str, default: str) -> str:
    return str(settings().get(name) or default)
