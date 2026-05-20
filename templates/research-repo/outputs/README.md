# Outputs

Human-facing deliverables go here:

- XLSX reports;
- final Markdown summaries;
- presentation-ready CSVs;
- customer question registers;
- development backlog seeds.

Intermediate indexes and noisy diagnostics belong under `analysis/cache/`.

The autopilot final-map workflow produces:

- `customization-map.md`
- `customization-map.xlsx`
- `open-questions.csv`
- `open-questions.xlsx`
- `review/index.html`
- `review/data.json`

When `autopilot.enabled=true`, these files are checked by `python -m one_c_autoresearch doctor`.

Build the analyst review dashboard after final-gate outputs are current:

```bash
python -m one_c_autoresearch review-dashboard build
```
