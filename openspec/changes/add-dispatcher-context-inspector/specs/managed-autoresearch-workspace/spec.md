## ADDED Requirements

### Requirement: Inspect dispatcher entities in context
The managed workspace SHALL provide one contextual inspector for dispatcher roles, slots, invocations, queues, DIF items, and MRQ items, SHALL preserve the exact selected entity across incremental reconciliation, and SHALL derive displayed state from the dispatcher projection and bounded server-owned detail reads.

#### Scenario: Inspect a circuit
- **WHEN** a user activates a stage node or circuit navigation control
- **THEN** the inspector shows the existing circuit summary and actions without requiring a child entity selection

#### Scenario: Inspect an agent role
- **WHEN** a user activates an analyzer, coordinator, grouper, classifier, or researcher role
- **THEN** the inspector shows its phase, profile, model, reasoning effort, configured concurrency, environment state, state counters, and every server-projected configured slot

#### Scenario: Inspect an assigned slot
- **WHEN** a user activates a slot with a current or previous invocation
- **THEN** the inspector identifies the slot, work assignment, latest state, and bounded invocation history without replacing the graph or opening a separate page

#### Scenario: Inspect an idle slot
- **WHEN** a user activates a slot without assigned work
- **THEN** the inspector shows the stable slot identity and translates its server-owned idle reason code without inventing an assignment or reason in the browser

#### Scenario: Inspect a role or slot before the first run
- **WHEN** a configured role or slot is activated before any dispatcher run identifier exists
- **THEN** the inspector uses projection data, shows `work_not_requested` for the idle slot, marks operational history unavailable, and does not require or fabricate a run identifier

#### Scenario: Inspect a queue aggregate
- **WHEN** a user activates a dispatcher queue node
- **THEN** the inspector shows the complete aggregate count, bounded current window, omission state, and independently activatable visible items

#### Scenario: Inspect a queue item
- **WHEN** a user activates a DIF or MRQ item from a graph queue or contextual list
- **THEN** the inspector shows the available item evidence and state and provides exact navigation to the corresponding filtered registry record

#### Scenario: Navigate from an invocation
- **WHEN** a user follows the journal action for an inspected invocation
- **THEN** the workspace opens the existing journal with the invocation and run filter initialized and the matching group expanded when retained data exists

#### Scenario: Selected entity disappears during reconciliation
- **WHEN** an incremental update no longer contains the selected role, slot, invocation, or item
- **THEN** the inspector preserves its last resolved identity and content, marks the entity stale or unavailable, disables entity mutations, retains current parent-circuit actions, and does not silently select another entity

### Requirement: Expose bounded invocation details
The service SHALL expose one project-scoped read-only dispatcher inspection resource accepting either an invocation identifier or a phase, role, and canonical slot selector with an optional run identifier and returning slot assignment, invocation identity, work unit, immutable per-invocation execution identity, timestamps, derived duration, safe terminal summary, optional opaque node-result reference and summary, bounded same-run same-slot history, and bounded correlated observable events.

#### Scenario: Read a current invocation
- **WHEN** the browser requests an invocation belonging to the selected project
- **THEN** the service returns available operational fields, immutable profile and context fingerprints, history and event cursors, availability markers, and a safe result summary without returning credentials, prompts, private reasoning, unrestricted provider output, or arbitrary file content

#### Scenario: Read an idle slot
- **WHEN** the browser requests a complete valid slot selector that has no current invocation
- **THEN** the service returns the slot assignment state, server-owned idle reason, and bounded same-slot history without requiring an invocation identifier

#### Scenario: Read a never-run configured slot
- **WHEN** the browser requests a valid configured phase, role, and canonical slot selector without a run identifier
- **THEN** the service returns the projected slot with `work_not_requested`, empty unavailable history, unavailable events, and no fabricated invocation or run identity

#### Scenario: Read a legacy invocation
- **WHEN** a historical invocation lacks fields introduced by the inspector change
- **THEN** the service returns the known fields with explicit unavailable markers and does not fabricate missing profile, result, error, or event data

#### Scenario: Request an invocation from another project
- **WHEN** an invocation identifier does not belong to the project in the request path
- **THEN** the service returns the same not-found response used for an unknown invocation and discloses no invocation metadata

#### Scenario: Invocation history exceeds the response bound
- **WHEN** same-slot invocation history exceeds the default 20 or requested maximum 100 rows
- **THEN** the service returns `(created_at DESC, invocation_id DESC)` keyset pagination with an opaque slot-scoped next cursor and a truncation marker

#### Scenario: Correlated events exceed the response bound
- **WHEN** correlated retained events exceed the default 50 or requested maximum 200 rows
- **THEN** the service returns events in `sequence ASC` order with an independent exclusive next cursor and a truncation marker

#### Scenario: Reject an invalid cursor
- **WHEN** a history or event cursor is malformed, exceeds its selector scope, or is reused for another slot or invocation
- **THEN** the service returns an unprocessable request and returns no page data

#### Scenario: Invocation execution snapshot is unavailable
- **WHEN** the invocation row cannot be matched to an immutable run snapshot with the recorded fingerprint and phase-role profile
- **THEN** the service marks execution identity unavailable and does not substitute current policy, another work unit, or another profile

#### Scenario: Invocation details cannot be loaded
- **WHEN** the operational store cannot serve a valid inspection response
- **THEN** the inspector retains its last resolved view, shows a bounded retryable error, and does not clear or replace the selected entity

#### Scenario: Derive invocation duration
- **WHEN** an invocation has valid creation and terminal timestamps or remains running at response observation time
- **THEN** the service returns a non-negative duration from creation to terminal time or server observation time respectively, and marks duration unavailable when a required legacy timestamp is missing or invalid

### Requirement: Correlate invocation lifecycle observability
The dispatcher SHALL use `invocation.started` and `invocation.finished` events with deterministic unique transition keys, SHALL atomically enqueue lifecycle delivery with invocation state in SQLite, SHALL make the terminal invocation row authoritative, and SHALL keep complete structured results and logs in their existing stores.

#### Scenario: Invocation starts
- **WHEN** the dispatcher allocates a logical slot and starts an agent invocation
- **THEN** it records immutable execution-snapshot, profile, and context-manifest fingerprints and enqueues `invocation.started` with transition key `invocation:<id>:started`, invocation/run/phase/role/slot/work-unit identity, `invocation_status=running`, and timestamp without changing the enclosing run status

#### Scenario: Invocation completes
- **WHEN** an agent returns a validated structured result
- **THEN** the dispatcher terminalizes the invocation once, enqueues `invocation.finished` with `invocation_status=completed`, terminal time, duration, and an opaque deterministic `node-result` reference, does not duplicate the complete result in the invocation row, and does not change the enclosing run status

#### Scenario: One concurrent invocation finishes
- **WHEN** one invocation finishes while another invocation in the same run remains active
- **THEN** the run snapshot remains running until the run-level workflow emits `run.finished`

#### Scenario: Invocation fails
- **WHEN** an agent invocation terminates with an error
- **THEN** the dispatcher terminalizes the invocation with one of `validation_error`, `provider_error`, `provider_blocked`, `provider_timeout`, `tool_error`, `cancelled`, `interrupted`, `environment_unavailable`, or `internal_error` and a redacted summary truncated at a valid UTF-8 boundary to 4096 bytes without persisting credentials, executable markup, or private reasoning

#### Scenario: Invocation is cancelled
- **WHEN** active work is cancelled by the user, soft-stop propagation, or dispatcher branch cancellation
- **THEN** every affected invocation is idempotently terminalized as cancelled and receives the same correlated terminal-delivery treatment as other outcomes

#### Scenario: Invocation is interrupted during recovery
- **WHEN** lease loss, process mismatch, orphan reconciliation, or service restart proves a running invocation no longer has a live owner
- **THEN** the invocation is idempotently terminalized as interrupted and a missing terminal transition is enqueued

#### Scenario: Lifecycle transition is retried
- **WHEN** outbox delivery or recovery repeats an already recorded started or finished transition
- **THEN** the unique transition key prevents a second state transition and consumers deduplicate any repeated JSONL delivery without regressing state

#### Scenario: Event retention is incomplete
- **WHEN** retained correlated events do not cover the full invocation lifetime
- **THEN** the detail response marks event history as truncated while preserving the invocation's terminal summary

#### Scenario: State commits before event delivery
- **WHEN** the service stops after committing invocation state and its outbox row but before appending the workflow event
- **THEN** later reconciliation retries delivery while the committed invocation state remains available and authoritative

#### Scenario: Node result is not yet published
- **WHEN** a successful invocation has a deterministic result reference but the graph callback has not published or no longer has the matching node-result record
- **THEN** inspection reports the opaque reference as unavailable and reads no arbitrary path or unrelated proposal

### Requirement: Migrate invocation inspection state compatibly
The service SHALL migrate invocation inspection state additively, SHALL name every invocation insert target column explicitly before adding columns, SHALL index bounded slot history, and SHALL define rollback against an owner-only pre-migration operational database backup.

#### Scenario: Upgrade a legacy operational database
- **WHEN** the new service opens a database without inspector columns or outbox tables
- **THEN** it backs up the database, applies additive nullable columns and indexes, and can create and terminalize a new invocation

#### Scenario: Open an already migrated database
- **WHEN** migration runs again against the current schema
- **THEN** it makes no destructive change and preserves all invocation rows, outbox transitions, and indexes

#### Scenario: Roll back the frontend only
- **WHEN** the previous static frontend is served with the upgraded backend
- **THEN** it ignores additive projection fields and events while existing circuit operation remains usable

#### Scenario: Roll back the backend binary
- **WHEN** an operator must restore backend code that predates explicit invocation column lists
- **THEN** the documented recovery restores the pre-migration operational database backup and warns that post-upgrade operational history is not retained while repository artifacts remain unchanged

### Requirement: Preserve contextual inspector interaction
The frontend SHALL use a closed selection containing parent circuit identity, SHALL render circuit, role, slot, invocation, queue, and item activators as independent keyboard controls, SHALL keep the last resolved inspector view, history position, graph viewport, keyboard focus target, and unrelated unsaved state stable across SSE and polling updates, and SHALL render all invocation-derived text as non-executable text.

#### Scenario: Live progress updates an inspected invocation
- **WHEN** an event changes the state of the invocation currently displayed
- **THEN** only the affected summary and event content update while selection, scroll position, graph viewport, and unrelated forms remain intact

#### Scenario: Work starts in the selected idle slot
- **WHEN** `invocation.started` matches the selected slot's phase, role, and canonical slot identity and either matches its run or supplies the first run
- **THEN** the slot adopts that run scope and refreshes to its running assignment while preserving selection, scroll, pagination state, graph viewport, and keyboard focus

#### Scenario: Unrelated progress event arrives
- **WHEN** an event belongs to another invocation or circuit
- **THEN** the selected invocation detail is not refetched and its cursors, scroll, and last-resolved content remain unchanged

#### Scenario: Switch selected invocations quickly
- **WHEN** a response for invocation A completes after the user has selected invocation B
- **THEN** request cancellation or sequence guarding prevents response A from replacing invocation B

#### Scenario: Event stream is degraded
- **WHEN** SSE cannot provide live reconciliation
- **THEN** one single-flight five-second fallback refreshes the dispatcher projection and selected non-terminal detail until SSE reopens

#### Scenario: Event stream is connected
- **WHEN** SSE remains connected while dispatcher work is active
- **THEN** one thirty-second reconciliation confirms state without creating per-entity polling loops

#### Scenario: Activate nested dispatcher entities
- **WHEN** a user clicks or presses Enter or Space on a role, slot, invocation, queue item, DIF, or MRQ control
- **THEN** exactly one activation selects that entity and no nested interactive element or parent node handles the same action

#### Scenario: Close an inspector opened from a slot
- **WHEN** a keyboard user closes the inspector
- **THEN** focus returns to the exact still-mounted slot or invocation control, falling back to its parent graph node when necessary

#### Scenario: Selected control disappears before close
- **WHEN** the exact initiating control is removed by reconciliation before the inspector closes
- **THEN** focus returns to the parent graph node or parent circuit navigation control without moving during ordinary content refresh

#### Scenario: Provider text contains markup
- **WHEN** an invocation error, result summary, or event contains HTML or script syntax
- **THEN** the inspector displays the syntax literally and executes none of it

#### Scenario: Navigate to an exact registry item
- **WHEN** the inspector opens a DIF or MRQ registry target
- **THEN** the workspace selects the declared registry, requests the exact stable item identifier through the fixed registry filter, and shows not found rather than an unrelated record when the target is absent
