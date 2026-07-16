from __future__ import annotations

import json
import subprocess
import sys
import os
from pathlib import Path

from one_c_autoresearch.cli import build_parser
from one_c_autoresearch.common import read_json, write_json


ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)


def _manual_fixture(root: Path) -> str:
    row_id = "ROW-1"
    relative = "Report/Demo/Template/Print/Template.mxl"
    nested = root / "analysis/cache/noise/clean-rebase-v8unpack/repo"
    path = nested / relative
    path.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(nested)], check=True)
    subprocess.run(["git", "-C", str(nested), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(nested), "config", "user.name", "Test"], check=True)
    path.write_text("vendor\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(nested), "add", "."], check=True)
    subprocess.run(["git", "-C", str(nested), "commit", "-qm", "vendor"], check=True)
    subprocess.run(["git", "-C", str(nested), "branch", "vendor-baseline"], check=True)
    path.write_text("customer noise\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(nested), "commit", "-qam", "customer"], check=True)
    queue = root / "analysis/detailed-register-reverse-review/manual-markup-queue.jsonl"
    queue.parent.mkdir(parents=True)
    queue.write_text(json.dumps({
        "row_id": row_id, "task_id": "Q-1", "central_task_id": "Q-1", "status": "pending",
        "attempts": 0, "paths": [relative], "stable_diff_ids": ["V8D-1"], "old_diff_ids": ["D-1"],
        "current_diff_ids": ["D-1"],
    }) + "\n", encoding="utf-8")
    decisions = root / "decisions.json"
    decisions.write_text(json.dumps({"decisions": [{
        "row_id": row_id, "decision": "remove_noise", "review_basis": "free_read",
        "notes": "Технический шум", "source_paths": [relative], "evidence_refs": [],
    }]}, ensure_ascii=False), encoding="utf-8")
    return relative


def test_parallel_cli_defers_worker_count_to_project_settings() -> None:
    args = build_parser().parse_args(["parallel-research", "run", "--run-id", "PR-X"])
    assert args.workers == 0
    assert args.timeout == 0


def test_parallel_compatibility_wrapper_preserves_failure_contract() -> None:
    canonical = subprocess.run(
        [sys.executable, "-m", "one_c_autoresearch", "parallel-research", "inspect", "--run-id", "PR-MISSING"],
        cwd=ROOT, text=True, capture_output=True,
    )
    legacy = subprocess.run(
        [sys.executable, "scripts/parallel_research.py", "inspect", "--run-id", "PR-MISSING"],
        cwd=ROOT, text=True, capture_output=True,
    )
    assert canonical.returncode == legacy.returncode == 1
    assert json.loads(canonical.stdout) == json.loads(legacy.stdout)


def test_parallel_plan_wrapper_matches_canonical_json(tmp_path: Path) -> None:
    queue = tmp_path / "analysis/queue/tasks.jsonl"
    queue.parent.mkdir(parents=True)
    (tmp_path / "project.toml").write_text('[project]\nid="fixture"\n', encoding="utf-8")
    queue.write_text(json.dumps({
        "id": "Q-1", "type": "review", "status": "pending", "title": "Review",
        "feature_id": "F-1", "source_artifacts": ["project.toml"], "expected_outputs": ["out.json"],
    }) + "\n", encoding="utf-8")
    canonical = _run([sys.executable, "-m", "one_c_autoresearch", "parallel-research", "plan"], tmp_path)
    legacy = _run([sys.executable, str(ROOT / "scripts/parallel_research_plan.py")], tmp_path)
    assert canonical.returncode == legacy.returncode == 0
    left, right = json.loads(canonical.stdout), json.loads(legacy.stdout)
    left.pop("created_at"); right.pop("created_at")
    assert left == right


def test_common_json_writer_is_atomic_and_stable(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    write_json(path, {"b": 2, "a": "тест"})
    assert read_json(path) == {"a": "тест", "b": 2}
    assert path.read_text(encoding="utf-8") == '{\n  "a": "тест",\n  "b": 2\n}\n'


def test_manual_cli_and_wrapper_physically_apply_same_cleanup(tmp_path: Path) -> None:
    canonical_root = tmp_path / "canonical"
    legacy_root = tmp_path / "legacy"
    relative = _manual_fixture(canonical_root)
    _manual_fixture(legacy_root)
    common = ["--task-id", "Q-1", "--pass-id", "pass-1", "--decisions", "decisions.json"]
    canonical = _run([sys.executable, "-m", "one_c_autoresearch", "manual-cleanup", "apply-decisions", *common], canonical_root)
    legacy = _run([sys.executable, str(ROOT / "scripts/manual_cleanup_apply_decisions.py"), *common], legacy_root)
    assert canonical.returncode == legacy.returncode == 0, (canonical.stderr, legacy.stderr)
    for root in (canonical_root, legacy_root):
        queue = json.loads((root / "analysis/detailed-register-reverse-review/manual-markup-queue.jsonl").read_text())
        assert queue["status"] == "done" and queue["decision"] == "remove_noise"
        assert (root / "analysis/detailed-register-reverse-review/pass-1.csv").is_file()
        diff = subprocess.check_output([
            "git", "-C", str(root / "analysis/cache/noise/clean-rebase-v8unpack/repo"),
            "diff", "--name-only", "vendor-baseline..HEAD", "--", relative,
        ], text=True)
        assert diff == ""


def test_manual_trace_summary_wrapper_matches_canonical(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(json.dumps({
        "type": "item.completed",
        "item": {"type": "command_execution", "command": "python3 -m one_c_autoresearch manual-cleanup probe-row --row-id ROW-1"},
    }) + "\n", encoding="utf-8")
    args = ["--trace", str(trace), "--expected-row-id", "ROW-1", "--fail-on-missing-probe"]
    canonical = _run([sys.executable, "-m", "one_c_autoresearch", "manual-cleanup", "trace-summary", *args], tmp_path)
    legacy = _run([sys.executable, str(ROOT / "scripts/manual_cleanup_trace_summary.py"), *args], tmp_path)
    assert canonical.returncode == legacy.returncode == 0
    assert json.loads(canonical.stdout) == json.loads(legacy.stdout)
