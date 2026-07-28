#!/usr/bin/env python3
"""Build the deterministic portable research repository archive."""
from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "templates" / "research-repo"
OUTPUT = ROOT / "dist" / "research-template.zip"
IGNORED = {"__pycache__", ".pytest_cache", ".venv", "node_modules", "test-results", ".git"}


def members() -> list[Path]:
    manifest = json.loads((SOURCE / "research/runtime-sync-manifest.json").read_text(encoding="utf-8"))["paths"]
    paths = sorted(
        (path for path in SOURCE.rglob("*") if path.is_file() and not (set(path.relative_to(SOURCE).parts) & IGNORED)),
        key=lambda path: path.relative_to(SOURCE).as_posix(),
    )
    actual = [path.relative_to(SOURCE).as_posix() for path in paths if path.relative_to(SOURCE).as_posix() != "research/runtime-sync-manifest.json"]
    expected = [path for path in manifest if path != "research/runtime-sync-manifest.json"]
    if actual != expected:
        raise RuntimeError("template manifest does not match the portable repository payload")
    return paths


def build() -> Path:
    entries = members()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    (OUTPUT.parent / ".gitignore").unlink(missing_ok=True)
    with tempfile.NamedTemporaryFile(dir=OUTPUT.parent, prefix=".research-template-", suffix=".zip", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in entries:
                info = zipfile.ZipInfo(path.relative_to(SOURCE).as_posix(), (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o755 if path.stat().st_mode & 0o111 else 0o644) << 16
                archive.writestr(info, path.read_bytes())
        temporary.replace(OUTPUT)
    finally:
        temporary.unlink(missing_ok=True)
    return OUTPUT


if __name__ == "__main__":
    print(build())
