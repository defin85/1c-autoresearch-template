## ADDED Requirements

### Requirement: Operate one canonical indexing schema
The managed workspace SHALL accept, emit, edit, diagnose, and execute only the closed schema-3 indexing configuration and SHALL NOT expose schema selection, schema-1/2 compatibility execution, downgrade, or configuration rollback.

#### Scenario: A supported project opens
- **WHEN** its exact schema-3 configuration passes closed validation
- **THEN** the workspace MUST show task-oriented backend, profile, lexical, hybrid, operation, build, and readiness states without exposing a schema selector.

#### Scenario: An obsolete indexing configuration is supplied
- **WHEN** a tracked configuration declares schema 1, schema 2, no schema, a legacy alias, or an incomplete schema-3 route set
- **THEN** the workspace MUST fail closed with `indexing.schema3_required`, MUST NOT execute or normalize the obsolete configuration, and MUST direct the operator to the reviewed release cutover procedure.

#### Scenario: An operator enables semantic search
- **WHEN** the exact BSL Analyzer contract and complete surface are ready and an enabled probed embedding profile satisfies disclosure policy
- **THEN** the workspace MUST preview explicit lexical and hybrid routes, identities, affected components, retained inactive indexes, required explicit builds, and protected-data non-impact before applying an unchanged fingerprinted plan.

#### Scenario: The reviewed configuration is applied
- **WHEN** file and plan fingerprints remain current and no conflicting invocation is admitted
- **THEN** apply MUST atomically write schema 3, start no build, delete no index, and mutate no source, generation, DIF/MRQ, decision, credential, or output state.

#### Scenario: Schema-3 indexes are missing after cutover
- **WHEN** lexical or hybrid readiness is evaluated
- **THEN** each modality MUST report its exact missing state independently and MUST require an explicit create action without reusing an obsolete-schema identity.

#### Scenario: An operator inspects obsolete operational indexes
- **WHEN** current schema-3 identities make prior instances inactive
- **THEN** the workspace MUST expose them only through fingerprinted garbage-collection preview and MUST require separate confirmation before confined deletion.

### Requirement: Complete semantic-search setup through the user interface
The managed workspace SHALL provide one end-to-end semantic-search setup flow that can reach a verified ready state without manual file editing or schema knowledge.

#### Scenario: BSL Analyzer is incompatible
- **WHEN** its exact build does not expose the approved machine contract 1.3 surface
- **THEN** the flow MUST stop at the backend step, show the detected safe version and required recovery, and MUST NOT allow semantic enablement.

#### Scenario: The embedding profile is incomplete
- **WHEN** endpoint, model, dimension, secret, probe, or remote disclosure acknowledgement is missing or stale
- **THEN** the flow MUST identify the exact missing requirement and preserve lexical readiness without claiming hybrid readiness.

#### Scenario: Semantic setup completes
- **WHEN** configuration apply and explicit hybrid index builds finish successfully
- **THEN** the workspace MUST show semantic search ready for every covered component, the selected backend and embedding identity class, and the independently ready lexical route.
