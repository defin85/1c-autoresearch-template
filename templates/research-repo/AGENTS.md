# Repository Instructions

This repository uses one repository-owned research workflow.

## Source Of Truth

- `project.toml` contains project identity, version targets, and evidence access policy.
- `research/workflow.toml` contains the fixed seven-gate, eight-job, nine-operation contract.
- `research/infobases.toml`, `research/external-artifacts.toml`, and `research/indexing.toml` contain non-secret setup declarations.
- `research/active-source-generation.json`, `research/active-diff-generation.json`, and `research/active-generation.json` are the only active-state pointers.
- Immutable source payloads live under `sources/generations/`.
- Immutable physical differences live under `analysis/indexes/generations/`.
- Immutable MRQ graphs live under `analysis/migration-requirements/generations/` and are bound by `research/generations/` manifests.
- `outputs/projections.json` is derived and never owns decisions.

## Rules

- Derive the next work unit with `python -m one_c_autoresearch next`; do not create a task queue.
- Use only `python -m one_c_autoresearch apply <typed-operation>` or the equivalent HTTP action.
- Every customer `DIF-*` has exactly one primary `MRQ-*` owner or approved-noise disposition.
- Store evidence as repository path plus content fingerprint; an index result alone is not evidence.
- Source indexes, events, logs, bookmarks, connection profiles, upload drafts, and preferences are disposable user state.
- Never store credentials in tracked files.
- Do not restore removed legacy commands, readers, paths, or compatibility adapters.
- Use one component-level `rlm-tools-bsl` index per configuration, extension UUID, or external `EXT-*`; never index a mixed role parent.

## Verification

```bash
python -m one_c_autoresearch doctor
python -m one_c_autoresearch doctor --strict
uv run --extra workspace basedpyright
uv run --extra workspace --with pytest --with httpx python -m pytest -q tests
cd web/workspace && npm test -- --run && npm run build
```

## UI Runbook

UI-задача не считается завершённой после исправления отдельного снимка, компонента или автоматической проверки. Перед сдачей нужно пройти пользовательский сценарий целиком в собранном приложении.

1. Найти общий компонент и общий маршрут состояния. Не исправлять одинаковый дефект отдельно для разных узлов, экранов или состояний.
2. Проверить минимум пустое, рабочее, заполненное и ошибочное состояния. Для динамических элементов проверить появление, обновление и удаление данных.
3. Пройти все затронутые переходы между экранами в обе стороны, включая прямой возврат. Состав, порядок и геометрия постоянной навигации не должны меняться при переключении экрана.
4. Проверить на поддерживаемых размерах окна:
   - рамки, переполнение и обрезку содержимого;
   - коннекторы и линии графа;
   - панели, прокрутку и сохранение положения холста;
   - управление клавиатурой, видимый фокус и возврат фокуса инициатору;
   - отсутствие ошибок в консоли и необработанных ошибок страницы.
5. После сборки запустить приложение на постоянном каталоге состояния, перезапустить сервер и убедиться, что закладки репозиториев и другие ожидаемо сохраняемые пользовательские данные доступны.
6. Добавить одну минимальную проверку, воспроизводящую найденный дефект, и выполнить относящиеся к изменению модульные и браузерные проверки.
7. Не изменять утверждённые визуальные эталоны без прямого указания пользователя. Расхождение с эталоном не исправлять скрытой подменой рабочего интерфейса; сначала проверить фактический пользовательский сценарий и сообщить о конфликте.

В отчёте о готовности назвать пройденные состояния, переходы, размеры окна, результат перезапуска и сохранность данных. Если какой-либо пункт не проверен, UI-задача остаётся незавершённой.
