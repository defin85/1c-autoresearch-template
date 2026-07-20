## Context

The repository already implements the reusable autoresearch stages and stores durable analysis state in project files. Its generated dashboards are static result surfaces and cannot configure a project, select infobases or agent systems, edit prompts, start work, approve decisions, or follow long-running operations.

The new application is an optional local control plane. The first delivery targets one workstation and one authenticated local user, while allowing multiple registered research projects. It must remain responsive during operations that can run for minutes or hours and must recover useful state after a browser or service restart.

### Locked Decisions

- Use React-admin Open Source 5 with React, TypeScript, and Material UI as the application shell.
- Use a setup wizard for first-time configuration and a project workspace for regular operation.
- Treat existing dashboards only as embedded or linked intermediate and final result views.
- Use a local Python API, background process groups, custom SSE, TanStack Query cache updates, and polling fallback.
- Keep repository artifacts as project truth; keep UI runtime data and secrets outside the project.
- Provide the complete user workflow without requiring CLI access while retaining CLI compatibility for automation and recovery.
- Ship the production frontend as versioned static assets in the optional Python wheel and store secrets in owner-only user-scope credential files.
- Provide Codex CLI and Claude Code as the initial agent-system choices through fixed adapters; unsupported providers are not represented as runnable.

## Goals / Non-Goals

**Goals:**

- Let a user create or open a project and configure all required sources and infobase access from the browser.
- Let a user assign an agent profile and prompt policy independently to each supported stage.
- Make stage dependencies, readiness, progress, blockers, approvals, logs, and outputs visible in one workspace.
- Run long operations asynchronously and update only the affected cards, counters, logs, and artifact links.
- Preserve exact execution inputs, prompt versions, source fingerprints, events, and terminal outcomes for reproducibility.
- Fail closed around secrets, write-capable infobase access, arbitrary commands, unsafe paths, and stale stage prerequisites.

**Non-Goals:**

- Replace existing package commands or file-backed analysis contracts.
- Turn static result dashboards into the control plane.
- Add multi-tenant hosting, remote workers, organization roles, billing, scheduling, or a message broker in the first delivery.
- Provide an interactive shell or unrestricted agent terminal in the browser.
- Stream individual model tokens or retain unlimited process output in the browser.

## Decisions

### 1. Build an optional modular monolith

The managed workspace will consist of one React-admin single-page application and one local Python service. The Python service will serve the compiled frontend, expose typed JSON endpoints, stream SSE, manage subprocesses, and adapt existing package operations.

The Python web stack will be installed through an optional dependency group so `one-c-autoresearch` remains usable without a web runtime. FastAPI and Uvicorn are selected for typed request validation, generated API documentation, asynchronous streaming, and a small server boundary. A deterministic frontend build will produce versioned static assets that are packaged inside the optional Python wheel; a production installation never depends on a repository-local Node.js process or source tree.

The service will run as one Uvicorn worker under an exclusive user-scope instance lock. This preserves in-process run ownership and one SQLite writer while SSE and ordinary reads remain asynchronous. A second service instance must fail closed with the location and identity of the active instance.

Alternative: separate frontend, API, worker, and broker services. Rejected because the first delivery runs on one workstation and existing operations already persist resumable state in repository files.

Alternative: implement the server with only `http.server`. Rejected because request validation, cancellation, streaming, and structured error handling would become custom infrastructure larger than the dependencies avoided.

### 2. Use React-admin resources plus two custom application screens

React-admin resources will cover projects, connections, agent profiles, prompt templates, runs, approvals, and artifacts. Custom routes will implement `/projects/:id/setup`, `/projects/:id/workflow`, and `/projects/:id/stages/:stageId`.

The setup wizard will use Material UI Stepper and React Hook Form through React-admin form primitives. The workflow screen will use stable component identities per stage so updates do not reset selection, form input, scroll position, or expanded panels.

Alternative: copy a visual-only dashboard block. Rejected because it would require rebuilding resource forms, validation, API state, navigation, and error behavior already provided by React-admin.

### 3. Derive project truth from repository artifacts

The service will read `project.toml`, queue records, workitems, analysis artifacts, and doctor output to derive stage readiness and completion. Mutations will call package-level functions where available and otherwise use a fixed allowlist of argument builders for public `one_c_autoresearch` commands. Project roots must be selected below explicit user-approved workspace roots. Repository mutations and artifact serving remain confined to the registered project root; read-only source access is additionally allowed only for canonical source roots explicitly saved in that project's supported `project.toml` roles. Symlinks and every resolved operation argument are rechecked at use time.

UI state must not duplicate canonical analysis decisions. SQLite will store only registered project locations, connection metadata, agent profiles, prompt versions, run/event records, log indexes, approvals, and UI preferences.

SQLite will use schema migrations, foreign-key enforcement, WAL mode, explicit transactions, and one application writer. Project configuration changes use compare-and-swap: the browser previews a proposed `project.toml`, submits the source fingerprint it reviewed, and the service atomically replaces the file only if that fingerprint still matches. Approval records become effective only after the corresponding typed repository mutation and its verification succeed; SQLite retains the request and audit result, not a competing canonical decision.

Alternative: import all analysis files into a database. Rejected because it would create two competing sources of truth and weaken Git-reviewable reproducibility.

### 4. Model the workflow as a server-owned stage catalog

Each supported stage will declare its identifier, title, dependencies, readiness probe, execution kind (`deterministic` or `agent`), allowed operation, mutation class, configuration schema, default prompt template, approval policy, resume policy, and result view. Agent profiles and prompts are required only for agent stages; deterministic validation, generation, and doctor stages expose their fixed package operation instead of pretending to use an agent. Clients receive the resolved catalog and state; they cannot submit executable command strings.

The initial catalog will cover project intake, source and connection validation, source normalization, physical cleanup, diff inventory, feature and requirement research, reverse mapping, final-gate normalization, evidence packs, subject cards, functional gaps, review preparation, result generation, dashboards, and strict doctor verification.

Alternative: let users freely compose arbitrary stages. Rejected because the documented method has ordered safety and evidence gates; custom workflows can be considered after the fixed catalog proves insufficient.

The run manager will allow at most one repository-mutating run per registered project. A read-only run may overlap only when both stage declarations explicitly permit it. Dispatch records prerequisite and input fingerprints and rechecks them while holding the project run lock immediately before process creation. An incompatible concurrent request returns a conflict and starts nothing. Existing queue, reverse-map, and single-writer locks remain authoritative inside their narrower operations.

### 5. Treat connections and agent profiles as reusable user-scope records

An infobase connection will contain a logical role (`customer` or `vendor`), a supported access channel, non-secret target metadata, tested capabilities, and a reference to a secret stored outside research repositories. The initial channels are the repository's existing surfaces: 1C MCP, web publication, and optional direct PostgreSQL read access. Source trees remain separate project inputs for `vendor_baseline`, `target_cf`, `target_cfe`, and `next_vendor`. Unsupported channels are displayed as unavailable rather than accepted optimistically.

The service will bind only to loopback by default. Secret values will be written atomically to owner-only user-scope credential files, never to SQLite or a project, and API responses will expose only stable secret references and presence state. Updating a secret is write-only; reading it back is not an API operation. Secrets are supplied to adapters through private files or inherited environment entries, never command-line arguments. Logs, errors, events, subprocess arguments, and diagnostics must pass structured redaction before persistence or delivery.

Connection tests are fixed per supported channel. They accept only channel-appropriate schemes and fields, use bounded timeouts and response sizes, do not follow redirects across origins, never accept `file:`, Unix-socket, or arbitrary-request definitions, and send credentials only to the exact saved origin. Private and loopback targets remain allowed because local 1C infrastructure requires them, so the UI must show the resolved destination before the user confirms a test.

Read-only infobase access is the default. Any write-capable probe requires an explicit stage capability, visible warning, and per-run confirmation.

Agent profiles will identify the provider adapter, model, reasoning setting, worker ceiling, timeout, allowed tools, fallback profile, and secret reference. Each stage stores a reference to an agent profile plus stage-specific overrides permitted by its schema. The first delivery supports two fixed adapters: Codex CLI, reusing the repository's existing `codex exec` contour, and Claude Code in non-interactive print mode with structured JSON output and explicit permission/tool restrictions. Both adapters must run the same deterministic unit context in an isolated workspace, produce the coordinator-owned decision schema, preserve traces and source fingerprints, and leave canonical publication to the existing single writer. Provider-specific output is normalized and revalidated; provider claims never bypass the common schema or evidence gates.

Each adapter exposes a fixed executable identity, version/authentication probe, supported execution modes, structured-output capability, tool-policy mapping, immutable argument builder, event parser, cancellation behavior, and result normalizer. The service marks a profile unavailable when its binary, authentication, version, or required capability probe fails. It never downloads, upgrades, logs in, or rewrites global provider configuration on the user's behalf. The provider field is a server-published allowlist, not a generic executable. Fallback references must resolve to another enabled compatible profile and must be acyclic.

Alternative: put credentials and model keys in `project.toml`. Rejected because generated repositories are portable and reviewable and must not contain secrets.

Alternative: add arbitrary executable templates or treat any installed agent CLI as compatible. Rejected because each provider needs fixed permission, output, cancellation, and evidence normalization. Providers beyond Codex CLI and Claude Code require a separate capability change and tests.

### 6. Compose and version prompts without weakening mandatory instructions

The effective prompt will be assembled from immutable repository and method instructions, a versioned stage template, resolved project variables, and a user supplement. The UI will show the assembled prompt and a diff from the previous version before saving or running.

Each run records the prompt template version, user supplement version, agent profile snapshot, stage configuration, and input fingerprints. User text cannot replace mandatory repository, security, evidence, or output-schema instructions.

Alternative: expose one unrestricted prompt field. Rejected because it would make runs irreproducible and allow users to bypass safety and evidence contracts unintentionally.

### 7. Run operations asynchronously with durable event replay

Starting an operation returns HTTP `202` with a run identifier. Every mutating request carries a client-generated idempotency key scoped to its project and operation; replay returns the original response, while reuse with a different payload is rejected. A local run manager starts an allowlisted runner wrapper in a new OS-specific process group, captures bounded stdout/stderr, persists lifecycle state in SQLite, and writes complete logs plus an atomic heartbeat and terminal-result file to a private user-scope run directory. Wrapper files are owner-only and carry an unguessable run token. The wrapper records its PID identity and process start marker so restart reconciliation cannot mistake PID reuse for the original run.

The versioned `/api/v1` API uses a same-origin, HTTP-only, `SameSite=Strict` session cookie created from a short-lived one-time local bootstrap token. A desktop or service launcher places the token in the URL fragment, the frontend immediately removes the fragment with `history.replaceState`, and exchanges it through a same-origin POST so it is not sent in the initial HTTP request or retained in navigation history. State-changing requests additionally require a session-bound CSRF token. The service rejects non-loopback hosts by default, disables permissive CORS, validates `Host` and `Origin`, and emits a restrictive content security policy. SSE uses the same-origin cookie because browser `EventSource` cannot set an arbitrary authorization header.

The API exposes an SSE stream keyed by project or run. Events have monotonically increasing IDs and typed payloads such as `run.queued`, `run.started`, `stage.progress`, `log.append`, `approval.required`, `artifact.ready`, `run.completed`, and `run.failed`. Reconnection with `Last-Event-ID` replays retained events before switching to live delivery. Event IDs are delivery cursors, not state versions; duplicate delivery is tolerated and clients apply events idempotently. When retention no longer covers a cursor, the server sends a reset event and closes the stream so the client fetches a fresh workflow snapshot before reconnecting.

The frontend updates exact TanStack Query cache entries for the affected project, stage, run, log page, or artifact. Progress events are coalesced to at most four UI updates per second and log chunks are bounded. Prompts, paths, logs, agent output, evidence excerpts, errors, and event payload text are rendered as text, never injected as HTML. A low-frequency status query runs on stream failure and periodically reconciles terminal state.

Alternative: poll every resource continuously. Rejected because it produces unnecessary load and stale or twitching progress views.

Alternative: WebSocket. Rejected because commands use ordinary HTTP and monitoring is server-to-client; SSE provides reconnection and event IDs with less code. WebSocket remains an upgrade path only for a future interactive agent session.

### 8. Make interruption and recovery explicit

Cancel sends a staged termination to the tracked process group and records the outcome. On service startup, runs left non-terminal are reconciled against the wrapper heartbeat, terminal-result file, PID identity, process start marker, and repository verification. A live matching wrapper remains observable from its files even though the restarted service no longer owns its output pipes. A missing or mismatched process is marked `interrupted`, not `failed` or `complete`; the UI offers only the documented resume or retry action for that stage. A terminal-result file is accepted only when its run token and input fingerprints match and the stage terminal verifier passes.

Browser closure never cancels a run. The server must compute completion from the operation result and repository verification, not from the presence of an SSE client.

Alternative: automatically restart every interrupted command. Rejected because some stages require a compatibility check or explicit resume mode before safe continuation.

### 9. Keep result dashboards isolated from control actions

Existing generated dashboards will be embedded in a sandboxed frame when their asset layout permits it. The server will serve only resolved paths inside the registered project and will prevent path traversal. Result dashboards cannot call control APIs or receive credentials. A non-embedded HTML result is offered only as an attachment download; it is never opened as executable same-origin content.

Embedding uses an iframe sandbox without `allow-same-origin`; `allow-scripts` is added only for dashboards that require their bundled script. Artifact responses use a restrictive content security policy and cannot navigate the parent. If a dashboard cannot render under those restrictions, the application offers an attachment download instead of weakening the sandbox.

### 10. Verify responsiveness and end-to-end behavior explicitly

Frontend tests will prove that progress updates do not remount unrelated stage cards or erase unsaved prompt text. API tests will cover allowlists, path confinement, redaction, event ordering/replay, cancellation, recovery, and polling reconciliation. A browser test will create a fixture project, configure it through the wizard, launch a fake long operation, observe incremental progress, reload the page, resume observation, approve a gate, and reach a verified result without invoking the CLI manually.

## Risks / Trade-offs

- [React-admin realtime helpers are paid] -> Use native `EventSource` and direct TanStack Query cache updates; do not depend on `ra-realtime`.
- [A local control API can become arbitrary remote execution] -> Bind to loopback, require a session token, allowlist operations and arguments, confine paths, and never accept shell command text.
- [UI and repository state diverge] -> Recompute stage state from repository artifacts and doctor probes; treat SQLite state as operational metadata only.
- [High-frequency agent output makes the UI unstable] -> Coalesce progress, chunk logs, page history, and cap the in-browser tail.
- [Service restart loses process ownership] -> Persist process metadata, reconcile explicitly, and rely on stage-specific resume semantics instead of claiming false completion.
- [Frontend dependencies increase maintenance] -> Keep a single application, standard React-admin and Material UI components, and no second design system.
- [Embedded generated HTML could access the control origin] -> Prefer links; when embedding, use a sandboxed frame and a separate read-only asset route without session material.

## Migration Plan

1. Add optional API/frontend packaging and a fixture-only application shell without changing core CLI installation.
2. Add user-scope SQLite state, project registration, safe connection records, stage catalog, and readiness endpoints.
3. Add the setup wizard and resource management screens.
4. Add the asynchronous run manager, event persistence, SSE replay, cancellation, reconciliation, and polling fallback.
5. Add the workflow and stage screens, prompt composition, approvals, logs, and artifact views.
6. Add existing dashboard integration and end-to-end browser verification against a fresh generated repository.
7. Extend template and research checks, documentation, packaging, and strict doctor verification.

Rollback removes or disables the optional managed-workspace entry point and its user-scope state. Existing repositories, CLI commands, and canonical analysis artifacts remain valid.

## Open Questions

None. Agent providers beyond Codex CLI and Claude Code, remote hosting, operating-system keyring integration, and weaker dashboard sandbox modes require separate future changes rather than implementation-time decisions in this change.
