## MODIFIED Requirements

### Requirement: Maintain a canonical migration requirement graph
The system SHALL maintain stable `MRQ-*` requirements, explicit many-to-many links to canonical `CUS-*` records with controlled relation roles and implementation ownership, physical DIF evidence links owned by the CUS registry, and canonical lineage for every MRQ merge, split, or supersede performed during whole-inventory consolidation.

#### Scenario: Group customizations by business scenario
- **WHEN** several classified source differences support semantic customizations that implement one user-visible migration scenario
- **THEN** their `CUS-*` records may link to one `MRQ-*` while preserving individual DIF evidence and exactly one primary implementation owner per included customization.

#### Scenario: Share a customization across requirements
- **WHEN** one semantic customization supports multiple business scenarios
- **THEN** the graph records non-primary supporting MRQ links without duplicating primary implementation ownership.

#### Scenario: Merge duplicate MRQs
- **WHEN** complete-inventory consolidation proves that two or more active MRQs represent one migration requirement
- **THEN** it MUST create exactly one target MRQ, reassign every primary CUS ownership exactly once, mark every source MRQ superseded, and record one `merge` lineage entry with rationale and evidence.

#### Scenario: Split an overloaded MRQ
- **WHEN** complete-inventory consolidation proves that one active MRQ contains multiple independent migration requirements
- **THEN** it MUST create at least two target MRQs, reassign every primary CUS ownership exactly once, mark the source MRQ superseded, and record one `split` lineage entry with rationale and evidence.

## ADDED Requirements

### Requirement: Consolidate MRQs from complete DIF evidence
The system SHALL form and consolidate MRQs only from a complete current DIF-classification generation, its compatible complete CUS registry, and the complete active MRQ graph.

#### Scenario: First MRQ generation
- **WHEN** all DIF records are classified and no active MRQ generation exists
- **THEN** stage 3 MUST form a global compatible set of CUS records, MRQs, and approved-noise dispositions covering the complete DIF inventory.

#### Scenario: Existing MRQ generation
- **WHEN** all DIF records are classified and an active MRQ graph exists
- **THEN** stage 3 MUST evaluate every active CUS, MRQ, and classification and produce exhaustive mutually exclusive identity outcomes plus complete evidence and ownership assignments.

#### Scenario: Only a partial window is available
- **WHEN** any customer DIF remains unclassified
- **THEN** no MRQ formation, merge, split, supersede, approval proposal, or canonical MRQ publication MUST run.

#### Scenario: Consolidation coverage overlaps
- **WHEN** a proposed consolidation assigns one meaning DIF as primary evidence to more than one CUS or one included CUS to more than one primary MRQ
- **THEN** validation MUST reject the complete plan and preserve the previous active CUS/MRQ generation pair.

#### Scenario: Consolidation coverage is incomplete
- **WHEN** a proposed consolidation omits a customer DIF from both primary CUS evidence membership and approved-noise disposition, or omits an included CUS from primary MRQ ownership
- **THEN** validation MUST reject the complete plan and preserve the previous active CUS/MRQ generation pair.

#### Scenario: Consolidation is approved
- **WHEN** one complete plan is explicitly approved and its source, diff, classification, prior-CUS, prior-MRQ, and plan fingerprints remain current
- **THEN** the system MUST apply all lineage and dispositions atomically and publish exactly one compatible CUS/MRQ generation pair.

#### Scenario: Consolidation inputs become stale
- **WHEN** any bound generation or plan fingerprint changes before approval
- **THEN** approval MUST fail as stale and publish no canonical mutation.

#### Scenario: Repeat an approved consolidation
- **WHEN** the same approved plan fingerprint is submitted again against unchanged inputs
- **THEN** the system MUST return the already published result and MUST NOT create another generation, approval, or identity.

#### Scenario: Publish a changed MRQ graph
- **WHEN** consolidation activates an MRQ fingerprint different from the one used by active batch, target-decision, or derived-output generations
- **THEN** their stored input bindings MUST fail comparison with the aggregate consolidation pointer, the immutable generations MUST remain addressable, and they MUST NOT satisfy stages 4, 5, or publication.

#### Scenario: Publish target decisions
- **WHEN** stage 5 publishes decisions for the current consolidation fingerprint
- **THEN** the decisions MUST reside in a separate immutable generation and the aggregate pointer transition MUST preserve the CUS/MRQ pair and consolidation identity.

#### Scenario: Carry a legacy target decision
- **WHEN** first version-4 consolidation retains or revalidates an MRQ and proves exact old-DIF to new-CUS closure plus unchanged target payload, approval, and normalized inputs
- **THEN** the approved plan MAY publish that decision into a separate version-4 decision generation with legacy provenance.

#### Scenario: Legacy target decision is incompatible
- **WHEN** any carryover condition fails
- **THEN** the decision MUST remain immutable audit evidence, MUST NOT satisfy stage 5, and MUST be reported stale for explicit rebuild.
