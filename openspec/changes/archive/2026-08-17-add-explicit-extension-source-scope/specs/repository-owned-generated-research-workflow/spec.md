## MODIFIED Requirements

### Requirement: Generated state follows immutable generation boundaries
The system SHALL publish source, DIF classification, MRQ consolidation, and target-decision state through separate immutable generations with explicit active pointers, source bindings, deterministic manifests, and stale-result rejection. A source generation SHALL include only configuration extensions whose discovered UUID has an explicit tracked `include` decision; excluded or unreviewed extensions SHALL NOT enter its components.

#### Scenario: Source acquisition publishes
- **WHEN** source acquisition completes with all discovered extension UUIDs explicitly reviewed
- **THEN** it publishes immutable source artifacts, included-extension component bindings, source fingerprints, and an active source pointer atomically before downstream work can begin

#### Scenario: Source acquisition encounters an unreviewed extension
- **WHEN** the current tested profiles contain a discovered extension UUID without a tracked decision
- **THEN** acquisition publishes no generation and reports `extension_scope_required` with bounded role observations

#### Scenario: Live extension inventory changed
- **WHEN** fixed bounded discovery immediately before export does not match the reviewed role observations
- **THEN** acquisition publishes no generation, reports `extension_inventory_stale`, and requires refreshed connection tests and route review

#### Scenario: Excluded extension is present
- **WHEN** a discovered extension UUID has a tracked `exclude` decision with rationale
- **THEN** route preview records the exclusion and observations while acquisition exports no payload and publishes no component for that UUID

#### Scenario: DIF analysis publishes a window
- **WHEN** the DIF analyzer completes one deterministic bounded window
- **THEN** it appends immutable, per-item, schema-valid classification results bound to the active source and DIF generation without publishing MRQ state

#### Scenario: One DIF in a window fails
- **WHEN** one item in the active DIF window fails validation or provider execution
- **THEN** no partial classification generation becomes active, successful sibling results remain retry evidence only, and the failed item remains pending

#### Scenario: MRQ consolidation publishes
- **WHEN** the grouping agent completes an accepted consolidation plan after all active DIF are classified
- **THEN** the coordinator validates full primary-DIF closure, approved noise, retained MRQ decisions, evidence, and source bindings before atomically publishing a new MRQ batch generation

#### Scenario: Target decisions publish
- **WHEN** a target decision is accepted
- **THEN** the coordinator publishes a separate immutable decision generation bound to the current MRQ batch generation

#### Scenario: Operation is unsupported
- **WHEN** any CLI, service, or UI entrypoint requests an operation outside the canonical stage model
- **THEN** the runtime rejects it without invoking a compatibility reader or mutating canonical state

### Requirement: Canonical diff workflow owns normalization and stable identities
The canonical generated runtime SHALL own source comparison, semantic normalization, physical-to-semantic path closure, stable DIF identity and lineage, target coverage, DIF classification, and MRQ inputs for the supported configuration, included extension, and external-artifact representations. Automatic extension discovery SHALL NOT create a DIF authority until the UUID is explicitly included by the tracked source contract.

#### Scenario: Supported sources are rebuilt unchanged
- **WHEN** the same tracked source contract, included extension decisions, source generation, comparison epoch, adapter versions, and normalized content are rebuilt
- **THEN** comparison IDs, stable DIF IDs, lineage, semantic extension identities, path closure, and coverage remain deterministic

#### Scenario: Excluded extension differs from the baseline
- **WHEN** an extension exists in a role but its UUID has a tracked `exclude` decision
- **THEN** its files and semantic interventions produce no physical extension row, DIF inventory row, classification unit, target-coverage row, or MRQ input

#### Scenario: Included extension differs from the baseline
- **WHEN** an extension UUID has a tracked `include` decision and its normalized component differs between roles
- **THEN** the runtime preserves the existing UUID-based physical comparison, semantic intervention analysis, path closure, stable identity, and target-coverage behavior

#### Scenario: Semantic representation covers a physical payload
- **WHEN** one physical source path maps to one or more semantic intervention rows
- **THEN** the canonical path-coverage artifact records every semantic owner and the physical row does not also appear as an independent customer requirement

#### Scenario: Representation is unsupported or inconclusive
- **WHEN** the selected adapter cannot prove a supported normalized representation or account for every physical path
- **THEN** comparison fails closed without publishing partial DIF, classification, or MRQ authority

### Requirement: Synchronization is deterministic and customer-independent
The template SHALL synchronize the canonical generated runtime, extension-scope contract schema, source workspace, tests, and documentation from a verified reference without copying customer sources, discovered extension observations, extension decisions, evidence, runtime state, credentials, or generated indexes.

#### Scenario: Reference contains customer evidence
- **WHEN** synchronization input contains source generations, extension observations or decisions, DIF, MRQ, approvals, outputs, indexes, runtime state, secrets, or customer-specific files
- **THEN** synchronization rejects the input and changes no template output

#### Scenario: Same reference is synchronized twice
- **WHEN** synchronization is run repeatedly from the same verified reusable reference
- **THEN** generated runtime files, scaffold files, workspace assets, schemas, and packaged template remain byte-identical

#### Scenario: Release version differs from reference
- **WHEN** the template release version is intentionally different from the verified reference
- **THEN** synchronization normalizes only the declared release-version fields and rejects every other unexplained runtime or contract difference

#### Scenario: Removed upstream file remains locally
- **WHEN** an owned runtime, test, schema, workspace, or documentation file is absent from the reviewed reference but present in a derived template tree
- **THEN** synchronization plans its deletion and verification fails until the stale file is removed

#### Scenario: Replacement is previewed
- **WHEN** a maintainer requests synchronization without explicit apply mode
- **THEN** the tool emits a sorted content-free add, change, and delete plan plus a fingerprint and changes no file

#### Scenario: Planned inputs change before apply
- **WHEN** the reference, owned-tree inventory, normalization inputs, or expected plan fingerprint changes after preview
- **THEN** apply fails before replacing any owned tree

#### Scenario: Replacement stops after one owned tree
- **WHEN** synchronization stops after replacing only part of the derived outputs
- **THEN** parity and package checks fail closed and an idempotent rerun from the same reference converges all owned trees

#### Scenario: Historical OpenSpec evidence is scanned
- **WHEN** forbidden-authority checks inspect active runtime, contract, documentation, test, and distribution scopes
- **THEN** archived OpenSpec history remains excluded as non-executable evidence while active change artifacts do not authorize a runtime interface

## ADDED Requirements

### Requirement: Derive component-grouped consolidation context without a new authority
The canonical workflow SHALL derive bounded component-grouped stage-3 context from existing source, DIF, classification, evidence, and target-coverage facts for included extensions and all declared external artifacts. A component group SHALL NOT have a canonical registry, stable package identifier, active pointer, approval, disposition, generation, or independent lifecycle.

#### Scenario: Component contains many DIF
- **WHEN** an included extension or declared external artifact owns more than one stable DIF
- **THEN** grouping preserves every member DIF identity, evidence, classification, and ownership and does not publish a synthetic component-level DIF

#### Scenario: Included component is wholly added
- **WHEN** an included extension UUID or declared external-artifact ID exists only in `target_cf` for the customer comparison
- **THEN** an explicitly started stage-2 job classifies every owned DIF as existing-schema deterministic `meaning` through its bounded windows without an agent call for those rows and stage 3 later receives their exact IDs through one derived component group

#### Scenario: Included component is wholly deleted
- **WHEN** an included extension UUID or declared external-artifact ID exists only in `vendor_baseline` for the customer comparison
- **THEN** an explicitly started stage-2 job classifies every owned DIF as existing-schema deterministic `meaning` through its bounded windows without an agent call for those rows and stage 3 later receives their exact IDs through one derived component group

#### Scenario: Included component exists on both sides
- **WHEN** the same extension UUID or external-artifact ID exists in both customer-comparison roles and has internal differences
- **THEN** every member DIF remains subject to ordinary stage-2 meaning-or-noise classification before stage 3 can run

#### Scenario: Component group contains several functions
- **WHEN** stage 3 finds multiple independently testable business meanings inside one derived component group
- **THEN** it may propose multiple ordinary MRQs whose primary DIF sets are disjoint and whose union covers every meaningful member DIF

#### Scenario: Compatible prior MRQs exist
- **WHEN** current member DIF and evidence still satisfy retained MRQ identity and closure rules
- **THEN** derived component grouping does not force replacement or merging and normal retention remains available

#### Scenario: Component behavior depends on another component
- **WHEN** a proposed MRQ relates primary or supporting DIF across a component boundary
- **THEN** the proposal identifies every component key and includes explicit source-backed evidence and rationale for the relationship

#### Scenario: Complete classification gate is evaluated
- **WHEN** deterministic whole-component rows and agent-produced rows together contain exactly one valid classification for every active customer DIF
- **THEN** the existing `all-dif-classified` gate becomes complete without treating the derived grouping as classification or canonical state

#### Scenario: Deterministic classification validation fails
- **WHEN** any deterministic or agent-produced row in the current bounded window fails source binding, evidence, schema, or fingerprint validation
- **THEN** the stage-2 attempt publishes none of that window, preserves earlier published windows, and leaves the exact current-window DIF pending

#### Scenario: Whole component was deleted
- **WHEN** deterministic classification or consolidation reads evidence for a wholly deleted component
- **THEN** it resolves the exact owned evidence under `vendor_baseline` in the active source generation rather than requiring an absent `target_cf` path

#### Scenario: Whole component was added
- **WHEN** deterministic classification or consolidation reads evidence for a wholly added component
- **THEN** it resolves the exact owned evidence under `target_cf` in the active source generation

#### Scenario: Deterministic rows complete stage 2
- **WHEN** deterministic rows complete the active customer DIF inventory
- **THEN** stage 2 becomes complete without an agent call and stage 3 remains stopped until its existing explicit start action

#### Scenario: Derived context is rebuilt
- **WHEN** stage 3 reconstructs component grouping from unchanged canonical inputs
- **THEN** it produces the same ordered component keys and member DIF IDs without reading or writing a package registry

#### Scenario: Component membership exceeds one context partition
- **WHEN** one derived component group cannot fit in the validated stage-3 input capacity
- **THEN** the coordinator supplies a deterministic summary and bounded ordered member pages through existing consolidation partitioning while retaining coordinator-owned proof of complete member closure and without merging the member DIF

#### Scenario: Downstream publication completes
- **WHEN** consolidation publishes accepted MRQ state
- **THEN** canonical ownership and approvals refer only to existing DIF and MRQ identities and no component-package artifact is published
