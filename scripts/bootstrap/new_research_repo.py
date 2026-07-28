#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "research-repo"
TEXT_SUFFIXES = {".md", ".toml", ".json", ".jsonl", ".yaml", ".yml", ".py", ".ts", ".tsx", ".html", ".css"}


def create(destination: Path, replacements: dict[str, str]) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError("destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATE, destination, dirs_exist_ok=True)
    for path in sorted(destination.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        for token, value in replacements.items():
            text = text.replace(token, value)
        path.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-path", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--baseline-version", required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--next-vendor-version", required=True)
    args = parser.parse_args()
    create(Path(args.target_path), {
        "__PROJECT_ID__": args.project_id,
        "__PRODUCT__": args.product,
        "__BASELINE_VERSION__": args.baseline_version,
        "__TARGET_VERSION__": args.target_version,
        "__NEXT_VENDOR_VERSION__": args.next_vendor_version,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
