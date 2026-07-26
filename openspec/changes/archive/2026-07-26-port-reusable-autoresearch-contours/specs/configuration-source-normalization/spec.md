## ADDED Requirements

### Requirement: Normalize supported 1C source formats
The system SHALL parse configured Designer XML/BSL and `v8unpack` source roots into deterministic, versioned snapshot records without requiring customer-specific paths.

#### Scenario: Parse Designer XML and BSL
- **WHEN** a project invokes source parsing for a valid Designer XML/BSL export
- **THEN** the snapshot records top-level metadata objects, structural parts, modules, form modules, templates, help, predefined data, and explicit diagnostics for unsupported visible parts

#### Scenario: Parse v8unpack with sidecars
- **WHEN** a project invokes source parsing for a valid `v8unpack` export
- **THEN** the snapshot prefers JSON, BSL, MXL, and other supported machine-readable sidecars and records opaque binary payloads only as bounded evidence

### Requirement: Compare normalized snapshots reproducibly
The system SHALL compare compatible source snapshots into a canonical custom-metadata inventory with stable record identities and explicit added, modified, removed, identical, and unsupported states.

#### Scenario: Rebuild without source changes
- **WHEN** the same parser version and source contents are processed twice
- **THEN** canonical snapshot hashes, metadata identities, comparison states, and sorted output records are identical

### Requirement: Assign stable diff identities
The system SHALL assign a stable diff identifier from normalized source role, object identity, part identity, change type, and path, and SHALL preserve inactive lineage when cleanup removes a diff.

#### Scenario: Inventory order changes
- **WHEN** an inventory is regenerated with different row ordering but unchanged normalized changes
- **THEN** each unchanged diff retains the same stable identifier

### Requirement: Handle large tabular artifacts safely
The system SHALL provide a paged CSV reader that supports fields larger than Python's default CSV field limit and limits interactive output by row and selected columns.

#### Scenario: Read a large metadata field
- **WHEN** an analyst requests a page containing a field larger than 131072 bytes
- **THEN** the command returns the requested rows without `_csv.Error` and without loading unrelated rows into displayed output
