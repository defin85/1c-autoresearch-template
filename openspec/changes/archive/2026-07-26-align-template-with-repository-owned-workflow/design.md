## Context

The template predates the repository-owned workflow now verified in `sppr-research`. Its generated scaffold contains multiple legacy queues and mutable analysis paths, and its reusable package exposes a different workspace model. The reference repository has the required seven gates, seven jobs, eight operations, immutable generations, typed application boundary, user-scope operational state, and source setup needed by folder import.

The template worktree also contains unrelated uncommitted managed-workspace edits. This change must preserve them and avoid treating the current top-level template UI as the generated research UI.

## Goals / Non-Goals

**Goals:**

- Generate a self-contained repository that implements the verified repository-owned workflow contract.
- Port reusable source acquisition, external artifact declarations and drafts, workflow service, local API/UI, schemas, tests, and verification commands without customer data.
- Make a freshly generated repository pass its strict doctor and test/build gates.
- Establish one explicit synchronization inventory so future reusable changes can be ported deterministically.

**Non-Goals:**

- Migrating existing generated repositories in place.
- Copying source generations, customer configuration exports, binaries, analysis results, host paths, credentials, or user-scope state.
- Replacing the template repository's own legacy managed workspace in this change.
- Preserving removed queue commands or compatibility readers inside newly generated repositories.

## Decisions

### 1. Treat the verified project runtime as the generated-repository contract

The generated scaffold will contain the customer-independent files from the verified runtime: project and research declarations, workflow and source schemas, Python package, local workspace source, tests, and bootstrap verification. A checked-in inventory records every synchronized path and rejects missing or customer-owned paths.

Alternative rejected: selectively adapting folder import to the old template workspace would preserve two incompatible workflow authorities and make parity unverifiable.

### 2. Keep template control plane and generated runtime separate

Existing top-level `workspace_api.py`, React-admin UI, compiled assets, and their uncommitted edits remain untouched except where bootstrap must expose the new generated payload. The generated repository receives its own current runtime under `templates/research-repo/`.

Alternative rejected: replacing the top-level workspace would overwrite unrelated work and broaden the change beyond generation parity.

### 3. Build the generated payload from a reviewed allowlist

A deterministic synchronization script copies only allowlisted reusable paths from a reference repository, rejects absolute host paths, customer identifiers, binaries and generated payloads, and writes no non-empty destination without explicit replacement. The checked-in scaffold is the release artifact; the reference path is never stored in it.

Alternative rejected: copying the whole project repository would leak immutable customer evidence and derived analysis.

### 4. Fresh generation is the migration boundary

Bootstrap creates the new contract only in an empty target. Existing repositories are not upgraded implicitly. The generated repository must start with empty declarations and valid empty active-state pointers or documented initial blockers, then advance only through typed operations.

### 5. Verification compares behavior, not only files

Tests assert the exact gate/job/operation catalog, canonical path ownership, absence of legacy authorities, user-scope storage boundaries, source setup/folder import behavior, strict doctor, Python suite, web suite, and production build. A fresh generated repository is exercised directly.

## Risks / Trade-offs

- [Large one-time scaffold replacement] → Use an allowlist, generated-repo tests, and no in-place migration.
- [Reference implementation can drift] → Check a synchronization manifest and behavior assertions rather than relying on manual copying.
- [Top-level template workspace remains architecturally different] → Keep the boundary explicit and schedule convergence only when its current work is landed.
- [Generated package duplicates reusable top-level modules] → Accept temporary duplication to avoid destructive merging; remove it only after measured consolidation.

## Migration Plan

1. Add the synchronization inventory and safe scaffold builder.
2. Replace the generated repository contract with customer-independent verified files.
3. Add bootstrap and generated-repository behavior tests.
4. Port external folder import after the workflow contract is green.
5. Rebuild the distributable research template archive without touching unrelated top-level workspace changes.

Rollback restores the prior generated scaffold and inventory; already generated repositories remain unchanged.

## Open Questions

None.
