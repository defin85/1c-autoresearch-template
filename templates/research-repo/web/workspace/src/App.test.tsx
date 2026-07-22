import { render, screen } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { App, groupEvents } from './App';

beforeEach(() => { vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => [] })); });

test('shows repository-owned workspace entry', async () => {
  render(<App />);
  expect(await screen.findByText('Исследование конфигурации 1С')).toBeInTheDocument();
  expect(screen.getByText(/Репозиторий хранит состояние процесса/)).toBeInTheDocument();
});

test('groups workflow events as run, job, step and attempt', () => {
  const grouped = groupEvents([
    { sequence: 1, timestamp: '2026-01-01T00:00:00Z', type: 'run.created', run_id: 'run-1', payload: {} },
    { sequence: 2, timestamp: '2026-01-01T00:00:01Z', type: 'step.started', run_id: 'run-1', job_id: 'job-1', step_id: 'step-1', attempt: 1, payload: {} },
  ]);
  expect(grouped['run-1'].run.run[0]).toHaveLength(1);
  expect(grouped['run-1']['job-1']['step-1'][1][0].sequence).toBe(2);
});
