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
4. Use `$1c-autoresearch-queue-worker` when processing queue tasks. If the skill selector is unavailable, read `.agents/skills/1c-autoresearch-queue-worker/SKILL.md` in the generated research repo and follow it directly.
5. Use `scripts/queue/claim_next_analysis_task.py` to claim one task.
6. Write evidence under `analysis/features/<feature-id>/`.
7. Validate with `python -m one_c_autoresearch doctor` and `python -m one_c_autoresearch checks research`.
8. Update the selected task status and stop.

For end-to-end customization-map work, read `docs/method/autopilot-customization-map.md` after `project.toml`, initialize with `python -m one_c_autoresearch autopilot scaffold --enable-gate` when needed, and stop only after `python -m one_c_autoresearch doctor --deep --strict` proves complete diff coverage.

For reverse functional mapping continuation, read `docs/method/reverse-functional-map.md` and the concrete repo's `analysis/reverse-map/state.md`, then run `python -m one_c_autoresearch reverse-map claim`. Process one returned workitem, update reverse-map state, verify, and stop.

Do not treat this template as an evidence source for any customer project.
