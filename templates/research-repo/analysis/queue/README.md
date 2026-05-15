# Analysis Queue

This directory stores the queue state outside the Codex context window.

## Files

- `tasks.jsonl`: one JSON task per line.
- `task-schema.md`: field contract and statuses.
- `worker-prompt.md`: prompt for manual or automation-based runs.
- `review-checklist.md`: quality gates for deep dives and reviews.
- `runs/`: queue-specific automation diagnostics. Prefer `analysis/runs/` for new task run logs.

## Next Task

```bash
python -m one_c_autoresearch queue claim
```

## Core Rule

Process one task per run. If new work appears, create a follow-up task instead of expanding scope.
Use `get_next_analysis_task.py` for read-only inspection and
`claim_next_analysis_task.py` when starting work.
