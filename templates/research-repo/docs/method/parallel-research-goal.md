# Parallel Research Goal

`/goal Параллельное исследование` uses deterministic read-only worker units and
one locked writer. Resume a compatible run or create a small pilot, then run
the configured model, worker count, timeout, source profile, and Git refs from
`project.toml`. Workers may read only `allowed_sources` and must return the
structured decision schema. The coordinator validates evidence and source
fingerprints before publication; incomplete or stale units remain rejected.

This pass establishes source-side business meaning and `CUS-*` to `MRQ-*`
relations. Target-release equivalence remains a separate functional-gap pass.
