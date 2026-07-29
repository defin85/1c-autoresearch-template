## 1. Define The Shared Contract

- [x] 1.1 Add `context-envelope/v1` schemas for bindings, selected payload, subject, facts, evidence, related subjects, selection, budget, selected paths, and provenance with stage-declared item granularity.
- [x] 1.2 Freeze the provider projection, render it once without derived budget or fingerprint fields, then canonicalize and fingerprint the prepared input and completed envelope without a size cycle.
- [x] 1.3 Validate repository-relative selected file origins and fingerprints against the existing context manifest without treating selection as filesystem sandboxing.
- [x] 1.4 Add a content-free provenance ledger for every included item and bounded safe summaries without storing prompts, values, response-schema bodies, private reasoning, credentials, unrestricted output, or arbitrary file content.

## 2. Enforce Budget And Reuse

- [x] 2.1 Render base instructions, framing, supplement, canonical selected payload, adapter overhead, and response schema, then apply `utf8-v1`, the 4096-token structured-response reserve, and each invocation role's validated provider context-window limit before slot allocation.
- [x] 2.2 Fail closed on unknown or too-small capacity, unknown estimator, negative byte headroom, untraceable provenance, stale selected paths, or any budget-driven truncation.
- [x] 2.3 Bind cached-result reuse to envelope, estimator, selection-policy, selected-path-manifest, provider-capability, execution, profile, instruction, and result-schema fingerprints.
- [x] 2.4 Report the first incompatible reuse field without silently rebinding prior results.
- [x] 2.5 Add finite response-schema string and collection maxima whose maximum canonical JSON fits the 8192-byte `utf8-v1` reserve.

## 3. Adapt Stage Policies

- [x] 3.1 Wrap DIF-analysis context in the shared envelope while preserving per-DIF, component, dependency, and evidence selection.
- [x] 3.2 Wrap MRQ-consolidation partitions, pairs, proposals, and reduction context without weakening complete coverage, budgeting groupers and coordinator against their own effective profiles.
- [x] 3.2a Replace the monolithic coordinator response with content-derived candidate IDs, deterministic bounded page-pair comparisons, contradiction checks, server-owned union-find membership, binary descriptive reductions, and paged exact noise closure.
- [x] 3.3 Wrap MRQ-classification windows and target-research work units while preserving stable ordering and linkage evidence.
- [x] 3.4 Emit comparable provenance diagnostics for deterministic whole-component classification without creating an invocation.
- [x] 3.5 Remove the unused combined discovery compiler after all active stages use the common builder.

## 4. Expose Diagnostics

- [x] 4.1 Add nullable invocation columns and atomically persist prepared-input and envelope fingerprints, complete content-free provenance, bounded budget and selection summary, phase-work assignment, and start outbox transition.
- [x] 4.2 Extend the existing project-scoped dispatcher details with paged context provenance, bounded diagnostics, and explicit unavailable markers for legacy invocations.
- [x] 4.3 Show prepared-input estimate, headroom, origin groups, policy exclusions, versions, execution-kind counts, and incompatibility causes in the existing contextual inspector.
- [x] 4.4 Preserve pagination, redaction, plain-text rendering, stale-selection behavior, and project confinement.
- [x] 4.5 Store preflight failures, deterministic decisions, and compatible reuse diagnostics in existing phase-work or checkpoint state without fake invocations or slots.

## 5. Verify And Synchronize

- [x] 5.1 Add contract, canonicalization, complete-input rendering, provenance, per-role capacity, no-truncation, and reuse-compatibility tests.
- [x] 5.2 Add integration coverage for every agent stage and the deterministic whole-component path.
- [x] 5.3 Add API, frontend, and browser checks for bounded safe diagnostics and legacy unavailable markers.
- [x] 5.4 Implement and verify target-first, then synchronize only reusable behavior into the template runtime and scaffold.
- [x] 5.5 Run Python, frontend, browser, synchronization, template, doctor, and strict OpenSpec checks.
- [x] 5.6 Verify owner-only idempotent SQLite migration, restart after invocation-start commit, frontend-only rollback, and backend rollback through the existing pre-migration backup procedure.
- [x] 5.7 Upgrade new runs to execution-snapshot schema version 2 and verify fenced interruption plus explicit restart of active or resumable schema-version-1 agent work without legacy result reuse.

## Разрывы ревью

- [x] R.1 Remove the production-dead combined discovery compiler and its obsolete tests.
- [x] R.2 Persist and expose the first incompatible result-reuse binding instead of reporting only a cache miss.
- [x] R.3 Add direct graph coverage for hierarchical page comparison, union, binary reduction, exact noise closure, and incomplete coverage.
- [x] R.4 Verify explicit fenced restart of active or resumable execution-snapshot schema-version-1 work with legacy result reuse disabled.
