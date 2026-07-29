## Context

The coordinator already owns the stable boundary: one immutable work unit becomes one bounded `context-envelope/v1`, one fixed response schema, one validated proposal, and only then a canonical publication. Runtime discovery, process launch, and result extraction are still embedded around Codex CLI.

The existing workspace specification also promises an unimplemented Claude Code adapter and automatic fallback profiles. This change intentionally replaces that promise with the user-approved fixed catalog below and retains the existing rule that failed jobs require an explicit operator recovery action.

## Locked Decisions

1. The fixed adapter IDs are `codex-cli`, `opencode`, and `pi`. Claude Code and generic command adapters are not supported.
2. `opencode` means the `opencode` executable from `opencode-ai` by `anomalyco/opencode`; `pi` means the `pi` executable from `@earendil-works/pi-coding-agent` by `earendil-works/pi`.
3. Agent runtime, model provider, and model are separate profile fields and immutable execution bindings.
4. Every adapter receives the exact coordinator-prepared input and bounded response schema. It returns only a candidate which passes the existing schema, evidence, binding, and single-writer publication checks.
5. Every invocation uses a fresh process and runtime session. Runtime continuation, shared conversation history, automatic compaction, automatic provider retry, and cross-work-unit memory are disabled.
6. OpenCode and Pi expose no runtime-native filesystem, shell, web, question, nested-agent, skill, extension, or model-switch tools. They may receive only the coordinator-owned `source_search` bridge when its separately versioned policy is enabled; otherwise they run without tools.
7. Model-provider API traffic for the selected model is allowed. Model-visible arbitrary web access is not.
8. There is no automatic runtime, model, or profile fallback and no fallback field in profile schema version 2. A user may explicitly retry supported work with another compatible profile; that uses the existing reviewed recovery operation and creates a new run and invocation.
9. Workflow configuration remains authoritative for stage timeout and worker concurrency. A user profile does not duplicate or override those limits.
10. Provider-managed reasoning, raw event streams, raw stdout/stderr, prompts, credentials, runtime configuration, and unrestricted responses are never copied into coordinator operational or canonical records. Invocation-private vendor state may hold the minimum data required by a runtime until process death and mandatory cleanup.
11. Authentication and provider configuration remain user-owned. The adapter may project only an allowlisted credential reference into invocation-private runtime state without reading its contents; the model receives no tool that can read it, and the service never logs, returns, refreshes, or rewrites credentials.
12. Executable compatibility is feature- and protocol-probed rather than inferred from semantic version ranges. The initial conformance fixtures are Codex CLI `0.145.0`, OpenCode `1.17.18`, and Pi `0.79.1`; any installed version is unavailable until it passes the same closed probe and fixture contract.
13. Existing Codex behavior keeps the same coordinator input and result semantics. Its current default model provider is recorded as `openai-codex` because the existing `--ignore-user-config` invocation cannot select a custom provider.
14. The full run-snapshot fingerprint remains the audit identity. Cross-run result reuse uses one derived immutable invocation-binding fingerprint that excludes run ID, policy source, retry lineage, and unrelated profiles but includes the selected adapter, executable, permissions, provider, model, capacity, framing, tool transport, context, instruction, schema, and result bindings.

## Fixed Adapter Contract

Each catalog entry implements:

- exact executable discovery, content fingerprint, version, and adapter-protocol probe;
- bounded authentication readiness and structured model catalog probes without returning credential material;
- provider/model identity, positive input-context and maximum-output limits, supported reasoning values, structured protocol, and capability fingerprint;
- immutable argument, environment, framing, permission, tool-transport, and cancellation builders;
- a bounded native-event parser and final candidate extractor;
- process-group cancellation, timeout, fenced orphan recovery, and temporary-state cleanup.

The capability fingerprint covers adapter ID and protocol version, executable fingerprint and version, provider/model IDs, context and output limits, reasoning controls, native-tool policy, `source_search` transport version or absence, framing reserve, event/result normalization, and cancellation semantics.

Probe limits are fixed server policy: 15 seconds, 4 MiB combined output, 1,000 normalized models, and no provider request made solely to test credentials. Catalog entries are cached for at most five minutes and re-probed when the executable identity changes. Profile save uses an expected catalog fingerprint; invocation preparation re-probes and freezes current capabilities.

Unknown mandatory fields, event kinds, permission requests, multiple final candidates, missing terminal proof, provider/model mismatch, non-positive capacity, oversized records, or unsupported protocol versions fail closed. Unknown explicitly optional notifications are dropped with bounded counters.

## Runtime-Specific Mappings

### Codex CLI

The existing `codex exec --ephemeral --ignore-user-config --ignore-rules --sandbox read-only` contour becomes adapter protocol `codex-cli/v1`. Its current executable, environment, prompt, output-schema, last-message extraction, model catalog, and result validation behavior remain the compatibility baseline. The adapter registers only the private coordinator `source_search` bridge when the frozen profile policy requires it.

### Pi

Adapter protocol `pi-rpc/v1` uses strict LF-delimited JSONL with unique request IDs and launches:

`pi --mode rpc --no-session --no-extensions --no-skills --no-prompt-templates --no-themes --no-context-files --no-approve --offline --provider <provider> --model <model> --thinking <value> --system-prompt <adapter-owned-fixed-prompt> --append-system-prompt '' --no-tools`

The adapter-owned system prompt and explicit empty append are immutable, fingerprinted, and included in the framing budget, so user-global `SYSTEM.md` and `APPEND_SYSTEM.md` files cannot change an invocation. When `source_search` is enabled, the exact tool contour is `--no-builtin-tools --tools source_search --extension <server-owned-adapter> --no-extensions`; the extension exposes only the authenticated per-invocation bridge and extension discovery remains disabled. The adapter confirms `get_available_models` and `get_state`, requires correlated success from `set_auto_compaction=false` and `set_auto_retry=false`, then sends exactly one `prompt`. After one `agent_end`, it obtains all messages through `get_messages` and requires every assistant message to name the frozen provider and model. Intermediate tool-call assistant messages must have `stopReason=toolUse`; exactly one last text assistant message must have `stopReason=stop` and becomes the candidate. `length`, terminal `toolUse`, `error`, `aborted`, any other reason, or multiplicity fails with a typed error and no candidate. Final `get_state` must retain the frozen provider, model, thinking value, `autoCompactionEnabled=false`, and no session file; the correlated setter response is the proof that automatic retry was disabled.

Cancellation sends correlated `abort`, waits for its acknowledgement and terminal event within the adapter deadline, then escalates to the common process-group termination. Steering, follow-up, session, fork, model-switch, bash, UI, extension-command, compaction, and retry activity are protocol violations.

### OpenCode

Adapter protocol `opencode-server/v1` starts one private authenticated loopback `opencode serve --pure --hostname 127.0.0.1 --port <reserved>` process per invocation. Because OpenCode `1.17.18` does not reliably allocate an ephemeral port for `--port 0`, the service reserves a port under its local allocation lock and retries at most three bind conflicts. A random invocation secret authenticates every request and is never logged or exposed to the model.

The process receives invocation-private `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, `XDG_STATE_HOME`, temporary directory, and OpenCode configuration directory and starts from an owner-only staged working directory without project or user OpenCode resources. Only the selected vendor `auth.json` is projected at its expected private-data path through an owner-only read-only bind/reference; no whole home or global configuration tree is exposed. The selected non-secret provider definition is sanitized into the highest-precedence inline configuration; a custom provider that cannot be represented without copying secrets is unavailable.

That inline configuration fixes the selected provider/model and server-owned agent and disables auto-update, sharing, snapshots, compaction, pruning, formatters, LSP, MCP discovery, plugins, commands, instructions, project agents, subagents, questions, shell, edits, web tools, and every native tool. If `source_search` is enabled, it registers and permits exactly the authenticated private coordinator bridge.

The adapter establishes the authenticated event stream before creating a session and submitting the asynchronous prompt, accepts only bounded documented events, retrieves the terminal assistant message through the structured API, verifies its provider and model, and extracts exactly one final text candidate. Cancellation calls the native session-abort endpoint before process escalation. OpenCode may persist prompts and responses only inside this inaccessible invocation-private vendor state while the process is live. After process death, cleanup removes the whole private state. Quarantine may retain it only while process identity is live or unverifiable, owner-only and invisible to UI and logs; the scavenger removes it after a safe terminal predicate.

## Profiles, Snapshots, And Capacity

The user-scope file advances from the current implicit map to:

```json
{
  "schema_version": "2",
  "profiles": {
    "default": {
      "agent_runtime": "codex-cli",
      "model_provider": "openai-codex",
      "model": "gpt-5.6-sol",
      "reasoning_effort": "low",
      "instructions_version": "1",
      "environment_preset": "local-read-only"
    }
  }
}
```

Profile keys are closed. `agent_runtime`, `model_provider`, `model`, `reasoning_effort`, `instructions_version`, and `environment_preset` are required; `environment_preset` is exactly `local-read-only`. Derived capacity and capability fields are never stored in the file. The separately defined optional `source_search` object is admitted only when the selected adapter passes its bridge conformance suite.

The effective timeout and concurrency remain the tracked stage values. Before slot allocation, the service freezes:

- adapter/executable/protocol and permission identities;
- provider/model/reasoning, input-context and maximum-output limits;
- exact adapter system/framing reserve;
- prepared `context-envelope/v1`, instruction, response-schema, and result bindings;
- `source_search` policy, bridge version, and worst-case dynamic reserve when enabled;
- process, event, output, and cancellation bounds.

Capacity admission uses the active estimator and proves that prepared input, adapter framing, fixed output reserve, and any maximum dynamic tool transcript fit the model input limit. Provider telemetry is diagnostic only and never substitutes for this proof. Automatic compaction or an implicit capacity reduction is forbidden.

Each native record is at most 256 KiB; combined stdout/stderr is at most 8 MiB; at most 4,096 normalized events and one 8,192-byte final candidate are accepted. Stage timeout remains the total invocation deadline. `source_search` owns its call, concurrency, query, result, byte, and backend-time limits. With no search policy, no runtime tool loop exists.

## Lifecycle, Events, Cancellation, And Recovery

Native events are observational and may create only bounded `invocation.progress` records. The common lifecycle creates `invocation.started` and creates `invocation.finished` only after coordinator validation. `queued` remains phase-work state; timeout terminalizes the invocation as `failed` with `provider_timeout`; SQLite invocation terminal states remain `completed`, `failed`, `cancelled`, and `interrupted`.

`invocation.progress` contains invocation ID, a closed kind (`model`, `source_search`, or `runtime`), bounded phase/subject, completed/total when available, and dropped-record counters. No native terminal event completes an invocation. The coordinator terminalizes only after the process and event stream close, provider/model identity is verified, exactly one candidate is extracted, and ordinary proposal validation succeeds. Duplicate and late events cannot change terminal state.

The common launcher creates a new process group through a small supervised wrapper. The wrapper blocks vendor execution until the parent atomically stores invocation ID, PID, process-group ID, OS process-start token, adapter state path, and lease token. Cancellation closes result admission, validates the stored process identity, requests native cancellation, escalates to group `SIGTERM` and then `SIGKILL` after five seconds, waits for process death, discards late output, cleans temporary state, and only then terminalizes exactly once.

Startup reconciliation inspects every running invocation. A matching live process group is cancelled and killed; a missing process is marked interrupted. A PID/start-token mismatch is never signalled: result admission and the lease are fenced, the invocation terminalizes exactly once as `interrupted` with `process_identity_unconfirmed`, and its state is quarantined. Lost stdio, RPC, or SSE channels are never resumed. Result publication requires the still-current lease and running invocation, so late completion after cancellation, lease loss, or restart is discarded.

Adapter state lives in an owner-only invocation directory keyed by project and invocation IDs. Cleanup is idempotent. Startup scavenging removes only directories whose recorded process identity is absent or has already been terminalized; live or unverifiable identities are quarantined and diagnosed rather than deleted.

## Explicit Retry

Runtime failure never starts another profile automatically. The existing explicit recovery preview may offer another currently compatible profile. Apply binds the failed invocation, current work and catalog fingerprints, chosen profile, and an idempotency key; it creates a new run and invocation with `retry_of_invocation_id` and a new immutable snapshot. It never reuses or continues native fragments or an invalid candidate.

## Workspace, Migration, And Rollback

The capability endpoint returns the bounded fixed runtime catalog grouped by adapter, provider, and model. The profile editor saves only catalog-backed values with an expected catalog fingerprint. Dispatcher details resolve immutable identity from the invocation's run snapshot and show adapter protocol, executable summary, provider/model, reasoning, permission/tool policy, context/output capacity, normalized failure, and explicit retry lineage.

Schema-version-1 is the current unwrapped name-to-profile map with `provider="codex-cli"`. Reading it normalizes in memory to `agent_runtime="codex-cli"` and `model_provider="openai-codex"` without writing. This is deterministic because the existing executable is invoked with `--ignore-user-config`. Migration preserves `instructions_version="1"`, drops persisted derived `input_context_tokens`, `context_estimator_version`, and `capability_fingerprint`, and proves that the same catalog fingerprint resolves them again. Unknown legacy providers remain readable but unavailable.

A typed migration preview returns the normalized version-2 file, current file fingerprint, catalog fingerprint, unresolved profiles, and exact behavior impacts. Apply requires both fingerprints and an idempotency key, revalidates under the project user-state lock, writes one owner-only pre-migration backup, and atomically replaces only `agent-profiles.json`. The existing operational SQLite mutation store records the key, input and output fingerprints, and terminal result in the same owner operation: a crash before replacement leaves version 1, while a retry after replacement recognizes the exact output fingerprint and completes the same operation without another rewrite.

Downgrade preview emits the exact version-1 map only when every current profile is `codex-cli`, uses `openai-codex`, has no `source_search`, and all fields are exactly representable. Otherwise downgrade is blocked without mutation. Emergency restoration of the old backup is a separate explicitly destructive recovery action because it may discard later profile edits; normal rollback never restores a stale whole-store backup or rewrites OpenCode/Pi profiles as Codex.

SQLite changes are additive nullable invocation identity, retry-lineage, process-identity, and diagnostic columns under the existing idempotent owner-only migration. Old rows remain untouched and display unavailable markers. Repository generations are unaffected.

## Audit Matrix

| Concern | Required evidence |
| --- | --- |
| Catalog | Only the three exact products and conformed protocol versions are selectable |
| Authority | Coordinator envelope, schema, evidence validator, lease, and single writer remain common |
| Isolation | Fresh process/session; no native tools for OpenCode/Pi; only the private search bridge when enabled |
| Identity | Full run audit identity and derived invocation-binding reuse identity are distinct and frozen |
| Capacity | Prepared input, framing, output reserve, and dynamic search maximum fit before slot allocation |
| Protocol | Bounded machine records, exact terminal proof, provider/model echo, one candidate |
| Cancellation | Native abort, TERM/KILL, process identity fencing, late-output rejection, restart reconciliation |
| Events | Existing statuses and bounded normalized events only; native terminal events are observational |
| Retry | Explicit reviewed new run only; no automatic profile or model transition |
| Migration | In-memory v1 read, reviewed atomic v2 apply, representability-checked downgrade |
| Privacy | Raw runtime data stays only in live invocation-private vendor state and never enters coordinator records |
| Composition | Every selected runtime conforms to `source_search` when the role enables it |
| Compatibility | Codex provider migration and the derived cross-run reuse fingerprint are deterministic |

## Execution Plan

1. Extract current Codex process and catalog behavior behind the fixed adapter contract without duplicating coordinator logic.
2. Add schema-version-2 profile reading, typed migration preview/apply, catalog-fingerprint save, and representable downgrade.
3. Add the supervised process launcher, fenced identity persistence, bounded event normalization, cancellation, and startup cleanup.
4. Add Pi `pi-rpc/v1` and OpenCode `opencode-server/v1` probes and transports with no native tools.
5. Conform all three adapters to the private `source_search` bridge and capacity reserve contract.
6. Extend snapshots and the existing inspector projection; retain the full audit fingerprint and derive the bounded cross-run reuse fingerprint.
7. Add explicit alternate-profile retry through the existing recovery boundary.
8. Implement target-first, synchronize reusable runtime/scaffold outputs, rebuild packages, and run the full verification matrix.

## Risks / Trade-offs

- OpenCode server transport is heavier than `run --format json`, but the latter cannot prove terminal provider/model identity or acknowledge native cancellation.
- Disabling native OpenCode/Pi tools sacrifices vendor-specific exploration in exchange for one enforceable common search and evidence boundary.
- Vendor authentication may expire during a run; the adapter reports authentication unavailable and never logs in or copies credentials.
- Exact feature probes permit future versions without speculative semver compatibility, but every protocol change requires new fixtures.
- Explicit retry avoids hidden duplicate cost and provider changes, but requires an operator action after failure.

## Assumptions And Open Questions

- Assumption: the separately reviewed `source_search` change is delivered before enabling OpenCode or Pi for a role whose profile contains that policy.
- Assumption: each supported vendor credential can be exposed through the adapter's allowlisted read-only reference or authentication environment without exposing the broader user configuration.
- Open questions: none required for implementation.
