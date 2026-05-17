# Evidence Pack Schema

Feature evidence packs live under `analysis/features/<feature-id>/`.

Use the files below for every feature pack that reaches `evidence_pack`,
`drafted`, `needs_review`, or `done`.

## Required Files

```text
brief.md
findings.md
evidence.csv
open-questions.md
review.md
```

Task-specific discovery packs may also produce `feature-candidates.csv`.

Autopilot final-map indexes use separate contracts in
`docs/method/autopilot-customization-map.md`.

## evidence.csv

Canonical header:

```csv
feature_id,claim_id,source_kind,source_path,line_start,line_end,evidence_type,confidence,summary,notes
```

Column meanings:

| Column | Meaning |
| --- | --- |
| `feature_id` | Stable feature folder id. |
| `claim_id` | Stable id for the claim in `findings.md`, for example `F-001`. |
| `source_kind` | Evidence source category. Prefer `vendor_baseline`, `target_cf`, `target_cfe`, `next_vendor`, `index`, `mcp`, `web`, or `inference`. |
| `source_path` | Source file path, index path, MCP target, or web URL used as evidence. |
| `line_start` | 1-based starting line when source evidence has lines. Leave empty when not applicable. |
| `line_end` | 1-based ending line when source evidence spans multiple lines. Leave empty when not applicable. |
| `evidence_type` | Evidence area, for example `metadata`, `bsl`, `form`, `role`, `scheduled_job`, `business_process`, `task`, `register`, `report`, `integration`, or `runtime_data`. |
| `confidence` | `high`, `medium`, or `low`. |
| `summary` | Short evidence summary. |
| `notes` | Optional caveats, runtime dependencies, or follow-up pointers. |

## feature-candidates.csv

Canonical header:

```csv
feature_id,title,source_bucket,classification,confidence,summary,next_step
```

Use `classification` values aligned with the method:

- `standard`
- `standard_with_settings`
- `covered_by_existing_customization`
- `requires_development`
- `disputed`
- `requires_clarification`

## Markdown Files

`brief.md` should define business meaning and scope.

`findings.md` should group claims by functional behavior and reference
`claim_id` values from `evidence.csv`.

`open-questions.md` should include runtime-data dependencies and customer
questions. Mark runtime-only facts as `needs_infobase_data`.

`review.md` should record independent review outcome, gaps, and whether the
quality gates were met.
