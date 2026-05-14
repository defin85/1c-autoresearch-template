# Verification Runbook

This is the canonical verification matrix for the template repository. Short command snippets in `README.md` and `AGENTS.md` should point back here when the workflow changes.

## Template Changes

Run after changing reusable template docs, scripts, checks, or files under `templates/research-repo/`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\Test-Template.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\Test-Doctor.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\doctor.ps1
```

For machine-readable automation output:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\doctor.ps1 -Json
```

Use `-Deep` when source paths, tooling, or local environment assumptions matter:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\doctor.ps1 -Json -Deep
```

Use `-Strict` in CI or pre-merge automation when warnings should block the change:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\doctor.ps1 -Deep -Strict
```

## Generated Research Repo

After creating a concrete research repo, validate from the template repo:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\Test-ResearchRepo.ps1 -RepoPath <target-repo>
```

Then validate from the generated repo:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File <target-repo>\scripts\doctor.ps1
```

For deeper source-path and tool checks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File <target-repo>\scripts\doctor.ps1 -Deep
```

## What Each Check Covers

| Check | Scope |
| --- | --- |
| `scripts/checks/Test-Template.ps1` | Required template paths and template queue JSONL parseability. |
| `scripts/checks/Test-Doctor.ps1` | End-to-end doctor smoke test, bootstrap output, placeholder replacement, queue validation, and manifest policy diagnostics. |
| `scripts/checks/Test-ResearchRepo.ps1` | Delegates generated repository health to `scripts/doctor.ps1 -Mode research`. |
| `scripts/doctor.ps1` | Primary health gate for template or research repos: required paths, manifest sections, queue schema, dependency cycles, stale claims, expected outputs, unresolved placeholders, MCP/web policy, and optional tool checks. |

## Expected Result

- `status = ok`: repository contract is healthy.
- `status = warn`: repository is usable, but an agent should report the warning before claiming full health.
- `status = fail`: do not continue autonomous work until the failure is fixed.

When verification rules change, update this file, `scripts/doctor.ps1`, and the smoke tests together.
