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
The system SHALL present the supported autoresearch process as stable stage components showing dependencies, readiness, state, progress, blockers, approvals, actions, and outputs derived from server and repository evidence.

#### Scenario: Prerequisite is incomplete
- **WHEN** a stage depends on an incomplete or stale prerequisite
- **THEN** the stage remains non-runnable and identifies the blocking prerequisite and required recovery action

#### Scenario: Repository state changes outside the UI
- **WHEN** canonical project artifacts change through a supported external automation path
- **THEN** the next reconciliation updates affected stage state without overwriting canonical artifacts from stale UI data

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
