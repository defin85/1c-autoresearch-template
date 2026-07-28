## 1. Operational Invocation Contract

- [x] 1.1 Change every invocation insert to name its target columns explicitly before adding nullable execution-snapshot fingerprint, profile identifier, context-manifest fingerprint, terminal time, error code, error summary, and opaque result-reference columns.
- [x] 1.2 Add an idempotent additive SQLite migration for the invocation columns, the lifecycle outbox, unique transition keys, and the `(run_id, phase_id, role_id, slot_id, created_at DESC, invocation_id DESC)` history index.
- [x] 1.3 Document and test owner-only WAL-consistent backup through the SQLite online backup API, repeated migration, legacy database opening, frontend-only rollback, and backend rollback by backup restoration with loss of post-upgrade operational history.
- [x] 1.4 Build and persist the context manifest before starting an invocation, then atomically record the immutable execution-snapshot, profile, and context-manifest identity with the running invocation.
- [x] 1.5 Project every configured logical slot with the existing canonical `<phase_id>:<role_id>:<ordinal>` identifier, optional active/latest run identifier, current invocation reference when assigned, and a server-owned idle-reason code when unassigned; preserve legacy history joins and explicit unavailable markers.
- [x] 1.6 Implement one idempotent terminalization path for completed, failed, cancelled, and interrupted outcomes, including user cancellation, soft stop, branch cancellation, `InterruptedError`, lease loss, process mismatch, orphan recovery, and service restart.
- [x] 1.7 Atomically enqueue `invocation.started` and `invocation.finished` transitions with `invocation_status`, deliver them to the existing JSONL event store without overwriting the enclosing run status, and reconcile committed but undelivered transitions after a crash without duplicating lifecycle state.
- [x] 1.8 Derive an opaque deterministic node-result reference from the existing node-result identity for every invocation kind, publish only an allowlisted redacted summary, and report missing, not-yet-published, or orphaned results as unavailable without accepting filesystem paths.

## 2. Read-Only Inspection API

- [x] 2.1 Add project-scoped store lookups for invocation identity, phase/role/canonical-slot selectors with optional run scope, and same-run same-slot history using opaque keyset cursors ordered by `(created_at DESC, invocation_id DESC)`.
- [x] 2.2 Add the single dispatcher inspection route with mutually exclusive invocation and slot selectors, allow never-run configured slots without `run_id`, and enforce default/maximum history limits of 20/100 and event limits of 50/200.
- [x] 2.3 Return independently paginated correlated events in `sequence ASC` order from the run-scoped retained snapshot, using an opaque `{project_id,run_id,invocation_id,last_sequence}` cursor with exclusive sequence semantics, explicit availability and truncation markers, and no substitution from current policy or another snapshot.
- [x] 2.4 Enforce cursor scope, project confinement, identical not-found behavior for unknown and foreign invocations, `422` for invalid selectors or cursors, `503` for an unavailable operational store, allowlisted error codes of at most 64 ASCII characters, and redacted UTF-8 summaries of at most 4096 bytes.
- [x] 2.5 Extend the existing registry read with an optional exact stable item-identifier filter so inspector navigation cannot land on an unrelated DIF or MRQ record.
- [x] 2.6 Test active, completed, failed, cancelled, interrupted, legacy, idle-slot, never-run idle-slot, foreign-project, missing, invalid-cursor, unavailable-snapshot, missing-result, truncated-history, and truncated-event responses, including canonical legacy slot joins, duplicate timestamps, concurrent history insertion, history-cursor cross-slot reuse, event-cursor cross-invocation reuse, and identical invocation identifiers in different project stores.

## 3. Contextual Inspector

- [x] 3.1 Replace the string initiator key with a closed selection union for circuit, role, slot, invocation, queue, DIF item, and MRQ item; include parent circuit identity in every child variant and keep the initiating element reference separately.
- [x] 3.2 Refactor the existing dispatcher panel into one complementary contextual inspector with one heading, existing circuit actions, entity-specific sections, and a preserved last-resolved view for stale or failed detail reads.
- [x] 3.3 Render role and server-projected slot views, including every configured idle or assigned slot, stable identity, configuration, counters, assignment, translated idle reason, history, and omission markers.
- [x] 3.4 Add keyed invocation-detail loading with request cancellation or sequence guards, bounded history/event pagination, correlated-event refresh, project-change cleanup, safe text rendering, and unavailable result/snapshot states.
- [x] 3.5 Add queue aggregate and independently activatable item views without duplicating registry state; wire exact DIF/MRQ registry targets and invocation/run journal filters through typed workspace navigation callbacks.

## 4. Interaction and Reconciliation

- [x] 4.1 Render role, slot, invocation, queue, DIF, and MRQ activators as sibling native controls rather than nested controls; verify mouse, Enter, and Space each produce exactly one selection callback and retain circuit-node behavior.
- [x] 4.2 Move focus to the inspector heading only when selection changes, preserve focus during content refresh, and restore it on close to the exact still-mounted initiator or its parent graph/circuit control.
- [x] 4.3 Preserve selection, last-resolved content, inspector scroll and pagination, graph viewport, keyboard target, and unrelated form state across incremental reconciliation; never auto-select a replacement for a stale entity.
- [x] 4.4 Reuse the existing event-stream hook with one single-flight five-second fallback while SSE is degraded and one thirty-second reconciliation while connected and work is active; refresh selected invocation detail by invocation identity and selected slot detail by phase/role/slot plus the selected run when present, adopting the first matching run for a never-run slot, and never refresh for unrelated events.
- [x] 4.5 Support `Escape`, reduced motion, bounded live announcements, literal rendering of markup-like provider text, and disabled entity mutations while stale without disabling parent-circuit actions.

## 5. Verification and Template Delivery

- [x] 5.1 Add backend migration, store, outbox, recovery, route, pagination, redaction, project-confinement, registry-filter, and lifecycle-path tests, including duplicate delivery, crash-window recovery, and two concurrent invocations proving that the first terminal event preserves running run status until `run.finished`.
- [x] 5.2 Add frontend model, request-race, reconciliation, and component tests for every selection variant, every circuit identity, pre-run roles and slots, idle-slot-to-running updates, active/idle/failed/legacy/stale states, A-to-B response races, unrelated events, project switching, literal markup, exact navigation, and focus fallback.
- [x] 5.3 Add browser acceptance for circuit to role to slot to invocation navigation, queue aggregate and item navigation, journal and registry filters, history/event pagination, stale selection, SSE loss and recovery, and keyboard/focus behavior without requiring selection persistence across a full page reload.
- [x] 5.4 Implement and verify the behavior in a concrete generated repository first, then sync only customer-independent runtime, tests, static assets, and scaffold changes into the template.
- [x] 5.5 Run frontend tests, type checking, production build, targeted and full Python tests, generated-repository checks, template checks, strict deep doctor, and strict OpenSpec validation.
- [x] 5.6 Verify the packaged template contains no customer data, credentials, operational database, invocation logs, generated results, unrestricted provider output, or private model reasoning.
