# Reverse Functional Map

This workflow supports long-running reverse engineering where the agent must reconstruct functional intent from existing 1C customizations.

It is not an automatic business classifier. The durable source of truth is the repository state under `analysis/reverse-map/`; the agent context is disposable.

## Entry Trigger

When the user writes a short continuation trigger such as `/goal Исследование`, the agent should:

1. Read `project.toml`, `docs/agent/repo-map.md`, and `analysis/reverse-map/state.md`.
2. Run `python -m one_c_autoresearch reverse-map claim`.
3. If a workitem is returned, process exactly that workitem.
4. If no workitem is returned, run `python -m one_c_autoresearch reverse-map status` and report whether coverage is complete or blocked.
5. Update `analysis/reverse-map/coverage.csv`, `decisions.csv`, `unresolved.csv`, and the scenario folder.
6. Run `python -m one_c_autoresearch doctor` and `python -m one_c_autoresearch checks research`.
7. Stop with the claimed workitem either advanced or explicitly blocked.

## State Files

- `coverage.csv`: every diff row and its reverse-map review status.
- `workitems.jsonl`: durable queue for scenario or cluster research.
- `decisions.csv`: manual or agent-recorded assignment decisions.
- `unresolved.csv`: facts that need infobase data, business review, or follow-up evidence.
- `scenarios/`: one folder per reconstructed scenario.
- `outputs/`: intermediate human-facing reverse-map outputs before promotion to final deliverables.

## Coverage Statuses

- `unreviewed`: diff row has not been assigned to a reverse-map workitem.
- `assigned`: diff row is assigned to an open workitem.
- `confirmed_in_scenario`: fact belongs to the reconstructed scenario.
- `supporting_shared`: fact supports multiple scenarios or a shared layer.
- `belongs_to_other_scenario`: fact was inspected and moved elsewhere.
- `technical_platform`: technical/platform customization, not a standalone business scenario.
- `technical_noise`: reviewed as non-business noise.
- `needs_infobase_data`: source code is insufficient; live or exported infobase data is needed.
- `needs_manual_review`: business meaning is unclear after static analysis.
- `out_of_scope`: reviewed and excluded with rationale.

## Quality Rule

The agent may generate hypotheses, but final map rows must be evidence-backed. If a claim cannot be tied to source files, lines, metadata, or explicit runtime-data requirements, it belongs in `unresolved.csv`, not in the final map.

## Continuation Rule

The trigger is not the memory. The trigger only tells the agent to read the current reverse-map state and continue from the next uncovered or pending item.
