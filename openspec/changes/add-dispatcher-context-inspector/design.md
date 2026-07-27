## Context

The current dispatcher projection already contains role configuration, bounded invocation rows, queue items, circuit state, and leases. The graph renders role slots and invocation identifiers, but `PipelineDispatcher` stores only a selected circuit plus a string initiator key. Slot and invocation clicks therefore open the same generic circuit panel. The queue is the only graph entity with dedicated detail content.

Invocation rows currently retain identity, run, phase, role, work unit, slot, status, and timestamps. The projection derives visible slots only from those recent rows, so a configured slot without an invocation does not exist as an inspectable entity. Agent execution does not persist immutable per-invocation context identity, an invocation-correlated safe error summary, or a result reference, and the closed workflow event catalog has no invocation lifecycle events. Existing run events, node-result records, and private bounded logs remain the established observability stores.

### Locked Decisions

- Use one contextual inspector component; do not create separate pages or independent panels for every node type.
- Keep repository artifacts canonical and treat invocation details as operational projections only.
- Reuse the existing dispatcher projection, immutable execution snapshot, event store, result store, and journal instead of duplicating their content.
- Add one bounded read-only inspection route accepting either a complete slot selector or an invocation identifier; role and queue summaries continue to come from the dispatcher projection.
- Project server-owned logical slots and idle reason codes; do not synthesize slot identity or waiting reasons in the browser.
- Persist only safe immutable invocation identity, lifecycle correlation, and terminal metadata in SQLite, not prompts, complete model output, private reasoning, or duplicate logs.
- Use `invocation.started` and `invocation.finished` plus an SQLite outbox for recoverable cross-store event delivery; the invocation row remains terminal-state truth.
- Represent results only by an opaque `node-result` identifier resolved against the existing dispatcher proposal store; never store or accept a result path.
- Render all externally produced text literally and pass persisted or returned errors through the existing redaction boundary.
- Preserve graph viewport, exact selection, keyboard focus, unsaved input, and unrelated panel state across incremental updates.
- Use existing React state and browser APIs for detail loading and cancellation; add no client state or routing dependency.

## Goals / Non-Goals

**Goals:**

- Let an operator inspect a role, slot, invocation, queue, DIF, or MRQ directly from the graph.
- Explain both active work and idle/waiting state using server-owned state.
- Show immutable execution identity, timing, safe outcome, observable events, and navigable related entities for a selected invocation.
- Support bounded history and explicit partial-history markers for omitted or legacy data.
- Keep the inspector usable by keyboard and stable during SSE or polling reconciliation.
- Close the existing polling-fallback contract gap for both the dispatcher projection and selected non-terminal detail.

**Non-Goals:**

- Expose chain-of-thought, unrestricted process output, credentials, or unredacted provider responses.
- Add commands, retry controls, cancellation controls, or mutations to the invocation-detail route.
- Replace the existing journal, registry screens, circuit action panel, or dispatcher projection.
- Backfill information that was never recorded for historical invocations.
- Introduce a new event bus, log store, state-management library, or database.
- Persist inspector selection across a full page reload.

## Decisions

### 1. Represent every inspectable target explicitly

The frontend will use this closed selection shape:

```ts
type InspectorSelection =
  | { kind: 'circuit'; circuitId: CircuitId }
  | { kind: 'role'; circuitId: CircuitId; runId?: string; phaseId: string; roleId: string }
  | { kind: 'slot'; circuitId: CircuitId; runId?: string; phaseId: string; roleId: string; slotId: string }
  | { kind: 'invocation'; circuitId: CircuitId; runId: string; phaseId: string; roleId: string; slotId: string; invocationId: string }
  | { kind: 'queue'; circuitId: CircuitId; queueId: string }
  | { kind: 'item'; circuitId: CircuitId; itemType: 'dif' | 'mrq'; itemId: string };
```

The `circuit` variant preserves current stage/navigation behavior and every child carries its parent circuit so actions and stale rendering remain unambiguous. The selection contains identifiers only; the initiating element is captured separately as a connected element reference for focus restoration.

The selection and an immutable `lastResolvedSelectionView` remain outside the live projection and are cleared on project change. A found entity refreshes the last-resolved view. A disappeared entity keeps that snapshot, receives a stale badge, and exposes no entity mutation; parent circuit actions continue to use current circuit state.

Alternative: keep parsing colon-delimited initiator strings. Rejected because the current key loses entity type and makes parent actions, stale state, and focus restoration ambiguous.

### 2. Project all configured logical slots

Each agent-role projection will include its optional active/latest `run_id` and exactly `configured_slots` stable slot records:

```json
{
  "slot_id": "analyze-dif:analyzer:1",
  "display_label": "Analyzer 1",
  "state": "running|idle",
  "idle_reason_code": "none|work_not_requested|waiting_for_prerequisite|waiting_for_dispatch|queue_empty|phase_complete|environment_unavailable",
  "current_invocation_id": null
}
```

Slot IDs use the existing dispatcher allocation convention `<phase_id>:<role_id>:<one-based index>` so projected slots join existing and legacy invocation history; a shorter localized label is presentation only. The server applies idle-reason precedence in this order: running invocation, unavailable environment, incomplete prerequisite, queued work waiting for phase dispatch, completed phase, no requested work, then empty queue. Before the first dispatcher run, `run_id` is absent and configured slots use `work_not_requested`. The browser translates only the stable code and never invents a reason.

Legacy projections without `slots` remain readable: the client shows invocation-derived slots and marks configured-but-unprojected slots unavailable rather than assigning identities itself.

### 3. Use one inspector with entity-specific sections

`DispatcherInspector` will retain existing circuit controls and add contextual sections. A role shows execution configuration, counters, and all projected slots. A slot shows its assignment reason and bounded history. An invocation shows immutable identity, work unit, timing, safe terminal summary, bounded events, and related navigation. Queues and items show projection data and exact registry navigation.

The inspector is a non-modal complementary region labelled by a focusable heading (`tabIndex=-1`). Opening a different selection focuses the heading once; projection/detail refreshes do not steal focus. `Escape` or Close restores a captured connected initiator, then its parent graph node, then the parent circuit navigation control.

Role, slot, invocation, queue, DIF, and MRQ activators are sibling native buttons, never nested buttons. Mouse, Enter, and Space produce exactly one activation callback with an unambiguous accessible name.

Alternative: one modal per entity. Rejected because it duplicates close/focus/error/loading behavior and obscures graph context.

### 4. Add one bounded dispatcher-inspection route

The service will expose:

`GET /api/v1/projects/{project_id}/dispatcher/inspect`

It accepts exactly one selector:

- `kind=invocation&invocation_id=<uuid>`;
- `kind=slot&phase_id=<id>&role_id=<id>&slot_id=<id>[&run_id=<uuid>]`.

Invocation selection resolves the slot scope from the invocation row and rejects extra selector fields. Slot selection works without any current invocation and without `run_id`; in that never-run case it returns the configured idle slot with `work_not_requested`, empty unavailable history, and no events. When `run_id` is supplied, both selectors return the selected slot, the current/selected invocation when present, same-run same-slot history, and section availability. Invocation responses additionally return bounded correlated events and a safe node-result summary.

History defaults to 20 and is capped at 100, ordered by `(created_at DESC, invocation_id DESC)`. Its opaque base64url cursor encodes the full slot scope plus the last `(created_at, invocation_id)` key and is rejected with `422` when malformed or cross-scoped. A composite index covers `(run_id, phase_id, role_id, slot_id, created_at, invocation_id)`.

Events default to 50 and are capped at 200, ordered by monotonic `sequence ASC`. Their independent opaque base64url cursor encodes `{project_id,run_id,invocation_id,last_sequence}` and selects only `sequence > last_sequence`; selector mismatch is rejected with `422`. The response exposes `history_next_cursor`, `history_truncated`, `event_next_cursor`, `events_truncated`, and per-section `available|unavailable|truncated` markers. It reads at most the retained per-run snapshot, not the global 10,000-event file.

Unknown and foreign-project selectors return the same `404` body. Invalid selectors, limits, or cursors return `422`. An unavailable operational store returns a generic `503`. No response contains credentials, effective prompts, private reasoning, unrestricted provider output, or arbitrary file content.

Each response includes server `observed_at`. Derived duration is `max(0, finished_at - created_at)` for terminal rows and `max(0, observed_at - created_at)` for running rows; it is unavailable when a required legacy timestamp is missing or invalid.

Alternative: put all details into every dispatcher projection. Rejected because result and event payloads would increase every reconciliation response when no inspector is open.

### 5. Persist immutable invocation identity before execution

The dispatcher builds and verifies the actual derived work-unit context manifest before allocating the invocation row. `start_invocation` atomically stores:

- `execution_snapshot_fingerprint`;
- resolved `profile_id`;
- `context_manifest_fingerprint`;
- existing run, phase, role, slot, and work-unit identity;
- `created_at`.

The detail resolver obtains model, reasoning effort, environment preset, and source-generation bindings only by joining `profile_id` and phase/role against the immutable run snapshot whose fingerprint matches the row. A missing or mismatched snapshot makes execution identity unavailable; it never falls back to current policy or the run's initial work unit.

This proves distinct derived work units inside one dispatcher run without persisting the complete prompt or context manifest in SQLite.

### 6. Define recoverable lifecycle events and terminalization

The closed event catalog gains:

- `invocation.started`, with transition key `invocation:<id>:started`, `invocation_status=running`, invocation/run/phase/role/slot/work-unit identity, and timestamp;
- `invocation.finished`, with transition key `invocation:<id>:finished`, `invocation_status` in `completed|failed|cancelled|interrupted`, terminal timestamp, duration, optional safe error, and optional opaque result reference.

Invocation insertion or terminalization and insertion of its outbox record occur in one SQLite transaction. The outbox has a unique transition key and records delivered sequence. Delivery to the existing EventStore is retryable; duplicate JSONL delivery after a crash is tolerated and detail responses deduplicate by transition key. Startup and ordinary reconciliation drain pending outbox rows.

Invocation lifecycle delivery preserves the enclosing run snapshot status. The EventStore updates run status only from run-level lifecycle fields, and only `run.finished` may set a terminal run status; an invocation event never places its `invocation_status` into the snapshot's run `status`.

All terminal paths use one idempotent terminalization operation: successful validation, validation/provider failure, `InterruptedError`, user cancellation, soft stop, lease loss, orphan/process recovery, and service restart. The terminal invocation row is truth. If delivery remains missing or its retained event was evicted, the inspector reports an event gap without weakening the terminal summary.

`error_code` is one of `validation_error`, `provider_error`, `provider_blocked`, `provider_timeout`, `tool_error`, `cancelled`, `interrupted`, `environment_unavailable`, or `internal_error`; every value also matches `[a-z0-9_.-]{1,64}`. Unknown causes map to `internal_error`. `error_summary` is redacted before SQLite persistence and then truncated at a valid UTF-8 boundary to 4096 bytes. Provider text is never used as an error code.

### 7. Use opaque node-result references

`result_ref` has the fixed API shape `{"kind":"node-result","id":"<opaque sha256 key>"}` and never contains a path. A shared result-name mapping derives the existing node-result key from thread, phase, role, and work unit:

- `analyze-dif:<work_unit_id>`;
- `form-mrq:<work_unit_id>` for groupers and `form-mrq:coordinate` for the coordinator;
- `classify-batches:<work_unit_id>`;
- `research-target:<work_unit_id>`.

Successful invocation terminalization may store the deterministic reference before the graph callback publishes the node result. The detail route resolves only that exact key in the selected project's existing dispatcher proposal store and exposes `result_available=false` until publication. When available, it returns at most a 4096-byte redacted allowlisted summary: classification/confidence/counts for analysis, semantic key/title/counts for grouping, group/batch counts for coordination/classification, or decision/risk/counts for target research. Missing/orphan references remain unavailable and never trigger filesystem reads.

### 8. Use local keyed detail state with race protection

The client keeps a minimal detail cache keyed by `projectId + selector identity`. It uses `AbortController` plus a monotonically increasing request token so a late response for selection A cannot replace selection B. Project change aborts requests and clears selection, cache, and last-resolved data.

The stream hook exposes connection state and the last accepted workflow event. Detail refresh occurs only when selection changes, a lifecycle event matches the selected invocation identifier, or it matches the selected slot's phase/role/slot and its run when one was already selected, the user requests another history/event page, or the low-frequency fallback applies. For a never-run slot, the first matching `invocation.started` supplies the new run scope. Thus a selected idle slot becomes assigned immediately without losing selection or scroll. Unrelated progress events do not refetch details. Page merges retain history cursor, event cursor, and inspector scroll.

When SSE is degraded, one single-flight five-second poll refreshes the projection and selected non-terminal detail. It stops when SSE reopens. While SSE is connected, one 30-second reconciliation confirms active/terminal projection state as required by the base workspace contract. No parallel per-entity polling loops are created.

### 9. Define exact journal and registry navigation

Workspace callbacks carry typed targets:

- journal `{runId, invocationId}` initializes the exact text filter and expands the matching run/event group;
- registry `{name: 'diff-inventory'|'mrq', itemId}` selects the registry and requests the exact item.

The existing registry endpoint gains an optional exact `item_id` filter over the registry's declared stable identifier; it does not accept arbitrary field names or expressions. Journal and registry screens consume the target once, preserve it while open, and expose a clear-filter action. Missing or superseded targets show a not-found message without silently displaying an unrelated record.

## Risks / Trade-offs

- [Legacy rows lack correlation metadata] → Return explicit availability flags and never fabricate profile, result, or error details.
- [Event retention may omit early lifecycle events] → Report truncation and keep immutable invocation summary fields independently available.
- [Error text may contain secrets or HTML] → Redact before persistence and response, bound its size, and render it as text.
- [Invocation history can grow without bound] → Enforce server-side limits and cursor pagination.
- [Selected entities can disappear after reconciliation] → Preserve the selection as stale until the user closes it.
- [One inspector can become crowded] → Start with compact sections and add tabs only after demonstrated need.
- [SQLite and JSONL cannot share one transaction] → Use an idempotent SQLite outbox and make terminal rows authoritative.
- [Inspector refresh could amplify event load] → Refresh only on correlated lifecycle events and use single-flight fallback intervals.

## Migration Plan

1. Create an owner-only, WAL-consistent backup of each operational SQLite database with the SQLite online backup API before schema migration.
2. Change every invocation insert to an explicit target-column list, then add nullable `execution_snapshot_fingerprint`, `profile_id`, `context_manifest_fingerprint`, `finished_at`, `error_code`, `error_summary`, and `result_ref` columns, the history index, and the invocation-event outbox.
3. Deploy terminalization, outbox draining, logical-slot projection, and the bounded inspection route before enabling the new client.
4. Introduce typed selection, contextual sections, exact navigation, fallback reconciliation, and focus behavior.
5. Frontend-only rollback serves the previous static bundle and safely ignores additive fields and events.
6. Binary backend rollback to code that uses positional invocation inserts is unsupported against the migrated database; restore the pre-migration operational backup first, accepting loss of post-upgrade operational history. Repository artifacts and CLI workflows are unaffected.

## Open Questions

None. The first delivery intentionally links to the existing journal for complete retained logs instead of embedding or duplicating them in the inspector, and intentionally does not persist selection across a full page reload.
