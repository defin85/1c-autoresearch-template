# Review Dashboard

Static analyst review dashboard generated from current research artifacts.

Build it from the research repository root:

```bash
python -m one_c_autoresearch detail-map build
python -m one_c_autoresearch review-dashboard build
```

Default outputs:

- `index.html`: self-contained HTML page that can be opened without a backend.
- `data.json`: machine-readable snapshot used to audit what the page contains.

The dashboard reads `analysis/indexes/`, `analysis/reverse-map/`,
`analysis/detail-maps/`, scenario summaries, scenario evidence,
`analysis/final-audit.md`, and final `outputs/` deliverables. The detail-map
stage writes `analysis/detail-maps/index.csv` and generated subject maps before
the HTML page is rebuilt.
It is a review surface, not a source of truth; update the underlying CSV/Markdown
artifacts first, then regenerate the dashboard.
