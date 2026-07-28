## ADDED Requirements

### Requirement: Installable runtime has one canonical authority surface
The installable `one_c_autoresearch` package version `0.3.0` SHALL expose only the canonical generated-repository runtime, commands, routes, schemas, and workspace behavior and SHALL contain no forbidden legacy authority. Version `0.2.0` SHALL be treated as the exact last compatible legacy package version.

#### Scenario: Distribution is inspected
- **WHEN** a wheel, source distribution, compiled workspace, template archive, or clean installation is scanned
- **THEN** no forbidden path, module, command, route, registration, `CUS-*` identifier, or subject-card schema marker exists, and two-way parity MUST reject any unexplained extra runtime or test file even if it is absent from the named forbidden inventory.

#### Scenario: Removed interface is requested
- **WHEN** a caller imports a removed module or invokes a removed command
- **THEN** the interface MUST be absent and MUST NOT route through a compatibility adapter.

#### Scenario: Legacy installation is upgraded
- **WHEN** the canonical `0.3.0` wheel is installed over a fingerprinted `0.2.0` wheel built from the exact pre-cleanup commit
- **THEN** removed modules, commands, package data, and entry points MUST be absent exactly as in a clean installation.

### Requirement: Template maintenance is isolated from project runtime
The template SHALL expose repository generation, synchronization, packaging, and `checks template|doctor|research` only through the exact retained repository-local scripts `scripts/sync_generated_runtime.py`, `scripts/build_workspace_template.py`, `scripts/bootstrap/new_research_repo.py`, and `scripts/checks/test_template.py`, `test_doctor.py`, `test_research_repo.py`. They SHALL not import a removed maintenance CLI, SHALL not be part of the installable runtime, and SHALL not be copied into generated project repositories.

Active root documentation SHALL be limited to `README.md`, `AGENTS.md`, `docs/agent/repo-map.md`, `docs/agent/verification.md`, and `docs/operator/dispatcher-inspector-rollback.md`. Root-only tests SHALL be limited to `tests/test_generated_runtime_sync.py` and `tests/test_template_release.py` in addition to the exact canonical target test inventory.

#### Scenario: Maintainer validates a target repository
- **WHEN** `checks research` is invoked through the maintenance command for a canonical repository
- **THEN** `test_research_repo.py` MUST execute that repository's canonical non-strict doctor with the current Python executable, explicit repository working directory and source path, no shell, and propagated exit status without requiring retired template contours.

#### Scenario: Maintainer creates a repository
- **WHEN** `new_research_repo.py` receives an empty destination and declared project tokens
- **THEN** it MUST copy only the validated canonical scaffold and replace only those tokens without importing runtime maintenance commands.

#### Scenario: Generated repository is inspected
- **WHEN** a fresh generated repository or its runtime CLI is inspected
- **THEN** it MUST contain no template-maintenance module, script, command, or repository-generation authority.

#### Scenario: Template archive is published
- **WHEN** root maintenance builds `research-template.zip`
- **THEN** `build_workspace_template.py` MUST write a deterministic separate release artifact containing only the validated canonical portable repository payload and MUST NOT install it as `one_c_autoresearch` package data.

### Requirement: Canonical diff workflow owns normalization and stable identities
The canonical source-routing and diff-generation workflow SHALL deterministically select supported semantic representations, publish immutable physical and semantic difference closure, assign content-derived stable `DIF-*` identities with lineage, and expose explicit diagnostics or physical-only evidence for unsupported or opaque payloads without restoring a standalone normalization CLI or cleanup queue.

#### Scenario: Supported sources are rebuilt unchanged
- **WHEN** equal routed XML/BSL, `v8unpack`, extension, and declared external-artifact inputs are rebuilt with the same contract and tool versions
- **THEN** routing fingerprints, comparison identities, sorted semantic and physical rows, stable `DIF-*` identities, and lineage MUST be identical.

#### Scenario: Semantic representation covers a physical payload
- **WHEN** an opaque or binary physical difference has an authoritative supported semantic representation
- **THEN** the physical path MUST remain accounted for in closed path coverage but MUST NOT independently create or preserve a semantic customization DIF.

#### Scenario: Representation is unsupported or inconclusive
- **WHEN** source routing cannot prove a supported semantic representation
- **THEN** the workflow MUST retain explicit bounded physical evidence and diagnostics and MUST NOT silently classify the payload as semantic customization or technical noise.

### Requirement: Canonical CSV readers accept large evidence fields
The canonical runtime SHALL configure the largest CSV field limit supported by the active Python platform, with bounded `OverflowError` backoff, and SHALL NOT restore the retired standalone paged CSV reader.

#### Scenario: Canonical evidence contains a large field
- **WHEN** a canonical CSV reader processes an evidence field larger than Python's default CSV limit
- **THEN** the field MUST be parsed without `_csv.Error`, and no standalone paging command or compatibility adapter MUST be exposed.

## MODIFIED Requirements

### Requirement: Synchronization is deterministic and customer-independent
The template SHALL maintain explicit scoped reusable and forbidden inventories and a safe synchronization command that accepts a reference repository, rejects customer payloads and host-specific values, stages and validates the complete derived output set before mutation, defaults to a sorted content-free add/change/delete plan with a content-derived fingerprint, requires that expected fingerprint for explicit apply, replaces each reviewed owned tree from the unchanged staged set, rejects unexpected stale files, blocks packaging while parity is incomplete, and refuses to overwrite a non-empty unapproved destination.

#### Scenario: Reference contains customer evidence
- **WHEN** synchronization encounters source generations, analysis generations, binaries, credentials, absolute workstation paths, or customer-specific outputs
- **THEN** those paths MUST be excluded or the synchronization MUST fail before updating the scaffold.

#### Scenario: Same reference is synchronized twice
- **WHEN** identical reusable inputs are synchronized repeatedly
- **THEN** the generated scaffold and its manifest fingerprints MUST be identical.

#### Scenario: Release version differs from reference
- **WHEN** normalized target parity is checked for release `0.3.0` against the verified canonical reference
- **THEN** only declared package and runtime version fields MAY differ, and every code, schema, command, route, asset, and behavior inventory MUST otherwise match.

#### Scenario: Removed upstream file remains locally
- **WHEN** a file absent from the staged canonical runtime remains in an owned package tree
- **THEN** synchronization MUST remove it through full-tree replacement or fail before publication, and MUST NOT silently preserve it.

#### Scenario: Replacement is previewed
- **WHEN** synchronization is invoked without explicit apply mode
- **THEN** it MUST mutate nothing and report only sorted relative paths, add/change/delete status, non-secret hashes, and one fingerprint binding the reference, staged manifest, destination, and complete change set.

#### Scenario: Planned inputs change before apply
- **WHEN** explicit apply receives a missing or mismatched expected plan fingerprint or any bound input changed after preview
- **THEN** synchronization MUST fail before mutation and require a new preview.

#### Scenario: Replacement stops after one owned tree
- **WHEN** synchronization stops after replacing only part of the validated owned-tree set
- **THEN** parity and packaging MUST fail, and a new preview plus explicit apply with the reference MUST replace every derived tree before packaging can continue.

#### Scenario: Historical OpenSpec evidence is scanned
- **WHEN** forbidden-authority verification scans the repository root
- **THEN** it MUST exclude historical OpenSpec archives and the active removal record while still rejecting the same markers in executable code, active docs, skills, tests, generated repositories, and distributions.

### Requirement: Fresh generation is fully verified
The template SHALL test a newly generated repository with canonical Python tests, web tests, a production build, clean-install and `0.2.0`→`0.3.0` package smoke checks, normalized target parity, and forbidden-authority scans before release.

#### Scenario: Template verification runs
- **WHEN** the release verification matrix is executed
- **THEN** template maintenance checks, fresh generated-repository checks, distribution-member scans, clean-install and legacy-upgrade command checks, target parity, and canonical runtime tests MUST pass without relying on customer files or an existing workstation path.
