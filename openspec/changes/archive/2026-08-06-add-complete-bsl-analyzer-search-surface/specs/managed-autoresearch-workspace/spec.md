## ADDED Requirements

### Requirement: Configure and diagnose complete BSL Analyzer search
The managed workspace SHALL expose reviewed semantic/reference configuration and exact per-operation BSL Analyzer readiness without exposing secrets, raw queries, native output, source bodies, operational databases, or unrestricted backend configuration.

#### Scenario: An operator opens index diagnostics
- **WHEN** BSL Analyzer is configured
- **THEN** the workspace MUST show complete, partial, or incompatible surface status, executable/build/contract and surface fingerprints, actual workspace transport, supervised backend target/PID state, warm/idle/superseded lifecycle, active sessions, workspace/reference readiness, lexical and semantic readiness, operation coverage, component/index bindings and safe contract-drift causes.

#### Scenario: An operator configures semantic search
- **WHEN** the owner opens the embedding profile editor
- **THEN** it MUST support reviewed endpoint class, provider, model label, dimension and bounded limits, accept the API key only as a write-only value, show redacted probe, disclosure/cost and rebuild impact, and MUST never return or persist the secret in browser state.

#### Scenario: An operator configures ITS help
- **WHEN** the owner opens the ITS profile editor
- **THEN** it MUST show the fixed external service, disclosure warning, question/response/concurrency limits and token-configured state, accept `NAPARNIK_TOKEN` only as a write-only value, and expose no URL or CA override.

#### Scenario: An operator applies an embedding profile
- **WHEN** expected configuration and plan fingerprints plus idempotency match
- **THEN** the service MUST revalidate under the project mutation lock, atomically update owner-only state, mark affected semantic targets stale, and MUST NOT start a rebuild implicitly.

#### Scenario: A search surface is degraded
- **WHEN** semantic service, reference corpus, native action, response schema or compatible index is unavailable
- **THEN** the workspace MUST identify the exact affected operations and recovery action, distinguish an explicitly ready lexical route from an unavailable hybrid route, and report broker fallback, unsupervised backend, identity mismatch or untrusted rendezvous as incompatible rather than silently direct-stdio.

#### Scenario: A user edits an agent search policy
- **WHEN** source-search-tool v2 is selected
- **THEN** the profile editor MUST group Code, Symbol, Graph, Metadata, Diagnostics and Reference operations, show role restrictions and worst-case reserve, validate operation-specific filters and common limits, and require a new invocation after changes.

#### Scenario: A v2 invocation is inspected
- **WHEN** it used one or more complete-surface operations
- **THEN** dispatcher inspection MUST show bounded counts by operation, modality and canonical-versus-derived result class, selected route, surface/embedding/reference identities, degradation and ledger completeness without query or result content.

#### Scenario: A complete-surface rebuild or validation runs
- **WHEN** an operator starts the existing confirmed action
- **THEN** every affected workspace, semantic and reference target MUST expose visible background progress, terminal failure and recovery through the existing job and stage controls.

#### Scenario: A long operation changes state
- **WHEN** preview becomes stale, work queues, starts, retries, cancels, fails or completes
- **THEN** the initiating control and relevant stage/card MUST expose accessible status text, focus-safe recovery, keyboard operation and paginated or narrowed diagnostics without revealing secret or source content.

### Requirement: Manage complete-search service profiles in owner-only project state
The managed workspace SHALL store embedding and ITS profiles in `<state-root>/projects/<workspace-id>/search-services.json` using exact schema `search-services/v1`, owner-only permissions and the existing project mutation authority, and SHALL expose only safe profile metadata.

#### Scenario: A profile is written
- **WHEN** an authorized reviewed apply passes CSRF, idempotency, expiry and expected-fingerprint checks
- **THEN** the service MUST use confined no-follow owner-only temporary creation, fsync, atomic rename and directory fsync under the project mutation lock.

#### Scenario: A profile is read
- **WHEN** UI or diagnostics requests service state
- **THEN** the service MUST return only configured state, random secret-version ID, redacted endpoint class/HMAC prefix and safe probe state, and MUST NOT return a credential, raw endpoint, payload, embedding or native response.

#### Scenario: A secret is replaced or a profile is deleted
- **WHEN** an owner confirms the mutation
- **THEN** replacement MUST generate a new random secret-version ID, deletion MUST first disable admission and drain or cancel bounded work, and secure purge MUST affect only the selected project's profile and not shared CA material.

### Requirement: Present reviewed migration and rollback for complete search
The managed workspace SHALL preview and apply the schema-version-3 complete-search migration and any representable rollback without implicit builds, hidden disclosure or silent capability substitution.

#### Scenario: An operator previews migration from a prior machine contract
- **WHEN** schema-version-2 routes are present
- **THEN** preview MUST show the selected machine-contract-1.3 executable and surface identity, route/profile changes, stale indexes, legacy alias rejections, backup location class, disclosure implications and the fact that no build starts automatically.

#### Scenario: An operator previews rollback
- **WHEN** prior-runtime-readable schema 2 can be restored
- **THEN** preview MUST show admission closure, in-flight handling, retained or purged v2 state and the mandatory prior-runtime readiness test before rollback can complete.
