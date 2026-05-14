# __PROJECT_ID__ Research

Concrete 1C autoresearch repository.

## Sources

Source paths are declared in `project.toml`.

## First Commands

Show the next queue task:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\queue\Get-NextAnalysisTask.ps1
```

Validate the repository:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\Test-ResearchRepo.ps1
```

## Layout

```text
analysis/cache/      generated indexes and noisy machine data
analysis/queue/      file-backed work queue
analysis/features/   feature evidence packs
analysis/runs/       run logs
outputs/             human-facing deliverables
docs/                method and agent guidance
scripts/             queue and validation helpers
```
