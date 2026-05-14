# Analysis Queue

This directory stores the queue state outside the Codex context window.

## Files

- `tasks.jsonl`: one JSON task per line.
- `task-schema.md`: field contract and statuses.
- `worker-prompt.md`: prompt for manual or automation-based runs.
- `review-checklist.md`: quality gates for deep dives and reviews.
- `runs/`: queue-specific automation diagnostics. Prefer `analysis/runs/` for new task run logs.

## Next Task

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\queue\Get-NextAnalysisTask.ps1
```

## Core Rule

Process one task per run. If new work appears, create a follow-up task instead of expanding scope.
