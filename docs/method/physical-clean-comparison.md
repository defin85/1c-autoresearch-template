# Physical Clean Comparison

This runbook defines the reusable contract for physically cleaning a customer configuration dump against a vendor baseline before business analysis starts.

Use it when raw source dumps contain exporter noise, binary-form churn, ordering churn, generated identifiers, vendor metadata drift, or other changes that would hide the real customization diff.

The result is not the final customization map. The result is a deterministic clean diff and an analyst-facing audit trail that proves which raw differences were removed, preserved, or left for manual review.

## Inputs

Declare or record these inputs in the concrete research repo:

- `paths.vendor_baseline`: vendor baseline source dump.
- `paths.target_cf`: customer-modified source dump.
- optional exporter-specific dumps such as `v8unpack`, `ibcmd`, EDT, or other source formats.
- raw comparison repository path when a nested git repo is used, usually under `analysis/cache/noise/`.
- immutable raw tags or commits for the vendor baseline and customer dump.
- exporter version, platform version, command line, and normalization options.

Do not rewrite the raw baseline or raw target commits. All cleanup decisions must be represented by new commits, ledgers, or generated state.

## Artifact Layout

For a generic clean-comparison layer, use:

```text
analysis/clean-comparison/README.md
analysis/clean-comparison/refinement-queue.csv
analysis/clean-comparison/decisions.jsonl
analysis/clean-comparison/summary.json
analysis/clean-comparison/batches/
analysis/clean-comparison/reports/
outputs/clean-comparison-dashboard/index.html
outputs/clean-comparison-dashboard/data.json
```

Project-specific layers may use a more explicit slug, for example:

```text
analysis/v8unpack-refinement/
outputs/v8unpack-refinement-dashboard/
```

The slug must be stable and documented in `analysis/clean-comparison/README.md` or the layer-specific README. Keep nested git repositories and bulky raw dumps under `analysis/cache/noise/`; keep durable decisions, summaries, and analyst dashboards in the tracked analysis and outputs layers when the project policy allows tracking them.

## Queue Contract

`refinement-queue.csv` should contain one row per object, group, or review unit:

```csv
item_id,source,path,object_kind,object_name,status,decision,noise_class,feature_hint,risk,raw_diff_count,clean_diff_count,evidence_ref,notes
```

Allowed `status` values:

- `pending`: not reviewed yet.
- `kept`: real customization preserved in the clean diff.
- `reverted`: exporter or dump noise removed from the clean diff.
- `split`: the item was split into smaller review units.
- `manual_review`: the item needs human review before final classification.
- `blocked`: required evidence is missing.

Allowed `decision` values:

- `preserve_customization`
- `remove_noise`
- `split_for_review`
- `needs_manual_review`
- `blocked_by_missing_evidence`

`decisions.jsonl` records the detailed rationale. Each entry should include:

- `item_id`
- `decision`
- `rationale`
- `evidence`
- `commands`
- `before_commit`
- `after_commit`
- `reviewer`
- `timestamp`

## Dashboard Contract

Generate a static analyst dashboard whenever the clean comparison has many review units, binary or form changes, or any manual-review queue. The dashboard is mandatory for large unmanaged-form configurations and other cases where a table alone is not a usable review surface.

The dashboard writes:

- `outputs/clean-comparison-dashboard/index.html`: self-contained HTML that can be opened without a server.
- `outputs/clean-comparison-dashboard/data.json`: reproducible data snapshot used by the HTML page.

Layer-specific paths such as `outputs/v8unpack-refinement-dashboard/index.html` are allowed when the README names the layer explicitly.

The dashboard must provide Russian analyst-facing labels and must include:

- summary counts for raw rows, clean rows, kept items, reverted noise, manual-review items, and blockers;
- filters by status, decision, noise class, object kind, feature hint, risk, batch, and source exporter;
- object-level detail with paths, raw and clean diff counts, rationale, evidence references, and related commits;
- first-pass and refinement decisions when both exist;
- copyable git commands for inspecting the raw and clean diff;
- explicit lists of unresolved items and blocked evidence.

The dashboard is a review surface, not a source of truth. Regenerate it from the queue, decisions ledger, summary, nested git state, and reports.

## Done Definition

The clean-comparison layer is complete only when:

- vendor and customer raw commits or tags are immutable and named in the summary;
- the nested comparison repo has no uncommitted cleanup work;
- every queue item is `kept`, `reverted`, `split`, `manual_review`, or `blocked`;
- every non-`pending` item has a decision and rationale;
- every `manual_review` or `blocked` item has owner, reason, and closure method;
- raw diff count, clean diff count, removed noise classes, and preserved customization classes are recorded;
- the command for reproducing the clean diff is recorded;
- the dashboard exists when the project has a manual-review queue or a large comparison layer;
- the downstream diff inventory uses the clean diff, not the raw dump diff.

## Relation To Autopilot

`docs/method/autopilot-customization-map.md` consumes the clean diff produced here. The final `outputs/review/` dashboard remains the publishable analyst surface after `final-gate`. The clean-comparison dashboard is an intermediate audit surface that proves the physical cleanup step before feature classification and reverse-map normalization.
