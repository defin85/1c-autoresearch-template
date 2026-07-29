# Source Search Operations

Source indexes are disposable user-scope state. They help workers navigate the
active immutable source generation, but only files re-read from the canonical
generation can become evidence.

## Configuration and migration

`research/indexing.toml` schema version 1 remains readable and keeps the
existing exact-version `rlm-tools-bsl` index usable when its repository,
component, generation, representation, and content identities still match.
Reading version 1 never rewrites the file.

Use the index workspace to edit the typed version 2 value. Preview shows the
normalized file and impact fingerprint. Apply requires the unchanged file and
plan fingerprints, replaces only `research/indexing.toml`, and starts no build.
Use **Ensure indexes** after apply. Route order is authoritative; a ready later
member is a degraded fallback, not a blocker.

To downgrade, preview exactly `{"schema_version": "1"}`. Downgrade is allowed
only for one `rlm-tools-bsl` backend when every route contains exactly that
backend. Any BSL Analyzer backend, missing route, or different route order
blocks the downgrade without changing the file.

## Build, validation, and disk ownership

**Validate readiness** is read-only and never starts a build. **Rebuild**
requires confirmation. Each build uses a target-scoped fenced lease, an
owner-only private staging directory, exact source and index manifests, an
immutable promoted instance, and an atomic current pointer. A dead process
lease is recoverable; a live owner blocks a concurrent build.

Operational indexes live below
`$XDG_STATE_HOME/one-c-autoresearch/indexes-v2` (or the service state root), not
in the repository. The user running the workspace owns this directory. It can
be removed to force rebuilding without changing source, DIF, MRQ, or published
generations. Do not copy it into a repository, wheel, source archive, or
research deliverable.

## Worker search and limits

An agent profile may enable the provider-neutral `source_search` tool. The
profile must declare permitted operations and every per-call and aggregate
limit. The resolved policy and source scope are frozen for a new invocation;
editing a profile never changes a running invocation.

Before a worker starts, the coordinator reserves context capacity for maximum
query bytes, canonical returned bytes, and fixed tool framing. Each call then
atomically reserves its call, concurrency, query, result, returned-byte, and
backend-time capacity. If only part of aggregate backend time remains, that
remainder becomes the call deadline. Exhaustion reports the exact limit,
configured value, consumed amount, and request. Prior completed reads remain
valid, but no violating backend process starts.

Fallback is allowed only when an earlier route member is unavailable,
unsupported, or has no compatible ready index. A successful empty result does
not trigger fallback. Results from several backends are never merged or
re-ranked.

## Provenance and HMAC rotation

The ledger stores only a versioned project-scoped HMAC of each query plus
scope, route, adapter, index, canonical result-manifest, counts, duration, and
terminal state. It stores no query text, raw backend output, capability secret,
source snippet, or tool transcript.

Rotate the project key with the server-owned
`source_search_bridge.rotate_hmac_key(project_state_root)` operation during a
maintenance window. Rotation creates the next `vN` owner-only key and switches
the current pointer atomically. Old keys remain so prior ledger identities stay
attributable; remove them only after the corresponding operational ledgers no
longer need retention. Keys and their pointer must never enter repository
artifacts or logs.

## Cancellation, rollback, and recovery

Cancellation closes admission, terminalizes reserved calls, kills bounded
adapter process groups, discards late output, and never replays a search.
After an unclean restart, reconcile the dispatcher before resuming; incomplete
calls are terminal operational records and must be requested again by a new
worker invocation.

Rollback the runtime independently from canonical research data:

1. stop active dispatcher and index actions;
2. restore a previously reviewed `research/indexing.toml`, or use the exact
   version 1 downgrade when representable;
3. retain compatible old promoted indexes, or delete disposable operational
   index state;
4. validate readiness, then ensure only missing routes;
5. start a new invocation so policy, scope, route, key version, and capacity
   reserve are frozen consistently.
