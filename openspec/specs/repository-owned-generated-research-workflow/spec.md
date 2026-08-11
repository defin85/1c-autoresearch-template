# repository-owned-generated-research-workflow Specification

## Purpose
TBD - created by archiving change align-template-with-repository-owned-workflow. Update Purpose after archive.
## Requirements
### Requirement: Fresh repositories use one canonical workflow
The template SHALL generate a repository with workflow schema version `4`, exactly eight gates, nine jobs, and ten workflow operations whose canonical configuration is owned by `project.toml` and `research/*.toml` plus active-generation pointers. The catalog SHALL contain separate `analyze-dif` and `consolidate-mrq` jobs backed by `dif.classify-next` version `1` and `mrq.consolidate` version `1`, and SHALL NOT contain the legacy combined `discover-mrq` job or `mrq.discover-next` operation.

#### Scenario: New repository is generated
- **WHEN** bootstrap creates a repository in an empty destination
- **THEN** its strict doctor MUST report the exact workflow catalog and no competing queue, stage, preference, or compatibility authority.

#### Scenario: Inspect stages 2 and 3
- **WHEN** the canonical workflow catalog is loaded
- **THEN** DIF analysis and MRQ consolidation have different job identifiers, operation identifiers, dependencies, profiles, leases, runs, and recovery actions.

### Requirement: Generated state follows immutable generation boundaries
Generated repositories SHALL keep source payloads, physical differences, complete DIF classifications, MRQ graphs, and target-decision payloads in immutable generation directories and SHALL update only their canonical active pointers through typed operations.

#### Scenario: Source acquisition publishes
- **WHEN** all three roles and declared external artifacts validate
- **THEN** one immutable source generation MUST be published atomically and the prior generation MUST remain addressable.

#### Scenario: DIF analysis publishes a window
- **WHEN** one bounded DIF window completes validation
- **THEN** one accumulated immutable classification generation MUST be published atomically with exact source and physical-diff bindings while prior classification generations remain addressable.

#### Scenario: One DIF in a window fails
- **WHEN** any selected DIF lacks a valid result
- **THEN** the complete window MUST publish no classification pointer update, compatible successful envelopes MAY be reused only by explicit same-job recovery, and no MRQ work MUST start.

#### Scenario: MRQ consolidation publishes
- **WHEN** a complete consolidation plan passes approval and validation
- **THEN** one immutable MRQ generation MUST be activated through one atomically replaced consolidation pointer and no partial generation MUST be visible to a supported reader.

#### Scenario: Target decisions publish
- **WHEN** stage 5 decisions pass their typed approval against the current consolidation fingerprint
- **THEN** one immutable decision generation MUST be written and an aggregate compare-and-swap MUST update only its decision binding while preserving the active MRQ generation, consolidation receipt, and compatible batch binding.

#### Scenario: Operation is unsupported
- **WHEN** a caller submits an arbitrary command or removed legacy operation
- **THEN** the typed application boundary MUST reject it before repository mutation.

### Requirement: Operational state remains outside generated repositories
Generated repositories SHALL keep connection profiles, upload drafts, source indexes, events, bookmarks, previews, preferences, and credentials in mode-confined user scope and SHALL keep only portable declarations and evidence in tracked files.

#### Scenario: User configures local access
- **WHEN** a connection, upload, index, or preview is created
- **THEN** no secret, absolute host path, temporary byte stream, or raw preview identifier MUST be written to tracked repository state.

### Requirement: Synchronization is deterministic and customer-independent
The template SHALL maintain explicit scoped reusable and forbidden inventories and a safe synchronization command that accepts a reference repository, rejects customer payloads and host-specific values, stages and validates the complete derived output set before mutation, defaults to a sorted content-free add/change/delete plan with a content-derived fingerprint, requires that expected fingerprint for explicit apply, replaces each reviewed owned tree from the unchanged staged set, rejects unexpected stale files, blocks packaging while parity is incomplete, and refuses to overwrite a non-empty unapproved destination.

#### Scenario: Reference contains customer evidence
- **WHEN** synchronization encounters source generations, analysis generations, binaries, credentials, absolute workstation paths, or customer-specific outputs
- **THEN** those paths MUST be excluded or the synchronization MUST fail before updating the scaffold.

#### Scenario: Same reference is synchronized twice
- **WHEN** identical reusable inputs are synchronized repeatedly
- **THEN** the generated scaffold and its manifest fingerprints MUST be identical.

#### Scenario: Release version differs from reference
- **WHEN** normalized target parity is checked for release `0.3.0` against the verified canonical reference
- **THEN** only declared package and runtime version fields MAY differ, and every code, schema, command, route, asset, and behavior inventory MUST otherwise match.

#### Scenario: Removed upstream file remains locally
- **WHEN** a file absent from the staged canonical runtime remains in an owned package tree
- **THEN** synchronization MUST remove it through full-tree replacement or fail before publication, and MUST NOT silently preserve it.

#### Scenario: Replacement is previewed
- **WHEN** synchronization is invoked without explicit apply mode
- **THEN** it MUST mutate nothing and report only sorted relative paths, add/change/delete status, non-secret hashes, and one fingerprint binding the reference, staged manifest, destination, and complete change set.

#### Scenario: Planned inputs change before apply
- **WHEN** explicit apply receives a missing or mismatched expected plan fingerprint or any bound input changed after preview
- **THEN** synchronization MUST fail before mutation and require a new preview.

#### Scenario: Replacement stops after one owned tree
- **WHEN** synchronization stops after replacing only part of the validated owned-tree set
- **THEN** parity and packaging MUST fail, and a new preview plus explicit apply with the reference MUST replace every derived tree before packaging can continue.

#### Scenario: Historical OpenSpec evidence is scanned
- **WHEN** forbidden-authority verification scans the repository root
- **THEN** it MUST exclude historical OpenSpec archives and the active removal record while still rejecting the same markers in executable code, active docs, skills, tests, generated repositories, and distributions.

### Requirement: Generated runtime includes source folder import
The generated repository SHALL expose the same versioned external-artifact folder preview, exact declaration-diff review, typed `sources.configure` confirmation, resumable draft staging, CLI, HTTP, and native web selector contract as the verified reusable implementation.

#### Scenario: Equivalent folder is imported
- **WHEN** CLI or web imports equal relative EPF/ERF paths and bytes into a fresh generated repository
- **THEN** both adapters MUST produce equivalent normalized entries, declarations, draft bytes, acquisition-pending state, and later source acquisition behavior.

### Requirement: Fresh generation is fully verified
The template SHALL test a newly generated repository with canonical Python tests, web tests, a production build, clean-install and `0.2.0`→`0.3.0` package smoke checks, normalized target parity, and forbidden-authority scans before release.

#### Scenario: Template verification runs
- **WHEN** the release verification matrix is executed
- **THEN** template maintenance checks, fresh generated-repository checks, distribution-member scans, clean-install and legacy-upgrade command checks, target parity, and canonical runtime tests MUST pass without relying on customer files or an existing workstation path.

### Requirement: Complete DIF classification before MRQ consolidation
The workflow SHALL classify the complete active customer DIF inventory in bounded windows before making MRQ consolidation runnable.

#### Scenario: More than one DIF window remains
- **WHEN** a stage-2 run completes a validated window and unclassified customer DIF records remain
- **THEN** it MUST publish accumulated classification state, select the next window, and continue without invoking MRQ consolidation.

#### Scenario: A DIF is still unclassified
- **WHEN** the active classification generation lacks a valid row for any customer DIF
- **THEN** `all-dif-classified` MUST remain incomplete and `consolidate-mrq` MUST be non-runnable with the exact remaining count.

#### Scenario: Every DIF is classified
- **WHEN** the active classification generation contains exactly one valid meaning-or-noise-candidate row for every active customer DIF and no extra row
- **THEN** `all-dif-classified` MUST become complete and stage 3 MAY be started explicitly.

#### Scenario: DIF inventory is empty
- **WHEN** the active customer DIF inventory contains no records
- **THEN** stage 2 MUST publish an explicit valid empty classification generation without an agent call and stage 3 MUST remain an explicit separately approved operation.

#### Scenario: Inputs change after classification
- **WHEN** the active source or physical-diff generation no longer matches the classification pointer
- **THEN** the gate MUST become stale, stage 3 MUST be blocked, and no classification or MRQ pointer MUST be silently rebound.

#### Scenario: Recompute creates new bindings
- **WHEN** a supported source or diff recompute publishes a new active input generation
- **THEN** it MUST replace pre-stage-3 MRQ revalidation with a new empty classification generation, import only rows whose stable DIF, physical evidence, result schema, analyzer instruction/profile, and context allowed-path fingerprints remain equal, and require stage 2 for every remaining DIF.

### Requirement: Migrate the split workflow fail closed
The generated runtime SHALL provide an explicit migration from compatible legacy analysis results and SHALL NOT resume a legacy combined dispatcher run under the split catalog.

#### Scenario: Compatible analyzer results exist
- **WHEN** migration finds analyzer result envelopes matching the active source, diff, work-unit, profile, instruction, context, and schema fingerprints
- **THEN** it MUST validate and import those rows into an immutable classification generation and report exact imported and remaining counts.

#### Scenario: Preliminary grouping results exist
- **WHEN** migration finds legacy grouper, coordinator, or partial MRQ proposal results
- **THEN** it MUST NOT treat them as canonical stage-3 input or publish them as MRQs.

#### Scenario: Legacy run is active
- **WHEN** workflow migration encounters an active or resumable `discover-mrq` lease
- **THEN** one SQLite transaction MUST mark its invocation and phase work interrupted, preserve its evidence and audit identity, replace its lease with the fenced `workflow-migration` lease, and require an explicit start of the appropriate new job.

#### Scenario: Migration is interrupted
- **WHEN** the process stops after any migration phase
- **THEN** the next invocation MUST use the durable migration journal to resume or restore the exact workflow files, operational database, and prior pointer contents without deleting evidence or repeating a committed effect.

#### Scenario: Migration is repeated after commit
- **WHEN** the migration command is invoked after the same catalog transition committed
- **THEN** it MUST return the recorded result without changing files, pointers, leases, or generations.

#### Scenario: Another entrypoint is called during migration
- **WHEN** the durable version-3-to-version-4 journal exists in any phase before `committed`
- **THEN** every server, CLI, doctor, dispatcher, recompute, and approval entrypoint MUST reject mutation and expose only migration status or recovery.

#### Scenario: Migration lease is requested
- **WHEN** stage recompute or any non-legacy dispatcher lease is active
- **THEN** the global-exclusive `workflow-migration` lease MUST be rejected without interrupting that run.

#### Scenario: Only a legacy discovery lease is active
- **WHEN** the only conflicting lease is active or resumable `discover-mrq`
- **THEN** one SQLite transaction MUST terminalize that run as interrupted, replace its lease with the fenced migration lease, and insert a `handoff_prepared` migration row sufficient to reconstruct the external journal without an unfenced gap.

#### Scenario: Process stops after lease handoff
- **WHEN** `workflow-migration` and its `handoff_prepared` SQLite row committed but the external migration journal does not exist
- **THEN** restart recovery MUST validate the same fence, reconstruct the journal idempotently from the row, and permit no other mutation before recovery completes.

#### Scenario: Another lease is requested during migration
- **WHEN** the global-exclusive `workflow-migration` lease exists
- **THEN** every other lease acquisition and mutating entrypoint MUST fail before repository or operational-state mutation.

#### Scenario: Rollback restores a live legacy lease backup
- **WHEN** the pre-migration database contained an active or resumable `discover-mrq` lease
- **THEN** rollback MUST restore its evidence but persist the lease as interrupted before releasing the migration fence.

#### Scenario: Legacy target decisions exist
- **WHEN** migration or first version-4 consolidation reads approved target decisions embedded in MRQ version 1
- **THEN** it MUST carry only decisions whose retained or revalidated MRQ identity, exact DIF closure, target payload, approval fingerprint, and normalized inputs remain compatible into a separate decision generation, and report every other decision stale.

### Requirement: Installable runtime has one canonical authority surface
The installable `one_c_autoresearch` package version `0.3.0` SHALL expose only the canonical generated-repository runtime, commands, routes, schemas, and workspace behavior and SHALL contain no forbidden legacy authority. Version `0.2.0` SHALL be treated as the exact last compatible legacy package version.

#### Scenario: Distribution is inspected
- **WHEN** a wheel, source distribution, compiled workspace, template archive, or clean installation is scanned
- **THEN** no forbidden path, module, command, route, registration, `CUS-*` identifier, or subject-card schema marker exists, and two-way parity MUST reject any unexplained extra runtime or test file even if it is absent from the named forbidden inventory.

#### Scenario: Removed interface is requested
- **WHEN** a caller imports a removed module or invokes a removed command
- **THEN** the interface MUST be absent and MUST NOT route through a compatibility adapter.

#### Scenario: Legacy installation is upgraded
- **WHEN** the canonical `0.3.0` wheel is installed over a fingerprinted `0.2.0` wheel built from the exact pre-cleanup commit
- **THEN** removed modules, commands, package data, and entry points MUST be absent exactly as in a clean installation.

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

### Requirement: Canonical diff workflow owns normalization and stable identities
The canonical source-routing and diff-generation workflow SHALL deterministically select supported semantic representations, publish immutable physical and semantic difference closure, assign content-derived stable `DIF-*` identities with lineage, and expose explicit diagnostics or physical-only evidence for unsupported or opaque payloads without restoring a standalone normalization CLI or cleanup queue.

#### Scenario: Supported sources are rebuilt unchanged
- **WHEN** equal routed XML/BSL, `v8unpack`, extension, and declared external-artifact inputs are rebuilt with the same contract and tool versions
- **THEN** routing fingerprints, comparison identities, sorted semantic and physical rows, stable `DIF-*` identities, and lineage MUST be identical.

#### Scenario: Semantic representation covers a physical payload
- **WHEN** an opaque or binary physical difference has an authoritative supported semantic representation
- **THEN** the physical path MUST remain accounted for in closed path coverage but MUST NOT independently create or preserve a semantic customization DIF.

#### Scenario: Representation is unsupported or inconclusive
- **WHEN** source routing cannot prove a supported semantic representation
- **THEN** the workflow MUST retain explicit bounded physical evidence and diagnostics and MUST NOT silently classify the payload as semantic customization or technical noise.

### Requirement: Canonical CSV readers accept large evidence fields
The canonical runtime SHALL configure the largest CSV field limit supported by the active Python platform, with bounded `OverflowError` backoff, and SHALL NOT restore the retired standalone paged CSV reader.

#### Scenario: Canonical evidence contains a large field
- **WHEN** a canonical CSV reader processes an evidence field larger than Python's default CSV limit
- **THEN** the field MUST be parsed without `_csv.Error`, and no standalone paging command or compatibility adapter MUST be exposed.

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

### Requirement: Expose the complete approved BSL Analyzer search surface
The generated workflow SHALL expose every approved read-only search, symbol, graph, metadata, diagnostics, and reference action declared by the exact configured BSL Analyzer search-surface manifest through typed provider-neutral operations, and SHALL NOT expose arbitrary native MCP, query execution, evaluation, event-log, debugger, or mutation authority.

#### Scenario: Complete-surface readiness is evaluated
- **WHEN** the fixed BSL Analyzer adapter probes the configured launcher
- **THEN** it MUST bind the exact executable, build, machine contract, live MCP tool schemas, adapter mapping and response normalizers into one search-surface fingerprint and MUST report complete only when every approved action is compatible.

#### Scenario: The native search contract changes
- **WHEN** any tool or action is added, removed, missing, incompatible, absent from the complete allowlist and denylist, a structured response is unversioned, or an exact text-native response violates its pinned wrapper
- **THEN** complete-surface readiness MUST fail closed with typed contract-drift diagnostics and MUST NOT silently retain the prior capability claim.

#### Scenario: A text-native action returns
- **WHEN** `its_help` or coordinator-owned status returns native MCP text
- **THEN** the adapter MUST accept exactly one bounded UTF-8 text item through `native-text-envelope/v1`, label it untrusted, reject all other content shapes, and MUST determine readiness from action-specific probes rather than prose parsing.

#### Scenario: Syntax help returns
- **WHEN** `syntax_help` is invoked
- **THEN** its tool declaration MUST expose `outputSchema`, its result MUST contain `structuredContent` with `schema_version = "1"` and exactly one closed `kind` from `type`, `method`, `global_function`, or `keyword`, the adapter MUST normalize only that structured body, and any compatibility text MUST remain non-authoritative and MUST NOT be parsed.

#### Scenario: A worker searches project code
- **WHEN** its v2 policy permits lexical or hybrid code search
- **THEN** the coordinator MUST invoke the fixed workspace search action, enforce the requested modality, canonically resolve source-bearing hits, and return only bounded normalized results.

#### Scenario: A worker inspects a symbol
- **WHEN** it supplies a qualified symbol or an allowed canonical path and position
- **THEN** the coordinator MUST return a bounded symbol card with explicit definition, type, documentation and usage-summary availability while preserving canonical path scope and backend provenance.

#### Scenario: A worker navigates the semantic graph
- **WHEN** it requests overview, schema, resolve, node, source, neighbors, callers, or callees
- **THEN** the coordinator MUST validate closed direction, detail, edge-kind, provenance, depth and count bounds and return only invocation-bound navigation tokens, normalized relationships and coordinator-read canonical excerpts.

#### Scenario: A worker navigates metadata
- **WHEN** it requests configuration info, tree, object structure, or managed-form structure
- **THEN** the coordinator MUST force source mode, enforce active component scope and return bounded derived navigation findings with canonical owner provenance where available.

#### Scenario: A worker requests analyzer diagnostics
- **WHEN** it requests catalog, schema, one scoped file, or a bounded workspace sweep
- **THEN** the coordinator MUST enforce code, severity, range, file, finding and output bounds and MUST label every result as a derived diagnostic rather than canonical evidence.

#### Scenario: A worker searches platform references
- **WHEN** it requests documentation find/search, syntax help, or ITS expert help
- **THEN** the coordinator MUST use the fixed reference profile, bind the response to its corpus fingerprint, return bounded reference findings, and MUST NOT present them as repository evidence.

#### Scenario: A worker requests symbol references
- **WHEN** the native approved contract offers only a usage summary and no complete reference-list action
- **THEN** the adapter MUST NOT declare complete `symbol-references`, MUST expose the summary under `symbol.info`, and MUST direct full incoming-call traversal to the graph operations.

### Requirement: Operate managed semantic search safely
The generated workflow SHALL enable BSL Analyzer semantic search only through an explicitly reviewed owner-only embedding profile and SHALL keep embedding infrastructure, secrets and derived vectors outside the repository and canonical research state.

#### Scenario: An embedding profile is previewed
- **WHEN** an owner configures an OpenAI-compatible endpoint, model, dimension, provider limits and write-only API key
- **THEN** preview MUST validate endpoint policy, TLS or explicit loopback HTTP, secret existence, model/dimension shape, affected targets and rebuild/reuse impact without returning the secret.

#### Scenario: Semantic indexing starts
- **WHEN** a reviewed enabled profile and semantic route are active
- **THEN** BSL Analyzer MUST receive only a coordinator-owned loopback embedding-egress-broker URL plus non-secret model and dimension settings, that egress broker alone MUST receive the upstream address and credential, and the semantic index MUST bind provider/model-label/dimension/protocol/endpoint-HMAC/secret-version identity.

#### Scenario: The embedding egress broker connects upstream
- **WHEN** it receives the fixed loopback embedding request
- **THEN** it MUST permit only the fixed POST path, remove proxy settings, follow no redirects, validate every resolved address and actual peer on every connection, preserve Host and TLS SNI, enforce hostname verification and approved CA identity, block forbidden address classes, forward no user headers, and validate a bounded response of the exact dimension.

#### Scenario: A remote embedding operation is previewed
- **WHEN** a reviewed profile would disclose source-derived input outside the host
- **THEN** preview MUST show the exact components, files, maximum source bytes, requests and vectors, a known price estimate or unknown-cost warning, and MUST require an external-disclosure acknowledgement bound to the plan.

#### Scenario: Semantic service is unavailable
- **WHEN** endpoint, authentication, model, dimension, TLS, address policy, indexing coverage, or query embedding validation fails
- **THEN** hybrid search MUST report unavailable or degraded with the exact safe cause and MUST NOT silently claim semantic readiness or fall back to lexical under a hybrid operation.

#### Scenario: Lexical search is explicitly requested
- **WHEN** lexical routing and its compatible index remain ready while semantic service is unavailable
- **THEN** lexical search MAY continue independently with explicit lexical modality and unchanged canonical evidence rules.

#### Scenario: Embedding identity changes
- **WHEN** endpoint identity, provider, model, dimension, protocol or secret version changes
- **THEN** affected semantic indexes and search-derived reuse MUST become stale, rebuild MUST require an explicit action, and canonical source and research generations MUST remain unchanged.

#### Scenario: Lexical and hybrid processes are created
- **WHEN** the coordinator builds or queries either modality
- **THEN** it MUST use distinct process and index identities, remove every `EMBEDDING_*` variable for lexical work, point hybrid work only to the loopback broker, and validate the returned modality.

#### Scenario: Embedding limits are enforced
- **WHEN** a probe, query, build or retry is attempted
- **THEN** the runtime MUST cap a probe at 1 request/64 KiB/1 vector/10 s, a query at 1 request/64 KiB/1 vector/30 s, and a build at 10,000 requests/512 MiB/1,000,000 vectors/concurrency 4/batch 256/30 min; profiles MAY only narrow these limits, only 429/502/503/504 MAY retry at most twice within the original deadline and budgets, and restart MUST NOT replay a request.

### Requirement: Preserve bounded evidence and provenance across complete search
The generated workflow SHALL apply the existing scope, budget, ledger, cancellation, evidence-promotion and reuse contracts to every v2 search operation and SHALL distinguish canonical navigation results from backend-derived findings.

#### Scenario: A native result contains source text
- **WHEN** search, graph, metadata, diagnostics, or reference output contains a native snippet or body
- **THEN** the adapter MUST discard it and MAY return only a separately coordinator-read bounded canonical excerpt for an active repository file or a bounded corpus-bound reference excerpt.

#### Scenario: A derived finding has no canonical file
- **WHEN** a symbol, graph, metadata, diagnostic, syntax or ITS finding cannot be resolved to active repository source
- **THEN** it MUST remain navigation-only with backend/index/corpus provenance and MUST NOT independently satisfy final evidence validation.

#### Scenario: A v2 call is admitted
- **WHEN** its typed operation and scope fit the frozen policy
- **THEN** the existing atomic reservation, deadline, aggregate execution-time, query-byte, result-count, returned-byte, context-capacity, HMAC-ledger and exactly-once settlement rules MUST apply before backend execution.

#### Scenario: An operation reaches its tighter bound
- **WHEN** code, symbol, graph, metadata, diagnostics or reference output reaches its operation-specific item, depth, file, finding, excerpt or byte maximum
- **THEN** the adapter MUST stop at a complete item boundary, return stable operation-specific ordering plus `truncated` and a closed narrowing hint, and MUST NOT fabricate a continuation cursor.

#### Scenario: A workspace BSL Analyzer target is admitted
- **WHEN** workspace build, validation or query work requires a compatible target
- **THEN** the coordinator MUST supervise one native broker backend bound to the complete project/component/source-generation/modality/executable/contract/topology/embedding target fingerprint, use a private serving workspace and stable HOME/XDG/runtime socket, prove trusted required-broker transport with no auto-launch or direct-stdio fallback, and MUST NOT reuse it across target identities.

#### Scenario: Resident and call capacity is admitted
- **WHEN** a workspace backend or MCP session is requested
- **THEN** resident backends MUST be capped at four per project and eight per service with a 300-second narrowable idle TTL, 30-second orphan grace and 16-GiB address-space ceiling, calls MUST be capped independently at four per project and eight per service, and queue wait MUST consume the original deadline.

#### Scenario: A reference process is admitted
- **WHEN** one reference or ITS operation starts
- **THEN** it MUST use one private direct-stdio process group bound to one reference identity and obey its admission, startup, operation and address-space ceilings.

#### Scenario: An operation is cancelled or the service restarts
- **WHEN** workspace, reference, embedding or ITS work is in flight
- **THEN** one workspace call MUST close only its session, cancel its proxy and embedding I/O, reject late output and settle exactly once without killing peer calls; target supersession, project shutdown or restart MUST close target admission, drain bounded sessions, TERM then KILL the supervised backend after the fixed grace, quarantine staging and serving state, and replay no request or partial promotion.

#### Scenario: Search-derived work is considered for reuse
- **WHEN** prior v2 results influenced selection or reasoning
- **THEN** reuse MUST additionally require matching operation schema, surface manifest, modality, embedding or reference-corpus identity, route, adapter, index, scope and all canonical result fingerprints.

### Requirement: Build complete-search indexes and references in confined operational state
The generated workflow SHALL store BSL Analyzer indexes, reference corpora and service profiles only in project-scoped owner-only operational state, SHALL promote validated instances atomically, and SHALL keep credentials and approved CA material outside disposable index roots.

#### Scenario: An index instance is built
- **WHEN** a workspace, lexical, hybrid or reference build starts
- **THEN** it MUST point `HOME` and every XDG config/data/cache/state variable to one confined owner-only staging environment bundle, prove through the exact-build access fixture that no persistent access escapes it, use a fenced lease plus atomic writes and directory fsync, enforce the default 32-GiB per-project quota unless the owner narrows it, and MUST switch the current pointer only after manifest and quota validation.

#### Scenario: A promoted index is queried
- **WHEN** the coordinator starts an operation against a current lexical, hybrid or reference instance
- **THEN** it MUST validate the promoted manifest, create or reuse only the matching private copy-on-write serving workspace for its supervised backend, point all HOME/XDG selectors there, discard the serving workspace after drain, and MUST NOT write, mutate or promote changes back into the promoted instance.

#### Scenario: Garbage collection is requested
- **WHEN** operational storage approaches its project quota
- **THEN** the owner MUST receive a dry-run preview and the collector MAY remove only unreferenced non-current instances after confirmation.

#### Scenario: A documentation reference corpus is promoted
- **WHEN** `find_docs` or `search_docs` is enabled
- **THEN** the corpus MUST be bundled with the selected executable and bound to its fingerprint; external or downloaded corpora MUST be rejected until the approved machine contract exposes a proven corpus selector, and probe/build MUST perform no network access.

#### Scenario: Syntax help is enabled
- **WHEN** the exact approved build exposes `syntax_help`
- **THEN** its identity MUST bind the executable fingerprint, build, machine contract, input schema, `outputSchema`, structured schema version and bundled platform data.

#### Scenario: ITS help is enabled
- **WHEN** an owner enables its named profile
- **THEN** the profile MUST use only fixed `https://code.1c.ai`, a write-only `NAPARNIK_TOKEN`, one in-flight request, the fixed question/response/deadline bounds and explicit external-disclosure acknowledgement; it MUST remove proxy variables, allow no URL or CA override, retry and reuse no answer, and label the result untrusted non-evidence.

### Requirement: Migrate complete-search state through reviewed version boundaries
The generated workflow SHALL require `add-pluggable-source-search-backends` to be implemented and archived before this change can archive and SHALL migrate schema 2 to schema 3 with an approved BSL Analyzer machine contract 1.3 only through reviewed preview/apply without implicit indexing.

#### Scenario: Schema 2 configuration is upgraded
- **WHEN** the owner accepts a valid migration preview
- **THEN** apply MUST write explicit lexical/hybrid routes and service-profile bindings, mark affected indexes stale, preserve a pre-migration owner-only backup, and MUST start no build.

#### Scenario: A legacy profile claims symbol references
- **WHEN** a new v2 invocation would use legacy `find_references`
- **THEN** admission MUST reject it with reviewed migration guidance while an already running v1 invocation MAY finish under its pinned v1 semantics and its result MUST NOT be reused by v2.

#### Scenario: The owner rolls back
- **WHEN** a reviewed downgrade is representable
- **THEN** the runtime MUST close v2 admission, drain or cancel its process groups, restore the backed-up prior-runtime-readable schema 2 state, leave v2 namespaces unread by the prior runtime, offer confined retain or purge, and prove prior-runtime readiness before declaring rollback safe.
