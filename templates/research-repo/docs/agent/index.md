# Agent Router

1. Read `project.toml`.
2. Read `analysis/queue/README.md`.
3. Select one task with `scripts/queue/Get-NextAnalysisTask.ps1`.
4. Process only the selected task.
5. Write evidence under `analysis/features/<feature-id>/`.
6. Update the selected task status.
7. Run `scripts/checks/Test-ResearchRepo.ps1` before claiming repository health.

Do not use live 1C MCP or web evidence unless the task explicitly allows it and `project.toml` identifies the target.
