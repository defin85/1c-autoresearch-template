## Why

Infobase preflight correctly discovers configuration extensions, but the current source-routing contract silently promotes every discovered extension into the research scope. The workspace simultaneously reports that no external artifacts are declared because that section covers only EPF, ERF, source trees, and other uploaded files. A user can therefore see an empty external-file contract while an automatically discovered technical extension is exported, compared with an empty baseline, and expanded into many customer DIF records.

## What Changes

- Add an explicit, repository-tracked include or exclude decision for every discovered extension UUID before source routing can become ready.
- Present extensions as their own source-contract section with per-role presence, activation, name, version, and decision state.
- Keep automatic extension enumeration as discovery evidence only; it SHALL NOT imply inclusion.
- Include only explicitly selected extensions in acquisition, semantic extension analysis, DIF inventory, target coverage, and downstream DIF/MRQ work.
- Record excluded extensions and their rationale in the route preview without exporting or classifying their internal files.
- Deterministically classify every DIF of a wholly added or deleted included extension or declared external-artifact component as meaningful without an agent call.
- Give MRQ consolidation a derived component-grouped view of those existing DIF so it can form one or more MRQs without introducing a new canonical package entity or replacing stable DIF identities.
- Continue ordinary per-DIF stage-2 analysis when the same component exists on both comparison sides and only part of it changed.
- Rename the existing workspace section to make clear that it manages uploaded external files such as EPF and ERF, not infobase extensions.
- Treat extension-scope changes or newly discovered unreviewed UUIDs as source-contract changes that invalidate the route preview and begin a new comparison epoch when applied.
- Repeat bounded read-only extension discovery immediately before acquisition and fail without publication when the live inventory differs from the reviewed snapshot.
- Preserve existing immutable generations for read-only use and require explicit extension review before a legacy project can acquire another generation.

## Impact

- Affected specifications: `managed-autoresearch-workspace`, `repository-owned-generated-research-workflow`.
- Expected implementation areas: source-contract validation, connection preflight summaries, source routing, acquisition, deterministic DIF classification, MRQ consolidation context, workflow fingerprints, workspace source setup, tests, generated scaffold synchronization, and operator documentation.
- Existing projects without extensions continue unchanged. Existing projects with discovered extensions remain readable but cannot acquire a new generation until each current UUID has an explicit tracked decision.
- This change does not alter the separate external-artifact upload contract and does not infer scope from extension names, prefixes, activation state, or technical purpose.
