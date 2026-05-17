# Agent Repo Map

This is a concrete 1C autoresearch repository created from `1c-autoresearch-template`. Treat `project.toml` and the analysis queue as the active source of truth for the customer/project context.

## Top-Level Map

| Path | Purpose | Edit When |
| --- | --- | --- |
| `project.toml` | Project identity, source paths, RLM projects, MCP/web policy, and evidence permissions. | Source locations, RLM projects, MCP/web targets, or policy change. |
| `.codex/1c-mcp.toml` | Optional repo-local active 1C MCP/web target manifest. | Published infobase, MCP server, service root, URL, or active RLM target changes. |
| `AGENTS.md` | Mandatory local rules for agents. | Agent safety rules or verification entry points change. |
| `README.md` | Short project overview and first commands. | Onboarding commands or layout change. |
| `docs/agent/` | Agent router, repo map, and verification runbook. | Agent workflow or navigation changes. |
| `docs/method/` | Project-local copy of the reusable analysis method. | Method needs project-specific clarification. |
| `analysis/queue/` | File-backed queue state and worker contract. | Tasks, statuses, schema, or review rules change. |
| `analysis/indexes/` | Reviewable autopilot indexes: diff inventory and feature map. | Diff entries are classified or functional features are grouped. |
| `analysis/features/` | Feature evidence packs and file templates. | A queue task produces or updates feature-level evidence. |
| `analysis/cache/` | Generated indexes and noisy intermediate artifacts. | Static analysis or comparison tools produce machine data. |
| `analysis/runs/` | Run logs for task execution. | A worker run needs an auditable trace. |
| `outputs/` | Human-facing deliverables. | Final reports, question registers, or backlog seeds are produced. |
| `scripts/queue/` | Atomic claim, read-only selection, and status update helpers. | Queue mechanics change. |
| `scripts/checks/` and `scripts/doctor.py` | Repository health checks. | Validation contract changes. |
| `.agents/skills/` | Repo-local Codex workflows. | A repeatable agent workflow should be discoverable as a skill. |

## Task Run Route

1. Read `project.toml`.
2. Read `analysis/queue/README.md`, `task-schema.md`, `review-checklist.md`, and `tasks.jsonl`.
3. Use `scripts/queue/claim_next_analysis_task.py` to atomically claim one task.
4. If no task is returned, stop without editing queue state.
5. Write evidence under `analysis/features/<feature-id>/`.
6. Run `scripts/doctor.py` and record warnings or failures.
7. Update the selected task status and stop.

## Autopilot Route

Use this route when the goal is the final end-to-end customization map rather than a single queue task:

1. Read `project.toml` and `docs/method/autopilot-customization-map.md`.
2. Run `python -m one_c_autoresearch autopilot scaffold --enable-gate` if the final-map artifacts are not initialized.
3. Build or reuse the physical clean-rebase comparison.
4. Classify every clean diff entry in `analysis/indexes/diff-inventory.csv`.
5. Group real customizations in `analysis/indexes/feature-map.csv`.
6. Create complete evidence packs under `analysis/features/<feature-id>/`.
7. Generate `outputs/customization-map.md`, `outputs/customization-map.xlsx`, `outputs/open-questions.csv`, and `outputs/open-questions.xlsx`.
8. Write `analysis/final-audit.md`.
9. Run `python -m one_c_autoresearch doctor --deep --strict`; do not claim completion until it passes.

## System Of Record

- `project.toml`: active source paths, RLM projects, MCP/web target, and evidence policy.
- `.codex/1c-mcp.toml`: active MCP/web target when present; it must match `project.toml` before live evidence is used.
- `analysis/queue/tasks.jsonl`: current queue state.
- `analysis/indexes/diff-inventory.csv`: every clean diff entry and its classification status.
- `analysis/indexes/feature-map.csv`: functional grouping for the final customization map.
- `analysis/features/`: durable feature evidence.
- `docs/method/evidence-pack-schema.md`: canonical CSV headers and feature pack file contract.
- `docs/method/autopilot-customization-map.md`: final-map pipeline and completion gate.
- `outputs/`: final human-facing deliverables.
- `docs/agent/verification.md`: validation commands and health gate meaning.

Do not use this generated repository as evidence for another customer project.
