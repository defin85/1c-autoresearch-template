# Repo Instructions

This is a concrete 1C autoresearch repository created from `1c-autoresearch-template`.

## Source of Truth

- `project.toml` declares source paths, RLM projects, MCP/web policy, and project identity.
- `.codex/1c-mcp.toml`, when present, is the repo-local source of truth for the active 1C MCP/web target and must match `project.toml`.
- `analysis/queue/tasks.jsonl` is the current analysis queue.
- `analysis/indexes/diff-inventory.csv` and `analysis/indexes/feature-map.csv` are the source of truth for the autopilot final customization map when `autopilot.enabled=true`.
- `analysis/features/` contains feature-level evidence packs.
- `outputs/` contains human-facing deliverables.
- `docs/agent/repo-map.md` maps agent entry points and change routing.
- `docs/agent/verification.md` is the canonical verification matrix.

## Operating Rules

- Process one queue task at a time.
- Use `$1c-autoresearch-queue-worker` when selecting, claiming, executing, or updating queue tasks.
- For an end-to-end customization map, follow `docs/method/autopilot-customization-map.md` instead of stopping after a single queue task.
- Prefer static source evidence and generated indexes before live 1C access.
- Do not use unrelated 1C MCP servers as evidence.
- Before using live 1C MCP or web access, confirm the configured target in `project.toml` and `.codex/1c-mcp.toml` if that file exists.
- If a finding depends on infobase data, mark it as `needs_infobase_data`.
- Record file and line evidence for confirmed claims where possible.
- Keep generated intermediate indexes under `analysis/cache/`.
- Keep final deliverables under `outputs/`.
- Do not claim final customization-map completion while `python -m one_c_autoresearch doctor --deep --strict` fails with `autopilot.enabled=true`.

## Verification

See `docs/agent/verification.md` for the full verification runbook.

```bash
python -m one_c_autoresearch doctor
python -m one_c_autoresearch checks research
```
