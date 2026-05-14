# Review Checklist

## Evidence

- Confirmed claims have file and line evidence where possible.
- Inferences are labeled `high`, `medium`, or `low`.
- Runtime-data dependencies are labeled `needs_infobase_data`.
- `cf`, `cfe`, and vendor baseline evidence are separated.

## Coverage

- Direct markers were searched.
- Synonyms and alternative paths were searched.
- Calls/references were traced at least one level outward from key procedures.
- Extension overrides were checked when `cfe` is in scope.

## 1C Areas

- Metadata.
- Forms and commands.
- BSL write/fill/validation handlers.
- Roles, rights, workgroups, and routing.
- Scheduled/background jobs.
- Business processes and tasks.

## Output

- Findings are grouped by functional behavior.
- Open questions are actionable.
- The result is readable without redoing the whole bucket analysis.
