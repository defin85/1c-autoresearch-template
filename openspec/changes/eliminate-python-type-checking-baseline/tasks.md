## 1. Establish the migration gate

- [x] 1.1 Recalculate unsuppressed diagnostics in the verified target and record aggregate counts by module and rule without storing source snippets.
- [x] 1.2 Add the pinned BasedPyright dependency and production-source configuration to canonical dependency metadata, scaffold verification and supported CI.
- [x] 1.3 Add temporary-baseline guards that forbid rule downgrades, ignored diagnostics, per-file overrides and exclusion of owned production modules.

## 2. Type external and persistence boundaries

- [x] 2.1 Define minimal owned types for shared identifiers, JSON/TOML values and serialized workflow records, reusing existing dataclasses and enums where available.
- [x] 2.2 Validate and narrow repository-file, JSON and TOML inputs at their owning loaders without changing accepted schemas.
- [x] 2.3 Type SQLite rows, parameters, transactions and returned state while preserving locking and atomic-publication behavior.
- [x] 2.4 Type subprocess, filesystem and third-party adapter results and keep platform-specific behavior valid on Ubuntu, Windows and macOS.
- [x] 2.5 Run focused boundary and persistence tests, review diagnostic deltas, and shrink the temporary baseline only for removed findings.

## 3. Repair research workflow types

- [x] 3.1 Repair event, source, generation and difference types and their immutable publication paths.
- [x] 3.2 Repair workflow, stage recomputation, DIF classification, MRQ batching and consolidation types without changing state transitions or fingerprints.
- [x] 3.3 Resolve every optional, unbound, argument, return and initialization diagnostic in this layer as a checked invariant or tested defect fix.
- [x] 3.4 Run focused workflow tests and the full Python suite, then shrink the temporary baseline from verified deltas.

## 4. Repair search and indexing types

- [x] 4.1 Repair indexing configuration, manifests, lifecycle and adapter discovery types without changing routes or index identities.
- [x] 4.2 Repair source search, reference search, search-service and process-bridge request and result types at their external boundaries.
- [x] 4.3 Verify cancellation, timeouts, bounded outputs, degraded states and platform-specific process handling with focused tests.
- [x] 4.4 Run search and indexing tests and shrink the temporary baseline from verified deltas.

## 5. Repair orchestration and workspace types

- [x] 5.1 Repair dispatcher, service, runner, pipeline graph and CLI types while preserving operation names and failure contracts.
- [x] 5.2 Repair workspace API request, response and internal state types without changing HTTP schemas or status codes.
- [x] 5.3 Resolve remaining import cycles, unused owned code, deprecated calls and intentionally ignored results after direct reference and side-effect review.
- [x] 5.4 Run API, CLI, dispatcher and full Python tests and shrink the temporary baseline to zero entries.

## 6. Remove the migration baseline

- [x] 6.1 Run BasedPyright without `baselineFile` and prove zero errors, zero warnings and zero notes across every production module.
- [x] 6.2 Delete `.basedpyright/baseline.json` and the `baselineFile` setting and add verification that rejects either artifact, ignored diagnostics, rule downgrades and production-source exclusions.
- [x] 6.3 Confirm the final code contains no broad `Any`, unchecked cast, fake protocol or shadow stub introduced only to satisfy the checker.

## 7. Synchronize and release-verify

- [x] 7.1 Run target doctor, strict doctor where the fixture is publication-complete, the full Python suite and the unsuppressed type check.
- [x] 7.2 Preview and fingerprint canonical synchronization, apply the unchanged plan, and prove normalized two-way parity for runtime source, configuration, lockfile and verification commands.
- [x] 7.3 Run template, doctor, fresh-repository, package, distribution, frontend and browser gates required by the release matrix.
- [ ] 7.4 Run BasedPyright on Ubuntu, Windows and macOS with the pinned version and review platform-specific branches.
- [ ] 7.5 Run `openspec validate eliminate-python-type-checking-baseline --strict --no-interactive`, `openspec validate --all --strict`, `git diff --check`, and direct final diff and artifact review.

## Разрывы ревью

<!-- GOAL_CURSOR -->
- [ ] 8.1 Удалить одноразовые `Protocol`-интерфейсы собственных модулей, введенные только для прохождения проверки типов, не возвращая циклы импортов.
  - Этап цикла: исправление
  - Состояние шага: независимое ревью обнаружило, что `owned_function()` скрывает сигнатуры 25 внутренних вызовов; задача повторно открыта
  - Следующее действие: заменить динамические вызовы конкретными типизированными границами и устранить возникающие циклы импортов минимальными локальными переносами
  - Файлы шага: src/one_c_autoresearch/contracts.py, src/one_c_autoresearch/agents.py, src/one_c_autoresearch/mrq.py, src/one_c_autoresearch/sources.py, src/one_c_autoresearch/workspace_api.py
- [x] 8.2 Определить и доказать нормализованный паритет собранных статических файлов между целевым репозиторием и каноническим runtime без перезаписи несвязанных пользовательских изменений.
- [x] 8.3 Заменить одноразовые протоколы встроенных контейнеров на прямое сужение типов и синхронизировать исправление во всех трех поверхностях.
- [x] 8.4 Исправить платформенные ветви, чтобы BasedPyright давал ноль ошибок и предупреждений при `--pythonplatform Windows`, не ослабляя общую конфигурацию.
- [x] 8.5 Удалить выявленную повторным Ponytail-ревью неиспользуемую обертку разблокировки файла.
- [x] 8.6 Не запускать заведомо POSIX-зависимый полный набор исполнения на Windows в матрице, сохранив обязательную проверку BasedPyright на всех трех ОС.
