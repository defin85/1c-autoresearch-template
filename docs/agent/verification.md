# Verification Runbook

This is the canonical verification matrix for the template repository. Short command snippets in `README.md` and `AGENTS.md` should point back here when the workflow changes.

## Template Changes

Run after changing reusable template docs, scripts, checks, or files under `templates/research-repo/`:

```bash
python -m one_c_autoresearch checks template
python -m one_c_autoresearch checks doctor
python -m one_c_autoresearch doctor --json --deep --strict
```

The same gate runs in GitHub Actions via `.github/workflows/verify.yml`.

For machine-readable automation output:

```bash
python -m one_c_autoresearch doctor --json
```

Use `--deep` when source paths, tooling, or local environment assumptions matter:

```bash
python -m one_c_autoresearch doctor --json --deep
```

Use `--strict` in CI or pre-merge automation when warnings should block the change:

```bash
python -m one_c_autoresearch doctor --deep --strict
```

## Generated Research Repo

After creating a concrete research repo, validate from the template repo:

```bash
python -m one_c_autoresearch checks research --repo-path <target-repo>
```

Then validate from the generated repo:

```bash
python -m one_c_autoresearch doctor --repo-path <target-repo>
```

For deeper source-path and tool checks:

```bash
python -m one_c_autoresearch doctor --repo-path <target-repo> --deep
```

## What Each Check Covers

| Check | Scope |
| --- | --- |
| `scripts/checks/test_template.py` | Required template and generated-repo paths, method-doc drift checks, and template queue JSONL parseability. |
| `scripts/checks/test_doctor.py` | End-to-end doctor smoke test, bootstrap output, placeholder replacement, queue validation, and manifest policy diagnostics. |
| `scripts/checks/test_research_repo.py` | Delegates generated repository health to `python -m one_c_autoresearch doctor --mode research`. |
| `scripts/doctor.py` | Primary health gate for template or research repos: required paths, manifest sections, queue schema, dependency cycles, stale claims, expected outputs, evidence pack CSV headers, unresolved placeholders, MCP/web policy, optional `.codex/1c-mcp.toml` consistency, and optional tool checks. |

## Expected Result

- `status = ok`: repository contract is healthy.
- `status = warn`: repository is usable, but an agent should report the warning before claiming full health.
- `status = fail`: do not continue autonomous work until the failure is fixed.

When verification rules change, update this file, `scripts/doctor.py`, and the smoke tests together.
