# Detail Maps

Detail maps describe concrete customization subjects below the `BF-*` level:
documents, catalogs, routes, scheduled jobs, access models, integrations,
reports, and UI surfaces.

Each map lives in its own folder:

```text
analysis/detail-maps/<slug>/detail-map.json
analysis/detail-maps/generated/<slug>/detail-map.json
```

Build the generated inventory from current final-gate and reverse-map artifacts:

```bash
python -m one_c_autoresearch detail-map build
```

The builder writes:

- `analysis/detail-maps/index.csv`
- `analysis/detail-maps/generated/<slug>/detail-map.json`

Generated maps use `generation_mode=generated` and `completeness=generated_seed`.
They are start points for analyst drill-down, not final human conclusions. Manual
or enriched maps outside `generated/` are not overwritten by the builder.

The `review-dashboard` command reads every `detail-map.json` under this folder
and renders them as the `Карты доработок` section. `BF-*` blocks remain the
scenario-level navigation layer; detail maps provide the analyst-facing drill
down into attributes, form rules, validations, lifecycle, rights, jobs, UI,
integrations, source traces, open questions, and migration notes.

Use `_templates/detail-map.json` as the starting contract. Keep stable field
names in English for machine processing. Write analyst-facing values in the
language of the research project.
