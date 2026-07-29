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
    queue_aggregates: {
      'dif-queue': { total: 75, visible: 1, omitted: 74 },
      'mrq-queue': { total: 1, visible: 1, omitted: 0 },
    },
    agent_phases: [
      {
        job_id: 'analyze-dif',
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
          run_id: 'run-1',
          slots: [
            { slot_id: 'analyzer-1', display_label: 'Анализатор 1', state: 'running', current_invocation_id: 'i-1' },
            { slot_id: 'analyzer-2', display_label: 'Анализатор 2', state: 'idle', idle_reason_code: 'waiting_for_dispatch' },
            { slot_id: 'analyzer-3', display_label: 'Анализатор 3', state: 'idle', idle_reason_code: 'work_not_requested' },
            { slot_id: 'analyzer-4', display_label: 'Анализатор 4', state: 'idle', idle_reason_code: 'work_not_requested' },
          ],
          invocations: [
            { invocation_id: 'i-1', slot_id: 'analyzer-1', work_unit_id: 'DIF-001', status: 'running' },
            { invocation_id: 'i-2', slot_id: 'analyzer-2', work_unit_id: 'DIF-004', status: 'queued' },
          ],
        }],
      },
      { job_id: 'consolidate-mrq', phase_id: 'form-mrq', mode: 'coordinated-pool', max_concurrency: 2, roles: [
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
      proposals: [],
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
  for (const title of ['Подготовка различий', 'Анализ DIF', 'Формирование и консолидация MRQ', 'Формирование пакетов', 'Исследование цели']) {
    expect((await screen.findAllByText(title)).length).toBeGreaterThan(0);
  }
  expect(screen.queryByText('DIF-002')).not.toBeInTheDocument();
  expect(screen.queryByText(/MRQ-014/)).not.toBeInTheDocument();
  expect((await screen.findAllByText(/Смысловых DIF: 1/)).length).toBeGreaterThan(0);
  expect(document.querySelector('[data-zone="decide-outcomes"]')).toBeNull();
  expect(screen.getByText('Легенда')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Журнал' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Реестры' })).not.toBeInTheDocument();
  expect(screen.getByTestId('dispatcher-new-canvas')).toBeInTheDocument();
  expect(document.querySelectorAll('[data-stage-state="active"]')).toHaveLength(1);
  expect(document.querySelectorAll('[data-stage-state="future"]')).toHaveLength(3);
});

test('opens target approvals as a collection', async () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.items.proposals.push({
    id: 'approval-1', job_id: 'decide-mrq', kind: 'approval', approval_stage: '',
    semantic_key: 'orders', dif_ids: [], evidence_count: 1, noise_count: 0, mrq_id: 'MRQ-014', created_at: '2026-07-21T12:00:00Z',
  });
  render(<PipelineDispatcher projectId="proj-1" initialProjection={projection} />);
  fireEvent.click(document.querySelector<HTMLElement>('[data-zone="decide-target-base"]')!);
  expect(await screen.findByRole('heading', { name: 'Ожидают одобрения' })).toBeInTheDocument();
  expect(screen.getByText('Всего: 1', { exact: false })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'approval-1' })).toBeInTheDocument();
});

test('server-projected idle slot and invocation open exact inspector resources', async () => {
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockImplementation((input) => {
    const url = String(input);
    if (url.includes('/dispatcher/inspect?')) return Promise.resolve({ ok: true, json: async () => ({ observed_at: '2026-07-27T00:00:00Z', history: { items: [] }, events: { items: [] } }) } as Response);
    return Promise.resolve({ ok: true, json: async () => url.endsWith('/workflow') ? snapshotWithDispatcher : { events: [], next_cursor: 0, resync_required: false } } as Response);
  });
  const neverRun = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  neverRun.agent_phases![0].roles[0].run_id = undefined;
  render(<PipelineDispatcher projectId="proj-1" initialProjection={neverRun} />);
  fireEvent.click(document.querySelector<HTMLElement>('[data-dispatcher-kind="role"][data-role-id="analyzer"]')!);
  fireEvent.click(await screen.findByRole('button', { name: 'Анализатор 4' }));
  expect(await screen.findByRole('heading', { name: 'Слот analyzer-4' })).toHaveFocus();
  expect(screen.getByText('Работа ещё не запрошена')).toBeInTheDocument();
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes('kind=slot') && String(url).includes('slot_id=analyzer-4'))).toBe(true));
  fireEvent.click(document.querySelector<HTMLElement>('[data-dispatcher-kind="role"][data-role-id="analyzer"]')!);
  fireEvent.click(await screen.findByRole('button', { name: 'Анализатор 2' }));
  expect(await screen.findByText('Ожидается назначение работы')).toBeInTheDocument();
  fireEvent.click(document.querySelector<HTMLElement>('[data-dispatcher-kind="role"][data-role-id="analyzer"]')!);
  fireEvent.click(await screen.findByRole('button', { name: /Вызов i-1/ }));
  expect(await screen.findByRole('heading', { name: 'Вызов i-1' })).toHaveFocus();
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes('kind=invocation') && String(url).includes('invocation_id=i-1'))).toBe(true));
});

test('invocation inspector shows prepared context budget and provenance', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes('/dispatcher/inspect?')) return Promise.resolve({
      ok: true,
      json: async () => ({
        invocation: { invocation_id: 'i-1' },
        history: { items: [] },
        events: { items: [] },
        context: {
          available: true,
          prepared_input_fingerprint: 'sha256:input',
          envelope_fingerprint: 'sha256:envelope',
          summary: {
            contract_version: 'context-envelope/v1',
            prepared_input_bytes: 1200,
            headroom_bytes: 400,
            budget_truncation_count: 0,
          },
          provenance: {
            items: [{
              item_key: 'customer-diff:DIF-1',
              item_kind: 'subject',
              selection_reason: 'primary_subject',
              origin_kind: 'work_unit',
              origin_ref: 'DIF-1',
              fingerprint: 'sha256:subject',
            }],
            truncated: false,
            next_cursor: null,
          },
        },
      }),
    } as Response);
    return Promise.resolve({ ok: true, json: async () => ({ events: [], next_cursor: 0, resync_required: false }) } as Response);
  });
  const projection = snapshotWithDispatcher.dispatcher as unknown as DispatcherProjection;
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:fixture"
    selection={{ kind: 'invocation', circuitId: 'analyze-dif', phaseId: 'analyze-dif', roleId: 'analyzer', slotId: 'analyzer-1', invocationId: 'i-1' }}
    onClose={() => {}} />);
  expect(await screen.findByText('Контекст вызова')).toBeInTheDocument();
  expect(screen.getByText('context-envelope/v1')).toBeInTheDocument();
  expect(screen.getByText('customer-diff:DIF-1')).toBeInTheDocument();
  expect(screen.getByText('1200')).toBeInTheDocument();
});

test.each([
  [{ kind: 'role', circuitId: 'analyze-dif', phaseId: 'analyze-dif', roleId: 'analyzer' } as const, 'Профиль: local'],
  [{ kind: 'queue', circuitId: 'analyze-dif', queueId: 'dif-queue' } as const, 'Ожидают анализа: 75'],
  [{ kind: 'item', itemKind: 'dif', circuitId: 'analyze-dif', queueId: 'dif-queue', itemId: 'DIF-001' } as const, 'Catalogs/Test.xml'],
])('preserves last resolved %s view when its projection entity disappears', async (selection, expected) => {
  const { rerender } = render(<DispatcherPanel projectId="proj-1" projection={snapshotWithDispatcher.dispatcher as unknown as DispatcherProjection} fingerprint="sha256:fixture" selection={selection} onClose={() => {}} />);
  expect(await screen.findByText(expected, { exact: false })).toBeInTheDocument();
  const missing = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  if (selection.kind === 'role') missing.agent_phases = [];
  if (selection.kind === 'queue') missing.circuits = missing.circuits.filter((item) => item.id !== 'analyze-dif');
  if (selection.kind === 'item') missing.items.dif_queue = [];
  rerender(<DispatcherPanel projectId="proj-1" projection={missing} fingerprint="sha256:fixture" selection={selection} onClose={() => {}} />);
  expect(await screen.findByText(/показаны последние доступные сведения/i)).toBeInTheDocument();
  expect(screen.getByText(expected, { exact: false })).toBeInTheDocument();
});

test('ignores a late invocation A response after invocation B is selected and renders markup literally', async () => {
  let resolveA!: (response: Response) => void;
  let resolveB!: (response: Response) => void;
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockImplementation((input) => {
    const url = String(input);
    if (url.includes('invocation_id=i-1')) return new Promise((resolve) => { resolveA = resolve; });
    if (url.includes('invocation_id=i-2')) return new Promise((resolve) => { resolveB = resolve; });
    return Promise.resolve({ ok: true, json: async () => ({ events: [], next_cursor: 0, resync_required: false }) } as Response);
  });
  const neverRun = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  neverRun.agent_phases![0].roles[0].run_id = undefined;
  render(<PipelineDispatcher projectId="proj-1" initialProjection={neverRun} />);
  fireEvent.click(document.querySelector<HTMLElement>('[data-dispatcher-kind="role"][data-role-id="analyzer"]')!);
  fireEvent.click(await screen.findByRole('button', { name: /Вызов i-1/ }));
  fireEvent.click(document.querySelector<HTMLElement>('[data-dispatcher-kind="role"][data-role-id="analyzer"]')!);
  fireEvent.click(await screen.findByRole('button', { name: /Вызов i-2/ }));
  resolveB({ ok: true, json: async () => ({ invocation: { invocation_id: 'i-2', error_summary: '<img src=x onerror=alert(1)>' }, history: { items: [] }, events: { items: [] } }) } as Response);
  expect(await screen.findByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
  resolveA({ ok: true, json: async () => ({ invocation: { invocation_id: 'i-1', error_summary: 'late-A' }, history: { items: [] }, events: { items: [] } }) } as Response);
  await waitFor(() => expect(screen.queryByText('late-A')).not.toBeInTheDocument());
  expect(document.querySelector('img')).toBeNull();
});

test('keeps inspection cache keyed by selection and never renders B details under A heading', async () => {
  let resolveA!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes('invocation_id=i-1')) return new Promise((resolve) => { resolveA = resolve; });
    if (url.includes('invocation_id=i-2')) return Promise.resolve({
      ok: true,
      json: async () => ({ invocation: { invocation_id: 'i-2', error_summary: 'ТОЛЬКО-Б' }, history: { items: [] }, events: { items: [] } }),
    } as Response);
    return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
  });
  const projectionValue = snapshotWithDispatcher.dispatcher as unknown as DispatcherProjection;
  const a = { kind: 'invocation', circuitId: 'analyze-dif', phaseId: 'analyze-dif', roleId: 'analyzer', slotId: 'analyzer-1', invocationId: 'i-1' } as const;
  const b = { ...a, slotId: 'analyzer-2', invocationId: 'i-2' } as const;
  const { rerender } = render(<DispatcherPanel projectId="proj-1" projection={projectionValue} fingerprint="sha256:fixture" selection={b} onClose={() => {}} />);
  expect(await screen.findByText('ТОЛЬКО-Б')).toBeInTheDocument();
  rerender(<DispatcherPanel projectId="proj-1" projection={projectionValue} fingerprint="sha256:fixture" selection={a} onClose={() => {}} />);
  expect(await screen.findByRole('heading', { name: 'Вызов i-1' })).toBeInTheDocument();
  expect(screen.queryByText('ТОЛЬКО-Б')).not.toBeInTheDocument();
  rerender(<DispatcherPanel projectId="proj-1" projection={projectionValue} fingerprint="sha256:fixture" selection={b} onClose={() => {}} />);
  expect(await screen.findByText('ТОЛЬКО-Б')).toBeInTheDocument();
  resolveA({ ok: true, json: async () => ({ invocation: { invocation_id: 'i-1', error_summary: 'ПОЗДНИЙ-А' }, history: { items: [] }, events: { items: [] } }) } as Response);
  await waitFor(() => expect(screen.queryByText('ПОЗДНИЙ-А')).not.toBeInTheDocument());
});

test('aborts an invocation history page when selection changes', async () => {
  let resolvePage!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = String(input);
    if (url.includes('history_cursor=next-a')) return new Promise((resolve) => { resolvePage = resolve; });
    if (url.includes('invocation_id=i-1')) return Promise.resolve({ ok: true, json: async () => ({ invocation: { invocation_id: 'i-1' }, history: { items: [], next_cursor: 'next-a' }, events: { items: [] } }) } as Response);
    if (url.includes('invocation_id=i-2')) return Promise.resolve({ ok: true, json: async () => ({ invocation: { invocation_id: 'i-2' }, history: { items: [] }, events: { items: [] } }) } as Response);
    return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
  });
  const projection = snapshotWithDispatcher.dispatcher as unknown as DispatcherProjection;
  const a = { kind: 'invocation', circuitId: 'analyze-dif', phaseId: 'analyze-dif', roleId: 'analyzer', slotId: 'analyzer-1', invocationId: 'i-1' } as const;
  const b = { ...a, slotId: 'analyzer-2', invocationId: 'i-2' } as const;
  const { rerender } = render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:fixture" selection={a} onClose={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Ещё история' }));
  rerender(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:fixture" selection={b} onClose={() => {}} />);
  expect(await screen.findByText('i-2')).toBeInTheDocument();
  resolvePage({ ok: true, json: async () => ({ history: { items: [{ invocation_id: 'old-page-a' }] } }) } as Response);
  await waitFor(() => expect(screen.queryByText('old-page-a')).not.toBeInTheDocument());
});

test('item view opens the exact typed registry target', () => {
  const onOpenRegistry = vi.fn();
  render(<DispatcherPanel projectId="proj-1" projection={snapshotWithDispatcher.dispatcher as unknown as DispatcherProjection} fingerprint="sha256:fixture"
    selection={{ kind: 'item', itemKind: 'dif', circuitId: 'analyze-dif', queueId: 'dif-queue', itemId: 'DIF-001' }} onClose={() => {}} onOpenRegistry={onOpenRegistry} />);
  fireEvent.click(screen.getByRole('button', { name: 'Открыть запись реестра' }));
  expect(onOpenRegistry).toHaveBeenCalledWith({ registry: 'diff-inventory', itemId: 'DIF-001' });
});

test('a never-run slot adopts the first matching invocation event run', async () => {
  class Source {
    static instance: Source;
    listeners: Record<string, Array<(event: MessageEvent) => void>> = {};
    constructor() { Source.instance = this; }
    addEventListener(name: string, listener: (event: MessageEvent) => void) { (this.listeners[name] ??= []).push(listener); }
    emit(name: string, payload: unknown) { this.listeners[name]?.forEach((listener) => listener({ data: JSON.stringify(payload) } as MessageEvent)); }
    close() {}
  }
  vi.stubGlobal('EventSource', Source);
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockImplementation((input) => {
    const url = String(input);
    if (url.includes('/dispatcher/inspect?')) return Promise.resolve({ ok: true, json: async () => ({ history: { items: [] }, events: { items: [] } }) } as Response);
    return Promise.resolve({ ok: true, json: async () => url.endsWith('/workflow') ? snapshotWithDispatcher : { events: [], next_cursor: 0, resync_required: false } } as Response);
  });
  const projectionWithoutRun = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projectionWithoutRun.agent_phases![0].roles[0].run_id = undefined;
  render(<PipelineDispatcher projectId="proj-1" initialProjection={projectionWithoutRun} />);
  fireEvent.click(document.querySelector<HTMLElement>('[data-dispatcher-kind="role"][data-role-id="analyzer"]')!);
  fireEvent.click(await screen.findByRole('button', { name: 'Анализатор 4' }));
  Source.instance.emit('workflow', { sequence: 1, type: 'invocation.started', run_id: 'run-first', phase_id: 'analyze-dif', role_id: 'analyzer', slot_id: 'analyzer-4' });
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('slot_id=analyzer-4') && String(input).includes('run_id=run-first'))).toBe(true));
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
    'Формирование и консолидация MRQ',
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

test('DIF queue node shows only counts and opens detailed side panel', async () => {
  render(<PipelineDispatcher projectId="proj-1" initialProjection={snapshotWithDispatcher.dispatcher as unknown as DispatcherProjection} initialFingerprint="sha256:workflow" />);
  const node = await waitFor(() => document.querySelector<HTMLElement>('.react-flow__node[data-id="dif-queue"]')!);
  expect(node).toHaveTextContent('Канонически осталось: 75 Готово к публикации: 0');
  expect(node).not.toHaveTextContent('DIF-001');
  fireEvent.click(node);
  expect(await screen.findByText('DIF, ожидающие анализа')).toBeInTheDocument();
  expect(screen.getByText('Ожидают анализа: 75 · не показано: 74')).toBeInTheDocument();
  expect(await screen.findByText('DIF-001')).toBeInTheDocument();
  expect(screen.getByText('Catalogs/Test.xml')).toBeInTheDocument();
});

test('collection nodes show counts and open their typed items in the side panel', async () => {
  render(<PipelineDispatcher projectId="proj-1" initialProjection={snapshotWithDispatcher.dispatcher as unknown as DispatcherProjection} initialFingerprint="sha256:workflow" />);
  const semantic = await waitFor(() => document.querySelector<HTMLElement>('.react-flow__node[data-id="semantic-dif"]')!);
  expect(semantic).toHaveTextContent('Смысловых DIF: 1');
  expect(semantic).not.toHaveTextContent('DIF-002');
  fireEvent.click(semantic);
  expect(await screen.findByRole('button', { name: 'DIF-002' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));

  const batches = document.querySelector<HTMLElement>('.react-flow__node[data-id="batch-output"]')!;
  expect(batches).toHaveTextContent('Опубликовано пакетов: 1');
  fireEvent.click(batches);
  expect(await screen.findByRole('button', { name: 'MRQB-1234567890ABCDEF' })).toBeInTheDocument();
});

test('dispatcher new uses the shared action callback and restores focus', async () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockImplementation((url) => Promise.resolve({
    ok: true,
    json: async () => String(url).endsWith('/dispatcher/analyze-dif/start')
      ? { outcome: { status: 'running', revision: 8, summary: {} } }
      : { events: [], next_cursor: 0, resync_required: false },
  } as Response));
  render(<PipelineDispatcher projectId="proj-1" initialProjection={projection} initialFingerprint="sha256:fixture" />);
  const node = document.querySelector<HTMLElement>('.react-flow__node[data-id="analyzer-1"]')!;
  const role = node.querySelector<HTMLElement>('[data-dispatcher-kind="role"]')!;
  expect(node).not.toHaveAttribute('tabindex');
  expect(role.tagName).toBe('BUTTON');
  fireEvent.click(role);
  expect(await screen.findByText('Текущее задание')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Запустить' }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/dispatcher/analyze-dif/start'))).toHaveLength(1));
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/projects/proj-1/dispatcher/analyze-dif/start',
    expect.objectContaining({ method: 'POST', body: JSON.stringify({ actor: 'local-user' }) }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
  await waitFor(() => expect(role).toHaveFocus());
});

test.each([
  ['остановленное', stoppedProjection, 'Остановлен'],
  ['ошибочное', errorProjection, 'Ошибка'],
  ['ожидающее одобрения', approvalProjection, 'Ожидает одобрения'],
] as const)('%s состояние аренды показано текстом', (_name, projection, label) => {
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:fixture" selection={{ kind: 'circuit', circuitId: 'analyze-dif' }} onClose={() => {}} />);
  expect(screen.getByText(new RegExp(`Аренда: fixture-agent \\(${label}\\)`))).toBeInTheDocument();
});

test('ошибка задания показана в основном контроле этапов', () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.jobs['analyze-dif'] = { ...errorProjection.jobs['analyze-dif'] };
  render(<PipelineDispatcher projectId="proj-1" initialProjection={projection} />);
  expect(document.querySelector('[data-stage-state="error"]')).toHaveTextContent('Анализ DIFОшибка');
});

test('активное задание имеет приоритет над заблокированным следующим этапом', () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.circuits[1].state = 'active' as DispatcherProjection['circuits'][number]['state'];
  projection.jobs['analyze-dif'] = {
    job_id: 'analyze-dif', thread_id: 'thread', work_unit_id: 'analyze-dif', owner: 'local-user',
    acquired_at: new Date().toISOString(), renewed_at: new Date().toISOString(), state: 'running', summary: {},
  };
  render(<PipelineDispatcher projectId="proj-1" initialProjection={projection} />);
  expect(document.querySelector('[data-stage-state="active"]')).toHaveTextContent('Анализ DIFТекущий');
});

test('context settings action passes the selected workflow step', () => {
  const onOpenSettings = vi.fn();
  render(<DispatcherPanel projectId="proj-1" projection={snapshotWithDispatcher.dispatcher as unknown as DispatcherProjection} fingerprint="sha256:fixture" selection={{ kind: 'circuit', circuitId: 'classify-mrq' }} onClose={() => {}} onOpenSettings={onOpenSettings} />);
  fireEvent.click(screen.getByRole('button', { name: 'Профили и параметры' }));
  expect(onOpenSettings).toHaveBeenCalledWith('classify-mrq');
});

test('PipelineDispatcher shows real invocation states independently of a soft-stopped lease', async () => {
  const lease = { job_id: 'analyze-dif', thread_id: 'thread-1', work_unit_id: 'DIF-001', owner: 'agent-1', acquired_at: new Date().toISOString(), renewed_at: new Date().toISOString(), state: 'resumable', summary: { execution_snapshot_fingerprint: 'sha256:snapshot' } };
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.jobs = { 'analyze-dif': lease };
  projection.circuits[1].leases = [lease];
  render(<PipelineDispatcher projectId="proj-1" initialProjection={projection} initialFingerprint="sha256:abc" />);
  expect(await screen.findByText(/Выполняется: 1/)).toBeInTheDocument();
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
      job_id: 'analyze-dif', thread_id: 'thread-1', work_unit_id: 'DIF-001', owner: 'agent-1',
      acquired_at: new Date().toISOString(), renewed_at: new Date().toISOString(), state,
      summary: { execution_snapshot_fingerprint: 'sha256:snapshot' },
    };
    projection.jobs = { 'analyze-dif': lease };
    projection.circuits[1].leases = [lease];
  } else {
    projection.jobs = {};
    projection.circuits[1].leases = [];
  }
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ outcome: { status: 'running', revision: 8, summary: {} } }) } as Response);
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'analyze-dif' }} onClose={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: button }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(fetchMock).toHaveBeenCalledWith(
    `/api/v1/projects/proj-1/dispatcher/analyze-dif/${action}`,
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
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'analyze-dif' }} onClose={() => {}} readOnly />);
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Пересчитать с этапа' })).toBeDisabled();
});

test.each([
  ['консолидация MRQ', 'consolidate-mrq', 'form-mrq', 'consolidation', 'approve-consolidation'],
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
    id: `${stage}-proposal`, job_id: job, kind: 'approval', approval_stage: stage,
    semantic_key: '', dif_ids: [], evidence_count: 1, noise_count: 0, mrq_id: '', created_at: new Date().toISOString(),
  }];
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ outcome: { status: 'complete', revision: 8, summary: {} } }) } as Response);
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: circuit }} onClose={() => {}} />);
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
          stop_before: 'dif.classify-next',
          generations: { source: 'src-1', diff: 'diff-1' },
        }),
      } as Response);
    }
    if (url.endsWith('/stage-recompute/runs')) {
      return Promise.resolve({ ok: true, json: async () => ({ run_id: 'run-1', status: 'running', plan_fingerprint: 'sha256:plan' }) } as Response);
    }
    return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
  });
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'analyze-dif' }} onClose={() => {}} />);
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
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'analyze-dif' }} onClose={() => {}} />);
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Отменить пересчёт' })).toBeEnabled();
  expect(screen.getByText(/Выполняется шаг: diff.build/)).toBeInTheDocument();
  expect(screen.getByText(/временно недоступны/)).toBeInTheDocument();
});

test('MRQ panel guides to explicit actions without reset', () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'form-mrq' }} onClose={() => {}} />);
  expect(screen.getByText(/массовый сброс не поддерживается/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Пересчитать с этапа' })).not.toBeInTheDocument();
});

test('explicit retry sends policy, predecessor and expected fingerprints', async () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  const lease = {
    job_id: 'analyze-dif',
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
  projection.jobs = { 'analyze-dif': lease };
  projection.circuits[1].leases = [lease];
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValue({
    ok: true,
    json: async () => ({ outcome: { status: 'running', run_id: 'run-new', thread_id: 'thread-new', revision: 8, summary: {} } }),
  } as Response);
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'analyze-dif' }} onClose={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Явный повтор' }));
  expect(screen.getByRole('dialog', { name: 'Явный повтор задания' })).toBeInTheDocument();
  fireEvent.mouseDown(screen.getByLabelText('Источник политики'));
  fireEvent.click(screen.getByRole('option', { name: 'Текущая политика' }));
  fireEvent.click(screen.getByRole('button', { name: 'Запустить повтор' }));
  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/dispatcher/analyze-dif/retry'));
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
    job_id: 'analyze-dif',
    thread_id: 'thread-1',
    work_unit_id: 'DIF-001',
    owner: 'agent-1',
    acquired_at: new Date().toISOString(),
    renewed_at: new Date().toISOString(),
    state: 'resumable',
    summary: { execution_snapshot_fingerprint: 'sha256:snapshot' },
  };
  projection.jobs = { 'analyze-dif': lease };
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'analyze-dif' }} onClose={() => {}} />);
  expect(screen.getByText(/неизменяемый исходный снимок sha256:snapshot/)).toBeInTheDocument();
  expect(screen.getByText(/текущая политика не перечитывается/)).toBeInTheDocument();
});

test('DIF analysis never exposes approval for a noise candidate', () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.jobs = {
    'analyze-dif': {
      job_id: 'analyze-dif',
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
    job_id: 'analyze-dif',
    kind: 'noise_candidate',
    approval_stage: '',
    semantic_key: '',
    dif_ids: [],
    evidence_count: 2,
    noise_count: 2,
    mrq_id: '',
    created_at: new Date().toISOString(),
  }];
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'analyze-dif' }} onClose={() => {}} />);
  expect(screen.queryByRole('button', { name: /Одобрить/ })).not.toBeInTheDocument();
});

test('stale approval proposal remains an explicit error instead of a decision', async () => {
  const projection = structuredClone(approvalProjection);
  projection.items.proposals = [{
    id: 'stale-proposal',
    job_id: 'consolidate-mrq',
    kind: 'approval',
    approval_stage: 'consolidation',
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
  projection.jobs = { 'consolidate-mrq': { ...projection.jobs['analyze-dif'], job_id: 'consolidate-mrq' } };
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:fixture" selection={{ kind: 'circuit', circuitId: 'form-mrq' }} onClose={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Одобрить предложение' }));
  expect(await screen.findByText('proposal_stale')).toBeInTheDocument();
  expect(screen.queryByText(/Решение утверждено/)).not.toBeInTheDocument();
});

test('completed run remains selectable as an explicit retry predecessor', () => {
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.retry_candidates = [{
    run_id: 'run-completed',
    job_id: 'analyze-dif',
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
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'analyze-dif' }} onClose={() => {}} />);
  const retry = screen.getByRole('button', { name: 'Явный повтор' });
  expect(retry).toBeEnabled();
  fireEvent.click(retry);
  expect(screen.getByRole('combobox', { name: 'Запуск-предшественник' })).toHaveTextContent('run-completed · completed');
});

test('split stages show canonical window progress, context preflight and blockers', () => {
  const projection = structuredClone(saturatedProjection);
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'analyze-dif' }} onClose={() => {}} />);
  expect(screen.getByRole('region', { name: 'Ход анализа DIF' })).toHaveTextContent('Всего: 12 · классифицировано: 11 · осталось: 1');
  expect(screen.getByRole('region', { name: 'Ход анализа DIF' })).toHaveTextContent('Текущее окно: 11 из 12 · ошибки: 1');
  expect(screen.getByText(/Текущее окно не опубликовано/)).toBeInTheDocument();
  cleanup();

  projection.circuits.find((item) => item.id === 'form-mrq')!.aggregates = {
    ...projection.circuits.find((item) => item.id === 'form-mrq')!.aggregates,
    blocker_code: 'consolidation.context_capacity',
    blocker_message: 'Ёмкость контекста недостаточна',
    uncovered_partition_count: 2,
  };
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'form-mrq' }} onClose={() => {}} />);
  const progress = screen.getByRole('region', { name: 'Ход консолидации MRQ' });
  expect(progress).toHaveTextContent('Предварительный расчёт: разделов 2, пар 3, вызовов 5');
  expect(progress).toHaveTextContent('Фактическая ёмкость модели: 200000 токенов · оценщик: utf8-v1');
  expect(progress).toHaveTextContent('consolidation.context_capacity');
  expect(progress).toHaveTextContent('непокрытых разделов: 2');
});

test('already applied consolidation is shown as an idempotent result', () => {
  const projection = structuredClone(saturatedProjection);
  projection.circuits.find((item) => item.id === 'form-mrq')!.aggregates!.already_applied = true;
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'form-mrq' }} onClose={() => {}} />);
  expect(screen.getByText(/возвращён существующий результат без нового поколения/)).toBeInTheDocument();
});

test('stale legacy decisions are visible after consolidation', () => {
  const projection = structuredClone(saturatedProjection);
  projection.circuits.find((item) => item.id === 'form-mrq')!.aggregates!.legacy_decisions_stale = 2;
  render(<DispatcherPanel projectId="proj-1" projection={projection} fingerprint="sha256:workflow" selection={{ kind: 'circuit', circuitId: 'form-mrq' }} onClose={() => {}} />);
  expect(screen.getByText(/Устаревшие решения прежнего поколения: 2/)).toBeInTheDocument();
});

type NewCanvasActionCase = {
  name: string;
  node: 'analysis' | 'mrq' | 'classify' | 'target';
  endpoint: string;
  body: Record<string, unknown>;
  setup: (projection: DispatcherProjection) => void;
  invoke: () => Promise<void>;
};

const leaseFor = (job: 'analyze-dif' | 'consolidate-mrq' | 'classify-mrq' | 'decide-mrq', state: string) => ({
  job_id: job,
  thread_id: `${job}-thread`,
  work_unit_id: job === 'analyze-dif' ? 'DIF-001' : job === 'consolidate-mrq' ? 'consolidation:sha256:fixture' : job === 'classify-mrq' ? 'classify:sha256:fixture' : 'MRQ-014',
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
      work_unit_id: job === 'analyze-dif' ? 'DIF-001' : job === 'consolidate-mrq' ? 'consolidation:sha256:fixture' : job === 'classify-mrq' ? 'classify:sha256:fixture' : 'MRQ-014',
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
  job: 'analyze-dif' | 'consolidate-mrq' | 'classify-mrq' | 'decide-mrq',
  circuit: 'analyze-dif' | 'form-mrq' | 'classify-mrq' | 'decide-target',
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
  job: 'consolidate-mrq' | 'decide-mrq',
  stage: 'consolidation' | 'decision',
) => (projection: DispatcherProjection) => {
  const lease = leaseFor(job, 'blocked');
  projection.jobs = { [job]: lease };
  projection.items.proposals = [{
    id: `${stage}-proposal`,
    job_id: job,
    kind: 'approval',
    approval_stage: stage,
    semantic_key: '',
    dif_ids: [],
    evidence_count: 2,
    noise_count: 0,
    mrq_id: '',
    created_at: new Date().toISOString(),
  }];
};

const retryBody = (job: 'analyze-dif' | 'consolidate-mrq' | 'classify-mrq' | 'decide-mrq') => ({
  policy_source: 'reuse-snapshot',
  predecessor_run_id: `${job}-run`,
  expected_workflow_fingerprint: 'sha256:workflow',
  expected_input_fingerprints: {
    source_generation_id: 'source-1',
    diff_generation_id: 'diff-1',
    canonical_generation_id: 'canonical-1',
    work_unit_id: job === 'analyze-dif' ? 'DIF-001' : job === 'consolidate-mrq' ? 'consolidation:sha256:fixture' : job === 'classify-mrq' ? 'classify:sha256:fixture' : 'MRQ-014',
  },
});

const basicNewCanvasActions: NewCanvasActionCase[] = ([
  ['analyze-dif', 'analysis', 'analyze-dif'],
  ['consolidate-mrq', 'mrq', 'form-mrq'],
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
    name: 'одобрение консолидации MRQ',
    node: 'mrq',
    endpoint: '/dispatcher/consolidate-mrq/approve-consolidation',
    body: { actor: 'local-user', proposal_key: 'consolidation-proposal', expected_fingerprint: 'sha256:workflow' },
    setup: approvalSetup('consolidate-mrq', 'consolidation'),
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
    setup: setupJobAction('analyze-dif', 'analyze-dif'),
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
    setup: setupJobAction('analyze-dif', 'analyze-dif'),
    invoke: clickStartRecompute,
  },
  {
    name: 'отмена пересчёта',
    node: 'analysis',
    endpoint: '/stage-recompute/runs/recompute-run/cancel',
    body: {},
    setup: (projection) => {
      setupJobAction('analyze-dif', 'analyze-dif')(projection);
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
