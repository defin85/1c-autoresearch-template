# Task Schema

`tasks.jsonl` stores one JSON object per line.

## Required Fields

- `id`
- `type`
- `status`
- `priority`
- `title`
- `feature_id`
- `created_at`
- `updated_at`

## Optional Fields

Use optional fields additively so older agents and scripts can keep reading the queue.

| Field | Meaning |
| --- | --- |
| `dependencies` | Task ids that must be `done` or `skipped` before this task can be selected. |
| `source_paths` | Per-task source hints, usually copied from `project.toml` at task creation time. |
| `scope` | Short list of objects, scenarios, or evidence areas included in this task. |
| `markers` | Known metadata names, procedure names, constants, or other exact source markers. |
| `search_terms` | Human-language terms and synonyms for positive and negative search. |
| `quality_gates` | Coverage gates expected for this task. Values should come from the list below when possible. |
| `expected_outputs` | Files that should exist once the task reaches `evidence_pack`, `drafted`, `needs_review`, or `done`. |
| `evidence_sources` | Paths, indexes, RLM projects, MCP targets, or web sessions used as evidence. |
| `open_questions` | Runtime-data dependencies or customer questions discovered while working. |
| `claimed_by` | Worker id that claimed the task. |
| `claimed_at` | UTC timestamp when the task was claimed. |
| `completed_at` | UTC timestamp when the task reached `done` or `skipped`. |
| `result_summary` | Brief final summary for queue readers. |

## Task Types

- `discovery`
- `deep_dive`
- `review`
- `migration_map`
- `packaging`
- `needs_infobase_data`

## Statuses

- `pending`
- `claimed`
- `evidence_pack`
- `drafted`
- `needs_review`
- `needs_followup`
- `blocked`
- `done`
- `skipped`

## Quality Gates

- `source_lines`
- `vendor_delta`
- `positive_search`
- `negative_search`
- `metadata_checked`
- `bsl_checked`
- `forms_checked`
- `roles_checked`
- `scheduled_jobs_checked`
- `extension_checked`
- `needs_infobase_data_marked`
- `review_passed`
