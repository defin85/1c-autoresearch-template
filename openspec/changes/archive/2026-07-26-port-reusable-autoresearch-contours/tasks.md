## 1. Porting Baseline And Guardrails

- [x] 1.1 Inventory reusable source modules, CLI commands, docs, tests, and fixtures in the mature research repository and record the accepted source commit.
- [x] 1.2 Add a portability check that rejects absolute customer paths, customer names, fixed source-product labels, fixed Git branch names, and generated research artifacts in template-owned files.
- [x] 1.3 Consolidate reusable JSON, JSONL, CSV, hashing, path, subprocess, and lock operations in the existing common helpers before porting dependent modules.
- [x] 1.4 Extend `project.toml` examples with optional source roles and execution settings while preserving all existing generated-repository defaults.

## 2. Configuration Source Normalization

- [x] 2.1 Port and generalize the Designer XML/BSL and `v8unpack` parser with versioned deterministic snapshots and explicit unsupported-part diagnostics.
- [x] 2.2 Port form, form-module, ordinary-form, template, predefined-data, help, and machine-readable sidecar handling without direct interpretation of opaque binaries.
- [x] 2.3 Port custom-metadata snapshot comparison, canonical ordering, stable metadata identities, and strict reconciliation validation.
- [x] 2.4 Port stable diff identifiers and active/inactive lineage generation, and integrate them with diff and final inventories.
- [x] 2.5 Add the paged large-field CSV helper as a documented thin command.
- [x] 2.6 Add compact XML/BSL and `v8unpack` fixtures and parser, comparison, stable-ID, and large-CSV tests.
- [x] 2.7 Add `configuration-source` and `custom-metadata` CLI groups and update template and research verification matrices.

## 3. Atomic Customization Registry

- [x] 3.1 Port and generalize stable `CUS-*` identity allocation, canonical JSONL records, evidence links, lineage, context building, and derived exports.
- [x] 3.2 Support evidence from stable diffs, normalized metadata, source review, subject groupings, and external report or processor inventories.
- [x] 3.3 Preserve missing external artifacts as included unresolved records with explicit source status and questions, without inferred implementation details.
- [x] 3.4 Add `customization-registry bootstrap`, `context-build`, `build`, `validate`, and `export` commands with atomic publication.
- [x] 3.5 Add empty customization-registry scaffolds, schemas, README, ignore rules, and English bootstrap methodology to `templates/research-repo/`.
- [x] 3.6 Add registry unit tests, external-artifact fixtures, idempotency checks, and stale-derived-view checks.

## 4. Unified Migration Requirements

- [x] 4.1 Port and generalize stable `MRQ-*` records, controlled relation roles, ownership ledger, requirement lineage, and bounded graph queries.
- [x] 4.2 Implement atomic graph replacement and validation for uncovered customizations, dangling links, duplicate owners, invalid states, and stale views.
- [x] 4.3 Port bounded `show`, `context`, `neighbors`, `link`, `unlink`, `build`, `validate`, activation, rollback, and output commands.
- [x] 4.4 Generate subject-card compatibility views, functional-gap views, customer registers, and specification drafts from canonical `MRQ-*` records.
- [x] 4.5 Remove product-specific BP 2.0/BP 3.0 labels from reusable graph contracts and resolve source and target roles from project configuration.
- [x] 4.6 Add empty migration-requirement scaffolds, schemas, README, and English methodology to generated repositories.
- [x] 4.7 Add graph, ownership, context-boundary, output-redaction, atomic-publication, and migration tests.

## 5. Queue And External Artifact Research

- [x] 5.1 Port reusable queue seeding profiles, deterministic task IDs, dependency planning, and review-preparation support without customer-specific queue rows.
- [x] 5.2 Port external `.epf` and `.erf` inventory, reconciliation, planning, review validation, and ordinary research queue handoff.
- [x] 5.3 Add English `/goal Research` and review-preparation methods and agent skills that operate on one deterministic unit.
- [x] 5.4 Add queue, research-review, external-artifact, and fresh-repository seed tests.

## 6. Parallel Research Execution

- [x] 6.1 Port deterministic planning, stable unit IDs, exact allowed-source expansion, source fingerprints, and bounded context generation.
- [x] 6.2 Port isolated read-only worker workspaces, structured output schemas, trace summaries, process-group tracking, and forbidden-mutation enforcement.
- [x] 6.3 Port decision validation for evidence paths, line ranges, assigned entities, semantic mutation gates, stale tasks, and changed sources.
- [x] 6.4 Port the locked single writer with per-unit rejection, block rollback, idempotent ledgers, block-scoped generators, and completion criteria.
- [x] 6.5 Port compatible resume, validated-decision reuse, interruption cleanup, run inspection, trace cleanup, and completed-run compaction.
- [x] 6.6 Replace hard-coded model, worker, timeout, source-path, Git-ref, regulatory-report, and product assumptions with conservative project configuration.
- [x] 6.7 Add the `parallel-research` CLI group, generated-repository method, and standalone agent skill.
- [x] 6.8 Add fake-worker integration tests for concurrency, isolation, resume, rejection, rollback, compaction, zero-remainder completion, and phase separation.

## 7. Optional Physical Diff Cleanup

- [x] 7.1 Port and generalize cleanup queue initialization, stable row identities, normalized source contexts, and evidence probes behind an explicit source profile.
- [x] 7.2 Port deterministic helpers for next rows, candidate building, structured `codex exec`, trace summaries, decision application, and ready-batch publication.
- [x] 7.3 Implement sidecar-first binary handling and source-kind strategies for metadata, forms, BSL, templates, and schema contracts.
- [x] 7.4 Port the one-writer cleanup ledger, unresolved-row stop rule, block-scoped rebuilds, and publishable clean-repository generation.
- [x] 7.5 Add English manual-cleanup methodology and agent skill with configurable batch and worker settings.
- [x] 7.6 Add cleanup classification, sidecar, concurrency, rollback, and clean-repository validation tests.

## 8. Template Integration And Verification

- [x] 8.1 Register all new modules and commands in package exports, CLI help, repository map, verification docs, and root README.
- [x] 8.2 Extend `doctor`, `checks template`, and `checks research` for optional contour prerequisites, stale artifacts, missing evidence packs, and forbidden generated state.
- [x] 8.3 Extend `templates/research-repo/AGENTS.md`, repository docs, empty directories, schemas, examples, and `.gitignore` without copying mature-project data.
- [x] 8.4 Generate a fresh research repository and verify all existing commands remain backward compatible before enabling any optional contour.
- [x] 8.5 Run focused unit suites after each capability slice, then run `python -m one_c_autoresearch checks template`, `checks doctor`, strict deep doctor, and full tests.
- [x] 8.6 Run an end-to-end fixture flow from source parsing through `CUS-*`, `MRQ-*`, a fake parallel research pass, and derived specification output.
- [x] 8.7 Confirm the template contains no source dumps, customer records, run traces, workspaces, normalized source-pair caches, XLSX deliverables, or compiled Python files.
