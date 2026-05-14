# 1C Autoresearch Template

Clean process template for evidence-based analysis of 1C customizations, functional gaps, and migration readiness.

Use it when you need to compare a vendor baseline, a customer-modified 1C configuration, optional extensions, and a newer vendor release without losing context across agent runs.

## What This Template Provides

- A project manifest contract: `project.toml`.
- A file-backed analysis queue for loopback/autonomous Codex runs.
- Feature evidence packs for deep dives.
- Quality gates for static 1C source analysis.
- Bootstrap and validation scripts for new research repositories.
- A small method layer for standard-vs-custom-vs-next-release gap analysis.

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

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap\New-1cResearchRepo.ps1 `
  -TargetPath E:\Projects\do_21_research `
  -ProjectId do21-traitek `
  -Product "1C Document Management" `
  -BaselineVersion "2.1" `
  -TargetVersion "2.1" `
  -NextVendorVersion "3.0" `
  -VendorBaseline "E:\Projects\do_21_demo" `
  -TargetCf "E:\Projects\do_21_traitek\cf" `
  -TargetCfe "E:\Projects\do_21_traitek\cfe" `
  -NextVendor "E:\Projects\do_30_demo" `
  -RlmVendorBaseline do_21_demo `
  -RlmTargetCf do_21_traitek_cf `
  -RlmTargetCfe do_21_traitek_cfe `
  -RlmNextVendor do_30_demo `
  -InitGit
```

Validate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\Test-ResearchRepo.ps1 -RepoPath E:\Projects\do_21_research
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
  features/
  runs/
outputs/
scripts/
  queue/
  checks/
```

## Agent Loop

1. Read `project.toml`.
2. Read `analysis/queue/README.md`, `task-schema.md`, `review-checklist.md`, and `tasks.jsonl`.
3. Select one pending task using `scripts/queue/Get-NextAnalysisTask.ps1`.
4. Claim exactly one task.
5. Use static indexes and 1C source trees first.
6. Write evidence under `analysis/features/<feature-id>/`.
7. Update the task status and stop.

The queue is deliberately file-backed. It is slower than a broker but transparent, diffable, and easy for Codex automation to resume.
