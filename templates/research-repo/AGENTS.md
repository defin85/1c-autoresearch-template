# Repo Instructions

This is a concrete 1C autoresearch repository created from `1c-autoresearch-template`.

## Source of Truth

- `project.toml` declares source paths, RLM projects, MCP/web policy, and project identity.
- `.codex/1c-mcp.toml`, when present, is the repo-local source of truth for the active 1C MCP/web target and must match `project.toml`.
- `analysis/queue/tasks.jsonl` is the current analysis queue.
- `analysis/indexes/diff-inventory.csv` and `analysis/indexes/feature-map.csv` are the primary autopilot inventory.
- `analysis/indexes/final-diff-inventory.csv` and `analysis/indexes/final-feature-map.csv` are the publishable autopilot layer after reverse-map normalization.
- `analysis/clean-comparison/` is the durable state for physical cleanup of raw vendor-vs-customer diffs before diff inventory generation.
- `analysis/reverse-map/` is the durable state for long-running fact-to-intent reverse mapping. Agent context is disposable; continuation must read this folder.
- `analysis/subject-cards/` is the durable registry and bundle layer for analyst-owned subject cards.
- `analysis/functional-gaps/` is the durable one-card-per-pass layer for migration gap hypotheses and target-release checks.
- `analysis/features/` contains feature-level evidence packs.
- `outputs/` contains human-facing deliverables.
- `docs/agent/repo-map.md` maps agent entry points and change routing.
- `docs/agent/verification.md` is the canonical verification matrix.

## Operating Rules

- Process one queue task at a time.
- Use `$1c-autoresearch-queue-worker` when selecting, claiming, executing, or updating queue tasks.
- For reverse engineering continuation triggers such as `/goal Исследование`, follow `docs/method/reverse-functional-map.md`: run `python -m one_c_autoresearch reverse-map claim`, process one workitem, update reverse-map state, run `python -m one_c_autoresearch final-gate status`, verify, and stop.
- For an end-to-end customization map, follow `docs/method/autopilot-customization-map.md` instead of stopping after a single queue task.
- When raw source dumps include exporter noise, binary forms, ordering churn, or generated metadata drift, follow `docs/method/physical-clean-comparison.md` before building `analysis/indexes/diff-inventory.csv`.
- For subject-card or functional-gap work, process one card at a time and validate the selected card before moving on.
- Prefer static source evidence and generated indexes before live 1C access.
- Do not use unrelated 1C MCP servers as evidence.
- Before using live 1C MCP or web access, confirm the configured target in `project.toml` and `.codex/1c-mcp.toml` if that file exists.
- If a finding depends on infobase data, mark it as `needs_infobase_data`.
- When a `needs_infobase_data` item is checked against a live infobase, record the pass in `analysis/reverse-map/infobase-checks.csv` with the custom target, vendor target when available, result, final status, and evidence reference.
- Record file and line evidence for confirmed claims where possible.
- Keep generated intermediate indexes under `analysis/cache/`.
- Keep physical cleanup decision queues, ledgers, summaries, and reports under `analysis/clean-comparison/` or a documented layer-specific directory; keep bulky nested repos under `analysis/cache/noise/`.
- Keep final deliverables under `outputs/`.
- Do not generate final deliverables directly from primary `confirmed/high` rows; run final-gate and use the `final-*` indexes.
- Do not claim final customization-map completion while `python -m one_c_autoresearch doctor --deep --strict` fails with `autopilot.enabled=true`.

## Verification

See `docs/agent/verification.md` for the full verification runbook.

```bash
python -m one_c_autoresearch doctor
python -m one_c_autoresearch checks research
```
