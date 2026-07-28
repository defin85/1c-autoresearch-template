## Context

The current repository has two source channels:

- uploaded external artifacts declared in `research/external-artifacts.toml`;
- extensions discovered from each tested infobase profile by `ibcmd extension list`.

The first channel is explicit and tracked. The second is stored in user-scope tested connection state and is automatically converted into routing members. When an extension exists only in `target_cf`, physical comparison intentionally compares an empty directory with the extension export, after which the semantic analyzer expands the added files into extension interventions. This is correct comparison machinery applied before the missing scope decision.

## Locked Decisions

- Extensions remain separate 1C components and are never represented as members of the main configuration.
- Automatic enumeration discovers facts only and grants no authority to include an extension in research.
- Every currently discovered extension UUID requires one explicit tracked decision: `include` or `exclude`.
- The decision is keyed by normalized extension UUID and applies across all three source roles; role-specific presence, activation, name, and version remain observed facts.
- The tracked decision applies regardless of the observed activation flag; inactive extensions are neither silently included nor silently excluded.
- `exclude` requires a non-empty rationale. No name, prefix, active flag, or heuristic can create an exclusion automatically.
- Only included extensions enter routing, acquisition, indexing, semantic extension analysis, DIF inventory, target coverage, and DIF/MRQ processing.
- Excluded extensions remain visible in the route preview with observed role facts and rationale but produce no physical or semantic DIF.
- Extension decisions are additive fields in the existing infobase source contract; no second general-purpose registry is introduced.
- Changing a decision or discovering an unreviewed UUID invalidates the route preview. Applying a changed decision starts a new comparison epoch and makes prior downstream generations stale without rewriting them.
- Immediately before export, acquisition repeats bounded read-only discovery and UUID identity verification for all three roles. Any difference from the reviewed observations aborts before repository publication and requires a refreshed review.
- A dormant decision automatically applies if the exact UUID is discovered again; changed observations are shown and fingerprinted, but UUID identity remains the scope authority.
- The uploaded external-artifact contract remains independent. The workspace labels that section as external files and explains that extensions are reviewed separately.
- A component grouping is a derived stage-3 input view over existing stable DIF, not a canonical entity, repository registry, active pointer, approval target, or generation.
- Every customer DIF remains present in the canonical inventory and receives exactly one existing-schema classification row before stage 3 becomes runnable.
- Deterministic whole-component classification runs only inside an explicitly started stage-2 job; it never starts stage 3 automatically.
- If an included extension or declared external-artifact component is wholly added or deleted between the customer comparison roles, the coordinator classifies all of its DIF as meaningful deterministically without an agent call.
- If the same component exists on both sides and is modified, its DIF follow the ordinary stage-2 meaning-or-noise analysis.
- Stage 3 receives whole-component DIF grouped by extension UUID or external-artifact ID and may form one or more ordinary MRQs; it does not publish a package object.

## Goals / Non-Goals

**Goals:**

- Make the complete effective source scope visible and reviewable before acquisition.
- Prevent service, diagnostic, or other technical extensions from silently becoming customer DIF.
- Preserve deterministic component routing and UUID-based extension identity.
- Avoid per-method agent classification when the user has already included a component that is wholly added or deleted.
- Preserve functional decomposition by letting MRQ consolidation split one derived component group into multiple ordinary MRQs.
- Keep CLI and browser behavior aligned through the same tracked contract and validation.
- Provide a fail-closed migration for repositories created before extension decisions existed.

**Non-Goals:**

- Deciding whether any specific extension is business or technical.
- Merging extensions into the main configuration export.
- Uploading CFE files through the EPF/ERF external-file flow.
- Adding wildcard, prefix-based, or role-specific policy rules.
- Creating a persistent component-package registry, stable package ID, package approval, or package generation.
- Automatically declaring every modification inside a component meaningful when that component exists on both comparison sides.

## Decisions

### 1. Extend the existing infobase source contract

`research/infobases.toml` SHALL contain a top-level `extension_decisions` array of tables. Absence is read as an empty array, and fresh contracts write the empty array explicitly. Every record has exactly the keys `uuid`, `decision`, and `rationale`; records are serialized in normalized UUID order. `uuid` is a lowercase canonical UUID, `decision` is exactly `include` or `exclude`, and `rationale` is NFC-normalized and trimmed, empty for `include`, and non-empty for `exclude`. The repository contract owns intent; tested connection profiles own observed names, versions, activation flags, and presence.

The validator SHALL reject additional record keys, duplicate UUID decisions, non-canonical or malformed UUIDs, unsupported decisions, non-empty include rationales, and empty exclusion rationales. A decision referring to an extension no longer discovered in any role remains dormant and creates no routing member; if that exact UUID reappears, the existing decision applies.

Alternative rejected: storing decisions only in browser or SQLite state would make CLI acquisition behave differently and would omit scope intent from repository review.

### 2. Block on discovered UUIDs without decisions

Route preview SHALL compute the union of extension UUIDs discovered across the three current tested profiles. Any member of that union without a tracked decision yields a typed `extension_scope_required` blocker containing bounded role facts and no credentials. Acquisition cannot start while this blocker exists.

Alternative rejected: default inclusion recreates the defect; default exclusion can silently omit customer functionality.

Immediately before canonical source export, the acquisition worker SHALL repeat fixed read-only extension enumeration and UUID identity verification for all three roles under versioned server-owned command-time, overall-time, response-size, and extension-count bounds. UUID verification may stage extension exports in a private temporary directory because the list operation does not prove UUID identity. After all roles match the reviewed fingerprint, staged payloads for included UUIDs may be reused for acquisition; excluded payloads and all failed or stale staging are deleted and never enter canonical source generations, indexes, evidence, or agent context. A mismatch returns `extension_inventory_stale`, publishes no source generation, changes no tracked decision, and requires refreshed connection tests and route preview.

Alternative rejected: trusting the saved profile until a manual retest leaves a window in which a newly added extension can escape review. A time-to-live policy adds configuration while retaining such a window.

### 3. Filter before acquisition and comparison

Component membership SHALL be constructed only for decisions marked `include`. This keeps excluded extensions out of source generations and prevents their file-level changes from reaching the extension analyzer. The route preview SHALL separately list excluded UUIDs, role observations, and rationale so exclusion remains auditable without creating synthetic DIF.

Alternative rejected: exporting everything and filtering after DIF construction wastes work and allows excluded content to leak into indexes, prompts, and evidence.

### 4. Treat scope changes as comparison-epoch changes

The canonical source-contract fingerprint SHALL include normalized extension decisions. A changed decision or a newly discovered UUID invalidates prior previews. Applying a changed decision starts a new comparison epoch; existing immutable source, DIF, classification, and MRQ generations remain readable but cannot satisfy current readiness.

### 5. Separate extensions from external files in the workspace

The source setup SHALL render a dedicated extension section after tested connections. It shows one row per UUID, all three role observations, the explicit decision control, and rationale where required. The current external-artifact section SHALL be titled `External files (EPF, ERF, and source trees)` and its empty state SHALL say only that no uploaded external files are declared.

Rows are sorted by UUID, observed names remain role-labelled facts, and activation state is explicitly informational rather than a default decision. An `include` choice explains that the component enters source comparison and that a whole-component addition or deletion receives deterministic meaningful classifications; an `exclude` choice requires rationale and explains that no canonical payload or DIF will be published. Dormant decisions appear separately as not currently detected.

The UI SHALL preview the exact tracked contract mutation and comparison-epoch impact and require confirmation through the existing stale-fingerprint and idempotency controls. It SHALL preserve unsaved decisions during unrelated reconciliation, associate every control and error with its UUID row, support keyboard operation and accessible names, and SHALL not maintain a browser-only shadow decision.

### 6. Derive component groups without a new authority

For customer DIF belonging to an included extension or any declared external artifact, the coordinator SHALL derive `component_kind` and `component_key` from canonical source and diff facts. Extension keys use normalized UUID; external-artifact keys use the existing `external_artifact_id`. These values are stage input context and SHALL NOT create another repository record, stable identity namespace, active pointer, approval target, or lifecycle. Declaration is the existing explicit inclusion decision for an external artifact.

When a component exists only on one side of the customer comparison, every DIF owned by that component SHALL receive an existing-schema deterministic `meaning` classification row. The row remains bound to its own stable DIF and physical evidence, uses a versioned coordinator-owned deterministic instruction/profile identity, and requires no classification-agent invocation. This satisfies the existing complete-classification gate without bypassing it.

On explicit start or resume of stage 2, the coordinator derives eligibility from routing membership while selecting each existing bounded DIF window. It produces deterministic rows locally, invokes the classification agent only for non-eligible members of that window, and atomically publishes the complete validated window into the accumulated classification generation. A validation or binding failure publishes none of the current window; previously published windows remain valid retry evidence. If deterministic windows complete the inventory, stage 2 becomes complete without an agent call but stage 3 still requires its existing explicit start.

When the component exists on both sides and is modified, its member DIF SHALL continue through ordinary stage-2 classification because serialization noise and independent behavior changes remain possible.

The deterministic algorithm identity is `whole-component-meaning/v1`; its version participates in the existing instruction, profile, context, and result fingerprints so an algorithm change cannot silently reuse old rows. Added-component evidence resolves from `target_cf`; deleted-component evidence resolves from `vendor_baseline`. Both remain confined to the active immutable source generation and use the exact physical or semantic evidence already owned by each DIF.

At stage 3, the coordinator SHALL present wholly added or deleted component members through the existing bounded consolidation partitioning as a derived component summary plus deterministic pages of exact member DIF IDs, role-correct evidence references, and target-coverage facts. The group never replaces, merges, or synthesizes its member DIF into one large DIF: every member keeps its stable DIF identity, evidence, classification row, and downstream ownership. Page boundaries do not create identities or permit partial closure. The component grouping is a hint: it does not force one MRQ, prevent retention of compatible prior MRQs, or override normal merge, split, and supersede decisions. The consolidation agent MAY create one or more ordinary MRQs from the component group. It MAY relate an MRQ to DIF from another component or the main configuration only with explicit evidence and rationale. Canonical ownership remains the existing rule that each primary DIF belongs to exactly one MRQ or approved noise disposition.

Alternative rejected: a persistent component-package entity would duplicate DIF lineage, approvals, generations, and stale-state handling without adding a required business decision.

## Audit Matrix

| Concern | Required evidence |
| --- | --- |
| Contract | Extension decisions are canonical, UUID-keyed, validated, and included in source fingerprints |
| Completeness | Union discovery across all three roles blocks when any UUID is unreviewed |
| Correctness | Only included extensions become routing members and source-generation components |
| Auditability | Excluded extensions remain visible with role facts and rationale but create no DIF |
| Stage boundary | Whole-component additions/deletions receive complete deterministic DIF classifications; modified existing components retain ordinary analysis |
| Authority | Component grouping is derived from canonical facts and creates no registry, pointer, approval, or stable package identity |
| DIF identity | Grouping never replaces many stable member DIF with one synthetic component-level DIF |
| Decomposition | Stage 3 can split one component group into multiple MRQs while preserving exact primary-DIF ownership |
| Evidence | Added and deleted component DIF resolve evidence from the correct active source role and remain path-confined |
| UI consistency | Extensions and uploaded external files are separate sections using the same server contract |
| Accessibility | UUID rows, role observations, decision controls, rationales, errors, and impact text are keyboard-operable and programmatically associated |
| Compatibility | Old immutable generations remain readable; new acquisition fails closed until review |
| Concurrency | Preview/apply rejects stale profile enumeration, contract fingerprint, or workflow fingerprint |
| Security | Responses contain no credentials; discovery is fixed and bounded; private identity staging is deleted on exclusion, drift, failure, and cancellation |
| Freshness | Acquisition repeats bounded live discovery and publishes nothing on observation drift |
| Performance | Discovery uses fixed count, response, command, and overall operation bounds; large groups reuse stage-3 partitioning |
| Operability | Blockers identify exact UUIDs and roles and provide a direct review action |
| Verification | Backend, frontend, browser, CLI, scaffold, synchronization, and strict doctor checks cover the contract |

## Execution Plan

1. Add schema, parsing, canonicalization, and fingerprint coverage for extension decisions.
2. Make route preview compute discovered, included, excluded, dormant, and unreviewed UUID sets, fail closed on the last set, and repeat bounded live discovery before export.
3. Filter component membership before acquisition and preserve exclusion evidence in the preview.
4. Add deterministic classification for wholly added or deleted included components and derive bounded component-grouped stage-3 context from existing facts.
5. Add the dedicated extension-review UI and clarify external-file labels.
6. Add migration, stale-preview, role-drift, grouping, split-MRQ, and no-DIF-for-excluded-extension tests.
7. Implement target-first, synchronize reusable runtime and scaffold outputs, rebuild packaged assets, and run the full verification matrix.

## Risks / Trade-offs

- [Existing projects with extensions become temporarily blocked] → Keep previous generations readable and provide the exact UUID review action.
- [An extension is renamed between roles] → Key decisions only by UUID and display names as observed role facts.
- [An extension appears after the last preview] → Revalidate tested-profile and contract fingerprints immediately before acquisition.
- [Fresh UUID verification lengthens acquisition] → Stage once under versioned server-owned bounds and reuse verified included payloads for canonical acquisition.
- [A user excludes required customer behavior] → Require an explicit rationale and show all role facts; do not infer the decision.
- [Dormant decisions accumulate] → Display them as not currently detected; retain them because deletion is unnecessary for correctness.
- [Excluded content is still exported accidentally] → Test source-generation manifests and filesystem output, not only route-preview rows.
- [One extension contains several independent features] → Pass one derived block to stage 3 but allow it to produce multiple ordinary MRQs.
- [Automatic meaning hides serialization noise in a wholly added component] → Treat the complete included component as the meaningful change; retain internal rows as evidence rather than spending agent work proving that newly added files are new.
- [Derived grouping becomes a hidden authority] → Prohibit package persistence and verify that removing the derived view leaves canonical DIF, classifications, and MRQ ownership unchanged.
- [A user includes a large technical component without understanding the consequence] → Show the source, DIF, and deterministic-classification impact before confirmation and require explicit exclusion rationale for the opposite decision.

## Migration Plan

1. Upgrade without rewriting existing source or downstream generations.
2. On the next connection preflight or route preview, compute the discovered UUID union.
3. If decisions are missing, return `extension_scope_required` and open the extension-review section.
4. Persist the confirmed decisions through the normal tracked-contract mutation.
5. Repeat live discovery immediately before export; on drift, refresh review rather than publishing.
6. Start a new comparison epoch and acquire a new generation only after the route is ready and the live observations still match.

Frontend-only rollback is safe while the new backend continues enforcing the contract. Full runtime downgrade is not a supported mutating mode: operators stop the workspace and CLI mutation entrypoints before downgrade and may use repository files and immutable generations only for read-only recovery, because the prior runtime does not enforce exclusions. New acquisition resumes only after reinstalling the enforcing runtime. No rollback step deletes decisions or rewrites evidence.

## Assumptions And Open Questions

- Assumption: extension UUID is the stable cross-role identity already required by current routing.
- Assumption: one decision per UUID is sufficient; role-specific include/exclude rules are intentionally unsupported.
- Assumption: existing `external_artifact_id` is the stable component key for EPF, ERF, and source-tree artifacts.
- Open questions: none.
