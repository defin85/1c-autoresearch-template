# Outputs

Human-facing deliverables go here:

- XLSX reports;
- final Markdown summaries;
- presentation-ready CSVs;
- customer question registers;
- development backlog seeds.

Intermediate indexes and noisy diagnostics belong under `analysis/cache/`.
Physical cleanup review dashboards are intermediate analyst artifacts and may
live under `outputs/clean-comparison-dashboard/` or a documented layer-specific
directory such as `outputs/v8unpack-refinement-dashboard/`.

The autopilot final-map workflow produces:

- `customization-map.md`
- `customization-map.xlsx`
- `infobase-questions.csv`
- `open-questions.csv`
- `open-questions.xlsx`
- `review/index.html`
- `review/data.json`

When `autopilot.enabled=true`, these files are checked by `python -m one_c_autoresearch doctor`.

Build the analyst review dashboard after final-gate outputs and detail-map
inventory are current:

```bash
python -m one_c_autoresearch detail-map build
python -m one_c_autoresearch review-dashboard build
```
