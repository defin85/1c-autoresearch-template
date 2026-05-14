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
