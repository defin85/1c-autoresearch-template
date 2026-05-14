# Repo Instructions

This is a concrete 1C autoresearch repository created from `1c-autoresearch-template`.

## Source of Truth

- `project.toml` declares source paths, RLM projects, MCP/web policy, and project identity.
- `.codex/1c-mcp.toml`, when present, is the repo-local source of truth for the active 1C MCP/web target and must match `project.toml`.
- `analysis/queue/tasks.jsonl` is the current analysis queue.
- `analysis/features/` contains feature-level evidence packs.
- `outputs/` contains human-facing deliverables.
- `docs/agent/repo-map.md` maps agent entry points and change routing.
- `docs/agent/verification.md` is the canonical verification matrix.

## Operating Rules

- Process one queue task at a time.
- Use `$1c-autoresearch-queue-worker` when selecting, claiming, executing, or updating queue tasks.
- Prefer static source evidence and generated indexes before live 1C access.
- Do not use unrelated 1C MCP servers as evidence.
- Before using live 1C MCP or web access, confirm the configured target in `project.toml` and `.codex/1c-mcp.toml` if that file exists.
- If a finding depends on infobase data, mark it as `needs_infobase_data`.
- Record file and line evidence for confirmed claims where possible.
- Keep generated intermediate indexes under `analysis/cache/`.
- Keep final deliverables under `outputs/`.

## Verification

See `docs/agent/verification.md` for the full verification runbook.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\doctor.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\Test-ResearchRepo.ps1
```
