# Worker Prompt

```text
You are processing a 1C autoresearch queue.

Rules:
1. Read project.toml.
2. Read analysis/queue/README.md, task-schema.md, review-checklist.md, and tasks.jsonl.
3. Select exactly one pending task with the highest priority whose dependencies are done.
4. Claim it with scripts/queue/Set-AnalysisTaskStatus.ps1.
5. Create a run folder under analysis/runs/<timestamp>-<task-id>/ when the run produces useful diagnostics.
6. Use static sources and cached indexes first.
7. Do not use 1C MCP or web evidence unless the task explicitly asks for it and project.toml allows it.
8. Record source file and line for confirmed claims.
9. Perform positive and negative search.
10. Mark runtime-data-dependent conclusions as needs_infobase_data.
11. Write evidence under analysis/features/<feature-id>/.
12. Update task status and stop.
```
