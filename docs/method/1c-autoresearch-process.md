# 1C Autoresearch Process

This method is for comparing:

- a vendor baseline configuration;
- a customer-modified configuration;
- optional customer extensions;
- optional next vendor release.

The goal is a functional gap map, not a raw diff report.

## Evidence Levels

- `high`: direct source evidence from BSL/XML/metadata, with file and line.
- `medium`: strong static relation through marker, type, form, role, or call graph, but runtime values are unknown.
- `low`: plausible hypothesis that requires live infobase, customer confirmation, or scenario execution.

When behavior depends on infobase data, mark it as `needs_infobase_data`.

## Pipeline

1. Build or import indexes:
   - file diff index;
   - change index;
   - role rights index;
   - form element index;
   - symbol occurrence index.
2. Split raw diffs into functional buckets.
3. Convert bucket findings into feature candidates.
4. Run deep dives per feature.
5. Run independent review tasks.
6. Produce a functional customization register.
7. Compare features against the next vendor release.
8. Classify each feature:
   - standard;
   - standard with settings;
   - covered by existing customization;
   - requires development;
   - disputed or requires clarification.

For an autonomous final customization map, use the stricter contract in
`docs/method/autopilot-customization-map.md`. That workflow adds a physical
clean-rebase stage, mandatory diff-inventory coverage, final outputs, and a
doctor gate that rejects unclassified diff entries.

## Deep Dive Coverage

For each feature, inspect:

- metadata objects and attributes;
- forms, commands, element visibility, mandatory flags, and event handlers;
- write and before-write validations;
- lifecycle state transitions;
- scheduled/background behavior;
- business processes and tasks;
- roles, rights, workgroups, and routing participants;
- extension overrides and borrowed objects;
- vendor delta: added, changed, or vendor-existing.

## Output Contract

Each feature pack should be understandable without re-reading the full repository:

- `brief.md`: business meaning and scope.
- `findings.md`: grouped functional findings.
- `evidence.csv`: machine-readable source evidence following `docs/method/evidence-pack-schema.md`.
- `open-questions.md`: unresolved facts and required evidence.
- `review.md`: independent review result.
- `artifacts/`: optional Excel/CSV/raw extracts.

The final map deliverables are generated from feature packs and indexes:

- `outputs/customization-map.md`
- `outputs/customization-map.xlsx`
- `outputs/open-questions.csv`
- `outputs/open-questions.xlsx`
- `analysis/final-audit.md`
