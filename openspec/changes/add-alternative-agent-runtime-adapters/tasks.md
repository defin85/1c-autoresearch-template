## 1. Define The Common Runtime Contract

- [ ] 1.1 Add the exact Codex CLI, OpenCode, and Earendil Pi products, adapter protocols, and initial conformance fixtures.
- [ ] 1.2 Add versioned profiles and immutable snapshots that separate runtime, provider, model, adapter, permissions, and capabilities while leaving timeout and concurrency in tracked workflow configuration.
- [ ] 1.3 Deny all native OpenCode/Pi tools and external resources; expose only the common bounded `source_search` bridge when selected, while allowing provider API traffic without a model-visible web tool.
- [ ] 1.4 Add one conformance suite for exact product and protocol identity, catalog and capacity proof, search policy, bounded events, final model proof, cancellation, timeout, and restart recovery.

## 2. Preserve Codex Behavior

- [ ] 2.1 Move current behavior behind the exact `codex-cli/v1` adapter without changing its execution, validation, or publication semantics.
- [ ] 2.2 Normalize readable v1 Codex profiles to the `codex-cli` runtime and `openai-codex` provider and prove equivalent snapshots, prepared inputs, validations, and publications.
- [ ] 2.3 Keep unknown or unrepresentable legacy profiles readable but unavailable instead of guessing a runtime, provider, or model.

## 3. Add Pi And OpenCode

- [ ] 3.1 Implement exact `pi-rpc/v1` LF-delimited JSONL execution with a fingerprinted system prompt, explicit empty appended prompt, one invocation per process, no session persistence, no automatic retry or compaction, and no native tools.
- [ ] 3.2 Implement exact `opencode-server/v1` private REST/SSE execution with private XDG roots, a narrow read-only authentication reference, one server-owned agent, no global/project resources, no session reuse, and no native tools.
- [ ] 3.3 Probe exact product, executable, protocol, authentication readiness, model catalog, capacity, reasoning controls, input framing, every-message provider/model proof, exact Pi stop reasons, terminal-message proof, and adapter compatibility without reading or returning credentials.
- [ ] 3.4 Clean or quarantine temporary state on success, validation failure, provider failure, cancellation, timeout, process loss, and restart recovery, with bounded scavenging.
- [ ] 3.5 Reject unexpected tools, runtime model changes, malformed protocols, oversized output, unknown mandatory events, and unavailable context limits; discard only explicitly optional notifications with a bounded counter.

## 4. Integrate Lifecycle And Explicit Recovery

- [ ] 4.1 Route all adapters through the common context envelope, budget preflight, response schema, evidence validation, terminalization, and single-writer publication.
- [ ] 4.2 Treat bounded native events as observations while preserving existing job statuses and mapping timeout to `failed` plus `provider_timeout`.
- [ ] 4.3 Supervise each runtime process with persisted PID, process group, OS start token, state path, and lease; implement bounded cancellation and safe restart orphan recovery.
- [ ] 4.4 Permit only an explicit reviewed operator retry as a new invocation with `retry_of_invocation_id`; never retry, switch profile/model, or continue a partial result automatically.
- [ ] 4.5 Derive the cross-run invocation-binding reuse fingerprint without run or lineage identity, retain the full snapshot as audit identity, and report the first incompatible binding field.
- [ ] 4.6 Compose the adapter with the optional common `source_search` bridge, freeze its policy and bridge identity in the snapshot, and reserve the worst-case bounded search transcript before dispatch.

## 5. Add Workspace And Migration

- [ ] 5.1 Return the exact bounded installed runtime catalog grouped by runtime, provider, model, availability, capacity, reasoning controls, protocol, and safe failure.
- [ ] 5.2 Add typed migration preview/apply with derived-field removal, durable idempotency record, compare-and-swap, locking, crash recognition, atomic replacement, and compatible profile editing.
- [ ] 5.3 Show immutable runtime, adapter version, provider, model, permissions, capacity, process identity, normalized failure, and explicit retry lineage in dispatcher inspection.
- [ ] 5.4 Support representability-checked downgrade and reserve the pre-migration backup for documented emergency restoration, not ordinary rollback.

## 6. Verify And Synchronize

- [ ] 6.1 Run exact protocol fixtures and bounded installed-runtime probes for `codex-cli/v1`, `pi-rpc/v1`, and `opencode-server/v1`, including Pi global prompt isolation and correlated retry-disable proof.
- [ ] 6.2 Add integration tests for schema and evidence validation, capacity budgeting, publication authority, cross-run reuse diagnostics, cancellation, PID/start-token mismatch, process loss, and restart recovery across adapters.
- [ ] 6.3 Add security tests proving OpenCode/Pi native tools and external resources stay disabled, Codex retains only its current read-only contour, only the selected bounded search bridge is reachable, global OpenCode resources are not loaded or written, credentials are redacted, and hostile or oversized output is rejected.
- [ ] 6.4 Add backend, frontend, and browser coverage for migration crashes, stale and concurrent apply, idempotency, allowed and blocked downgrade, explicit alternate-profile retry, absence of automatic retry after every terminal failure, catalogs, progress, errors, accessibility, and stale reconciliation.
- [ ] 6.5 Implement target-first, synchronize reusable runtime and scaffold outputs, rebuild packages, and run Python, frontend, browser, template, doctor, and strict OpenSpec checks.
