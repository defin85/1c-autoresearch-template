#!/usr/bin/env python3
"""Build the deterministic research-template archive shipped with the workspace."""
from __future__ import annotations

import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src" / "one_c_autoresearch" / "workspace_assets" / "research-template.zip"
INCLUDE = (ROOT / "templates" / "research-repo", ROOT / "src", ROOT / "one_c_autoresearch")
FILES = (ROOT / "pyproject.toml", ROOT / "scripts" / "doctor.py", ROOT / "scripts" / "checks" / "test_research_repo.py")


def include(path: Path) -> bool:
    return not ({"__pycache__", ".pytest_cache", ".venv", "node_modules", "workspace_assets"} & set(path.parts)) and path.suffix not in {".pyc", ".pyo"}


def build() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    entries = [path for base in INCLUDE for path in base.rglob("*") if path.is_file() and include(path)] + list(FILES)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(set(entries), key=lambda item: item.relative_to(ROOT).as_posix()):
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if path.stat().st_mode & 0o111 else 0o644) << 16
            archive.writestr(info, path.read_bytes())


if __name__ == "__main__":
    build()
