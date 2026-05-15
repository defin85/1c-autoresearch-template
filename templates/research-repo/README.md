# __PROJECT_ID__ Research

Concrete 1C autoresearch repository.

## Sources

Source paths are declared in `project.toml`.

## First Commands

Show the next queue task without claiming it:

```bash
python -m one_c_autoresearch queue get
```

Claim one task when starting work:

```bash
python -m one_c_autoresearch queue claim
```

Validate the repository:

```bash
python -m one_c_autoresearch doctor
python -m one_c_autoresearch checks research
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
.agents/skills/      repo-local Codex workflows
```

Agent navigation starts in `docs/agent/repo-map.md`. Verification details live in `docs/agent/verification.md`.
