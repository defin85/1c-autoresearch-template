## MODIFIED Requirements

### Requirement: Guide initial project setup
The system SHALL provide a resumable setup wizard that creates or opens a research project, collects product and version metadata, selects source roles and infobases, configures access policy, validates prerequisites, discovers configuration extensions, requires an explicit tracked include or exclude decision for every discovered extension UUID, and selects enabled workflow stages. The workspace SHALL present configuration extensions separately from uploaded external files.

#### Scenario: Required input is unavailable
- **WHEN** a wizard step cannot validate a required path, infobase connection, credential reference, source role, or extension-scope decision
- **THEN** the wizard preserves entered data, reports the exact failed check, and prevents completion of dependent setup steps

#### Scenario: Resume unfinished setup
- **WHEN** a user returns to a partially configured project
- **THEN** the wizard restores saved non-secret values and resumes at the first incomplete required step

#### Scenario: Extensions are discovered
- **WHEN** tested infobase profiles report one or more extension UUIDs across the three source roles
- **THEN** the workspace shows one UUID-sorted extension row per UUID with per-role presence, activation, observed name, observed version, and the tracked include or exclude decision, while identifying activation as informational

#### Scenario: Extension has no decision
- **WHEN** a discovered extension UUID has no tracked include or exclude decision
- **THEN** source routing remains blocked with an `extension_scope_required` action directed to that exact extension row

#### Scenario: User reviews extension-decision impact
- **WHEN** the user selects `include` or `exclude`
- **THEN** the workspace explains the resulting acquisition, DIF, and deterministic-classification behavior, requires rationale for exclusion, and previews the exact tracked contract and comparison-epoch change before confirmation

#### Scenario: Extension decision is dormant
- **WHEN** a tracked extension UUID is not discovered in any current role
- **THEN** the workspace lists the decision separately as dormant, creates no routing member, and explains that the same decision applies if the exact UUID reappears

#### Scenario: Extension observations reconcile while the user edits
- **WHEN** unrelated progress or status reconciliation occurs while extension decisions are unsaved
- **THEN** the workspace preserves the exact selections and rationales and updates no UUID row from stale server data

#### Scenario: Extension review uses assistive input
- **WHEN** a user navigates the extension section by keyboard or assistive technology
- **THEN** every role observation, decision control, rationale, impact message, and validation error has an accessible name and is programmatically associated with its UUID row

#### Scenario: No uploaded external files are declared
- **WHEN** the external-artifact contract contains no EPF, ERF, source-tree, or other uploaded-file declaration
- **THEN** the workspace states that no uploaded external files are declared without implying that no extensions were discovered

### Requirement: Manage infobase connections safely
The system SHALL let users register, select, test, and update customer or vendor infobase profiles for the supported 1C MCP, web-publication, and optional direct-PostgreSQL-read channels while storing secrets in owner-only user-scope credential files, returning no stored secret values, redacting diagnostics, and defaulting to read-only access. A successful fixed connection preflight SHALL enumerate extension UUID, name, version, and activation facts but SHALL NOT include an extension in research without a separate tracked scope decision.

#### Scenario: Test a read-only connection
- **WHEN** a user saves and tests an infobase profile with valid secret references
- **THEN** the system reports verified capabilities, targets, and bounded extension observations without exposing credentials, performing a write, or deciding extension scope

#### Scenario: Request a write-capable probe
- **WHEN** an enabled stage requires a write-capable infobase operation
- **THEN** the system displays the target and impact and requires explicit confirmation for that run before dispatch

#### Scenario: Select an unsupported connection channel
- **WHEN** a user attempts to configure a channel not published by the server as supported
- **THEN** the system marks it unavailable and persists no runnable connection profile

#### Scenario: Extension observations change after preview
- **WHEN** bounded read-only discovery immediately before export finds extension presence, UUID, activation, name, or version different from the reviewed route preview
- **THEN** the system rejects acquisition as `extension_inventory_stale`, publishes no source generation, changes no tracked decision, and requires refreshed connection tests and extension review without forwarding credentials

#### Scenario: Live extension discovery exceeds a bound
- **WHEN** pre-export enumeration or UUID verification exceeds its extension-count, response-size, command-time, or overall operation bound
- **THEN** acquisition fails before export or repository publication and reports the exact exhausted bound without treating the saved observations as current

#### Scenario: Excluded extension identity is verified
- **WHEN** live UUID verification requires a temporary export of an extension whose tracked decision is `exclude`
- **THEN** the system confines the payload to private staging, publishes none of it, indexes none of it, exposes none of it to an agent, and deletes it after verification or on failure or cancellation

### Requirement: Preserve CLI and repository compatibility
The managed workspace SHALL use the canonical generated-repository artifact formats and supported runtime operations, and its absence or disablement SHALL not prevent canonical CLI operation or invalidate a canonical generated repository. Browser and CLI source setup SHALL enforce the same tracked extension decisions. Compatibility SHALL NOT include removed queue, `CUS`, subject-card, reverse-map, functional-gap, manual-cleanup, old dashboard, or compatibility-reader surfaces.

#### Scenario: Open an existing research repository
- **WHEN** a repository using the supported canonical workflow is registered
- **THEN** the UI derives its stages from canonical artifacts and requests only missing UI-specific configuration

#### Scenario: Open a legacy repository with discovered extensions
- **WHEN** existing immutable generations are present but a currently discovered extension UUID has no tracked decision
- **THEN** the workspace keeps those generations readable, marks new acquisition blocked, and requires an explicit extension decision without rewriting prior evidence

#### Scenario: Open a repository that depends on removed authorities
- **WHEN** a repository requires a removed legacy path, command, route, module, or state authority
- **THEN** the workspace MUST reject it as unsupported without importing, converting, deleting, or mutating its evidence

#### Scenario: Remove the optional UI runtime
- **WHEN** the optional web dependencies and user-scope UI state are removed
- **THEN** canonical research files, extension decisions, and canonical CLI workflows remain usable

#### Scenario: Roll back only the frontend
- **WHEN** the workspace frontend is rolled back while the enforcing backend remains active
- **THEN** CLI enforcement and canonical extension decisions remain effective and source mutation cannot bypass them

#### Scenario: Concurrent external edit precedes configuration save
- **WHEN** the tracked source contract no longer matches the fingerprint shown in the browser preview
- **THEN** the service rejects the save, preserves the external edit, and requires a refreshed preview
