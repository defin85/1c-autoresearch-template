# unified-migration-requirements Specification

## Purpose
TBD - created by archiving change port-reusable-autoresearch-contours. Update Purpose after archive.
## Requirements
### Requirement: Maintain a canonical migration requirement graph
The system SHALL maintain stable `MRQ-*` requirements and explicit many-to-many links to `CUS-*` records with controlled relation roles and implementation ownership.

#### Scenario: Group customizations by business scenario
- **WHEN** several atomic customizations implement one user-visible migration scenario
- **THEN** they may link to one `MRQ-*` while preserving individual evidence and exactly one implementation owner per customization

#### Scenario: Share a customization across requirements
- **WHEN** one customization supports multiple business scenarios
- **THEN** the graph records shared or supporting links without duplicating implementation effort

### Requirement: Provide bounded agent contexts
The system SHALL provide show, context, and neighborhood commands that return one requirement, its linked customizations, exact evidence, and relevant target-source pointers without requiring a full graph scan.

#### Scenario: Request one requirement context
- **WHEN** an agent requests context for an existing `MRQ-*`
- **THEN** the response contains the requirement, relations, source evidence, open questions, and bounded target pointers and excludes unrelated graph records

### Requirement: Separate source understanding from target-gap decisions
The system SHALL allow source customization coverage to complete before target-system equivalence and final migration decisions are known.

#### Scenario: Source graph is complete
- **WHEN** every included `CUS-*` has one implementation owner and at least one `MRQ-*` relation
- **THEN** source coverage reports complete even if target coverage remains `needs_customer_decision`

### Requirement: Generate derived customer outputs
The system SHALL generate subject-card compatibility views, functional-gap views, compact and detailed customer registers, and a specification draft from active `MRQ-*` records without exposing internal identifiers in customer-facing prose.

#### Scenario: Rebuild outputs
- **WHEN** the canonical graph changes and output generation succeeds
- **THEN** every published requirement is traceable to its graph record and evidence while customer-facing text contains no internal `MRQ-*`, `CUS-*`, diff IDs, or repository paths

### Requirement: Validate graph integrity
The system SHALL fail publication for uncovered included customizations, duplicate implementation ownership, dangling links, stale derived views, invalid statuses, or missing required acceptance information.

#### Scenario: Link references an unknown customization
- **WHEN** graph validation encounters a relation to an absent `CUS-*`
- **THEN** validation fails with the exact relation and no canonical outputs are replaced

### Requirement: Form MRQ directly from complete DIF classifications
The system SHALL form the complete MRQ graph directly from the active complete DIF-classification generation and the complete prior MRQ graph.

#### Scenario: Direct ownership
- **WHEN** a complete consolidation plan is built
- **THEN** every meaning DIF MUST belong primarily to exactly one MRQ and every approved-noise DIF MUST belong to none.

#### Scenario: Incomplete classification
- **WHEN** any active DIF lacks a canonical classification
- **THEN** MRQ consolidation MUST remain blocked and MUST publish nothing.

### Requirement: Publish one immutable MRQ generation
The system SHALL publish MRQ schema version 3 with requirements, direct DIF dispositions, evidence, lineage, and a content-derived manifest.

#### Scenario: Approved publication
- **WHEN** the complete plan is explicitly approved and all input fingerprints remain current
- **THEN** the system MUST write one immutable MRQ generation and atomically replace the version-2 consolidation pointer.

#### Scenario: Repeated approval
- **WHEN** the same approved plan is applied again against unchanged inputs
- **THEN** the system MUST return the existing generation without identity churn.

### Requirement: Rebuild instead of migrating the removed layer
The system SHALL reset legacy consolidation state and require a fresh full MRQ consolidation.

#### Scenario: Upgrade
- **WHEN** a repository upgrades from the earlier aggregate model
- **THEN** its consolidation pointer MUST become `unpublished`, old intermediate artifacts MUST NOT be active inputs, and stage 3 MUST rebuild MRQ from classified DIF.
