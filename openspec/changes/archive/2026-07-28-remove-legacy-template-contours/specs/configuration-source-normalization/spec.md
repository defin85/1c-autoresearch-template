## REMOVED Requirements

### Requirement: Normalize supported 1C source formats
**Reason**: The standalone normalization artifact and CLI are superseded by canonical source acquisition and diff generation.
**Migration**: Configure supported source roles and run canonical source acquisition.

### Requirement: Compare normalized snapshots reproducibly
**Reason**: Snapshot comparison is now internal to canonical immutable diff generation rather than a public competing artifact contract.
**Migration**: Rebuild the canonical diff generation from the active source generation.

### Requirement: Assign stable diff identities
**Reason**: Stable DIF identity is owned by the canonical diff workflow and no longer by the retired standalone normalizer.
**Migration**: Use identifiers emitted by the canonical active diff generation.

### Requirement: Handle large tabular artifacts safely
**Reason**: The standalone parser capability is removed with its legacy artifact surface.
**Migration**: Process supported source inputs through canonical acquisition and diff operations; retain the previous release for unsupported standalone exports.
