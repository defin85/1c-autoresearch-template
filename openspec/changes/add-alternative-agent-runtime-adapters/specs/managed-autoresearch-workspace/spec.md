## MODIFIED Requirements

### Requirement: Configure an agent profile for each stage
The system SHALL identify each stage as deterministic or agent-executed and SHALL let a user select one fixed Codex CLI, OpenCode, or Earendil Pi runtime, compatible model provider, model, reasoning setting where supported, instruction version, environment preset, and optional bounded `source_search` policy for every agent-executed stage. Tracked workflow configuration SHALL remain authoritative for worker concurrency and timeout. Every fixed adapter SHALL prove exact product and executable identity, authentication readiness, model input and output limits, required protocol, isolated execution, tool policy, bounded normalized output, source and snapshot fingerprints, cancellation, and coordinator-owned evidence validation, and the system SHALL NOT accept Claude Code, generic executable templates, arbitrary runtime options, automatic fallback, or native OpenCode/Pi tools.

#### Scenario: Stage has no valid agent profile
- **WHEN** an executable agent stage lacks a compatible enabled profile or required authentication readiness
- **THEN** the system marks the stage not ready and prevents dispatch.

#### Scenario: Deterministic stage is configured
- **WHEN** a stage only runs a fixed package validation or generation operation
- **THEN** the UI identifies it as deterministic and requires no artificial agent profile or prompt.

#### Scenario: Stage overrides a shared profile
- **WHEN** a user applies an override allowed by the stage schema
- **THEN** the system previews and records the resolved execution profile without mutating the shared profile or overriding tracked timeout and concurrency authority.

#### Scenario: Select between installed agent systems
- **WHEN** two or more of Codex CLI, OpenCode, and Earendil Pi pass their exact product, protocol, authentication, model-capacity, permission, cancellation, and required tool-transport probes for a stage
- **THEN** the user can assign any compatible profile to that stage and the run records the selected runtime, adapter protocol, provider, model, and executable identity.

#### Scenario: Runtime selection changes
- **WHEN** a user switches the profile runtime
- **THEN** provider, model, reasoning, capacity, and `source_search` compatibility choices MUST be recomputed from the selected adapter catalog rather than carried over as assumed-compatible values.

#### Scenario: Adapter cannot enforce the stage contract
- **WHEN** it cannot provide the required no-native-tool policy, optional common search bridge, structured protocol, provider/model proof, capacity, cancellation, or result normalization
- **THEN** the system marks that profile incompatible and prevents dispatch through it.

#### Scenario: Adapter output violates the common schema
- **WHEN** Codex CLI, OpenCode, or Pi returns output that fails coordinator-owned decision or evidence validation
- **THEN** the run is rejected, no alternate runtime starts automatically, and no canonical repository mutation is published.

#### Scenario: User explicitly retries with another profile
- **WHEN** a terminal failed invocation and current compatible catalog permit the supported recovery operation
- **THEN** the workspace MUST preview the new profile, runtime, model, capacity, current work bindings, new-snapshot boundary, and idempotency fingerprint before apply creates a separate run and invocation.

## ADDED Requirements

### Requirement: Manage proven runtime catalogs and profile migration
The managed workspace SHALL expose a bounded catalog of only installed fixed runtime products and proven models, SHALL save profiles against an expected catalog fingerprint, and SHALL migrate profile schema through reviewed atomic operations without losing unsupported data.

#### Scenario: A user creates or edits a profile
- **WHEN** the runtime catalog is current
- **THEN** the workspace MUST require an approved runtime, compatible provider and model, supported reasoning value, instruction version, `local-read-only` preset, and any valid optional `source_search`, while displaying tracked effective timeout and concurrency as non-profile values.

#### Scenario: A runtime or model is unavailable
- **WHEN** executable, protocol, authentication, model identity, positive context or output limit, tool policy, cancellation, or capability probing fails
- **THEN** the workspace MUST disable new selection, preserve existing profile data, and show a bounded safe cause without exposing credentials or configuration content.

#### Scenario: A catalog changes during profile save
- **WHEN** the submitted expected catalog fingerprint is stale
- **THEN** save MUST change nothing and require a new compatible selection from the refreshed catalog.

#### Scenario: Version-1 migration is previewed
- **WHEN** current profiles use the old unwrapped map
- **THEN** preview MUST show the exact normalized version-2 file, current-file and catalog fingerprints, unresolved profiles, and behavior impacts without mutating user state.

#### Scenario: A legacy profile cannot be migrated
- **WHEN** its provider is not the exact supported `codex-cli` value
- **THEN** the workspace MUST keep it readable and unavailable and require explicit review and resaving without choosing a runtime or provider.

#### Scenario: Downgrade cannot represent current profiles
- **WHEN** any profile uses OpenCode, Pi, a non-default Codex provider, `source_search`, or another version-2-only field
- **THEN** normal rollback MUST be blocked without restoring a stale whole-store backup or rewriting the profile as Codex.

### Requirement: Diagnose immutable runtime execution safely
The managed workspace SHALL expose immutable runtime and model identity, normalized lifecycle, permission and tool policy, capacity, process reconciliation, and explicit retry lineage through existing dispatcher inspection without exposing prompts, private reasoning, credentials, temporary configuration, or unrestricted output.

#### Scenario: A user inspects an invocation
- **WHEN** adapter metadata is available
- **THEN** the inspector MUST show runtime, adapter protocol, executable version and bounded fingerprint summary, model provider, model, reasoning value, permission and tool-transport versions, input and output limits, framing and dynamic reserve, capability fingerprint, and normalized status.

#### Scenario: An invocation is an explicit alternate-profile retry
- **WHEN** it has `retry_of_invocation_id`
- **THEN** the inspector MUST show both invocation identities, profiles, runtimes, models, original failure, operator action, and new-snapshot boundary and MUST NOT imply automatic fallback or transcript continuation.

#### Scenario: A runtime violates protocol or permissions
- **WHEN** it emits malformed or oversized data, requests a prohibited tool, changes provider or model, produces multiple candidates, or cannot be cancelled
- **THEN** the workspace MUST show the common typed error, bounded adapter code, dropped-record counters, and reconciliation state and MUST render all retained runtime-derived text literally after redaction.

#### Scenario: A process is reconciled after restart
- **WHEN** stored process identity is absent, killed, mismatched, or quarantined
- **THEN** the inspector MUST show the bounded outcome without exposing PID reuse details, paths, command lines, or raw output and MUST NOT offer resume of a lost machine channel.

#### Scenario: A legacy invocation lacks adapter fields
- **WHEN** immutable history predates the adapter contract
- **THEN** the inspector MUST preserve known fields and show runtime-specific identity, permissions, tool policy, process identity, and retry data as unavailable rather than inferring them.

#### Scenario: Correlated runtime events are requested
- **WHEN** safe normalized events exist for the invocation
- **THEN** the existing bounded paginated event surface MUST return only the closed common event schema and MUST represent discarded native records through counts rather than raw stdout, stderr, prompts, responses, or reasoning.
