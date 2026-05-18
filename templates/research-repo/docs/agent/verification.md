# Verification Runbook

This is the canonical verification matrix for a concrete 1C autoresearch repository.

## Standard Health Check

Run from the repository root:

```bash
python -m one_c_autoresearch doctor
python -m one_c_autoresearch checks research
```

Use the doctor JSON output for automation:

```bash
python -m one_c_autoresearch doctor --json
```

Use `--deep` when source paths, local tools, or environment assumptions matter:

```bash
python -m one_c_autoresearch doctor --deep
```

Use `--strict` when warnings should block automated continuation:

```bash
python -m one_c_autoresearch doctor --deep --strict
```

## MCP Manifest Promotion

When live 1C MCP or web evidence is enabled, copy `.codex/1c-mcp.example.toml`
to `.codex/1c-mcp.toml`, fill in the active MCP server, URL, service root,
RLM project, web URL, username, and credential file, then run:

```bash
python -m one_c_autoresearch doctor --json --deep
```

Do not use live 1C evidence until the local manifest matches `project.toml`.

## Before Claiming A Queue Task Complete

1. Confirm the claimed task by reading `analysis/queue/tasks.jsonl`.
2. Confirm expected evidence exists under `analysis/features/<feature-id>/`.
3. Run `python -m one_c_autoresearch doctor`.
4. Run `python -m one_c_autoresearch checks research`.
5. Record unresolved runtime dependencies in the feature pack and queue status before stopping.

## Autopilot Final Gate

For an end-to-end customization map, initialize and enable the final gate:

```bash
python -m one_c_autoresearch autopilot scaffold --enable-gate
```

After `autopilot.enabled=true`, `doctor` fails until:

- `analysis/indexes/diff-inventory.csv` has no unclassified diff entries;
- `analysis/indexes/feature-map.csv` maps every non-noise feature to a complete evidence pack;
- final Markdown and XLSX outputs exist;
- open questions have reason, closure method, and impact;
- `analysis/final-audit.md` contains `Coverage status: complete` and `Unclassified diff entries: 0`;
- final text artifacts contain no `TODO` or `FIXME` markers.

## Reverse-Map Continuation Gate

For long-running reverse functional mapping, initialize state when needed:

```bash
python -m one_c_autoresearch reverse-map scaffold
python -m one_c_autoresearch reverse-map seed
```

Continue one workitem:

```bash
python -m one_c_autoresearch reverse-map claim
```

The standard doctor validates that `analysis/reverse-map/coverage.csv` covers every diff row from `analysis/indexes/diff-inventory.csv`, workitems parse, statuses are valid, and reverse-map CSV headers match the contract. Open work is allowed; it is represented by `assigned`, `needs_manual_review`, or `needs_infobase_data` instead of disappearing from coverage.

## Health Gate Meaning

- `status = ok`: repository contract is healthy.
- `status = warn`: repository is usable, but warnings must be reported before claiming health.
- `status = fail`: stop autonomous work and fix the contract issue first.

The doctor validates required paths, `project.toml`, queue schema, task dependencies, stale claims, expected outputs, evidence pack CSV headers, autopilot final-map coverage, unresolved placeholders, MCP/web policy, and optional tools when `--deep` is used.
When `.codex/1c-mcp.toml` exists, the doctor also compares its MCP server, URL, service root, active RLM project, and web URL with `project.toml`.
