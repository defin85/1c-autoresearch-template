## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Control split DIF and MRQ jobs independently
The managed workspace SHALL map stage 2 only to `analyze-dif` and stage 3 only to `consolidate-mrq`, and SHALL expose separate profiles, runs, leases, recovery actions, inspection data, and typed API actions for each job.

#### Scenario: Start stage 2
- **WHEN** the user starts a ready DIF-analysis stage
- **THEN** the service MUST launch only `analyze-dif`, process successive bounded windows, and MUST NOT invoke groupers, coordinators, MRQ restructuring, or MRQ publication.

#### Scenario: Start stage 3
- **WHEN** the user starts a ready MRQ-consolidation stage
- **THEN** the service MUST launch only `consolidate-mrq` against the complete classification generation, compatible CUS registry, and complete active MRQ graph, then stop for explicit plan approval before canonical mutation.

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
- **WHEN** a new CUS/MRQ generation invalidates active batch, target-decision, or derived-output bindings
- **THEN** the workspace MUST mark the affected later stages stale, identify the changed MRQ fingerprint, and offer only their supported explicit rebuild or revalidation actions.
