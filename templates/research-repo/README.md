# __PROJECT_ID__ Research

Concrete 1C autoresearch repository.

## Sources

Source paths are declared in `project.toml`.

## First Commands

Show the next queue task without claiming it:

```bash
python -m one_c_autoresearch queue get
```

Claim one task when starting work:

```bash
python -m one_c_autoresearch queue claim
```

Validate the repository:

```bash
python -m one_c_autoresearch doctor
python -m one_c_autoresearch checks research
```

Continue reverse functional mapping:

```bash
python -m one_c_autoresearch reverse-map claim
python -m one_c_autoresearch final-gate status
```

If the reverse-map queue is empty, the command seeds workitems from uncovered rows in `analysis/indexes/diff-inventory.csv`.

Build the static analyst review dashboard:

```bash
python -m one_c_autoresearch detail-map build
python -m one_c_autoresearch review-dashboard build
```

## Layout

```text
analysis/cache/      generated indexes and noisy machine data
analysis/detail-maps/ analyst-level maps for concrete customization subjects
analysis/queue/      file-backed work queue
analysis/reverse-map/ durable state for fact-to-intent reverse mapping
analysis/features/   feature evidence packs
analysis/runs/       run logs
outputs/             human-facing deliverables
docs/                method and agent guidance
scripts/             queue and validation helpers
.agents/skills/      repo-local Codex workflows
```

Agent navigation starts in `docs/agent/repo-map.md`. Verification details live in `docs/agent/verification.md`.

## Autopilot Customization Map

For an end-to-end final customization map, use:

```bash
python -m one_c_autoresearch autopilot scaffold --enable-gate
```

Then follow `docs/method/autopilot-customization-map.md`. With `autopilot.enabled=true`, `python -m one_c_autoresearch doctor --deep --strict` fails until every diff entry is classified, reverse-map decisions are normalized through `python -m one_c_autoresearch final-gate build`, every feature pack has evidence, final outputs exist, and `analysis/final-audit.md` declares complete coverage without reverse-map blockers.

After final outputs are current, run `python -m one_c_autoresearch detail-map build` to create the generated subject inventory. Add or enrich analyst-owned maps under `analysis/detail-maps/` when a block needs deeper review, then run `python -m one_c_autoresearch review-dashboard build` to create `outputs/review/index.html` and `outputs/review/data.json`.
