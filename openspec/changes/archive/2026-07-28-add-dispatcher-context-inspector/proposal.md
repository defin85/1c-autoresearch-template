## Why

The dispatcher graph exposes aggregate role state and compact invocation rows, but selecting an analyzer, grouper, slot, or invocation opens only the generic circuit panel. Operators cannot answer which profile ran, what work unit was assigned, why a slot is waiting, how long an invocation ran, or where its result and observable events are located without manually searching the journal.

## What Changes

- Add one contextual dispatcher inspector for roles, slots, invocations, queues, DIF items, and MRQ items.
- Make graph selection preserve the exact selected entity instead of reducing every click to a circuit identifier.
- Project every configured logical agent slot, including idle slots with a server-owned reason code, instead of deriving slots only from recent invocations.
- Expose one bounded read-only inspection route for complete slot selectors and invocation identifiers, derived from operational state, immutable execution snapshots, existing events, and existing node-result records.
- Correlate invocation lifecycle events, immutable per-invocation identity, safe terminal summaries, and opaque result references with `invocation_id`.
- Preserve keyboard focus, current graph viewport, inspector selection, and unrelated UI state during live projection updates.
- Add exact filtered navigation from inspected invocations and items to the existing journal and registries.
- Keep private model reasoning unavailable and apply existing redaction and text-only rendering to errors, results, and events.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `managed-autoresearch-workspace`: Add entity-specific dispatcher inspection and invocation observability without creating a second canonical state store.

## Impact

- Extends dispatcher operational state, logical-slot projection, event outbox, and event payloads with additive invocation correlation and terminal-summary fields.
- Adds one bounded read-only dispatcher-inspection API route and exact optional filtering to existing registry reads.
- Replaces the circuit-only graph selection key with a typed contextual selection in the React workspace.
- Extends dispatcher API, projection, SSE fallback reconciliation, frontend navigation, accessibility, migration, recovery, and browser acceptance tests.
- Does not change repository-owned DIF, MRQ, evidence, or workflow artifact formats and adds no external dependency.
