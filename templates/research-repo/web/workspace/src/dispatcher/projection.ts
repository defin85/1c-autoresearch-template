// Типы серверной операционной проекции диспетчера.
//
// Проекция присоединяется к снимку под ключём ``dispatcher`` и НЕ входит в
// канонические отпечатки.

export type CircuitId = 'prepare-diffs' | 'analyze-dif' | 'form-mrq' | 'classify-mrq' | 'decide-target';

export interface CircuitLease {
  job_id: string;
  thread_id: string;
  work_unit_id: string;
  owner: string;
  acquired_at: string;
  renewed_at: string;
  state: string;
  summary: Record<string, unknown>;
}

export interface CircuitProjection {
  id: CircuitId;
  state: 'complete' | 'ready' | 'blocked' | 'unknown';
  leases?: CircuitLease[];
  aggregates?: DispatcherAggregates;
  zones?: DispatcherZoneProjection[];
  publication?: DispatcherZoneProjection;
}

export interface DispatcherAggregates extends Record<string, number | string | boolean | undefined> {
  total?: number;
  classified?: number;
  meaning?: number;
  noise_candidate?: number;
  remaining?: number;
  current_window_total?: number;
  current_window_completed?: number;
  failed?: number;
  reusable_completed?: number;
  window_published?: boolean;
  retained?: number;
  new?: number;
  merged?: number;
  split?: number;
  superseded?: number;
  approval_pending?: number;
  published?: number;
  coverage?: number | string;
  partition_count?: number;
  pair_count?: number;
  planned_invocation_count?: number;
  uncovered_partition_count?: number;
  plan_status?: string;
  plan_fingerprint?: string;
  blocker_code?: string;
  blocker_message?: string;
  input_context_tokens?: number;
  context_estimator_version?: string;
  already_applied?: boolean;
  legacy_decisions_stale?: number;
}

export interface DispatcherZoneProjection {
  id: string;
  state: 'unknown' | 'waiting' | 'active' | 'error' | 'complete';
  count?: number;
  updated_at?: string;
}

export interface DispatcherProjection {
  schema_version: string;
  revision: number;
  fresh_at: string;
  circuits: CircuitProjection[];
  jobs: Record<string, CircuitLease>;
  stage_recompute_run?: { run_id: string; status: string; result?: Record<string, unknown> | null; plan?: { plan_fingerprint?: string } };
  items: DispatcherItems;
  agent_phases?: AgentPhaseProjection[];
  queue_aggregates?: Record<string, {
    total: number;
    visible?: number;
    omitted: number;
  }>;
  retry_candidates?: Array<{
    run_id: string;
    job_id: 'analyze-dif' | 'consolidate-mrq' | 'classify-mrq' | 'decide-mrq';
    status: string;
    execution_snapshot_fingerprint: string;
    policy_source: 'reuse-snapshot' | 'current-policy';
    workflow_fingerprint: string;
    input_fingerprints: Record<string, string>;
  }>;
  error?: string;
}
export interface AgentInvocationProjection {
  invocation_id: string; slot_id: string; work_unit_id: string; status: string; created_at?: string; updated_at?: string;
}
export interface AgentSlotProjection {
  slot_id: string;
  display_label: string;
  state: 'running' | 'idle';
  idle_reason_code?: string | null;
  current_invocation_id?: string | null;
}
export interface AgentRoleProjection {
  role_id: string; agent_profile: string; configured_slots: number; requested: number;
  running: number; queued: number; completed: number; failed: number; cancelled: number; interrupted: number;
  invocations: AgentInvocationProjection[];
  run_id?: string;
  slots?: AgentSlotProjection[];
  invocation_total?: number; invocation_omitted?: number;
  model?: string; reasoning_effort?: string; environment_preset?: string; environment_status?: string;
}

export type DispatcherSelection =
  | { kind: 'circuit'; circuitId: CircuitId }
  | { kind: 'role'; circuitId: CircuitId; phaseId: string; roleId: string }
  | { kind: 'slot'; circuitId: CircuitId; phaseId: string; roleId: string; slotId: string; runId?: string }
  | { kind: 'invocation'; circuitId: CircuitId; phaseId: string; roleId: string; slotId: string; invocationId: string; runId?: string }
  | { kind: 'queue'; circuitId: CircuitId; queueId: string }
  | { kind: 'item'; itemKind: 'dif' | 'mrq' | 'outcome' | 'batch' | 'decision' | 'proposal'; circuitId: CircuitId; queueId: string; itemId: string };
export interface AgentPhaseProjection {
  job_id: string; phase_id: string; mode: string; max_concurrency: number; roles: AgentRoleProjection[];
}

export interface DispatcherItems {
  dif_queue: DifCard[];
  meaning_diffs: DifCard[];
  unassigned_meaning_diffs?: DifCard[];
  noise_diffs: DifCard[];
  proposals: ProposalCard[];
  mrq_outcomes: CollectionCard[];
  mrqs: MrqCard[];
  all_mrqs?: MrqCard[];
  batches: BatchCard[];
  decisions: DecisionCard[];
  approval_count: number;
}

export interface DifCard {
  id: string;
  path: string;
  kind: string;
  state: string;
  intervention_kind?: string;
  object_scope?: string;
  target_coverage?: string;
  extension_uuid?: string;
  component_id?: string;
  affected_base_identity?: string;
  evidence_count?: number;
  dependency_count?: number;
  compatibility_summary?: Record<string, number>;
  blocker_codes?: string[];
}
export interface ProposalCard { id: string; job_id: string; kind: string; approval_stage?: 'consolidation' | 'decision' | ''; semantic_key: string; dif_ids: string[]; evidence_count: number; noise_count: number; mrq_id: string; created_at: string }
export interface MrqCard { id: string; title: string; semantic_key: string; state: string; dif_ids: string[]; evidence_count: number }
export interface BatchCard { id: string; mrq_ids: string[]; reason: string }
export interface DecisionCard { id: string; title: string; decision: string; target_solution: string; evidence_count: number; gap: boolean }
export interface CollectionCard { id: string; title: string; state: string; evidence_count: number; source_ids?: string[]; target_ids?: string[] }

export const EMPTY_ITEMS: DispatcherItems = { dif_queue: [], meaning_diffs: [], unassigned_meaning_diffs: [], noise_diffs: [], proposals: [], mrq_outcomes: [], mrqs: [], all_mrqs: [], batches: [], decisions: [], approval_count: 0 };

export const EMPTY_PROJECTION: DispatcherProjection = {
  schema_version: '2',
  revision: 0,
  fresh_at: '',
  circuits: [],
  jobs: {},
  items: EMPTY_ITEMS,
  agent_phases: [],
};

export const VISIBLE_QUEUE_LIMIT = 4;
export const VISIBLE_DECISION_LIMIT = 3;
export const VISIBLE_INVOCATION_LIMIT = 16;

export function visibleWindow<T>(items: readonly T[], limit: number): T[] {
  return items.slice(0, limit);
}

export function roleProgress(role: AgentRoleProjection): { valid: boolean; percent?: number; processed: number; freeCapacity: number } {
  const processed = role.completed + role.failed + role.cancelled + role.interrupted;
  const partition = role.running + role.queued + processed;
  return {
    valid: partition === role.requested,
    percent: role.requested > 0 && partition === role.requested ? Math.floor(100 * processed / role.requested) : undefined,
    processed,
    freeCapacity: Math.max(role.configured_slots - role.running, 0),
  };
}

export function circuitById(projection: DispatcherProjection | undefined | null, id: CircuitId): CircuitProjection | undefined {
  if (!projection) return undefined;
  return projection.circuits.find((circuit) => circuit.id === id);
}

export function isFresh(renewedAt: string, now: number): boolean {
  if (!renewedAt) return false;
  const parsed = Date.parse(renewedAt);
  if (Number.isNaN(parsed)) return false;
  // порог 10 секунд без серверного подтверждения (spec.md)
  return now - parsed < 10_000;
}

export function freshnessLabel(projection: DispatcherProjection | undefined | null): string {
  if (!projection) return 'без операционного состояния';
  if (projection.error) return 'операционное состояние недоступно';
  if (!projection.fresh_at) return 'без операционного состояния';
  const activeLeases = Object.values(projection.jobs);
  if (activeLeases.length === 0) return `подтверждено ${new Date(projection.fresh_at).toLocaleTimeString('ru-RU')}`;
  return activeLeases.every((lease) => isFresh(lease.renewed_at, Date.now())) ? 'данные актуальны' : 'данные несвежие';
}
