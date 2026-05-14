# Agent Repo Map

This is a concrete 1C autoresearch repository created from `1c-autoresearch-template`. Treat `project.toml` and the analysis queue as the active source of truth for the customer/project context.

## Top-Level Map

| Path | Purpose | Edit When |
| --- | --- | --- |
| `project.toml` | Project identity, source paths, RLM projects, MCP/web policy, and evidence permissions. | Source locations, RLM projects, MCP/web targets, or policy change. |
| `AGENTS.md` | Mandatory local rules for agents. | Agent safety rules or verification entry points change. |
| `README.md` | Short project overview and first commands. | Onboarding commands or layout change. |
| `docs/agent/` | Agent router, repo map, and verification runbook. | Agent workflow or navigation changes. |
| `docs/method/` | Project-local copy of the reusable analysis method. | Method needs project-specific clarification. |
| `analysis/queue/` | File-backed queue state and worker contract. | Tasks, statuses, schema, or review rules change. |
| `analysis/features/` | Feature evidence packs. | A queue task produces or updates feature-level evidence. |
| `analysis/cache/` | Generated indexes and noisy intermediate artifacts. | Static analysis or comparison tools produce machine data. |
| `analysis/runs/` | Run logs for task execution. | A worker run needs an auditable trace. |
| `outputs/` | Human-facing deliverables. | Final reports, question registers, or backlog seeds are produced. |
| `scripts/queue/` | Queue selection and status update helpers. | Queue mechanics change. |
| `scripts/checks/` and `scripts/doctor.ps1` | Repository health checks. | Validation contract changes. |
| `.agents/skills/` | Repo-local Codex workflows. | A repeatable agent workflow should be discoverable as a skill. |

## Task Run Route

1. Read `project.toml`.
2. Read `analysis/queue/README.md`, `task-schema.md`, `review-checklist.md`, and `tasks.jsonl`.
3. Use `scripts/queue/Get-NextAnalysisTask.ps1` to select one task.
4. Claim only that task with `scripts/queue/Set-AnalysisTaskStatus.ps1`.
5. Write evidence under `analysis/features/<feature-id>/`.
6. Update the selected task status and stop.
7. Run `scripts/doctor.ps1` before claiming repository health.

## System Of Record

- `project.toml`: active source paths, RLM projects, MCP/web target, and evidence policy.
- `analysis/queue/tasks.jsonl`: current queue state.
- `analysis/features/`: durable feature evidence.
- `outputs/`: final human-facing deliverables.
- `docs/agent/verification.md`: validation commands and health gate meaning.

Do not use this generated repository as evidence for another customer project.
