import { render, screen } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { PipelineDispatcher, shortWorkId } from './PipelineDispatcher';
import type { DispatcherProjection } from './projection';

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
      { id: 'decide-target', state: 'unknown', aggregates: { decision_count: 0 } },
    ],
    jobs: {},
    items: {
      dif_queue: [{ id: 'DIF-001', path: 'Catalogs/Test.xml', kind: 'changed', state: 'queued' }],
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

test('PipelineDispatcher renders four circuits and freshness label', async () => {
  render(<PipelineDispatcher projectId="proj-1" />);
  expect((await screen.findAllByText('Подготовка')).length).toBeGreaterThan(0);
  for (const title of ['Подготовка различий', 'Анализ DIF', 'Формирование MRQ', 'Исследование целевой базы']) {
    expect((await screen.findAllByText(title)).length).toBeGreaterThan(0);
  }
  expect((await screen.findAllByText('DIF-002')).length).toBeGreaterThan(0);
  expect((await screen.findAllByText('MRQ-014')).length).toBeGreaterThan(0);
  expect((await screen.findAllByText('Адаптировать')).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/подтверждено/).length).toBeGreaterThan(0);
  expect(screen.getByText('Легенда')).toBeInTheDocument();
  expect(screen.getByText('Целевая база')).toBeInTheDocument();
  expect(screen.getByText('Классификатор MRQ')).toBeInTheDocument();
  expect(document.querySelectorAll('[data-stage-state="active"]')).toHaveLength(1);
  expect(document.querySelectorAll('[data-stage-state="future"]')).toHaveLength(4);
});

test('shortWorkId keeps the type and a readable stable prefix', () => {
  expect(shortWorkId('DIF-0083AF4703A5C7DCCA')).toBe('DIF-0083A');
  expect(shortWorkId('MRQ-014')).toBe('MRQ-014');
});

test('PipelineDispatcher does not imitate active workers without a lease', async () => {
  render(<PipelineDispatcher projectId="proj-1" />);
  expect((await screen.findAllByText('Свободен')).length).toBeGreaterThanOrEqual(8);
  expect(screen.queryByText('Анализирует')).not.toBeInTheDocument();
  expect(screen.queryByText('Исследует')).not.toBeInTheDocument();
});

test('PipelineDispatcher shows a soft-stopped lease without imitating work', async () => {
  const lease = { job_id: 'discover-mrq', thread_id: 'thread-1', work_unit_id: 'DIF-001', owner: 'agent-1', acquired_at: new Date().toISOString(), renewed_at: new Date().toISOString(), state: 'resumable', summary: {} };
  const projection = structuredClone(snapshotWithDispatcher.dispatcher) as unknown as DispatcherProjection;
  projection.jobs = { 'discover-mrq': lease };
  projection.circuits[1].leases = [lease];
  render(<PipelineDispatcher projectId="proj-1" initialProjection={projection} initialFingerprint="sha256:abc" />);
  expect((await screen.findAllByText('Остановлен')).length).toBeGreaterThan(0);
  expect(screen.queryByText('Анализирует')).not.toBeInTheDocument();
});
