import type { CircuitLease, DispatcherProjection } from '../src/dispatcher/projection';

const NOW = '2026-07-21T12:00:00.000Z';

const lease = (state: string): CircuitLease => ({
  job_id: 'analyze-dif',
  thread_id: 'thread-fixture',
  work_unit_id: 'DIF-00001',
  owner: 'fixture-agent',
  acquired_at: NOW,
  renewed_at: NOW,
  state,
  summary: { run_id: 'run-fixture', execution_snapshot_fingerprint: 'sha256:fixture' },
});

const circuits: DispatcherProjection['circuits'] = [
  {
    id: 'prepare-diffs',
    state: 'complete',
    aggregates: { diff_count: 12 },
    zones: [
      { id: 'vendor-baseline', state: 'complete', updated_at: NOW },
      { id: 'target-cf', state: 'complete', updated_at: NOW },
      { id: 'next-vendor', state: 'complete', updated_at: NOW },
      { id: 'sources-acquire', state: 'complete', updated_at: NOW },
      { id: 'diffs-build', state: 'complete', count: 12, updated_at: NOW },
      { id: 'indexes-build', state: 'complete', updated_at: NOW },
    ],
  },
  { id: 'analyze-dif', state: 'ready', aggregates: { total: 12, classified: 11, meaning: 6, noise_candidate: 5, remaining: 1, current_window_total: 12, current_window_completed: 11, failed: 1, reusable_completed: 11, window_published: false } },
  {
    id: 'form-mrq',
    state: 'ready',
    aggregates: { retained: 3, new: 2, merged: 1, split: 0, superseded: 1, evidence: 12, coverage: 'complete', approval_pending: 1, published: 0, partition_count: 2, pair_count: 3, planned_invocation_count: 5, input_context_tokens: 200000, context_estimator_version: 'utf8-v1', plan_status: 'approval_required', plan_fingerprint: 'sha256:plan' },
    publication: { id: 'publication', state: 'waiting', updated_at: NOW },
  },
  { id: 'classify-mrq', state: 'complete', aggregates: { input_mrq_count: 6, window_count: 1, batch_count: 5, validation_state: 'valid' } },
  { id: 'decide-target', state: 'blocked', aggregates: { decision_count: 4 } },
];

const dif = (id: number, state: string) => ({
  id: `DIF-${String(id).padStart(5, '0')}`,
  path: `Catalogs/Fixture${id}.xml`,
  kind: 'changed',
  state,
  evidence_count: 2,
});

const invocations = Array.from({ length: 16 }, (_, index) => ({
  invocation_id: `invocation-${String(index + 1).padStart(2, '0')}`,
  slot_id: `analyzer-${(index % 4) + 1}`,
  work_unit_id: `DIF-${String(index + 1).padStart(5, '0')}`,
  status: index < 2 ? 'running' : index < 4 ? 'queued' : index === 13 ? 'cancelled' : index === 14 ? 'failed' : index === 15 ? 'interrupted' : 'completed',
  created_at: NOW,
  updated_at: NOW,
}));

export const emptyProjection: DispatcherProjection = {
  schema_version: '2',
  revision: 1,
  fresh_at: NOW,
  circuits: [
    { id: 'prepare-diffs', state: 'unknown' },
    { id: 'analyze-dif', state: 'unknown' },
    { id: 'form-mrq', state: 'unknown' },
    { id: 'classify-mrq', state: 'unknown' },
    { id: 'decide-target', state: 'unknown' },
  ],
  jobs: {},
  items: { dif_queue: [], meaning_diffs: [], noise_diffs: [], proposals: [], mrq_outcomes: [], mrqs: [], batches: [], decisions: [], approval_count: 0 },
  agent_phases: [],
};

export const saturatedProjection: DispatcherProjection = {
  schema_version: '2',
  revision: 42,
  fresh_at: NOW,
  circuits,
  jobs: { 'analyze-dif': lease('running') },
  queue_aggregates: {
    'dif-queue': { total: 12, visible: 12, omitted: 0 },
    'mrq-queue': { total: 6, visible: 6, omitted: 0 },
  },
  items: {
    dif_queue: Array.from({ length: 12 }, (_, index) => dif(index + 1, 'queued')),
    meaning_diffs: Array.from({ length: 6 }, (_, index) => dif(index + 21, 'meaning')),
    noise_diffs: Array.from({ length: 5 }, (_, index) => dif(index + 31, 'noise')),
    proposals: Array.from({ length: 5 }, (_, index) => ({
      id: `group-${index + 1}`,
      job_id: index === 0 ? 'decide-mrq' : 'consolidate-mrq',
      kind: 'approval',
      approval_stage: index === 0 ? 'decision' as const : '' as const,
      semantic_key: `fixture-${index + 1}`,
      dif_ids: [`DIF-${String(index + 21).padStart(5, '0')}`],
      evidence_count: 2,
      noise_count: 0,
      mrq_id: '',
      created_at: NOW,
    })),
    mrq_outcomes: [{ id: 'mrq:retained:MRQ-00001', title: 'MRQ-00001', state: 'retained', evidence_count: 2, source_ids: ['MRQ-00001'], target_ids: ['MRQ-00001'] }],
    mrqs: Array.from({ length: 6 }, (_, index) => ({
      id: `MRQ-${String(index + 1).padStart(5, '0')}`,
      title: `Требование ${index + 1}`,
      semantic_key: `requirement-${index + 1}`,
      state: index < 4 ? 'approved' : 'pending',
      dif_ids: [`DIF-${String(index + 21).padStart(5, '0')}`],
      evidence_count: 2,
    })),
    batches: Array.from({ length: 5 }, (_, index) => ({
      id: `MRQB-${String(index + 1).padStart(16, '0')}`,
      mrq_ids: [`MRQ-${String(index + 1).padStart(5, '0')}`],
      reason: `fixture-${index + 1}`,
    })),
    decisions: [
      { id: 'MRQ-00001', title: 'Типовой механизм', decision: 'adopt_vendor', target_solution: 'Типовой механизм', evidence_count: 3, gap: false },
      { id: 'MRQ-00002', title: 'Адаптация', decision: 'adapt', target_solution: 'Адаптация', evidence_count: 3, gap: true },
      { id: 'MRQ-00003', title: 'Сохранение', decision: 'retain_custom', target_solution: 'Сохранение', evidence_count: 3, gap: false },
      { id: 'MRQ-00004', title: 'Вне объёма', decision: 'out_of_scope', target_solution: 'Вне объёма', evidence_count: 3, gap: false },
    ],
    approval_count: 1,
  },
  agent_phases: [
    {
      job_id: 'consolidate-mrq',
      phase_id: 'analyze-dif',
      mode: 'parallel-pool',
      max_concurrency: 4,
      roles: [{
        role_id: 'analyzer',
        agent_profile: 'local',
        configured_slots: 4,
        requested: 18,
        running: 2,
        queued: 2,
        completed: 11,
        failed: 1,
        cancelled: 0,
        interrupted: 2,
        invocation_total: 18,
        invocation_omitted: 2,
        model: 'gpt-5.6-sol',
        reasoning_effort: 'low',
        environment_preset: 'local-read-only',
        environment_status: 'ready',
        invocations,
      }],
    },
    {
      job_id: 'analyze-dif',
      phase_id: 'form-mrq',
      mode: 'coordinated-pool',
      max_concurrency: 3,
      roles: [
        { role_id: 'coordinator', agent_profile: 'local', configured_slots: 1, requested: 1, running: 0, queued: 0, completed: 1, failed: 0, cancelled: 0, interrupted: 0, invocation_total: 1, invocation_omitted: 0, invocations: [{ invocation_id: 'coordinator-1', slot_id: 'coordinator-1', work_unit_id: 'DIF-00021', status: 'completed', updated_at: NOW }] },
        { role_id: 'grouper', agent_profile: 'local', configured_slots: 2, requested: 2, running: 1, queued: 1, completed: 0, failed: 0, cancelled: 0, interrupted: 0, invocation_total: 2, invocation_omitted: 0, invocations: [{ invocation_id: 'grouper-1', slot_id: 'grouper-1', work_unit_id: 'DIF-00022', status: 'running', updated_at: NOW }, { invocation_id: 'grouper-2', slot_id: 'grouper-2', work_unit_id: 'DIF-00023', status: 'queued', updated_at: NOW }] },
      ],
    },
    {
      job_id: 'classify-mrq',
      phase_id: 'classify-batches',
      mode: 'sequential',
      max_concurrency: 1,
      roles: [{
        role_id: 'classifier',
        agent_profile: 'local',
        configured_slots: 1,
        requested: 1,
        running: 0,
        queued: 0,
        completed: 1,
        failed: 0,
        cancelled: 0,
        interrupted: 0,
        invocation_total: 1,
        invocation_omitted: 0,
        invocations: [{ invocation_id: 'classifier-1', slot_id: 'classifier-1', work_unit_id: 'window:0', status: 'completed', updated_at: NOW }],
      }],
    },
    {
      job_id: 'decide-mrq',
      phase_id: 'research-target',
      mode: 'parallel-pool',
      max_concurrency: 4,
      roles: [{
        role_id: 'researcher',
        agent_profile: 'local',
        configured_slots: 4,
        requested: 4,
        running: 1,
        queued: 1,
        completed: 1,
        failed: 1,
        cancelled: 0,
        interrupted: 0,
        invocation_total: 4,
        invocation_omitted: 0,
        invocations: [
          { invocation_id: 'researcher-1', slot_id: 'researcher-1', work_unit_id: 'MRQ-00001', status: 'running', updated_at: NOW },
          { invocation_id: 'researcher-2', slot_id: 'researcher-2', work_unit_id: 'MRQ-00002', status: 'queued', updated_at: NOW },
          { invocation_id: 'researcher-3', slot_id: 'researcher-3', work_unit_id: 'MRQ-00003', status: 'completed', updated_at: NOW },
          { invocation_id: 'researcher-4', slot_id: 'researcher-4', work_unit_id: 'MRQ-00004', status: 'failed', updated_at: NOW },
        ],
      }],
    },
  ],
};

const variant = (mutate: (projection: DispatcherProjection) => void) => {
  const projection = structuredClone(saturatedProjection);
  mutate(projection);
  return projection;
};

export const activeProjection = variant((projection) => {
  projection.items.dif_queue = projection.items.dif_queue.slice(0, 2);
  projection.items.meaning_diffs = [];
  projection.items.noise_diffs = [];
  projection.items.proposals = [];
  projection.items.mrqs = [];
  projection.items.batches = [];
  projection.items.decisions = [];
  projection.agent_phases = projection.agent_phases?.slice(0, 1);
  const analyzer = projection.agent_phases?.[0].roles[0];
  if (analyzer) {
    analyzer.invocations = analyzer.invocations.slice(0, 1);
    analyzer.requested = analyzer.running = 1;
    analyzer.queued = analyzer.completed = analyzer.failed = analyzer.cancelled = analyzer.interrupted = 0;
  }
});
const setLease = (projection: DispatcherProjection, state: string) => {
  const current = lease(state);
  projection.jobs['analyze-dif'] = current;
  projection.circuits[1].leases = [current];
};

export const approvalProjection = variant((projection) => setLease(projection, 'blocked'));
export const stoppedProjection = variant((projection) => setLease(projection, 'resumable'));
export const errorProjection = variant((projection) => {
  setLease(projection, 'failed');
  projection.circuits[0].zones!.find((zone) => zone.id === 'diffs-build')!.state = 'error';
});
export const staleProjection = variant((projection) => {
  const current = { ...lease('running'), renewed_at: '2026-07-21T10:00:00.000Z' };
  projection.jobs['analyze-dif'] = current;
  projection.circuits[1].leases = [current];
});

export const dispatcherFixtures = {
  empty: emptyProjection,
  saturated: saturatedProjection,
  active: activeProjection,
  approval: approvalProjection,
  stopped: stoppedProjection,
  error: errorProjection,
  stale: staleProjection,
} as const;
