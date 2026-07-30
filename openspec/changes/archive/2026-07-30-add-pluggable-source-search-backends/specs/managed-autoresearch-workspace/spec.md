## ADDED Requirements

### Requirement: Configure and diagnose approved source-search backends
The managed workspace SHALL expose the fixed approved indexing backends, their capabilities, component readiness, deterministic routes, and bounded failures without accepting arbitrary executables or exposing index content.

#### Scenario: A user opens index diagnostics
- **WHEN** one or more backends are configured
- **THEN** the workspace MUST show the user-scope operational storage root, backend and adapter IDs, engine and adapter versions, capability summary, component, representation, source-generation binding, index fingerprint, status, last validation, and route priority.

#### Scenario: A user checks host tools before running the workflow
- **WHEN** the common live tool inventory is opened
- **THEN** it MUST list every fixed indexing adapter with detected installation, version, path, and route-derived requirement, MUST block only the indexing stage when a required adapter is unavailable or incompatible, and MUST NOT block source acquisition for an unselected indexer.

#### Scenario: A backend fallback occurred
- **WHEN** a query used a lower-priority backend
- **THEN** context diagnostics MUST show the required capability, preferred and selected backend identities, and typed fallback cause without returning the query text or source content.

#### Scenario: An index requires rebuilding
- **WHEN** its component, generation, engine, adapter, capability, or content identity is stale
- **THEN** the workspace MUST identify the mismatched class and offer the existing confirmed background rebuild action with visible server progress.

#### Scenario: A backend fails
- **WHEN** probe, build, validation, query, normalization, or cancellation fails
- **THEN** the workspace MUST show a redacted bounded error code and recovery action and MUST NOT render raw process output, credentials, database paths, or unrestricted snippets.

#### Scenario: Backend configuration is edited
- **WHEN** a user changes enabled backends or capability order
- **THEN** the workspace MUST preview the normalized tracked file, affected builds, degraded routes and reuse bindings and apply it only through expected file and plan fingerprints, idempotency, mutation locking, and atomic replacement.

### Requirement: Diagnose bounded worker source search
The managed workspace SHALL expose safe per-invocation `source_search` policy, usage, routing, exhaustion, and provenance diagnostics without returning query text, source excerpts, raw tool traffic, or unrestricted runtime output.

#### Scenario: A worker has search access
- **WHEN** an invocation role permits `source_search`
- **THEN** dispatcher inspection MUST show permitted operation classes, scope counts and fingerprint, configured call, concurrency, time, result, query-byte, and returned-byte limits, and tool-policy version.

#### Scenario: Source-search policy is configured
- **WHEN** a user edits an agent profile
- **THEN** the profile editor MUST validate the optional operation set and every per-call and aggregate limit against server maxima, show worst-case context reserve and headroom, and require a new invocation for changes.

#### Scenario: A worker performs searches
- **WHEN** one or more calls are accepted
- **THEN** inspection MUST show reserved and in-flight calls, admission state, capacity reserve, actual call, backend-time, result and canonical returned-byte counts, status counts, selected backend and fallback summaries, ledger completeness and reconciliation, and the dynamic-ledger fingerprint.

#### Scenario: A search limit is exhausted
- **WHEN** a tool call reaches or exceeds a frozen limit
- **THEN** the current invocation and stage diagnostics MUST show the exact exhausted limit class, configured value, consumed value, and bounded recovery guidance without exposing the query or results.

#### Scenario: Dynamic-search entries are inspected
- **WHEN** their count exceeds the bounded summary
- **THEN** the existing dispatcher detail resource MUST paginate content-free entries with opaque cursors bound to project, invocation, policy and ledger fingerprints and MUST NOT add a raw transcript endpoint.

#### Scenario: Search policy or key version changes
- **WHEN** a profile limit, route, source binding, or project-scoped query-HMAC key version changes
- **THEN** diagnostics MUST explain that only a new invocation can use the new policy and MUST mark incompatible prior reuse without exposing raw or unsalted query identity.
