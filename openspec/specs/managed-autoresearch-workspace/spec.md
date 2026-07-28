# managed-autoresearch-workspace Specification

## Purpose
TBD - created by archiving change add-managed-autoresearch-workspace-ui. Update Purpose after archive.
## Requirements
### Requirement: Operate the workflow without CLI access
The system SHALL let an authorized user configure, execute, monitor, review, and complete a supported autoresearch project through the browser without requiring the user to enter a CLI command or edit a repository control file manually.

#### Scenario: Complete a fixture workflow from the browser
- **WHEN** a user creates a project, configures its required inputs, runs all enabled stages, resolves approvals, and requests final verification through the browser
- **THEN** the system reaches a verified terminal result and exposes its outputs without asking the user to invoke the CLI

### Requirement: Guide initial project setup
The system SHALL provide a resumable setup wizard that creates or opens a research project, collects product and version metadata, selects source roles and infobases, configures access policy, validates prerequisites, and selects enabled workflow stages.

#### Scenario: Required input is unavailable
- **WHEN** a wizard step cannot validate a required path, infobase connection, credential reference, or source role
- **THEN** the wizard preserves entered data, reports the exact failed check, and prevents completion of dependent setup steps

#### Scenario: Resume unfinished setup
- **WHEN** a user returns to a partially configured project
- **THEN** the wizard restores saved non-secret values and resumes at the first incomplete required step

### Requirement: Manage infobase connections safely
The system SHALL let users register, select, test, and update customer or vendor infobase profiles for the supported 1C MCP, web-publication, and optional direct-PostgreSQL-read channels while storing secrets in owner-only user-scope credential files, returning no stored secret values, redacting diagnostics, and defaulting to read-only access.

#### Scenario: Test a read-only connection
- **WHEN** a user saves and tests an infobase profile with valid secret references
- **THEN** the system reports verified capabilities and targets without exposing credentials or performing a write

#### Scenario: Request a write-capable probe
- **WHEN** an enabled stage requires a write-capable infobase operation
- **THEN** the system displays the target and impact and requires explicit confirmation for that run before dispatch

#### Scenario: Select an unsupported connection channel
- **WHEN** a user attempts to configure a channel not published by the server as supported
- **THEN** the system marks it unavailable and persists no runnable connection profile

### Requirement: Present a dependency-aware project workspace
The system SHALL present the supported autoresearch process as stable stage components showing dependencies, readiness, state, progress, blockers, approvals, actions, and outputs derived from server and repository evidence. DIF analysis and MRQ formation and consolidation SHALL be separate components whose readiness and actions are derived from separate canonical jobs.

#### Scenario: Prerequisite is incomplete
- **WHEN** a stage depends on an incomplete or stale prerequisite
- **THEN** the stage remains non-runnable and identifies the blocking prerequisite and required recovery action.

#### Scenario: Repository state changes outside the UI
- **WHEN** canonical project artifacts change through a supported external automation path
- **THEN** the next reconciliation updates affected stage state without overwriting canonical artifacts from stale UI data.

#### Scenario: DIF analysis is incomplete
- **WHEN** any active customer DIF remains unclassified
- **THEN** stage 2 MUST show exact total, classified, meaning, noise-candidate, remaining, and current-window progress while stage 3 remains disabled with the remaining count.

#### Scenario: DIF analysis is complete
- **WHEN** the repository-derived `all-dif-classified` gate becomes complete
- **THEN** stage 2 MUST show complete and stage 3 MUST become independently runnable without starting automatically.

#### Scenario: MRQ consolidation is running
- **WHEN** stage 3 has an active consolidation run
- **THEN** its component MUST show retained, new, merge, split, supersede, evidence, approval, and coverage progress independently of stage 2.

### Requirement: Configure an agent profile for each stage
The system SHALL identify each stage as deterministic or agent-executed and SHALL let a user select Codex CLI or Claude Code, model, reasoning setting where supported, worker ceiling, timeout, allowed tools, compatible acyclic fallback profile, and permitted stage overrides for every agent-executed stage. Both fixed adapters SHALL prove binary version, authentication readiness, required capabilities, isolated execution, structured normalized output, trace retention, source fingerprints, and coordinator-owned evidence validation, and the system SHALL NOT accept generic executable templates as agent providers.

#### Scenario: Stage has no valid agent profile
- **WHEN** an executable agent stage lacks a compatible enabled profile or required secret reference
- **THEN** the system marks the stage not ready and prevents dispatch

#### Scenario: Deterministic stage is configured
- **WHEN** a stage only runs a fixed package validation or generation operation
- **THEN** the UI identifies it as deterministic and requires no artificial agent profile or prompt

#### Scenario: Stage overrides a shared profile
- **WHEN** a user applies an override allowed by the stage schema
- **THEN** the system previews and records the resolved execution profile without mutating the shared profile

#### Scenario: Fallback profiles form a cycle
- **WHEN** saving a profile would create a direct or indirect fallback cycle
- **THEN** the system rejects the profile update and preserves the previous valid graph

#### Scenario: Select between installed agent systems
- **WHEN** both Codex CLI and Claude Code pass their version, authentication, and capability probes for a stage
- **THEN** the user can assign either profile to that stage and the run records the selected provider and adapter version

#### Scenario: Provider cannot enforce the stage contract
- **WHEN** an adapter cannot provide the required permission mode, tool restriction, structured output, or result normalization for a stage
- **THEN** the system marks that profile incompatible and prevents dispatch through it

#### Scenario: Provider output violates the common schema
- **WHEN** Codex CLI or Claude Code returns output that fails the coordinator-owned decision or evidence validation
- **THEN** the run is rejected and no canonical repository mutation is published

### Requirement: Compose and version effective prompts
The system SHALL compose each effective prompt from immutable repository and method instructions, a versioned stage template, resolved project variables, and a versioned user supplement, and SHALL show the assembled prompt before execution.

#### Scenario: User customizes a stage prompt
- **WHEN** a user edits and saves a prompt supplement
- **THEN** the system stores a new version, shows its difference from the previous version, and preserves mandatory instructions unchanged

#### Scenario: Run records prompt inputs
- **WHEN** a stage run is accepted for dispatch
- **THEN** the run snapshot records exact prompt versions, resolved agent profile, stage configuration, and input fingerprints

### Requirement: Dispatch only allowlisted background operations
The system SHALL start supported stage actions asynchronously through server-owned argument builders, return HTTP `202` with a run identifier, enforce one repository-mutating run per project, revalidate prerequisite and input fingerprints under the project run lock, and SHALL reject arbitrary commands, shell fragments, unknown operations, unsafe concurrency, and paths outside the registered project or its explicit canonical read-only source roots.

#### Scenario: Start a long operation
- **WHEN** a ready stage is launched
- **THEN** the API returns a run identifier promptly while the operation continues in a tracked background process group

#### Scenario: Submit an arbitrary command
- **WHEN** a client submits command text or an unsupported operation identifier
- **THEN** the server rejects the request and starts no process

#### Scenario: Concurrent mutating run exists
- **WHEN** a second repository-mutating stage is dispatched for a project with an active mutating run
- **THEN** the server returns a conflict identifying the active run and starts no second process

#### Scenario: Dispatch request is retried
- **WHEN** a client repeats the same operation and payload with the same project-scoped idempotency key
- **THEN** the server returns the original run response and starts no duplicate process

#### Scenario: Inputs changed after preview
- **WHEN** prerequisite or input fingerprints differ while the project run lock is held immediately before dispatch
- **THEN** the server returns a stale-input conflict and starts no process

#### Scenario: Configured source is outside the project
- **WHEN** a stage reads a canonical source root explicitly assigned to a supported source role in the current project manifest
- **THEN** the server permits read-only access to that resolved root while continuing to confine mutations and artifacts to the project root

#### Scenario: Source path escapes its approved root
- **WHEN** a submitted or symlink-resolved path is outside the registered project and its current canonical source roots
- **THEN** the server rejects dispatch and reads or writes no content at that path

### Requirement: Persist run state and bounded logs
The system SHALL persist run lifecycle, process identity and start marker, configuration snapshot, input fingerprints, event IDs, terminal result, artifact references, wrapper heartbeat, and bounded log indexes outside the research repository while retaining complete logs in a private user-scope run directory.

#### Scenario: Browser closes during execution
- **WHEN** the browser disconnects while a run is active
- **THEN** the server continues the run and persists its progress independently of connected clients

#### Scenario: Log output exceeds the browser tail
- **WHEN** a run produces more output than the configured live-log bound
- **THEN** the UI retains only the bounded tail and loads older output by page without losing the private complete run log

### Requirement: Stream replayable incremental events
The system SHALL expose an authenticated SSE stream with monotonically increasing event identifiers, typed bounded payloads, retained replay, heartbeat, and project or run scoping.

#### Scenario: Reconnect after a stream interruption
- **WHEN** a client reconnects with a valid `Last-Event-ID`
- **THEN** the server replays retained later events in order and then continues live delivery

#### Scenario: Event retention no longer covers the client cursor
- **WHEN** the requested event identifier predates retained history
- **THEN** the server sends a reset event, closes the stream, and requires the client to fetch a current snapshot before reconnecting

#### Scenario: Event is delivered twice
- **WHEN** reconnection or replay delivers an already applied event identifier
- **THEN** the client ignores the duplicate without duplicating log content or regressing state

### Requirement: Protect the local control plane
The system SHALL bind to loopback, enforce one active service instance, require same-origin state changes, validate host and origin, disable permissive CORS, and apply a restrictive content security policy without requiring local authentication.

#### Scenario: Cross-origin mutation is attempted
- **WHEN** a request with an invalid origin attempts to change state
- **THEN** the server rejects it and performs no mutation or process dispatch

#### Scenario: Second service instance starts
- **WHEN** another managed-workspace service attempts to use the same user state
- **THEN** it fails closed and reports the active instance without opening a second run manager

#### Scenario: Local application opens
- **WHEN** the local launcher opens the loopback application
- **THEN** the interface is immediately available without authentication

### Requirement: Bound connection tests
The system SHALL implement fixed tests for supported connection channels with validated schemes and fields, bounded time and response size, no file or Unix-socket access, no cross-origin credential redirect, and no arbitrary user-defined HTTP request.

#### Scenario: Connection redirects credentials to another origin
- **WHEN** a connection test receives a redirect to an origin different from the saved target
- **THEN** the test stops without forwarding credentials and reports the redirect as a failed check

#### Scenario: Private infobase target is tested
- **WHEN** a user confirms a supported test after reviewing its resolved private or loopback destination
- **THEN** the adapter performs only its fixed bounded probe against that exact destination

### Requirement: Update only affected UI state
The frontend SHALL apply each event to the exact project, stage, run, approval, log, or artifact cache entry it affects, preserve stable component identities and unsaved user input, coalesce progress updates, avoid whole-page replacement, and render all prompt, log, agent, path, evidence, error, and event text as non-executable text.

#### Scenario: Another stage reports progress
- **WHEN** a progress event arrives for a background stage while the user edits a prompt on the current stage
- **THEN** only the background stage progress changes and the unsaved prompt, selection, scroll, and expanded state remain intact

#### Scenario: Events arrive faster than the display limit
- **WHEN** progress or log events exceed the configured UI update rate
- **THEN** the frontend coalesces them into bounded updates while preserving the latest progress and ordered log content

#### Scenario: Agent output contains HTML or script
- **WHEN** an agent or process emits markup or script syntax in a log, error, evidence excerpt, or event
- **THEN** the UI displays the literal text and executes none of it

### Requirement: Reconcile with polling fallback
The frontend SHALL use low-frequency status reconciliation when SSE is unavailable and periodically confirm terminal state even while SSE is connected.

#### Scenario: SSE connection fails
- **WHEN** the event stream cannot be established or remains disconnected
- **THEN** the UI indicates degraded live monitoring and polls active run and stage state without blocking user navigation

### Requirement: Cancel and recover runs explicitly
The system SHALL support staged process-group cancellation, persist cancellation results, reconcile non-terminal runs after service restart, and expose only stage-supported retry or resume actions.

#### Scenario: User cancels an active run
- **WHEN** an authorized user confirms cancellation
- **THEN** the server terminates the tracked process group with the configured grace period and records whether cancellation completed or failed

#### Scenario: Service restarts during a run
- **WHEN** startup reconciliation finds a matching live runner wrapper with a current heartbeat and process start marker
- **THEN** the system resumes observation from persisted logs and events without launching a replacement process

#### Scenario: Run identity cannot be proven after restart
- **WHEN** startup reconciliation cannot match the wrapper heartbeat, run token, PID identity, process start marker, terminal result, and input fingerprints
- **THEN** the system marks the run interrupted and offers the documented stage recovery action without claiming success

### Requirement: Require and record manual decisions
The system SHALL maintain a browser queue of approvals and manual decisions with target, evidence, impact, allowed choices, actor, timestamp, idempotency key, canonical repository mutation result, and resulting stage transition. A decision SHALL become effective only after the typed repository mutation and its verifier succeed.

#### Scenario: Stage reaches an approval gate
- **WHEN** an operation produces a decision requiring analyst confirmation
- **THEN** the dependent workflow pauses, the decision appears in the manual queue, and no publication occurs until an allowed choice is recorded

#### Scenario: Canonical decision publication fails
- **WHEN** the repository mutation or verification for an approval fails
- **THEN** the approval remains unapplied, the stage stays blocked, and the UI reports the failure without presenting the SQLite audit record as canonical truth

### Requirement: Expose existing dashboards as isolated results
The system SHALL expose generated clean-comparison, review, and functional-gap dashboards as read-only stage results through iframe sandboxing without `allow-same-origin`, and SHALL not grant them control API, parent navigation, or secret access. HTML that cannot run in that sandbox SHALL be available only as an attachment download, not executable same-origin content.

#### Scenario: Open a generated dashboard
- **WHEN** a user opens a valid dashboard artifact for the registered project
- **THEN** the server resolves it inside the project boundary and displays it without converting it into workflow state

#### Scenario: Dashboard path escapes the project
- **WHEN** an artifact request resolves outside the registered project root
- **THEN** the server rejects the request and returns no file content

#### Scenario: Dashboard requires a weaker sandbox
- **WHEN** a generated dashboard cannot render without same-origin privileges or parent navigation
- **THEN** the UI offers an attachment download and does not weaken the sandbox

### Requirement: Preserve CLI and repository compatibility
The managed workspace SHALL use existing canonical artifact formats and supported package operations, and its absence or disablement SHALL not prevent CLI operation or invalidate an existing generated repository.

#### Scenario: Open an existing research repository
- **WHEN** a compatible repository created before the managed workspace is registered
- **THEN** the UI derives its stages from existing artifacts and requests only missing UI-specific configuration

#### Scenario: Remove the optional UI runtime
- **WHEN** the optional web dependencies and user-scope UI state are removed
- **THEN** canonical research files and CLI workflows remain usable

#### Scenario: Concurrent external edit precedes configuration save
- **WHEN** `project.toml` no longer matches the fingerprint shown in the browser preview
- **THEN** the service rejects the save, preserves the external edit, and requires a refreshed preview

### Requirement: Verify security, recovery, and responsiveness
The implementation SHALL include automated API, frontend, and browser checks for operation allowlisting, path confinement, secret redaction, event order and replay, cancellation, restart reconciliation, polling fallback, stable component state, and a complete fixture workflow.

#### Scenario: End-to-end browser acceptance
- **WHEN** the acceptance suite configures a fresh fixture project, launches a fake long operation, observes progress, reloads the page, approves a gate, and completes verification
- **THEN** all actions finish through the browser, unrelated UI state remains stable, and no secret or arbitrary command is exposed

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

### Requirement: Control split DIF and MRQ jobs independently
The managed workspace SHALL map stage 2 only to `analyze-dif` and stage 3 only to `consolidate-mrq`, and SHALL expose separate profiles, runs, leases, recovery actions, inspection data, and typed API actions for each job.

#### Scenario: Start stage 2
- **WHEN** the user starts a ready DIF-analysis stage
- **THEN** the service MUST launch only `analyze-dif`, process successive bounded windows, and MUST NOT invoke groupers, coordinators, MRQ restructuring, or MRQ publication.

#### Scenario: Start stage 3
- **WHEN** the user starts a ready MRQ-consolidation stage
- **THEN** the service MUST launch only `consolidate-mrq` against the complete classification generation and complete active MRQ graph, then stop for explicit plan approval before canonical mutation.

#### Scenario: Attempt to start stage 3 early
- **WHEN** the user or client requests stage 3 before `all-dif-classified` is complete
- **THEN** the service MUST reject dispatch with a typed prerequisite blocker and start no agent.

#### Scenario: Stage 2 stops between windows
- **WHEN** stage 2 is softly stopped after publishing a validated window
- **THEN** the UI MUST show resumable state and the next start or resume MUST derive remaining work from the active classification generation.

#### Scenario: One item in the current window fails
- **WHEN** stage 2 cannot validate every selected DIF
- **THEN** the UI MUST show the window as unpublished, identify failed and reusable completed items separately, and expose only explicit same-job recovery actions.

#### Scenario: A new job fails
- **WHEN** either split job fails, becomes interrupted, stale, or is cancelled
- **THEN** no automatic retry or downstream start MUST occur and the UI MUST expose only its supported explicit recovery actions.

#### Scenario: Inspect stage-2 collections
- **WHEN** the user opens DIF-analysis details
- **THEN** the inspector MUST distinguish total DIF, classified DIF, meaning DIF, noise-candidate DIF, current window, failed items, and remaining items without showing a partial group as an MRQ or approved noise.

#### Scenario: Inspect stage-3 collections
- **WHEN** the user opens MRQ-consolidation details
- **THEN** the inspector MUST expose bounded lists and exact aggregates for retained, new, merged, split, superseded, approval-pending, and published MRQs.

#### Scenario: Complete consolidation exceeds agent context capacity
- **WHEN** bounded comparison rounds cannot prove complete global coverage within configured limits
- **THEN** stage 3 MUST fail closed with a typed context-capacity blocker, show the uncovered partition count, and expose no approval action.

#### Scenario: Provider context capacity is unknown
- **WHEN** the effective agent profile cannot provide a validated input-context limit and versioned estimator
- **THEN** stage 3 MUST fail preflight before any agent call and identify the invalid profile capability.

#### Scenario: Consolidation plan cannot be stored
- **WHEN** the canonical operational plan file cannot be atomically written or its hash does not equal its plan fingerprint
- **THEN** stage 3 MUST report `consolidation.plan_storage`, retain no approval action, and leave canonical repository state unchanged.

#### Scenario: Downstream result becomes stale
- **WHEN** a new MRQ generation invalidates active batch, target-decision, or derived-output bindings
- **THEN** the workspace MUST mark the affected later stages stale, identify the changed MRQ fingerprint, and offer only their supported explicit rebuild or revalidation actions.
