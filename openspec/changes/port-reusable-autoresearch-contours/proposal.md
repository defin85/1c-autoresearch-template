## Why

The template currently stops at raw diff inventories, subject cards, and functional-gap scaffolds, while mature project repositories have had to build their own source normalization, stable evidence identities, customization graph, and parallel research tooling. Porting these proven contours into the template will let new projects start with a reproducible end-to-end path instead of reimplementing the same infrastructure.

## What Changes

- Add normalized parsing and comparison for Designer XML/BSL and `v8unpack` sources, including forms, form modules, binary-sidecar handling, paged CSV reads, and stable diff identifiers.
- Add an atomic customization registry (`CUS-*`) that connects physical diffs, metadata parts, source evidence, external reports/processors, and analysis groupings.
- Add a unified migration-requirement graph (`MRQ-*`) with many-to-many `CUS-*` links, ownership rules, bounded agent contexts, customer-facing views, and specification generation.
- Add reusable queue planning and research-review support for ordinary sources and unpacked external `.epf`/`.erf` artifacts.
- Add a parallel research mode with deterministic work assignment, isolated read-only workers, structured decisions, trace and fingerprint validation, a single writer, resumable runs, and run compaction.
- Add an optional physical diff-cleanup contour for `v8unpack` sources with batch review, deterministic helper commands, stable evidence retention, and publishable clean-repository generation.
- Extend template bootstrap, CLI, validation, agent skills, and generated-repository scaffolds for the new capabilities.
- Keep customer data, generated research results, source dumps, worker traces, model-specific defaults, and product-specific BP 2.0/BP 3.0 assumptions out of the template.

## Capabilities

### New Capabilities

- `configuration-source-normalization`: Parse and compare supported 1C source formats into reproducible snapshots and stable diff evidence.
- `customization-registry`: Maintain atomic `CUS-*` customization records with source-backed evidence and external artifact support.
- `unified-migration-requirements`: Maintain the canonical `MRQ-*` graph, ownership, bounded contexts, derived views, and specification outputs.
- `parallel-research-exec`: Coordinate deterministic read-only research workers and apply validated results through one writer.
- `physical-diff-cleanup`: Classify and physically remove technical source-export noise while preserving an auditable decision history.

### Modified Capabilities

None. The repository has no published base OpenSpec capability contracts yet; integration with existing template commands is specified by the new capabilities.

## Impact

- Adds reusable modules under `src/one_c_autoresearch/`, CLI groups, tests, fixtures, agent skills, and English methodology documents.
- Extends `templates/research-repo/` with empty durable artifact layouts, configuration keys, ignore rules, and verification guidance.
- Updates shared `doctor`, template checks, research checks, queue handling, functional-gap generation, and customer-facing output generation.
- Introduces no third-party runtime dependency, database, queue service, or customer-specific source artifact.
- Requires staged delivery because the registry and migration graph depend on normalized source evidence, and parallel research depends on both graph layers.
