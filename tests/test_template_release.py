from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.request
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = json.loads((ROOT / "templates/research-repo/research/forbidden-authorities.json").read_text(encoding="utf-8"))


def test_python_type_check_policy_is_pinned_without_source_suppressions() -> None:
    expected = {
        "include": ["src/one_c_autoresearch"],
        "pythonVersion": "3.11",
    }
    for project_root in (ROOT, ROOT / "templates/research-repo"):
        project = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
        assert project["tool"]["basedpyright"] == expected
        assert project["dependency-groups"]["dev"] == ["basedpyright==1.39.9"]
        assert not (project_root / ".basedpyright" / "baseline.json").exists()
        for path in (project_root / "src/one_c_autoresearch").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "# type: ignore" not in text
            assert "# pyright:" not in text
    workflow = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
    assert "basedpyright==1.39.9" in workflow
    assert "run: basedpyright" in workflow


def test_runtime_matches_canonical_scaffold() -> None:
    def content(root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }

    assert content(ROOT / "src/one_c_autoresearch") == content(
        ROOT / "templates/research-repo/src/one_c_autoresearch"
    )


def test_removed_modules_are_absent() -> None:
    for module in FORBIDDEN["modules"]:
        assert not (ROOT / "src/one_c_autoresearch" / module).exists()
        assert importlib.util.find_spec(f"one_c_autoresearch.{Path(module).stem}") is None


def test_canonical_cli_has_no_removed_commands() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "one_c_autoresearch", "--help"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=True,
        capture_output=True,
        text=True,
    )
    for token in FORBIDDEN["command_tokens"]:
        assert token not in result.stdout


def test_compiled_workspace_has_no_removed_routes_or_schema_markers() -> None:
    compiled = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted((ROOT / "src/one_c_autoresearch/workspace_static").rglob("*"))
        if path.is_file()
    )
    for token in FORBIDDEN["route_tokens"]:
        assert re.search(rf"""(["']){re.escape(token)}\1""", compiled) is None
    assert "CUS-" not in compiled
    assert "subject-card" not in compiled


def test_template_archive_is_separate_and_canonical(tmp_path: Path) -> None:
    subprocess.run([sys.executable, str(ROOT / "scripts/build_workspace_template.py")], cwd=ROOT, check=True)
    archive = ROOT / "dist/research-template.zip"
    assert archive.is_file()
    with zipfile.ZipFile(archive) as value:
        names = set(value.namelist())
    assert "src/one_c_autoresearch/workspace_assets/research-template.zip" not in names
    assert not any(name.startswith("scripts/") for name in names)
    assert names == {
        path.relative_to(ROOT / "templates/research-repo").as_posix()
        for path in (ROOT / "templates/research-repo").rglob("*")
        if path.is_file() and not ({"__pycache__", ".pytest_cache", ".venv", "node_modules", "test-results"} & set(path.parts))
    }


def test_release_distribution_members() -> None:
    expected = {
        "one_c_autoresearch-0.3.0-py3-none-any.whl",
        "one_c_autoresearch-0.3.0.tar.gz",
        "research-template.zip",
    }
    assert {path.name for path in (ROOT / "dist").iterdir() if path.is_file()} == expected
    wheel = ROOT / "dist/one_c_autoresearch-0.3.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = archive.namelist()
    with tarfile.open(ROOT / "dist/one_c_autoresearch-0.3.0.tar.gz") as archive:
        sdist_names = archive.getnames()
    with zipfile.ZipFile(ROOT / "dist/research-template.zip") as archive:
        template_names = archive.namelist()
    for module in FORBIDDEN["modules"]:
        assert not any(name.endswith(f"one_c_autoresearch/{module}") for name in wheel_names + sdist_names)
    assert not any("workspace_assets/" in name or name.startswith("scripts/") for name in wheel_names)
    assert not any("/scripts/" in name or "/workspace_assets/" in name for name in sdist_names)
    release_names = wheel_names + sdist_names + template_names
    forbidden_operational = (
        "source-search-hmac", "indexes-v2/", "/.build/",
        "/targets/", "/instances/", "/staging/", "/quarantine/",
    )
    assert not any(
        name.lower().endswith((".sqlite", ".sqlite3", ".db", ".key"))
        or any(marker in name.lower() for marker in forbidden_operational)
        for name in release_names
    )
    for marker in ("sources/generations/", "analysis/indexes/generations/"):
        assert all(
            name.endswith(".gitkeep")
            for name in release_names
            if marker in name
        )


@pytest.mark.parametrize("upgrade", [False, True])
@pytest.mark.parametrize("workspace", [False, True])
def test_clean_and_upgrade_install_matrix(tmp_path: Path, upgrade: bool, workspace: bool) -> None:
    baseline_value = os.environ.get("ONE_C_AUTORESEARCH_020_WHEEL")
    baseline_hash = os.environ.get("ONE_C_AUTORESEARCH_020_SHA256")
    if not baseline_value or not baseline_hash:
        pytest.skip("exact 0.2.0 baseline is supplied by the release job")
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is required for isolated release installation")
    baseline = Path(baseline_value).resolve()
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == baseline_hash
    environment = tmp_path / ("upgrade" if upgrade else "clean")
    subprocess.run([uv, "venv", str(environment)], check=True, capture_output=True)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    suffix = "[workspace]" if workspace else ""
    if upgrade:
        subprocess.run([uv, "pip", "install", "--python", str(python), f"{baseline}{suffix}"], check=True)
    wheel = ROOT / "dist/one_c_autoresearch-0.3.0-py3-none-any.whl"
    subprocess.run([uv, "pip", "install", "--python", str(python), f"{wheel}{suffix}"], check=True)
    modules = [Path(module).stem for module in FORBIDDEN["modules"]]
    code = f"""
import importlib.metadata
import importlib.util
import one_c_autoresearch
assert one_c_autoresearch.__version__ == "0.3.0"
for module in {modules!r}:
    assert importlib.util.find_spec(f"one_c_autoresearch.{{module}}") is None
assert importlib.util.find_spec("one_c_autoresearch.workspace_assets") is None
scripts = importlib.metadata.entry_points(group="console_scripts")
names = {{item.name for item in scripts if item.value.startswith("one_c_autoresearch")}}
assert names == {{"one-c-autoresearch", "one-c-autoresearch-workspace"}}
"""
    if workspace:
        code += "\nimport one_c_autoresearch.workspace_api\n"
    subprocess.run([str(python), "-c", code], cwd=tmp_path, check=True)
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    executable_suffix = ".exe" if os.name == "nt" else ""
    cli = bin_dir / f"one-c-autoresearch{executable_suffix}"
    help_result = subprocess.run([str(cli), "--help"], cwd=tmp_path, check=True, capture_output=True, text=True)
    help_text = help_result.stdout + help_result.stderr
    for token in FORBIDDEN["command_tokens"]:
        assert token not in help_text

    if workspace:
        server = subprocess.Popen(
            [str(bin_dir / f"one-c-autoresearch-workspace{executable_suffix}")],
            cwd=ROOT / "templates/research-repo",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8765/api/v1/health", timeout=1) as response:
                        assert json.load(response) == {"status": "ok", "schema_version": "1"}
                    with urllib.request.urlopen("http://127.0.0.1:8765/", timeout=1) as response:
                        assert '<div id="root"></div>' in response.read().decode()
                    break
                except OSError:
                    if server.poll() is not None or time.monotonic() >= deadline:
                        stdout, stderr = server.communicate()
                        pytest.fail(f"workspace did not become ready\nstdout:\n{stdout}\nstderr:\n{stderr}")
                    time.sleep(0.1)
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
