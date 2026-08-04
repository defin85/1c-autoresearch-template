#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tomllib


ROOT_RUNTIME = "src/one_c_autoresearch"
ROOT_ONLY_TESTS = {"test_generated_runtime_sync.py", "test_template_release.py"}
MAINTENANCE = {
    "scripts/sync_generated_runtime.py", "scripts/build_workspace_template.py",
    "scripts/bootstrap/new_research_repo.py", "scripts/checks/test_template.py",
    "scripts/checks/test_doctor.py", "scripts/checks/test_research_repo.py",
}
ACTIVE_DOCS = {
    "README.md", "AGENTS.md", "docs/agent/repo-map.md", "docs/agent/verification.md",
    "docs/operator/dispatcher-inspector-rollback.md", "docs/operator/source-search.md",
}
TYPE_CHECK_CONFIG = {
    "include": ["src/one_c_autoresearch"],
    "pythonVersion": "3.11",
}


def files(root: Path) -> set[str]:
    ignored = {"__pycache__", ".pytest_cache", ".venv", "node_modules", "test-results"}
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and not (set(path.relative_to(root).parts) & ignored)}


def contents(root: Path) -> dict[str, str]:
    return {
        path: hashlib.sha256((root / path).read_bytes()).hexdigest()
        for path in files(root)
    }


def normalized_test(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = text.replace(' / "templates/research-repo"', "")
    text = text.replace(
        '    scaffold = Path(__file__).resolve().parents[1]\n',
        "",
    ).replace(
        '(scaffold / "research/workflow.toml")',
        '(Path(__file__).resolve().parents[1] / "research/workflow.toml")',
    ).replace(
        '(scaffold / "research/forbidden-authorities.json")',
        '(Path(__file__).resolve().parents[1] / "research/forbidden-authorities.json")',
    )
    text = text.replace(
        'HAS_ACTIVE_SOURCES = (REPO / "research/active-source-generation.json").is_file()\n',
        "",
    ).replace(
        '@pytest.mark.skipif(not HAS_ACTIVE_SOURCES, reason="portable scaffold has no acquired source generation")\n',
        "",
    )
    return text


def check(root: Path) -> list[str]:
    errors: list[str] = []
    scaffold = root / "templates/research-repo"
    runtime = contents(root / ROOT_RUNTIME)
    expected_runtime = contents(scaffold / ROOT_RUNTIME)
    if runtime != expected_runtime:
        errors.append("runtime content parity mismatch")
    canonical_tests = {Path(path).name for path in files(scaffold / "tests") if path.endswith(".py")}
    root_tests = {Path(path).name for path in files(root / "tests") if path.endswith(".py")}
    if root_tests != canonical_tests | ROOT_ONLY_TESTS:
        errors.append(f"test inventory mismatch: missing={sorted((canonical_tests|ROOT_ONLY_TESTS)-root_tests)} extra={sorted(root_tests-(canonical_tests|ROOT_ONLY_TESTS))}")
    for name in sorted(canonical_tests):
        if normalized_test(root / "tests" / name) != normalized_test(scaffold / "tests" / name):
            errors.append(f"canonical test content mismatch: {name}")
    scripts = {path for path in files(root / "scripts") if path.endswith(".py")}
    if scripts != {path.removeprefix("scripts/") for path in MAINTENANCE}:
        errors.append(f"maintenance script inventory mismatch: {sorted(scripts)}")
    active_docs = {
        path for path in files(root)
        if path in {"README.md", "AGENTS.md"} or path.startswith("docs/")
    }
    if active_docs != ACTIVE_DOCS:
        errors.append(f"active documentation inventory mismatch: {sorted(active_docs)}")
    for project_root in (root, scaffold):
        project = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
        if project.get("tool", {}).get("basedpyright") != TYPE_CHECK_CONFIG:
            errors.append(f"BasedPyright policy mismatch: {project_root.relative_to(root) or '.'}")
        if (project_root / ".basedpyright" / "baseline.json").exists():
            errors.append(f"BasedPyright baseline is forbidden: {project_root.relative_to(root) or '.'}")
        if project.get("dependency-groups", {}).get("dev") != ["basedpyright==1.39.9"]:
            errors.append(f"BasedPyright version mismatch: {project_root.relative_to(root) or '.'}")
        for path in (project_root / ROOT_RUNTIME).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "# type: ignore" in text or "# pyright:" in text:
                errors.append(f"type-check suppression is forbidden: {path.relative_to(root)}")
    manifest = json.loads((scaffold / "research/runtime-sync-manifest.json").read_text(encoding="utf-8"))["paths"]
    actual = sorted(path for path in files(scaffold) if path != "research/runtime-sync-manifest.json")
    expected = sorted(path for path in manifest if path != "research/runtime-sync-manifest.json")
    if actual != expected:
        errors.append("scaffold manifest mismatch")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", default=str(Path(__file__).resolve().parents[2]))
    errors = check(Path(parser.parse_args().repo_path).resolve())
    if errors:
        print("\n".join(errors))
        return 1
    print("Template validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
