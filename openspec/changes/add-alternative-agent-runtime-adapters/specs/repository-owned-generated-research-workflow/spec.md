## ADDED Requirements

### Requirement: Execute agents through fixed runtime adapters
The generated workflow SHALL execute every provider-bound work unit through the fixed `codex-cli`, `opencode`, or `pi` server-owned adapter, SHALL keep agent runtime, model provider, and model as separate immutable bindings, and SHALL preserve the common context, validation, lifecycle, and publication authority.

#### Scenario: An agent profile is resolved
- **WHEN** a stage selects a profile
- **THEN** the runtime MUST resolve the exact approved product, executable fingerprint and version, adapter protocol, model provider, model, positive input and output limits, reasoning control, permission and tool-transport versions, framing reserve, and capability fingerprint before slot allocation.

#### Scenario: An installed version is evaluated
- **WHEN** its semantic version differs from an initial Codex CLI `0.145.0`, OpenCode `1.17.18`, or Earendil Pi `0.79.1` fixture
- **THEN** it MUST remain unavailable until the exact installed executable passes the same bounded closed protocol and conformance suite, and version range alone MUST NOT establish compatibility.

#### Scenario: A profile names an arbitrary runtime
- **WHEN** it contains Claude Code, a generic executable, command template, arbitrary runtime options, unknown adapter, unproven model, or unsupported capability
- **THEN** profile validation MUST fail and no process MUST start.

#### Scenario: Any approved runtime prepares a call
- **WHEN** Codex CLI, OpenCode, or Pi is selected
- **THEN** it MUST receive the exact coordinator-rendered input and bounded response schema and its output MUST pass the same context-envelope, schema, evidence, binding, and proposal validation before publication.

#### Scenario: A runtime returns a valid-looking result
- **WHEN** the adapter normalizes a candidate response
- **THEN** the runtime MUST NOT publish it directly or bypass the coordinator-owned lease and single writer.

### Requirement: Isolate and constrain every agent runtime
The generated workflow SHALL run every adapter invocation in a fresh non-persistent process and session under the versioned `local-read-only` policy and SHALL fail closed when the adapter cannot prove the exact policy required by the selected role.

#### Scenario: An invocation starts
- **WHEN** its immutable snapshot and budget validate
- **THEN** the adapter MUST start a fresh session with no continuation, shared conversation, persistent runtime memory, automatic compaction, automatic provider retry, autonomous model selection, or cross-work-unit state.

#### Scenario: OpenCode or Pi is selected
- **WHEN** its invocation policy contains no `source_search`
- **THEN** the adapter MUST expose no native or coordinator tool and MUST disable shell, filesystem, web, question, skill, extension, nested-agent, session, and model-switch capabilities.

#### Scenario: Bounded source search is enabled
- **WHEN** the frozen role profile contains a valid `source_search` policy
- **THEN** the adapter MUST have passed the common bridge conformance suite and MUST include the bridge and worst-case dynamic transcript identities in snapshot and capacity validation; OpenCode and Pi MUST expose exactly that bridge and no native tool, while Codex CLI retains only its existing sandboxed read-only capabilities plus the bridge.

#### Scenario: A runtime requests a prohibited capability
- **WHEN** it requests any native edit, read, write, patch, shell, external-directory, web, nested-agent, user-question, session-continuation, skill, extension, compaction, retry, or model-switch capability
- **THEN** the adapter MUST reject it as a typed permission or protocol failure, close result admission, and publish no partial result.

#### Scenario: The selected provider sends the prepared input
- **WHEN** the runtime calls the frozen model-provider API
- **THEN** that provider traffic MUST be allowed while arbitrary model-visible web tools remain unavailable.

#### Scenario: Vendor authentication is required
- **WHEN** the fixed runtime uses an allowlisted owner-only read-only credential reference or authentication environment
- **THEN** the vendor process MAY authenticate normally but the service and model MUST NOT read credential contents or copy, log, return, refresh, or rewrite credential material.

#### Scenario: Temporary runtime state is created
- **WHEN** an adapter creates transport, session, or permission state
- **THEN** it MUST be owner-only, expose at most an allowlisted read-only credential reference, remain outside repository, coordinator records, UI, and logs, and be deleted after process death or safely quarantined only while process identity is live or unverifiable.

#### Scenario: OpenCode configuration is isolated
- **WHEN** an OpenCode invocation is prepared
- **THEN** it MUST use private XDG configuration, data, cache, state, and temporary roots, MUST load no global or project agent resource, and MUST receive only the selected sanitized provider definition and an allowlisted read-only `auth.json` reference.

#### Scenario: Pi input and result are proven
- **WHEN** a Pi invocation is prepared and later reports completion
- **THEN** it MUST use the fingerprinted adapter-owned system prompt and explicit empty appended prompt, require correlated success when disabling automatic retry and compaction, verify final compaction state, require every assistant message to use the frozen provider and model, require `toolUse` only on intermediate tool-call messages, and accept exactly one last text message with `stop`; `length`, terminal `toolUse`, `error`, `aborted`, any other reason, or multiplicity MUST fail without a candidate.

#### Scenario: Runtime identity changes after snapshot
- **WHEN** executable, protocol, permission mapping, model provider, model, context or output limit, tool transport, framing reserve, or capability fingerprint differs from the immutable snapshot
- **THEN** execution or completion validation MUST fail without reinterpreting the invocation under current configuration.

### Requirement: Normalize bounded lifecycle and explicit recovery across runtimes
The generated workflow SHALL normalize adapter events, cancellation, timeout, process recovery, and explicit alternate-profile retry into the existing invocation lifecycle without copying private reasoning or unrestricted runtime output into coordinator records and SHALL NOT start another runtime or model automatically.

#### Scenario: A native runtime event arrives
- **WHEN** Codex CLI, OpenCode, or Pi emits progress, tool activity, completion, failure, or cancellation data
- **THEN** the adapter MUST map a recognized event only to a closed bounded `invocation.progress` record, discard only explicitly optional unknown notifications with a counter, and MUST NOT copy reasoning, raw transcripts, prompts, credentials, or unrestricted output into coordinator records.

#### Scenario: A native terminal event arrives
- **WHEN** the vendor reports completion
- **THEN** the coordinator MUST keep the invocation non-terminal until the process and stream close, provider/model identity is verified, exactly one bounded candidate is extracted, and ordinary proposal validation succeeds.

#### Scenario: Protocol or output bounds are exceeded
- **WHEN** any record exceeds 256 KiB, combined stdout/stderr exceeds 8 MiB, normalized events exceed 4,096, more than one final candidate appears, or the candidate exceeds 8,192 bytes
- **THEN** the invocation MUST fail closed with a typed bounded error and publish nothing.

#### Scenario: Cancellation is requested
- **WHEN** an invocation is active
- **THEN** result admission MUST close, native cancellation MUST be attempted, the validated process group MUST receive TERM and KILL after the fixed deadline if needed, late output MUST be discarded, temporary state MUST be reconciled, and only then MAY the invocation terminalize exactly once.

#### Scenario: Service restarts during an invocation
- **WHEN** a running row has persisted PID, process group, OS start token, adapter state path, and lease identity
- **THEN** recovery MUST validate identity before signalling, terminate a matching live group, never signal a PID/start-token mismatch, fence its lease and result admission, quarantine unverifiable state, never resume a lost machine channel, reject late publication, and mark the invocation interrupted exactly once.

#### Scenario: An operator retries with another profile
- **WHEN** a supported explicit recovery preview binds the terminal invocation, current work and catalog fingerprints, chosen compatible profile, and idempotency key
- **THEN** apply MUST create a new run and invocation with a new immutable snapshot and `retry_of_invocation_id`, without continuing native fragments or an invalid candidate.

#### Scenario: No explicit recovery is applied
- **WHEN** an invocation fails, times out, is interrupted, or is cancelled
- **THEN** no other profile, runtime, model, retry, or downstream job MUST start automatically.

#### Scenario: Result reuse is considered
- **WHEN** prior work was produced by any adapter
- **THEN** reuse MUST require equality of a derived immutable invocation-binding fingerprint that excludes run ID, policy source, retry lineage, and unrelated profiles but contains runtime, executable, protocol, permission, provider, model, capacity, framing, bridge, context, instruction, schema, and result bindings and MUST report the first incompatible field; the full run-snapshot fingerprint remains the audit identity.

### Requirement: Migrate Codex profiles without fabricating provider identity
The generated workflow SHALL read current Codex profiles without mutation, SHALL migrate them through a typed reviewed operation to schema version 2, and SHALL preserve immutable legacy evidence and exact rollback representability.

#### Scenario: A schema-version-1 Codex profile is read
- **WHEN** its current `provider` is exactly `codex-cli`
- **THEN** the reader MUST normalize it in memory to `agent_runtime=codex-cli` and `model_provider=openai-codex`, preserve `instructions_version="1"`, and omit persisted derived capability fields without writing because the current `--ignore-user-config` execution uses that built-in provider.

#### Scenario: A legacy provider is unknown
- **WHEN** its provider is not exactly the supported `codex-cli` value
- **THEN** the profile MUST remain readable but unavailable and require explicit resaving rather than guessing.

#### Scenario: Profile migration is applied
- **WHEN** normalized version-2 preview, current-file fingerprint, catalog fingerprint, and idempotency key still match under the user-state lock
- **THEN** the service MUST create one owner-only pre-migration backup and atomically replace only `agent-profiles.json`; stale or invalid input MUST change nothing.

#### Scenario: Profile downgrade is previewed
- **WHEN** every current profile is Codex CLI with `openai-codex`, contains no `source_search`, and is exactly representable by schema version 1
- **THEN** the service MAY emit and atomically apply the exact old map, otherwise it MUST block downgrade without rewriting any profile.

#### Scenario: Legacy invocation details are inspected
- **WHEN** old rows lack explicit runtime or model-provider metadata
- **THEN** readers MUST show unavailable markers and MUST NOT rewrite or infer immutable history.
