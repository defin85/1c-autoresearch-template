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
| `analysis/reverse-map/` | Durable fact-to-intent reverse mapping state: coverage, workitems, decisions, unresolved items, and scenario outputs. | Continuing or auditing long-running reverse engineering work. |
| `analysis/indexes/` | Reviewable autopilot indexes: primary diff/feature maps and final-gate normalized maps. | Diff entries are classified, reverse-map decisions are normalized, or functional features are grouped. |
| `analysis/subject-cards/` | Iterative subject-card registry and analyst-owned cards below coarse `BF-*` containers. | Building or reviewing concrete subject cards. |
| `analysis/functional-gaps/` | One-card-per-pass map of migration hypotheses, target-release checks, and analyst decisions. | A subject card is being prepared for transition to a newer release. |
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
6. Complete reverse-map coverage and decisions under `analysis/reverse-map/`.
7. Run `python -m one_c_autoresearch final-gate build`.
8. Create complete evidence packs under `analysis/features/<feature-id>/`.
9. Generate `outputs/customization-map.md`, `outputs/customization-map.xlsx`, `outputs/open-questions.csv`, and `outputs/open-questions.xlsx` from the final-gate layer.
10. Write `analysis/final-audit.md`.
11. Run `python -m one_c_autoresearch doctor --deep --strict`; do not claim completion until it passes.

## Reverse-Map Route

Use this route when a continuation trigger such as `/goal Исследование` asks the agent to keep reconstructing functional intent from existing customizations:

1. Read `project.toml`, `docs/method/reverse-functional-map.md`, and `analysis/reverse-map/state.md`.
2. Run `python -m one_c_autoresearch reverse-map claim`.
3. If a workitem is returned, inspect its `source_diff_ids`, source objects, metadata, BSL, forms, roles, scheduled jobs, and overrides.
4. Record evidence and decisions under `analysis/reverse-map/`.
5. Mark runtime-only gaps in `unresolved.csv`.
6. Run `python -m one_c_autoresearch final-gate status`.
7. Run `python -m one_c_autoresearch doctor` and `python -m one_c_autoresearch checks research`.
8. Advance the workitem status with `python -m one_c_autoresearch reverse-map set-status` and stop.

## Subject-Card Route

Use this route when coarse `BF-*` containers need analyst-owned subject cards:

1. Run `python -m one_c_autoresearch detail-map build` when detail maps are stale.
2. Run `python -m one_c_autoresearch subject-card discover`.
3. Review `analysis/subject-cards/candidates.csv`.
4. Run `python -m one_c_autoresearch subject-card classify` and `registry-build`.
5. Run `python -m one_c_autoresearch subject-card seed --from-registry`.
6. Refine one card with `python -m one_c_autoresearch subject-card refine --card <slug>`.
7. Validate with `python -m one_c_autoresearch subject-card validate --card <slug>`.

## Functional-Gap Route

Use this route when the goal is to build a migration gap map for one already prepared subject card:

1. Read `analysis/subject-cards/cards/<slug>/subject-card.json`, `evidence.csv`, and `gaps.csv`.
2. Run `python -m one_c_autoresearch functional-gap build --card <slug>`.
3. Review `analysis/functional-gaps/cards/<slug>/review.md`.
4. Fill or close checks in `checks.csv` as target-release evidence appears.
5. Run `python -m one_c_autoresearch functional-gap validate --card <slug>`.
6. Stop after one subject card; start a new pass for the next card.

## System Of Record

- `project.toml`: active source paths, RLM projects, MCP/web target, and evidence policy.
- `.codex/1c-mcp.toml`: active MCP/web target when present; it must match `project.toml` before live evidence is used.
- `analysis/queue/tasks.jsonl`: current queue state.
- `analysis/reverse-map/`: durable continuation state for reverse functional mapping.
- `analysis/subject-cards/`: durable subject-card registry and generated card bundles.
- `analysis/functional-gaps/`: durable one-card-per-pass state for migration gap mapping.
- `analysis/indexes/diff-inventory.csv`: every clean diff entry and its classification status.
- `analysis/indexes/feature-map.csv`: functional grouping for the final customization map.
- `analysis/indexes/final-diff-inventory.csv`: publishable row-level map after reverse-map normalization.
- `analysis/indexes/final-feature-map.csv`: publishable feature-level map after reverse-map normalization.
- `analysis/features/`: durable feature evidence.
- `docs/method/evidence-pack-schema.md`: canonical CSV headers and feature pack file contract.
- `docs/method/autopilot-customization-map.md`: final-map pipeline and completion gate.
- `outputs/`: final human-facing deliverables.
- `docs/agent/verification.md`: validation commands and health gate meaning.

Do not use this generated repository as evidence for another customer project.
