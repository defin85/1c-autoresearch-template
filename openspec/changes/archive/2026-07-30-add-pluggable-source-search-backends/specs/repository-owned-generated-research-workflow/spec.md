## ADDED Requirements

### Requirement: Use fixed capability-based source index backends
The generated workflow SHALL build and validate source indexes through a fixed server-owned backend catalog, SHALL route each query deterministically by required capability and configured backend order, and SHALL bind every index to its exact source component and generation identity.

#### Scenario: A component requires an index
- **WHEN** the workflow evaluates an indexable component from the active routed source generation
- **THEN** every configured backend MUST independently declare representation support, adapter and engine versions, capability fingerprint, opaque repository-instance fingerprint, component fingerprint, source-generation binding, and rebuildable index identity.

#### Scenario: Configuration names an arbitrary executable
- **WHEN** indexing configuration contains an executable path, shell template, arbitrary argument list, credential, unknown backend, or unknown capability
- **THEN** validation MUST fail before probing or starting a process.

#### Scenario: A query requires one capability
- **WHEN** a coordinator-owned operation requests text, symbol, reference, call, or metadata navigation
- **THEN** the runtime MUST select the first configured backend whose bounded probe, declared capabilities, representation support, and current compatible index satisfy the request.

#### Scenario: The preferred backend cannot serve the query
- **WHEN** the preferred backend is unavailable, lacks the required capability, or has no ready compatible index
- **THEN** the runtime MAY select the next configured backend and MUST record the exact fallback reason and both backend identities.

#### Scenario: The preferred backend returns no hits
- **WHEN** the selected backend completes a valid query with an empty result
- **THEN** the runtime MUST treat that as the query result and MUST NOT invoke another backend merely to obtain a non-empty answer.

#### Scenario: BSL Analyzer is configured
- **WHEN** the fixed `bsl-analyzer` adapter is enabled
- **THEN** it MUST mean the installed `itrous/bsl-analyzer` launcher, require machine contract major 1 and the exact configured build version, use direct-stdio workspace MCP only against a manifest-equivalent private operational mirror, keep canonical source unchanged and free of `.build`, and MUST NOT install, update, authenticate, enable embeddings, configure shared services, or mutate global tool state.

#### Scenario: Required route coverage is evaluated
- **WHEN** enabled profiles require source-search capabilities
- **THEN** the index gate MUST require at least one compatible ready route member for every required capability and searchable component while reporting failed lower-priority members as degraded rather than blocking when fallback is ready.

#### Scenario: Index configuration changes
- **WHEN** a typed preview is applied with matching file and plan fingerprints and idempotency key
- **THEN** the runtime MUST revalidate under the project mutation lock, atomically replace only normalized `research/indexing.toml`, report rebuild and reuse impacts, and MUST NOT start a build implicitly.

### Requirement: Promote search navigation into canonical evidence safely
The generated workflow SHALL treat backend search results only as bounded navigation evidence and SHALL re-resolve every selected hit against canonical source before using it in an agent context or published research result.

#### Scenario: A search hit is selected
- **WHEN** a stage selector chooses a normalized backend hit
- **THEN** the coordinator MUST verify backend and index fingerprints, active generation, component identity, repository confinement, actual file existence, and current file fingerprint before emitting ordinary context provenance.

#### Scenario: A backend returns a path
- **WHEN** it returns a normalized hit
- **THEN** the hit MUST contain only `component_relative_path`, and the coordinator MUST convert it into the existing logical configuration, extension, or external-artifact evidence path with component, generation, and canonical file fingerprints.

#### Scenario: A backend returns native identity or score
- **WHEN** a result contains a backend symbol ID, rank, score, database key, or snippet
- **THEN** those values MUST NOT become stable DIF, MRQ, evidence, or context identities or be represented as business confidence.

#### Scenario: Several backends could answer
- **WHEN** more than one ready backend declares the required capability
- **THEN** the runtime MUST use configured order to select exactly one and MUST NOT merge, re-rank, or compare their results.

#### Scenario: A hit is stale or escapes scope
- **WHEN** its generation, component, path, capability, index fingerprint, or canonical file validation fails
- **THEN** the stage MUST fail closed before provider invocation or canonical publication and expose a typed safe blocker.

#### Scenario: Search influenced selected context
- **WHEN** a canonical context item was selected using a backend query
- **THEN** its selection provenance and reuse compatibility MUST include route, capability, adapter, and index fingerprints in addition to canonical file provenance.

### Requirement: Let workers use one bounded source-search tool
The generated workflow SHALL let an eligible agent worker decide whether, when, and which permitted source-search operation to request through one provider-neutral `source_search` tool while the coordinator retains backend routing, scope, limits, canonical resolution, provenance, and evidence validation.

#### Scenario: An eligible worker starts
- **WHEN** its role permits source research
- **THEN** its user-scope profile MUST contain a valid optional source-search policy and the immutable invocation policy MUST freeze the intersection of profile and role operations, active generation and components, existing logical evidence-path scope derived from the active stage work unit, and exact per-call and aggregate call, concurrency, deadline, execution-time, query-byte, result-count, and returned-byte limits.

#### Scenario: Profile has no source-search policy
- **WHEN** an otherwise valid profile omits the optional policy
- **THEN** the invocation MUST expose no `source_search` tool and MUST NOT infer default limits.

#### Scenario: Dynamic search capacity is admitted
- **WHEN** an invocation with source search is prepared
- **THEN** preflight MUST prove the prepared input, fixed response reserve, maximum aggregate query and canonical result bytes, and maximum canonical tool framing fit the exact model context limit under the active estimator or fail before slot allocation without narrowing profile limits.

#### Scenario: Tool request is authenticated and bound
- **WHEN** a runtime transports a `source_search` request
- **THEN** the coordinator MUST verify its private invocation capability, project, invocation, policy fingerprint, expiry, unique call ID, and non-terminal admission state before reserving budget.

#### Scenario: A worker submits a valid search
- **WHEN** its operation, query, and requested narrowing fit the frozen policy and remaining budget
- **THEN** the coordinator MUST derive the capability, select the backend deterministically, execute the bounded query, canonically resolve hits, and return only verified navigation fields, fingerprints, closed hit kinds, and any explicitly bounded canonical excerpts.

#### Scenario: A worker attempts to control infrastructure
- **WHEN** it names a backend, uses backend-native query syntax, requests an index build, broadens component or path scope, selects another generation, or exceeds a frozen limit
- **THEN** the tool MUST reject the request with a typed bounded result and MUST NOT execute the requested backend operation.

#### Scenario: Dynamic search is recorded
- **WHEN** the coordinator accepts a tool call
- **THEN** one transaction MUST reserve its ordinal and all applicable aggregate capacity before backend execution and settlement MUST append exactly one content-free terminal ledger entry containing a project-scoped versioned query HMAC, capability, scope, route and index fingerprints, canonical result-manifest fingerprint, result count and bytes, backend duration, and terminal status.

#### Scenario: Search budget is exhausted
- **WHEN** call, concurrency, query-byte, elapsed-time, result-count, or returned-byte capacity is unavailable
- **THEN** the tool MUST return a typed exhaustion result, reject further violating calls, preserve prior valid ledger entries, and publish no result unless the final proposal independently validates.

#### Scenario: Concurrent calls approach one limit
- **WHEN** two or more valid requests would together exceed call, in-flight, query-byte, result-count, returned-byte, or aggregate backend-time capacity
- **THEN** atomic reservation MUST admit only the requests that fit, reserve each call a hard deadline equal to the lesser of its per-call deadline and the currently unreserved aggregate seconds, release only unused reserved seconds at settlement, and MUST start no backend for rejected reservations.

#### Scenario: Invocation terminates during search
- **WHEN** cancellation, timeout, lease loss, or restart reconciliation occurs with reserved or in-flight searches
- **THEN** admission MUST close, backend process groups MUST be cancelled, every ordinal MUST terminalize exactly once, late output and the final proposal MUST be rejected, and no search MUST replay automatically.

#### Scenario: A worker cites a search result
- **WHEN** its final proposal names evidence reached through `source_search`
- **THEN** proposal validation MUST re-resolve and fingerprint that canonical evidence and reject stale, unbound, absent, or altered content.

#### Scenario: Dynamic search usage is diagnosed
- **WHEN** one or more tool calls occur
- **THEN** the workflow MUST report configured limits and actual calls, time, results, and canonical returned bytes separately from prepared-input estimates, provider-managed transcript usage, measured tokens, and monetary cost.

#### Scenario: A searched result is considered for reuse
- **WHEN** prior validated work used `source_search`
- **THEN** reuse without query replay MUST require the prior policy schema and scope, current source, component and file fingerprints, current deterministic route, adapter, capability and index identities, and every canonical result manifest to remain compatible; volatile ordinals, durations, counters, and timestamps MUST NOT participate.

### Requirement: Keep source indexes rebuildable and non-canonical
The generated workflow SHALL keep all backend index databases and lifecycle state outside the repository as rebuildable operational state and SHALL NOT create an index generation, approval, or canonical research authority.

#### Scenario: A backend is added, removed, or rebuilt
- **WHEN** indexing configuration or compatible operational state changes
- **THEN** immutable source, DIF, classification, and MRQ generations MUST remain unchanged while affected search-derived cached work becomes incompatible through its recorded bindings.

#### Scenario: An index build runs
- **WHEN** a backend builds or replaces one index target
- **THEN** it MUST hold a target-keyed fenced lease, write only private staging, validate the exact source and index manifests, derive a promoted instance identity, atomically promote the immutable instance and switch the target pointer, serve queries only from pointed promoted state, and prevent stale, cancelled, crashed, or lease-lost work from promotion.

#### Scenario: The local RLM CLI builds an index
- **WHEN** the coordinator invokes `rlm-bsl-index` instead of the password-protected RLM MCP project registry
- **THEN** build, validation, and query processes MUST share one instance-owned `RLM_INDEX_DIR`, promote that directory with the immutable instance, and MUST NOT depend on a user home, registered project, or project password.

#### Scenario: A legacy matching index is discovered
- **WHEN** existing `rlm-tools-bsl` state has the complete active component, generation, engine, and content identity required by the fixed adapter
- **THEN** the runtime MAY adopt it after bounded validation without rebuilding it solely for the adapter migration.

#### Scenario: Index data is packaged or committed
- **WHEN** template, repository, wheel, source distribution, or research archive checks run
- **THEN** they MUST reject backend databases, process output, credentials, and operational index state.

#### Scenario: Version-2 configuration is downgraded
- **WHEN** an operator previews downgrade for a prior runtime
- **THEN** the service MUST write exact schema version 1 only when one `rlm-tools-bsl` backend and all routes are exactly representable, otherwise block downgrade, retain compatible legacy state, and leave canonical generations unchanged.

## MODIFIED Requirements

### Requirement: Template maintenance is isolated from project runtime
The template SHALL expose repository generation, synchronization, packaging, and `checks template|doctor|research` only through the exact retained repository-local scripts `scripts/sync_generated_runtime.py`, `scripts/build_workspace_template.py`, `scripts/bootstrap/new_research_repo.py`, and `scripts/checks/test_template.py`, `test_doctor.py`, `test_research_repo.py`. They SHALL not import a removed maintenance CLI, SHALL not be part of the installable runtime, and SHALL not be copied into generated project repositories.

Active root documentation SHALL be limited to `README.md`, `AGENTS.md`, `docs/agent/repo-map.md`, `docs/agent/verification.md`, `docs/operator/dispatcher-inspector-rollback.md`, and `docs/operator/source-search.md`. Root-only tests SHALL be limited to `tests/test_generated_runtime_sync.py` and `tests/test_template_release.py` in addition to the exact canonical target test inventory.

#### Scenario: Maintainer validates a target repository
- **WHEN** `checks research` is invoked through the maintenance command for a canonical repository
- **THEN** `test_research_repo.py` MUST execute that repository's canonical non-strict doctor with the current Python executable, explicit repository working directory and source path, no shell, and propagated exit status without requiring retired template contours.

#### Scenario: Maintainer creates a repository
- **WHEN** `new_research_repo.py` receives an empty destination and declared project tokens
- **THEN** it MUST copy only the validated canonical scaffold and replace only those tokens without importing runtime maintenance commands.

#### Scenario: Generated repository is inspected
- **WHEN** a fresh generated repository or its runtime CLI is inspected
- **THEN** it MUST contain no template-maintenance module, script, command, or repository-generation authority.

#### Scenario: Template archive is published
- **WHEN** root maintenance builds `research-template.zip`
- **THEN** `build_workspace_template.py` MUST write a deterministic separate release artifact containing only the validated canonical portable repository payload and MUST NOT install it as `one_c_autoresearch` package data.
