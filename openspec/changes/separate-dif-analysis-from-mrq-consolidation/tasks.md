## 1. Canonical DIF Classification State

- [ ] 1.1 Add the immutable DIF-classification generation schema, active pointer, exact source/diff bindings, and strict validation.
- [ ] 1.2 Publish each validated window as an accumulated generation with idempotent pointer updates and stale-input rejection.
- [ ] 1.3 Derive `all-dif-classified` from exact inventory coverage and expose total, classified, remaining, meaning, and noise counts.
- [ ] 1.4 Extend bootstrap, doctor, and repository checks for the classification artifacts and their generation integrity.
- [ ] 1.5 Replace pre-stage-3 MRQ revalidation in stage recompute with an empty classification generation and cross-generation reuse based on stable DIF plus normalized evidence, schema, profile/instruction, and context fingerprints.
- [ ] 1.6 Handle an empty DIF inventory without agent execution and make a partially failed window publish no pointer update while retaining compatible envelopes for explicit recovery.

## 2. Split Workflow Catalog

- [ ] 2.1 Replace the combined `discover-mrq` job with separate `analyze-dif` and `consolidate-mrq` jobs.
- [ ] 2.2 Add typed `dif.classify-next` and `mrq.consolidate` operations and remove `mrq.discover-next` after migration.
- [ ] 2.3 Add `all-dif-classified`, rename `diffs-classified` to `mrq-consolidated`, and enforce workflow schema version 4 with the eight-gate, nine-job, ten-operation catalog and exact operation versions.
- [ ] 2.4 Make one stage-2 run resume through all remaining bounded windows without starting stage 3 or retrying automatically.

## 3. Global MRQ Consolidation

- [ ] 3.1 Build one deterministic stage-3 input snapshot from every classified DIF and the complete active CUS and MRQ graphs.
- [ ] 3.2 Produce a normalized plan with exhaustive mutually exclusive CUS/MRQ outcomes, complete DIF-to-CUS evidence membership, CUS-to-MRQ ownership, and approved-noise candidates.
- [ ] 3.3 Implement provider-validated context budgets, versioned byte estimation, deterministic stable-ID partitions, every unordered partition pair, a fingerprinted coverage bitmap, and fail-closed capacity checks.
- [ ] 3.4 Atomically persist the full plan as `consolidation-plans/<fingerprint>.json`, store only its reference and bounded aggregates in SQLite, and bind explicit approval to source, diff, classification, prior-CUS, prior-MRQ, and plan fingerprints.
- [ ] 3.5 Apply the complete plan to cloned in-memory graphs through existing pure semantics, write both generations, and atomically replace the single authoritative `active-consolidation-generation.json`.
- [ ] 3.6 Enforce stable identity rules, acyclic merge/split/supersede lineage, no revalidation self-lineage, complete coverage, and stale-input CAS before pointer mutation.
- [ ] 3.7 Make repeated approval or retry of unchanged inputs return the existing published result without identity churn or another generation.
- [ ] 3.8 Add exact downstream input bindings and gate comparisons for batch, decision, and derived-output generations; clear aggregate batch and decision bindings on changed consolidation.
- [ ] 3.9 Add CUS manifest schema version 1 with exact files, hashes, row counts, content-derived identity, loaders and validators; add MRQ generation schema version 2 with CUS-owned relations and legacy version-1 input migration.
- [ ] 3.10 Route every version-4 server, CLI, doctor, runner, and recompute reader through the aggregate consolidation pointer and make legacy individual pointers compatibility projections only.
- [ ] 3.11 Implement aggregate `unpublished`, `legacy_input`, and `active` states, restrict legacy input to stage 3, and regenerate stale compatibility projections without blocking valid canonical reads.
- [ ] 3.12 Update bootstrap, forbidden-authority rules, runtime synchronization manifests, package contents, and strict doctor for generated CUS generations and aggregate authority.
- [ ] 3.13 Move target decisions out of MRQ version-2 rows into immutable decision generations and add typed stage-5 aggregate CAS, approval, receipt, and idempotence while preserving consolidation and batch fields.
- [ ] 3.14 Extract legacy MRQ version-1 decisions, carry only exact retained/revalidated closure and approval matches into the first decision generation, and report incompatible rows stale.

## 4. Migration And Recovery

- [ ] 4.1 Add the fenced user-scope `migrations/workflow-v3-v4.json` journal with exact schema, backups, fingerprints, and prepared, legacy-interrupted, classification-published, catalog-switched, and committed phases.
- [ ] 4.2 Import only compatible analyzer result envelopes into a validated classification generation and report imported and remaining counts.
- [ ] 4.3 Exclude preliminary grouper, coordinator, and partial MRQ proposal results from canonical stage-3 state.
- [ ] 4.4 Interrupt legacy combined leases without deleting evidence, invalidate legacy approvals, and require a fresh explicit split-job start.
- [ ] 4.5 Preserve immutable source, diff, CUS, MRQ, batch, and decision generations; restore exact prior files, non-lease database records, and pointer contents while terminalizing restored legacy leases.
- [ ] 4.6 Guard every mutating entrypoint while the journal is uncommitted and implement deterministic resume-or-rollback rules for every phase.
- [ ] 4.7 Add crash-recovery, fencing-loss, entrypoint-guard, rollback-lease, and repeated-migration checks for every journal boundary.
- [ ] 4.8 Make `workflow-migration` globally exclusive and atomically replace an eligible lone `discover-mrq` lease with interruption, a new migration fence, and a durable `handoff_prepared` SQLite recovery row; reject stage-recompute and all non-legacy conflicts.
- [ ] 4.9 Reconstruct a missing external journal idempotently from `handoff_prepared` under the same fence and test the crash boundary immediately after SQLite commit.

## 5. Dispatcher And Web Interface

- [ ] 5.1 Expose separate stage-2 and stage-3 actions, leases, profiles, runs, recovery controls, and typed early-start rejection.
- [ ] 5.2 Show stage-2 window and aggregate classification progress and stage-3 planning, approval, and publication progress.
- [ ] 5.3 Update dispatcher cards and detail panels to show separate DIF-classification, CUS-consolidation, and MRQ-consolidation collections without embedding long item lists.
- [ ] 5.4 Keep downstream execution and retry explicit; completion of either stage MUST NOT start another job automatically.
- [ ] 5.5 Show unpublished partial windows, context-capacity blockers, stale downstream bindings, and idempotent already-applied approval results.
- [ ] 5.6 Verify and display the effective model context capability and exact stage-3 preflight partition, pair, and planned invocation counts before dispatch.

## 6. Target-First Verification And Template Promotion

- [ ] 6.1 Implement and test the behavior first in `sppr-research-ver2`, including first-generation and existing-MRQ scenarios with at least one real merge.
- [ ] 6.2 Add server tests for exact DIF-to-CUS-to-MRQ coverage, multi-window resume, partial-window failure, empty inventory, stale inputs, migration reuse, plan exclusivity, lineage, mandatory approval, idempotence, and atomic aggregate-pointer publication.
- [ ] 6.3 Add client tests and browser checks for incomplete, running, blocked, approval-required, failed, interrupted, and completed states.
- [ ] 6.4 Add recovery tests for every migration phase, aggregate-pointer publication crash point, plan-receipt reconstruction, recompute reset/reuse, and computed downstream invalidation.
- [ ] 6.5 Synchronize only reusable runtime, tests, web sources, generated assets, manifests, and package contents into the template.
- [ ] 6.6 Run the target research checks, template checks, deep strict doctor, package smoke test, and strict OpenSpec validation.
