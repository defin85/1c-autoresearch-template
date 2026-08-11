## Context

The canonical runtime and generated repository currently contain three indexing-format paths:

- schema 1 compatibility for the legacy single-RLM configuration;
- schema 2 pluggable backends with six generic capability routes;
- schema 3 complete BSL Analyzer operations with distinct lexical and hybrid identities and managed search-service profiles.

Schema 3 is already the only model that can represent honest semantic readiness. The current generated `sppr-research-ver2` repository remains on schema 2, routes every capability through RLM Tools BSL, and has a configured but contract-incompatible BSL Analyzer. Operational indexes live outside the repository and are disposable. Canonical customer sources and research generations must not be imported, rewritten, or deleted by generation, synchronization, cutover, rollback, or cleanup.

## Locked Decisions

1. Schema 3 becomes the only accepted and emitted indexing configuration.
2. Schema 1 and schema 2 are removed from runtime source, generated source, UI, tests, and active documentation; they do not remain hidden compatibility readers.
3. Existing index instances are not migrated. Schema-3 indexes are built explicitly after configuration cutover.
4. Old operational index instances are not deleted by cutover. They may be removed only by the existing fingerprinted garbage-collection preview/apply operation.
5. The canonical template is changed first and the generated `sppr-research-ver2` runtime is synchronized from it; target-only divergence is not an acceptable final state.
6. The target project's indexing configuration changes only through an explicit reviewed operation. Synchronization and bootstrap never overwrite project configuration or evidence.
7. BSL Analyzer machine contract 1.3 and a complete approved search-surface manifest are prerequisites for applying schema-3 configuration.
8. Semantic search requires an enabled, probed owner-only embedding profile and explicit disclosure acknowledgement when the service is remote.
9. Applying configuration starts no build. The operator explicitly creates missing indexes after reviewing the applied routes and identities.
10. The user interface does not expose schema numbers, compatibility modes, downgrade, or rollback controls.
11. Canonical sources, active generations, DIF/MRQ state, decisions, credentials, and deliverables are outside this cutover's mutation boundary.
12. The prior schema-2 backup and prior-runtime rollback contract from `add-complete-bsl-analyzer-search-surface` is superseded after that prerequisite change is archived.

## Architecture

### One closed configuration contract

`research/indexing.toml` has one exact top-level shape:

```toml
schema_version = "3"
machine_contract_version = "1.3"

[[backends]]
adapter_id = "rlm-tools-bsl"
engine_version = "<exact discovered version>"

[[backends]]
adapter_id = "bsl-analyzer"
engine_version = "<exact approved build>"

[routes]
code-search-lexical = ["bsl-analyzer", "rlm-tools-bsl"]
code-search-hybrid = ["bsl-analyzer"]
symbol-info = ["bsl-analyzer"]
# remaining complete operation capabilities are explicit

[service_profiles]
lexical = "lexical-default"
hybrid = "embedding-default"
```

The checked-in template pins structural defaults and stable profile identifiers, not credentials or machine-local endpoints. Every route name is from the complete closed capability vocabulary. Unknown or missing fields fail validation.

### Existing-project cutover

The workspace derives a schema-3 candidate from the current backend inventory, exact compatible BSL Analyzer probe, configured agent operation policies, and named service profiles. Preview shows:

- exact backend builds and surface identity;
- lexical and hybrid route assignments;
- embedding identity and disclosure state;
- affected components;
- old operational instances that will become inactive;
- missing indexes that will require an explicit build;
- confirmation that canonical project data and existing index files are unchanged.

Apply accepts only the unchanged file fingerprint and reviewed plan fingerprint, writes schema 3 atomically, and starts no process or build. It does not need a schema-2 runtime parser: the one-time target cutover is delivered and executed before the release that removes the old reader, or by a reviewed release helper that accepts only the exact pre-recorded target file fingerprint and produces the closed schema-3 document without executing legacy semantics. The helper is maintenance-only, lives outside the distributed runtime, and is deleted after the target and template cutover are verified.

### Index lifecycle

Schema-3 target identity includes modality and embedding identity, so schema-2 instances cannot accidentally satisfy readiness. After apply:

1. readiness reports lexical/hybrid missing without fallback claims;
2. the operator selects affected components and creates missing indexes;
3. promoted schema-3 instances become current only after existing manifest, lease, quota, and atomic-promotion checks;
4. old instances appear in garbage-collection preview;
5. deletion remains separately confirmed and confined to inactive operational paths.

### User experience

The index page exposes one task-oriented flow:

1. **BSL Analyzer** — install/repair and prove complete contract readiness;
2. **Embedding service** — configure, acknowledge, and probe;
3. **Semantic search** — review and enable routes;
4. **Indexes** — explicitly create missing lexical/hybrid instances;
5. **Ready** — show available operations and selected engines.

Advanced diagnostics retain exact fingerprints and safe failure codes. No page offers schema selection or rollback to an obsolete format.

## Alternatives Considered

### Keep schema 2 readable but not writable

Rejected. It leaves a permanent parser, fixtures, error paths, and ambiguity over whether a generated repository is operational before migration.

### Auto-upgrade schema 2 on service startup

Rejected. Startup would mutate tracked project configuration, could bind a wrong embedding profile, and would hide the disclosure and rebuild consequences.

### Convert existing index instances

Rejected. Indexes are disposable and schema-3 identity is stronger. Conversion would be more complex and less trustworthy than rebuilding.

### Change only `sppr-research-ver2`

Rejected. The canonical template would restore schema-1/2 code during the next synchronization and new projects would keep inheriting obsolete formats.

## Audit Matrix

| Area | Required outcome | Planned evidence |
| --- | --- | --- |
| Configuration | Only exact schema 3 loads and serializes | unit fixtures for accepted shape and rejected 1/2/unknown inputs |
| Generation | New repository starts with schema 3 | bootstrap/template checks and archive inspection |
| Runtime parity | installable runtime equals generated runtime | preview/apply synchronization and fingerprint manifest checks |
| Current project | `sppr-research-ver2` uses reviewed schema 3 | exact file/API read plus workspace readiness response |
| Semantic service | profile identity, probe, disclosure and secret boundary hold | profile API tests and safe diagnostics |
| BSL Analyzer | exact contract 1.3 complete surface is proven | offline contract fixture plus live opt-in conformance |
| Index state | no old instance is reused or deleted during cutover | before/after operational manifests and GC dry run |
| Data safety | sources, generations, DIF/MRQ, decisions and outputs unchanged | file manifests before/after cutover |
| UI | one end-to-end setup path, no schema controls | component tests and built-app browser acceptance |
| Compatibility removal | no schema-1/2 executable branches remain | targeted source scan and mutation tests |
| Packaging | no customer state or operational indexes enter artifacts | wheel/zip content inspection |
| Rollback | code rollback restores prior release; data remains untouched | release rollback rehearsal without configuration downgrade |

## Release And Rollback

The safe sequence is expand, cut over, contract:

1. finish and archive `add-complete-bsl-analyzer-search-surface`;
2. provision an exact compatible BSL Analyzer build and verify contract 1.3;
3. add the task-oriented schema-3 setup UI and closed schema-3 runtime while the temporary cutover helper still exists;
4. generate and validate a schema-3 template repository;
5. preview and apply the exact `sppr-research-ver2` configuration cutover;
6. build and verify new lexical/hybrid indexes;
7. prove canonical data manifests unchanged;
8. remove the helper and every schema-1/2 runtime/test/doc branch;
9. synchronize canonical and generated runtimes, build release artifacts, and run the full release matrix.

After contraction, rollback means restoring the prior application release and the pre-cutover tracked `research/indexing.toml` from version control or the reviewed cutover artifact. The new runtime does not contain an in-product downgrade. Operational schema-3 indexes may remain because the prior release must ignore unknown identities; no customer evidence rollback is required.

## Risks

- **Irreversible runtime contraction:** once readers are removed, an uncut project cannot start on the new release. Release checks must enumerate every supported generated repository before contraction.
- **Profile mismatch:** a wrong model or dimension invalidates semantic identity. Preview and probe must bind exact profile identity before apply.
- **Accidental data mutation:** broad synchronization or cleanup could touch project state. All operations must use owned-path manifests and fail closed on scope drift.
- **False readiness:** lexical readiness must never satisfy hybrid readiness; modality-specific target and probe tests remain mandatory.

## Assumptions And Open Questions

- The operator-approved cutover set contains only `Presail/sppr-research-ver2` (schema 2). `/Projects/OneC/sppr-research` (schema 1) was discovered but is explicitly outside this change and must remain untouched.
- BSL Analyzer commit `3c97c237200fcfe90547f8960bb93d3a623df4d7` is installed and live-probed with machine contract 1.3 and the complete search surface.
- The release version number is intentionally left to release policy; the plan requires an explicitly incompatible version boundary.
