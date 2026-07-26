import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { DispatcherCanvasErrorBoundary, DispatcherPanel, leaseActionMatrix, PipelineDispatcher } from './PipelineDispatcher';
import type { DispatcherProjection } from './projection';
import { approvalProjection, errorProjection, saturatedProjection, staleProjection, stoppedProjection } from '../../e2e/dispatcher.fixtures';

const snapshotWithDispatcher = {
  workflow_fingerprint: 'sha256:abc',
  dispatcher: {
    schema_version: '1',
    revision: 7,
    fresh_at: new Date(Date.now() - 1_000).toISOString(),
    circuits: [
      { id: 'prepare-diffs', state: 'complete', aggregates: { diff_count: 145 } },
      { id: 'analyze-dif', state: 'ready', leases: [], aggregates: { customer_diff_count: 75, window_size: 32 } },
      { id: 'form-mrq', state: 'blocked', aggregates: { active_mrq_count: 4, target_batch_count: 4 } },
      { id: 'classify-mrq', state: 'ready', leases: [], aggregates: { active_mrq_count: 4, target_batch_count: 1 } },
      { id: 'decide-target', state: 'unknown', aggregates: { decision_count: 0 } },
    ],
    jobs: {},
    agent_phases: [
      {
        job_id: 'discover-mrq',
        phase_id: 'analyze-dif',
        mode: 'parallel-pool',
        max_concurrency: 4,
        roles: [{
          role_id: 'analyzer',
          agent_profile: 'local',
          configured_slots: 4,
          requested: 2,
          running: 1,
          queued: 1,
          completed: 0,
          failed: 0,
          cancelled: 0,
          interrupted: 0,
          model: 'gpt-5.6-sol',
          reasoning_effort: 'low',
          environment_preset: 'local-read-only',
          environment_status: 'ready',
          invocations: [
            { invocation_id: 'i-1', slot_id: 'analyzer-1', work_unit_id: 'DIF-001', status: 'running' },
            { invocation_id: 'i-2', slot_id: 'analyzer-2', work_unit_id: 'DIF-004', status: 'queued' },
          ],
        }],
      },
      { job_id: 'discover-mrq', phase_id: 'form-mrq', mode: 'coordinated-pool', max_concurrency: 2, roles: [
        { role_id: 'coordinator', agent_profile: 'local', configured_slots: 1, requested: 1, running: 0, queued: 0, completed: 1, failed: 0, cancelled: 0, interrupted: 0, invocations: [] },
        { role_id: 'grouper', agent_profile: 'local', configured_slots: 2, requested: 2, running: 0, queued: 0, completed: 2, failed: 0, cancelled: 0, interrupted: 0, invocations: [] },
      ] },
      { job_id: 'classify-mrq', phase_id: 'classify-mrq', mode: 'sequential', max_concurrency: 1, roles: [
        { role_id: 'classifier', agent_profile: 'local', configured_slots: 1, requested: 0, running: 0, queued: 0, completed: 0, failed: 0, cancelled: 0, interrupted: 0, invocations: [] },
      ] },
      { job_id: 'decide-mrq', phase_id: 'research-target', mode: 'parallel-pool', max_concurrency: 3, roles: [
        { role_id: 'researcher', agent_profile: 'local', configured_slots: 3, requested: 1, running: 0, queued: 1, completed: 0, failed: 0, cancelled: 0, interrupted: 0, invocations: [] },
      ] },
    ],
    items: {
      dif_queue: [{ id: 'DIF-001', path: 'Catalogs/Test.xml', kind: 'extension_intervention', state: 'queued', intervention_kind: 'method_interception', object_scope: 'adopted', target_coverage: 'needs_semantic_review', extension_uuid: '471acdde-293c-497c-bd55-e6ab48d98dc4', component_id: 'target_cf:extension:471acdde-293c-497c-bd55-e6ab48d98dc4', affected_base_identity: 'catalog.products', evidence_count: 2, dependency_count: 1, compatibility_summary: { unresolved: 1 }, blocker_codes: ['unresolved_dependency'] }],
      meaning_diffs: [{ id: 'DIF-002', path: 'Documents/Test.xml', kind: 'changed', state: 'meaning' }],
      noise_diffs: [{ id: 'DIF-003', path: 'Forms/Test.xml', kind: 'changed', state: 'noise' }],
      proposals: [{ id: 'group-1', job_id: 'discover-mrq', kind: 'group.ready', semantic_key: 'orders', dif_ids: ['DIF-002'], evidence_count: 2, noise_count: 0, mrq_id: '', created_at: '2026-07-21T12:00:00Z' }],
      mrqs: [{ id: 'MRQ-014', title: 'Согласование заказа', semantic_key: 'orders', state: 'approved', dif_ids: ['DIF-002'], evidence_count: 2 }],
      batches: [{ id: 'MRQB-1234567890ABCDEF', mrq_ids: ['MRQ-014'], reason: 'orders' }],
      decisions: [{ id: 'MRQ-014', title: 'Согласование заказа', decision: 'adapt', target_solution: 'Адаптировать', evidence_count: 4, gap: true }],
      approval_count: 1,
    },
  },
};

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.endsWith('/workflow')) {
        return Promise.resolve({ ok: true, json: async () => snapshotWithDispatcher });
      }
      return Promise.resolve({ ok: true, json: async () => ({ events: [], next_cursor: 0, resync_required: false }) });
    }),
  );
  // EventSource не доступен в jsdom; заглушка, чтобы хук не падал
  vi.stubGlobal('EventSource', class {
    addEventListener() {}
    close() {}
  });
});

afterEach(cleanup);

test('PipelineDispatcher renders five circuits and freshness label', async () => {
  render(<PipelineDispatcher projectId="proj-1" />);
  expect((await screen.findAllByText('Подготовка')).length).toBeGreaterThan(0);
  for (const title of ['Подготовка различий', 'Анализ DIF', 'Формирование MRQ', 'Формирование пакетов', 'Исследование цели']) {
    expect((await screen.findAllByText(title)).length).toBeGreaterThan(0);
  }
  expect((await screen.findAllByText('DIF-002')).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/MRQ-014/)).length).toBeGreaterThan(0);
  expect(document.querySelector('[data-zone="decide-outcomes"]')).not.toBeNull();
  expect(screen.getByText('Легенда')).toBeInTheDocument();
  expect(screen.getByTestId('dispatcher-new-canvas')).toBeInTheDocument();
  expect(document.querySelectorAll('[data-stage-state="active"]')).toHaveLength(1);
  expect(document.querySelectorAll('[data-stage-state="future"]')).toHaveLength(3);
});

test('local canvas error keeps the surrounding shell mounted', () => {
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
  const BrokenCanvas = () => { throw new Error('graph failed'); };
  render(<>
    <div>Оболочка диспетчера</div>
    <DispatcherCanvasErrorBoundary><BrokenCanvas /></DispatcherCanvasErrorBoundary>
    <div>Нижняя сводка</div>
  </>);
  expect(screen.getByText('Оболочка диспетчера')).toBeInTheDocument();
  expect(screen.getByText('Холст диспетчера недоступен: graph failed')).toBeInTheDocument();
  expect(screen.getByText('Нижняя сводка')).toBeInTheDocument();
  consoleError.mockRestore();
});

test('dispatcher stages have keyboard-focusable text controls', async () => {
  render(<PipelineDispatcher projectId="proj-1" />);
  await screen.findByRole('button', { name: 'Анализ DIF' });
  expect(within(screen.getByRole('navigation', { name: 'Этапы диспетчера' })).getAllByRole('button').map((button) => button.textContent)).toEqual([
    'Подготовка различий',
    'Анализ DIF',
    'Формирование MRQ',
    'Формирование пакетов',
    'Исследование цели',
  ]);
  const node = document.querySelector<HTMLElement>('.react-flow__node[data-id="analysis"]');
  expect(node).not.toBeNull();
  expect(node).toHaveAttribute('tabindex', '0');
  fireEvent.keyDown(node!, { key: 'Enter' });
  expect(await screen.findByText('Текущее задание')).toBeInTheDocument();
  fireEvent.keyDown(document.querySelector<HTMLElement>('.react-flow__node[data-id="analysis"]')!, { key: 'Escape' });
  await waitFor(() => expect(screen.queryByText('Текущее задание')).not.toBeInTheDocument());
  const currentNode = document.querySelector<HTMLElement>('.react-flow__node[data-id="analysis"]')!;
  fireEvent.keyDown(currentNode, { key: ' ' });
  expect(await screen.findByText('Текущее задание')).toBeInTheDocument();
});

test('dispatcher new uses the shared action callback and restores focus', async () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockImplementation((url) => Promise.resolve({
    ok: true,
    json: async () => String(url).endsWith('/dispatcher/discover-mrq/start')
      ? { outcome: { status: 'running', revision: 8, summary: {} } }
      : { events: [], next_cursor: 0, resync_required: false },
  } as Response));
  render(<PipelineDispatcher projectId="proj-1" initialProjection={projection} initialFingerprint="sha256:fixture" />);
  const node = document.querySelector<HTMLElement>('.react-flow__node[data-id="analyzer-1"]');
  expect(node).not.toBeNull();
  expect(node).toHaveAttribute('tabindex', '0');
  fireEvent.keyDown(node!, { key: 'Enter' });
  expect(await screen.findByText('Текущее задание')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Запустить' }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/dispatcher/discover-mrq/start'))).toHaveLength(1));
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/projects/proj-1/dispatcher/discover-mrq/start',
    expect.objectContaining({ method: 'POST', body: JSON.stringify({ actor: 'local-user' }) }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
  await waitFor(() => expect(document.querySelector<HTMLElement>('.react-flow__node[data-id="analyzer-1"]')).toHaveFocus());
});

test.each([
  ['остановленное', stoppedProjection, 'Остановлен'],
  ['ошибочное', errorProjection, 'Ошибка'],
  ['ожидающее одобрения', approvalProjection, 'Ожидает одобрения'],
] as const)('%s состояние аренды показано текстом', (_name, projection, label) => {
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:fixture" selectedCircuit="analyze-dif" onClose={() => {}} />);
  expect(screen.getByText(new RegExp(`Аренда: fixture-agent \\(${label}\\)`))).toBeInTheDocument();
});

test('context settings action passes the selected workflow step', () => {
  const onOpenSettings = vi.fn();
  render(<DispatcherPanel projectId="proj-1" projection={snapshotWithDispatcher.dispatcher as unknown as DispatcherProjection} fingerprint="sha256:fixture" selectedCircuit="classify-mrq" onClose={() => {}} onOpenSettings={onOpenSettings} />);
  fireEvent.click(screen.getByRole('button', { name: 'Профили и параметры' }));
  expect(onOpenSettings).toHaveBeenCalledWith('classify-mrq');
});

test('PipelineDispatcher shows real invocation states independently of a soft-stopped lease', async () => {
  const lease = { job_id: 'discover-mrq', thread_id: 'thread-1', work_unit_id: 'DIF-001', owner: 'agent-1', acquired_at: new Date().toISOString(), renewed_at: new Date().toISOString(), state: 'resumable', summary: { execution_snapshot_fingerprint: 'sha256:snapshot' } };
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.jobs = { 'discover-mrq': lease };
  projection.circuits[1].leases = [lease];
  render(<PipelineDispatcher projectId="proj-1" initialProjection={projection} initialFingerprint="sha256:abc" />);
  expect(await screen.findByText('Выполняется')).toBeInTheDocument();
  expect(screen.queryByText('Анализирует')).not.toBeInTheDocument();
});

test.each([
  ['absent', undefined, ['start']],
  ['running', 'running', ['stop', 'cancel']],
  ['resumable', 'resumable', ['resume', 'retry', 'cancel']],
  ['blocked', 'blocked', ['cancel', 'approve']],
  ['failed', 'failed', ['retry', 'cancel']],
  ['stale', 'stale', ['retry', 'cancel']],
  ['expired', 'running', ['retry', 'cancel']],
] as const)('lease action matrix: %s', (name, state, enabled) => {
  const now = Date.now();
  const lease = state ? {
    owner: 'agent',
    state,
    work_unit_id: 'DIF-001',
    renewed_at: new Date(name === 'expired' ? now - 31_000 : now).toISOString(),
  } : undefined;
  const matrix = leaseActionMatrix(lease, true, false, true, now);
  expect(Object.entries(matrix).filter(([, value]) => value).map(([key]) => key)).toEqual(enabled);
});

test.each([
  ['Запустить', undefined, 'start', { actor: 'local-user' }],
  ['Мягкая остановка', 'running', 'stop', {}],
  ['Продолжить', 'resumable', 'resume', { actor: 'local-user' }],
  ['Отменить задание', 'running', 'cancel', { actor: 'local-user' }],
] as const)('%s выполняет ровно один прежний POST', async (button, state, action, body) => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.circuits[1].state = 'ready';
  if (state) {
    const lease = {
      job_id: 'discover-mrq', thread_id: 'thread-1', work_unit_id: 'DIF-001', owner: 'agent-1',
      acquired_at: new Date().toISOString(), renewed_at: new Date().toISOString(), state,
      summary: { execution_snapshot_fingerprint: 'sha256:snapshot' },
    };
    projection.jobs = { 'discover-mrq': lease };
    projection.circuits[1].leases = [lease];
  } else {
    projection.jobs = {};
    projection.circuits[1].leases = [];
  }
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ outcome: { status: 'running', revision: 8, summary: {} } }) } as Response);
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selectedCircuit="analyze-dif" onClose={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: button }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(fetchMock).toHaveBeenCalledWith(
    `/api/v1/projects/proj-1/dispatcher/discover-mrq/${action}`,
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify(body),
    }),
  );
  expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get('Idempotency-Key')).toEqual(expect.any(String));
});

test('read-only panel blocks mutations while a resync snapshot is unavailable', () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.circuits[1].state = 'ready';
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selectedCircuit="analyze-dif" onClose={() => {}} readOnly />);
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Пересчитать с этапа' })).toBeDisabled();
});

test.each([
  ['пакет MRQ', 'discover-mrq', 'form-mrq', 'batch', 'approve-batch'],
  ['целевое решение', 'decide-mrq', 'decide-target', 'decision', 'approve-decision'],
] as const)('одобрение: %s выполняет ровно один типизированный POST', async (_name, job, circuit, stage, action) => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.jobs = {
    [job]: {
      job_id: job, thread_id: 'thread-1', work_unit_id: 'work-1', owner: 'agent-1',
      acquired_at: new Date().toISOString(), renewed_at: new Date().toISOString(), state: 'blocked', summary: {},
    },
  };
  projection.items.proposals = [{
    id: `${stage}-proposal`, job_id: job, kind: 'approval', approval_stage: stage === 'batch' ? 'batch' : '',
    semantic_key: '', dif_ids: [], evidence_count: 1, noise_count: 0, mrq_id: '', created_at: new Date().toISOString(),
  }];
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ outcome: { status: 'complete', revision: 8, summary: {} } }) } as Response);
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selectedCircuit={circuit} onClose={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Одобрить предложение' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(fetchMock).toHaveBeenCalledWith(
    `/api/v1/projects/proj-1/dispatcher/${job}/${action}`,
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ actor: 'local-user', proposal_key: `${stage}-proposal`, expected_fingerprint: 'sha256:workflow' }),
    }),
  );
  expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get('Idempotency-Key')).toEqual(expect.any(String));
});

test('panel separates current task and recompute preview before run', async () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.circuits[1].state = 'complete';
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/stage-recompute/preview')) {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          boundary: 'diffs',
          plan_fingerprint: 'sha256:plan',
          steps: [{ step_id: '1:diff.build', operation: 'diff.build' }],
          required_confirmations: ['confirm_recompute'],
          possible_result: 'unchanged',
          stop_before: 'mrq.discover-next',
          generations: { source: 'src-1', diff: 'diff-1' },
        }),
      } as Response);
    }
    if (url.endsWith('/stage-recompute/runs')) {
      return Promise.resolve({ ok: true, json: async () => ({ run_id: 'run-1', status: 'running', plan_fingerprint: 'sha256:plan' }) } as Response);
    }
    return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
  });
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selectedCircuit="analyze-dif" onClose={() => {}} />);
  expect(screen.getByRole('region', { name: 'Текущее задание' })).toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'Пересчёт этапа' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Пересчитать с этапа' }));
  expect(await screen.findByLabelText('Предварительный просмотр пересчёта')).toHaveTextContent('diff.build');
  expect(screen.getByRole('button', { name: 'Запустить пересчёт' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Подтверждаю: confirm_recompute' }));
  fireEvent.click(screen.getByRole('button', { name: 'Запустить пересчёт' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/projects/proj-1/stage-recompute/preview',
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ boundary: 'diffs', expected_workflow_fingerprint: 'sha256:workflow' }),
    }),
  ));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/projects/proj-1/stage-recompute/runs',
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        boundary: 'diffs',
        workflow_fingerprint: 'sha256:workflow',
        plan_fingerprint: 'sha256:plan',
        confirmations: ['confirm_recompute'],
      }),
    }),
  ));
  expect(fetchMock.mock.calls.map(([, init]) => new Headers(init?.headers).get('Idempotency-Key')))
    .toEqual([expect.any(String), expect.any(String)]);
});

test('active recompute disables normal start and has a separate cancel', () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.jobs['stage-recompute'] = {
    job_id: 'stage-recompute',
    thread_id: 'thread-recompute',
    work_unit_id: 'diffs',
    owner: 'system',
    acquired_at: new Date().toISOString(),
    renewed_at: new Date().toISOString(),
    state: 'running',
    summary: { run_id: 'run-1', current_step: 'diff.build' },
  };
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selectedCircuit="analyze-dif" onClose={() => {}} />);
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Отменить пересчёт' })).toBeEnabled();
  expect(screen.getByText(/Выполняется шаг: diff.build/)).toBeInTheDocument();
  expect(screen.getByText(/временно недоступны/)).toBeInTheDocument();
});

test('MRQ panel guides to explicit actions without reset', () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selectedCircuit="form-mrq" onClose={() => {}} />);
  expect(screen.getByText(/массовый сброс не поддерживается/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Пересчитать с этапа' })).not.toBeInTheDocument();
});

test('explicit retry sends policy, predecessor and expected fingerprints', async () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  const lease = {
    job_id: 'discover-mrq',
    thread_id: 'thread-1',
    work_unit_id: 'DIF-001',
    owner: 'agent-1',
    acquired_at: new Date().toISOString(),
    renewed_at: new Date().toISOString(),
    state: 'failed',
    summary: {
      run_id: 'run-old',
      bindings: {
        source_generation_id: 'source-1',
        diff_generation_id: 'diff-1',
        canonical_generation_id: 'canonical-1',
        work_unit_id: 'DIF-001',
      },
    },
  };
  projection.jobs = { 'discover-mrq': lease };
  projection.circuits[1].leases = [lease];
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValue({
    ok: true,
    json: async () => ({ outcome: { status: 'running', run_id: 'run-new', thread_id: 'thread-new', revision: 8, summary: {} } }),
  } as Response);
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selectedCircuit="analyze-dif" onClose={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Явный повтор' }));
  expect(screen.getByRole('dialog', { name: 'Явный повтор задания' })).toBeInTheDocument();
  fireEvent.mouseDown(screen.getByLabelText('Источник политики'));
  fireEvent.click(screen.getByRole('option', { name: 'Текущая политика' }));
  fireEvent.click(screen.getByRole('button', { name: 'Запустить повтор' }));
  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/dispatcher/discover-mrq/retry'));
    expect(call).toBeDefined();
    expect(JSON.parse(String(call![1]?.body))).toMatchObject({
      policy_source: 'current-policy',
      predecessor_run_id: 'run-old',
      expected_workflow_fingerprint: 'sha256:workflow',
      expected_input_fingerprints: {
        source_generation_id: 'source-1',
        diff_generation_id: 'diff-1',
        canonical_generation_id: 'canonical-1',
        work_unit_id: 'DIF-001',
      },
    });
    expect(new Headers(call![1]?.headers).get('Idempotency-Key')).toEqual(expect.any(String));
  });
});

test('resume explains immutable execution snapshot', () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  const lease = {
    job_id: 'discover-mrq',
    thread_id: 'thread-1',
    work_unit_id: 'DIF-001',
    owner: 'agent-1',
    acquired_at: new Date().toISOString(),
    renewed_at: new Date().toISOString(),
    state: 'resumable',
    summary: { execution_snapshot_fingerprint: 'sha256:snapshot' },
  };
  projection.jobs = { 'discover-mrq': lease };
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selectedCircuit="analyze-dif" onClose={() => {}} />);
  expect(screen.getByText(/неизменяемый исходный снимок sha256:snapshot/)).toBeInTheDocument();
  expect(screen.getByText(/текущая политика не перечитывается/)).toBeInTheDocument();
});

test('noise approval is a separate action before MRQ coordination', async () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.jobs = {
    'discover-mrq': {
      job_id: 'discover-mrq',
      thread_id: 'thread-1',
      work_unit_id: 'DIF-001',
      owner: 'agent-1',
      acquired_at: new Date().toISOString(),
      renewed_at: new Date().toISOString(),
      state: 'blocked',
      summary: { run_id: 'run-1' },
    },
  };
  projection.items.proposals = [{
    id: 'noise-review',
    job_id: 'discover-mrq',
    kind: 'approval',
    approval_stage: 'noise',
    semantic_key: '',
    dif_ids: [],
    evidence_count: 2,
    noise_count: 2,
    mrq_id: '',
    created_at: new Date().toISOString(),
  }];
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ outcome: { status: 'resumable', run_id: 'run-1', thread_id: 'thread-1', revision: 8, summary: { next_action: 'resume' } } }),
  } as Response);
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selectedCircuit="analyze-dif" onClose={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Одобрить шум (2)' }));
  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/dispatcher/discover-mrq/approve-noise'));
    expect(call).toBeDefined();
    expect(JSON.parse(String(call![1]?.body))).toEqual({
      actor: 'local-user',
      proposal_key: 'noise-review',
      expected_fingerprint: 'sha256:workflow',
    });
    expect(new Headers(call![1]?.headers).get('Idempotency-Key')).toEqual(expect.any(String));
  });
});

test('stale approval proposal remains an explicit error instead of a decision', async () => {
  const projection = structuredClone(approvalProjection);
  projection.items.proposals = [{
    id: 'stale-proposal',
    job_id: 'discover-mrq',
    kind: 'approval',
    approval_stage: 'batch',
    semantic_key: 'fixture',
    dif_ids: ['DIF-00001'],
    evidence_count: 1,
    noise_count: 0,
    mrq_id: '',
    created_at: '2026-07-20T12:00:00Z',
  }];
  vi.mocked(fetch).mockResolvedValueOnce({
    ok: false,
    json: async () => ({ detail: 'proposal_stale' }),
  } as Response);
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:fixture" selectedCircuit="analyze-dif" onClose={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Одобрить предложение' }));
  expect(await screen.findByText('proposal_stale')).toBeInTheDocument();
  expect(screen.queryByText(/Решение утверждено/)).not.toBeInTheDocument();
});

test('completed run remains selectable as an explicit retry predecessor', () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.retry_candidates = [{
    run_id: 'run-completed',
    job_id: 'discover-mrq',
    status: 'completed',
    execution_snapshot_fingerprint: 'sha256:snapshot',
    policy_source: 'current-policy',
    workflow_fingerprint: 'sha256:workflow',
    input_fingerprints: {
      source_generation_id: 'source-1',
      diff_generation_id: 'diff-1',
      canonical_generation_id: 'canonical-1',
      work_unit_id: 'DIF-001',
    },
  }];
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selectedCircuit="analyze-dif" onClose={() => {}} />);
  const retry = screen.getByRole('button', { name: 'Явный повтор' });
  expect(retry).toBeEnabled();
  fireEvent.click(retry);
  expect(screen.getByRole('combobox', { name: 'Запуск-предшественник' })).toHaveTextContent('run-completed · completed');
});

type NewCanvasActionCase = {
  name: string;
  node: 'analysis' | 'mrq' | 'classify' | 'target';
  endpoint: string;
  body: Record<string, unknown>;
  setup: (projection: DispatcherProjection) => void;
  invoke: () => Promise<void>;
};

const leaseFor = (job: 'discover-mrq' | 'classify-mrq' | 'decide-mrq', state: string) => ({
  job_id: job,
  thread_id: `${job}-thread`,
  work_unit_id: job === 'discover-mrq' ? 'DIF-001' : job === 'classify-mrq' ? 'classify:sha256:fixture' : 'MRQ-014',
  owner: 'matrix-agent',
  acquired_at: new Date().toISOString(),
  renewed_at: new Date().toISOString(),
  state,
  summary: {
    run_id: `${job}-run`,
    bindings: {
      source_generation_id: 'source-1',
      diff_generation_id: 'diff-1',
      canonical_generation_id: 'canonical-1',
      work_unit_id: job === 'discover-mrq' ? 'DIF-001' : job === 'classify-mrq' ? 'classify:sha256:fixture' : 'MRQ-014',
    },
  },
});

const openNewCanvasNode = async (node: NewCanvasActionCase['node']) => {
  const element = await waitFor(() => {
    const found = document.querySelector<HTMLElement>(`.react-flow__node[data-id="${node}"]`);
    expect(found).not.toBeNull();
    return found!;
  });
  expect(element).toHaveAttribute('tabindex', '0');
  fireEvent.click(element);
  expect(await screen.findByRole('region', { name: 'Текущее задание' })).toBeInTheDocument();
};

const clickButton = (name: string) => async () => {
  const button = screen.getByRole('button', { name });
  expect(button).toBeEnabled();
  fireEvent.click(button);
};

const clickRetry = async () => {
  await clickButton('Явный повтор')();
  expect(screen.getByRole('dialog', { name: 'Явный повтор задания' })).toBeInTheDocument();
  await clickButton('Запустить повтор')();
};

const clickStartRecompute = async () => {
  await clickButton('Пересчитать с этапа')();
  const preview = await screen.findByLabelText('Предварительный просмотр пересчёта');
  expect(preview).toBeInTheDocument();
  for (const confirmation of ['z-confirmation', 'a-confirmation']) {
    fireEvent.click(screen.getByRole('checkbox', { name: `Подтверждаю: ${confirmation}` }));
  }
  await clickButton('Запустить пересчёт')();
};

const setupJobAction = (
  job: 'discover-mrq' | 'classify-mrq' | 'decide-mrq',
  circuit: 'analyze-dif' | 'classify-mrq' | 'decide-target',
  state?: string,
) => (projection: DispatcherProjection) => {
  const target = projection.circuits.find((item) => item.id === circuit)!;
  target.state = 'ready';
  projection.jobs = {};
  target.leases = [];
  if (state) {
    const lease = leaseFor(job, state);
    projection.jobs[job] = lease;
    target.leases = [lease];
  }
};

const approvalSetup = (
  job: 'discover-mrq' | 'decide-mrq',
  stage: 'noise' | 'batch' | 'decision',
) => (projection: DispatcherProjection) => {
  const lease = leaseFor(job, 'blocked');
  projection.jobs = { [job]: lease };
  projection.items.proposals = [{
    id: `${stage}-proposal`,
    job_id: job,
    kind: 'approval',
    approval_stage: stage === 'decision' ? '' : stage,
    semantic_key: '',
    dif_ids: [],
    evidence_count: 2,
    noise_count: stage === 'noise' ? 2 : 0,
    mrq_id: '',
    created_at: new Date().toISOString(),
  }];
};

const retryBody = (job: 'discover-mrq' | 'classify-mrq' | 'decide-mrq') => ({
  policy_source: 'reuse-snapshot',
  predecessor_run_id: `${job}-run`,
  expected_workflow_fingerprint: 'sha256:workflow',
  expected_input_fingerprints: {
    source_generation_id: 'source-1',
    diff_generation_id: 'diff-1',
    canonical_generation_id: 'canonical-1',
    work_unit_id: job === 'discover-mrq' ? 'DIF-001' : job === 'classify-mrq' ? 'classify:sha256:fixture' : 'MRQ-014',
  },
});

const basicNewCanvasActions: NewCanvasActionCase[] = ([
  ['discover-mrq', 'analysis', 'analyze-dif'],
  ['classify-mrq', 'classify', 'classify-mrq'],
  ['decide-mrq', 'target', 'decide-target'],
] as const).flatMap(([job, node, circuit]) => [
  {
    name: `${job}: запуск`,
    node,
    endpoint: `/dispatcher/${job}/start`,
    body: { actor: 'local-user' },
    setup: setupJobAction(job, circuit),
    invoke: clickButton('Запустить'),
  },
  {
    name: `${job}: мягкая остановка`,
    node,
    endpoint: `/dispatcher/${job}/stop`,
    body: {},
    setup: setupJobAction(job, circuit, 'running'),
    invoke: clickButton('Мягкая остановка'),
  },
  {
    name: `${job}: продолжение`,
    node,
    endpoint: `/dispatcher/${job}/resume`,
    body: { actor: 'local-user' },
    setup: setupJobAction(job, circuit, 'resumable'),
    invoke: clickButton('Продолжить'),
  },
  {
    name: `${job}: отмена`,
    node,
    endpoint: `/dispatcher/${job}/cancel`,
    body: { actor: 'local-user' },
    setup: setupJobAction(job, circuit, 'running'),
    invoke: clickButton('Отменить задание'),
  },
  {
    name: `${job}: явный повтор`,
    node,
    endpoint: `/dispatcher/${job}/retry`,
    body: retryBody(job),
    setup: setupJobAction(job, circuit, 'failed'),
    invoke: clickRetry,
  },
]);

const specializedNewCanvasActions: NewCanvasActionCase[] = [
  {
    name: 'одобрение шума',
    node: 'analysis',
    endpoint: '/dispatcher/discover-mrq/approve-noise',
    body: { actor: 'local-user', proposal_key: 'noise-proposal', expected_fingerprint: 'sha256:workflow' },
    setup: approvalSetup('discover-mrq', 'noise'),
    invoke: clickButton('Одобрить шум (2)'),
  },
  {
    name: 'одобрение пакета MRQ',
    node: 'mrq',
    endpoint: '/dispatcher/discover-mrq/approve-batch',
    body: { actor: 'local-user', proposal_key: 'batch-proposal', expected_fingerprint: 'sha256:workflow' },
    setup: approvalSetup('discover-mrq', 'batch'),
    invoke: clickButton('Одобрить предложение'),
  },
  {
    name: 'одобрение целевого решения',
    node: 'target',
    endpoint: '/dispatcher/decide-mrq/approve-decision',
    body: { actor: 'local-user', proposal_key: 'decision-proposal', expected_fingerprint: 'sha256:workflow' },
    setup: approvalSetup('decide-mrq', 'decision'),
    invoke: clickButton('Одобрить предложение'),
  },
  {
    name: 'предварительный просмотр пересчёта',
    node: 'analysis',
    endpoint: '/stage-recompute/preview',
    body: { boundary: 'diffs', expected_workflow_fingerprint: 'sha256:workflow' },
    setup: setupJobAction('discover-mrq', 'analyze-dif'),
    invoke: clickButton('Пересчитать с этапа'),
  },
  {
    name: 'запуск пересчёта',
    node: 'analysis',
    endpoint: '/stage-recompute/runs',
    body: {
      boundary: 'diffs',
      workflow_fingerprint: 'sha256:workflow',
      plan_fingerprint: 'sha256:plan',
      confirmations: ['a-confirmation', 'z-confirmation'],
    },
    setup: setupJobAction('discover-mrq', 'analyze-dif'),
    invoke: clickStartRecompute,
  },
  {
    name: 'отмена пересчёта',
    node: 'analysis',
    endpoint: '/stage-recompute/runs/recompute-run/cancel',
    body: {},
    setup: (projection) => {
      setupJobAction('discover-mrq', 'analyze-dif')(projection);
      projection.jobs['stage-recompute'] = {
        job_id: 'stage-recompute',
        thread_id: 'recompute-thread',
        work_unit_id: 'diffs',
        owner: 'system',
        acquired_at: new Date().toISOString(),
        renewed_at: new Date().toISOString(),
        state: 'running',
        summary: { run_id: 'recompute-run', current_step: 'diff.build' },
      };
    },
    invoke: clickButton('Отменить пересчёт'),
  },
];

const newCanvasActionMatrix = [...basicNewCanvasActions, ...specializedNewCanvasActions];

test.each(newCanvasActionMatrix)('$name доступно из интерактивного узла new и отправляет точный POST', async (actionCase) => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  actionCase.setup(projection);
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockImplementation((input) => {
    const url = String(input);
    if (url.endsWith('/stage-recompute/preview')) {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          boundary: 'diffs',
          plan_fingerprint: 'sha256:plan',
          steps: [{ step_id: '1:diff.build', operation: 'diff.build' }],
          required_confirmations: ['z-confirmation', 'a-confirmation'],
        }),
      } as Response);
    }
    if (url.includes('/stage-recompute/')) {
      return Promise.resolve({ ok: true, json: async () => ({ run_id: 'recompute-run', status: 'running', plan_fingerprint: 'sha256:plan' }) } as Response);
    }
    if (url.includes('/dispatcher/')) {
      return Promise.resolve({ ok: true, json: async () => ({ outcome: { status: 'running', revision: 8, summary: {} } }) } as Response);
    }
    return Promise.resolve({ ok: true, json: async () => ({ events: [], next_cursor: 0, resync_required: false }) } as Response);
  });
  render(<PipelineDispatcher projectId="proj-1" initialProjection={projection} initialFingerprint="sha256:workflow" />);
  await openNewCanvasNode(actionCase.node);
  await actionCase.invoke();

  await waitFor(() => {
    const calls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith(actionCase.endpoint));
    expect(calls).toHaveLength(1);
    const [, init] = calls[0];
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual(actionCase.body);
    expect(new Headers(init?.headers).get('Idempotency-Key')).toEqual(expect.stringMatching(/.+/));
  });
  if (actionCase.endpoint.includes('/dispatcher/')) {
    expect(await screen.findByText('Статус: running; ревизия: 8')).toBeInTheDocument();
  } else if (actionCase.endpoint === '/stage-recompute/preview') {
    expect(await screen.findByLabelText('Предварительный просмотр пересчёта')).toHaveTextContent('diff.build');
  } else {
    expect(await screen.findByText('Пересчёт: running')).toBeInTheDocument();
  }
  expect(screen.getByText('Ревизия 7')).toBeInTheDocument();
});

test.each(newCanvasActionMatrix)('$name показывает серверную ошибку без ложного состояния и допускает ручной повтор', async (actionCase) => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  actionCase.setup(projection);
  let targetAttempts = 0;
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockImplementation((input) => {
    const url = String(input);
    if (url.endsWith('/stage-recompute/preview') && actionCase.endpoint !== '/stage-recompute/preview') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          boundary: 'diffs',
          plan_fingerprint: 'sha256:plan',
          steps: [{ step_id: '1:diff.build', operation: 'diff.build' }],
          required_confirmations: ['z-confirmation', 'a-confirmation'],
        }),
      } as Response);
    }
    if (url.endsWith(actionCase.endpoint)) {
      targetAttempts += 1;
      return Promise.resolve((targetAttempts === 1
        ? { ok: false, json: async () => ({ detail: 'matrix-error' }) }
        : actionCase.endpoint === '/stage-recompute/preview'
          ? {
              ok: true,
              json: async () => ({
                boundary: 'diffs',
                plan_fingerprint: 'sha256:plan',
                steps: [{ step_id: '1:diff.build', operation: 'diff.build' }],
                required_confirmations: [],
              }),
            }
          : actionCase.endpoint.includes('/stage-recompute/')
            ? { ok: true, json: async () => ({ run_id: 'recompute-run', status: 'running', plan_fingerprint: 'sha256:plan' }) }
            : { ok: true, json: async () => ({ outcome: { status: 'running', revision: 8, summary: {} } }) }) as Response);
    }
    return Promise.resolve({ ok: true, json: async () => ({ events: [], next_cursor: 0, resync_required: false }) } as Response);
  });
  render(<PipelineDispatcher projectId="proj-1" initialProjection={projection} initialFingerprint="sha256:workflow" />);
  await openNewCanvasNode(actionCase.node);
  await actionCase.invoke();

  expect(await screen.findByText('matrix-error')).toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith(actionCase.endpoint))).toHaveLength(1);
  expect(screen.getByText('Ревизия 7')).toBeInTheDocument();
  expect(screen.queryByText(/Статус:/)).not.toBeInTheDocument();

  if (actionCase.endpoint.endsWith('/retry')) {
    await clickButton('Запустить повтор')();
  } else if (actionCase.endpoint === '/stage-recompute/runs') {
    await clickButton('Запустить пересчёт')();
  } else {
    await actionCase.invoke();
  }
  await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith(actionCase.endpoint))).toHaveLength(2));
  expect(screen.getByText('Ревизия 7')).toBeInTheDocument();
});
