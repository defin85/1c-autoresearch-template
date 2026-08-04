## Why

The generated Python runtime now runs BasedPyright, but its initial baseline suppresses 7,026 diagnostics across 34 production modules. The largest groups are untyped `Any` propagation, unknown values crossing JSON, TOML, SQLite, subprocess and HTTP boundaries, and unchecked optional or partially initialized state. The check prevents new diagnostics, but it does not yet prove that the existing runtime is type-safe.

Keeping the baseline indefinitely would turn a migration aid into a second authority for accepted defects. The runtime and generated scaffold need one strict, reproducible type-checking gate whose success depends on source types rather than stored suppressions.

## What Changes

- Eliminate every diagnostic currently stored in the BasedPyright baseline for `src/one_c_autoresearch`.
- Type external data at the boundary where it is decoded, then keep internal workflow, storage, search, indexing and API contracts typed end to end.
- Resolve dependency cycles and real optional, unbound, argument, return and attribute errors before cosmetic unused-code findings.
- Prohibit bulk suppressions, rule downgrades, broad `Any`, unchecked casts and generated stub facades as substitutes for typing the owned runtime.
- Remove `baselineFile` and `.basedpyright/baseline.json` after the unsuppressed check reaches zero errors, warnings and notes.
- Make the same pinned BasedPyright check mandatory in the canonical template, generated scaffold, verified target, release verification and supported CI environments.
- Preserve runtime behavior, repository schemas, HTTP and CLI contracts, customer evidence, immutable generations and the `0.3.0` compatibility boundary.

## Impact

- Affected specification: `repository-owned-generated-research-workflow`.
- Expected implementation areas: Python boundary models, workflow and storage types, search and indexing adapters, dispatcher and service orchestration, workspace API, tests, dependency metadata, generated scaffold synchronization, verification documentation and CI.
- The work is a behavior-preserving internal hardening. Any discovered runtime ambiguity that cannot be resolved from existing contracts requires a separate reviewed behavior decision rather than a type-checker workaround.
- The initial baseline remains a temporary migration ledger only while tasks are incomplete; it cannot be shipped as the completed state.
