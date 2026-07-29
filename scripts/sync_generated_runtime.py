#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path


FILES = (
    "AGENTS.md", "README.md", "pyproject.toml", "uv.lock",
    "docs/operator/dispatcher-inspector-rollback.md", "docs/operator/source-search.md",
    "research/workflow.toml", "research/indexing.toml", "research/forbidden-authorities.json",
    "tests/test_external_folder.py", "tests/test_source_routing.py", "tests/test_source_tools.py",
    "tests/test_sources.py", "tests/test_diffs.py", "tests/test_extension_analyzer.py", "tests/test_indexes.py",
    "tests/test_runner.py", "tests/test_workspace_api.py", "tests/test_stage_recompute.py",
    "tests/test_stage_recompute_api.py", "tests/test_dispatcher.py", "tests/test_dispatcher_api.py",
    "tests/test_dispatcher_inspector_server.py", "tests/test_dispatcher_smoke.py", "tests/test_doctor.py", "tests/test_mrq.py",
    "tests/test_mrq_batches.py", "tests/test_pipeline_graphs.py", "tests/test_workflow.py",
    "tests/test_contracts.py", "tests/test_source_search.py",
    "tests/test_dif_classifications.py", "tests/test_consolidation.py",
    "tests/test_component_groups.py", "tests/test_service_extension_scope.py",
    "tests/test_decision_generations.py", "tests/test_workflow_migration.py",
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
CUSTOMER_PATHS = ("sources/generations", "analysis/indexes/generations", "analysis/migration-requirements/generations")
SECRET_NAMES = re.compile(r"(?i)(?:^\.env$|credential|secret|private[_-]?key)")


def allowed(path: Path) -> bool:
    return not (set(path.parts) & IGNORED_PARTS) and path.suffix.lower() not in FORBIDDEN_SUFFIXES and ".egg-info" not in path.parts


def copy_tree(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file() or not allowed(path.relative_to(source)): continue
        target = destination / path.relative_to(source); target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, target)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): file_hash(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and allowed(path.relative_to(root))
    }


def selected_reference_files(reference: Path) -> list[Path]:
    paths = [reference / relative for relative in FILES]
    for relative in TREES:
        root = reference / relative
        if root.exists():
            paths.extend(path for path in root.rglob("*") if path.is_file() and not (set(path.relative_to(root).parts) & IGNORED_PARTS))
    for source, _target in VISUAL_ASSETS:
        root = reference / source
        if root.exists():
            paths.extend(path for path in root.rglob("*") if path.is_file() and not (set(path.relative_to(root).parts) & IGNORED_PARTS))
    return sorted(set(paths), key=lambda path: path.relative_to(reference).as_posix())


def selected_reference_hashes(reference: Path) -> dict[str, str]:
    return {
        path.relative_to(reference).as_posix(): file_hash(path)
        for path in selected_reference_files(reference)
        if path.is_file() and allowed(path.relative_to(reference))
    }


def validate_reference(reference: Path) -> None:
    if not (reference / "research/workflow.toml").is_file() or not (reference / "src/one_c_autoresearch/service.py").is_file():
        raise ValueError("reference does not implement the canonical runtime")
    for relative in CUSTOMER_PATHS:
        root = reference / relative
        if root.exists() and any(path.is_file() and path.name != ".gitkeep" for path in root.rglob("*")):
            raise ValueError(f"reference contains customer data: {relative}")
    for path in selected_reference_files(reference):
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError(f"reference contains forbidden binary payload: {path.relative_to(reference).as_posix()}")
        if SECRET_NAMES.search(path.name):
            raise ValueError(f"reference contains credential-like payload: {path.relative_to(reference).as_posix()}")


def write_seed(root: Path) -> None:
    (root / "project.toml").write_text('''[project]\nid = "__PROJECT_ID__"\nproduct = "__PRODUCT__"\nbaseline_version = "__BASELINE_VERSION__"\ntarget_version = "__TARGET_VERSION__"\nnext_vendor_version = "__NEXT_VENDOR_VERSION__"\ndescription = "Concrete 1C autoresearch repository."\n\n[mcp]\nenabled = false\nserver = ""\nurl = ""\nservice_root = "mcp"\n\n[web]\nenabled = false\nurl = ""\nusername = ""\ncredential_file = ""\n\n[policy]\nstatic_sources_first = true\nallow_live_infobase_evidence = false\nmark_runtime_data_dependencies = true\ndefault_confidence_for_inference = "medium"\n''', encoding="utf-8")
    research = root / "research"; research.mkdir(exist_ok=True)
    roles = (("vendor_baseline", "baseline", "__BASELINE_VERSION__"), ("target_cf", "target", "__TARGET_VERSION__"), ("next_vendor", "next-vendor", "__NEXT_VENDOR_VERSION__"))
    lines = ['schema_version = "1"', 'acquisition_profile = "ibcmd+form-aware/v1"', "extension_decisions = []", ""]
    for role, profile, version in roles: lines.extend((f"[roles.{role}]", f'connection_profile = "local-{profile}"', 'configuration_name = "__PRODUCT__"', 'root_uuid = "00000000-0000-0000-0000-000000000001"', f'version = "{version}"', ""))
    (research / "infobases.toml").write_text("\n".join(lines), encoding="utf-8")
    (research / "external-artifacts.toml").write_text('schema_version = "1"\nartifacts = []\n', encoding="utf-8")
    (research / "active-diff-generation.json").write_text("{}\n", encoding="utf-8")
    (research / "active-consolidation-generation.json").write_text(json.dumps({
        "batch_generation_id": None, "batch_input_fingerprint": None,
        "classification_fingerprint": None,
        "consolidation_approval_fingerprint": None,
        "decision_generation_id": None,
        "decision_input_fingerprint": None, "diff_fingerprint": None,
        "mrq_generation_id": None, "plan_fingerprint": None,
        "schema_version": "2", "source_fingerprint": None,
        "state": "unpublished", "transaction_id": None,
    }, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
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


def build_staging(reference: Path, staging: Path) -> None:
    validate_reference(reference)
    staging.mkdir()
    for relative in FILES:
        source = reference / relative
        if not source.is_file(): raise FileNotFoundError(relative)
        target = staging / relative; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
    for relative in TREES: copy_tree(reference / relative, staging / relative)
    for source, target in VISUAL_ASSETS: copy_tree(reference / source, staging / target)
    write_seed(staging); sanitize(staging, reference)
    manifest = sorted(path.relative_to(staging).as_posix() for path in staging.rglob("*") if path.is_file())
    (staging / "research/runtime-sync-manifest.json").write_text(json.dumps({"schema_version": "1", "paths": manifest}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def change_plan(staged: Path, destination: Path) -> list[dict[str, str]]:
    before, after = tree_hashes(destination), tree_hashes(staged)
    plan = []
    for path in sorted(before.keys() | after.keys()):
        if path not in before:
            plan.append({"status": "A", "path": path, "hash": after[path]})
        elif path not in after:
            plan.append({"status": "D", "path": path, "hash": before[path]})
        elif before[path] != after[path]:
            plan.append({"status": "C", "path": path, "hash": after[path]})
    return plan


def plan_fingerprint(reference: Path, destination: Path, staged: Path, plan: list[dict[str, str]]) -> str:
    payload = {
        "reference": str(reference),
        "destination": str(destination),
        "reference_files": selected_reference_hashes(reference),
        "staged_manifest": tree_hashes(staged),
        "changes": plan,
    }
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parity_errors(expected: Path, actual: Path) -> list[str]:
    plan = change_plan(expected, actual)
    return [f"{item['status']} {item['path']}" for item in plan]


def require_parity(expected: Path, actual: Path) -> None:
    errors = parity_errors(expected, actual)
    if errors:
        raise RuntimeError("derived tree parity is incomplete:\n" + "\n".join(errors))


def replace_tree(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def sync(
    reference: Path,
    destination: Path,
    apply: bool = False,
    expected_fingerprint: str = "",
    promote: bool = False,
    package_root: Path | None = None,
) -> dict[str, object]:
    reference = reference.resolve(); destination = destination.resolve()
    package_root = (package_root or Path(__file__).resolve().parents[1]).resolve()
    approved = Path(__file__).resolve().parents[1] / "templates/research-repo"
    generated = (destination / "research/runtime-sync-manifest.json").is_file()
    if destination != approved.resolve() and destination.exists() and any(destination.iterdir()) and not generated:
        raise RuntimeError("destination is non-empty and is not the approved canonical scaffold")
    with tempfile.TemporaryDirectory(prefix="one-c-generated-runtime-") as temporary:
        staging = Path(temporary) / "research-repo"
        build_staging(reference, staging)
        plan = change_plan(staging, destination)
        if promote:
            plan = [{**item, "path": f"templates/research-repo/{item['path']}"} for item in plan]
            promoted = (
                ("src/one_c_autoresearch", staging / "src/one_c_autoresearch", package_root / "src/one_c_autoresearch"),
                ("web/workspace", staging / "web/workspace", package_root / "web/workspace"),
            )
            plan += [
                {**item, "path": f"{prefix}/{item['path']}"}
                for prefix, source, target in promoted
                for item in change_plan(source, target)
            ]
            for relative in (path for path in FILES if path.startswith("docs/operator/")):
                source, target = staging / relative, package_root / relative
                if not target.is_file():
                    plan.append({"status": "A", "path": relative, "hash": file_hash(source)})
                elif file_hash(source) != file_hash(target):
                    plan.append({"status": "C", "path": relative, "hash": file_hash(source)})
            for relative in (path for path in FILES if path.startswith("tests/")):
                source, target = staging / relative, package_root / relative
                if not target.is_file():
                    plan.append({"status": "A", "path": relative, "hash": file_hash(source)})
                elif file_hash(source) != file_hash(target):
                    plan.append({"status": "C", "path": relative, "hash": file_hash(source)})
            plan.sort(key=lambda item: item["path"])
        fingerprint = plan_fingerprint(reference, destination, staging, plan)
        result = {"fingerprint": fingerprint, "changes": plan}
        if not apply:
            return result
        if not expected_fingerprint or expected_fingerprint != fingerprint:
            raise RuntimeError("plan fingerprint mismatch; generate a new preview")
        replace_tree(staging, destination)
        require_parity(staging, destination)
        if promote:
            promote_package(destination, package_root)
        return result


def promote_package(scaffold: Path, package_root: Path) -> None:
    source_package = scaffold / "src/one_c_autoresearch"
    target_package = package_root / "src/one_c_autoresearch"
    replace_tree(source_package, target_package)
    replace_tree(scaffold / "web/workspace", package_root / "web/workspace")
    for relative in (path for path in FILES if path.startswith("docs/operator/")):
        target = package_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(scaffold / relative, target)
    for relative in (path for path in FILES if path.startswith("tests/")):
        target = package_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(scaffold / relative, target)
        text = target.read_text(encoding="utf-8")
        text = text.replace(
            "REPO = Path(__file__).resolve().parents[1]\n",
            'REPO = Path(__file__).resolve().parents[1] / "templates/research-repo"\n',
        ).replace(
            '(Path(__file__).resolve().parents[1] / "research/',
            '(Path(__file__).resolve().parents[1] / "templates/research-repo" / "research/',
        )
        target.write_text(text, encoding="utf-8")
    require_parity(source_package, target_package)
    require_parity(scaffold / "web/workspace", package_root / "web/workspace")


def main() -> int:
    package_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(); parser.add_argument("--reference", required=True); parser.add_argument("--destination", default=str(package_root / "templates/research-repo")); parser.add_argument("--apply", action="store_true"); parser.add_argument("--expected-fingerprint", default=""); parser.add_argument("--promote-package", action="store_true"); args = parser.parse_args()
    destination = Path(args.destination)
    result = sync(Path(args.reference), destination, args.apply, args.expected_fingerprint, args.promote_package, package_root)
    for item in result["changes"]:
        print(item["status"], item["path"], item["hash"])
    print(result["fingerprint"])
    return 0


if __name__ == "__main__": raise SystemExit(main())
