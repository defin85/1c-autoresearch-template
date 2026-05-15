from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from .common import copy_tree_contents, utc_now_iso


TEXT_EXTENSIONS = {".md", ".toml", ".jsonl", ".py", ".gitignore"}


def forward_slash(value: str | None) -> str:
    return "" if not value else value.replace("\\", "/")


def replace_in_text_file(path: Path, replacements: dict[str, str]) -> None:
    content = path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        content = content.replace(key, value)
    path.write_text(content, encoding="utf-8", newline="\n")


def create_research_repo(args: argparse.Namespace) -> int:
    template_root = Path(args.template_root).resolve() if args.template_root else Path(__file__).resolve().parents[2]
    source = template_root / "templates" / "research-repo"
    if not source.exists():
        raise FileNotFoundError(f"Template source not found: {source}")
    target = Path(args.target_path).resolve()
    if target.exists() and any(target.iterdir()) and not args.force:
        raise RuntimeError(f"Target directory exists and is not empty. Use --force only when you intentionally want to merge template files: {target}")
    target.mkdir(parents=True, exist_ok=True)

    copy_tree_contents(source, target, force=args.force)
    shutil.copytree(template_root / "src", target / "src", dirs_exist_ok=True)
    shutil.copytree(template_root / "one_c_autoresearch", target / "one_c_autoresearch", dirs_exist_ok=True)
    shutil.copy2(template_root / "pyproject.toml", target / "pyproject.toml")
    shutil.copy2(template_root / "scripts" / "doctor.py", target / "scripts" / "doctor.py")
    shutil.copy2(template_root / "scripts" / "checks" / "test_research_repo.py", target / "scripts" / "checks" / "test_research_repo.py")

    replacements = {
        "__PROJECT_ID__": args.project_id,
        "__PRODUCT__": args.product,
        "__BASELINE_VERSION__": args.baseline_version,
        "__TARGET_VERSION__": args.target_version,
        "__NEXT_VENDOR_VERSION__": args.next_vendor_version,
        "__VENDOR_BASELINE__": forward_slash(args.vendor_baseline),
        "__TARGET_CF__": forward_slash(args.target_cf),
        "__TARGET_CFE__": forward_slash(args.target_cfe),
        "__NEXT_VENDOR__": forward_slash(args.next_vendor),
        "__RLM_VENDOR_BASELINE__": args.rlm_vendor_baseline,
        "__RLM_TARGET_CF__": args.rlm_target_cf,
        "__RLM_TARGET_CFE__": args.rlm_target_cfe,
        "__RLM_NEXT_VENDOR__": args.rlm_next_vendor,
        "__CREATED_AT__": utc_now_iso(),
    }
    for path in target.rglob("*"):
        if path.is_file() and (path.suffix in TEXT_EXTENSIONS or path.name == ".gitignore"):
            replace_in_text_file(path, replacements)

    if args.init_git:
        subprocess.run(["git", "-C", str(target), "init"], check=True)

    print(f"Created research repo: {target}")
    print(f"Project id: {args.project_id}")
    print("Next task command:")
    print("python -m one_c_autoresearch queue get")
    return 0
