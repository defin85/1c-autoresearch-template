## ADDED Requirements

### Requirement: Use a versioned context envelope for every agent invocation
The generated workflow SHALL validate every provider-bound agent input against one versioned context envelope containing invocation bindings, subject, facts, evidence, related subjects, selection metadata, budget accounting, selected source paths, provenance, and a canonical envelope fingerprint while preserving stage-specific deterministic selection policies.

#### Scenario: A stage prepares an agent call
- **WHEN** DIF analysis, MRQ consolidation, MRQ classification, or target research prepares a provider-bound invocation
- **THEN** the runtime MUST validate and fingerprint a complete `context-envelope/v1` before starting the invocation.

#### Scenario: Envelope size is calculated
- **WHEN** the adapter builds a context envelope
- **THEN** it MUST freeze the selected payload and provenance, render the initial provider input without budget or fingerprint fields, calculate its budget and prepared-input fingerprint once, and calculate the completed envelope fingerprint excluding only itself.

#### Scenario: Stage strategies differ
- **WHEN** two stages require different subjects, evidence, partitioning, ordering, or coverage rules
- **THEN** each stage MUST retain its versioned deterministic selection policy while producing the same envelope shape.

#### Scenario: An included context item has no provenance
- **WHEN** a subject, fact, evidence reference, or related subject lacks a stable item key, applicable file or registry origin, source or registry binding, or selection reason
- **THEN** the runtime MUST fail before provider invocation and publish no partial stage result.

#### Scenario: A selected file origin is invalid or stale
- **WHEN** an envelope file origin is absolute, escapes the repository, is absent from the context manifest, or no longer matches its recorded fingerprint
- **THEN** the runtime MUST reject the envelope without returning the referenced content; this selection check MUST NOT be represented as narrowing the repository-level read-only sandbox.

### Requirement: Budget every agent context before invocation
The generated workflow SHALL prove the complete initial rendered input and bounded response schema of every provider-bound invocation against that invocation role's effective profile context-window limit using a versioned estimator, a fixed 4096-token structured-response reserve, and non-negative estimated byte headroom, and SHALL identify these values as prepared-context estimates rather than provider telemetry.

#### Scenario: Context fits
- **WHEN** the base instruction, operation framing, instruction-version framing, supplement, canonical selected payload, fixed adapter framing, and canonical response schema fit within `2 × (context_window_tokens - 4096)` UTF-8 bytes under `utf8-v1`
- **THEN** the envelope MUST record context-window tokens, reserved-output tokens, estimated-input bytes, byte headroom, estimator identifier, and estimator version.

#### Scenario: Response schema is unbounded
- **WHEN** any structured-response string, array, or nested collection lacks a finite maximum or its maximum canonical JSON exceeds the 8192-byte `utf8-v1` response reserve
- **THEN** the runtime MUST fail preflight before slot allocation or provider invocation.

#### Scenario: Stage policy excludes candidates
- **WHEN** a versioned stage policy excludes candidates before rendering the complete selected input
- **THEN** diagnostics MUST record bounded excluded counts and reasons and MUST distinguish policy exclusion from budget truncation.

#### Scenario: Complete selected context does not fit
- **WHEN** the complete stage-selected input exceeds its invocation role's estimated byte allowance
- **THEN** the stage MUST perform no budget-driven truncation, fail closed with a typed context-capacity blocker before slot or provider invocation, and publish no partial result.

#### Scenario: Capacity cannot be validated
- **WHEN** the effective role profile lacks either a validated context-window limit or capability fingerprint, its limit is not greater than 4096 tokens, or the configured estimator identifier or version is unsupported
- **THEN** the stage MUST fail preflight and identify the missing capability.

#### Scenario: Consolidation roles have different capacities
- **WHEN** grouper and coordinator profiles expose different context-window limits
- **THEN** every partition and pair call MUST be budgeted with the grouper profile and final reduction MUST be budgeted with the coordinator profile.

#### Scenario: Complete consolidation output exceeds one response reserve
- **WHEN** candidate proposals or final MRQ membership cannot be represented within one bounded coordinator response
- **THEN** stage 3 MUST use deterministic bounded candidate-page comparisons, server-owned membership union, binary descriptive reductions, and paged noise review rather than increasing the 4096-token reserve, truncating output, or returning the complete graph in one call.

#### Scenario: Provider tools add transcript context
- **WHEN** a provider-managed tool read or transcript compaction occurs after the initial request
- **THEN** the workflow MUST NOT report that provider-managed context as measured preflight usage or monetary cost and MUST retain the prepared-input estimate as such.

### Requirement: Bind result reuse to context construction
The generated workflow SHALL reuse an agent result only when its execution bindings, context-envelope version and fingerprint, estimator version, selection-policy version, selected-path-manifest fingerprint, provider capability fingerprint, profile, instruction, and result schema match the active work.

#### Scenario: All reuse bindings match
- **WHEN** a prior validated result has exactly equal active reuse bindings
- **THEN** the workflow MAY reuse it and MUST record `execution_kind=reused`, its source result reference, compatibility fingerprint, and bounded diagnostics in existing phase-work state without creating an invocation.

#### Scenario: A reuse binding differs
- **WHEN** any required reuse binding differs or is unavailable
- **THEN** the workflow MUST reject reuse, report the first incompatible field, and schedule ordinary recomputation without rebinding the prior result.

#### Scenario: A legacy invocation lacks envelope metadata
- **WHEN** an immutable prior invocation predates `context-envelope/v1`
- **THEN** the workflow MUST keep it readable as legacy evidence and MUST NOT fabricate, rewrite, or infer missing envelope metadata.

#### Scenario: An adapted stage encounters a legacy cached result
- **WHEN** a stage using `context-envelope/v1` encounters a cached result without the complete new fingerprint set
- **THEN** it MUST reject reuse and recompute rather than applying the prior compatibility rules.

#### Scenario: Upgrade finds active snapshot-version-1 agent work
- **WHEN** the new runtime finds a running or resumable agent job bound to execution-snapshot schema version 1
- **THEN** one existing fenced recovery transaction MUST terminalize its invocation and phase work as interrupted, preserve completed evidence, and require an explicit new run using execution-snapshot schema version 2 without reusing legacy node results.

### Requirement: Preserve non-agent and stage boundaries
The generated workflow SHALL emit comparable provenance diagnostics for deterministic context-consuming decisions without fabricating agent invocations and SHALL NOT use the common envelope contract to merge workflow stages or create a new canonical context entity.

#### Scenario: Whole-component classification is deterministic
- **WHEN** an included wholly added or deleted component receives deterministic meaningful classifications
- **THEN** the workflow MUST record the algorithm version, input bindings, item origins, and decision fingerprint with `execution_kind=deterministic` and MUST NOT create an agent invocation or consume an agent budget.

#### Scenario: Budget preflight fails before invocation
- **WHEN** the complete selected input fails context-budget validation
- **THEN** the workflow MUST store the typed blocker and safe budget diagnostic in existing phase-work or checkpoint state and MUST NOT create an invocation.

#### Scenario: Context compilation is unified
- **WHEN** all active stage compilers use the shared envelope builder
- **THEN** the runtime MUST remove the unused combined discovery compiler while preserving historical readers and immutable evidence.

#### Scenario: Envelope validation succeeds
- **WHEN** a valid envelope is built for an existing stage
- **THEN** the workflow MUST preserve that stage's explicit start, readiness gate, stable DIF and MRQ identities, publication boundary, and approval behavior.

### Requirement: Consolidate MRQ hierarchically within bounded responses
The generated workflow SHALL replace the monolithic coordinator response with a deterministic hierarchy that preserves complete global candidate, conflict, noise, and DIF-ownership coverage while every agent response remains within the fixed structured-response reserve.

#### Scenario: Candidate leaves are ready
- **WHEN** all partition and pair grouper calls return validated proposals
- **THEN** the runtime MUST assign content-derived candidate IDs, retain complete membership server-side, create deterministic bounded descriptor pages, and schedule every unordered page pair exactly once.

#### Scenario: Coordinator compares candidate pages
- **WHEN** a page-pair comparison runs
- **THEN** its bounded response MUST reference only candidate IDs from those pages, classify every eligible cross-page relationship as merge or keep separate, and contain no repeated full DIF membership.

#### Scenario: Page comparisons complete
- **WHEN** every planned page-pair result validates
- **THEN** the runtime MUST reject merge/keep contradictions, apply merge links through deterministic union-find, and prove exact page-pair coverage before descriptive reduction.

#### Scenario: One linked component needs final description
- **WHEN** a union component contains more than one candidate proposal
- **THEN** the coordinator MUST reduce its ordered child aggregates through a deterministic binary tree whose calls name exactly two child IDs and whose server-owned aggregate carries the union of their DIF membership.

#### Scenario: Noise candidates require approval
- **WHEN** active classifications contain noise candidates
- **THEN** the coordinator MUST review them in deterministic bounded pages and the runtime MUST prove each noise candidate has exactly one approved or rejected result before plan construction.

#### Scenario: Hierarchy is incomplete or stale
- **WHEN** a page, pair, child aggregate, noise decision, binding, or DIF owner is missing, duplicated, unknown, contradictory, oversized, or stale
- **THEN** stage 3 MUST fail before approval or canonical publication and MUST expose the exact incomplete hierarchy count and typed blocker.

#### Scenario: Hierarchy completes
- **WHEN** link, reduction, noise, and ownership closure all validate
- **THEN** the runtime MUST assemble the existing ordinary consolidation plan from server-owned memberships and reduced descriptions without creating a canonical page, candidate, or aggregate entity.

### Requirement: Retain agent context provenance operationally
The generated workflow SHALL store a content-free provenance descriptor for every included context item, the envelope fingerprint, and a bounded diagnostic summary in owner-only operational state, SHALL atomically bind them to invocation start, and SHALL keep canonical source and evidence content in their existing stores.

#### Scenario: Invocation starts
- **WHEN** budget and context validation succeed and the dispatcher allocates an invocation
- **THEN** one SQLite transaction MUST store the invocation row, envelope and prepared-input fingerprints, complete content-free provenance ledger, bounded diagnostic summary, phase-work assignment, and `invocation.started` outbox transition.

#### Scenario: Provenance is recorded
- **WHEN** a subject, fact, evidence reference, or related subject is included according to the item granularity declared by its versioned stage policy
- **THEN** its descriptor MUST contain stable item key, closed kind and selection-reason codes, applicable generation or registry fingerprint, and either a repository-relative fingerprinted file origin or a stable registry identifier without storing the item's value, free-form rationale, label, or source content.

#### Scenario: Process stops after invocation start
- **WHEN** the service stops after the invocation transaction commits but before provider completion
- **THEN** restart reconciliation MUST retain the exact context identity and provenance while terminalizing or recovering the invocation through existing lifecycle rules.

#### Scenario: Operational schema is upgraded
- **WHEN** the service opens a database without context-envelope diagnostic columns
- **THEN** it MUST create the owner-only online backup `.pre-context-envelope.sqlite` without reusing `.pre-inspector.sqlite`, add nullable columns with explicit insert target lists, preserve legacy rows, and make repeated migration idempotent.

#### Scenario: Backend rollback is required
- **WHEN** an operator restores a backend that predates the additive context columns
- **THEN** mutations MUST stop and the documented recovery MUST restore `.pre-context-envelope.sqlite` while preserving repository generations and warning that post-upgrade operational history is lost.
