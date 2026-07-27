## MODIFIED Requirements

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
