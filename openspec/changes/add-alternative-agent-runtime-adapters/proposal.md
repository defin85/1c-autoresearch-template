## Why

The agent context and result contracts are provider-neutral, but the operational implementation is not: user profiles accept only `codex-cli`, model discovery calls `codex debug models`, execution snapshots fingerprint one Codex executable, and invocation arguments are fixed to `codex exec`. This prevents the same bounded research work from using OpenCode or Pi even when they can provide a read-only non-interactive execution mode.

OpenCode and Pi are agent runtimes, not model providers. A profile must distinguish the runtime that manages tools and sessions from the underlying model provider and model. Adding them through arbitrary command templates would bypass permission, output, cancellation, context-capacity, and evidence guarantees.

## What Changes

- Introduce a fixed server-owned agent-runtime adapter catalog.
- Separate `agent_runtime`, `model_provider`, and `model` in profiles, capabilities, snapshots, diagnostics, and reuse bindings.
- Preserve Codex CLI behavior as the first adapter and add fixed adapters for OpenCode and Pi.
- Require every adapter to prove exact product and executable identity, machine protocol, authentication readiness, model catalog, input and output limits, tool policy, structured event protocol, cancellation, and output normalization.
- Run every invocation as an isolated non-persistent session. OpenCode and Pi receive no native tools and may receive only the common coordinator-owned `source_search` bridge when enabled.
- Keep the coordinator-owned context envelope, response schema, validation, publication, lifecycle, and redaction authoritative for every runtime.
- Normalize runtime events into the existing invocation lifecycle without copying private reasoning or unrestricted transcripts into coordinator records.
- Keep recovery explicit: changing runtime or model requires a reviewed operator retry that creates a new run, invocation, snapshot, and lineage.
- Expose runtime, model-provider, model, capability, availability, and failure diagnostics in existing profile and dispatcher views.

## Impact

- Affected specifications: `repository-owned-generated-research-workflow`, `managed-autoresearch-workspace`.
- Expected implementation areas: user profiles, runtime capability discovery, execution snapshots, provider invocation, event normalization, cancellation and recovery, context budgeting, dispatcher projection, workspace profile editor, tests, scaffold synchronization, and operator documentation.
- Existing `codex-cli` profiles migrate deterministically to `model_provider=openai-codex` because their current `--ignore-user-config` contour uses the built-in provider.
- Replace the unimplemented Claude Code and automatic-fallback promises in the current workspace specification with the approved Codex CLI, OpenCode, and Pi catalog plus explicit recovery.
- No generic executable provider, runtime-managed publication, automatic retry, hidden model switch, native OpenCode/Pi filesystem tool, or persistent runtime session is introduced.
