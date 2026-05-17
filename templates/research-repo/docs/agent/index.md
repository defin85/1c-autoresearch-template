# Agent Router

Read `docs/agent/repo-map.md` before choosing files. Read `docs/agent/verification.md` before claiming repository health.

1. Read `project.toml`.
2. Read `analysis/queue/README.md`.
3. Use `$1c-autoresearch-queue-worker` when processing a queue task. If the skill selector is unavailable, read `.agents/skills/1c-autoresearch-queue-worker/SKILL.md` and follow it directly.
4. Claim one task with `scripts/queue/claim_next_analysis_task.py`.
5. Process only the selected task.
6. Write evidence under `analysis/features/<feature-id>/`.
7. Run `python -m one_c_autoresearch doctor` and `python -m one_c_autoresearch checks research`.
8. Update the selected task status and stop.

For end-to-end customization-map work, read `docs/method/autopilot-customization-map.md`, initialize with `python -m one_c_autoresearch autopilot scaffold --enable-gate` when needed, and stop only after `python -m one_c_autoresearch doctor --deep --strict` proves complete diff coverage.

Do not use live 1C MCP or web evidence unless the task explicitly allows it and `project.toml` identifies the target.
