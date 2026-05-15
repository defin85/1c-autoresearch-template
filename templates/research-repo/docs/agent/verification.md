# Verification Runbook

This is the canonical verification matrix for a concrete 1C autoresearch repository.

## Standard Health Check

Run from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\doctor.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\Test-ResearchRepo.ps1
```

Use the doctor JSON output for automation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\doctor.ps1 -Json
```

Use `-Deep` when source paths, local tools, or environment assumptions matter:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\doctor.ps1 -Deep
```

Use `-Strict` when warnings should block automated continuation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\doctor.ps1 -Deep -Strict
```

## MCP Manifest Promotion

When live 1C MCP or web evidence is enabled, copy `.codex/1c-mcp.example.toml`
to `.codex/1c-mcp.toml`, fill in the active MCP server, URL, service root,
RLM project, web URL, username, and credential file, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\doctor.ps1 -Json -Deep
```

Do not use live 1C evidence until the local manifest matches `project.toml`.

## Before Claiming A Queue Task Complete

1. Confirm the claimed task by reading `analysis/queue/tasks.jsonl`.
2. Confirm expected evidence exists under `analysis/features/<feature-id>/`.
3. Run `scripts\doctor.ps1`.
4. Run `scripts\checks\Test-ResearchRepo.ps1`.
5. Record unresolved runtime dependencies in the feature pack and queue status before stopping.

## Health Gate Meaning

- `status = ok`: repository contract is healthy.
- `status = warn`: repository is usable, but warnings must be reported before claiming health.
- `status = fail`: stop autonomous work and fix the contract issue first.

The doctor validates required paths, `project.toml`, queue schema, task dependencies, stale claims, expected outputs, evidence pack CSV headers, unresolved placeholders, MCP/web policy, and optional tools when `-Deep` is used.
When `.codex/1c-mcp.toml` exists, the doctor also compares its MCP server, URL, service root, active RLM project, and web URL with `project.toml`.
