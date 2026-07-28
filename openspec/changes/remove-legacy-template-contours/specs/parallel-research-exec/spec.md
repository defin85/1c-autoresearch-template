## REMOVED Requirements

### Requirement: Plan deterministic non-overlapping work units
**Reason**: The queue/CUS parallel planner is superseded by canonical dispatcher jobs and bounded work units.
**Migration**: Use the canonical dispatcher projection and typed workflow operations.

### Requirement: Isolate read-only workers
**Reason**: Worker isolation is now owned by canonical invocation execution rather than the removed parallel-research contour.
**Migration**: Configure canonical agent profiles and dispatcher slots.

### Requirement: Validate structured decisions and traces
**Reason**: The old parallel decision and trace formats are no longer canonical.
**Migration**: Publish typed canonical result envelopes and retained invocation events.

### Requirement: Apply through a single writer
**Reason**: The legacy queue writer is replaced by typed canonical operations and immutable-generation publication.
**Migration**: Apply changes only through supported canonical operations.

### Requirement: Resume and compact runs safely
**Reason**: Legacy run directories and compaction are replaced by canonical invocation recovery and bounded retained events.
**Migration**: Use canonical dispatcher recovery and invocation inspection.

### Requirement: Keep execution policy configurable
**Reason**: The removed parallel-research policy would compete with canonical workflow and agent-profile configuration.
**Migration**: Configure the canonical workflow and agent profiles.
