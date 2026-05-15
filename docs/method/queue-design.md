# Queue Design

The queue is a JSONL file, not a service.

This is intentional:

- Codex automation can read and write it without infrastructure.
- Every state transition is visible in Git.
- The queue can be reviewed, edited, or repaired manually.
- A task can carry enough context to survive context-window resets.
- A claim helper serializes task selection and status update so parallel agents
  do not claim the same task.

## Unit of Work

Use a functional unit, not a source file:

- document type;
- business process scenario;
- scheduled job behavior;
- integration flow;
- report or print form;
- access/routing rule;
- migration mapping item.

## Task Lifecycle

```text
pending -> claimed -> evidence_pack -> drafted -> needs_review -> done
                                      -> needs_followup -> pending
                                      -> blocked
```

## Worker Rules

- Process exactly one task per run.
- Claim the task with `scripts/queue/Claim-NextAnalysisTask.ps1`.
- Prefer indexes and cached evidence before broad source searches.
- Record source file and line for every confirmed claim.
- Run positive and negative search.
- Create follow-up tasks for runtime-data dependencies.
- Run the repository doctor before moving a task out of `claimed`.
- Stop after updating the selected task.
