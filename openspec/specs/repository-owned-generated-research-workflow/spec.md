# repository-owned-generated-research-workflow Specification

## Purpose
TBD - created by archiving change align-template-with-repository-owned-workflow. Update Purpose after archive.
## Requirements
### Requirement: Fresh repositories use one canonical workflow
The template SHALL generate a repository with workflow schema version `4`, exactly eight gates, nine jobs, and ten workflow operations whose canonical configuration is owned by `project.toml` and `research/*.toml` plus active-generation pointers. The catalog SHALL contain separate `analyze-dif` and `consolidate-mrq` jobs backed by `dif.classify-next` version `1` and `mrq.consolidate` version `1`, and SHALL NOT contain the legacy combined `discover-mrq` job or `mrq.discover-next` operation.

#### Scenario: New repository is generated
- **WHEN** bootstrap creates a repository in an empty destination
- **THEN** its strict doctor MUST report the exact workflow catalog and no competing queue, stage, preference, or compatibility authority.

#### Scenario: Inspect stages 2 and 3
- **WHEN** the canonical workflow catalog is loaded
- **THEN** DIF analysis and MRQ consolidation have different job identifiers, operation identifiers, dependencies, profiles, leases, runs, and recovery actions.

### Requirement: Generated state follows immutable generation boundaries
Generated repositories SHALL keep source payloads, physical differences, complete DIF classifications, MRQ graphs, and target-decision payloads in immutable generation directories and SHALL update only their canonical active pointers through typed operations.

#### Scenario: Source acquisition publishes
- **WHEN** all three roles and declared external artifacts validate
- **THEN** one immutable source generation MUST be published atomically and the prior generation MUST remain addressable.

#### Scenario: DIF analysis publishes a window
- **WHEN** one bounded DIF window completes validation
- **THEN** one accumulated immutable classification generation MUST be published atomically with exact source and physical-diff bindings while prior classification generations remain addressable.

#### Scenario: One DIF in a window fails
- **WHEN** any selected DIF lacks a valid result
- **THEN** the complete window MUST publish no classification pointer update, compatible successful envelopes MAY be reused only by explicit same-job recovery, and no MRQ work MUST start.

#### Scenario: MRQ consolidation publishes
- **WHEN** a complete consolidation plan passes approval and validation
- **THEN** one immutable MRQ generation MUST be activated through one atomically replaced consolidation pointer and no partial generation MUST be visible to a supported reader.

#### Scenario: Target decisions publish
- **WHEN** stage 5 decisions pass their typed approval against the current consolidation fingerprint
- **THEN** one immutable decision generation MUST be written and an aggregate compare-and-swap MUST update only its decision binding while preserving the active MRQ generation, consolidation receipt, and compatible batch binding.

#### Scenario: Operation is unsupported
- **WHEN** a caller submits an arbitrary command or removed legacy operation
- **THEN** the typed application boundary MUST reject it before repository mutation.

### Requirement: Operational state remains outside generated repositories
Generated repositories SHALL keep connection profiles, upload drafts, source indexes, events, bookmarks, previews, preferences, and credentials in mode-confined user scope and SHALL keep only portable declarations and evidence in tracked files.

#### Scenario: User configures local access
- **WHEN** a connection, upload, index, or preview is created
- **THEN** no secret, absolute host path, temporary byte stream, or raw preview identifier MUST be written to tracked repository state.

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

### Requirement: Generated runtime includes source folder import
The generated repository SHALL expose the same versioned external-artifact folder preview, exact declaration-diff review, typed `sources.configure` confirmation, resumable draft staging, CLI, HTTP, and native web selector contract as the verified reusable implementation.

#### Scenario: Equivalent folder is imported
- **WHEN** CLI or web imports equal relative EPF/ERF paths and bytes into a fresh generated repository
- **THEN** both adapters MUST produce equivalent normalized entries, declarations, draft bytes, acquisition-pending state, and later source acquisition behavior.

### Requirement: Fresh generation is fully verified
The template SHALL test a newly generated repository with canonical Python tests, web tests, a production build, clean-install and `0.2.0`→`0.3.0` package smoke checks, normalized target parity, and forbidden-authority scans before release.

#### Scenario: Template verification runs
- **WHEN** the release verification matrix is executed
- **THEN** template maintenance checks, fresh generated-repository checks, distribution-member scans, clean-install and legacy-upgrade command checks, target parity, and canonical runtime tests MUST pass without relying on customer files or an existing workstation path.

### Requirement: Complete DIF classification before MRQ consolidation
The workflow SHALL classify the complete active customer DIF inventory in bounded windows before making MRQ consolidation runnable.

#### Scenario: More than one DIF window remains
- **WHEN** a stage-2 run completes a validated window and unclassified customer DIF records remain
- **THEN** it MUST publish accumulated classification state, select the next window, and continue without invoking MRQ consolidation.

#### Scenario: A DIF is still unclassified
- **WHEN** the active classification generation lacks a valid row for any customer DIF
- **THEN** `all-dif-classified` MUST remain incomplete and `consolidate-mrq` MUST be non-runnable with the exact remaining count.

#### Scenario: Every DIF is classified
- **WHEN** the active classification generation contains exactly one valid meaning-or-noise-candidate row for every active customer DIF and no extra row
- **THEN** `all-dif-classified` MUST become complete and stage 3 MAY be started explicitly.

#### Scenario: DIF inventory is empty
- **WHEN** the active customer DIF inventory contains no records
- **THEN** stage 2 MUST publish an explicit valid empty classification generation without an agent call and stage 3 MUST remain an explicit separately approved operation.

#### Scenario: Inputs change after classification
- **WHEN** the active source or physical-diff generation no longer matches the classification pointer
- **THEN** the gate MUST become stale, stage 3 MUST be blocked, and no classification or MRQ pointer MUST be silently rebound.

#### Scenario: Recompute creates new bindings
- **WHEN** a supported source or diff recompute publishes a new active input generation
- **THEN** it MUST replace pre-stage-3 MRQ revalidation with a new empty classification generation, import only rows whose stable DIF, physical evidence, result schema, analyzer instruction/profile, and context allowed-path fingerprints remain equal, and require stage 2 for every remaining DIF.

### Requirement: Migrate the split workflow fail closed
The generated runtime SHALL provide an explicit migration from compatible legacy analysis results and SHALL NOT resume a legacy combined dispatcher run under the split catalog.

#### Scenario: Compatible analyzer results exist
- **WHEN** migration finds analyzer result envelopes matching the active source, diff, work-unit, profile, instruction, context, and schema fingerprints
- **THEN** it MUST validate and import those rows into an immutable classification generation and report exact imported and remaining counts.

#### Scenario: Preliminary grouping results exist
- **WHEN** migration finds legacy grouper, coordinator, or partial MRQ proposal results
- **THEN** it MUST NOT treat them as canonical stage-3 input or publish them as MRQs.

#### Scenario: Legacy run is active
- **WHEN** workflow migration encounters an active or resumable `discover-mrq` lease
- **THEN** one SQLite transaction MUST mark its invocation and phase work interrupted, preserve its evidence and audit identity, replace its lease with the fenced `workflow-migration` lease, and require an explicit start of the appropriate new job.

#### Scenario: Migration is interrupted
- **WHEN** the process stops after any migration phase
- **THEN** the next invocation MUST use the durable migration journal to resume or restore the exact workflow files, operational database, and prior pointer contents without deleting evidence or repeating a committed effect.

#### Scenario: Migration is repeated after commit
- **WHEN** the migration command is invoked after the same catalog transition committed
- **THEN** it MUST return the recorded result without changing files, pointers, leases, or generations.

#### Scenario: Another entrypoint is called during migration
- **WHEN** the durable version-3-to-version-4 journal exists in any phase before `committed`
- **THEN** every server, CLI, doctor, dispatcher, recompute, and approval entrypoint MUST reject mutation and expose only migration status or recovery.

#### Scenario: Migration lease is requested
- **WHEN** stage recompute or any non-legacy dispatcher lease is active
- **THEN** the global-exclusive `workflow-migration` lease MUST be rejected without interrupting that run.

#### Scenario: Only a legacy discovery lease is active
- **WHEN** the only conflicting lease is active or resumable `discover-mrq`
- **THEN** one SQLite transaction MUST terminalize that run as interrupted, replace its lease with the fenced migration lease, and insert a `handoff_prepared` migration row sufficient to reconstruct the external journal without an unfenced gap.

#### Scenario: Process stops after lease handoff
- **WHEN** `workflow-migration` and its `handoff_prepared` SQLite row committed but the external migration journal does not exist
- **THEN** restart recovery MUST validate the same fence, reconstruct the journal idempotently from the row, and permit no other mutation before recovery completes.

#### Scenario: Another lease is requested during migration
- **WHEN** the global-exclusive `workflow-migration` lease exists
- **THEN** every other lease acquisition and mutating entrypoint MUST fail before repository or operational-state mutation.

#### Scenario: Rollback restores a live legacy lease backup
- **WHEN** the pre-migration database contained an active or resumable `discover-mrq` lease
- **THEN** rollback MUST restore its evidence but persist the lease as interrupted before releasing the migration fence.

#### Scenario: Legacy target decisions exist
- **WHEN** migration or first version-4 consolidation reads approved target decisions embedded in MRQ version 1
- **THEN** it MUST carry only decisions whose retained or revalidated MRQ identity, exact DIF closure, target payload, approval fingerprint, and normalized inputs remain compatible into a separate decision generation, and report every other decision stale.

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
