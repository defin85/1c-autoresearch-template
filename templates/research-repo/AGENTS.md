# Repo Instructions

This is a concrete 1C autoresearch repository created from `1c-autoresearch-template`.

## Source of Truth

- `project.toml` declares source paths, RLM projects, MCP/web policy, and project identity.
- `analysis/queue/tasks.jsonl` is the current analysis queue.
- `analysis/features/` contains feature-level evidence packs.
- `outputs/` contains human-facing deliverables.

## Operating Rules

- Process one queue task at a time.
- Prefer static source evidence and generated indexes before live 1C access.
- Do not use unrelated 1C MCP servers as evidence.
- If a finding depends on infobase data, mark it as `needs_infobase_data`.
- Record file and line evidence for confirmed claims where possible.
- Keep generated intermediate indexes under `analysis/cache/`.
- Keep final deliverables under `outputs/`.

## Verification

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\Test-ResearchRepo.ps1
```
