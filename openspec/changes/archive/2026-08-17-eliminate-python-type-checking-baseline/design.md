## Context

The verified generated target currently pins BasedPyright 1.39.9 and checks `src/one_c_autoresearch` against a 1.6 MiB baseline. The baseline contains 7,026 diagnostics in 34 files. The leading categories are `reportAny` (3,441), `reportExplicitAny` (972), unknown member, argument and variable types (1,358), and unused call results (430). Smaller groups include argument and return mismatches, import cycles, possibly unbound variables, optional access, deprecated calls and missing type arguments.

The runtime accepts dynamic data from TOML and JSON files, SQLite rows, subprocess results, HTTP request bodies and third-party libraries. Those boundaries currently allow unknown values to spread through workflow logic. Fixing individual use sites with casts would preserve that structural defect and make later schema changes harder to validate.

## Locked Decisions

- Completion means an unsuppressed `uv run --extra workspace basedpyright` reports exactly zero errors, zero warnings and zero notes for `src/one_c_autoresearch`.
- The completed configuration contains no `baselineFile`, and `.basedpyright/baseline.json` is absent from the canonical repository and generated scaffold.
- BasedPyright remains pinned to one reviewed version in the lockfile so diagnostic changes arrive through explicit dependency updates.
- The check uses BasedPyright's default strict diagnostics currently captured by the baseline; completion cannot lower the checking mode or disable a diagnostic rule to make the count fall.
- `# type: ignore`, `# pyright: ignore`, per-file diagnostic overrides and exclusion of owned production modules are not migration mechanisms.
- `Any`, `cast()` and protocol escape hatches are allowed only at a documented external boundary where the upstream library genuinely lacks a narrower contract; the value must be validated or narrowed before entering owned domain logic.
- Existing schemas, serialized field names, HTTP payloads, CLI behavior, workflow state transitions, immutable-generation rules and compatibility boundaries remain unchanged unless a separate reviewed change explicitly modifies them.
- Runtime source is repaired target-first, then synchronized through the existing preview/fingerprint/apply flow into both canonical runtime copies. Customer data and target-only state are never synchronized.
- Existing behavior tests remain mandatory. Type-checking success does not replace runtime, scaffold, packaging or cross-platform verification.

## Goals / Non-Goals

**Goals:**

- Make every owned production module pass the unsuppressed BasedPyright gate.
- Stop unknown external data at explicit parsing and validation boundaries.
- Give shared workflow records, persistence rows and service results reusable concrete types without changing their wire representation.
- Remove dead imports, unused private functions and ignored return values when direct review proves they are unnecessary.
- Keep canonical runtime, scaffold and verified target in normalized parity.

**Non-Goals:**

- Redesigning repository schemas, HTTP routes, workflow stages or user-visible behavior.
- Typing third-party packages or vendoring replacement stubs when a narrow local boundary is sufficient.
- Expanding the first gate to tests, archived OpenSpec history or maintenance scripts that are outside `src/one_c_autoresearch`.
- Combining this work with formatter, linter, dependency or broad API modernization.

## Decisions

### 1. Repair boundary types before downstream modules

Every dynamic input SHALL be decoded once into an existing dataclass or a minimal `TypedDict`, enum, tuple, or validated scalar shape at its owning boundary. JSON and TOML loaders validate required keys and value types; SQLite adapters convert rows to typed records; subprocess adapters normalize exit status and bounded text; HTTP handlers validate request bodies through the existing framework contract. Internal modules consume those typed results and do not repeatedly inspect `dict[str, object]` payloads.

Alternative rejected: adding annotations at each failing use site leaves unknown values flowing through the architecture and requires hundreds of casts.

### 2. Work in dependency order with a continuously shrinking baseline

The temporary baseline SHALL be regenerated only after a reviewed batch genuinely removes diagnostics. Each batch records the total and category delta, runs its focused tests and keeps the full BasedPyright check green against the smaller ledger. Work proceeds in this order:

1. shared scalar, identifier, serialization and boundary models;
2. SQLite, repository files, events and immutable generation readers/writers;
3. workflow, DIF, MRQ, consolidation and stage recomputation;
4. indexing, source/reference search and process adapters;
5. dispatcher, service, runner and command line;
6. workspace API and remaining leaf modules;
7. import-cycle, dead-code and final baseline removal.

The ordering may move one tightly coupled module with its dependency, but a batch cannot hide diagnostics or change runtime contracts merely to preserve the order.

Alternative rejected: file-by-file alphabetical cleanup repeatedly retypes the same dynamic structures and creates annotation churn.

### 3. Distinguish narrowing from suppression

Narrowing code SHALL perform a runtime check when external data may violate the expected shape. A cast is acceptable only when an already enforced invariant cannot be expressed to the checker and the adjacent code or test proves that invariant. Broad aliases such as `Json = Any`, unparameterized containers, fake protocols matching one concrete owned class and generated `.pyi` shadows are forbidden shortcuts.

### 4. Treat suspicious diagnostics as defect candidates

Optional access, unbound variables, invalid arguments, incorrect return types, missing initialization and ignored fallible results SHALL be reviewed as potential runtime defects. The implementation either fixes the safe behavior and adds the smallest regression test, or proves the existing invariant through explicit validation. It does not use a non-null assertion equivalent or unconditional cast.

Unused imports, functions, variables and call results SHALL be removed only after references and side effects are checked. Intentionally ignored results use the narrowest explicit assignment or API pattern supported by the callee.

### 5. Preserve target-first parity and release gates

The verified generated target is the working implementation surface. After its unsuppressed source and tests pass, the existing synchronization tool previews and applies the reusable changes to `templates/research-repo/` and `src/one_c_autoresearch/`. Root verification rejects a baseline, missing BasedPyright configuration, version drift, ignored diagnostics, excluded production modules or normalized source differences.

The release matrix runs BasedPyright on Ubuntu, Windows and macOS using the pinned lock. Platform-specific branches must therefore be narrowed without assuming one host's path, process or filesystem types.

## Audit Matrix

| Concern | Required evidence |
| --- | --- |
| Completeness | Unsuppressed check covers every `src/one_c_autoresearch/**/*.py` module and reports 0 errors, 0 warnings and 0 notes |
| No suppression | No baseline, ignored diagnostics, rule downgrade, per-file override or excluded owned module |
| Boundary safety | JSON, TOML, SQLite, subprocess, HTTP and third-party results are validated before domain use |
| Runtime correctness | Optional, unbound, argument, return, initialization and ignored-result findings are resolved with focused tests where behavior can fail |
| Compatibility | Serialized schemas, CLI and HTTP contracts, workflow transitions and `0.3.0` boundaries remain unchanged |
| Parity | Verified target, scaffold runtime and installable runtime are normalized two-way identical |
| Cross-platform | The pinned check passes on Ubuntu, Windows and macOS |
| Maintainability | Shared concrete types have one owner; no speculative abstraction or shadow stub tree is introduced |
| Release | Python tests, doctor, scaffold, synchronization, package and distribution checks remain green |

## Execution Plan

1. Add the pinned BasedPyright dependency, strict production-source configuration and temporary baseline to the canonical verification surfaces.
2. Inventory diagnostics by module, category and dependency layer; save only aggregate counts as migration evidence.
3. Type and validate shared external boundaries, then repair storage and immutable repository-state modules.
4. Repair workflow and research-domain modules using those boundary types.
5. Repair indexing, search and subprocess integration modules.
6. Repair orchestration, CLI and workspace API modules; resolve remaining cycles and dead code.
7. Run the checker without the baseline, remove the baseline setting and file, and add rejection tests for their return.
8. Synchronize the verified target into the scaffold and installable runtime, then run the complete release matrix and direct diff review.

## Risks

- Tightening dynamic decoders may expose malformed historical operational state. Mitigation: preserve accepted wire shapes, add fixtures for current valid and invalid forms, and fail with existing typed errors rather than silently coercing data.
- Large annotation-only diffs can conceal behavior changes. Mitigation: use small dependency-ordered batches, category deltas, focused tests and direct diff review before shrinking the baseline.
- Import-cycle repair can tempt broad module reorganization. Mitigation: first use existing module ownership, `TYPE_CHECKING` imports and narrower dependencies; move runtime ownership only when the cycle is real and covered.
- Third-party packages may expose incomplete types. Mitigation: isolate them behind the smallest local adapter and validate outputs instead of spreading `Any` or maintaining a general stub package.

## Assumptions And Open Questions

- The 7,026-entry snapshot is the migration baseline for planning. Implementation SHALL recalculate it before the first batch because concurrent runtime changes may alter the count.
- Production runtime under `src/one_c_autoresearch` is the requested scope. Tests and maintenance scripts continue to run but are not added to the strict type-check target by this change.
