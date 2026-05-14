# Agent Router

Read `docs/agent/repo-map.md` before choosing files. Read `docs/agent/verification.md` before claiming repository health.

1. Read `project.toml`.
2. Read `analysis/queue/README.md`.
3. Use `$1c-autoresearch-queue-worker` when processing a queue task.
4. Select one task with `scripts/queue/Get-NextAnalysisTask.ps1`.
5. Process only the selected task.
6. Write evidence under `analysis/features/<feature-id>/`.
7. Update the selected task status.
8. Run `scripts/doctor.ps1` and `scripts/checks/Test-ResearchRepo.ps1` before claiming repository health.

Do not use live 1C MCP or web evidence unless the task explicitly allows it and `project.toml` identifies the target.
