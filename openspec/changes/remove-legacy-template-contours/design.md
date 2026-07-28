## Context

`templates/research-repo/` and `sppr-research-ver2` implement the canonical workflow and explicitly reject the old authorities. The repository root is still a hybrid: `src/one_c_autoresearch/` exposes old commands and modules, the wheel packages them, root documentation and skills advertise them, and `promote_package()` copies canonical files over that directory without deleting stale files while preserving the old `cli.py` and `doctor.py`.

The cleanup is intentionally breaking. Keeping import stubs or hidden command aliases would leave two authorities and defeat the canonical forbidden-authority contract.

## Locked Decisions

- The canonical generated runtime remains the source of truth for `one_c_autoresearch`, its CLI, workspace, schemas, and package behavior.
- Scoped inventories distinguish forbidden generated-repository/runtime authorities from the small root-only maintenance scripts that create, synchronize, package, and verify the canonical runtime.
- Active implementation, active documentation, skills, tests, and distributions contain no `CUS-*` or subject-card contract; historical OpenSpec archives and this removal record remain non-executable evidence and are excluded from runtime scans.
- Removed legacy commands and Python imports receive no compatibility adapters.
- Template maintenance remains available only through existing repository-local scripts for generation, synchronization, packaging, and `checks template|doctor|research`; no second installable maintenance package or console command is introduced.
- The retained maintenance inventory is exact: `scripts/sync_generated_runtime.py`, `scripts/build_workspace_template.py`, `scripts/bootstrap/new_research_repo.py`, and `scripts/checks/test_template.py`, `test_doctor.py`, `test_research_repo.py`. Other current scripts are removed unless canonical target parity places them inside the generated payload.
- The retained active root documentation inventory is exact: `README.md`, `AGENTS.md`, `docs/agent/repo-map.md`, `docs/agent/verification.md`, and canonical `docs/operator/dispatcher-inspector-rollback.md`; historical OpenSpec artifacts remain history. Repository-local `.agents/skills/` and other active method documents are removed.
- Apart from the canonical target test inventory, root-only coverage is limited to reworked `tests/test_generated_runtime_sync.py` and new `tests/test_template_release.py`.
- `research-template.zip` remains a deterministic release artifact but moves outside `one_c_autoresearch` package data because the canonical runtime does not consume it.
- The breaking release version is `0.3.0`; `0.2.0` is the exact last compatible legacy version and upgrade-test baseline.
- Customer repositories and evidence are never deleted by this change.
- Canonical source routing, stable DIF identity and lineage, semantic/physical difference closure, platform-safe CSV field limits, DIF, MRQ, decision, source-import, dispatcher, and invocation-inspector behavior is preserved.

## Goals / Non-Goals

**Goals:**

- Make the installed runtime, generated scaffold, packaged archive, and verified reference expose the same canonical authority surface.
- Delete legacy implementation, tests, docs, skills, schemas, assets, and entry points.
- Make stale-file absence mechanically verifiable before a wheel or archive is accepted.
- Preserve a small independent maintenance interface for this template repository.

**Non-Goals:**

- Migrating old queue, `CUS`, subject-card, reverse-map, or dashboard state.
- Retaining deprecated imports, commands, routes, or readers.
- Changing canonical workflow semantics or current project evidence.
- Removing historical OpenSpec archives.

## Decisions

### 1. Keep template maintenance as repository-local scripts

`one_c_autoresearch` SHALL become the canonical project runtime. Repository creation, synchronization, package assembly, and cross-repository checks SHALL reuse the existing scripts under `scripts/` and SHALL NOT add another Python package or console entry point.

Alternatives rejected: keeping maintenance subcommands in the runtime CLI would preserve the hybrid parser; creating a second package would add a public surface where repository-local scripts already suffice.

The six retained scripts SHALL not import the removed maintenance CLI. Synchronization and deterministic archive construction use the Python standard library. Bootstrap copies the validated scaffold into an empty destination and substitutes only declared project tokens. `test_template.py` owns root/scaffold parity and forbidden scans, `test_doctor.py` owns fresh-bootstrap smoke, and `test_research_repo.py` invokes the selected canonical repository's doctor with `sys.executable`, explicit `cwd`, explicit `PYTHONPATH`, no shell, and propagated exit status.

### 2. Replace owned trees instead of overlaying them

Synchronization SHALL build and fully validate all derived outputs in staging before mutation, then emit a read-only sorted add/change/delete plan and a content-derived plan fingerprint without file contents or secrets. Mutation requires explicit apply mode with that expected fingerprint and unchanged inputs, then replaces each exact reviewed owned tree from the staged set. The generated scaffold and normalized manifest remain authoritative. If the process stops between tree replacements, parity and package gates SHALL fail and an idempotent rerun with the reference SHALL converge every derived tree before any distribution can be built. A short explicit allowlist identifies maintenance-only files outside those trees. Unexpected extra files fail verification.

Alternative rejected: continuing file-by-file overlay leaves deleted upstream modules installed indefinitely.

### 3. Use one executable forbidden-authority inventory

The release gate SHALL use two-way target parity plus explicit scoped inventories: generated-repository/runtime prohibitions, distribution-member prohibitions, and active root documentation/skill/test prohibitions. Parity rejects every unexplained extra runtime or test file even when the forbidden inventory omitted its name. Root-only maintenance scripts, the five active root documents, and the two root-only test files are allowlisted only in their exact scopes; historical `openspec/changes/archive/` and this change record are excluded as non-executable evidence. Structured path, module, parser-command, route, and registration checks are preferred over unbounded substring scans; `CUS-*` and subject-card schema markers remain forbidden in executable and active contract scopes.

Alternative rejected: a hand-maintained deletion checklist cannot detect stale wheel members or reintroduced strings.

### 4. Delete tests with their retired behavior and replace them with absence/parity checks

Tests whose only purpose is to validate a removed contour SHALL be deleted. Canonical runtime tests are synchronized from the verified target. Small package-surface tests SHALL prove removed imports and commands fail, maintenance commands remain available only through the maintenance entry point, and distribution archives contain no forbidden member.

Alternative rejected: retaining old tests as documentation keeps dead modules importable and makes future contributors believe the behavior remains supported.

### 5. Make the release boundary explicit and fail closed

The wheel, source distribution, compiled workspace assets, and separate `dist/research-template.zip` SHALL be rebuilt after pruning as release `0.3.0`. The archive SHALL contain only the canonical generated-repository payload and required portable bootstrap metadata, not the template root runtime or maintenance scripts. Before deletion, verification SHALL build and fingerprint a `0.2.0` baseline wheel from the exact pre-cleanup commit in an isolated temporary worktree outside the final `dist/`; any pre-existing ignored distribution may be used only after its package metadata and fingerprint are verified against that commit. It SHALL then clear release output, perform both a clean `0.3.0` install and a `0.2.0`→`0.3.0` upgrade, inspect the canonical command surface, start the canonical workspace, generate a fresh repository through root maintenance, and compare the normalized reusable manifest with the verified target. Final `dist/` SHALL contain only the `0.3.0` wheel/source distribution and separate template archive. Release version fields are an explicit normalization; unexplained code or contract differences are not.

## Audit Matrix

| Concern | Required evidence |
| --- | --- |
| Runtime authority | Root and target canonical module/CLI inventories match after documented normalization |
| Removed behavior | Forbidden imports, commands, routes, files, schema markers, `CUS-*`, and subject-card markers are absent |
| Maintenance | Existing root scripts create, synchronize, package, and check a repository without entering the runtime distribution |
| Packaging | Wheel, source distribution, workspace assets, and separate template archive pass member scans; the runtime wheel contains no template archive |
| Compatibility | Canonical repositories remain usable; removed legacy consumers receive an explicit breaking-change error through missing commands/imports, not adapters |
| Safety | Cleanup targets only reviewed repository paths and never customer evidence or generated project data |
| Verification | Canonical target tests, template maintenance tests, frontend tests/build, fresh-generation smoke, strict doctor, and strict OpenSpec validation pass |
| Rollback | Revert the cleanup commit and reinstall the previous package; no data conversion or destructive project migration is performed |
| Performance | Inventory and distribution scans are bounded to declared roots and archive members and do not traverse customer generations or dependency caches |
| Scalability and cost | Verification adds no runtime service or dependency; work grows linearly with the declared reusable/package file count |

## Execution Plan

1. Freeze the exact retained runtime and scoped maintenance inventories and add failing absence/parity tests.
2. Remove maintenance commands from the runtime CLI and route root maintenance through existing scripts.
3. Change synchronization and packaging to staged full-tree replacement with forbidden-authority scans.
4. Delete legacy modules, commands, routes, scripts, skills, docs, tests, schemas, and obsolete top-level capability specs.
5. Rebuild all distributed artifacts and update concise repository navigation and verification documents.
6. Run target-first parity, package installation, fresh-generation, frontend, Python, doctor, and OpenSpec gates.

## Risks / Trade-offs

- [External users import removed modules] → Publish this as a breaking release and name the last compatible release; provide no adapter that could mutate canonical state.
- [Cleanup removes a still-needed maintenance helper] → Retain only proven generation/check/package helpers in repository-local scripts before deleting their old dependency graph.
- [Synchronization overwrites unrelated owned-tree edits] → Default to a content-free read-only change plan and require its exact fingerprint in explicit apply mode.
- [Stale files survive in an archive or installed environment] → Scan built members and test both clean installation and in-place upgrade from the immediately preceding legacy distribution.
- [The forbidden inventory drifts from the target] → Generate parity evidence from the same normalized synchronization input and fail on unexplained additions in either direction.
- [Process stops between owned-tree replacements] → Parity and package gates fail closed; rerunning the deterministic synchronization from the reference replaces every derived tree and restores one validated set.
- [Historical documentation becomes misleading] → Preserve OpenSpec archives as history but remove active navigation and executable skills for retired contours.

## Migration Plan

1. Land the root maintenance scripts and release scans together with the deletions in one commit boundary.
2. Rebuild distributions and verify clean installs before publishing.
3. Announce removed commands/imports, name `0.2.0` as the last compatible version, and require consumers to migrate to canonical DIF/MRQ operations or remain on `0.2.0`.
4. Do not rewrite or delete any existing customer repository.

Rollback reverts the cleanup commit and reinstalls the prior package. Because the change performs no customer-data migration, rollback requires no repository-state restoration.

## Open Questions

None.
