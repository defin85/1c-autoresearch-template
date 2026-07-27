## Context

The repository currently models `analyze-dif` and `form-mrq` as two phases of one `discover-mrq` dispatcher job and one `mrq.discover-next` operation. Each run selects at most 32 uncovered DIF records, analyzes them, creates preliminary groups for that partial window, coordinates those groups, and then reaches a barrier that notices the remaining inventory. The graph has an implementation for canonical MRQ restructuring, including `merge` lineage, but the discovery graph does not invoke it.

The intended process is different: stage 2 must classify the complete DIF inventory in bounded parallel windows, while stage 3 must perform one global formation and consolidation pass over the complete classified DIF set and the complete active MRQ graph. The repository, not transient dispatcher state, must prove the boundary between those stages. The browser must expose the same boundary and must not imply that stage 3 is running while only one window is known.

### Locked Decisions

- Stage 2 and stage 3 are separate workflow jobs, operations, leases, runs, controls, and recovery scopes.
- Stage 2 retains a maximum analyzer window of 32 and bounded concurrency, but one run continues through successive windows until all customer DIF records are classified or the run stops explicitly.
- Stage 3 is not runnable until a repository-derived `all-dif-classified` gate proves exact classification coverage for the active physical-diff generation.
- Stage 3 sees the complete classified DIF inventory and complete active MRQ graph; it does not group one window at a time.
- Stage 3 preserves the canonical `DIF -> CUS -> MRQ` boundary: DIF is physical evidence, `CUS-*` is semantic customization identity, and MRQ implementation ownership is expressed through `CUS-*`.
- Stage 3 performs real canonical MRQ formation, merge, split, supersede, and revalidation as needed, with lineage for every changed identity.
- Every stage-3 plan requires explicit approval before one atomic CUS/MRQ publication transaction.
- Automatic retry remains disabled. A failed, interrupted, stale, or cancelled job requires its existing explicit recovery action.
- Reusable behavior is implemented and verified in a generated target repository first, then synchronized into the template.

## Goals / Non-Goals

**Goals:**

- Give stage 2 a durable, resumable, whole-inventory classification result independent of MRQ ownership.
- Prevent stage 3 execution against partial DIF evidence.
- Consolidate existing MRQs as well as form the first MRQ generation.
- Preserve exact one-primary-CUS-or-approved-noise coverage for every DIF and one implementation-owner MRQ for every included CUS at publication.
- Make progress, blockers, and actions unambiguous in the dispatcher.
- Define migration and rollback for current generated repositories and active legacy runs.

**Non-Goals:**

- Increase the analyzer window beyond 32 or remove configured concurrency limits.
- Automatically start stage 3, stage 4, or stage 5 after stage 2 completes.
- Change physical DIF identities, source generation formats, or target research decisions.
- Merge MRQs during stage 4 batch formation.
- Add a second task queue, an external workflow engine, or a new client state library.

## Decisions

### 1. Introduce a canonical DIF-classification generation

Stage 2 will publish accumulated classification rows under:

`analysis/dif-classifications/generations/<generation-id>/classifications.jsonl`

and atomically update:

`research/active-dif-classification-generation.json`

Each row is bound to the active source and physical-diff generation and contains one stable DIF identifier, `meaning|noise_candidate`, normalized semantic hints, evidence references with content fingerprints, rationale, and the analyzer result fingerprint. A stage-2 noise candidate is not approved noise. The manifest records exact row and input fingerprints.

Each completed window creates a new immutable accumulated generation. A window is complete only when every selected DIF has one valid result. A validation or agent failure makes the run `failed`; a soft stop makes it `resumable`; cancellation makes it `cancelled`. None advances the classification pointer for an incomplete window. Compatible successful node-result envelopes remain reusable only by explicit `retry` from `failed` or `resume` from `resumable`; cancellation requires a fresh start. The active pointer update is the only mutable canonical action. Publishing the same accumulated content is idempotent. A stale source or physical-diff binding fails before pointer mutation.

The `all-dif-classified` gate is complete only when the active classification generation contains exactly one valid row for every customer DIF in the active physical-diff inventory, no unknown identifier, and matching source/diff bindings. An empty inventory is represented by an explicit validated empty classification generation; stage 2 completes without an agent call and stage 3 still requires explicit start and approval to publish an empty compatible CUS/MRQ result.

An upstream source or diff recompute never rebinds classification rows by generation ID alone. It creates an explicit empty accumulated classification generation for the new bindings. A prior row is reusable only when the same stable DIF exists and its normalized physical-evidence fingerprint (path, kind, before/after content hashes), result schema, analyzer instruction/profile, and context-manifest allowed-path fingerprints are unchanged. Source/diff generation IDs are excluded only after those content checks pass; the validated row is then rebound to the new generation IDs. Every mismatch remains unclassified. The prior generation remains addressable. The existing recompute sequence must replace pre-stage-3 `mrq.revalidate-unchanged` with classification reset/import and computed downstream staleness.

Alternative: keep classifications only in SQLite node results. Rejected because operational state cannot prove a canonical workflow prerequisite and is disposable after restart or migration.

Alternative: create MRQ ownership while classifying each window. Rejected because ownership is the output of global stage-3 consolidation and would reproduce the current partial-grouping problem.

### 2. Split the workflow catalog at the operation boundary

The canonical catalog schema becomes version `4` with eight gates, nine jobs, and ten operations. `dif.classify-next` and `mrq.consolidate` start at operation version `1`.

Jobs:

1. `configure`
2. `acquire-sources`
3. `build-diffs`
4. `index-sources`
5. `analyze-dif`
6. `consolidate-mrq`
7. `classify-mrq`
8. `decide-mrq`
9. `publish`

New agent operations:

- `dif.classify-next`, owned by `analyze-dif`;
- `mrq.consolidate`, owned by `consolidate-mrq`.

The old `discover-mrq` job and `mrq.discover-next` operation are removed after migration. `analyze-dif` depends on indexes and advances `all-dif-classified`. `consolidate-mrq` depends on `all-dif-classified` and advances `mrq-consolidated` and `source-evidence-complete`.

The catalog adds `all-dif-classified` and renames the legacy `diffs-classified` gate to `mrq-consolidated`; this is one added gate overall, not two competing readiness authorities.

Alternative: keep one job and place an internal loop before grouping. Rejected because the UI, lease, retry, recovery, profile assignment, and audit trail would still expose one action for two independently meaningful stages.

### 3. Make one stage-2 run consume all remaining windows

The analyzer graph repeatedly:

1. derives the next unclassified window from the active physical-diff inventory and active classification generation;
2. dispatches at most 32 DIF records with the configured concurrency;
3. validates and publishes an accumulated classification generation;
4. recomputes progress and selects the next window.

It terminates complete when no unclassified DIF remains. It terminates resumable, failed, cancelled, interrupted, or stale according to existing dispatcher semantics. It never invokes a grouper, coordinator, MRQ mutation, or noise approval.

Progress is exact: total customer DIF, classified, meaning, noise candidate, current-window total, current-window completed, failed, and remaining. A window failure does not automatically retry and does not publish an invalid row for that DIF.

### 4. Give stage 3 one global consolidation graph

`mrq.consolidate` loads one immutable input snapshot containing:

- the complete active DIF-classification generation;
- the complete active `CUS-*` registry generation and its DIF evidence links, if one exists;
- the complete active MRQ graph, if one exists;
- all current primary and supporting DIF dispositions;
- current lineage and approvals;
- source and physical-diff generation bindings.

Stage 3 upgrades the current flat customization registry into immutable payloads under `analysis/customization-registry/generations/<generation-id>/`. Each generation contains `customization-items.jsonl`, `customization-evidence.jsonl`, `customization-links.jsonl`, `customization-lineage.jsonl`, and `manifest.json`. Row validation reuses the existing `customization-registry/v1` item/evidence/link/lineage contracts and their identity, cardinality, dangling-reference, and cycle checks. The manifest records schema version, source/diff/classification fingerprints, exact row counts, ordered file hashes, and a generation ID equal to the hash of that canonical manifest preimage. The guarded migration command may read existing flat files as legacy input, but after commit those flat paths remain forbidden authorities; only the generation subtree and aggregate pointer are permitted.

The generated CUS manifest schema starts at version `1`. The compatible MRQ generation schema becomes version `2`: primary and supporting relations reference `customization_id`, while physical DIF identifiers remain only in CUS evidence links. Existing MRQ version-1 rows and their DIF dispositions are immutable legacy input. Stage 3 may preserve a legacy `MRQ-*` identity only when its semantic key and the complete derived CUS ownership validate; otherwise the plan must represent the identity change explicitly as new, merge, split, or supersede.

One atomically replaced `research/active-consolidation-generation.json` is the sole version-4 authority. Bootstrap always creates its schema-version-1 `unpublished` state with null CUS/MRQ IDs and no downstream bindings; configure, source acquisition, diff build, indexing, classification, and strict doctor accept that sentinel, while consolidation and all downstream gates require their declared later state. Migration may create `legacy_input` with fingerprints and references to the prior flat CUS and MRQ version-1 evidence; only `consolidate-mrq` may consume that state, while downstream gates remain blocked. Approved publication replaces it with `active`, containing CUS generation ID, MRQ generation ID, source/diff/classification fingerprints, plan fingerprint, consolidation transaction ID, and downstream binding fields. Missing, invalid, or state-incomplete aggregate data fails closed.

Legacy `active-generation.json` and any convenience CUS pointer are read-only compatibility projections updated after the aggregate pointer and MUST NOT be used by version-4 server, CLI, doctor, or runner readers. Their absence or mismatch never invalidates a valid aggregate authority; doctor reports a repairable projection warning and the projection builder regenerates them. Thus a crash after aggregate replacement cannot hide or split the canonical pair.

The graph uses a deterministic bounded-reduction protocol. Profile verification stores a non-secret `input_context_tokens` capability obtained from the provider/model capability discovery for that exact model; it is not a free per-run input. The effective stage-3 profile MUST contain that verified positive limit. After subtracting the versioned prompt and response reserve, the runtime converts the remaining token budget to a conservative UTF-8 byte budget using the provider adapter's versioned estimator. An absent, stale, or non-positive capability fails preflight before any agent call.

Normalized classification, CUS, and MRQ records are sorted by stable identity and greedily packed without splitting a record. Partition capacity is calculated for the worst-case comparison invocation containing two partitions plus fixed prompt and response reserve, not for one partition alone. The manifest fingerprints every partition and enumerates every unordered partition pair, including self-pairs. Proposal workers process partitions; comparison workers process every pair; deterministic application code unions the fingerprinted equivalence and separation proposals and verifies a complete partition/pair bitmap before building the plan. No model invocation receives the complete matrix. Preflight reports exact partition, pair, and planned invocation counts before dispatch. If one normalized record cannot fit its half-pair budget, any expected pair/result chunk cannot be packed, or any manifest cell is missing, stage 3 fails with `consolidation.context_capacity` before approval and publishes nothing. This finite one-pass partition plus all-pairs comparison protocol has no heuristic early stop.

The plan explicitly lists:

- retained, newly formed, merged, split, and superseded CUS identities with complete physical DIF evidence membership;
- retained MRQs;
- newly formed MRQs;
- merges with two or more source MRQ IDs and exactly one target proposal;
- splits with exactly one source MRQ ID and two or more target proposals;
- superseded MRQs;
- revalidated unchanged identities;
- approved-noise candidates;
- complete primary DIF-to-CUS evidence membership and CUS-to-MRQ ownership assignments.

The MRQ outcome sets are exhaustive and mutually exclusive for every previously active MRQ. One source MRQ may participate in exactly one of retain, revalidate, merge-source, split-source, or supersede; one target identity may be created by exactly one outcome. Retain preserves the complete record unchanged. Revalidate preserves the `MRQ-*` identity while refreshing compatible bindings or evidence and records no self-lineage. Merge, split, and supersede record acyclic canonical lineage and never reuse a superseded target identity.

The same exclusivity applies to CUS outcomes. An unchanged semantic key preserves its deterministic `CUS-*` identity; a changed grouping uses the existing merge, split, or supersede lineage kinds, and no CUS may be both retained and a lineage source or target in the same plan.

Applying the plan extends and calls the existing pure `propose` and `restructure` set-reassignment semantics for CUS ownership over one cloned in-memory graph, not sequential service operations. The application boundary also applies the CUS plan, recomputes full DIF-to-CUS and CUS-to-MRQ coverage, and rejects duplicate ownership, missing or unknown identities, incomplete evidence, invalid lineage cardinality or cycles, or stale inputs. It writes both immutable generations first and atomically replaces the single aggregate consolidation pointer last. No intermediate generation pair becomes active.

For the first generation, the active CUS/MRQ graphs are empty and the same global plan consists of new CUS identities, new MRQ formations, evidence links, and noise candidates.

### 5. Separate readiness from approval

Stage 3 readiness requires `all-dif-classified`. Completing the consolidation plan does not itself mutate canonical state. Every plan, including an empty or unchanged plan, becomes blocked awaiting explicit approval. The full canonical JSON plan is atomically written under the user-scope operational project root at the server-derived path `consolidation-plans/<plan-fingerprint>.json`; callers cannot supply a path. SQLite stores only its fingerprint, relative path, complete source/diff/classification/prior-CUS/prior-MRQ fingerprints, status, and bounded UI aggregates. The file hash must equal the plan fingerprint before approval. A write, hash, or storage failure produces `consolidation.plan_storage` and exposes no approval action.

Approval reloads that exact plan under the repository lock, compares every bound fingerprint, and applies it once. The atomic aggregate pointer contains the approved plan fingerprint and is the durable publication receipt. After a crash, a missing operational receipt is reconstructed from that pointer; a duplicate approval returns the recorded generation pair. Rejection, cancellation, stale input, or duplicate approval leaves the aggregate pointer unchanged and remains auditable.

`source-evidence-complete` is evaluated only after canonical consolidation publication and requires complete evidence for all included CUS records, active MRQs, and approved noise.

Downstream staleness is computed, never written into immutable generations. MRQ version-2 rows contain source-side requirements and CUS ownership only; target decisions move to immutable `analysis/migration-requirements/decision-generations/<generation-id>/` payloads bound to the consolidation fingerprint. The aggregate pointer retains batch generation ID/input fingerprint and decision generation ID/input fingerprint; every derived-output manifest retains its input consolidation and decision fingerprints. A gate accepts an artifact only when all stored inputs equal the current aggregate pointer.

Publishing a changed CUS/MRQ pair clears batch and decision bindings and makes older output manifests fail comparison, except for decision rows explicitly carried by the approved consolidation plan. A decision is carryable only when its MRQ identity is retained or revalidated, its old primary-DIF closure exactly equals the new primary-CUS evidence closure, its target evidence/coverage/acceptance payload and approval fingerprint validate, and all normalized source/diff compatibility checks pass. Carried rows are written into the first version-4 decision generation with legacy provenance; every rejected legacy decision is reported stale and requires stage 5.

Stage 4 replaces only the batch binding through a typed aggregate compare-and-swap. Stage 5 writes one decision generation and atomically replaces only decision binding fields while preserving the CUS/MRQ IDs, plan fingerprint, consolidation transaction ID, and compatible batch binding; its own typed approval and idempotency receipt are keyed by consolidation plus decision fingerprints. `projections.build` replaces derived outputs. Rollback of the aggregate pointer restores previous binding predicates without mutating generations.

### 6. Reuse only compatible analyzer results

Existing node-result envelopes may seed the classification migration only when they:

- identify one DIF from the active physical-diff generation;
- match the current source, diff, operation-input, profile/instruction, context-manifest, and result schema fingerprints;
- pass current classification and evidence validation.

No preliminary group, coordinator result, or partial batch proposal is migrated into canonical stage-3 input. Missing or incompatible DIF results remain unclassified and are processed by stage 2.

### 7. Project distinct dispatcher circuits and controls

The server maps `analyze-dif` only to the `analyze-dif` circuit and `consolidate-mrq` only to `form-mrq`. Each circuit has its own lease, retry candidates, profiles, configured roles, progress, blockers, and actions.

Stage 2 shows full-inventory and current-window counts. Stage 3 shows readiness against the exact classification-generation fingerprint and, when running, proposal, merge, split, retained, superseded, evidence, and coverage counts. Before stage 2 completes, stage 3 is disabled and names the remaining unclassified DIF count.

The UI uses “DIF analysis” for stage 2 and “MRQ formation and consolidation” for stage 3. It never labels preliminary per-window grouping as MRQ formation.

### 8. Preserve immutable identity and explicit recovery

Execution snapshots include the classification generation, active CUS and MRQ generations, work unit, operation version, prompts, profiles, and input fingerprints appropriate to each job. Stage-2 resume continues from the active classification generation plus compatible cached results. Stage-3 resume keeps the same complete-input snapshot. Explicit retry against unchanged inputs reuses the same plan fingerprint and cannot create another generation or approval; retry after changed inputs creates a new plan identity and marks the prior plan stale.

No automatic retry or automatic downstream start is introduced.

## Risks / Trade-offs

- [Accumulated immutable classification generations increase small-file count] → Keep one JSONL payload and manifest per completed window; measure before adding compaction.
- [A long stage-2 run can span many windows] → Persist after every validated window and expose exact progress so restart resumes from canonical state.
- [Late source or physical-diff changes invalidate classification] → Bind every pointer and row to source/diff fingerprints and fail stale before mutation.
- [Global stage-3 context can exceed one model context] → Use deterministic inventory partitioning for proposal workers, then give the coordinator normalized bounded proposals plus the complete identity/coverage matrix, not raw source trees.
- [CUS or MRQ consolidation can destroy identity history] → Require canonical lineage, superseded source states, exact evidence and ownership reassignment, and atomic validation.
- [Legacy cached results may be unsafe to reuse] → Reuse only fully fingerprint-compatible analyzer envelopes; otherwise re-run.
- [Workflow catalog change breaks legacy resume] → Fail closed, mark legacy leases interrupted, and offer migration plus a fresh explicit start.
- [Stage 3 may be expensive after every upstream recompute] → Run it once per complete classification generation; do not execute on partial windows.

## Migration Plan

1. Add the classification schema, validator, immutable generation writer, active pointer, and doctor checks without changing the active workflow.
2. Add a migration command that imports only compatible analyzer results and reports imported, rejected, and remaining counts without publishing MRQs.
3. Add the two new operation versions and dispatcher jobs behind workflow schema version `4`.
4. Add an atomic legacy handoff in the operational SQLite transaction. Acquisition rejects `stage-recompute` and every non-legacy lease. With no lease it creates `workflow-migration`; with only one active or resumable `discover-mrq` lease it records that run and its invocation/phase work interrupted, preserves its audit identity and evidence, deletes that lease, and creates `workflow-migration` with a new fencing token in the same commit. That same transaction inserts a unique `workflow_migration_runs` row in `handoff_prepared` state containing the fence, migration ID, legacy lease preimage/audit IDs, and repository/workflow input fingerprints required to reconstruct the external journal. Every other lease acquisition rejects while migration exists. Migration never interrupts an active stage recompute; the operator must finish or cancel it explicitly.
5. With the acquired fencing token, the first external effect idempotently creates `<operational-project-root>/migrations/workflow-v3-v4.json` from the matching `handoff_prepared` row. Journal schema version `1` records the fence, input fingerprints, exact prior workflow/profile bytes, pointer contents or absence, database backup identity, and durable phases `prepared`, `legacy-interrupted`, `classification-published`, `catalog-switched`, and `committed`. After a crash with a migration lease but no journal, recovery must validate ownership of the same fence and reconstruct the journal from the SQLite row before any other action. Evidence and node results are never deleted during cutover.
6. Every server, CLI, doctor, dispatcher, recompute, and approval entrypoint checks both the global lease and journal before loading the catalog. Phases before `committed` expose only migration status/recovery and reject other mutations. Under the fence, migration imports compatible analyzer rows, records compatible legacy decisions as carryover candidates, writes workflow/profile files through temp-and-replace, validates the complete version-4 catalog and aggregate authority, and then commits the journal.
7. Restart handling is deterministic: `handoff_prepared` without a journal reconstructs it idempotently; `prepared` or `legacy-interrupted` resumes when backups, fence, and inputs validate and otherwise rolls back; `classification-published` or `catalog-switched` completes validation and commit when possible and otherwise rolls back. `committed` returns the stored result. No phase repeats a content-addressed generation or terminal lease transition.
8. Preserve existing immutable source, diff, CUS, MRQ, batch, and decision directories. Determine downstream staleness only from binding comparisons.
9. Start stage 2 explicitly if classification remains incomplete. Enable stage 3 only after the new gate passes.
10. Verify a first-generation path and an existing-MRQ path containing at least one real merge.
11. Implement and verify in `sppr-research-ver2`, then synchronize reusable runtime, tests, web sources, static assets, manifests, and package archive into the template.

Rollback restores the exact backed-up workflow declarations, non-lease operational records, and every prior active-pointer content or prior absence. It then records every pre-migration active or resumable legacy `discover-mrq` lease as interrupted before releasing the migration fence; evidence and node results remain intact. Newly written immutable generations remain unreferenced. Existing source, diff, CUS, MRQ, batch, and decision generations remain unchanged. A legacy run is never silently resumed across the catalog boundary.

## Open Questions

None. The workflow boundary, canonical classification artifact, global consolidation scope, merge lineage, approval behavior, migration reuse rule, and non-automatic downstream execution are locked by this change.
