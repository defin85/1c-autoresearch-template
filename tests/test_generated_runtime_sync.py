from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sync_generated_runtime", ROOT / "scripts/sync_generated_runtime.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def reference(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "reference"
    files = {
        "README.md": "# Example Research\n",
        "docs/operator/dispatcher-inspector-rollback.md": "rollback\n",
        "research/workflow.toml": 'schema_version = "1"\n',
        "src/one_c_autoresearch/service.py": "VALUE = 1\n",
        "src/one_c_autoresearch/cli.py": "COMMAND = 'status'\n",
        "tests/test_runner.py": "from pathlib import Path\nREPO = Path(__file__).resolve().parents[1]\n",
        "web/workspace/package.json": "{}\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(MODULE, "FILES", ("README.md", "docs/operator/dispatcher-inspector-rollback.md", "research/workflow.toml", "tests/test_runner.py"))
    monkeypatch.setattr(MODULE, "TREES", ("src/one_c_autoresearch", "web/workspace"))
    monkeypatch.setattr(MODULE, "VISUAL_ASSETS", ())
    return root


def preview(reference: Path, destination: Path) -> dict[str, object]:
    return MODULE.sync(reference, destination)


def apply(reference: Path, destination: Path, plan: dict[str, object]) -> dict[str, object]:
    return MODULE.sync(reference, destination, True, str(plan["fingerprint"]))


def test_preview_is_sorted_content_free_and_mutates_nothing(reference: Path, tmp_path: Path) -> None:
    destination = tmp_path / "generated"
    result = preview(reference, destination)
    changes = result["changes"]
    assert [item["path"] for item in changes] == sorted(item["path"] for item in changes)
    assert {item["status"] for item in changes} == {"A"}
    assert all(set(item) == {"status", "path", "hash"} and len(item["hash"]) == 64 for item in changes)
    assert str(result["fingerprint"]).startswith("sha256:")
    assert not destination.exists()


def test_fresh_seed_declares_empty_extension_scope(reference: Path, tmp_path: Path) -> None:
    destination = tmp_path / "generated"
    plan = preview(reference, destination)
    apply(reference, destination, plan)
    assert "extension_decisions = []" in (destination / "research/infobases.toml").read_text(encoding="utf-8")


def test_apply_requires_matching_fingerprint_and_rechecks_inputs(reference: Path, tmp_path: Path) -> None:
    destination = tmp_path / "generated"
    plan = preview(reference, destination)
    with pytest.raises(RuntimeError, match="fingerprint mismatch"):
        MODULE.sync(reference, destination, True, "sha256:wrong")
    (reference / "README.md").write_text("# Changed Research\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="fingerprint mismatch"):
        apply(reference, destination, plan)
    assert not destination.exists()


def test_apply_prunes_stale_files_and_second_preview_is_noop(reference: Path, tmp_path: Path) -> None:
    destination = tmp_path / "generated"
    apply(reference, destination, preview(reference, destination))
    stale = destination / "src/one_c_autoresearch/stale.py"
    stale.write_text("obsolete = True\n", encoding="utf-8")
    plan = preview(reference, destination)
    assert {"status": "D", "path": "src/one_c_autoresearch/stale.py", "hash": MODULE.file_hash(stale)} in plan["changes"]
    apply(reference, destination, plan)
    assert not stale.exists()
    assert preview(reference, destination)["changes"] == []


def test_partial_replacement_fails_parity_and_rerun_converges(reference: Path, tmp_path: Path) -> None:
    destination = tmp_path / "generated"
    apply(reference, destination, preview(reference, destination))
    (destination / "README.md").write_text("interrupted\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="parity is incomplete"):
        with pytest.MonkeyPatch.context() as patch:
            staging = tmp_path / "expected"
            MODULE.build_staging(reference, staging)
            MODULE.require_parity(staging, destination)
    apply(reference, destination, preview(reference, destination))
    staging = tmp_path / "expected-final"
    MODULE.build_staging(reference, staging)
    MODULE.require_parity(staging, destination)


def test_customer_data_and_non_empty_unapproved_destination_are_rejected(reference: Path, tmp_path: Path) -> None:
    customer = reference / "sources/generations/live/customer.json"
    customer.parent.mkdir(parents=True)
    customer.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="customer data"):
        preview(reference, tmp_path / "generated")
    customer.unlink()
    destination = tmp_path / "unapproved"
    destination.mkdir()
    (destination / "keep.txt").write_text("user data\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="non-empty"):
        preview(reference, destination)
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "user data\n"


def test_promoted_owned_trees_are_planned_and_pruned(reference: Path, tmp_path: Path) -> None:
    destination, package_root = tmp_path / "generated", tmp_path / "package"
    stale = package_root / "src/one_c_autoresearch/stale.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("obsolete = True\n", encoding="utf-8")
    plan = MODULE.sync(reference, destination, promote=True, package_root=package_root)
    assert any(item["status"] == "D" and item["path"] == "src/one_c_autoresearch/stale.py" for item in plan["changes"])
    MODULE.sync(reference, destination, True, str(plan["fingerprint"]), True, package_root)
    assert not stale.exists()
    assert MODULE.sync(reference, destination, promote=True, package_root=package_root)["changes"] == []


def test_fingerprint_is_bounded_and_declared_payloads_fail_closed(reference: Path, tmp_path: Path) -> None:
    destination = tmp_path / "generated"
    before = preview(reference, destination)["fingerprint"]
    unrelated = reference / "unrelated/cache.txt"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("ignored\n", encoding="utf-8")
    assert preview(reference, destination)["fingerprint"] == before
    forbidden = reference / "src/one_c_autoresearch/customer.cf"
    forbidden.write_bytes(b"binary")
    with pytest.raises(ValueError, match="forbidden binary"):
        preview(reference, destination)
    forbidden.unlink()
    credential = reference / "src/one_c_autoresearch/credentials.json"
    credential.write_text('{"token":"not-packaged"}', encoding="utf-8")
    with pytest.raises(ValueError, match="credential-like"):
        preview(reference, destination)
    credential.unlink()
    service = reference / "src/one_c_autoresearch/service.py"
    service.write_text('HOST = "/home/customer/private"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="host or customer-specific"):
        preview(reference, destination)
