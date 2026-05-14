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

## Verification

Run after template changes:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\Test-Template.ps1
```

Run after creating a concrete research repo:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\Test-ResearchRepo.ps1 -RepoPath <target-repo>
```
