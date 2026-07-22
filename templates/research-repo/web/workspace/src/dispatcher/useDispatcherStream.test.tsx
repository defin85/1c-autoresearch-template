import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { EMPTY_ITEMS } from './projection';
import { useDispatcherStream } from './useDispatcherStream';

const snapshot = { workflow_fingerprint: 'sha256:test', dispatcher: { schema_version: '1', revision: 1, fresh_at: new Date().toISOString(), circuits: [], jobs: {}, items: EMPTY_ITEMS } };

class EventSourceMock {
  static instance: EventSourceMock;
  listeners: Record<string, Array<(event: Event | MessageEvent) => void>> = {};
  constructor(public url: string) { EventSourceMock.instance = this; }
  addEventListener(name: string, listener: (event: Event | MessageEvent) => void) { (this.listeners[name] ??= []).push(listener); }
  emit(name: string, data = '') { for (const listener of this.listeners[name] ?? []) listener({ data } as MessageEvent); }
  close() {}
}

beforeEach(() => {
  vi.stubGlobal('EventSource', EventSourceMock);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot }));
});

test('uses one SSE stream, drops duplicates and resyncs sequence gaps without polling', async () => {
  const { result } = renderHook(() => useDispatcherStream({ projectId: 'project-1' }));
  await waitFor(() => expect(result.current.resyncing).toBe(false));
  const fetchMock = vi.mocked(fetch);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(EventSourceMock.instance.url).toContain('/events/stream?cursor=0');

  await act(async () => EventSourceMock.instance.emit('workflow', JSON.stringify({ sequence: 1, payload: { kind: 'dispatcher.group.ready' } })));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  await act(async () => EventSourceMock.instance.emit('workflow', JSON.stringify({ sequence: 1, payload: { kind: 'dispatcher.group.ready' } })));
  expect(fetchMock).toHaveBeenCalledTimes(2);

  await act(async () => EventSourceMock.instance.emit('workflow', JSON.stringify({ sequence: 3, payload: { kind: 'dispatcher.group.ready' } })));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
});

test('server resync event reloads the common snapshot', async () => {
  const { result } = renderHook(() => useDispatcherStream({ projectId: 'project-1' }));
  await waitFor(() => expect(result.current.resyncing).toBe(false));
  await act(async () => EventSourceMock.instance.emit('resync'));
  await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2));
});
