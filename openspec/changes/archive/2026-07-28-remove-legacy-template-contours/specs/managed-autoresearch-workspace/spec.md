## MODIFIED Requirements

### Requirement: Preserve CLI and repository compatibility
The managed workspace SHALL use the canonical generated-repository artifact formats and supported runtime operations, and its absence or disablement SHALL not prevent canonical CLI operation or invalidate a canonical generated repository. Compatibility SHALL NOT include removed queue, `CUS`, subject-card, reverse-map, functional-gap, manual-cleanup, old dashboard, or compatibility-reader surfaces.

#### Scenario: Open an existing research repository
- **WHEN** a repository using the supported canonical workflow is registered
- **THEN** the UI derives its stages from canonical artifacts and requests only missing UI-specific configuration.

#### Scenario: Open a repository that depends on removed authorities
- **WHEN** a repository requires a removed legacy path, command, route, module, or state authority
- **THEN** the workspace MUST reject it as unsupported without importing, converting, deleting, or mutating its evidence.

#### Scenario: Remove the optional UI runtime
- **WHEN** the optional web dependencies and user-scope UI state are removed
- **THEN** canonical research files and canonical CLI workflows remain usable.

#### Scenario: Concurrent external edit precedes configuration save
- **WHEN** `project.toml` no longer matches the fingerprint shown in the browser preview
- **THEN** the service rejects the save, preserves the external edit, and requires a refreshed preview.

## REMOVED Requirements

### Requirement: Expose existing dashboards as isolated results
**Reason**: The clean-comparison, old review, and functional-gap dashboards belong to removed legacy contours and would retain obsolete routes and artifact authorities.
**Migration**: Use the canonical workspace, dispatcher inspector, retained invocation results, and supported artifact downloads; preserve historical dashboard files outside the upgraded package if they are still needed for audit.
