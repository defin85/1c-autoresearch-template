## 1. Establish the migration gate

- [x] 1.1 Recalculate unsuppressed diagnostics in the verified target and record aggregate counts by module and rule without storing source snippets.
- [x] 1.2 Add the pinned BasedPyright dependency and production-source configuration to canonical dependency metadata, scaffold verification and supported CI.
- [x] 1.3 Add temporary-baseline guards that forbid rule downgrades, ignored diagnostics, per-file overrides and exclusion of owned production modules.

## 2. Type external and persistence boundaries

- [x] 2.1 Define minimal owned types for shared identifiers, JSON/TOML values and serialized workflow records, reusing existing dataclasses and enums where available.
- [x] 2.2 Validate and narrow repository-file, JSON and TOML inputs at their owning loaders without changing accepted schemas.
- [x] 2.3 Type SQLite rows, parameters, transactions and returned state while preserving locking and atomic-publication behavior.
- [x] 2.4 Type subprocess, filesystem and third-party adapter results and keep platform-specific behavior valid on Ubuntu, Windows and macOS.
<!-- GOAL_CURSOR -->
- [ ] 2.5 Run focused boundary and persistence tests, review diagnostic deltas, and shrink the temporary baseline only for removed findings.
  - Этап цикла: реализация
  - Состояние шага: профильные тесты границ и индексов проходят; полнопакетная проверка выявила межмодульные расхождения workflow, pipeline graphs и dispatcher
  - Следующее действие: завершить полнопакетную типизацию workflow и pipeline graphs, затем пересчитать и только уменьшить временную baseline
  - Файлы шага: src/one_c_autoresearch/workflow.py, src/one_c_autoresearch/pipeline_graphs.py, src/one_c_autoresearch/dispatcher.py

## 3. Repair research workflow types

- [ ] 3.1 Repair event, source, generation and difference types and their immutable publication paths.
- [ ] 3.2 Repair workflow, stage recomputation, DIF classification, MRQ batching and consolidation types without changing state transitions or fingerprints.
- [ ] 3.3 Resolve every optional, unbound, argument, return and initialization diagnostic in this layer as a checked invariant or tested defect fix.
- [ ] 3.4 Run focused workflow tests and the full Python suite, then shrink the temporary baseline from verified deltas.

## 4. Repair search and indexing types

- [ ] 4.1 Repair indexing configuration, manifests, lifecycle and adapter discovery types without changing routes or index identities.
- [ ] 4.2 Repair source search, reference search, search-service and process-bridge request and result types at their external boundaries.
- [ ] 4.3 Verify cancellation, timeouts, bounded outputs, degraded states and platform-specific process handling with focused tests.
- [ ] 4.4 Run search and indexing tests and shrink the temporary baseline from verified deltas.

## 5. Repair orchestration and workspace types

- [ ] 5.1 Repair dispatcher, service, runner, pipeline graph and CLI types while preserving operation names and failure contracts.
- [ ] 5.2 Repair workspace API request, response and internal state types without changing HTTP schemas or status codes.
- [ ] 5.3 Resolve remaining import cycles, unused owned code, deprecated calls and intentionally ignored results after direct reference and side-effect review.
- [ ] 5.4 Run API, CLI, dispatcher and full Python tests and shrink the temporary baseline to zero entries.

## 6. Remove the migration baseline

- [ ] 6.1 Run BasedPyright without `baselineFile` and prove zero errors, zero warnings and zero notes across every production module.
- [ ] 6.2 Delete `.basedpyright/baseline.json` and the `baselineFile` setting and add verification that rejects either artifact, ignored diagnostics, rule downgrades and production-source exclusions.
- [ ] 6.3 Confirm the final code contains no broad `Any`, unchecked cast, fake protocol or shadow stub introduced only to satisfy the checker.

## 7. Synchronize and release-verify

- [ ] 7.1 Run target doctor, strict doctor where the fixture is publication-complete, the full Python suite and the unsuppressed type check.
- [ ] 7.2 Preview and fingerprint canonical synchronization, apply the unchanged plan, and prove normalized two-way parity for runtime source, configuration, lockfile and verification commands.
- [ ] 7.3 Run template, doctor, fresh-repository, package, distribution, frontend and browser gates required by the release matrix.
- [ ] 7.4 Run BasedPyright on Ubuntu, Windows and macOS with the pinned version and review platform-specific branches.
- [ ] 7.5 Run `openspec validate eliminate-python-type-checking-baseline --strict --no-interactive`, `openspec validate --all --strict`, `git diff --check`, and direct final diff and artifact review.
