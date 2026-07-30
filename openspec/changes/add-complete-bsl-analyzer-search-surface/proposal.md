## Why

The fixed `bsl-analyzer` adapter currently advertises six generic capabilities, but it exposes only a narrow projection of the BSL Analyzer machine contract. Semantic code search is silently degraded to lexical search because the sanitized process receives no managed embedding configuration. `find_references` calls `symbol_info`, which returns a consolidated card and usage summary rather than a complete reference list. Graph navigation is limited to callers and callees, metadata navigation to a filtered tree, and diagnostics, source-bearing graph actions, forms, platform reference search, syntax help, and ITS expert help are unavailable to workers.

The runtime therefore overstates some capabilities while hiding other deterministic search surfaces. Complete support must mean an exact, versioned and testable projection of every search, navigation, metadata, diagnostics, and reference action declared by the approved BSL Analyzer machine contract, without exposing execution, query mutation, debugging, event-log, or arbitrary MCP access.

## What Changes

- Require `add-pluggable-source-search-backends` to be implemented and archived first; this change extends its backend, routing, evidence, budget, and operability contracts and cannot be archived independently before that prerequisite.
- Introduce `source-search-tool/v2` with typed provider-neutral operation families for the complete approved BSL Analyzer workspace and reference search surfaces.
- Cover workspace hybrid code search, symbol cards, graph overview/schema/resolve/node/source/neighbors/callers/callees, metadata info/tree/object/form, and diagnostics catalog/schema/file/workspace.
- Cover reference documentation `find_docs`, `search_docs`, `syntax_help`, and `its_help`.
- Require BSL Analyzer machine contract `1.3` for complete-surface readiness. `syntax_help` MUST publish an MCP `outputSchema` and return native `structuredContent` schema `1` with the closed kinds `type`, `method`, `global_function`, and `keyword`; the compatibility text block remains non-authoritative and MUST NOT be parsed.
- Replace the false `symbol-references` mapping with honest operation-specific capability declarations; no capability is ready unless its native action and normalized result contract are proven.
- Add an owner-only managed embedding profile for an OpenAI-compatible local or remote endpoint, model, dimension, provider settings, and write-only credential so semantic code search can be enabled without inheriting user environment or storing credentials in the repository.
- Put every remote embedding request through a coordinator-owned loopback egress broker. BSL Analyzer receives only the broker URL and no remote credential; the broker owns address validation, TLS, redirects, request/response limits, retries, cancellation, disclosure accounting, and the upstream secret.
- Add an independently reviewed owner-only ITS profile for the fixed `https://code.1c.ai` service. `its_help` remains unavailable until its write-only `NAPARNIK_TOKEN`, external-disclosure acknowledgement, and bounded policy are valid.
- Represent backend-derived symbol, graph, metadata, diagnostics, and documentation facts as bounded navigation findings. Only coordinator-re-read repository files can become canonical evidence.
- Add an exact search-surface manifest derived from `bsl-analyzer contract` plus MCP `tools/list`; unknown, missing, or changed required actions make complete-surface readiness incompatible rather than silently partial.
- Use the native BSL Analyzer process broker for the heavy `workspace` profile: one coordinator-owned resident backend per complete target identity serves bounded MCP sessions from multiple workers, while the lightweight `reference` profile remains direct `stdio`. Silent direct-stdio fallback is incompatible with complete-surface readiness.
- Require the selected BSL Analyzer build to add a supervised required-broker mode before delivery because the current native proxy may auto-launch a detached backend and fall back to direct `stdio`; the workflow MUST NOT emulate or infer that missing lifecycle contract.
- Extend index, invocation, and workspace diagnostics with lexical/semantic modality readiness, search-surface coverage, embedding identity, degraded causes, budgets, and unsupported contract drift without exposing queries, secrets, raw backend traffic, or unrestricted snippets.
- Preserve the existing deterministic backend routing, component scope, private mirrors, operational indexes, canonical evidence promotion, per-invocation budgets, HMAC ledger, and agent-runtime-neutral MCP bridge.

## Impact

- Affected specifications: `repository-owned-generated-research-workflow`, `managed-autoresearch-workspace`.
- Expected implementation areas: BSL Analyzer probing, native broker lifecycle and MCP adapters, capability vocabulary and routing, source-search policy/schema v2, canonical normalization, managed embedding profiles and secrets, index identity/readiness, dispatcher inspection, workspace configuration and diagnostics, tests, scaffold synchronization, and operator documentation.
- Existing `source-search-tool/v1` invocations remain readable but cannot claim complete BSL Analyzer coverage or be reused by v2 policies.
- Delivery is pinned to machine contract `1.3` and the approved search-surface schemas, not to a product version number. The exact executable build and fingerprint are recorded at provisioning and migration time; older machine contracts require reviewed migration and cannot claim complete-surface readiness.
- Existing lexical indexes remain usable for explicitly lexical routes. Enabling semantic search requires an explicit reviewed embedding profile and rebuild; it never occurs implicitly.
- Offline contract fixtures are the mandatory release gate. Live-binary conformance is an explicit opt-in environment check unless CI provisions the exact approved binary.
- No arbitrary MCP tool, executable, environment variable, live query execution, debugger operation, event-log access, or canonical research authority is introduced.
