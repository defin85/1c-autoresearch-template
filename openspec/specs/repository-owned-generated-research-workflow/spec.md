# repository-owned-generated-research-workflow Specification

## Purpose
TBD - created by archiving change align-template-with-repository-owned-workflow. Update Purpose after archive.
## Requirements
### Requirement: Fresh repositories use one canonical workflow
The template SHALL generate a repository with exactly seven gates, seven jobs, and eight workflow operations whose canonical configuration is owned by `project.toml` and `research/*.toml` plus active-generation pointers.

#### Scenario: New repository is generated
- **WHEN** bootstrap creates a repository in an empty destination
- **THEN** its strict doctor MUST report the exact workflow catalog and no competing queue, stage, preference, or compatibility authority.

### Requirement: Generated state follows immutable generation boundaries
Generated repositories SHALL keep source payloads, physical differences, and MRQ graphs in immutable generation directories and SHALL update only their canonical active pointers through typed operations.

#### Scenario: Source acquisition publishes
- **WHEN** all three roles and declared external artifacts validate
- **THEN** one immutable source generation MUST be published atomically and the prior generation MUST remain addressable.

#### Scenario: Operation is unsupported
- **WHEN** a caller submits an arbitrary command or removed legacy operation
- **THEN** the typed application boundary MUST reject it before repository mutation.

### Requirement: Operational state remains outside generated repositories
Generated repositories SHALL keep connection profiles, upload drafts, source indexes, events, bookmarks, previews, preferences, and credentials in mode-confined user scope and SHALL keep only portable declarations and evidence in tracked files.

#### Scenario: User configures local access
- **WHEN** a connection, upload, index, or preview is created
- **THEN** no secret, absolute host path, temporary byte stream, or raw preview identifier MUST be written to tracked repository state.

### Requirement: Synchronization is deterministic and customer-independent
The template SHALL maintain an explicit reusable-path inventory and a safe synchronization command that accepts a reference repository, rejects customer payloads and host-specific values, and refuses to overwrite a non-empty unapproved destination.

#### Scenario: Reference contains customer evidence
- **WHEN** synchronization encounters source generations, analysis generations, binaries, credentials, absolute workstation paths, or customer-specific outputs
- **THEN** those paths MUST be excluded or the synchronization MUST fail before updating the scaffold.

#### Scenario: Same reference is synchronized twice
- **WHEN** identical reusable inputs are synchronized repeatedly
- **THEN** the generated scaffold and its manifest fingerprints MUST be identical.

### Requirement: Generated runtime includes source folder import
The generated repository SHALL expose the same versioned external-artifact folder preview, exact declaration-diff review, typed `sources.configure` confirmation, resumable draft staging, CLI, HTTP, and native web selector contract as the verified reusable implementation.

#### Scenario: Equivalent folder is imported
- **WHEN** CLI or web imports equal relative EPF/ERF paths and bytes into a fresh generated repository
- **THEN** both adapters MUST produce equivalent normalized entries, declarations, draft bytes, acquisition-pending state, and later source acquisition behavior.

### Requirement: Fresh generation is fully verified
The template SHALL test a newly generated repository with strict contract checks, relevant Python tests, web tests, and a production build before release.

#### Scenario: Template verification runs
- **WHEN** the release verification matrix is executed
- **THEN** template checks and fresh generated-repository checks MUST pass without relying on customer files or an existing workstation path.
