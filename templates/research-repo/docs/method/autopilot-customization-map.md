# Autopilot Customization Map

This runbook defines the end-to-end contract for building a final 1C customization map from a vendor baseline, a customer-modified configuration, optional extensions, and optional live infobase evidence.

The goal is a completed functional map, not a list of follow-up tasks. Runtime-only gaps are allowed only as formal `open_question` or `blocked_by_infobase_data` records with reason, closure method, and impact.

## Inputs

Declare inputs in `project.toml`:

- `paths.vendor_baseline`: vendor baseline source dump.
- `paths.target_cf`: customer-modified configuration source dump.
- `paths.target_cfe`: optional customer extension source dump.
- `paths.next_vendor`: optional newer vendor release for future gap analysis.
- `rlm.*`: optional `rlm-tools-bsl` project names.
- `mcp` and `web`: optional live access channels.
- `autopilot.enabled`: set to `true` only when the final customization map gate should be enforced by `doctor`.

## Pipeline

1. **Intake**: validate `project.toml`, source paths, optional indexes, and access policy.
2. **Source normalization**: inspect source trees and exclude generated caches, parent configurations, dump metadata noise, and other known non-business dump artifacts.
3. **Physical clean rebase**: create or reuse a cleaned comparison repo where the custom configuration is physically cleaned against the vendor baseline. The final analysis must use this clean diff, not raw dump noise.
4. **Diff inventory**: classify every diff entry in `analysis/indexes/diff-inventory.csv`.
5. **Feature discovery**: group classified diff entries into functional features in `analysis/indexes/feature-map.csv`.
6. **Reverse functional map**: review the primary classifications under `analysis/reverse-map/`; this state is the source of truth for downgrades, reassignment, runtime blockers, and manual-review blockers.
7. **Final gate normalization**: run `python -m one_c_autoresearch final-gate build`. Final deliverables must read `analysis/indexes/final-diff-inventory.csv` and `analysis/indexes/final-feature-map.csv`, not the primary inventory directly.
8. **Feature deep dives**: create evidence packs under `analysis/features/<feature-id>/` for every publishable non-noise feature.
9. **Infobase evidence**: use live infobase data when allowed. If runtime evidence is unavailable, write formal open questions instead of TODO items.
10. **Final map generation**: produce the Markdown and XLSX deliverables under `outputs/` from the final-gate layer.
11. **Final audit**: write `analysis/final-audit.md` with coverage counts and completion evidence.
12. **Analyst review dashboard**: run `python -m one_c_autoresearch review-dashboard build` to generate the static analyst surface under `outputs/review/`.
13. **Doctor gate**: run `python -m one_c_autoresearch doctor --deep --strict`. With `autopilot.enabled=true`, the gate must prove that every diff entry is classified and every final claim is consistent with reverse-map decisions.

## Required Artifacts

```text
analysis/indexes/diff-inventory.csv
analysis/indexes/feature-map.csv
analysis/indexes/final-diff-inventory.csv
analysis/indexes/final-feature-map.csv
analysis/features/<feature-id>/brief.md
analysis/features/<feature-id>/findings.md
analysis/features/<feature-id>/evidence.csv
analysis/features/<feature-id>/open-questions.md
analysis/features/<feature-id>/review.md
outputs/customization-map.md
outputs/customization-map.xlsx
outputs/open-questions.csv
outputs/open-questions.xlsx
outputs/review/index.html
outputs/review/data.json
analysis/final-audit.md
```

## Diff Inventory Contract

`analysis/indexes/diff-inventory.csv` must use this header:

```csv
diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes
```

Allowed `status` values:

- `mapped_to_feature`: real customization mapped to a feature.
- `technical_noise_removed`: proven dump noise removed or excluded from business analysis.
- `requires_1c_review`: technical-looking change that may affect behavior.
- `blocked_by_infobase_data`: source evidence is insufficient and live data is required.

No row may remain empty, unclassified, or assigned to a missing feature.

## Final Gate Contract

`analysis/indexes/final-diff-inventory.csv` must use this header:

```csv
diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes,reverse_status,reverse_confidence,reverse_scenario_id,final_feature_id,final_status,final_action,blocking_reason
```

The final gate joins primary diff rows with `analysis/reverse-map/coverage.csv`.

- `confirmed_in_scenario` and `supporting_shared` allow publication.
- `needs_manual_review`, `needs_infobase_data`, `needs_runtime_verification`, `needs_reclassification`, and `cross_scenario_reclassification` block or downgrade the feature.
- `technical_noise` and `out_of_scope` are excluded from business counts.
- `technical_platform` is supporting/platform evidence only, not standalone business functionality.
- `belongs_to_other_scenario` moves the row out of the source feature.

Run:

```bash
python -m one_c_autoresearch final-gate build
python -m one_c_autoresearch final-gate verify
```

`verify` returns non-zero while final publication is blocked by reverse-map rows.

## Feature Map Contract

`analysis/indexes/feature-map.csv` must use this header:

```csv
feature_id,title,domain,source_bucket,classification,confidence,status,owner,summary,evidence_pack_path,open_questions_path,outputs,notes
```

Allowed `status` values:

- `complete`
- `blocked_by_infobase_data`
- `requires_1c_review`
- `requires_runtime_verification`
- `needs_reclassification`
- `out_of_scope`

Every feature that is not `out_of_scope` must point to a complete evidence pack.

## Open Questions Contract

`outputs/open-questions.csv` must use this header:

```csv
question_id,feature_id,status,reason,closure_method,impact,source_ref,owner,notes
```

Allowed `status` values:

- `open_question`
- `blocked_by_infobase_data`
- `closed`

Every open row must include `reason`, `closure_method`, and `impact`. Do not use generic TODO wording.

## Final Audit Contract

`analysis/final-audit.md` must contain:

- `Coverage status: complete`
- `Unclassified diff entries: 0`
- starting and final diff counts;
- removed noise classes;
- preserved customization classes;
- feature count;
- open question count and closure plan;
- commands used for final verification.

## Scaffold Command

From a concrete research repo:

```bash
python -m one_c_autoresearch autopilot scaffold --enable-gate
```

The scaffold creates the required index and output paths. Enabling the gate intentionally makes `doctor` fail until the final map is complete.

## Review Dashboard

Generate the static analyst review dashboard after final-gate normalization and final outputs are current:

```bash
python -m one_c_autoresearch review-dashboard build
```

The command writes:

- `outputs/review/index.html`: a self-contained Russian-language review surface with summary metrics, BF block details, evidence samples, open questions, infobase/runtime checks, and migration-impact notes for the move to a newer release such as ДО 3.0.
- `outputs/review/data.json`: the reproducible data snapshot used by the HTML page.

The dashboard also reads optional concrete subject maps from
`analysis/detail-maps/*/detail-map.json`. These maps are the drill-down layer
below `BF-*`: documents, catalogs, routes, scheduled jobs, rights, integrations,
reports, and UI surfaces with attributes, form rules, validations, lifecycle,
roles, source traces, open questions, and migration notes.

The dashboard is not a separate source of truth. It must be regenerated from `analysis/indexes/`, `analysis/reverse-map/`, `analysis/detail-maps/`, scenario summaries/evidence, `outputs/open-questions.*`, `outputs/customization-map.*`, and `analysis/final-audit.md`.
