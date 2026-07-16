# Agent Router

Read `docs/agent/repo-map.md` first when you need to locate ownership or decide which files to inspect. Read `docs/agent/verification.md` before claiming repository health.

For this concrete research repo:

1. Read `project.toml`.
2. Read `docs/agent/repo-map.md` and `docs/agent/verification.md`.
3. Read `analysis/queue/README.md`.
4. Use `$1c-autoresearch-queue-worker` when processing queue tasks. If the skill selector is unavailable, read `.agents/skills/1c-autoresearch-queue-worker/SKILL.md` in the generated research repo and follow it directly.
5. Use `scripts/queue/claim_next_analysis_task.py` to claim one task.
6. Write evidence under `analysis/features/<feature-id>/`.
7. Validate with `python -m one_c_autoresearch doctor` and `python -m one_c_autoresearch checks research`.
8. Update the selected task status and stop.

For end-to-end customization-map work, read `docs/method/autopilot-customization-map.md` and `docs/method/physical-clean-comparison.md` after `project.toml`, initialize with `python -m one_c_autoresearch autopilot scaffold --enable-gate` when needed, build or audit the clean diff before `analysis/indexes/diff-inventory.csv`, run `python -m one_c_autoresearch final-gate build` before final output generation, build and validate subject cards before `review-dashboard build`, and stop only after `python -m one_c_autoresearch doctor --deep --strict` proves complete reverse-map and subject-card coverage.

For reverse functional mapping continuation, read `docs/method/reverse-functional-map.md` and the concrete repo's `analysis/reverse-map/state.md`, then run `python -m one_c_autoresearch reverse-map claim`. Process one returned workitem, update reverse-map state, run `python -m one_c_autoresearch final-gate status`, verify, and stop.

For subject-card work, run one refinement pass at a time: `subject-card discover`, `classify`, `registry-build`, `seed --from-registry`, then `subject-card refine --card <slug>` and `subject-card validate --card <slug>`.

For migration gap mapping, work one subject card per pass: run `python -m one_c_autoresearch functional-gap build --card <slug>`, review `analysis/functional-gaps/cards/<slug>/review.md`, then run `python -m one_c_autoresearch functional-gap validate --card <slug>`.

Do not treat agent memory or primary classifications as final evidence when current files disagree.

## Goal Entry Points

- `/goal Исследование`: `docs/method/research-goal-router.md`
- `/goal Параллельное исследование`: `docs/method/parallel-research-goal.md`
- `/goal Подготовь ревью`: `docs/method/research-review-preparation.md`
- `/goal Ручная разметка`: `docs/method/manual-markup-goal.md`
- `/goal Карта разрывов`: `docs/method/functional-gap-goal.md`
