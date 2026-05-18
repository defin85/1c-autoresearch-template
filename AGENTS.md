# Repo Instructions

This repository is a clean template for reproducible 1C autoresearch projects.

## Scope

- Keep this repo free of customer-specific source dumps, generated indexes, XLSX reports, and one-off analysis artifacts.
- Put reusable process assets here: methodology, templates, queue scripts, validation scripts, and example scaffolds.
- Create customer/project-specific research repositories from `templates/research-repo/`.

## Language

- Keep reusable template docs and scripts in English.
- Project instances may contain Russian business terms, 1C object names, and customer-facing outputs.

## Editing Rules

- Do not copy real `cf`, `cfe`, infobase data, or customer deliverables into this template.
- Prefer additive changes to the template contract.
- Keep queue schemas backward-compatible: add optional fields instead of changing existing meanings.
- Scripts must be safe by default and fail before overwriting non-empty target directories.

## Agent Navigation

- Use `docs/agent/repo-map.md` as the map of entry points, ownership, and change routing.
- Use `docs/agent/verification.md` as the canonical verification matrix.
- Use `docs/method/1c-autoresearch-process.md`, `docs/method/queue-design.md`, and `docs/method/reverse-functional-map.md` as the reusable analysis methodology.
- Keep short command snippets in this file aligned with `docs/agent/verification.md`.

## Verification

Run after template changes:

```bash
python -m one_c_autoresearch checks template
python -m one_c_autoresearch checks doctor
python -m one_c_autoresearch doctor --json --deep --strict
```

Run after creating a concrete research repo:

```bash
python -m one_c_autoresearch checks research --repo-path <target-repo>
python -m one_c_autoresearch doctor --repo-path <target-repo>
```
