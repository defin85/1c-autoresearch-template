## ADDED Requirements

### Requirement: Build an auditable cleanup queue
The system SHALL derive deterministic cleanup candidates from configured vendor and customer source roles and record stable row identifiers, source paths, evidence classes, and current decisions.

#### Scenario: Rebuild unchanged cleanup queue
- **WHEN** queue initialization runs against unchanged source refs and configuration
- **THEN** existing stable row identities and completed decisions are preserved

### Requirement: Support batch source-backed review
The cleanup contour SHALL provide bounded source contexts, normalized comparisons, candidate probes, structured review output, and block-scoped validation for manual or model-assisted classification.

#### Scenario: Review a batch
- **WHEN** a reviewer receives a cleanup batch
- **THEN** every row is classified from exact vendor/customer source evidence and expensive rebuilds are deferred until the block is applied

### Requirement: Prefer semantic representations over binaries
The cleanup contour SHALL ignore an opaque binary difference when an adjacent supported machine-readable representation covers the same payload and is semantically unchanged.

#### Scenario: Binary changes but sidecar is identical
- **WHEN** a binary payload differs while its authoritative JSON, XML, MXL, or BSL sidecar is normalized-identical
- **THEN** the binary difference is classified as technical noise and cannot independently preserve a customization

### Requirement: Apply cleanup with one writer
The system SHALL apply validated `remove_noise` and `keep_customization` decisions through one writer, preserve the decision ledger, rebuild the clean comparison at block boundaries, and prevent unresolved review states from being published as clean.

#### Scenario: Batch contains unresolved rows
- **WHEN** any row remains `manual_review` after the configured review cascade
- **THEN** publication stops and reports the exact unresolved rows for direct resolution

### Requirement: Publish a clean source repository without run caches
The system SHALL be able to create a publishable clean comparison repository whose history and working tree contain source evidence and final semantic differences but not worker traces, temporary workspaces, or customer-independent template files.

#### Scenario: Build publishable repository
- **WHEN** all blocking cleanup decisions are resolved
- **THEN** the generated repository validates against configured vendor and customer refs and exposes only retained semantic changes
