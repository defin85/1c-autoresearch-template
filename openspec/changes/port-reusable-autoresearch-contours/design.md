## Context

The template already provides repository bootstrap, raw diff inventories, reverse mapping, subject cards, functional-gap scaffolds, dashboards, and validation. A mature generated repository now contains additional proven tooling, but it also contains customer-specific defaults, Russian BP 2.0/BP 3.0 terminology, generated evidence, and large run caches that must not be copied into the reusable template.

The new contours form a dependency chain: normalized source evidence feeds atomic customizations; customizations feed migration requirements; the graph feeds bounded parallel research; physical cleanup can improve the evidence before any of those layers are rebuilt. The template must ship code, contracts, empty scaffolds, and tests while generated repositories own all project data.

## Goals / Non-Goals

**Goals:**

- Port the proven behavior as reusable, source-format-aware template capabilities.
- Preserve file-based, Git-reviewable sources of truth and stable identifiers.
- Keep generated repositories runnable through `python -m one_c_autoresearch` without extra services.
- Make every contour optional and fail closed when prerequisite artifacts are absent or stale.
- Provide deterministic tests and small fixtures for every supported source representation.
- Preserve existing template commands and generated-repository compatibility.

**Non-Goals:**

- Copy customer source dumps, decisions, cards, traces, caches, or deliverables.
- Generalize product-specific accounting semantics into the template.
- Add a graph database, message broker, worker service, or runtime dependency.
- Run research automatically when creating a repository.
- Guarantee direct parsing of opaque 1C binary payloads when a machine-readable sidecar is unavailable.

## Decisions

### 1. Deliver in dependency-ordered capability slices

Implementation will proceed in five slices: source normalization, `CUS-*`, `MRQ-*`, parallel research, then optional physical cleanup integration. Each slice includes its CLI, scaffold, checks, docs, and tests before the next slice is enabled.

Alternative: copy all mature-project modules at once. Rejected because it would hide project assumptions and make failures impossible to localize.

### 2. Keep the package as the implementation source of truth

Reusable logic will live in `src/one_c_autoresearch/`. Root and generated-repository scripts remain thin compatibility entry points only where a documented direct invocation is still required. Shared JSON/JSONL/CSV, hashing, subprocess, and locking helpers will be reused rather than copied.

Alternative: place complete implementations in generated repositories. Rejected because fixes would diverge across projects.

### 3. Configure source roles instead of naming products

The contracts will use `source_vendor`, `source_customer`, and `target_vendor` roles resolved from `project.toml`. Model name, worker count, source roots, Git refs, and optional cleanup repository paths will be configuration or command arguments with conservative defaults.

Alternative: preserve BP 2.0/BP 3.0 names and known branch paths. Rejected as customer and product specific.

### 4. Preserve a layered file graph

Physical evidence receives stable diff identities; normalized metadata parts receive stable metadata identities; atomic customizations receive `CUS-*`; business migration requirements receive `MRQ-*`. JSONL is canonical for record sets, JSON is canonical for cards and manifests, and CSV/Markdown/XLSX remain derived views.

Alternative: collapse all layers into one record or use a graph database. Rejected because physical evidence, semantic customization, and business agreement have different lifecycles, while the current scale remains practical for indexed files.

### 5. Use read-only workers and one writer

Parallel workers receive deterministic, non-overlapping entity sets and exact allowed files in isolated workspaces. They cannot modify the repository. The coordinator validates structured output, evidence paths, trace summaries, source fingerprints, and task state before one locked writer applies results and runs block-level generators.

Alternative: allow each worker to commit its own changes. Rejected because shared JSONL graphs, queues, and generated views would race.

### 6. Prefer machine-readable sidecars over binary payloads

For `v8unpack` and Designer exports, structured JSON/XML/BSL/MXL representations take precedence. Binary payloads are retained as evidence only when no supported sidecar exists and are never heuristically interpreted as business behavior.

Alternative: parse every binary format directly. Rejected because it is fragile, format-version dependent, and unnecessary when supported exporters provide sidecars.

### 7. Ship empty scaffolds, not generated state

`templates/research-repo/` will contain README files, schemas, headers, templates, configuration examples, and ignore rules. Run manifests, decisions, traces, normalized source pairs, source dumps, and customer cards are created only in concrete repositories.

## Risks / Trade-offs

- [Large initial port] -> Deliver capability slices with independent checks and commits; do not copy unrelated register/report generators.
- [Mature code contains hidden customer assumptions] -> Add negative scans for absolute paths, customer names, fixed Git refs, and BP-specific prompt text.
- [CLI and check modules become monolithic] -> Register each capability through focused command builders and validators while retaining the current public command names.
- [Generated repositories drift from the package] -> Keep scripts thin and add template-generation smoke tests that execute the installed package against a fresh repository.
- [Stable identifiers change after normalization changes] -> Version canonicalization inputs and preserve an identifier map with active/inactive lineage.
- [Parallel runs consume large Git space] -> Ignore disposable workspaces and traces, support compacted audit packages, and never scaffold run output.
- [Optional cleanup assumptions do not fit every exporter] -> Gate cleanup by an explicit source profile and keep normalized source analysis usable without cleanup.

## Migration Plan

1. Port and generalize common helpers, source parser, metadata inventory, stable diff identities, CLI, fixtures, and checks.
2. Add empty customization-registry scaffolds and port the `CUS-*` builder, validator, context, and export commands.
3. Add empty migration-requirement scaffolds and port the `MRQ-*` graph, derived views, functional-gap integration, and specification generation.
4. Add queue profiles, external artifact research, agent skills, and the parallel coordinator with fake-worker integration tests.
5. Add the optional physical-cleanup command family and publishable-clean-repository checks.
6. Generate a fresh repository and run template checks, strict doctor, research checks, and representative end-to-end fixtures.

Rollback is slice-based: remove or disable the newly introduced CLI group and scaffold for the incomplete slice while retaining earlier stable slices. Existing template commands and artifact formats remain valid throughout.

## Open Questions

- Whether physical cleanup should be enabled by a single project flag or only by explicit commands; default behavior remains disabled either way.
- Whether the default parallel model belongs in `project.toml` or environment-only configuration; the implementation must not hard-code a newly released model name.
