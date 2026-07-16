import json

from one_c_autoresearch.queue import claim_next_task, get_next_task


def test_ordinary_queue_skips_reserved_tasks(tmp_path):
    queue = tmp_path / "tasks.jsonl"
    tasks = [
        {"id": "Q-MANUAL", "type": "manual_markup", "status": "pending", "priority": 300},
        {"id": "Q-PARALLEL", "type": "review", "status": "pending", "priority": 200, "parallel_adapter": "uncovered_cus"},
        {"id": "Q-ORDINARY", "type": "review", "status": "pending", "priority": 100},
    ]
    queue.write_text("".join(json.dumps(task) + "\n" for task in tasks), encoding="utf-8")

    assert get_next_task(queue)["id"] == "Q-ORDINARY"
    assert claim_next_task(queue)["id"] == "Q-ORDINARY"
    assert claim_next_task(queue) == {}
    assert claim_next_task(queue, task_type="manual_markup")["id"] == "Q-MANUAL"
