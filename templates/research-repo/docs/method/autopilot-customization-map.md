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
6. **Feature deep dives**: create evidence packs under `analysis/features/<feature-id>/` for every non-noise feature.
7. **Infobase evidence**: use live infobase data when allowed. If runtime evidence is unavailable, write formal open questions instead of TODO items.
8. **Final map generation**: produce the Markdown and XLSX deliverables under `outputs/`.
9. **Final audit**: write `analysis/final-audit.md` with coverage counts and completion evidence.
10. **Doctor gate**: run `python -m one_c_autoresearch doctor --deep --strict`. With `autopilot.enabled=true`, the gate must prove that every diff entry is classified.

## Required Artifacts

```text
analysis/indexes/diff-inventory.csv
analysis/indexes/feature-map.csv
analysis/features/<feature-id>/brief.md
analysis/features/<feature-id>/findings.md
analysis/features/<feature-id>/evidence.csv
analysis/features/<feature-id>/open-questions.md
analysis/features/<feature-id>/review.md
outputs/customization-map.md
outputs/customization-map.xlsx
outputs/open-questions.csv
outputs/open-questions.xlsx
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

## Feature Map Contract

`analysis/indexes/feature-map.csv` must use this header:

```csv
feature_id,title,domain,source_bucket,classification,confidence,status,owner,summary,evidence_pack_path,open_questions_path,outputs,notes
```

Allowed `status` values:

- `complete`
- `blocked_by_infobase_data`
- `requires_1c_review`
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
