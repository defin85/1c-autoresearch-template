## ADDED Requirements

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
