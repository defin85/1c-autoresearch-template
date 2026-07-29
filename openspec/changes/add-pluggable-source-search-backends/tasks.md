## 1. Define The Backend Contract

- [x] 1.1 Add schema-version-2 indexing configuration with an ordered approved-backend list and closed capability routes.
- [x] 1.2 Define fixed adapter, capability, pre-build target-identity, promoted instance-identity, normalized-hit, routing-decision, and bounded diagnostic schemas.
- [x] 1.3 Keep component discovery and canonical file navigation common and independent from backend adapters.
- [x] 1.4 Include an opaque repository-instance fingerprint in every state key and distinguish backend `component_relative_path` from existing logical evidence paths.
- [x] 1.5 Reject executable paths, shell templates, arbitrary arguments, unknown adapters, unknown capabilities, duplicate IDs or routes, missing required routes, unknown fields, implicit defaults, and unbounded limits.
- [x] 1.6 Add typed indexing-configuration preview/apply with normalized file and impact preview, expected file and plan fingerprints, idempotency, mutation locking, and atomic replacement.

## 2. Adapt Index Lifecycle

- [x] 2.1 Move the existing `rlm-tools-bsl` probe, version, build, validation, and cancellation behavior behind the fixed adapter contract.
- [x] 2.2 Adopt compatible existing `rlm-tools-bsl` state without an unnecessary rebuild and mark incomplete identities stale.
- [x] 2.3 Add the fixed `itrous/bsl-analyzer` launcher, contract-major-1, exact-build-version, direct-stdio workspace MCP adapter over a verified private component mirror using only machine-declared search, graph, symbol, status, and schema surfaces.
- [x] 2.4 Key build coordination by repository instance plus target identity and key ready routing and reuse by promoted instance identity so several configured indexes and repository clones cannot collide.
- [x] 2.5 Add a target-keyed fenced lease, private staging, exact source and index manifest validation, atomic immutable instance promotion and pointer switch, compatible-old-ready retention, and cleanup or quarantine for every build.
- [x] 2.6 Bound process time, output bytes, result counts, state writes, cancellation, and concurrent rebuild or validation races for every adapter operation.
- [x] 2.7 Probe installed BSL Analyzer capabilities without installing, updating, authenticating, enabling embeddings, configuring shared services, or mutating global tool state.

## 3. Route And Promote Search Results

- [x] 3.1 Resolve exactly one backend from required capability, configured order, current probe, representation support, and ready compatible index.
- [x] 3.2 Permit fallback only for unavailable, unsupported, or non-ready backends and record the exact typed reason.
- [x] 3.3 Normalize text, symbol, reference, caller, callee, and metadata-navigation results with `component_relative_path` and without comparing backend-native scores.
- [x] 3.4 Re-resolve selected hits into existing configuration, extension, or external-artifact logical evidence paths against the active source generation, component root, canonical file, and fingerprint.
- [x] 3.5 Bind route, capability, adapter, and index fingerprints into selection provenance and compatible-result reuse.
- [x] 3.6 Fail closed on stale, escaping, unknown, conflicting, oversized, or untraceable hits without publishing partial results.

## 4. Add Bounded Worker Search

- [x] 4.1 Define `source-search-policy/v1` and one provider-neutral `source_search` schema with closed operations and no backend selector or backend-native query syntax.
- [x] 4.2 Add a closed optional `source_search` object to user-scope agent profiles with configurable operations and per-call and aggregate limits bounded by the locked server maxima; freeze it in the execution snapshot.
- [x] 4.3 Reserve worst-case query, canonical response, and tool-framing capacity under the invocation model estimator before slot allocation without relabelling it as prepared-input use.
- [x] 4.4 Transport the common tool through an authenticated per-invocation MCP bridge for every runtime enabled at delivery, binding project, invocation, policy, expiry, terminal state, and replay-protected call ID.
- [x] 4.5 Atomically reserve ordinal, in-flight capacity, calls, query bytes, requested results and returned bytes, and aggregate execution time before a backend starts; settle each reservation exactly once.
- [x] 4.6 Return only canonically verified paths, fingerprints, navigation fields, closed hit kinds, and bounded canonical excerpts; reject raw backend snippets and unbound content.
- [x] 4.7 Persist a content-free ordered dynamic-search ledger using a project-scoped versioned HMAC for queries plus scope, route, index, result-manifest, count, byte, duration, and terminal status fields.
- [x] 4.8 Revalidate final cited evidence and bind current policy, scope, route, adapter, index, canonical result manifests, and source identities into reuse without replaying or comparing volatile ledger timing.
- [x] 4.9 Close admission and reconcile every reservation and process exactly once on cancellation, timeout, lease loss, or restart; discard late output and never replay a search automatically.
- [x] 4.10 Keep dynamic reserve and actual search accounting separate from prepared-context estimates, provider-managed transcript, measured-token, and monetary telemetry.
- [x] 4.11 Return typed bounded tool failures for authentication, replay, terminal invocation, invalid scope, unsupported operation, unavailable route, stale index, timeout, and exhausted budgets.

## 5. Add Operability

- [x] 5.1 Expose backend and component readiness, capabilities, versions, generation binding, route priority, degraded readiness, validation time, and safe failure through the existing index workspace.
- [x] 5.2 Show profile-configured limits, capacity reserve, reserved and in-flight calls, admission state, actual bounded usage, selected backend, fallback cause, ledger completeness, reconciliation, and ledger fingerprint without exposing queries, raw output, source snippets, credentials, or index content.
- [x] 5.3 Add explicit rebuild and validation actions using existing server progress, confirmation, idempotency, and stale-fingerprint controls.
- [x] 5.4 Bind ledger cursors to project, invocation, policy, and ledger fingerprints and require a new invocation after profile or limit changes.
- [x] 5.5 Document migration, typed configuration mutation, rebuild, worker search, profile limits, HMAC rotation, limit exhaustion, fallback, disk ownership, exact v1 downgrade, and rollback behavior.

## 6. Verify And Synchronize

- [x] 6.1 Add unit and integration tests for both fixed adapters, BSL contract feature detection, private-mirror manifest equality, canonical-tree immutability and absence of canonical `.build`, capability routing, degraded readiness, empty-success behavior, fallback, cancellation, output bounds, clone isolation, and state identity.
- [x] 6.2 Add exact-limit and race tests for capacity admission, concurrent atomic reservation including aggregate-time reservation and release, context overflow, replay, cross-project and cross-invocation calls, expired capability, terminal invocation, and HMAC confidentiality.
- [x] 6.3 Add cancellation and crash tests before backend start, during output, after result before settlement, before and after index promotion, plus late completion and no automatic replay.
- [x] 6.4 Add stale-generation, changed-component, configuration/extension/external path conversion, escaping-path, conflicting-hit, canonical-promotion, dynamic provenance, final evidence revalidation, HMAC rotation, and reuse-invalidation tests.
- [x] 6.5 Add repeated builds of one target with distinct promoted instance fingerprints, atomic current-pointer switching, simultaneous rebuild, validation-versus-promotion, route mutation, typed preview/apply, stale apply, idempotency, v1 upgrade, representable downgrade, and blocked downgrade tests.
- [x] 6.6 Add frontend and browser coverage for multiple backends, mixed and degraded readiness, profile search limits, capacity reserve, reserved/in-flight/closed states, failure, fallback, exhaustion, reconciliation, cursor isolation, rebuild progress, accessibility, and stale responses.
- [x] 6.7 Prove existing schema-version-1 `rlm-tools-bsl` projects remain usable and no index data, raw or unsalted query identity, dynamic source content, capability secret, or tool transcript enters repository artifacts, APIs, or logs.
- [x] 6.8 Implement target-first, synchronize reusable runtime and scaffold outputs, rebuild packages, and run Python, frontend, browser, template, doctor, and strict OpenSpec checks.

## Разрывы ревью

- [x] 7.1 Isolate every backend process from user home, XDG configuration, cache, data, credentials, and inherited service configuration.
- [x] 7.2 Require versioned structured BSL Analyzer replies before readiness or navigation data can be consumed.
- [x] 7.3 Expose route degradation, bounded failure recovery, and stale-or-missing readiness reasons in index diagnostics and the workspace.
- [x] 7.4 Show the profile's worst-case dynamic reserve and maximum remaining prepared-input headroom.
- [x] 7.5 Reject backend databases, HMAC keys, SQLite state, private mirrors, and other operational index payloads from release artifacts.
- [x] 7.6 Bind adapter capability, target, promoted instance, routing, and reuse identity to the installed executable content fingerprint.
- [x] 7.7 Keep status and explicit validation filesystem-read-only; create operational roots only for build or mutation paths.
- [x] 7.8 Bound the aggregate promoted index payload and quarantine oversized adapter output before publication.
