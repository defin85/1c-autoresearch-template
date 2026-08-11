## 0. Satisfy The Delivery Prerequisite

- [x] 0.1 Implement, verify and archive `add-pluggable-source-search-backends`; confirm its requirements exist in the base specifications before this change is implemented or archived.
- [x] 0.2 Capture a pre-migration owner-only schema-2 backup and prove the prior runtime starts and reports readiness from it.
- [x] 0.3 Gate release on an approved offline machine-contract-1.3 fixture; keep live-binary conformance opt-in unless CI explicitly provisions the selected executable.
- [x] 0.4 Extend BSL Analyzer with a machine-declared supervised required-broker mode that accepts coordinator-owned startup, forbids proxy auto-launch and direct-stdio fallback, and exposes safe transport/backend identity diagnostics; block this change's implementation until its conformance fixture passes.

## 1. Freeze The Complete Surface — after 0

- [x] 1.1 Capture complete allowlist and denylist fixtures for every workspace/reference tool and action in BSL Analyzer machine contract 1.3 without descriptions driving execution.
- [x] 1.2 Define `bsl-search-surface/v1`, complete/partial/incompatible states, exact executable/build/contract/schema/normalizer fingerprints, structured-native response versions, `native-text-envelope/v1` for genuinely text-only actions, and fail-closed contract drift.
- [x] 1.3 Add golden conformance tests proving every approved action is mapped exactly once or coordinator-owned, every structured action matches its advertised `outputSchema`, every text-only response has exactly one bounded text item, and every non-search or newly changed action remains forbidden.
- [x] 1.4 Replace broad or false capability declarations with operation-specific capabilities and remove complete `symbol-references` until the native contract supplies it.
- [x] 1.5 Freeze native broker conformance: required workspace-broker mode, supervised daemon startup, trusted rendezvous, no proxy auto-launch, no direct-stdio fallback, complete target identity and explicit transport diagnostics.

## 2. Define Source Search V2 — after 1

- [x] 2.1 Add the closed tagged `source-search-tool/v2` request schemas for Code, Symbol, Graph, Metadata, Diagnostics, and Reference families.
- [x] 2.2 Add operation-specific closed enums, parameter combinations, stable ordering and per-operation item/depth/file/finding/excerpt/byte maxima; profiles may only narrow the common server maxima.
- [x] 2.3 Define bounded canonical-hit, canonical-excerpt, navigation-node/edge, derived-finding, reference-hit, native-text, retry and degraded responses with complete-item truncation, narrowing hints and no fabricated cursors.
- [x] 2.4 Add safe v1 aliases for lexical text, symbol, caller, callee and metadata tree; reject legacy `find_references` with typed migration guidance.

## 3. Complete Workspace Search Adapters — after 2

- [x] 3.1 Implement distinct lexical and hybrid process/index builders for `search_code`; remove every embedding variable from lexical work, use only the loopback broker for hybrid work, validate modality and canonically resolve source-bearing hits.
- [x] 3.2 Implement qualified and positional `symbol_info` without misrepresenting usage summaries as full references.
- [x] 3.3 Implement graph overview, schema, resolve, node, source, neighbors, callers and callees with bounded direction, detail, edge, provenance, depth and node filters.
- [x] 3.4 Implement metadata info, tree, object and managed-form operations in forced source mode and bind derived structures to component and canonical owner provenance.
- [x] 3.5 Implement diagnostics catalog, schema, scoped file and bounded workspace operations with code, severity, range, file, finding and output caps.
- [x] 3.6 Discard native snippets/bodies and generate source excerpts only by coordinator re-read of active canonical files.

## 4. Add Reference Search — after 1 and 2

- [x] 4.1 Add a separate fixed reference-profile process/index lifecycle using only the selected-build-bundled corpus; reject external corpora until the approved machine contract provides a proven selector and prohibit downloads or network during probe/build.
- [x] 4.2 Implement structured docs search; consume `syntax_help` only from native `structuredContent` schema 1 and retain its text block solely for compatibility diagnostics; use the bounded text wrapper only for `its_help` and coordinator-owned text status.
- [x] 4.3 Keep reference findings navigation-only and reject their promotion as repository evidence.
- [x] 4.4 Add the owner-only ITS profile with fixed service, write-only token, explicit disclosure acknowledgement, one in-flight call, no user URL/CA override, proxy removal, no retry/reuse and fixed question/response/deadline bounds.
- [x] 4.5 Add readiness, no-download, cancellation, stale-corpus and output-bound tests plus `syntax_help` `outputSchema`, schema-version, closed-kind, structured-body/text-compatibility and unsupported-schema tests for every reference operation.

## 5. Add Managed Semantic Search — after 1 and 2

- [x] 5.1 Define exact `search-services/v1` named embedding/ITS profiles in `<state-root>/projects/<workspace-id>/search-services.json`, direct write-only credentials, random secret-version IDs, fixed safe read projection, owner-only permissions and confined atomic writes.
- [x] 5.2 Add reviewed embedding preview/apply with endpoint/address/TLS policy, approved CA ID, secret presence, exact external-disclosure scope and volumes, known or unknown cost, rebuild/reuse impact, fingerprints, acknowledgement, idempotency and locking.
- [x] 5.3 Build the coordinator-owned loopback embedding broker; give BSL Analyzer only its fixed local URL plus non-secret model/dimension and remove inherited proxy, embedding, credential and global analyzer configuration.
- [x] 5.4 Enforce fixed POST shape, per-connect DNS and peer validation, forbidden address classes, Host/SNI and TLS verification, zero redirects, fixed Bearer auth, no user headers, exact vector dimension and bounded JSON.
- [x] 5.5 Bind provider, endpoint HMAC, model, dimension, protocol and secret version to semantic index identity, routing, ledger, diagnostics and reuse.
- [x] 5.6 Enforce probe/query/build request, input-byte, vector, batch, concurrency and elapsed-time maxima; retry only 429/502/503/504 at most twice within original budgets and never replay after restart.
- [x] 5.7 Keep lexical readiness independent, require proved embedding coverage for hybrid readiness, and never silently downgrade a hybrid request to lexical.

## 6. Extend Routing, Lifecycle, Budgets, And Provenance — after 2–5

- [x] 6.1 Route every v2 operation by honest capability, modality, component or reference corpus, surface manifest and compatible promoted index.
- [x] 6.2 Apply existing atomic calls, concurrency, time, query, results, returned bytes and context-capacity admission to every new operation and response class.
- [x] 6.3 Extend HMAC ledger and invocation bindings with operation, modality, surface, embedding/reference identity and canonical-versus-derived manifests without storing queries or content.
- [x] 6.4 Implement one supervised native workspace backend per complete target fingerprint, fixed required-broker proxies, private stable HOME/XDG/runtime socket, trusted peer verification, recorded PID/process group and no cross-project or cross-target reuse; keep reference operations direct stdio.
- [x] 6.5 Add the confined schema-3 complete HOME/XDG environment-bundle layout, exact-build persistent-access fixture, fenced leases, owner-only no-follow staging, fsync/atomic promotion, project quota and previewed deletion of only unreferenced non-current instances or drained serving workspaces.
- [x] 6.6 Create one private copy-on-write serving workspace from the validated promoted bundle per broker identity, never promote derived serving changes back, discard it after drain and prove the current instance remains byte-identical.
- [x] 6.7 Enforce separate resident-backend and call admission caps, queue deadlines, 300-second narrowable idle TTL, 30-second orphan grace, startup/operation/address-space ceilings and visible warm/idle/superseded lifecycle.
- [x] 6.8 Extend cancellation and restart reconciliation across workspace sessions/backends, reference MCP, embedding broker and ITS work: cancel one session without killing peers, reject late output, settle once, drain target work, TERM/KILL only the supervised target group, quarantine staging/serving and replay nothing.
- [x] 6.9 Revalidate final repository evidence and bind v2 and broker identities into reuse while forbidding derived-only findings from satisfying evidence requirements.

## 7. Add Operability — after 4–6

- [x] 7.1 Show complete/partial/incompatible surface coverage, contract drift, lexical/semantic/reference readiness, exact operation routes and actual workspace transport/backend lifecycle in the index workspace.
- [x] 7.2 Add owner-only embedding and ITS profile UI with write-only credentials, redacted live probes, disclosure/cost acknowledgement, reviewed apply, rebuild impact and no secret or raw endpoint return to the browser.
- [x] 7.3 Group v2 profile operations by family, show role restrictions, operation filters, limits and worst-case context reserve.
- [x] 7.4 Extend dispatcher inspection with content-free operation/modality/result-class counts, surface/embedding/reference identities, degradation, exhaustion and reconciliation.
- [x] 7.5 Use existing confirmed background jobs and visible accessible progress for workspace, semantic and reference builds and validation, including queued/retry/cancel/failure states, keyboard operation and focus-safe recovery.
- [x] 7.6 Paginate or require narrowing for large diagnostics, reject stale previews visibly, and keep secrets, source bodies and native responses out of DOM, browser state and accessibility text.
- [x] 7.7 Document complete-surface meaning, excluded non-search tools, embedding endpoint security, reference authority, migration, rebuild, failure recovery and rollback.

## 8. Verify, Migrate, And Synchronize — after 0–7

- [x] 8.1 Add mandatory offline fixture conformance for every approved action, structured/text response version, missing/added action and hostile argument; run live conformance only when the selected executable is explicitly provisioned.
- [x] 8.2 Add lexical-versus-hybrid, embedding-egress-broker DNS/connect/peer/TLS/address/redirect/auth/dimension/budget/retry/cancel failures, semantic rebuild, disclosure/cost and secret-redaction tests.
- [x] 8.3 Add graph/metadata/diagnostics/reference scope, truncation, derived-evidence rejection, canonical excerpt and stale identity tests.
- [x] 8.4 Add exact budget, operation truncation/order, concurrent reservation, process/storage quota, aggregate time/bytes/results, context reserve, cancellation phase, restart and no-replay tests across all families.
- [x] 8.5 Add native process-broker race, identity, trusted-rendezvous, no-fallback, warm reuse, concurrent session, per-call cancellation, peer survival, idle/orphan expiry, supersession, daemon crash and restart-reconciliation tests.
- [x] 8.6 Add reviewed schema-2 to schema-3/machine-contract-1.3 migration tests proving safe aliases, explicit `find_references` rejection, no implicit build, legacy in-flight completion, v2 reuse invalidation and prior-runtime rollback readiness.
- [x] 8.7 Add API, frontend and browser coverage for configuration, coverage matrix, degraded modes, progress, accessibility, stale responses and secret non-disclosure.
- [x] 8.8 Implement target-first, synchronize reusable runtime and scaffold outputs, rebuild packages, run Python/frontend/browser/template/doctor/packaging checks, and require strict OpenSpec validation only after the prerequisite base specifications are present.
