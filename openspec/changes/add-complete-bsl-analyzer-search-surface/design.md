## Context

The existing pluggable-backend change established the correct authority boundary: workers call one provider-neutral bounded tool, the coordinator owns backend routing and scope, backend output is navigation evidence, and selected repository files are re-read from the active immutable source generation before becoming context or evidence.

The approved BSL Analyzer machine contract 1.3 exposes a larger read-only search surface:

- workspace `search`: `search_code`, `status`;
- workspace `symbol_info`;
- workspace `graph`: `overview`, `schema`, `status`, `resolve`, `node`, `source`, `neighbors`, `callers`, `callees`;
- workspace `metadata`: `info`, `tree`, `object`, `form`, `status`;
- workspace `diagnostics`: `catalog`, `schema`, `status`, `file`, `workspace`;
- reference `search`: `find_docs`, `search_docs`, `status`;
- reference `syntax_help`;
- reference `its_help`.

The same workspace profile also declares query validation/execution, arbitrary execution, event-log, and debugger tools. `query.validate` and `query.schema` are read-only static query analysis, but they are not source search and require a separate query-analysis contract. Query execution, arbitrary execution, event-log, and debugger operations additionally cross live-system, credential, mutation, or debugging authorities.

Live probing showed `search_code` returning lexical modality `L` and `degraded = "semantic skipped: not configured (set EMBEDDING_URL)"`. The installed launcher also recognizes managed embedding settings including URL, provider, model, dimension, concurrency, batch size, and API key. Complete hybrid search therefore requires an explicit infrastructure and secret contract.

The target machine contract adds a native `outputSchema` and versioned `structuredContent` to `syntax_help`, projected directly from platform data without parsing its compatibility Markdown. `its_help` and coordinator-owned status remain bounded text surfaces. `its_help` requires `NAPARNIK_TOKEN` and sends the question to the fixed external origin `https://code.1c.ai`, so complete support must distinguish structured native results from bounded text results and define ITS egress explicitly.

## Locked Decisions

1. “Complete BSL Analyzer search support” means every read-only search, symbol, graph, metadata, diagnostics, and reference action listed in the approved machine-contract surface for the exact configured build.
2. Query execution, arbitrary execution/evaluation, event-log, debugger, DAP, LSP mutation, and unrestricted native MCP access remain out of scope.
3. Workers continue to receive one provider-neutral `source_search` tool and cannot select a backend, native tool name, MCP profile, index, generation, endpoint, or credential.
4. The BSL Analyzer adapter uses only fixed native action builders. Structured-native actions require versioned `structuredContent`. `syntax_help` additionally requires its declared `outputSchema`, `schema_version = "1"`, and one closed `kind` from `type`, `method`, `global_function`, or `keyword`; its compatibility text is never parsed. `its_help` and coordinator-owned text status use a fixed bounded MCP-text wrapper and versioned adapter normalizer pinned by golden fixtures. The adapter never forwards arbitrary JSON arguments or backend-native tool names from a worker.
5. Complete-surface readiness is fail-closed. Missing required actions, incompatible input schemas, unknown structured response versions, or newly added search actions not mapped by the adapter produce explicit contract-drift diagnostics.
6. Search-surface coverage is pinned by adapter protocol version, exact executable fingerprint, build version, machine contract version, normalized native tool schemas, and result-normalizer versions.
7. Semantic search uses an owner-only managed embedding profile chosen by the user. It may target an allowlisted local or remote OpenAI-compatible endpoint and contains model, dimension, provider options, hard request/input/vector/retry limits, and a write-only operational credential.
8. Repository files, URLs, credentials, tokens, and embedding API keys never enter tracked indexing configuration, execution snapshots, ledgers, logs, diagnostics, packages, or canonical research artifacts.
9. Remote embedding traffic passes through a coordinator-owned loopback embedding egress broker. BSL Analyzer receives only its loopback URL and non-secret model/dimension settings; the egress broker alone receives the upstream address and credential. Neither process inherits proxy, embedding, credential, or global BSL Analyzer configuration.
10. Lexical and semantic readiness are distinct process and index identities. A lexical process has every `EMBEDDING_*` variable removed; a hybrid process points only to the loopback broker. Hybrid search cannot report ready when semantic indexing or querying is unavailable, and an explicitly hybrid request never silently downgrades to lexical.
11. Enabling, changing, or disabling the embedding identity requires a reviewed configuration operation and invalidates only semantic index instances and search-derived reuse bound to that identity.
12. Backend graph IDs are invocation- and index-bound navigation tokens, not stable evidence identities. Only canonical repository paths and fingerprints may be cited as source evidence.
13. Native snippets and source bodies are discarded. Source-bearing results are re-read from the canonical active source and returned only as policy-bounded canonical excerpts.
14. Symbol cards, metadata structures, diagnostics, and reference answers without a repository path are derived navigation findings. They carry backend/index/reference-corpus provenance and cannot independently satisfy final evidence requirements.
15. `find_references` is removed as a claimed complete capability until a native full-reference operation exists. Usage summaries and incoming calls are exposed under their honest operation names.
16. Existing call, concurrency, deadline, aggregate backend-time, query-byte, result-count, returned-byte, context-capacity, cancellation, HMAC-ledger, and exactly-once settlement rules apply to every new operation.
17. Reference-profile processes and indexes use the same owner-only operational-state boundary and fixed launcher, but a distinct reference-corpus identity and readiness lifecycle. They never download or update a corpus during probe or query.
18. `syntax_help`, `find_docs`, and `search_docs` use only reference data bundled with the selected approved executable. The first delivery supports no external corpus until the approved machine contract exposes a proven corpus selector. ITS is a separate external service identity and is never treated as the bundled reference corpus.
19. ITS requires an independently reviewed owner-only profile, write-only token and explicit external-disclosure acknowledgement. The exact approved launcher receives `NAPARNIK_TOKEN` only for one fixed reference process; workers never receive it, no user-controlled ITS URL exists, and proxy variables are removed.
20. The project reuses the existing owner-only user-state authority rather than inventing a generic secret store. Search service profiles and their write-only credentials live in a dedicated project file under that authority.
21. Machine contract 1.3 is the minimum complete-surface contract. Support is capability-pinned rather than product-version-pinned: the complete allowlist and denylist cover every tool/action in both profiles, while the selected executable build and fingerprint are recorded as runtime identity. Any added or removed tool/action is incompatible until the adapter is reviewed.
22. No new canonical registry, generation pointer, approval flow, or worker-visible infrastructure configuration is introduced.
23. The native BSL Analyzer process broker is the required transport for the heavy `workspace` profile. The coordinator owns and supervises one resident backend per complete target identity; workers still see only `source_search`. The lightweight `reference` profile remains direct `stdio`.
24. Workspace broker identity binds the canonical serving-workspace path plus repository instance, component, source generation, modality, executable fingerprint/build, machine/surface contracts, extension topology and embedding identity. Product-version-only rendezvous is insufficient.
25. Broker admission is fail-closed: the selected launcher MUST expose a conformance-proven required-broker mode that neither auto-launches an unsupervised backend nor falls back to direct `stdio`. A fallback, unknown backend PID, mismatched identity or untrusted rendezvous makes the route incompatible.

## Complete Search Surface

### Provider-neutral operation families

`source-search-tool/v2` uses closed tagged requests:

| Family | Operations | Native mapping |
| --- | --- | --- |
| Code | `code.search_lexical`, `code.search_hybrid` | workspace `search.search_code` with modality enforcement |
| Symbol | `symbol.info`, `symbol.info_at` | workspace `symbol_info` by qualified name or path/line/column |
| Graph | `graph.overview`, `graph.schema`, `graph.resolve`, `graph.node`, `graph.source`, `graph.neighbors`, `graph.callers`, `graph.callees` | corresponding workspace `graph` actions |
| Metadata | `metadata.info`, `metadata.tree`, `metadata.object`, `metadata.form` | corresponding workspace `metadata` actions in source mode |
| Diagnostics | `diagnostics.catalog`, `diagnostics.schema`, `diagnostics.file`, `diagnostics.workspace` | corresponding workspace `diagnostics` actions |
| Reference | `reference.find_docs`, `reference.search_docs`, `reference.syntax_help`, `reference.its_help` | reference profile `search`, `syntax_help`, and `its_help` |

Native `status` actions remain coordinator-owned readiness probes and are not worker operations.

Every request schema contains only the fields needed by that operation. Closed enums cover graph direction, detail, edge kinds (`call`, `manager_creates`, `manager_access`, `query_ref`, `contains`, `data_binding`), provenance (`resolved`, `inferred`, `visibility_blocked`, `unresolved`), metadata mode fixed to source, diagnostics severity/detail, locale, and bounded counts/ranges. Unknown fields and combinations fail before backend execution.

`code.search_lexical` and `code.search_hybrid` use separate promoted index targets and process builders because native `search_code` has no modality argument. The lexical builder removes every embedding variable; the hybrid builder supplies only the local broker endpoint plus fixed model and dimension. Returned modalities are validated against the requested operation.

### Response classes

The common response is a bounded ordered union:

- `canonical_navigation_hit`: canonical logical path, source generation, component and file fingerprints, optional line range, symbol and closed kind;
- `canonical_excerpt`: the same identity plus a coordinator-read bounded excerpt;
- `navigation_node`: opaque invocation-bound navigation token, label, kind, signature, bounded relationship summary, backend/index binding;
- `navigation_edge`: source and target navigation tokens, closed relation kind and provenance;
- `derived_finding`: closed finding kind, bounded structured fields, backend/index/reference-corpus binding, optional canonical owner path;
- `reference_hit`: reference-corpus fingerprint, bounded title/member/signature/excerpt and opaque rank;
- `bounded_native_text`: text from `its_help` or coordinator-owned status, wrapped without parsing markup into `native-text-envelope/v1`, UTF-8 validated, secret-redacted, byte-bounded and labelled untrusted external/reference text;
- `retry`: typed loading/stale/superseded state and retry-after bound;
- `degraded`: exact missing modality or unavailable surface without silently substituting another operation.

Backend scores remain opaque within one response and never become confidence or cross-query ordering. Structured-action text layouts are ignored. For `syntax_help`, the adapter validates the declared `outputSchema`, consumes only structured schema 1, and rejects unknown kinds or shapes; its text block is retained only for compatibility diagnostics. For text-native actions, the adapter accepts only MCP `content` containing one text item within the fixed byte limit; images, resources, embedded tool calls, additional content items, invalid UTF-8 and oversized content are rejected. Status text is retained only as a redacted bounded operator diagnostic; readiness is established by action-specific probes rather than by interpreting prose.

## Search-Surface Manifest And Probe

The adapter derives a canonical `bsl-search-surface/v1` manifest from:

- exact launcher content fingerprint and build version;
- machine contract major/minor and declared CLI/MCP profile tools/actions/parameters;
- live MCP `tools/list` names and canonicalized input schemas;
- adapter operation map and response-schema versions;
- supported lexical, semantic, graph-edge, metadata, diagnostics, and reference modalities.

The manifest declares `complete`, `partial`, or `incompatible`:

- `complete`: every locked action exists with compatible required parameters and structured schema;
- `partial`: safe explicitly configured subsets such as lexical-only are available, but the backend cannot satisfy a complete route;
- `incompatible`: a mapped action is missing/incompatible, a structured-native response is unversioned, a text-native response violates its exact wrapper, or either profile contains any tool/action absent from the complete approved allowlist and explicit denylist.

Contract descriptions are not parsed to drive execution or classify new actions. Tests pin every tool and action in both profiles, including the forbidden `query`, `execute`, `event_log`, `debug` and other non-search surfaces. Later builds require an adapter manifest update before they can claim complete coverage.

## Managed Embedding Profile

Search service configuration reuses the existing project-scoped owner-only user-state authority. It is stored in `<state-root>/projects/<workspace-id>/search-services.json`; the parent is mode `0700`, the file is mode `0600`, writes use confined no-follow creation, an owner-only temporary file, `fsync`, atomic rename and directory `fsync`. The file has exact schema `search-services/v1` and contains named embedding and ITS profiles. It is outside the disposable index root and is not packaged, synchronized, logged, fingerprinted with secret values, or returned wholesale by an API.

Embedding configuration is edited through the existing server-side project mutation authority and CSRF/idempotency boundary. A profile contains:

- stable profile ID and display label;
- provider kind `openai-compatible`;
- normalized HTTPS endpoint or explicitly allowed loopback HTTP endpoint, without userinfo, query or fragment;
- exact model and positive dimension;
- explicit per-build request, input-byte, vector, concurrency, batch and elapsed-time limits within server maxima;
- optional write-only API key stored directly in the owner-only profile plus a random non-secret secret-version ID changed on replacement;
- optional CA bundle ID from a fixed owner-only CA catalog, never an arbitrary path;
- enabled state and last successful probe identity.

Preview validates URL policy, model/dimension shape, credential presence without returning it, affected semantic targets, exact component/file/source-byte disclosure, configured maximum requests/input bytes/vectors, known price estimate or explicit unknown-cost warning, rebuild/reuse impact, current state fingerprint, actor, expiry and plan fingerprint. Remote profiles require an explicit disclosure acknowledgement bound to that plan. Apply revalidates authorization, CSRF, actor, project, expiry, idempotency and expected fingerprints under the project mutation lock. Read APIs return only `credential_configured`, secret-version ID, redacted endpoint class/HMAC prefix and safe probe state. The tracked schema-version-3 `research/indexing.toml` refers to a stable embedding profile ID and semantic mode, never to URL or secret.

For a remote profile the coordinator starts one local egress broker in the same cancellable process group. BSL Analyzer receives `EMBEDDING_URL=http://127.0.0.1:<ephemeral>/v1/embeddings`, model and dimension but no upstream URL or API key. The broker alone receives the resolved profile in memory and:

- accepts only loopback clients and fixed `POST /v1/embeddings` JSON;
- removes every proxy variable and honors no system proxy;
- permits zero redirects;
- resolves every connection, requires every A/AAAA result to pass the profile address class, connects to one validated address while retaining the canonical Host and TLS SNI, and verifies the actual peer;
- blocks unspecified, multicast, link-local, CGNAT and metadata-service ranges; remote profiles also block loopback, private and Unix-socket targets, while explicit local profiles allow only loopback;
- requires TLS hostname verification for remote HTTPS and may add one approved CA bundle without disabling system trust or hostname checks;
- injects only fixed Bearer authentication, forwards no user headers, and accepts only bounded JSON embedding responses of the exact configured dimension.

The server maxima are: probe `1` request, `64 KiB` input, `1` vector and `10 s`; one semantic query `1` request, `64 KiB` input, `1` vector and `30 s`; one build `10,000` requests, `512 MiB` input, `1,000,000` vectors, concurrency `4`, batch `256`, and `30 min`. Profiles MUST narrow build maxima. Authentication, policy, TLS, dimension and other 4xx failures are never retried. Only `429`, `502`, `503` and `504` receive at most two retries, honoring a capped `Retry-After` plus bounded jitter inside the original deadline and budgets. Restart never replays a remote request.

Semantic index identity includes provider kind, configured deployment/model label, normalized endpoint HMAC, dimension, embedding protocol and secret-version ID; it does not claim to prove immutable model weights. Any profile, endpoint, deployment/model label, dimension, protocol or secret-version change stales the identity. Broker telemetry records only counts, bytes, vectors, status class and bounded duration, never payloads, embeddings, endpoint, credential or raw response.

## Reference And ITS Lifecycle

Reference-profile state has two explicit authorities:

- `syntax_help` uses platform data bundled into the selected approved executable; its corpus identity is the executable fingerprint, build version, machine contract, native input schema, `outputSchema`, structured schema version and closed-kind set;
- `find_docs`/`search_docs` use only the corpus bundled with the selected executable. Owner-provided or downloaded corpora remain unsupported until the approved machine contract exposes a proven selector.

Reference build runs the fixed reference profile from the exact executable in owner-only staging without network or credentials, validates the bundled-corpus and promoted-index manifests, and atomically switches an executable-keyed current pointer under a fenced lease. An attempted download, unexpected corpus input, partial build, cancellation or crash prevents promotion. The workspace shows exact executable/corpus fingerprints; it never serves native corpus paths.

`its_help` is not a local corpus. Its named owner-only profile stores a write-only `NAPARNIK_TOKEN`, random secret-version ID, enabled state, fixed service identity `https://code.1c.ai`, per-call deadline `60 s`, maximum question `4096` UTF-8 bytes, maximum response `32 KiB`, maximum one in-flight request and explicit external-disclosure acknowledgement. No user URL or CA override is accepted. The selected approved child receives the token only in its private environment; proxy variables and unrelated credentials are removed. Until the approved machine contract offers a configurable ITS endpoint or token file descriptor, complete readiness records this transport limitation. ITS calls are never automatically retried or reused and their answers remain untrusted non-evidence.

Profile delete first disables admission and waits for or cancels bounded in-flight work. Secret replacement generates a new random version. APIs never return old secret values. Secure purge removes only the selected project profile after separate confirmation and does not delete shared CA material.

## Exact Operation Bounds And Ordering

Common v2 maxima remain `4096` query bytes, `100` requested results, `131072` returned bytes, `60 s` per call, and the existing per-invocation aggregate limits. Tighter operation maxima are:

| Operations | Additional maxima |
| --- | --- |
| code search, docs find/search | native limit `50`, excerpt `16 KiB` per hit |
| symbol info | one symbol or one path/line/column, `64 KiB` response |
| graph overview/resolve/node | top/nodes `50`, body excerpt `16 KiB` |
| graph source | `50` IDs, canonical excerpt aggregate `128 KiB` |
| graph neighbors/callers/callees | depth `5`, nodes `50`, edge kinds `6`, provenances `4` |
| metadata tree | items `1000`, aggregate `128 KiB` |
| metadata object/form/info | one object/form, aggregate `128 KiB` |
| diagnostics catalog/file | codes `200`, findings `200`, aggregate `128 KiB` |
| diagnostics workspace | files `1000`, findings `1000`, aggregate `128 KiB` |
| syntax/ITS help | one request, native text `32 KiB` |

Search and documentation hits retain native rank within one response. Graph nodes/edges are normalized and sorted by durable opaque ID and relation tuple; metadata by object type/name/path; diagnostics by canonical path, line, column and code. A response truncates only at a complete item boundary, sets `truncated` and a closed narrowing hint, and returns no continuation cursor while the approved machine contract exposes no stable pagination. Workers may issue another narrower budgeted query; the coordinator never fabricates offsets or partial source bodies.

## Process And Storage Lifecycle

The `workspace` profile uses the native BSL Analyzer process broker. The coordinator starts and supervises one resident backend for each complete target fingerprint before admitting calls; thin required-broker MCP proxies connect to it and never start or select another backend. A target fingerprint binds repository instance, component, source generation, lexical/hybrid modality, executable fingerprint/build, machine and surface contracts, extension topology and embedding identity. A changed axis creates a new serving identity; the old backend closes admission and drains. The `reference` profile remains one private direct-stdio process group per operation because it is lightweight and the native broker does not support it.

Operational layout is `<state-root>/indexes-v3/<repository-instance>/<target-fingerprint>/{lease.json,staging/,instances/,current.json,serving/}`. Each promoted instance is the immutable analyzer environment bundle selected through `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME` and `XDG_STATE_HOME`; no untracked host directory participates in index selection. Build points all five variables at staging and an access-trace conformance fixture for the selected executable proves every persistent read/write is confined there before promotion. Before starting a workspace backend, the coordinator validates the promoted manifest and creates one private copy-on-write serving workspace under `serving/<serve-identity>/`; the backend and all of its proxies receive the same private HOME/XDG and owner-only runtime-socket directory. Calls never mount or write the promoted instance. The serving workspace may update only derived state, is never promoted back, and is discarded after the backend drains. An observed persistent access outside the bundle or a changed promoted manifest makes the adapter incompatible.

Directories are `0700`, files `0600`; all opens are confined, no-follow and reject symlinks. Staging and promoted files use atomic writes and directory `fsync`; current pointer switches only after manifest and quota validation under the fenced lease. Per-project default quota is `32 GiB`, with owner-reviewed narrowing; garbage collection removes only unreferenced non-current instances and drained serving workspaces after a dry-run preview. Credentials and CA bundles are outside this root.

Call cancellation closes that session, cancels proxy and embedding-broker I/O, rejects late output and settles the ordinal exactly once without killing a shared workspace backend used by other calls. Project shutdown, restart reconciliation, target supersession or backend identity failure closes admission for the target, drains bounded sessions, then sends TERM and KILL after a `2 s` grace to the supervised backend process group and discards its serving workspace. Reference and ITS cancellation still terminates their private process groups. No cancelled native work is replayed or promoted. Restart quarantines stale staging and serving directories, never promotes partial state, and requires explicit rebuild when no compatible promoted instance exists.

Resident admission remains bounded: at most four workspace backends per project and eight per service, each with a `16 GiB` address-space ceiling and a default `300 s` idle TTL that configuration may only narrow. Call admission remains independently capped at four concurrent analyzer calls per project and eight per service; queue wait consumes the original deadline. A backend that has not served real MCP traffic exits after a `30 s` orphan grace. The coordinator records backend PID/process-group identity, transport, target fingerprint, active sessions, warm/idle/superseded state and terminal cause without queries or results.

## Routing And Policy

The capability vocabulary becomes operation-specific rather than overstated:

- `code-search-lexical`, `code-search-hybrid`;
- `symbol-info`, `symbol-info-positional`;
- graph capabilities for overview, schema, resolve, node, source, neighbors, callers, and callees;
- metadata capabilities for info, tree, object, and form;
- diagnostics capabilities for catalog, schema, file, and workspace;
- reference capabilities for docs find/search, syntax help, and ITS help.

Compatibility aliases translate v1 operations only where semantics are exact. `search_text` maps to lexical code search. `find_symbol`, `find_callers`, `find_callees`, and metadata tree retain narrow aliases. `find_references` has no automatic alias because the old implementation did not provide a complete reference list.

Profiles opt into operation families and may further narrow graph edge/provenance kinds, diagnostic severities/codes, metadata object types, locales, and reference operations. The server intersects profile policy, role allowlist, active work-unit source scope, route coverage, semantic/reference readiness, and remaining budgets.

Classifier remains limited to lexical/hybrid code search and symbol information unless explicitly expanded by a later role-policy change. Other source-research roles may receive the complete read-only surface under profile limits.

## Provenance, Evidence, And Reuse

Every terminal call ledger entry records operation version, modality, surface-manifest fingerprint, scope, route, adapter, workspace/reference index and embedding identities, canonical result-manifest fingerprint, counts, bytes, duration, degradation and terminal status. Raw queries remain protected by the existing project-scoped versioned HMAC.

Final proposal validation accepts only repository evidence re-resolved from the active source generation. Derived diagnostics, symbol facts, graph relationships, metadata projections, and reference answers may explain selection and reasoning but cannot replace a cited canonical file. Reuse additionally requires the v2 operation schema, surface manifest, modality, embedding/reference identities, route and all ordinary source fingerprints to remain compatible.

## Operability And UI

The existing index workspace adds:

- exact BSL Analyzer search-surface coverage by operation;
- complete/partial/incompatible status and contract-drift reason;
- lexical and semantic index/query readiness separately;
- embedding profile identity, model, dimension, redacted endpoint class, last probe and rebuild impact;
- workspace and reference profile readiness and corpus fingerprints;
- route coverage and degraded causes for every operation;
- confirmed rebuild/validate controls using existing visible background progress.

Agent-profile editing groups operations by Code, Symbol, Graph, Metadata, Diagnostics, and Reference and shows role restrictions and worst-case dynamic context reserve. Dispatcher inspection shows actual operation/modality counts and bounded derived-versus-canonical result counts without queries or content.

## Migration And Rollback

Existing v1 policies and ledgers remain readable. On upgrade:

1. v1 `search_text` becomes explicitly lexical;
2. exact symbol/caller/callee/tree operations receive compatible narrow aliases;
3. new invocations from a profile containing `find_references` are blocked until reviewed migration removes it; an already running v1 invocation continues under its immutable pinned v1 adapter semantics until terminal state, is never upgraded in place, and its result is not reusable by v2;
4. no semantic profile is inferred from environment;
5. no semantic rebuild starts automatically;
6. v1 completed results remain audit evidence but are not reused for v2 complete-surface work;
7. schema-version-2 indexing configuration remains readable but cannot satisfy this change; reviewed preview/apply selects an approved machine-contract-1.3 executable, writes schema version 3 with explicit lexical/hybrid routes and service profile bindings, marks affected indexes stale and starts no build.

Rollback first closes v2 admission, cancels or drains analyzer, broker, reference and ITS process groups, and applies a reviewed downgrade that removes v2 policies/routes and restores the backed-up prior-runtime-readable schema-version-2 configuration only when representable. V2 search-service state and indexes use namespaces the prior runtime never reads. The owner chooses explicit retain or secure purge; purge is confined to this project's v2 profiles and indexes and never deletes shared CA material without separate confirmation. A pre-migration owner-only backup restores operational profile and route state; canonical source, DIF, MRQ, and decision generations require no rollback. An automated compatibility test starts the prior runtime against the downgraded state and proves readiness before rollback is declared safe.

## Audit Matrix

| Concern | Required evidence |
| --- | --- |
| Completeness | Every approved machine-contract-1.3 search action has one typed operation or coordinator-owned status probe |
| Contract drift | Added, removed, or incompatible native search actions cannot retain complete readiness |
| Semantic truth | Hybrid readiness proves configured embedding probe, index coverage, model and dimension |
| Honest capabilities | No usage summary is advertised as a complete references list |
| Scope | Workspace operations remain bound to active components and logical path prefixes |
| Evidence | Only coordinator-read canonical files can satisfy final source evidence |
| Derived facts | Graph, metadata, diagnostics and reference outputs are explicitly navigation-only |
| Security | No arbitrary MCP, execution, debug, event log, credential, endpoint secret, raw native body or user environment |
| SSRF/TLS | Loopback egress broker owns DNS/peer validation, address classes, TLS, zero redirects, fixed path and proxy removal |
| External disclosure | Remote embedding and ITS preview identifies exact scope, volume, service and acknowledgement |
| Budgets | Every operation obeys calls, concurrency, time, query, result, bytes and context reserve |
| Cost | Build/query request, input-byte, vector, retry and concurrency ceilings plus known or unknown cost disclosure are enforced |
| Cancellation | A workspace call cancels its session without killing peer calls; target/service teardown drains and terminates the supervised backend; every ledger settles exactly once |
| Storage | Confined no-follow owner-only layout, quota, atomic promotion and previewed GC preserve current state |
| Scalability | Resident-backend and call caps, idle/orphan expiry, queue-deadline accounting and address-space ceilings bound native concurrency |
| Identity | Broker target, surface, executable, complete XDG environment bundle, index, embedding and bundled reference corpus identities bind routing and reuse |
| Transport | Workspace calls prove supervised native-broker transport with no auto-launch or direct-stdio fallback; reference remains direct stdio |
| Operability | UI separates lexical, semantic, workspace, reference and per-operation readiness |
| Compatibility | Safe v1 aliases are explicit; false references support is removed |
| Prerequisite | Pluggable backend change is implemented and archived before this change can archive |
| Authority | No new canonical research entity or backend-derived evidence authority |
| Verification | Native contract fixtures, live adapter conformance, hostile inputs, UI and scaffold checks cover the surface |

## Execution Plan

1. Implement, verify and archive `add-pluggable-source-search-backends`, then prove the base specifications contain its contracts and capture a prior-runtime rollback fixture.
2. Add the approved offline machine-contract-1.3 search-surface allowlist/denylist fixture, structured/text normalizers and contract-drift conformance tests.
3. Define `source-search-tool/v2`, operation-specific capabilities, typed requests, normalized response union, v1 aliases, bounds and role/profile policies.
4. Implement the supervised native workspace-broker lifecycle and complete workspace search, symbol, graph, metadata, and diagnostics adapters with canonical source resolution and bounded derived findings.
5. Implement the exact-build-bundled reference-corpus lifecycle plus docs, syntax and managed ITS operations with separate identities.
6. Add owner-only service profiles and the loopback embedding broker, then implement semantic identity and distinct lexical/hybrid readiness.
7. Extend routing, process/storage lifecycle, indexes, ledger, context admission, reuse, cancellation and recovery to every v2 operation.
8. Add accessible workspace/profile/dispatcher diagnostics and reviewed configuration, migration, rollback and rebuild flows.
9. Implement target-first, synchronize template/scaffold/package outputs, and run the offline release gate plus the full verification matrix; run live conformance only when the selected executable is provisioned.

## Risks / Trade-offs

- [Remote embedding endpoint leaks business source] → Require explicit owner configuration, endpoint policy, TLS, secret isolation, disclosure in preview, and an option to use a local endpoint.
- [“Complete” drifts as BSL Analyzer evolves] → Pin a surface manifest and fail closed on unclassified search actions.
- [Large graph/diagnostic output exhausts context] → Keep server-side caps, closed filters, canonical excerpts, and existing aggregate budgets.
- [Derived analyzer facts are mistaken for evidence] → Label response classes and require canonical repository evidence at final validation.
- [Semantic model changes ranking] → Bind model/dimension/provider identity to index, ledger, diagnostics and reuse.
- [Reference answers are external authority] → Bind corpus identity, label them reference navigation, and do not promote them as repository evidence.
- [Old profiles relied on false references support] → Mark the operation unsupported and require explicit profile migration rather than silently changing meaning.
- [Native broker silently falls back or reconnects to the wrong backend] → Require supervised fail-closed broker transport, complete target identity, trusted rendezvous and explicit transport diagnostics.
- [Cancelling one call kills peer calls or leaves unbounded work] → Cancel only its session, reject late output, and reserve process-group termination for bounded target/service teardown.

## Assumptions And Open Questions

- Assumption: machine contract 1.3 remains the minimum approved surface, structured-native BSL Analyzer actions continue to expose versioned structured replies, and genuinely text-native actions remain pinned to their wrapper fixtures until native output schemas are added.
- Assumption: an OpenAI-compatible endpoint honors the fixed `/v1/embeddings` request/response shape; configured deployment/model label and dimension identify compatibility but do not prove immutable model weights.
- Assumption: locally available reference material can prove origin and license without an automatic network acquisition step.
- Delivery prerequisite: BSL Analyzer must add and release a machine-declared, conformance-proven required-broker mode with supervised backend startup, no proxy auto-launch and no direct-stdio fallback; the current native proxy behavior does not satisfy this contract.
- Open questions: none.
