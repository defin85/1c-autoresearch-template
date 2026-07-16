# 1C Autoresearch Template

Clean process template for evidence-based analysis of 1C customizations, functional gaps, and migration readiness.

Optional reusable contours store canonical state under
`analysis/customization-registry/`, `analysis/migration-requirements/`, and
`analysis/parallel-research/`. Generated repositories start with empty
scaffolds; customer records and run artifacts are never shipped by the template.

Key optional commands are `configuration-source`, `custom-metadata`,
`customization-registry`, `migration-requirement`, `parallel-research`, and
`manual-cleanup`. Their paths, source roles, model, worker count, timeout, and
Git refs are configured in the generated repository's `project.toml`.

Use it when you need to compare a vendor baseline, a customer-modified 1C configuration, optional extensions, and a newer vendor release without losing context across agent runs.

## What This Template Provides

- A project manifest contract: `project.toml`.
- A file-backed analysis queue for loopback/autonomous Codex runs.
- Feature evidence packs for deep dives.
- An autopilot customization-map contract with strict completion gates.
- A physical clean-comparison contract for deterministic vendor-vs-customer diffs before business analysis.
- A reverse functional mapping state machine for long-running fact-to-intent research.
- A final-gate normalization layer that applies reverse-map decisions before final outputs are published.
- An iterative subject-card layer for analyst-owned customization cards below coarse `BF-*` containers.
- A one-card-per-pass functional-gap layer for migration hypotheses and target-release checks.
- A static analyst review dashboard generated from final research artifacts.
- An intermediate clean-comparison dashboard contract for analyst review of noise removal decisions.
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
  detail-maps/
  clean-comparison/
  subject-cards/
  functional-gaps/
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
5. Follow `docs/method/physical-clean-comparison.md` to build the clean diff, including the cleanup queue, decision ledger, summary, and intermediate dashboard when analyst review is needed.
6. Classify every clean diff entry, group entries into features, and complete reverse-map review.
7. Run `python -m one_c_autoresearch final-gate build` before generating final outputs.
8. Generate final outputs only from `analysis/indexes/final-diff-inventory.csv` and `analysis/indexes/final-feature-map.csv`, then write `analysis/final-audit.md`.
9. Run `python -m one_c_autoresearch detail-map build` to create generated subject maps, then enrich selected maps under `analysis/detail-maps/` when analyst-level drill-down below `BF-*` is needed.
10. Run `python -m one_c_autoresearch subject-card discover`, `classify`, `registry-build`, `seed --from-registry`, and `refine --card <slug>` to build analyst-owned subject cards.
11. Run `python -m one_c_autoresearch functional-gap build --card <slug>` to start a migration gap pass for one subject card.
12. Build the static analyst dashboard with `python -m one_c_autoresearch review-dashboard build`.
13. Run the generated repo doctor. With `autopilot.enabled=true`, it must prove every diff entry is classified and every final claim is consistent with reverse-map decisions before final delivery.
14. Update queue or reverse-map workitem status only after verification passes.

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

Build one migration gap card in a concrete research repo:

```bash
python -m one_c_autoresearch functional-gap build --repo-path ./do_21_research --card <slug>
python -m one_c_autoresearch functional-gap validate --repo-path ./do_21_research --card <slug>
```

Useful flags:

- `--json`: machine-readable output for automation.
- `--deep`: also checks source path existence and basic tools.
- `--strict`: returns exit code `2` when warnings exist.
