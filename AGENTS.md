# Repo Instructions

This repository maintains the canonical runtime and generator for reproducible
1C autoresearch repositories.

## Scope

- Keep customer sources, credentials, generated indexes, and deliverables out.
- Treat `templates/research-repo/` as the canonical generated-repository
  surface and keep the installable runtime in two-way parity with it.
- Do not restore legacy queue, `CUS`, subject-card, reverse-map,
  functional-gap, manual-cleanup, old dashboard, or compatibility-reader
  authorities. `0.2.0` is the last compatible legacy release.
- Never import, convert, overwrite, or delete customer evidence during
  generation, synchronization, verification, upgrade, or rollback.

## Language

- Keep reusable template docs and scripts in English.
- Project instances may contain Russian business terms and 1C object names.

## Owned Maintenance Surface

Only these repository-local maintenance scripts are supported:

- `scripts/sync_generated_runtime.py`
- `scripts/build_workspace_template.py`
- `scripts/bootstrap/new_research_repo.py`
- `scripts/checks/test_template.py`
- `scripts/checks/test_doctor.py`
- `scripts/checks/test_research_repo.py`

They must stay outside the runtime distribution and generated repositories.
Synchronization is preview-first and may apply only an unchanged,
fingerprinted plan. Bootstrap must reject non-empty destinations.

The active root documentation surface is exactly `README.md`, `AGENTS.md`,
`docs/agent/repo-map.md`, `docs/agent/verification.md`, and
`docs/operator/dispatcher-inspector-rollback.md` and
`docs/operator/source-search.md`. OpenSpec archives are non-executable history.

## Navigation And Verification

- Read `docs/agent/repo-map.md` for ownership and change routing.
- Read `docs/agent/verification.md` for the release matrix.

```bash
python scripts/checks/test_template.py
python scripts/checks/test_doctor.py
python scripts/checks/test_research_repo.py --repo-path <target-repo>
uv run --extra workspace basedpyright
pytest -q
```

`dist/research-template.zip` is a separate release artifact, never runtime
package data.

## Goal Cursor

- Для длинной работы с OpenSpec в режиме Goal использовать в `tasks.md` не более одного `<!-- GOAL_CURSOR -->` непосредственно перед активной задачей. Чекбокс сохранять в штатном виде `- [ ]` или `- [x]`; курсор сам по себе не означает завершение.
- Под активной задачей держать четыре вложенные строки: `Этап цикла` — `архитектурное ревью`, `реализация`, `ревью`, `исправление` или `финальная проверка`; `Состояние шага`; `Следующее действие`; `Файлы шага` — только пути незавершённого шага.
- После компактизации сначала прочитать `tasks.md` и сверить контракт с `git status --short`, `git diff`, `git diff --cached`, указанными файлами и доступными проверками. Пути служат навигацией, а не доказательством.
- Обновлять контракт перед длительной операцией и после изменения этапа, состояния или следующего действия. При переносе курсора удалить прежние четыре строки; подробные журналы и результаты запусков в `tasks.md` не дублировать.
