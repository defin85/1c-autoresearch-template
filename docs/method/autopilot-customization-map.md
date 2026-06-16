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
3. **Physical clean comparison**: follow `docs/method/physical-clean-comparison.md` to create or reuse a cleaned comparison repo where the custom configuration is physically cleaned against the vendor baseline. The final analysis must use this clean diff, not raw dump noise, and the cleanup decisions must have a queue, ledger, summary, and dashboard when analyst review is needed.
4. **Diff inventory**: classify every diff entry in `analysis/indexes/diff-inventory.csv`.
5. **Feature discovery**: group classified diff entries into functional features in `analysis/indexes/feature-map.csv`.
6. **Reverse functional map**: review the primary classifications under `analysis/reverse-map/`; this state is the source of truth for downgrades, reassignment, runtime blockers, and manual-review blockers.
7. **Final gate normalization**: run `python -m one_c_autoresearch final-gate build`. Final deliverables must read `analysis/indexes/final-diff-inventory.csv` and `analysis/indexes/final-feature-map.csv`, not the primary inventory directly.
8. **Feature deep dives**: create evidence packs under `analysis/features/<feature-id>/` for every publishable non-noise feature.
9. **Infobase evidence**: close `needs_infobase_data` blockers through the live evidence loop in `analysis/reverse-map/infobase-checks.csv` when live access is allowed. If runtime evidence is unavailable, write formal open questions instead of TODO items.
10. **Final map generation**: produce the Markdown and XLSX deliverables under `outputs/` from the final-gate layer.
11. **Subject-card layer**: build the analyst-owned subject-card registry below coarse `BF-*` containers. Run `subject-card discover`, review/classify the candidates, build the registry, seed accepted cards, refine cards, and validate the layer. A publishable autopilot map must not stop at BF containers.
12. **Final audit**: write `analysis/final-audit.md` with coverage counts and completion evidence.
13. **Analyst review dashboard**: run `python -m one_c_autoresearch detail-map build` and then `python -m one_c_autoresearch review-dashboard build` to generate the static analyst surface under `outputs/review/`. The first dashboard screen must contain concrete subject cards, not only BF groups or generated technical maps.
14. **Doctor gate**: run `python -m one_c_autoresearch doctor --deep --strict`. With `autopilot.enabled=true`, the gate must prove that every diff entry is classified, every final claim is consistent with reverse-map decisions, and the subject-card layer exposes ready analyst cards.

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
analysis/reverse-map/infobase-checks.csv
outputs/customization-map.md
outputs/customization-map.xlsx
outputs/infobase-questions.csv
outputs/open-questions.csv
outputs/open-questions.xlsx
outputs/review/index.html
outputs/review/data.json
analysis/subject-cards/candidates.csv
analysis/subject-cards/classification.csv
analysis/subject-cards/registry.csv
analysis/subject-cards/coverage.csv
analysis/subject-cards/cards/<slug>/subject-card.json
analysis/subject-cards/cards/<slug>/evidence.csv
analysis/subject-cards/cards/<slug>/gaps.csv
analysis/subject-cards/cards/<slug>/review.md
analysis/final-audit.md
```

When a physical cleanup step is required, also keep a durable clean-comparison
layer before building `analysis/indexes/diff-inventory.csv`:

```text
analysis/clean-comparison/README.md
analysis/clean-comparison/refinement-queue.csv
analysis/clean-comparison/decisions.jsonl
analysis/clean-comparison/summary.json
outputs/clean-comparison-dashboard/index.html
outputs/clean-comparison-dashboard/data.json
```

Layer-specific names such as `analysis/v8unpack-refinement/` and
`outputs/v8unpack-refinement-dashboard/` are allowed when documented in the
layer README. The downstream diff inventory must cite the clean diff source.

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

## Infobase Evidence Contract

Live infobase checks are used only after static evidence has identified a
concrete fact that cannot be proven from source dumps. Do not start with live
queries while static source evidence is still missing.

Record every live check in `analysis/reverse-map/infobase-checks.csv`:

```csv
check_id,item_id,workitem_id,diff_id,scenario_id,feature_id,subject_card_slug,question_ref,check_method,custom_target,vendor_target,query_or_probe,custom_result,vendor_result,result,status_before_pass,status_after_pass,evidence_ref,checked_by,checked_at,notes
```

Allowed `check_method` values:

- `1c_mcp_run_select_query`: read-only query through 1C MCP.
- `1c_mcp_debug_execute_bsl`: BSL probe through 1C MCP; use only when a query is insufficient.
- `playwright_1c_web_ui`: web UI scenario check.
- `direct_postgresql_query`: direct DBMS read when 1C access is unavailable and the table mapping is known.
- `manual_1c_scenario`: operator-performed scenario check.

Allowed `result` values:

- `custom_only`: confirmed only in the customer/custom infobase.
- `same_as_vendor`: custom and vendor behavior/data are equivalent for the checked fact.
- `vendor_differs`: custom and vendor differ for the checked fact.
- `runtime_only`: the fact exists only as runtime data or settings, not as a source customization.
- `manual_scenario_required`: a UI or business scenario is needed before the fact can be closed.
- `inconclusive`: live evidence did not answer the question.

Allowed `status_after_pass` values:

- `closed`: the infobase question is answered and linked by `evidence_ref`.
- `needs_infobase_data`: more live data is still required.
- `needs_runtime_verification`: a runtime scenario must still be executed.
- `needs_manual_review`: business interpretation is still unclear.
- `blocked_by_infobase_data`: access or data is unavailable and the final output must keep an open question.

When both custom and vendor infobases are available, run the same read-only check
against both and fill `custom_target`, `vendor_target`, `custom_result`, and
`vendor_result`. Use custom-only evidence only for custom behavior; use the
vendor target only as a baseline comparison.

`outputs/infobase-questions.csv` is the autopilot input registry for subject-card
questions that require infobase data. It must use this header:

```csv
question_id,subject_card_slug,feature_id,object_or_setting,check_target,reason,closing_result,risk_if_open,source_ref,status
```

Allowed `status` values:

- `open`
- `closed`
- `blocked_by_infobase_data`

Every unresolved row with status `needs_infobase_data`, and every
`outputs/infobase-questions.csv` row with status `open`, must either have a
closed row in `analysis/reverse-map/infobase-checks.csv` or be carried into
`outputs/open-questions.csv` with `status=blocked_by_infobase_data`, a concrete
closure method, and impact. `final-gate build` promotes open infobase questions
into `outputs/open-questions.csv`; `final-gate status` remains blocked until the
questions are closed by live-check rows or accepted as final blockers. A final
feature may be marked `complete` only after the related live checks are `closed`
or the unresolved fact has been downgraded out of the published claim.

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
- physical clean-comparison source, raw tags, clean commit, and clean diff command when cleanup was required;
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
python -m one_c_autoresearch detail-map build
python -m one_c_autoresearch subject-card discover
python -m one_c_autoresearch subject-card classify
python -m one_c_autoresearch subject-card registry-build
python -m one_c_autoresearch subject-card seed --from-registry
python -m one_c_autoresearch subject-card refine --card <slug>
python -m one_c_autoresearch subject-card validate
python -m one_c_autoresearch review-dashboard build
```

The command writes:

- `outputs/review/index.html`: a self-contained Russian-language review surface with summary metrics, BF block details, evidence samples, open questions, infobase/runtime checks, and migration-impact notes for the move to a newer release such as ДО 3.0.
- `outputs/review/data.json`: the reproducible data snapshot used by the HTML page.

Before the dashboard build, `detail-map build` creates a reproducible inventory
in `analysis/detail-maps/index.csv` and generated maps in
`analysis/detail-maps/generated/<slug>/detail-map.json` from
`analysis/indexes/final-diff-inventory.csv` and `analysis/reverse-map/coverage.csv`.
Manual or enriched maps outside `generated/` remain analyst-owned artifacts.

The dashboard reads concrete subject cards from
`analysis/subject-cards/cards/<slug>/subject-card.json` and uses
`analysis/detail-maps/**/*.json` as the technical drill-down layer below those
cards. The first analyst screen is the subject-card registry. `BF-*` groups and
generated detail maps are supporting layers; they are not sufficient by
themselves for a completed customization map.

The dashboard is not a separate source of truth. It must be regenerated from `analysis/indexes/`, `analysis/reverse-map/`, `analysis/detail-maps/`, scenario summaries/evidence, `outputs/open-questions.*`, `outputs/customization-map.*`, and `analysis/final-audit.md`.

The final review dashboard does not replace the clean-comparison dashboard from
`docs/method/physical-clean-comparison.md`. The clean-comparison dashboard proves
the physical diff cleanup before classification; `outputs/review/` presents the
final functional map after reverse-map and final-gate normalization.
