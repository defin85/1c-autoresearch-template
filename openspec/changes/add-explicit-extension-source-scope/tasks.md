## 1. Extend The Tracked Source Contract

- [x] 1.1 Add canonical extension decision records to the infobase contract with UUID, `include` or `exclude`, and required exclusion rationale.
- [x] 1.2 Validate malformed or duplicate UUIDs, unsupported decisions, empty exclusion rationales, and deterministic serialization.
- [x] 1.3 Include normalized extension decisions in source-contract, preview, workflow, and comparison-epoch fingerprints.
- [x] 1.4 Preserve dormant decisions for UUIDs not currently discovered without creating routing members.

## 2. Make Routing Fail Closed

- [x] 2.1 Compute the union of extension UUIDs from all three current tested connection profiles with per-role name, version, activation, and presence facts.
- [x] 2.2 Return a typed `extension_scope_required` blocker for every discovered UUID without a tracked decision.
- [x] 2.3 Filter extension component membership to explicit `include` decisions before acquisition, indexing, and comparison.
- [x] 2.4 Publish excluded and dormant extension summaries in route preview while keeping excluded payloads out of source generations.
- [x] 2.5 Revalidate profile enumeration, workflow, and contract fingerprints under the existing mutation lock before acquisition.
- [x] 2.6 Repeat bounded live extension enumeration and private UUID-verification staging for all three roles immediately before export; reuse only verified included payloads, delete excluded or failed staging, and on drift publish nothing and require refreshed review.

## 3. Align The Workspace Contract

<!-- GOAL_CURSOR -->
- [ ] 3.1 Add a dedicated extension-review section with one UUID row, three role observations, decision control, and conditional rationale.
  - Этап цикла: реализация
  - Состояние шага: раздел, полный выбор и доступные UUID-связанные элементы реализованы; требуется связать блокировщик и общий CLI-контракт
  - Следующее действие: добавить переход от extension_scope_required к точной строке, общий read-only helper и CLI-диагностику
  - Файлы шага: ../sppr-research-ver2/web/workspace/src/App.tsx; ../sppr-research-ver2/src/one_c_autoresearch/workspace_api.py; ../sppr-research-ver2/src/one_c_autoresearch/cli.py; ../sppr-research-ver2/tests; openspec/changes/add-explicit-extension-source-scope/tasks.md
- [ ] 3.2 Preview and confirm the exact tracked contract mutation using existing stale-input and idempotency protections.
- [ ] 3.3 Direct `extension_scope_required` blockers to the extension-review section and preserve unsaved selections during unrelated reconciliation.
- [ ] 3.4 Rename and explain the external-artifact section as uploaded external files for EPF, ERF, source trees, and other declared files.
- [ ] 3.5 Expose the same extension-scope readiness and diagnostics through CLI and browser paths.
- [ ] 3.6 Show include/exclude consequences, keep activation informational, list dormant decisions separately, and provide UUID-associated accessible controls, errors, and keyboard operation.

## 4. Preserve Generation And DIF Boundaries

- [ ] 4.1 Start a new comparison epoch when an extension decision changes or a new unreviewed UUID is discovered.
- [ ] 4.2 Keep previous immutable generations readable while rejecting them as current readiness evidence.
- [ ] 4.3 Prove excluded extensions produce no source component, physical extension row, semantic extension intervention, DIF classification unit, or MRQ input.
- [ ] 4.4 Prove included extensions retain existing UUID-based physical-to-semantic path closure and target coverage behavior.
- [ ] 4.5 Derive component kind, component key, whole-component change direction, exact member DIF IDs, evidence, and coverage from canonical source and diff facts without persisting a package entity.
- [ ] 4.6 Deterministically publish existing-schema `meaning` classifications for every DIF of a wholly added or deleted included extension or declared external-artifact component without an agent call.
- [ ] 4.7 Reuse existing bounded stage-2 windows: derive eligible rows locally, call the agent only for other window members, publish no partial window on failure, and never auto-start stage 3.
- [ ] 4.8 Keep ordinary stage-2 classification for DIF of components present on both comparison sides.
- [ ] 4.9 Supply paged bounded component-grouped context to stage 3 and allow one group to produce one or more ordinary MRQs while preserving exact primary-DIF ownership.
- [ ] 4.10 Version deterministic classification as `whole-component-meaning/v1`, bind it through existing fingerprints, and resolve added evidence from `target_cf` and deleted evidence from `vendor_baseline`.
- [ ] 4.11 Treat grouping as a hint that preserves compatible retained MRQs and normal merge, split, supersede, cross-component evidence, and complete closure rules.
- [ ] 4.12 Prove component grouping preserves every stable member DIF and never creates one synthetic extension-level or external-artifact-level DIF.

## 5. Migrate And Verify

- [ ] 5.1 Add legacy-project coverage showing existing generations remain readable and the next acquisition blocks until explicit review.
- [ ] 5.2 Add role-drift, rename-with-stable-UUID, activation-change, dormant-policy reappearance, stale-preview, pre-export live-drift, bound-exhaustion, and concurrent-edit tests.
- [ ] 5.3 Add deterministic-classification and consolidation tests for whole extension and external-artifact addition/deletion, modified existing components, preservation of individual DIF identities, one-group-to-many-MRQ decomposition, cross-component evidence, and absence of package persistence.
- [ ] 5.4 Add frontend and browser acceptance for empty, included, excluded, dormant, mixed-role, and newly discovered extension sets, including impact text, UUID-associated errors, keyboard operation, and unsaved-state preservation.
- [ ] 5.5 Implement and verify target-first in a concrete research repository, then synchronize only reusable behavior into the template runtime and scaffold.
- [ ] 5.6 Rebuild static assets and the packaged research template, then run Python, frontend, browser, synchronization, template, doctor, and strict OpenSpec checks.
- [ ] 5.7 Verify frontend-only rollback, document full-runtime downgrade as read-only recovery with mutation entrypoints stopped, and prove reinstalling the enforcing runtime resumes from unchanged decisions and immutable generations.
