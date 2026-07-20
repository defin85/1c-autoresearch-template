from __future__ import annotations

import json
from pathlib import Path

from one_c_autoresearch.doctor import run_doctor


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
