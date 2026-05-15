# Worker Prompt

```text
You are processing a 1C autoresearch queue.

Rules:
1. Read project.toml.
2. Read analysis/queue/README.md, task-schema.md, review-checklist.md, and tasks.jsonl.
3. Atomically claim exactly one pending task with scripts/queue/claim_next_analysis_task.py.
4. If no task is returned, stop without editing the queue.
5. Create a run folder under analysis/runs/<timestamp>-<task-id>/ when the run produces useful diagnostics.
6. Use static sources and cached indexes first.
7. Do not use 1C MCP or web evidence unless the task explicitly asks for it and project.toml allows it.
8. Record source file and line for confirmed claims.
9. Perform positive and negative search.
10. Mark runtime-data-dependent conclusions as needs_infobase_data.
11. Write evidence under analysis/features/<feature-id>/.
12. Run `python -m one_c_autoresearch doctor` before updating the task status.
13. Update task status with `python -m one_c_autoresearch queue set-status` and stop.
```
