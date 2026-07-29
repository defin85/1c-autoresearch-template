## Context

The runtime already describes selected context with a work unit, allowed paths, a context manifest, immutable execution bindings, and structured result schemas, while the provider adapter supplies the repository-level read-only sandbox. Stage 2 supplies per-DIF facts and evidence, stage 3 partitions the complete classification and MRQ graph, later classification uses stable MRQ windows, and target research supplies one MRQ plus bounded related summaries. These strategies solve different completeness problems and should not be collapsed into one coordinator heuristic.

The missing boundary is a common, inspectable contract around those payloads. Capacity enforcement is currently strongest in consolidation, while other stages rely on fixed windows or implicit limits. A context fingerprint proves equality but does not explain selection, origin, omissions, or headroom.

## Locked Decisions

1. Use one `context-envelope/v1` shape for every agent invocation.
2. Preserve stage-owned deterministic selection policies; unification does not mean one selection algorithm.
3. Require budget preflight for every agent call using that invocation role's effective provider context-window limit and a versioned estimator.
4. Record provenance and a selection reason for every included item.
5. Keep diagnostics bounded, repository-relative, redacted, and free of prompt text, private reasoning, credentials, and arbitrary file content.
6. Keep the existing context manifest authoritative for selection identity and freshness; `allowed_paths` does not narrow the repository-level read-only sandbox.
7. Include envelope, estimator, and selection-policy versions in reuse compatibility.
8. Keep prior immutable invocation records readable with explicit unavailable markers.
9. Keep the stage-3 coordinator as a reducer over bounded proposals; it does not regain sole responsibility for preparing all context.
10. Do not create a context-package registry, generation pointer, approval lifecycle, or second dispatcher-inspection API.
11. Do not truncate context dynamically to make a call fit; stage selection completes first and budget validation either admits the complete selected payload or fails closed.
12. Budget only the initial input assembled by this workflow and its bounded response reserve; provider-managed tool transcripts, compaction, and monetary usage are not inferred without provider telemetry.
13. Keep the 4096-token structured-response reserve by replacing the monolithic stage-3 coordinator output with bounded hierarchical comparison and reduction calls.

## Context Envelope

The envelope contains:

- `contract_version`: exact envelope schema version;
- `stage`, `role`, and `work_unit`: invocation purpose and stable subject identity;
- `bindings`: active source, DIF, classification, MRQ, execution-snapshot, profile, instruction, and result-schema fingerprints that apply to the stage;
- `payload.subject`: the primary DIF, MRQ, partition, pair, or target-decision item;
- `payload.facts`, `payload.evidence`, and `payload.related_subjects`: ordered stage-selected items;
- `selection`: selection-policy identifier and version, deterministic ordering key, included count, candidate count, and bounded omission summaries;
- `provenance`: one content-free descriptor per selected context item;
- `budget`: provider context-window limit, fixed output reserve, estimated complete input usage, remaining headroom, estimator identifier and version, and measurement units;
- `allowed_paths`: repository-relative selected source paths fingerprinted by the context manifest;
- `prepared_input_fingerprint`: canonical hash of the exact initial provider input;
- `envelope_fingerprint`: canonical hash of the completed envelope excluding only this field.

Each versioned stage policy declares the granularity of a context item. At minimum the subject and every entry of `facts`, `evidence`, and `related_subjects` are separate items; nested scalars are values of that item rather than implicit items. Every item carries a stable item key, kind, repository-relative origin reference when it originates from a file, source-generation or registry fingerprint where applicable, and selection reason. Repository-record origins use stable registry identifiers rather than invented file paths. Repeated content may be represented once and linked from several subjects.

Envelope construction has no size cycle. The selector first freezes `payload`, provenance, selection metadata, and selected paths. The adapter renders the initial provider input from that immutable projection, excluding `budget`, `prepared_input_fingerprint`, `envelope_fingerprint`, and diagnostics. It then calculates the budget and fingerprints and does not rerender a different provider input.

The first implementation reuses `utf8-v1`, the estimator already required by consolidation. For a provider context window of `N` tokens it reserves exactly 4096 tokens for the structured response and admits at most `2 × (N - 4096)` UTF-8 bytes of complete initial input. Complete initial input means the base instruction text, operation and instruction-version framing, stage supplement, canonical selected payload, fixed adapter framing, and canonical response schema. Every response schema adds finite maximum lengths and item counts whose maximum canonical JSON size fits the same 8192-byte reserve; an unbounded response schema fails preflight. The diagnostic records both token capacity/reserve and estimated input bytes/byte headroom; it does not label the byte estimate as measured tokens. Unsupported estimator identifiers fail closed. A later estimator requires a new version and invalidates incompatible reuse.

This is prepared-context accounting, not provider telemetry. Tool reads and provider-managed transcript compaction after the initial request remain governed by the existing read-only adapter and instruction policy. The workspace neither reports them as preflight usage nor invents actual token or monetary cost.

## Budgeting And Failure

The stage selector first produces its complete deterministic payload and grouped counts for candidates excluded by stage policy. The runtime then renders the exact initial provider input once and validates it as one unit. It never removes selected facts, evidence, related subjects, partitions, or proposals to make the call fit. If the complete selected input cannot fit, the call fails before provider invocation with a typed context-capacity blocker.

Unknown provider capacity, capacity not greater than the 4096-token output reserve, unknown estimator version, negative byte headroom, an untraceable included item, a stale selected path fingerprint, or budget-driven truncation all fail closed. No partial result is published.

Budgeting is per invocation and uses the effective profile of its actual role. Consolidation therefore plans grouper calls against the grouper profile and the final reduction against the coordinator profile; one role's larger window cannot authorize another role's payload. Phase diagnostics may sum estimates and call counts for operation planning, but no monetary-cost estimate is introduced.

## Stage Policies

- DIF analysis selects one DIF, its physical and semantic facts, component facts, dependencies, and exact evidence.
- MRQ consolidation retains complete deterministic partition and pair coverage, component summaries, proposals, and final reduction.
- MRQ classification retains stable bounded windows and exact DIF-to-MRQ linkage evidence.
- Target research selects one MRQ, bounded related summaries, target coverage, and source evidence.
- Deterministic whole-component classification emits the same provenance and budget-free decision diagnostic, marked `execution_kind=deterministic`, but creates no invocation.

Each policy has its own identifier and version. The common builder validates and fingerprints the result but does not decide stage relevance.

### Hierarchical Stage-3 Reduction

The existing grouper partition and pair calls remain the leaves. The runtime assigns every validated candidate proposal a content-derived `candidate_id` and retains its complete DIF membership server-side. It then:

1. divides candidate descriptors into deterministic bounded pages;
2. invokes the coordinator for every unordered page pair, returning only bounded links between candidate IDs and explicit keep-separate conflicts;
3. validates exact page-pair coverage and applies links through deterministic union-find;
4. reduces the descriptive fields of every linked component through a deterministic binary tree whose response names only its two input aggregate IDs and bounded descriptive fields;
5. derives the final stable and supporting DIF membership from the server-owned union rather than from agent-repeated arrays;
6. reviews noise candidates in deterministic bounded pages and requires exact noise coverage;
7. assembles and validates the ordinary consolidation plan only after link, reduction, noise, and DIF-ownership closure are complete.

Every intermediate aggregate ID is a hash of the algorithm version, ordered child IDs, and validated reduction decision. Page and tree boundaries create no canonical MRQ identity. A missing page, duplicate or unknown candidate ID, contradictory keep/merge result, oversized component input, incomplete noise coverage, or stale binding fails the complete stage before approval. No partial hierarchy is published, though compatible completed node results remain reusable under the normal envelope rules.

## Provenance And Diagnostics

Before provider execution, the dispatcher atomically stores the envelope fingerprint and a content-free provenance ledger with the invocation row and `invocation.started` outbox transition. The ledger contains one descriptor for every included item but no fact value, evidence body, prompt, response schema body, or source-file content. It is bounded by the already validated envelope size and uses stable identifiers, closed item-kind and selection-reason codes, origin references, and fingerprints. Free-form labels and rationales stay in their canonical evidence stores. The invocation also stores a bounded summary containing:

- contract, estimator, and selection-policy versions;
- budget limit, estimated use, reserved capacity, headroom, and unit;
- counts by context kind and origin kind;
- bounded included-item descriptors;
- candidates excluded by stage policy, grouped by reason, and a zero budget-truncation count;
- allowed-path and source-binding fingerprints;
- reuse status and incompatibility reason when applicable.

The existing project-scoped dispatcher detail resource and contextual inspector expose the summary and paginate the content-free provenance ledger. No second diagnostic API is added. Browser rendering treats all provider- and repository-derived text as plain text.

If budget preflight fails before invocation allocation, the typed blocker and safe budget diagnostic are stored on the existing phase-work/checkpoint state and projected by the stage inspector. If compatible work is reused, existing phase-work state records `execution_kind=reused`, the source result reference, compatibility fingerprint, and safe reuse diagnostic without allocating a slot or invocation. Deterministic whole-component work uses the same non-invocation path with `execution_kind=deterministic`.

## Reuse And Migration

A cached result is reusable only when the active execution bindings, context-envelope version, estimator version, selection-policy version, selected-path manifest fingerprint, envelope fingerprint, instruction/profile identity, provider capability fingerprint, and result schema all match. A mismatch explains the first incompatible field and schedules ordinary recomputation; it never silently rebinds a result.

Legacy invocations and generations remain readable. Their missing envelope details are reported as unavailable, and they are not upgraded in place. New runs use execution-snapshot schema version 2, which declares `context-envelope/v1` and `utf8-v1`. On upgrade, a running or resumable agent job whose immutable snapshot is version 1 is terminalized as interrupted through the existing fenced recovery transaction and requires explicit restart; its completed node results remain audit evidence but are not reusable. From the runtime release that activates `context-envelope/v1`, no legacy result lacking the new fingerprint set is reusable by an adapted stage.

Once every active stage compiler calls the shared envelope builder, the unused combined discovery compiler and its dual context strategy are removed. Historical records and migration readers remain intact.

The SQLite migration follows the existing inspector boundary but uses its own owner-only online backup named `.pre-context-envelope.sqlite`; it must not reuse `.pre-inspector.sqlite`. It adds nullable envelope-fingerprint, prepared-input-fingerprint, provenance-ledger, and diagnostic-summary columns with explicit insert column lists and makes reruns idempotent. Ledger and summary rows follow the existing invocation/phase-work operational-state lifecycle; this change adds no independent purge or retention authority. A previous frontend ignores the additive fields. Rolling the backend back requires stopping mutations and restoring `.pre-context-envelope.sqlite`, losing only post-upgrade operational history while repository generations remain unchanged.

## Audit Matrix

| Concern | Required evidence |
| --- | --- |
| Contract | Every provider invocation validates `context-envelope/v1` |
| Budget | Every call proves bounded response schema and non-negative prepared-input headroom with a known limit and estimator |
| Provenance | Every included item has origin, binding, and selection reason |
| Completeness | Budget never truncates selected context; stage-policy exclusions are counted and explained |
| Stage autonomy | Existing stage-specific selection and coverage rules remain intact |
| Reuse | Envelope and policy versions participate in compatibility checks |
| Security | Diagnostics disclose no prompts, reasoning, credentials, unrestricted output, or arbitrary files |
| Selection freshness | Every selected file origin is repository-relative, fingerprinted, and reverified by the context manifest |
| Legacy | Prior immutable records remain readable with unavailable markers |
| Upgrade | Snapshot-v1 active work is interrupted and explicitly restarted; completed legacy results are not reused |
| Operability | Inspector shows budget, origins, omissions, fingerprint, and incompatibility cause |
| Authority | No new canonical context entity, pointer, or approval is created |
| Performance | Canonical rendering and hashing are linear in the already bounded prepared input |
| Scalability | Large inputs retain stage partition/window behavior and fail before provider calls when a complete unit cannot fit |
| Cost | Diagnostics expose planned/reused/provider-call counts and prepared capacity only, without invented monetary usage |
| Rollback | A dedicated pre-context backup restores operational state without reverting repository generations or prior inspector migrations |
| Verification | Unit, integration, API, frontend, browser, scaffold, and strict checks cover the contract |

## Execution Plan

1. Define and validate the envelope, provenance, budget, and diagnostic schemas in the existing agent runtime.
2. Route each active stage compiler through the common builder while retaining its selection policy and tests.
3. Replace monolithic stage-3 coordination with bounded candidate-page comparisons, deterministic union, binary descriptive reduction, and paged noise closure.
4. Render and budget the complete input with each invocation role's effective profile before slot allocation.
5. Persist content-free provenance and bounded summaries atomically and expose them through existing dispatcher inspection.
6. Migrate execution snapshots and operational SQLite, reconcile legacy active work, and add deterministic/reuse diagnostics.
7. Remove the unused combined compiler, synchronize reusable runtime and scaffold outputs, and run the canonical verification matrix.

## Risks / Trade-offs

- [A conservative estimate rejects a call that might fit] → Keep the estimator versioned and show fixed, selected, reserved, and headroom values.
- [Runtime tool output grows after preflight] → Label the metric as prepared input and rely on the existing provider transcript management rather than presenting invented actual usage.
- [Detailed provenance grows operational SQLite] → Store content-free descriptors only, paginate reads, and retain canonical evidence in its existing stores.
- [One shared schema pressures stages into one algorithm] → Put relevance and ordering in versioned stage policies, not in the common builder.
- [Hierarchical links merge incompatible proposals transitively] → Require explicit pair decisions, reject merge/keep contradictions, and derive each aggregate from validated child IDs.
- [Legacy cached results stop being reusable] → Preserve them as audit evidence and recompute only when the stage next runs.
- [Upgrade encounters resumable snapshot-v1 work] → Interrupt it transactionally and require an explicit new run rather than mixing contracts.
- [Diagnostics leak source content] → Return identifiers, relative origins, hashes, counts, and safe summaries only.

## Assumptions And Open Questions

- Assumption: the existing serialized-input estimator is sufficient for the first cross-stage contract.
- Assumption: provider profiles can expose a validated context-window limit and capability fingerprint for every active agent role.
- Assumption: current dispatcher inspection is the only operator surface needed for context diagnostics.
- Open questions: none.
