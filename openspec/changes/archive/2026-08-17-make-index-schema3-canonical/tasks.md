## 1. Reconcile prerequisites and inventory

- [x] 1.1 Archive `add-complete-bsl-analyzer-search-surface` and reconcile its temporary schema-2 migration/rollback requirements before changing executable code.
- [x] 1.2 Inventory generated research repositories and record the operator-approved cutover set: only `sppr-research-ver2`; leave `/Projects/OneC/sppr-research` untouched by explicit user decision.
- [x] 1.3 Capture manifests for canonical template/runtime parity and for the target's protected sources, generations, DIF/MRQ state, decisions, outputs, and tracked configuration in `prerequisites.md`.
- [x] 1.4 Obtain and live-probe BSL Analyzer commit `3c97c237200fcfe90547f8960bb93d3a623df4d7`; require machine contract 1.3 and the complete pinned search-surface manifest before any target cutover.

## 2. Define the single schema-3 contract

- [x] 2.1 Replace multi-version indexing types with one closed schema-3 configuration type and exact parser/serializer.
- [x] 2.2 Define the canonical template's explicit lexical, hybrid, symbol, graph, metadata, diagnostics, and reference routes plus stable service-profile bindings.
- [x] 2.3 Reject schema 1, schema 2, missing versions, unknown fields, legacy aliases, and incomplete routes with one typed `indexing.schema3_required` diagnostic and no normalization or mutation.
- [x] 2.4 Prove lexical and hybrid target identities remain distinct and that embedding identity changes stale only hybrid instances and bound reuse.

## 3. Deliver the reviewed existing-project cutover

- [x] 3.1 Add a maintenance-only preview/apply helper or transitional endpoint that accepts only the exact inventoried schema-2 file fingerprint and emits the closed schema-3 document without executing generic legacy semantics.
- [x] 3.2 Show exact backend/surface/profile identities, route changes, disclosure, affected components, inactive old instances, explicit rebuild requirement, and protected-data non-impact in preview.
- [x] 3.3 Require unchanged file and plan fingerprints, project mutation lock, atomic write, file and directory fsync, idempotency, and no implicit build on apply.
- [x] 3.4 Add negative tests for stale fingerprints, incompatible BSL Analyzer, missing/disabled embedding profile, unacknowledged remote disclosure, active invocation conflict, and source/config drift.

## 4. Simplify the workspace to one user flow

- [x] 4.1 Replace schema/version/rollback controls with ordered BSL Analyzer, embedding profile, semantic enablement, build, and readiness states.
- [x] 4.2 Add one `Enable semantic search` action that previews the schema-3 configuration and explains that existing operational indexes are retained but not reused.
- [x] 4.3 Keep profile creation and remote-disclosure acknowledgement owner-only and require a successful safe probe before enablement.
- [x] 4.4 After apply, show lexical and hybrid missing states separately and require an explicit `Create missing indexes` action.
- [x] 4.5 Verify empty, incompatible-backend, missing-profile, preview-ready, applied-missing-indexes, build-progress, ready, failed, retry, and cleanup states in the built application.
- [x] 4.6 Verify forward/back navigation, keyboard/focus behavior, console errors, overflow, and supported desktop/mobile widths; preserve user-state bookmarks across service restart.

## 5. Cut over canonical generation and current project

- [x] 5.1 Update canonical `research/indexing.toml`, docs, runtime sync manifest, package data, and bootstrap output to schema 3.
- [x] 5.2 Preview and apply the unchanged fingerprinted synchronization plan from the canonical runtime into the generated template; verify two-way parity and convergence.
- [x] 5.3 Preview and apply the reviewed schema-3 configuration cutover to `sppr-research-ver2` without overwriting any other tracked or untracked project state.
- [x] 5.4 Re-read protected manifests and prove sources, generations, DIF/MRQ state, decisions, credentials, and outputs are unchanged.
- [x] 5.5 Explicitly build missing lexical and hybrid indexes for selected components, validate operation readiness, restart the service, and confirm persistent user state.
- [x] 5.6 Run garbage-collection preview and prove old schema-1/2 instances are only inactive candidates; do not delete them without a separate confirmed operator action.

## 6. Contract obsolete schemas out of the source tree

- [x] 6.1 Remove schema-1/2 parsers, serializers, normalizers, capability vocabulary, v1 aliases, migration preview/apply, backups, downgrade, rollback, prior-runtime readiness, API routes, CLI commands, UI controls, and compatibility diagnostics.
- [x] 6.2 Remove the maintenance-only cutover helper after every inventoried target is proven on schema 3.
- [x] 6.3 Delete obsolete fixtures and tests; replace them with schema-3-only contract, rejection, semantic lifecycle, and data-nonmutation tests.
- [x] 6.4 Remove active documentation describing schema selection, migration, downgrade, or rollback and document release rollback separately from configuration downgrade.
- [x] 6.5 Add a source-tree guard that fails when executable schema-1/2 indexing authorities or legacy `source-search-tool/v1` operations return; avoid matching unrelated domain schema versions.

## 7. Release verification

- [x] 7.1 Run `python scripts/checks/test_template.py` and `python scripts/checks/test_doctor.py`.
- [x] 7.2 Run `python scripts/checks/test_research_repo.py --repo-path /run/media/egor/D6B64A72B64A52E3/Projects/OneC/Presail/sppr-research-ver2`.
- [x] 7.3 Run `uv run --extra workspace basedpyright` and the full Python test suite.
- [x] 7.4 Run workspace unit tests, production build, and the complete Playwright suite against the built application.
- [x] 7.5 Run offline BSL Analyzer contract fixtures and the opt-in live exact-build conformance check.
- [x] 7.6 Inspect wheel, source archive, and `dist/research-template.zip`; prove they contain schema-3 runtime/configuration only and no operational indexes, profiles, credentials, or customer evidence.
- [x] 7.7 Run strict OpenSpec validation, runtime/template parity checks, `git diff --check`, and final worktree status review.
- [x] 7.8 Rehearse application-release rollback without in-product schema downgrade and record the exact supported boundary and recovery procedure.
