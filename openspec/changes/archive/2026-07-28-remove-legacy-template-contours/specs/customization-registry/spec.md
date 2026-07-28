## REMOVED Requirements

### Requirement: Maintain atomic customization records
**Reason**: The `CUS` registry is a superseded authority replaced by canonical DIF classifications and MRQ ownership.
**Migration**: Move supported evidence into canonical DIF/MRQ generations before upgrading, or remain on the prior release.

### Requirement: Distinguish evidence from business conclusions
**Reason**: The requirement is coupled to the removed `CUS` registry model.
**Migration**: Use canonical evidence references attached to DIF classifications and MRQ results.

### Requirement: Support external reports and processors
**Reason**: External artifacts are now declared and acquired through the canonical source-generation contract rather than `CUS` records.
**Migration**: Declare EPF/ERF artifacts through `research/external-artifacts.toml` and reacquire sources.

### Requirement: Bootstrap and validate the registry
**Reason**: Registry bootstrap and validation would recreate a competing authority.
**Migration**: Use canonical source acquisition, DIF classification, and MRQ consolidation operations.
