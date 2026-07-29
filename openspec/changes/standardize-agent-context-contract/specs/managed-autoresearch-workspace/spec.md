## ADDED Requirements

### Requirement: Diagnose agent context safely
The managed workspace SHALL expose bounded project-scoped context diagnostics through the existing dispatcher inspection, including context-envelope, estimator, and selection-policy versions, prepared-input estimate and headroom, origin groups, policy-exclusion groups, binding fingerprints, and reuse compatibility without exposing prompts, private reasoning, credentials, unrestricted provider output, or arbitrary file content or representing estimates as provider telemetry.

#### Scenario: Inspect a current agent invocation
- **WHEN** a user opens dispatcher details for an invocation with `context-envelope/v1`
- **THEN** the inspector MUST show the context-window limit, prepared-input estimate, structured-response reserve, headroom, measurement units, versions, prepared-input and envelope fingerprints, counts by context and origin kind, and bounded included and policy-excluded summaries.

#### Scenario: Inspect a capacity failure
- **WHEN** an invocation is not started because its complete selected context does not fit
- **THEN** the related stage and dispatcher diagnostics MUST show the typed blocker, required class, estimated deficit, limit, estimator version, and recovery guidance without exposing source content.

#### Scenario: Inspect a reuse rejection
- **WHEN** a prior result cannot be reused because a context binding changed
- **THEN** the inspector MUST show the first incompatible field and old and current safe fingerprints or versions without returning the prior result payload.

#### Scenario: Inspect a legacy invocation
- **WHEN** an invocation predates the context-envelope contract
- **THEN** the inspector MUST preserve its known operational data and show context budget, provenance, and selection diagnostics as unavailable rather than inventing values.

#### Scenario: Context diagnostics exceed response bounds
- **WHEN** included descriptors, policy-exclusion groups, or origin groups exceed their server-owned response bounds
- **THEN** the existing dispatcher detail resource MUST return deterministic bounded lists, complete aggregate counts, truncation markers, and opaque project-scoped cursors without introducing a second diagnostic API.

#### Scenario: Diagnostic text contains markup or secrets
- **WHEN** repository-derived labels or provider errors contain markup or match secret-bearing fields
- **THEN** the service MUST redact secret-bearing values and the browser MUST render remaining text literally without execution.

### Requirement: Diagnose deterministic context decisions without fake invocations
The managed workspace SHALL expose bounded provenance for deterministic workflow decisions and compatible result reuse that replace an agent call while keeping them distinct from dispatcher invocations and agent-budget accounting.

#### Scenario: Inspect deterministic whole-component classification
- **WHEN** stage 2 classifies a wholly added or deleted component without an agent
- **THEN** the workspace MUST show `execution_kind=deterministic`, algorithm version, decision fingerprint, input bindings, affected DIF count, and bounded origin summary, and MUST show no provider invocation, slot, model, or consumed agent budget.

#### Scenario: Inspect reused phase work
- **WHEN** compatible prior agent output satisfies current work without a provider call
- **THEN** the workspace MUST show `execution_kind=reused`, source result reference, compatibility fingerprint, and safe reuse summary, and MUST show no new invocation, slot allocation, or consumed provider budget.

#### Scenario: Inspect phase context accounting
- **WHEN** a user inspects an agent phase with planned, started, reused, deterministic, failed-preflight, and completed work
- **THEN** the workspace MUST show exact counts by execution kind and aggregate prepared-input estimates for started provider calls without presenting monetary cost or provider-managed transcript usage.
