from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


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
