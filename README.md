# 1C Autoresearch Template

Clean process template for evidence-based analysis of 1C customizations, functional gaps, and migration readiness.

Use it when you need to compare a vendor baseline, a customer-modified 1C configuration, optional extensions, and a newer vendor release without losing context across agent runs.

## What This Template Provides

- A project manifest contract: `project.toml`.
- A file-backed analysis queue for loopback/autonomous Codex runs.
- Feature evidence packs for deep dives.
- An autopilot customization-map contract with strict completion gates.
- A reverse functional mapping state machine for long-running fact-to-intent research.
- A final-gate normalization layer that applies reverse-map decisions before final outputs are published.
- A static analyst review dashboard generated from final research artifacts.
- Quality gates for static 1C source analysis.
- Bootstrap and validation scripts for new research repositories.
- A small method layer for standard-vs-custom-vs-next-release gap analysis.

## Agent Docs

- `docs/agent/repo-map.md`: entry points, change routing, and system-of-record map.
- `docs/agent/verification.md`: canonical verification matrix.
- `docs/agent/index.md`: short router for template work and concrete research repos.

## What This Template Does Not Contain

- Real customer configuration dumps.
- Generated comparison indexes.
- Customer Excel/Markdown deliverables.
- Live infobase credentials.

Keep those in concrete research repositories created from this template.

## Recommended Layout

```text
E:\Projects\1c-autoresearch-template  # reusable template
E:\Projects\<project>_research         # concrete research repo
E:\Projects\<vendor_baseline>          # vendor source dump
E:\Projects\<customer_cf>              # customer source dump
E:\Projects\<customer_cfe>             # extension source dump
```

## Create a Research Repo

```bash
python -m one_c_autoresearch new-repo \
  --target-path ./do_21_research \
  --project-id do21-traitek \
  --product "1C Document Management" \
  --baseline-version "2.1" \
  --target-version "2.1" \
  --next-vendor-version "3.0" \
  --vendor-baseline ./do_21_demo \
  --target-cf ./do_21_traitek/cf \
  --target-cfe ./do_21_traitek/cfe \
  --next-vendor ./do_30_demo \
  --rlm-vendor-baseline do_21_demo \
  --rlm-target-cf do_21_traitek_cf \
  --rlm-target-cfe do_21_traitek_cfe \
  --rlm-next-vendor do_30_demo \
  --init-git
```

Validate:

```bash
python -m one_c_autoresearch checks research --repo-path ./do_21_research
python -m one_c_autoresearch doctor --repo-path ./do_21_research
```

## Research Repo Contract

Concrete repositories use this structure:

```text
project.toml
AGENTS.md
README.md
docs/
  agent/
  method/
analysis/
  cache/
    indexes/
    noise/
  queue/
  reverse-map/
  features/
  runs/
outputs/
scripts/
  queue/
  checks/
```

## Agent Loop

1. Read `project.toml`.
2. For queue work, read `analysis/queue/README.md`, `task-schema.md`, `review-checklist.md`, and `tasks.jsonl`.
3. For reverse functional mapping continuation, read `analysis/reverse-map/state.md` and use `python -m one_c_autoresearch reverse-map claim` in the concrete research repo.
4. For end-to-end customization maps, read `docs/method/autopilot-customization-map.md` and use `python -m one_c_autoresearch autopilot scaffold --enable-gate` in the concrete research repo.
5. Build the clean diff, classify every diff entry, group entries into features, and complete reverse-map review.
6. Run `python -m one_c_autoresearch final-gate build` before generating final outputs.
7. Generate final outputs only from `analysis/indexes/final-diff-inventory.csv` and `analysis/indexes/final-feature-map.csv`, then write `analysis/final-audit.md`.
8. Build the static analyst dashboard with `python -m one_c_autoresearch review-dashboard build`.
9. Run the generated repo doctor. With `autopilot.enabled=true`, it must prove every diff entry is classified and every final claim is consistent with reverse-map decisions before final delivery.
10. Update queue or reverse-map workitem status only after verification passes.

The queue is deliberately file-backed. It is slower than a broker but transparent, diffable, and easy for Codex automation to resume.

## Doctor

See `docs/agent/verification.md` for the canonical verification matrix.

Run from this template repo:

```bash
python -m one_c_autoresearch doctor --json --deep --strict
```

Run from a concrete research repo:

```bash
python -m one_c_autoresearch doctor
```

Check another repo explicitly:

```bash
python -m one_c_autoresearch doctor --repo-path ./do_21_research
```

Scaffold the final customization-map contract in a concrete research repo:

```bash
python -m one_c_autoresearch autopilot scaffold --repo-path ./do_21_research --enable-gate
```

Continue reverse functional mapping in a concrete research repo:

```bash
python -m one_c_autoresearch reverse-map claim --repo-path ./do_21_research
python -m one_c_autoresearch final-gate status --repo-path ./do_21_research
```

Generate the static analyst dashboard in a concrete research repo:

```bash
python -m one_c_autoresearch review-dashboard build --repo-path ./do_21_research
```

Useful flags:

- `--json`: machine-readable output for automation.
- `--deep`: also checks source path existence and basic tools.
- `--strict`: returns exit code `2` when warnings exist.
