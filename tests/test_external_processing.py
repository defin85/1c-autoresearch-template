from __future__ import annotations

import json
from pathlib import Path

from one_c_autoresearch.common import read_jsonl, repo_path, write_jsonl
from one_c_autoresearch.external_processing import prepare_external_processing, validate_external_processing


def write_fixture(root: Path) -> None:
    repo_path(root, "analysis/external-processing/source/EXT-0001-EPF").mkdir(parents=True)
    repo_path(root, "analysis/external-processing/logs").mkdir(parents=True)
    repo_path(root, "analysis/queue").mkdir(parents=True)
    repo_path(root, "analysis/queue/tasks.jsonl").write_text(
        '{"id":"Q-HAND","type":"manual_markup","status":"pending","priority":1,"title":"manual","feature_id":"manual","created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )
    repo_path(root, "analysis/external-processing/source/EXT-0001-EPF/ExternalDataProcessor.json").write_text("{}\n", encoding="utf-8")
    repo_path(root, "analysis/external-processing/logs/EXT-0002-ERF.log").write_text("failed\n", encoding="utf-8")
    repo_path(root, "analysis/external-processing/inventory.csv").write_text(
        "external_id,kind,status,source_file,original_name,sha256,unpacked_dir,log_file,file_count,return_code\n"
        "EXT-0001-EPF,epf,ok,/in/one.epf,000000001 One.epf,abc,analysis/external-processing/source/EXT-0001-EPF,analysis/external-processing/logs/EXT-0001-EPF.log,1,0\n"
        "EXT-0002-ERF,erf,failed,/in/two.erf,000000002 Two.erf,def,,analysis/external-processing/logs/EXT-0002-ERF.log,0,1\n"
        "EXT-0003-EPF,epf,ok,/in/three.epf,Three.epf,ghi,analysis/external-processing/source/EXT-0001-EPF,analysis/external-processing/logs/EXT-0003-EPF.log,1,0\n",
        encoding="utf-8",
    )
    repo_path(root, "analysis/external-processing/external-tools-source-reconciliation.csv").write_text(
        "status,match_method,match_score,screen_code,screen_kind,screen_name,source_image,confidence,external_id,file_kind,file_status,file_code,file_title,original_name,unpacked_dir\n"
        "found,code,1.000,000000001,Обработка,One,img.png,99,EXT-0001-EPF,epf,ok,000000001,One,000000001 One.epf,analysis/external-processing/source/EXT-0001-EPF\n"
        "found,code,1.000,000000002,Отчет,Two,img.png,99,EXT-0002-ERF,erf,failed,000000002,Two,000000002 Two.erf,\n"
        "not_found,,,000000004,Обработка,Missing,img.png,99,,,,,,,\n"
        "group,,,000000005,Группа,Group,img.png,99,,,,,,,\n",
        encoding="utf-8",
    )
    repo_path(root, "analysis/external-processing/source-files-not-in-external-tools.csv").write_text(
        "external_id,kind,status,file_code,file_title,original_name,source_file,unpacked_dir\n"
        "EXT-0003-EPF,epf,ok,,Three,Three.epf,/in/three.epf,analysis/external-processing/source/EXT-0001-EPF\n",
        encoding="utf-8",
    )


def test_external_processing_plan_apply_and_validate(tmp_path: Path) -> None:
    write_fixture(tmp_path)
    queue_path = repo_path(tmp_path, "analysis/queue/tasks.jsonl")
    before = queue_path.read_bytes()
    before_files = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())

    plan = prepare_external_processing(tmp_path, queue_path, apply=False)

    assert plan["status"] == "ok", json.dumps(plan, ensure_ascii=False)
    assert plan["candidate_count"] == 4
    assert queue_path.read_bytes() == before
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()) == before_files
    queue_plan = plan["queue_plan"]
    assert all(task["type"] == "review" for task in queue_plan["tasks"])
    assert not any(task["type"] == "manual_markup" for task in queue_plan["tasks"])

    applied = prepare_external_processing(tmp_path, queue_path, apply=True)

    assert applied["status"] == "ok", json.dumps(applied, ensure_ascii=False)
    tasks = [task for _, task in read_jsonl(queue_path)]
    generated = [task for task in tasks if task.get("external_processing_generation")]
    assert len(generated) == 4
    assert any(task["type"] == "manual_markup" and not task.get("external_processing_generation") for task in tasks)
    assert all(not set(task["expected_outputs"]) & {
        "analysis/external-processing/review/index.csv",
        "analysis/external-processing/review/evidence.csv",
        "analysis/external-processing/review/rollup.md",
        "analysis/external-processing/review/open-questions.md",
    } for task in generated)

    generated[0]["status"] = "done"
    write_jsonl(queue_path, tasks)
    protected = prepare_external_processing(tmp_path, queue_path, apply=True)

    assert protected["counts"].get("protected", 0) >= 1
    assert ",done," in repo_path(tmp_path, "analysis/external-processing/review/index.csv").read_text(encoding="utf-8")


def test_external_processing_preserves_enrichment_questions_and_identity(tmp_path: Path) -> None:
    write_fixture(tmp_path)
    queue_path = repo_path(tmp_path, "analysis/queue/tasks.jsonl")
    first = prepare_external_processing(tmp_path, queue_path, apply=True)
    assert first["status"] == "ok"
    tasks = [task for _, task in read_jsonl(queue_path)]
    task = next(task for task in tasks if task.get("external_id") == "EXT-0001-EPF")
    task["status"] = "needs_followup"
    task["result_summary"] = "Подтвержденное назначение"
    task["open_questions"] = ["Вопрос заказчику"]
    old_id = task["id"]
    write_jsonl(queue_path, tasks)
    index_path = repo_path(tmp_path, "analysis/external-processing/review/index.csv")
    text = index_path.read_text(encoding="utf-8").replace(",pending_review,", ",needs_followup,", 1)
    index_path.write_text(text, encoding="utf-8")

    prepare_external_processing(tmp_path, queue_path, apply=True)

    refreshed = next(task for _, task in read_jsonl(queue_path) if task.get("external_id") == "EXT-0001-EPF")
    assert refreshed["id"] == old_id
    assert refreshed["status"] == "needs_followup"
    assert refreshed["result_summary"] == "Подтвержденное назначение"
    assert refreshed["open_questions"] == ["Вопрос заказчику"]
