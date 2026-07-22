## 1. Synchronization Boundary

- [x] 1.1 Add a reviewed reusable-path inventory for the canonical generated runtime and explicit exclusions for customer payloads, generations, binaries, credentials, and host paths.
- [x] 1.2 Add a deterministic synchronization command that validates its reference, writes only approved scaffold paths, and refuses unsafe non-empty destinations.
- [x] 1.3 Add repeatability and rejection tests for the synchronization boundary.

## 2. Generated Repository Contract

- [x] 2.1 Replace the generated scaffold declarations with `project.toml`, the seven-gate workflow, source/indexing declarations, schemas, and active-state pointer contract.
- [x] 2.2 Port the reusable typed application service, CLI, immutable source/diff/MRQ generation modules, user-state boundary, doctor, and runner required by the canonical workflow.
- [x] 2.3 Remove legacy queue, stage, preference, compatibility-reader, and competing mutable-authority paths from newly generated repositories.
- [x] 2.4 Update generated `AGENTS.md`, README, repo map, and verification documentation to name the canonical files and commands.

## 3. Source Setup And Folder Import

- [x] 3.1 Port canonical external-artifact declarations, upload drafts, source acquisition, and validation schemas without customer binaries.
- [x] 3.2 Port folder preview storage, secure CLI scanner, exact declaration-diff preview, typed `sources.configure` confirmation, and resumable draft staging.
- [x] 3.3 Port fixed CLI and HTTP routes plus the native paged web folder selector while preserving individual upload fallback.
- [x] 3.4 Port security, parity, redaction, retry, and acquisition-pending tests.

## 4. Bootstrap And Verification

- [x] 4.1 Update bootstrap so a fresh empty destination receives the canonical generated runtime and no legacy authorities.
- [x] 4.2 Add assertions for exactly seven gates, seven jobs, eight operations, immutable path ownership, and user-scope operational state.
- [x] 4.3 Generate a fresh repository and run strict doctor, Python tests, web tests, and production build against it.
- [x] 4.4 Rebuild the distributable research template archive without overwriting unrelated top-level managed-workspace edits.
- [x] 4.5 Run template checks, template doctor, strict OpenSpec validation, and the documented release verification matrix.
