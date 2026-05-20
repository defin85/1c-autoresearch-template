# Detail Maps

Detail maps describe concrete customization subjects below the `BF-*` level:
documents, catalogs, routes, scheduled jobs, access models, integrations,
reports, and UI surfaces.

Each map lives in its own folder:

```text
analysis/detail-maps/<slug>/detail-map.json
```

The `review-dashboard` command reads every `detail-map.json` under this folder
and renders them as the `Карты доработок` section. `BF-*` blocks remain the
scenario-level navigation layer; detail maps provide the analyst-facing drill
down into attributes, form rules, validations, lifecycle, rights, jobs, UI,
integrations, source traces, open questions, and migration notes.

Use `_templates/detail-map.json` as the starting contract. Keep stable field
names in English for machine processing. Write analyst-facing values in the
language of the research project.
