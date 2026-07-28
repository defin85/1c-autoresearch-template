from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import subprocess

from one_c_autoresearch import checks
from one_c_autoresearch.doctor import Doctor, run_doctor


def test_placeholder_scan_ignores_dependencies(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "dependency.py").write_text("__DEPENDENCY_MARKER__", encoding="utf-8")
    doctor = Doctor(tmp_path, mode="research")
    doctor.test_unresolved_placeholders()
    assert doctor.checks.checks == [{"id": "research.unresolved_placeholders", "status": "ok", "message": "No unresolved template placeholders found"}]


def test_doctor_still_detects_damaged_repository_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    queue = root / "analysis/queue/tasks.jsonl"
    queue.parent.mkdir(parents=True)
    (root / "project.toml").write_text('[project]\nid = "checks-test"\n', encoding="utf-8")
    queue.write_text(json.dumps({"id": "Q-1", "type": "review", "status": "pending", "priority": 1}) + "\n", encoding="utf-8")
    valid = run_doctor(root, mode="research")
    assert not any(check["id"] == "queue.invalid_status" for check in valid["checks"])
    rows = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines() if line]
    rows[0]["status"] = "not-a-status"
    queue.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = run_doctor(root, mode="research")
    assert result["status"] == "fail"
    assert any(check["id"] == "queue.invalid_status" and check["status"] == "fail" for check in result["checks"])


def test_doctor_resolves_manifest_paths_from_repository_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"; (root / "sources" / "target_cf").mkdir(parents=True)
    (root / "project.toml").write_text('[project]\nid="relative-paths"\n\n[paths]\ntarget_cf="sources/target_cf"\n', encoding="utf-8")
    result = run_doctor(root, mode="research", deep=True)
    check = next(item for item in result["checks"] if item["id"] == "manifest.paths.exists.target_cf")
    assert check["status"] == "ok"


def test_checks_research_uses_canonical_repo_doctor(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    (root / "research").mkdir(parents=True)
    (root / "research/workflow.toml").touch()
    (root / "src/one_c_autoresearch").mkdir(parents=True)
    (root / "src/one_c_autoresearch/service.py").touch()
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(checks.subprocess, "run", run)

    assert checks.test_research_repo(Namespace(repo_path=root)) == 0
    command, kwargs = calls[0]
    assert command[-3:] == ["--repo-path", str(root), "doctor"]
    assert kwargs["cwd"] == root
    assert kwargs["env"]["PYTHONPATH"] == str(root / "src")
