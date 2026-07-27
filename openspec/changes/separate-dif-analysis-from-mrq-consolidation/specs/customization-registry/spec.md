## MODIFIED Requirements

### Requirement: Maintain atomic customization records
The system SHALL maintain a canonical JSONL registry of stable `CUS-*` records representing atomic semantic customizations rather than raw export parts. Whole-inventory consolidation SHALL derive CUS membership from the complete current DIF-classification generation before assigning implementation ownership to MRQs.

#### Scenario: Build from available evidence layers
- **WHEN** normalized metadata, stable diffs, source analysis, external artifact inventory, or existing grouping evidence is available
- **THEN** the builder creates or updates deterministic `CUS-*` records and preserves links to every contributing evidence item.

#### Scenario: Consolidate classified DIF evidence
- **WHEN** stage 3 processes a complete current classification generation
- **THEN** every meaning DIF MUST be primary physical evidence for exactly one included `CUS-*`, MAY support additional CUS records, and every approved-noise DIF MUST belong to no CUS.

#### Scenario: Semantic grouping changes
- **WHEN** consolidation merges, splits, or supersedes existing CUS identities
- **THEN** the registry MUST preserve deterministic stable identities where semantics are unchanged and record acyclic lineage for every changed identity.

### Requirement: Distinguish evidence from business conclusions
The system SHALL keep physical DIF identifiers, metadata-part identifiers, source evidence, semantic `CUS-*` identity, and `MRQ-*` implementation ownership as separate linked entities.

#### Scenario: Technical diff is removed
- **WHEN** review proves that a linked diff contains only exporter or serialization noise
- **THEN** the `CUS-*` record is excluded or rebuilt according to remaining evidence without reassigning unrelated physical identifiers.

#### Scenario: Build MRQ ownership
- **WHEN** a complete compatible CUS registry is available
- **THEN** MRQ relations and implementation ownership MUST reference `CUS-*` identities and MUST NOT replace them with direct DIF ownership.

#### Scenario: Read a legacy DIF-owned MRQ generation
- **WHEN** stage 3 receives a version-1 MRQ graph whose dispositions reference DIF identifiers
- **THEN** it MUST treat those dispositions as legacy evidence only and MAY preserve an MRQ identity only after complete DIF-to-CUS membership and CUS-to-MRQ ownership validate.

## ADDED Requirements

### Requirement: Publish compatible CUS and MRQ generations atomically
Stage 3 SHALL validate and activate its CUS and MRQ outputs by atomically replacing one aggregate consolidation pointer that is the sole version-4 authority for the pair.

#### Scenario: Both graphs validate
- **WHEN** an explicitly approved plan has complete DIF-to-CUS evidence membership, complete CUS-to-MRQ implementation ownership, valid lineage, and current input fingerprints
- **THEN** the system MUST write both immutable generations and atomically activate exactly one aggregate pointer containing both generation IDs and all bound input fingerprints.

#### Scenario: Either graph fails
- **WHEN** validation, writing, or pointer publication fails for either output
- **THEN** the aggregate pointer MUST remain unchanged and incomplete immutable generations MUST remain unreferenced.

#### Scenario: A version-4 reader loads active consolidation
- **WHEN** a server, CLI, doctor, or runner needs active CUS or MRQ state
- **THEN** it MUST resolve both through `research/active-consolidation-generation.json`, reject absent or invalid aggregate state, and ignore missing or inconsistent non-authoritative compatibility projections.

#### Scenario: Bootstrap has not consolidated
- **WHEN** a fresh version-4 repository has not published stage 3
- **THEN** its aggregate pointer MUST be in explicit `unpublished` state with no active CUS/MRQ pair and downstream gates blocked.

#### Scenario: Migration has only legacy inputs
- **WHEN** version-3 evidence has migrated but no version-4 consolidation was approved
- **THEN** the aggregate pointer MUST be in `legacy_input` state that is readable only by `consolidate-mrq` and MUST NOT satisfy downstream gates.

#### Scenario: Compatibility projection is stale
- **WHEN** a valid active aggregate pointer differs from a legacy individual pointer
- **THEN** canonical reads MUST continue from the aggregate pointer and doctor MUST report a repairable projection warning without blocking the canonical pair.
