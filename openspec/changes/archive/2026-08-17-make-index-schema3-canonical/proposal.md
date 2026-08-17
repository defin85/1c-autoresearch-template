## Why

The runtime currently treats indexing schemas 1, 2, and 3 as executable formats. This transitional compatibility leaks into the workspace: a user can configure a schema-2 BSL Analyzer backend and an embedding profile but cannot complete semantic-search setup because explicit lexical and hybrid routes exist only in schema 3. The result is duplicated parsers, migration and rollback branches, UI states that cannot reach a working outcome, and a generated project whose configured backend can appear connected without being usable.

The complete BSL Analyzer search surface already defines schema 3 as the honest provider-neutral contract. It should become the only accepted runtime and generated-repository format. Index payloads are disposable operational state and do not need conversion; the cutover should rewrite only reviewed indexing configuration and rebuild selected indexes afterward.

## What Changes

- Make indexing schema 3 the only format accepted by the runtime, generator, workspace API, CLI, tests, and operator documentation.
- Remove schema-1 and schema-2 parsing, normalization, serialization, preview/apply, aliases, compatibility readers, backup, downgrade, rollback, and prior-runtime readiness code rather than retaining hidden fallback branches.
- Generate new repositories with explicit schema-3 lexical and hybrid routes, complete operation capability routes, machine contract 1.3, and stable lexical and embedding service-profile bindings.
- Add one reviewed workspace cutover flow that prepares a schema-3 configuration for an existing project, validates the exact BSL Analyzer contract and required profiles, explains that operational indexes will be rebuilt, and applies no index build implicitly.
- Cut over `sppr-research-ver2` through that reviewed configuration operation after the canonical runtime and generated template are ready. Do not overwrite project evidence, source generations, DIF/MRQ state, credentials, service-profile secrets, or deliverables.
- Do not migrate schema-1 or schema-2 index instances. Ignore them after cutover and expose them only as removable inactive operational state through the existing garbage-collection preview/apply flow.
- Replace schema-number controls and rollback UI with one user-facing semantic-search setup flow: repair/prove BSL Analyzer compatibility, configure and probe the embedding profile, review the configuration change, apply it, then explicitly build the missing lexical and hybrid indexes.
- Preserve fail-closed semantic readiness, reviewed external disclosure, owner-only secrets, canonical-source evidence promotion, bounded search, and agent-runtime-neutral routing.
- Replace the temporary schema-2 migration and rollback contract after the completed `add-complete-bsl-analyzer-search-surface` change has been archived.

## Impact

- Affected specifications: `managed-autoresearch-workspace`, `repository-owned-generated-research-workflow`.
- Expected implementation areas: `indexes.py`, `service.py`, `workspace_api.py`, `source_search.py`, workspace UI/tests, generated `research/indexing.toml`, runtime/template synchronization, operator documentation, release checks, and the explicit `sppr-research-ver2` cutover.
- Compatibility: indexing configurations with schema 1 or 2 become unsupported input and receive one typed schema-3-required diagnostic. They are not executed, normalized, silently upgraded, or overwritten by generation/synchronization.
- Operational indexes: no conversion, copying, promotion, or deletion during cutover. New schema-3 identities are built explicitly; old instances remain recoverable disposable files until reviewed garbage collection.
- Release boundary: this is a deliberate breaking runtime-format change and must ship under a new incompatible release, with `0.2.0` remaining the last legacy-compatible release.
- Project scope: cut over only `Presail/sppr-research-ver2`; `/Projects/OneC/sppr-research` is explicitly excluded and must not be modified.
