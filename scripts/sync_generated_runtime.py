#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from pathlib import Path


FILES = (
    "AGENTS.md", "README.md", "pyproject.toml", "uv.lock",
    "research/workflow.toml", "research/indexing.toml", "research/forbidden-authorities.json",
    "tests/test_external_folder.py", "tests/test_source_routing.py", "tests/test_source_tools.py",
    "tests/test_sources.py", "tests/test_diffs.py", "tests/test_extension_analyzer.py", "tests/test_indexes.py",
    "tests/test_runner.py", "tests/test_workspace_api.py", "tests/test_stage_recompute.py",
    "tests/test_stage_recompute_api.py", "tests/test_dispatcher.py", "tests/test_dispatcher_api.py",
    "tests/test_dispatcher_smoke.py", "tests/test_doctor.py", "tests/test_mrq.py",
    "tests/test_mrq_batches.py", "tests/test_pipeline_graphs.py", "tests/test_workflow.py",
)
TREES = (
    "src/one_c_autoresearch", "one_c_autoresearch", "research/schemas", "web/workspace",
    "tests/fixtures/source-routing", "tests/fixtures/extension-semantic",
)
VISUAL_ASSETS = (
    ("openspec/changes/archive/2026-07-25-add-mrq-batch-classification-stage/assets", "web/workspace/e2e/visual-assets/current"),
    ("openspec/changes/archive/2026-07-25-make-dispatcher-new-working-screen/assets", "web/workspace/e2e/visual-assets/legacy"),
)
IGNORED_PARTS = {"__pycache__", ".pytest_cache", ".venv", "node_modules", "test-results", ".artifacts", ".playwright-cli", ".git"}
FORBIDDEN_SUFFIXES = {".cf", ".cfe", ".epf", ".erf", ".dt", ".pyc", ".pyo"}
FORBIDDEN_TEXT = (re.compile(r"/run/" + r"media/"), re.compile(r"/home/[A-Za-z0-9._-]+/"), re.compile(r"sppr", re.I))


def allowed(path: Path) -> bool:
    return not (set(path.parts) & IGNORED_PARTS) and path.suffix.lower() not in FORBIDDEN_SUFFIXES and ".egg-info" not in path.parts


def copy_tree(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file() or not allowed(path.relative_to(source)): continue
        target = destination / path.relative_to(source); target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, target)


def write_seed(root: Path) -> None:
    (root / "project.toml").write_text('''[project]\nid = "__PROJECT_ID__"\nproduct = "__PRODUCT__"\nbaseline_version = "__BASELINE_VERSION__"\ntarget_version = "__TARGET_VERSION__"\nnext_vendor_version = "__NEXT_VENDOR_VERSION__"\ndescription = "Concrete 1C autoresearch repository."\n\n[mcp]\nenabled = false\nserver = ""\nurl = ""\nservice_root = "mcp"\n\n[web]\nenabled = false\nurl = ""\nusername = ""\ncredential_file = ""\n\n[policy]\nstatic_sources_first = true\nallow_live_infobase_evidence = false\nmark_runtime_data_dependencies = true\ndefault_confidence_for_inference = "medium"\n''', encoding="utf-8")
    research = root / "research"; research.mkdir(exist_ok=True)
    roles = (("vendor_baseline", "baseline", "__BASELINE_VERSION__"), ("target_cf", "target", "__TARGET_VERSION__"), ("next_vendor", "next-vendor", "__NEXT_VENDOR_VERSION__"))
    lines = ['schema_version = "1"', 'acquisition_profile = "ibcmd+form-aware/v1"', ""]
    for role, profile, version in roles: lines.extend((f"[roles.{role}]", f'connection_profile = "local-{profile}"', 'configuration_name = "__PRODUCT__"', 'root_uuid = "00000000-0000-0000-0000-000000000001"', f'version = "{version}"', ""))
    (research / "infobases.toml").write_text("\n".join(lines), encoding="utf-8")
    (research / "external-artifacts.toml").write_text('schema_version = "1"\nartifacts = []\n', encoding="utf-8")
    (research / "active-diff-generation.json").write_text("{}\n", encoding="utf-8")
    (root / "outputs").mkdir(exist_ok=True)
    for path in (root / "sources/generations", root / "analysis/indexes/generations", root / "analysis/migration-requirements/generations", root / "research/generations", root / "outputs"):
        path.mkdir(parents=True, exist_ok=True); (path / ".gitkeep").touch()
    (root / ".gitignore").write_text('.venv/\nnode_modules/\n__pycache__/\n*.py[cod]\n', encoding="utf-8")


def sanitize(root: Path, reference: Path) -> None:
    reference_text = str(reference.resolve())
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".zip"}: continue
        try: text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError: continue
        text = text.replace("# SPPR Research", "# __PRODUCT__ Research").replace("sppr-research", "__PROJECT_ID__")
        text = re.sub(r"sppr", "example", text, flags=re.I)
        text = text.replace("local-example-vendor", "local-baseline").replace("example_vendor", "baseline")
        text = text.replace(
            "../../openspec/changes/archive/2026-07-25-add-mrq-batch-classification-stage/assets",
            "e2e/visual-assets/current",
        ).replace(
            "../../openspec/changes/archive/2026-07-25-make-dispatcher-new-working-screen/assets",
            "e2e/visual-assets/legacy",
        )
        if path.relative_to(root).as_posix() == "web/workspace/e2e/workspace.spec.ts":
            text = text.replace(
                "  assertApprovedAsset(approvedName);\n  const expected",
                "  assertApprovedAsset(approvedName);\n  if (process.env.PORTABLE_TEMPLATE === '1') return;\n  const expected",
            )
        if path.relative_to(root).as_posix() == "web/workspace/playwright.config.ts":
            text = "process.env.PORTABLE_TEMPLATE = '1';\n" + text
        if "tests" in path.parts and path.suffix == ".py":
            text = text.replace('"gpt-5.6-' + 'sol"', '"test-model"')
        if path.suffix == ".py":
            text = text.replace(
                "csv.field_size_limit(sys.maxsize)",
                "field_size_limit = sys.maxsize\nwhile True:\n    try:\n        csv.field_size_limit(field_size_limit)\n        break\n    except OverflowError:\n        field_size_limit //= 10",
            )
        if reference_text in text or any(pattern.search(text) for pattern in FORBIDDEN_TEXT): raise ValueError(f"host or customer-specific text in synchronized path: {path.relative_to(root)}")
        path.write_text(text, encoding="utf-8", newline="\n")


def sync(reference: Path, destination: Path, replace: bool) -> None:
    reference = reference.resolve(); destination = destination.resolve()
    if not (reference / "research/workflow.toml").is_file() or not (reference / "src/one_c_autoresearch/service.py").is_file(): raise ValueError("reference does not implement the canonical runtime")
    if destination.exists() and any(destination.iterdir()) and not replace: raise RuntimeError("destination is not empty; pass --replace after reviewing the exact target")
    with tempfile.TemporaryDirectory(prefix="one-c-generated-runtime-") as temporary:
        staging = Path(temporary) / "research-repo"; staging.mkdir()
        for relative in FILES:
            source = reference / relative
            if not source.is_file(): raise FileNotFoundError(relative)
            target = staging / relative; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
        for relative in TREES: copy_tree(reference / relative, staging / relative)
        for source, target in VISUAL_ASSETS: copy_tree(reference / source, staging / target)
        write_seed(staging); sanitize(staging, reference)
        manifest = sorted(path.relative_to(staging).as_posix() for path in staging.rglob("*") if path.is_file())
        (staging / "research/runtime-sync-manifest.json").write_text(json.dumps({"schema_version": "1", "paths": manifest}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        if destination.exists(): shutil.rmtree(destination)
        shutil.copytree(staging, destination)


def promote_package(scaffold: Path, package_root: Path) -> None:
    source_package = scaffold / "src/one_c_autoresearch"
    target_package = package_root / "src/one_c_autoresearch"
    preserved = {"__init__.py", "cli.py", "doctor.py"}
    shutil.rmtree(target_package / "workspace_static", ignore_errors=True)
    for path in sorted(source_package.rglob("*")):
        relative = path.relative_to(source_package)
        if not path.is_file() or not allowed(relative) or relative.as_posix() in preserved:
            continue
        target = target_package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    target_web = package_root / "web/workspace"
    if target_web.exists():
        shutil.rmtree(target_web)
    shutil.copytree(scaffold / "web/workspace", target_web, ignore=shutil.ignore_patterns(*IGNORED_PARTS))


def main() -> int:
    package_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(); parser.add_argument("--reference", required=True); parser.add_argument("--destination", default=str(package_root / "templates/research-repo")); parser.add_argument("--replace", action="store_true"); parser.add_argument("--promote-package", action="store_true"); args = parser.parse_args()
    destination = Path(args.destination)
    sync(Path(args.reference), destination, args.replace)
    if args.promote_package:
        promote_package(destination, package_root)
    return 0


if __name__ == "__main__": raise SystemExit(main())
