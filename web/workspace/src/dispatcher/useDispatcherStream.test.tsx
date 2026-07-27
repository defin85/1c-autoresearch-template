import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { EMPTY_ITEMS } from './projection';
import { useDispatcherStream } from './useDispatcherStream';

const snapshot = { workflow_fingerprint: 'sha256:test', dispatcher: { schema_version: '1', revision: 1, fresh_at: new Date().toISOString(), circuits: [], jobs: {}, items: EMPTY_ITEMS } };

class EventSourceMock {
  static instance: EventSourceMock;
  static count = 0;
  listeners: Record<string, Array<(event: Event | MessageEvent) => void>> = {};
  constructor(public url: string) {
    EventSourceMock.instance = this;
    EventSourceMock.count += 1;
  }
  addEventListener(name: string, listener: (event: Event | MessageEvent) => void) { (this.listeners[name] ??= []).push(listener); }
  emit(name: string, data = '') { for (const listener of this.listeners[name] ?? []) listener({ data } as MessageEvent); }
  close() {}
}

beforeEach(() => {
  EventSourceMock.count = 0;
  vi.useFakeTimers();
  vi.stubGlobal('EventSource', EventSourceMock);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot }));
});

afterEach(() => vi.useRealTimers());

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });
const projection = (revision: number) => ({ ...snapshot, dispatcher: { ...snapshot.dispatcher, revision } });

test('coalesces an SSE burst into single-flight leading and trailing refreshes', async () => {
  let releaseFirst!: () => void;
  const first = new Promise<void>((resolve) => { releaseFirst = resolve; });
  vi.mocked(fetch)
    .mockImplementationOnce(async () => { await first; return { ok: true, json: async () => projection(2) } as Response; })
    .mockResolvedValue({ ok: true, json: async () => projection(3) } as Response);

  renderHook(() => useDispatcherStream({ projectId: 'project-1', initialProjection: snapshot.dispatcher }));
  const fetchMock = vi.mocked(fetch);
  expect(EventSourceMock.instance.url).toContain('/events/stream?cursor=0');

  act(() => {
    for (let sequence = 1; sequence <= 99; sequence += 1) {
      EventSourceMock.instance.emit('workflow', JSON.stringify({ sequence, type: sequence % 2 ? 'log.append' : 'step.progress' }));
    }
    EventSourceMock.instance.emit('workflow', JSON.stringify({ sequence: 100, type: 'run.finished' }));
    EventSourceMock.instance.emit('workflow', JSON.stringify({ sequence: 100, type: 'run.finished' }));
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);

  releaseFirst();
  await flush();
  await act(async () => vi.advanceTimersByTimeAsync(999));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => vi.advanceTimersByTimeAsync(1));
  expect(fetchMock).toHaveBeenCalledTimes(2);
  await flush();
  await act(async () => vi.advanceTimersByTimeAsync(2000));
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test('resyncs a sequence gap no sooner than 1000 ms and keeps an older revision blocked', async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce({ ok: true, json: async () => projection(5) } as Response)
    .mockResolvedValueOnce({ ok: true, json: async () => projection(4) } as Response);
  const { result } = renderHook(() => useDispatcherStream({ projectId: 'project-1', initialProjection: snapshot.dispatcher }));

  act(() => EventSourceMock.instance.emit('workflow', JSON.stringify({ sequence: 1 })));
  await flush();
  expect(result.current.projection.revision).toBe(5);

  await act(async () => vi.advanceTimersByTimeAsync(100));
  act(() => EventSourceMock.instance.emit('workflow', JSON.stringify({ sequence: 3 })));
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  await act(async () => vi.advanceTimersByTimeAsync(899));
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  await act(async () => vi.advanceTimersByTimeAsync(1));
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  await flush();
  expect(result.current.projection.revision).toBe(5);
  expect(result.current.resyncing).toBe(true);
  expect(result.current.error).toContain('older than current revision');
});

test('coalesces repeated gaps and resync requests while a snapshot is active', async () => {
  let releaseFirst!: () => void;
  const first = new Promise<void>((resolve) => { releaseFirst = resolve; });
  vi.mocked(fetch)
    .mockImplementationOnce(async () => { await first; return { ok: true, json: async () => projection(5) } as Response; })
    .mockResolvedValueOnce({ ok: true, json: async () => projection(4) } as Response);
  const { result } = renderHook(() => useDispatcherStream({ projectId: 'project-1', initialProjection: snapshot.dispatcher }));

  act(() => EventSourceMock.instance.emit('workflow', JSON.stringify({ sequence: 1 })));
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  act(() => {
    EventSourceMock.instance.emit('workflow', JSON.stringify({ sequence: 3 }));
    EventSourceMock.instance.emit('resync');
    EventSourceMock.instance.emit('resync');
  });
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  expect(EventSourceMock.count).toBe(1);

  releaseFirst();
  await flush();
  await act(async () => vi.advanceTimersByTimeAsync(999));
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  await act(async () => vi.advanceTimersByTimeAsync(1));
  await flush();

  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  expect(result.current.projection.revision).toBe(5);
  expect(result.current.resyncing).toBe(true);
  expect(result.current.error).toContain('older than current revision');
  expect(EventSourceMock.count).toBe(1);
});

test('keeps resync blocked after a failed snapshot until an explicit retry succeeds', async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce({ ok: false, json: async () => ({ detail: 'snapshot unavailable' }) } as Response)
    .mockResolvedValueOnce({ ok: true, json: async () => projection(6) } as Response);
  const { result } = renderHook(() => useDispatcherStream({ projectId: 'project-1', initialProjection: snapshot.dispatcher }));

  act(() => EventSourceMock.instance.emit('resync'));
  await flush();
  expect(result.current.projection.revision).toBe(1);
  expect(result.current.resyncing).toBe(true);
  expect(result.current.error).toBe('snapshot unavailable');
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);

  let retry!: Promise<void>;
  act(() => { retry = result.current.refresh(); });
  await flush();
  await act(async () => vi.advanceTimersByTimeAsync(1001));
  await act(async () => retry);
  expect(result.current.projection.revision).toBe(6);
  expect(result.current.resyncing).toBe(false);
  expect(result.current.error).toBe('');
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  expect(EventSourceMock.count).toBe(1);
});

test('does not reconnect when a cloned bootstrap projection is passed on rerender', () => {
  const { rerender } = renderHook(
    ({ initialProjection }) => useDispatcherStream({ projectId: 'project-1', initialProjection }),
    { initialProps: { initialProjection: snapshot.dispatcher } },
  );
  expect(EventSourceMock.count).toBe(1);
  rerender({ initialProjection: structuredClone(snapshot.dispatcher) });
  expect(EventSourceMock.count).toBe(1);
  expect(vi.mocked(fetch)).not.toHaveBeenCalled();
});

test('resets all stream state and accepts a lower revision after project switch before opening SSE', async () => {
  const oldProjection = projection(9).dispatcher;
  vi.mocked(fetch).mockImplementation(async (input) => ({
    ok: true,
    json: async () => String(input).includes('project-2')
      ? { workflow_fingerprint: 'sha256:project-2', dispatcher: { ...snapshot.dispatcher, revision: 1 } }
      : { workflow_fingerprint: 'sha256:project-1', dispatcher: { ...snapshot.dispatcher, revision: 10 } },
  } as Response));
  const { result, rerender } = renderHook(
    ({ projectId }) => useDispatcherStream({ projectId, initialProjection: oldProjection }),
    { initialProps: { projectId: 'project-1' } },
  );
  act(() => EventSourceMock.instance.emit('workflow', JSON.stringify({ sequence: 4, run_id: 'old-run' })));
  await flush();
  expect(result.current.lastEvent?.run_id).toBe('old-run');
  rerender({ projectId: 'project-2' });
  expect(result.current.connectionState).toBe('connecting');
  expect(result.current.lastEvent).toBeNull();
  expect(result.current.fingerprint).toBe('');
  expect(result.current.projection.revision).toBe(0);
  expect(EventSourceMock.count).toBe(1);
  await act(async () => vi.advanceTimersByTimeAsync(1000));
  await flush();
  expect(result.current.projection.revision).toBe(1);
  expect(result.current.fingerprint).toBe('sha256:project-2');
  expect(EventSourceMock.count).toBe(2);
  expect(EventSourceMock.instance.url).toContain('/projects/project-2/events/stream?cursor=0');
});

test('uses one five-second refresh while SSE is degraded', async () => {
  renderHook(() => useDispatcherStream({ projectId: 'project-1', initialProjection: snapshot.dispatcher }));
  act(() => EventSourceMock.instance.emit('error'));
  await act(async () => vi.advanceTimersByTimeAsync(4_999));
  expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  await act(async () => vi.advanceTimersByTimeAsync(1));
  await flush();
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
});

test('reconciles connected active work every thirty seconds', async () => {
  const active = { ...snapshot.dispatcher, jobs: { job: { state: 'running' } } };
  renderHook(() => useDispatcherStream({ projectId: 'project-1', initialProjection: active as never }));
  act(() => EventSourceMock.instance.emit('open'));
  await act(async () => vi.advanceTimersByTimeAsync(29_999));
  expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  await act(async () => vi.advanceTimersByTimeAsync(1));
  await flush();
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
});
