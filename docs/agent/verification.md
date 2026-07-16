# Verification Runbook

This is the canonical verification matrix for the template repository. Short command snippets in `README.md` and `AGENTS.md` should point back here when the workflow changes.

## Template Changes

Run after changing reusable template docs, scripts, checks, or files under `templates/research-repo/`:

```bash
python -m one_c_autoresearch checks template
python -m one_c_autoresearch checks doctor
python -m one_c_autoresearch doctor --json --deep --strict
pytest -q
```

The template gate also scans reusable files for absolute workstation paths,
customer identifiers, fixed product labels, and a fixed research model. A
fresh generated repository must pass `checks research` before release.

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

If live 1C MCP access is needed, copy `.codex/1c-mcp.example.toml` to `.codex/1c-mcp.toml` in the generated repo and make it match `project.toml` before using it as evidence.

## What Each Check Covers

| Check | Scope |
| --- | --- |
| `scripts/checks/test_template.py` | Required template and generated-repo paths, method-doc drift checks, and template queue JSONL parseability. |
| `scripts/checks/test_doctor.py` | End-to-end doctor smoke test, bootstrap output, placeholder replacement, queue validation, and manifest policy diagnostics. |
| `scripts/checks/test_research_repo.py` | Delegates generated repository health to `python -m one_c_autoresearch doctor --mode research`. |
| `scripts/doctor.py` | Primary health gate for template or research repos: required paths, manifest sections, queue schema, dependency cycles, stale claims, expected outputs, evidence pack CSV headers, reverse-map coverage state, autopilot final-map coverage, unresolved placeholders, MCP/web policy, optional `.codex/1c-mcp.toml` consistency, and optional tool checks. |

## Autopilot Final Gate

In a generated research repo, enable the end-to-end final-map gate with:

```bash
python -m one_c_autoresearch autopilot scaffold --enable-gate
```

After `autopilot.enabled=true`, `doctor` fails until:

- `analysis/indexes/diff-inventory.csv` has no unclassified diff entries;
- `analysis/indexes/feature-map.csv` maps every non-noise feature to a complete evidence pack;
- `analysis/indexes/final-diff-inventory.csv` and `analysis/indexes/final-feature-map.csv` are generated from current reverse-map coverage;
- final-gate rows have no reverse-map blockers for claims published as complete;
- `outputs/customization-map.md` and `outputs/customization-map.xlsx` exist;
- `outputs/open-questions.csv` and `outputs/open-questions.xlsx` exist;
- `analysis/detail-maps/README.md`, `index.csv`, and `_templates/detail-map.json` define the reusable detail-map contract;
- `analysis/subject-cards/registry.csv` and `coverage.csv` classify every publishable BF container into a concrete subject card or an explicit non-card decision;
- `analysis/subject-cards/cards/<slug>/subject-card.json`, `evidence.csv`, `gaps.csv`, and `review.md` exist for ready analyst cards;
- at least one subject card is in `ready_for_review` or `reviewed` status;
- `outputs/review/index.html` and `outputs/review/data.json` can be regenerated for analyst review after `detail-map build` and subject-card validation, including `analysis/detail-maps/**/*.json` when present;
- `outputs/review/data.json` contains non-empty `subject_cards` and `subject_registry` arrays, so the first dashboard screen is not empty;
- `outputs/open-questions.csv` covers every item in `analysis/reverse-map/unresolved.csv`;
- `analysis/reverse-map/infobase-checks.csv` exists and records every attempted live check for `needs_infobase_data` blockers;
- every open question has reason, closure method, and impact;
- `analysis/final-audit.md` contains `Coverage status: complete` and `Unclassified diff entries: 0`;
- final text artifacts contain no `TODO` or `FIXME` markers.

Build and verify the publishable layer before final output generation:

```bash
python -m one_c_autoresearch final-gate build
python -m one_c_autoresearch final-gate verify
python -m one_c_autoresearch detail-map build
python -m one_c_autoresearch subject-card discover
python -m one_c_autoresearch subject-card classify
python -m one_c_autoresearch subject-card registry-build
python -m one_c_autoresearch subject-card seed --from-registry
python -m one_c_autoresearch subject-card refine --card <slug>
python -m one_c_autoresearch subject-card validate
python -m one_c_autoresearch review-dashboard build
```

## Physical Clean Comparison

When the research project needs physical cleanup of a raw vendor-vs-customer
diff, follow `docs/method/physical-clean-comparison.md` before building
`analysis/indexes/diff-inventory.csv`.

Verify the cleanup layer by checking that:

- raw vendor and customer tags or commits are recorded and unchanged;
- the nested comparison repo has no uncommitted cleanup work;
- every `analysis/clean-comparison/refinement-queue.csv` row, or the documented layer-specific queue, has a terminal status or an explicit manual-review/blocker status;
- `decisions.jsonl` records a decision and rationale for every reviewed item;
- `summary.json` records raw diff count, clean diff count, removed noise classes, preserved customization classes, clean commit, and clean diff command;
- `outputs/clean-comparison-dashboard/index.html` and `outputs/clean-comparison-dashboard/data.json`, or documented layer-specific dashboard paths, exist when analyst review is required.

The final `outputs/review/` dashboard is still built after `final-gate`; it does
not replace the intermediate clean-comparison dashboard.

## Reverse Functional Map

In a generated research repo, initialize reverse-map state with:

```bash
python -m one_c_autoresearch reverse-map scaffold
python -m one_c_autoresearch reverse-map seed
```

Continue one durable workitem with:

```bash
python -m one_c_autoresearch reverse-map claim
```

After each workitem, run:

```bash
python -m one_c_autoresearch final-gate status
python -m one_c_autoresearch doctor
```

The doctor checks that `analysis/reverse-map/coverage.csv` covers every diff row from `analysis/indexes/diff-inventory.csv`, workitems parse, statuses are valid, reverse-map CSV headers match the contract, and final-gate outputs are fresh when autopilot publication is enabled. Open work is represented in state files rather than hidden in agent context.
`analysis/reverse-map/infobase-checks.csv` is the durable ledger for live
infobase passes. `outputs/infobase-questions.csv` is the autopilot input
registry for subject-card questions that need infobase data. `doctor --strict`
fails when a `needs_infobase_data` unresolved item or an open infobase question
is neither closed by `infobase-checks.csv` nor carried into final open questions
with a closure method and impact.

## Subject Cards

Build and validate subject cards iteratively:

```bash
python -m one_c_autoresearch subject-card discover
python -m one_c_autoresearch subject-card classify
python -m one_c_autoresearch subject-card registry-build
python -m one_c_autoresearch subject-card seed --from-registry
python -m one_c_autoresearch subject-card refine --card <slug>
python -m one_c_autoresearch subject-card validate --card <slug>
```

An empty scaffolded `analysis/subject-cards/` layer is valid for a newly generated research repo only while `autopilot.enabled=false` or before the final map is claimed complete. With `autopilot.enabled=true`, `doctor --deep --strict` fails until the subject-card registry, coverage rows, concrete card bundles, and the dashboard data all expose ready analyst cards.

## Functional Gap Map

Build and validate one migration gap card at a time:

```bash
python -m one_c_autoresearch functional-gap build --card <slug>
python -m one_c_autoresearch functional-gap validate --card <slug>
```

An empty scaffolded `analysis/functional-gaps/` layer is valid for a newly generated research repo. Once functional-gap cards exist, `doctor` validates the JSON/CSV contract for the whole layer. The builder intentionally has no `--all` mode: each pass must stay scoped to one subject card.

## Expected Result

- `status = ok`: repository contract is healthy.
- `status = warn`: repository is usable, but an agent should report the warning before claiming full health.
- `status = fail`: do not continue autonomous work until the failure is fixed.

When verification rules change, update this file, `scripts/doctor.py`, and the smoke tests together.
