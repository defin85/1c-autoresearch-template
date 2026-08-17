## ADDED Requirements

### Requirement: Verify the owned Python runtime without type-checking suppressions

The canonical generated workflow SHALL run one pinned BasedPyright check over every production module under `src/one_c_autoresearch` and SHALL complete only when the unsuppressed result contains zero errors, zero warnings and zero notes.

#### Scenario: Production source is verified

- **WHEN** local, scaffold, release or CI verification runs
- **THEN** it MUST execute the pinned BasedPyright version against every owned production module with no baseline, ignored diagnostic, disabled rule, per-file override or production-source exclusion

#### Scenario: Dynamic external data enters the runtime

- **WHEN** JSON, TOML, SQLite, subprocess, HTTP, filesystem or third-party data crosses into owned domain logic
- **THEN** the owning boundary MUST validate and narrow it to a concrete typed shape before downstream use and MUST NOT propagate an unknown or broad `Any` value through the workflow

#### Scenario: A diagnostic identifies a possible runtime defect

- **WHEN** BasedPyright reports optional access, an unbound value, invalid argument or return, missing initialization or an ignored fallible result
- **THEN** implementation MUST establish the invariant through runtime validation or correct the behavior with focused regression coverage and MUST NOT silence the diagnostic with an unconditional cast or ignore

#### Scenario: A third-party interface lacks complete typing

- **WHEN** an approved dependency returns a value whose precise type is unavailable
- **THEN** the runtime MUST confine the escape hatch to the smallest adapter boundary, validate or narrow the value before returning it, and MUST NOT introduce a general shadow-stub package or spread `Any` into owned modules

#### Scenario: The runtime is synchronized

- **WHEN** verified target changes are promoted into the canonical scaffold and installable runtime
- **THEN** normalized two-way parity MUST include production source, BasedPyright configuration, pinned dependency metadata and verification commands while excluding the temporary migration baseline from the completed artifact

#### Scenario: Type-checking policy is weakened

- **WHEN** a change adds a baseline, ignored diagnostic, rule downgrade, per-file override, broad production exclusion or unreviewed `Any` escape hatch
- **THEN** canonical verification MUST fail before packaging or scaffold publication

#### Scenario: Supported platforms verify the release

- **WHEN** the Python release matrix runs on Ubuntu, Windows and macOS
- **THEN** the same pinned unsuppressed check MUST pass without platform-specific rule changes or excluded branches
