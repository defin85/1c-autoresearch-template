# customization-registry Specification

## Purpose
TBD - created by archiving change port-reusable-autoresearch-contours. Update Purpose after archive.
## Requirements
### Requirement: Maintain atomic customization records
The system SHALL maintain a canonical JSONL registry of stable `CUS-*` records representing atomic semantic customizations rather than raw export parts.

#### Scenario: Build from available evidence layers
- **WHEN** normalized metadata, stable diffs, source analysis, external artifact inventory, or existing grouping evidence is available
- **THEN** the builder creates or updates deterministic `CUS-*` records and preserves links to every contributing evidence item

### Requirement: Distinguish evidence from business conclusions
The system SHALL keep physical diff identifiers, metadata-part identifiers, source evidence, and semantic customization identity as separate linked entities.

#### Scenario: Technical diff is removed
- **WHEN** review proves that a linked diff contains only exporter or serialization noise
- **THEN** the `CUS-*` record is excluded or rebuilt according to remaining evidence without reassigning unrelated physical identifiers

### Requirement: Support external reports and processors
The system SHALL inventory unpacked external reports and processors as possible standalone customizations and SHALL retain missing source artifacts as explicit unresolved scope rather than silently dropping them.

#### Scenario: External source is available
- **WHEN** unpacked `.epf` or `.erf` source contains source-backed standalone business logic
- **THEN** the registry can create a `CUS-*` record with exact source evidence and functional summary

#### Scenario: Listed external source is missing
- **WHEN** a customer inventory references an external artifact that was not provided
- **THEN** the registry records the missing-source status and an actionable open question without inventing implementation details

### Requirement: Bootstrap and validate the registry
The system SHALL provide bootstrap, context, build, validate, and export commands and SHALL generate only empty schema scaffolds in new repositories.

#### Scenario: Bootstrap a fresh repository
- **WHEN** registry bootstrap runs in a newly generated research repository
- **THEN** it creates the required empty files and documentation without copying example customer records
