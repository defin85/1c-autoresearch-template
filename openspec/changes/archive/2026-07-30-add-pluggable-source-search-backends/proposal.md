## Why

The generated workflow currently treats `rlm-tools-bsl` as both the only index lifecycle implementation and the implied source-search implementation. Its executable name, version probe, build command, readiness probe, and index identity are embedded directly in the runtime. This prevents a project from using BSL Analyzer or another source-aware engine for capabilities that the current engine does not provide, and it gives no stable contract for explaining which engine produced a navigation result.

Search output must remain navigation evidence rather than canonical research evidence. A backend hit can help select a file or symbol, but only a repository-confined file from the active immutable source generation, re-read and fingerprinted by the coordinator, can enter an agent context or published result.

## What Changes

- Separate index lifecycle capabilities from source-query capabilities behind a fixed server-owned backend catalog.
- Allow a project to configure an ordered set of approved backends and deterministic routing by required capability.
- Add `rlm-tools-bsl` and the fixed `itrous/bsl-analyzer` adapter using its `bsl-analyzer` launcher, machine-readable contract 1.x, and workspace MCP surface; do not accept arbitrary executable templates.
- Give every built index an engine, adapter, component, source-generation, source-fingerprint, capability, and version identity.
- Normalize search results into bounded navigation hits with backend provenance while preserving backend-native scores only as opaque diagnostics.
- Expose one bounded provider-neutral `source_search` tool so a worker can decide whether, when, and which supported search operation it needs without selecting or addressing a backend.
- Re-resolve every selected hit against the active canonical source generation before it can become evidence or agent context.
- Let user-scope agent profiles configure bounded search operations and per-invocation call, concurrency, deadline, aggregate execution-time, query-byte, result-count, and returned-byte budgets; freeze the resolved values in the invocation snapshot.
- Reserve enough model context capacity for the maximum configured dynamic tool transcript before dispatch and retain a content-free protected query and result provenance ledger.
- Support explicit fallback only for backend unavailability or unsupported capability; do not silently combine conflicting results.
- Expose per-component backend readiness, routing, validation, failure, and fallback diagnostics in the existing workspace.
- Preserve prior immutable research generations and treat indexes as rebuildable operational state rather than canonical repository authority.

## Impact

- Affected specifications: `repository-owned-generated-research-workflow`, `managed-autoresearch-workspace`.
- Expected implementation areas: indexing configuration and state, backend probes, build and query adapters, common agent tool transport, dynamic search budgets and provenance, workflow readiness, workspace diagnostics, tests, scaffold synchronization, and operator documentation.
- Existing projects continue to use `rlm-tools-bsl` after migration until another approved backend is explicitly configured.
- The service probes installed tools but never installs, updates, authenticates, or rewrites global `rlm-tools-bsl` or `bsl-analyzer` configuration.
- No index database, backend score, symbol identity, or search hit becomes a canonical research entity.
