# Clean Comparison

This directory stores durable state for physical cleanup of a raw vendor-versus-customer configuration diff.

Use it together with `docs/method/physical-clean-comparison.md`.

Keep bulky nested git repositories and raw exporter dumps under `analysis/cache/noise/`. Keep the review queue, decision ledger, summaries, and reports here when they are part of the project audit trail.

Typical files:

```text
refinement-queue.csv
decisions.jsonl
summary.json
batches/
reports/
```

When the layer needs analyst review, generate a static dashboard under `outputs/clean-comparison-dashboard/` or a documented layer-specific output directory such as `outputs/v8unpack-refinement-dashboard/`.
