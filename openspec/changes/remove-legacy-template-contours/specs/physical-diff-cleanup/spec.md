## REMOVED Requirements

### Requirement: Build an auditable cleanup queue
**Reason**: The manual cleanup queue is a removed competing state authority.
**Migration**: Represent supported physical-diff evidence through canonical immutable diff generations.

### Requirement: Support batch source-backed review
**Reason**: Batch review through the old queue is superseded by bounded canonical DIF classification.
**Migration**: Use `dif.classify-next` and canonical evidence references.

### Requirement: Prefer semantic representations over binaries
**Reason**: This standalone cleanup contract is removed; canonical source acquisition and diff building own supported representations.
**Migration**: Reacquire supported source formats through the canonical source-generation workflow.

### Requirement: Apply cleanup with one writer
**Reason**: The cleanup writer is replaced by canonical typed operations and immutable publication.
**Migration**: Use canonical diff and classification operations.

### Requirement: Publish a clean source repository without run caches
**Reason**: The separate clean-repository publication contour is outside the canonical workflow.
**Migration**: Preserve any required historical output before upgrading; new work uses canonical source and diff generations.
