# Customization Registry Bootstrap

This runbook initializes `analysis/customization-registry/` in a repository.

## Required Inputs

- `analysis/indexes/final-diff-inventory.csv`
- `analysis/custom-metadata/index.jsonl`

## Optional Inputs

- `analysis/external-processing/**`
- `analysis/subject-cards/**`
- `analysis/functional-gaps/**`
- Designer comparison report path passed as `--designer-report`
- clean `v8unpack` tree passed as `--v8unpack-root`

## Command Order

```bash
python3 -m one_c_autoresearch customization-registry bootstrap --repo-path .
python3 -m one_c_autoresearch customization-registry validate --repo-path .
python3 -m one_c_autoresearch customization-registry export --repo-path .
```

For debugging individual phases:

```bash
python3 -m one_c_autoresearch customization-registry context-build --repo-path .
python3 -m one_c_autoresearch customization-registry build --repo-path .
```

## Generated Artifacts

- `analysis/customization-registry/customization-items.jsonl`
- `analysis/customization-registry/customization-evidence.jsonl`
- `analysis/customization-registry/customization-links.jsonl`
- `analysis/customization-registry/customization-lineage.jsonl`
- `analysis/customization-registry/diff-context.jsonl`
- `analysis/customization-registry/designer-report-index.jsonl`
- `analysis/customization-registry/uuid-resolution.jsonl`
- `analysis/customization-registry/build-metadata.json`
- `analysis/customization-registry/summary.md`
- `analysis/customization-registry/customization-items.csv`

## Validation Gates

```bash
python3 -m one_c_autoresearch customization-registry validate --repo-path .
python3 -m one_c_autoresearch external-processing validate --repo-path .
python3 -m one_c_autoresearch custom-metadata validate --strict-reconciliation
python3 -m one_c_autoresearch functional-gap validate --repo-path .
python3 -m one_c_autoresearch checks research
python3 -m one_c_autoresearch doctor
```

## Stop Rules

- Include every discovered customization in the migration scope by default.
  Exclude it only after customer agreement and record the reason. Do not use
  `needs_followup`, unknown target-release coverage, missing runtime confirmation, or
  absence from the original customer list as an exclusion reason.
- Do not rebuild clean diff repositories, parser snapshots,
  `analysis/custom-metadata/`, subject cards, functional-gap cards, customer
  outputs, or manual-markup decisions from bootstrap.
- Missing external EPF/ERF source is not a blocker for registry creation; keep
  the related `CUS-*` included with status `needs_source`.
- Do not approve external processing based only on workbook, screenshot, name,
  or missing-source rows.
