## 1. Optional Application Foundation

- [x] 1.1 Add optional Python web dependencies, frontend workspace metadata, deterministic asset build commands, versioned frontend assets packaged in the optional wheel, and package entry points without changing the core CLI install.
- [x] 1.2 Create the React-admin 5 application shell with TypeScript, Material UI, Russian and English localization hooks, navigation, error handling, and a fixture data provider.
- [x] 1.3 Add the unauthenticated single-worker FastAPI loopback service, exclusive instance lock, host/origin validation, restrictive CORS/CSP, compiled-asset serving, typed error responses, health endpoint, and local launcher path.
- [x] 1.4 Add focused packaging and startup checks that prove the core CLI works without UI dependencies and the optional application serves after installation.

## 2. Durable UI State And Safe Project Access

- [x] 2.1 Add versioned SQLite migrations, foreign keys, WAL transactions, and repositories for project registrations, connection metadata, agent profiles, prompt versions, runs, events, logs, approvals, artifacts, and preferences.
- [x] 2.2 Add approved workspace roots, canonical project/source path resolution, project-write and artifact confinement, manifest-role read-only source confinement, symlink revalidation, owner-only user-state and credential directories, atomic write-only secret files, and redacted structured diagnostics.
- [x] 2.3 Add project registration and inspection APIs that derive metadata and workflow state from `project.toml`, repository artifacts, queues, and doctor probes.
- [x] 2.4 Add migration and compatibility tests for empty state, existing databases, existing generated repositories, path traversal, symlink escape, and removal of UI state.

## 3. Connections, Agents, And Prompt Policy

- [x] 3.1 Add customer/vendor infobase resources for 1C MCP, web publication, and optional direct PostgreSQL read access, with secret references outside projects, capability metadata, read-only defaults, and fixed scheme-validated bounded connection tests without cross-origin credential redirects.
- [x] 3.2 Add explicit confirmation and audit handling for any allowlisted write-capable infobase probe.
- [x] 3.3 Define the fixed agent-adapter contract for executable/version/authentication probes, capabilities, immutable arguments, isolated workspace, permission/tool mapping, event parsing, cancellation, normalized structured result, traces, and source fingerprints.
- [x] 3.4 Implement the Codex CLI adapter by reusing the existing `codex exec` worker contour and its coordinator-owned schema and evidence validation.
- [x] 3.5 Implement the Claude Code adapter in non-interactive print mode with structured JSON output, explicit permission/tool restrictions, the same isolated unit context, and normalization into the coordinator-owned schema.
- [x] 3.6 Add agent profiles with model, supported reasoning setting, concurrency, timeout, tools, compatible acyclic fallback, secret reference, and rejection of unavailable, incompatible, unsupported, or generic executable providers.
- [x] 3.7 Add versioned stage prompt templates, user supplements, immutable mandatory layers, variable resolution, assembled preview, and version diff.
- [x] 3.8 Add contract tests that run both adapters through fake executables and reject capability, permission, schema, evidence, cancellation, and normalization violations.
- [x] 3.9 Add API and frontend tests for loopback access, redaction, unavailable and incompatible profiles, fallback cycles, provider selection, allowed stage overrides, prompt immutability, and run snapshots.

## 4. Setup Wizard

- [x] 4.1 Implement the resumable Material UI Stepper flow for project identity, source roles, infobases, access policy, connection checks, enabled stages, and initial agent assignments.
- [x] 4.2 Persist non-secret partial setup, restore the first incomplete step, and map server validation errors to exact wizard fields and checks.
- [x] 4.3 Generate or update supported project configuration only through typed package operations with preview, source fingerprint compare-and-swap, and atomic replacement.
- [x] 4.4 Add component and browser tests for new projects, existing projects, failed prerequisites, preserved input, and completion without CLI use.

## 5. Workflow Catalog And Project Workspace

- [x] 5.1 Add the server-owned stage catalog with deterministic/agent execution kind, dependencies, readiness probes, allowlisted operations, mutation classes, configuration schemas, approval policy, agent prompt defaults where applicable, resume policy, and result views for the documented end-to-end process.
- [x] 5.2 Add the workflow snapshot API that reconciles stage state with repository artifacts and identifies stale or blocked prerequisites.
- [x] 5.3 Implement the project workspace with stable stage cards, status summary, dependency navigation, blockers, approvals, actions, and outputs.
- [x] 5.4 Implement the stage screen for resolved agent settings, prompt editing, execution parameters, run history, logs, evidence, approvals, and artifacts.
- [x] 5.5 Add tests for dependency blocking, external artifact changes, stale UI state, stable component identities, scroll preservation, expanded panels, unsaved prompt preservation, and literal rendering of untrusted agent/log/event text.

## 6. Background Run Manager

- [x] 6.1 Add allowlisted operation identifiers, project mutation locks, dispatch-time fingerprint validation, project-scoped idempotency keys, and server-owned argument builders that call package APIs where available and never accept shell command text.
- [x] 6.2 Add asynchronous HTTP `202` dispatch through an OS-specific runner wrapper with tracked process identity/start marker and unguessable run token, bounded output capture, private complete logs, atomic heartbeat/result files, configuration snapshots, and terminal verification.
- [x] 6.3 Add staged cancellation with grace timeout, process-group cleanup, idempotent terminal transitions, and recorded cancellation outcomes.
- [x] 6.4 Add startup reconciliation using run token, heartbeat, PID identity, process start marker, terminal result, repository verifier, and stage-specific interrupted, retry, and resume behavior.
- [x] 6.5 Add fake-operation integration tests for concurrent runs, invalid operations, unsafe arguments, browser disconnect, cancellation, service restart, interruption, retry, resume, and false-success prevention.

## 7. Incremental Monitoring

- [x] 7.1 Add durable idempotent typed event publication with monotonically increasing identifiers, project/run scopes, bounded payloads, documented retention, and heartbeats.
- [x] 7.2 Add same-origin cookie-authenticated SSE streaming, ordered `Last-Event-ID` replay, explicit reset-and-close retention-gap recovery, disconnect cleanup, and backpressure bounds.
- [x] 7.3 Add the frontend `EventSource` bridge that updates exact TanStack Query cache entries and coalesces progress and log events to configured rates.
- [x] 7.4 Add degraded-state indication, low-frequency polling fallback, periodic terminal reconciliation, and automatic live-stream recovery.
- [x] 7.5 Add API and frontend tests for event order, duplicate tolerance, replay, retention gaps, reconnect, polling fallback, bounded logs, high-frequency events, and no whole-page replacement.

## 8. Decisions And Results

- [x] 8.1 Add the idempotent manual decision queue with evidence, impact, allowed choices, actor, timestamp, typed canonical repository mutation, post-mutation verification, and dependency-aware workflow transitions.
- [x] 8.2 Add artifact discovery, project-confined read-only routes, and attachment-only delivery for non-sandboxed executable HTML.
- [x] 8.3 Add iframe embedding without `allow-same-origin` for compatible clean-comparison, review, and functional-gap dashboards, with restrictive artifact CSP and no control API, parent navigation, or credential access.
- [x] 8.4 Add tests for blocked publication, accepted and rejected decisions, artifact readiness, missing artifacts, traversal attempts, and isolated dashboard rendering.

## 9. End-To-End Verification And Template Integration

- [x] 9.1 Build a fresh generated research fixture and complete setup, fake long-running stages, progress observation, page reload, approval, result viewing, and strict final verification entirely through the browser.
- [x] 9.2 Add accessibility checks for keyboard navigation, focus handling, live progress announcements, form errors, and reduced-motion behavior.
- [x] 9.3 Extend template and generated-repository docs, repo maps, verification matrices, bootstrap assets, and doctor checks for the optional managed workspace.
- [x] 9.4 Run frontend type checks and tests, API tests, browser acceptance, packaging/install smoke tests, `python -m one_c_autoresearch checks template`, `checks doctor`, strict deep doctor, full Python tests, and strict OpenSpec validation.
- [x] 9.5 Verify no credentials, UI databases, run logs, compiled caches, customer artifacts, or generated research state are shipped in the reusable template or fresh repository scaffold.
