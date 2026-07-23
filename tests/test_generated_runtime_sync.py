from __future__ import annotations

from argparse import Namespace
import importlib.util
import json
from pathlib import Path
import os
import subprocess
import sys
import zipfile

import pytest

from one_c_autoresearch.bootstrap import create_research_repo


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sync_generated_runtime", ROOT / "scripts/sync_generated_runtime.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_sync_is_repeatable_and_refuses_non_empty_destination(tmp_path: Path):
    reference = ROOT / "templates/research-repo"
    first, second = tmp_path / "first", tmp_path / "second"
    MODULE.sync(reference, first, False)
    MODULE.sync(reference, second, False)
    assert json.loads((first / "research/runtime-sync-manifest.json").read_text()) == json.loads((second / "research/runtime-sync-manifest.json").read_text())
    assert (first / "research/workflow.toml").read_bytes() == (second / "research/workflow.toml").read_bytes()
    with pytest.raises(RuntimeError, match="not empty"):
        MODULE.sync(reference, first, False)


def test_packaged_template_matches_scaffold_and_starts_fresh_repo(tmp_path: Path):
    scaffold = ROOT / "templates/research-repo"
    archive = ROOT / "src/one_c_autoresearch/workspace_assets/research-template.zip"
    expected = {path.relative_to(scaffold).as_posix(): path.read_bytes() for path in scaffold.rglob("*") if path.is_file()}
    with zipfile.ZipFile(archive) as source:
        prefix = "templates/research-repo/"
        actual = {name.removeprefix(prefix): source.read(name) for name in source.namelist() if name.startswith(prefix) and not name.endswith("/")}
        assert actual.keys() == expected.keys()
        for path, content in expected.items():
            assert actual[path] == content, path
        source.extractall(tmp_path / "package")

    repo = tmp_path / "fresh"
    create_research_repo(Namespace(
        template_root=str(tmp_path / "package"), target_path=str(repo), force=False,
        project_id="parity-smoke", product="Parity Smoke", baseline_version="1",
        target_version="2", next_vendor_version="3", vendor_baseline="",
        target_cf="", target_cfe="", next_vendor="", rlm_vendor_baseline="",
        rlm_target_cf="", rlm_target_cfe="", rlm_next_vendor="", init_git=False,
    ))
    result = subprocess.run(
        [sys.executable, "-m", "one_c_autoresearch", "status"],
        cwd=repo, text=True, capture_output=True, check=True,
    )
    assert '"project-configured"' in result.stdout
    environment = {**os.environ, "PYTHONPATH": str(repo / "src")}
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_extension_analyzer.py",
            "tests/test_diffs.py",
        ],
        cwd=repo, env=environment, check=True,
    )
