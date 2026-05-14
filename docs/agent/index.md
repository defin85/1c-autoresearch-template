# Agent Router

Read `docs/agent/repo-map.md` first when you need to locate ownership or decide which files to inspect. Read `docs/agent/verification.md` before claiming repository health.

For template work:

1. Read `README.md`.
2. Read `AGENTS.md`.
3. Read `docs/agent/repo-map.md`.
4. Read `docs/method/1c-autoresearch-process.md`.
5. Read bootstrap and validation scripts only when changing them.

For concrete research repos created from this template:

1. Read `project.toml`.
2. Read `docs/agent/repo-map.md` and `docs/agent/verification.md`.
3. Read `analysis/queue/README.md`.
4. Use `$1c-autoresearch-queue-worker` when processing queue tasks.
5. Use `scripts/queue/Get-NextAnalysisTask.ps1` to select one task.
6. Write evidence under `analysis/features/<feature-id>/`.
7. Validate with `scripts/doctor.ps1` and `scripts/checks/Test-ResearchRepo.ps1`.

Do not treat this template as an evidence source for any customer project.
