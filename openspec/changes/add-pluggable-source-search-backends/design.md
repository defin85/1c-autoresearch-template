## Context

`indexes.py` currently owns component discovery and canonical source navigation correctly, but also embeds one executable and one command protocol. The source contract already provides immutable component IDs, generation IDs, component fingerprints, representations, and canonical path confinement. These existing identities are sufficient for several rebuildable indexes over the same component.

The agent-context contract requires stable provenance for included files. Search therefore feeds deterministic selection but never replaces direct source verification.

## Locked Decisions

1. Index lifecycle and source querying are separate capabilities even when one backend implements both.
2. Only fixed server-owned backend adapters are runnable; project configuration names approved adapter IDs and contains no executable path, shell template, credential, or arbitrary argument list.
3. The initial adapters are `rlm-tools-bsl` and `bsl-analyzer`. The latter means only <https://github.com/itrous/bsl-analyzer>, discovered through its `bsl-analyzer` launcher, probed through `bsl-analyzer contract`, requiring contract major 1, and queried through the `workspace` MCP profile over direct stdio with a manifest-equivalent private operational mirror passed as `--source-dir`.
4. Routing is deterministic by required capability and configured backend order.
5. Fallback occurs only when the preferred backend is unavailable, lacks the capability, or has no ready compatible index; an empty successful query is not a fallback trigger.
6. Results from different backends are not merged, re-ranked, or compared by numeric score in the first delivery.
7. A search hit is navigation evidence only. Before use, the coordinator resolves its component and relative path in the active immutable source generation, reads the canonical file, verifies confinement, and records its own fingerprint.
8. Backend-native symbol IDs, scores, database keys, and snippets never become stable DIF, MRQ, evidence, or context identities.
9. Indexes remain user-scope rebuildable operational state and never enter the repository, source generation, approval lifecycle, or canonical publication.
10. Backend adapter version, engine version, capability fingerprint, index fingerprint, component fingerprint, and source-generation binding participate in routing diagnostics and result-reuse compatibility where search influenced selection.
11. A backend may return only bounded text and metadata. Raw index databases, unrestricted source snippets, credentials, and arbitrary files are not exposed through the workspace.
12. Existing `rlm-tools-bsl` projects migrate without rebuilding solely because the adapter contract was introduced when their complete identity still matches, including the repository-instance fingerprint already present in the current index key.
13. Every agent runtime receives the same provider-neutral `source_search` tool when its role permits source research; workers decide whether, when, and which supported operation to call but cannot name or discover a backend.
14. The coordinator fixes the searchable components, path scope, operation vocabulary, maximum calls, total elapsed time, per-call results, returned bytes, and concurrency before invocation.
15. Every tool result is resolved from canonical source and recorded in a content-free dynamic-search ledger; backend-native unverified snippets never enter the agent transcript.
16. Dynamic tool reads do not alter or masquerade as the prepared initial context budget. Their separate limits, actual bounded counts, and result bytes are recorded as workflow-managed tool telemetry.
17. Source-search limits are configured in owner-only user-scope agent profiles, constrained by server maxima, resolved per role, and frozen in the immutable execution snapshot; absence of a search policy disables the tool for that profile.
18. Capacity admission reserves the worst-case dynamic query, response, and tool-framing bytes under the same estimator before dispatch; this reserve is capacity headroom, not prepared-input usage.
19. Concurrent tool calls atomically reserve all applicable budgets before backend execution and terminalize one ordered ledger entry exactly once.
20. Index builds use fenced leases, private staging, exact manifest validation, and atomic promotion; only a promoted immutable instance can serve queries.

## Backend Contracts

The fixed backend catalog exposes two related contracts:

- index lifecycle: `probe`, `capabilities`, `build`, `validate`, `cancel`, and `identity`;
- source query: `search_text`, `find_symbol`, `find_references`, `find_callers`, and `resolve_hit` for only the capabilities the adapter declares.

Capabilities use a closed versioned vocabulary such as `text-search`, `symbol-definition`, `symbol-references`, `callers`, `callees`, and `metadata-navigation`. An adapter capability fingerprint covers its executable identity, adapter protocol version, engine version, supported source representations, query features, limits, and normalization schema.

An index target identity, known before a build starts, contains:

- backend and adapter IDs and versions;
- engine version;
- project and component IDs;
- an opaque `repository_instance_fingerprint` derived server-side from project ID and canonical repository root so two clones with the same project ID cannot collide without exposing the absolute path;
- active source-generation ID;
- component path, representation, and content fingerprint;
- capability fingerprint.

A promoted index instance identity adds the deterministic validated index-manifest fingerprint to the target fingerprint. Build leases, staging, and the current-instance pointer are keyed by target identity; immutable ready instances, routing, diagnostics, and reuse are keyed by promoted instance identity.

The existing component discovery remains common and authoritative. Backends do not independently discover repository scope. The repository-instance fingerprint participates in the state key, routing identity, diagnostics, and reuse compatibility.

## Configuration And Routing

`research/indexing.toml` advances to this exact minimal schema:

```toml
schema_version = "2"

[[backends]]
adapter_id = "rlm-tools-bsl"
engine_version = "1.30.0"

[[backends]]
adapter_id = "bsl-analyzer"
engine_version = "0.2.63"

[routes]
text-search = ["bsl-analyzer", "rlm-tools-bsl"]
symbol-definition = ["bsl-analyzer", "rlm-tools-bsl"]
symbol-references = ["bsl-analyzer", "rlm-tools-bsl"]
callers = ["bsl-analyzer", "rlm-tools-bsl"]
callees = ["bsl-analyzer", "rlm-tools-bsl"]
metadata-navigation = ["bsl-analyzer", "rlm-tools-bsl"]
```

Presence in `backends` means enabled; there is no second enabled flag or component-selection policy in version 2, and every enabled backend targets every compatible discovered component. `adapter_id` is from the fixed catalog and unique. `engine_version` is an exact non-empty adapter-validated version. Every route key is from the closed capability vocabulary, every route is a non-empty duplicate-free ordered subset of declared backend IDs, and unknown fields, missing routes required by an enabled workflow role, duplicate IDs, duplicate route members, or implicit defaults fail validation. A capability not required by any enabled role may be absent and yields `source_search.capability_unconfigured` if later requested.

Schema version 1 `{engine = "rlm-tools-bsl", engine_version = V}` is read as one backend plus explicit routes for the capabilities declared by that exact probed adapter contract. The tracked file changes only through the typed reviewed mutation below; reading never silently rewrites it.

The runtime resolves one backend per query:

1. derive the required capability from the coordinator-owned operation;
2. read the configured ordered route for that capability;
3. discard adapters that fail their bounded probe, do not declare the capability, or lack a ready index matching the active component identity;
4. select the first remaining backend and record the routing decision;
5. fail with a typed blocker when none remains.

Configuration cannot request “all backends,” score fusion, backend-specific query syntax, or an executable. A future combined-search policy requires a new contract version.

The `index-sources` gate is ready when every capability required by enabled role profiles has at least one compatible ready backend for every searchable component. A failed lower-priority backend is a degraded diagnostic rather than a blocker when a later route member is ready. An enabled backend with no route may remain unbuilt; configured route membership determines required build coverage.

Index configuration is changed only through a typed server-owned preview/apply operation. Preview returns the normalized exact file, current file fingerprint, plan fingerprint, affected backends, capabilities, rebuilds, degraded routes, and reuse invalidations. Apply takes the expected file and plan fingerprints plus a project-scoped idempotency key, revalidates under the project mutation lock, atomically replaces only `research/indexing.toml`, and starts no build implicitly. Stale or invalid input publishes nothing.

## Result Normalization And Evidence Promotion

A normalized backend hit contains a backend ID, adapter version, index fingerprint, component ID, source-generation ID, `component_relative_path`, optional line and symbol label, closed hit kind, and an opaque backend rank. A backend cannot supply an absolute path, generation-root path, or ordinary evidence path. Snippet text is omitted by default and, where required for operator diagnosis, is length-bounded, redacted, and never persisted as evidence.

When a stage selector wants to use a hit, it calls the existing common canonical resolver. The resolver rejects stale generation, component, index, path, or capability bindings; confines `component_relative_path` to the component root; reads the actual file; computes its fingerprint; and emits the existing ordinary logical evidence path (`configuration/...`, `extensions/<id>/...`, or `external/<id>/source/...`) with component ID, source-generation ID, and file fingerprint. The descriptor may record the search backend and routing decision as selection provenance, but its evidence identity is that logical path and canonical file fingerprint.

## Fixed BSL Analyzer Adapter

The `bsl-analyzer` adapter owns only the installed `itrous/bsl-analyzer` product. It discovers the launcher from `PATH`, fingerprints it, runs `bsl-analyzer contract`, requires contract major `1`, verifies the exact configured app `build_version`, and feature-detects the `workspace` MCP tools and actions. It never scrapes `--help`, invokes the launcher's update commands, downloads a release, enables embeddings, configures PostgreSQL/Vault, installs MCP globally, or reads user credentials.

BSL Analyzer stores derived state under `<source-dir>/.build` and declares no separate cache root. The adapter therefore copies or reflinks the canonical component into an owner-only staging mirror, verifies that its byte manifest equals the canonical component fingerprint, and starts direct stdio as `bsl-analyzer mcp serve --profile workspace --mode stdio --source-dir <staging-mirror>` through a fixed argument builder and sanitized private environment. It never starts BSL Analyzer against the canonical source root and never uses a symlink as the mirror. The validated mirror and its `.build` directory are promoted together as one immutable index instance.

The adapter uses the declared `search`, `graph`, and `symbol_info` actions only when present in the machine contract, verifies their structured schema versions, and maps them to the common closed capabilities. Lexical `search_code` is sufficient for `text-search`; semantic search, network services, credentials, and inherited embedding or database configuration are disabled in the sanitized environment. `search(status)`, `graph(status)`, and the resident status surfaces provide readiness and revision evidence. Backend graph IDs and native snippets remain navigation-only.

## Worker Search Tool

The coordinator still supplies the deterministic minimum initial context, but every source-research role MAY receive one common `source_search` tool. The tool schema has a closed operation enum, structured query fields, optional component and path narrowing within the invocation scope, and no backend field. Agent-runtime adapters only transport the common tool request and response; they do not implement backend routing.

The selected user-scope agent profile optionally contains a closed `source_search` object. It names permitted operations and positive integer limits for calls, concurrent in-flight calls, per-call deadline seconds, aggregate backend execution seconds (sum of terminalized call durations), per-call and aggregate query bytes, per-call and aggregate canonical result count, and per-call and aggregate canonical returned bytes. All fields are required when the object is present; operations are a non-empty duplicate-free subset of the closed vocabulary. Unknown fields fail profile validation. Server maxima are respectively 64 calls, 4 concurrent calls, 60 seconds per call, 600 aggregate seconds, 4096/65536 query bytes, 100/1000 results, and 131072/2097152 returned bytes; profile values may only narrow them. No object means disabled.

Before invocation the coordinator freezes a `source-search-policy/v1` containing:

- permitted search operations for the role;
- active source-generation and component bindings;
- allowed component IDs and existing logical evidence-path prefixes;
- the exact profile-derived per-call and aggregate limits above;
- routing-policy and tool-schema versions.

Before slot allocation, capacity admission uses the invocation model's exact context limit and estimator to prove:

`prepared input + fixed structured-response reserve + maximum aggregate query bytes + maximum aggregate canonical returned bytes + maximum canonical tool framing for max calls`

fits. The dynamic reserve is reported separately from prepared-input use. A profile whose maximum tool transcript cannot fit fails preflight; the runtime never silently lowers its configured search limits.

The effective operation set is the intersection of profile operations and the fixed role allowlist. The coordinator derives component IDs and logical evidence-path prefixes from the same active stage work unit that owns the invocation; the profile and worker may only narrow that scope. An empty derived scope exposes no tool.

On each call the service validates policy and remaining budget, derives the required capability, selects one backend deterministically, executes the bounded query, and canonically resolves every returned hit. The response contains only the existing ordinary logical evidence path plus component, source-generation, and file fingerprints, line and symbol navigation, closed hit kind, and a bounded canonical excerpt when the operation requires content. It contains no raw backend snippet, backend-native query language, database key, or unrestricted file body.

The worker decides the sequence of permitted searches. It cannot broaden scope, select a backend, rebuild an index, change routing, search an unbound generation, or use a successful empty result to force fallback. Budget exhaustion returns a typed tool result and further calls fail without terminating already valid prior reads; the final proposal remains subject to ordinary evidence and result validation.

The coordinator exposes `source_search` through one fixed per-invocation MCP bridge registered by the agent-runtime adapter through private configuration rather than prompt text. The bridge receives a random 256-bit invocation capability outside the model-visible input. The coordinator stores only its verifier and binds it to project, invocation, policy fingerprint, expiry, and terminal state. Every request carries a unique call ID; cross-project, cross-invocation, expired, replayed, cancelled, or terminal requests fail before budget reservation. Capabilities, endpoints, and private adapter configuration never enter prompts, events, results, logs, or diagnostics.

One SQLite transaction assigns the monotonic request ordinal and reserves call count, in-flight slot, aggregate query bytes, maximum requested result count and returned bytes, and `min(per-call deadline, currently unreserved aggregate seconds)` before a backend starts. That reserved time is the call's hard backend deadline. A rejected reservation consumes no backend budget. Settlement permanently charges actual backend duration, releases unused reserved seconds and in-flight capacity, and terminalizes that ordinal exactly once; simultaneous calls cannot oversubscribe. Aggregate duration is the sum of backend wall-clock durations, not invocation elapsed time.

Every accepted request creates a content-free ledger entry with invocation ID, ordinal, a project-scoped HMAC of the normalized query using an owner-only versioned operational key, required capability, route and index fingerprints, scope fingerprint, result manifest fingerprint, canonical result count and bytes, duration, and terminal status. Raw queries and unsalted query hashes are never stored. Key rotation makes old query correlation unavailable and rejects reuse dependent on the old key version without invalidating canonical research evidence. Ledger access is project-and-invocation scoped, owner-only, paginated, and retained under the existing invocation operational-state lifecycle.

Final evidence named by the worker is revalidated once more during proposal validation. A prior validated result may be reused without replaying its query only if the policy schema and scope, source/component/file fingerprints, current deterministic route resolution, adapter/capability/index identities, and every returned canonical manifest remain current. The prior ledger fingerprint is an audit identity, not a value compared with a nonexistent new ledger; ordinal, duration, counters, and timestamps do not participate in compatibility.

Prepared-context accounting remains the exact initial input plus response reserve defined by `context-envelope/v1`. Workflow-managed `source_search` usage is diagnosed separately with configured limits and actual calls, time, results, and returned canonical bytes; it is not reported as prepared tokens, provider-managed telemetry, or monetary cost.

Cancellation, invocation timeout, lease loss, or restart reconciliation atomically closes admission, cancels every in-flight backend process group, terminalizes every reserved ordinal exactly once as `cancelled`, `timed_out`, or `interrupted`, discards late output, rejects the final proposal, and never replays a search automatically. A crash before start, during output, or after backend completion but before settlement is reconciled from the reserved ledger row and process identity without publishing an uncommitted result.

## State, Diagnostics, And Recovery

Operational state keys build coordination by index target identity rather than by component alone. Each component can therefore have several independently ready promoted instances. Build and validation state records bounded timestamps, status, error code, safe summary, and last successful validation.

Each target identity has one fenced build lease and current-instance pointer. A build writes only to an owner-only private staging directory, validates the exact expected source, index, and capability manifests, derives the promoted instance identity, then atomically promotes the immutable instance and switches the target pointer while still holding the lease. Queries open only the pointed promoted instance. Cancellation, crash, validation failure, generation change, configuration change, or lost lease prevents promotion and deletes or quarantines staging. A compatible old ready instance remains usable until replacement promotion. Simultaneous rebuild, validate-versus-promote, and route mutation cannot publish partial or foreign state.

The workspace extends the existing index area with backend, capability, source-generation, component, engine version, index fingerprint, readiness, route priority, validation time, and fallback cause. It never returns raw command output or index content.

Worker-search diagnostics additionally show reserved and in-flight calls, admission closed state, capacity reserve, actual dynamic bytes, terminal ledger completeness, and reconciliation state. Recovery guidance never mutates the current immutable invocation: changing a profile or limit always requires a new invocation. Ledger cursors bind to project, invocation, policy fingerprint, and ledger fingerprint.

An interrupted build remains failed or stale and can be rebuilt. Switching routing does not invalidate canonical generations; only contexts and cached results whose deterministic selection provenance includes the prior route become incompatible.

## Audit Matrix

| Concern | Required evidence |
| --- | --- |
| Contract | Every runnable backend comes from the fixed catalog and declares a versioned capability fingerprint |
| Scope | Common component discovery, not the backend, determines index membership |
| Freshness | Every ready index matches active source generation, component fingerprint, representation, engine, and adapter identity |
| Evidence | Every promoted hit is re-read and fingerprinted from canonical source |
| Determinism | Capability and configured order select exactly one backend |
| Fallback | Only unavailability, unsupported capability, or incompatible index permits fallback |
| Security | No arbitrary commands, credentials, raw databases, unrestricted snippets, or external paths are exposed |
| Compatibility | Existing matching `rlm-tools-bsl` state remains usable |
| Operability | Component/backend readiness and exact routing cause are visible |
| Authority | Index and search identities create no canonical research entity |
| Performance | Probes, result counts, output bytes, and query time are bounded |
| Context capacity | Worst-case profile search transcript is reserved before slot allocation |
| Reuse | Search-influenced context binds backend route, capability, and index fingerprints |
| Worker autonomy | Worker chooses permitted query timing and operation while backend and scope remain coordinator-owned |
| Tool bounds | Calls, concurrency, query bytes, elapsed time, results, and canonical returned bytes are enforced per invocation |
| Dynamic provenance | Every accepted tool request and canonical result manifest is fingerprinted without storing query or source content |
| Concurrency | Atomic reservation and exactly-once settlement prevent oversubscription |
| Recovery | Cancellation and restart close admission, stop processes, reconcile ledger rows, and never replay |
| Confidentiality | Query identity uses project-scoped HMAC rather than a reversible unsalted hash |
| Verification | Adapter contract, routing, fallback, stale state, promotion, UI, scaffold, and strict checks are covered |

## Execution Plan

1. Extract current component discovery and canonical navigation from the embedded `rlm-tools-bsl` command adapter without changing their behavior.
2. Introduce the fixed backend catalog, closed capabilities, target and promoted-instance identities, exact schema-version-2 configuration, typed preview/apply, and migration.
3. Implement the current `rlm-tools-bsl` behavior as the first adapter and prove existing-state compatibility.
4. Add the fixed `itrous/bsl-analyzer` contract-1.x workspace-MCP adapter with bounded probe, build, validation, cancellation, and supported queries.
5. Add deterministic capability routing, typed fallback, normalized hits, and canonical evidence promotion.
6. Add profile-owned source-search policies, worst-case capacity admission, the authenticated common tool bridge, atomic budgets, dynamic HMAC ledger, cancellation/recovery, and final evidence revalidation.
7. Bind initial and dynamic search provenance into context diagnostics and compatible-result reuse.
8. Add workspace diagnostics, target-first verification, scaffold synchronization, packaging, and strict checks.

## Risks / Trade-offs

- [Backends disagree] → Select one backend deterministically and expose its identity; do not synthesize consensus.
- [A backend score is mistaken for confidence] → Keep rank opaque and prohibit it from canonical evidence or business confidence.
- [An index reports a stale path] → Re-resolve against the active component and fail before context construction.
- [Several indexes multiply disk use] → Build only configured backends and components; keep all index data rebuildable outside the repository.
- [Fallback changes selected context] → Record route and index fingerprints and reject incompatible reuse.
- [BSL Analyzer supports a different representation set] → Declare representation compatibility as a probed capability and block unsupported components.
- [A worker loops on searches] → Enforce frozen per-invocation call, concurrency, time, result, and byte limits and expose exhaustion explicitly.
- [Dynamic reads escape prepared-context budgeting] → Diagnose them as separately bounded workflow-managed tool usage and never present them as prepared-input tokens.
- [The maximum tool transcript overfills model context] → Reserve its estimator-derived worst case before dispatch and reject the profile without silently narrowing it.
- [Different agent runtimes transport tools differently] → Keep one common schema and conformance suite; adapters only translate transport events.
- [Query hashes reveal enumerable business terms] → Use a project-scoped keyed fingerprint and owner-only key lifecycle.

## Migration And Rollback

On first schema-version-1 read, the runtime derives the exact version-2 in-memory form described above with one pinned `rlm-tools-bsl` backend and its contract-declared routes. The tracked file changes only through typed preview/apply. Matching old state is adopted after bounded adapter validation and repository-instance verification; incompatible state is marked stale and rebuilt explicitly.

Downgrade uses a confirmed server-owned preview/apply before installing the prior runtime. It is allowed only when `rlm-tools-bsl` is the sole backend and every route and setting is exactly representable by version 1; it atomically writes exact v1 `{schema_version="1", engine="rlm-tools-bsl", engine_version=V}` under expected-file and plan fingerprints. Otherwise downgrade is blocked. Version-2 operational state is ignored by the old runtime, and compatible legacy state is retained until downgrade verification succeeds. Canonical source and research generations need no rollback.

## Assumptions And Open Questions

- Assumption: every agent runtime enabled when this change is delivered can register the fixed per-invocation MCP bridge; a later runtime cannot advertise `source_search` until it passes the same authentication, capacity, cancellation, and hostile-request conformance suite.
- Assumption: owner-only operational key rotation can reuse the existing user-state ownership boundary.
- Open questions: none.
