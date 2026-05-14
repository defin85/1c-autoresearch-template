---
name: 1c-autoresearch-queue-worker
description: Process exactly one task in a concrete 1C autoresearch repository. Use when selecting, claiming, executing, reviewing, or updating a task from analysis/queue/tasks.jsonl; creating feature evidence packs; or running static 1C customization analysis with project.toml, RLM, MCP, and web evidence policies.
---

# 1C Autoresearch Queue Worker

## Ground Rules

- Process exactly one queue task per run.
- Read `project.toml` before using source paths, RLM projects, MCP servers, or web access.
- Prefer static source evidence and cached indexes before live 1C access.
- Do not use MCP or web evidence unless both `project.toml` and the selected task allow it.
- Record file and line evidence for confirmed claims where possible.
- Mark runtime-data-dependent conclusions as `needs_infobase_data`.
- Stop after updating the selected task status.

## Workflow

1. Read `project.toml`.
2. Read `analysis/queue/README.md`, `task-schema.md`, `review-checklist.md`, and `tasks.jsonl`.
3. Select one pending task with the highest priority whose dependencies are done:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\queue\Get-NextAnalysisTask.ps1
   ```

4. Claim the selected task:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\queue\Set-AnalysisTaskStatus.ps1 -Id <task-id> -Status claimed
   ```

5. Create a run log folder under `analysis/runs/<timestamp>-<task-id>/` when the run produces useful diagnostics.
6. Inspect static sources, generated indexes, metadata, BSL, forms, roles, scheduled jobs, business processes, and extension overrides as required by the task quality gates.
7. Write evidence under `analysis/features/<feature-id>/`.
8. Update the selected task to `evidence_pack`, `drafted`, `needs_review`, `needs_followup`, `blocked`, or `done`.
9. Run `scripts\doctor.ps1` and report any warnings or failures.

## Evidence Pack

Create or update only the selected feature folder:

```text
analysis/features/<feature-id>/
  brief.md
  findings.md
  evidence.csv
  open-questions.md
  review.md
  artifacts/
```

Use `evidence.csv` for machine-readable source evidence. Keep human-facing deliverables under `outputs/`, not inside the feature evidence pack.

## Review Standard

- Separate vendor baseline, target `cf`, target `cfe`, and next vendor evidence.
- Run positive and negative search.
- Label confidence as `high`, `medium`, or `low`.
- Create follow-up tasks instead of expanding scope beyond the selected task.
