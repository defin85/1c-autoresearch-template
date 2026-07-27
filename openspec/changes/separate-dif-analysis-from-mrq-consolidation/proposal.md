## Why

The current `discover-mrq` graph groups each bounded DIF window before the full DIF inventory has been classified. This makes stage 3 operate on incomplete evidence, repeats global grouping work, and does not invoke the existing canonical MRQ merge lineage when active MRQs must be consolidated.

## What Changes

- **BREAKING** Separate bounded DIF classification from global MRQ consolidation into independently runnable workflow jobs and operations.
- Make stage 2 consume successive DIF windows until every customer DIF has a durable meaning-or-noise-candidate classification.
- Add a repository-derived `all-dif-classified` gate; stage 3 is not runnable while any customer DIF remains unclassified.
- Make stage 3 consume the complete classified DIF set, canonical `CUS-*` registry, and complete active MRQ graph in one consolidation run.
- Preserve the canonical `DIF -> CUS -> MRQ` model: DIF records remain physical evidence, `CUS-*` records remain atomic semantic customizations, and implementation ownership remains on `CUS-*`.
- Require stage 3 to form new MRQs, merge duplicate or overlapping active MRQs through canonical lineage, and prove complete non-overlapping DIF-to-CUS evidence membership plus CUS-to-MRQ implementation ownership before publication.
- Publish the compatible CUS and MRQ generations through one atomically replaced aggregate consolidation pointer only after the result passes validation and receives explicit approval.
- Update dispatcher readiness, actions, progress, labels, recovery, and inspection so stage 2 and stage 3 have distinct leases, runs, counters, and blockers.
- Preserve bounded analyzer concurrency, immutable generations, typed operations, no automatic retry, and customer-independent template synchronization.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `repository-owned-generated-research-workflow`: Split DIF classification and MRQ consolidation into separate jobs, operations, gates, and canonical transitions.
- `customization-registry`: Preserve the DIF-to-CUS evidence boundary while rebuilding semantic customization membership from the complete classification generation.
- `unified-migration-requirements`: Require whole-inventory MRQ formation and canonical merge lineage before source-coverage publication.
- `managed-autoresearch-workspace`: Expose separate readiness, progress, controls, recovery, and collection details for stages 2 and 3.

## Impact

- Changes the canonical workflow catalog, operation versions, job dependencies, runner selection, dispatcher coordinator, LangGraph compilation, durable intermediate classification state, aggregate CUS/MRQ authority, separate target-decision generations, and active-generation validation.
- Changes workspace API job identifiers, dispatcher projection, frontend circuit-to-job routing, progress presentation, prompts, tests, and generated static assets.
- Requires a migration path for active or interrupted legacy `discover-mrq` runs and their cached node results; immutable source and physical-diff generations remain compatible.
- Requires target-first verification in a generated repository, synchronization into both template runtime copies, package rebuild, and strict template/OpenSpec validation.
- Adds no external dependency and does not move credentials or operational state into the repository.
