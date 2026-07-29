## Why

Agent invocations already bind work units, allowed paths, execution snapshots, and context fingerprints, but each workflow stage assembles its payload differently. Only MRQ consolidation proves its input against a provider context limit. Operators can see that an invocation used a context fingerprint, but cannot tell what was selected, where it came from, how much prepared-input capacity it was estimated to require, or what the stage policy excluded. This makes failures difficult to diagnose and makes reuse safety depend on stage-specific conventions.

## What Changes

- Add one versioned context-envelope contract for every agent invocation.
- Keep stage-specific selection strategies while requiring the same subject, facts, evidence, related-subject, provenance, budget, and binding sections.
- Require a validated provider context-window limit and a versioned estimator before every agent call, accounting for the complete rendered input and a fixed output reserve.
- Replace the monolithic stage-3 coordinator response with bounded hierarchical comparison and reduction calls so complete consolidation never depends on returning the whole MRQ graph in one 4096-token response reserve.
- Record the origin and selection reason of every included context item and bounded summaries of candidates excluded by the stage policy; budget validation never truncates the selected payload.
- Bind result reuse to the context-envelope version, estimator version, selection-policy version, and envelope fingerprint.
- Expose safe context-budget and provenance diagnostics through existing dispatcher inspection without returning prompts, private reasoning, credentials, unrestricted provider output, or arbitrary file content.
- Emit comparable decision diagnostics for deterministic non-agent paths without fabricating an agent invocation.
- Record successful reuse and preflight failures against existing phase-work state without fabricating an invocation.
- Preserve existing immutable runs as readable legacy evidence; do not rewrite or silently reinterpret them.
- Retire the unused legacy combined context-compilation path after all active stage compilers use the common envelope builder.

## Impact

- Affected specifications: `repository-owned-generated-research-workflow`, `managed-autoresearch-workspace`.
- Expected implementation areas: agent runtime, stage context compilers, capacity estimation, result-cache bindings, dispatcher projection and inspector, tests, generated scaffold synchronization, and operator documentation.
- No new canonical research entity, registry, generation pointer, or approval step is introduced.
- Existing stage boundaries, explicit starts, DIF/MRQ identities, and stage-specific context-selection algorithms remain unchanged.
- Context budgeting means model-context capacity, not monetary cost forecasting.
