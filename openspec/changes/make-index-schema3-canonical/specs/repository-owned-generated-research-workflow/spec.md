## ADDED Requirements

### Requirement: Generate and distribute schema-3-only source search
The canonical generator and installable runtime SHALL emit and execute only schema-3 indexing configuration and SHALL remove schema-1/2 and source-search-tool-v1 authorities from every distributed and generated surface.

#### Scenario: A new research repository is generated
- **WHEN** bootstrap creates an empty destination
- **THEN** the repository MUST contain the exact schema-3 configuration structure, complete explicit routes and stable service-profile identifiers, and MUST contain no credential, endpoint, operational index, compatibility reader, migration queue, downgrade, or rollback authority.

#### Scenario: Runtime and template are synchronized
- **WHEN** an unchanged fingerprinted synchronization plan is applied
- **THEN** canonical and generated runtime trees MUST converge on schema-3-only code and tests while project configuration, evidence, sources, generations, decisions, outputs, credentials, and operational indexes remain outside the replacement set.

#### Scenario: A generated project from an obsolete release is discovered
- **WHEN** its indexing configuration is not schema 3
- **THEN** the new runtime MUST reject it without mutation and release tooling MUST require the explicit reviewed cutover before installing the incompatible runtime.

#### Scenario: Existing operational indexes are present during cutover
- **WHEN** a project adopts schema 3
- **THEN** no index payload MUST be converted, copied, promoted, overwritten, or deleted; only explicitly built schema-3 instances MAY become current.

#### Scenario: Obsolete schema authority is checked
- **WHEN** release verification scans executable source, generated source, API, CLI, UI, tests and active documentation
- **THEN** it MUST fail if schema-1/2 indexing parsers, serializers, aliases, migration/downgrade/rollback paths, or source-search-tool-v1 execution return, while ignoring unrelated domain schema versions.

### Requirement: Cut over generated projects without customer-data mutation
The release workflow SHALL cut over every operator-approved generated project through an exact reviewed schema-3 configuration operation before removing obsolete readers and SHALL prove protected project state unchanged. Discovered projects explicitly excluded by the operator SHALL remain untouched.

#### Scenario: The current generated project is previewed
- **WHEN** `sppr-research-ver2` is selected for cutover
- **THEN** preview MUST bind its exact tracked indexing-file fingerprint, compatible BSL Analyzer surface, service profiles, route changes and affected components and MUST state that indexes will be rebuilt rather than migrated.

#### Scenario: The current generated project is cut over
- **WHEN** the reviewed plan is applied
- **THEN** only its tracked indexing configuration MAY change, no build may start implicitly, and before/after manifests for protected sources, generations, DIF/MRQ state, decisions and outputs MUST match.

#### Scenario: A discovered project is excluded from the change
- **WHEN** the operator excludes `/Projects/OneC/sppr-research` from the cutover set
- **THEN** synchronization, cutover, verification and cleanup MUST NOT modify its tracked configuration, runtime, evidence or operational indexes.

#### Scenario: Obsolete runtime code is contracted
- **WHEN** every inventoried project is verified on schema 3
- **THEN** the temporary cutover helper and every schema-1/2 runtime branch MUST be deleted before the incompatible release artifact is built.
