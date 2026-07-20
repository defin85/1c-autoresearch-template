import { afterEach, describe, expect, it, vi } from 'vitest';
import { streamProject } from './api';

class FakeEventSource {
  static instance: FakeEventSource;
  listeners = new Map<string, EventListener>();
  onopen: (() => void) | null = null; onerror: (() => void) | null = null;
  constructor(public url: string) { FakeEventSource.instance = this; }
  addEventListener(type: string, listener: EventListener) { this.listeners.set(type, listener); }
  close() {}
  emit(type: string, id: string, data = '{}') { this.listeners.get(type)?.({ lastEventId: id, data } as unknown as Event); }
}

describe('event bridge', () => {
  afterEach(() => vi.useRealTimers());
  it('deduplicates event ids and coalesces high-frequency updates to four per second', () => {
    vi.useFakeTimers(); vi.stubGlobal('EventSource', FakeEventSource);
    const applied: number[] = []; const stop = streamProject('p', (_event, id) => applied.push(id), () => {});
    FakeEventSource.instance.emit('stage.progress', '1'); FakeEventSource.instance.emit('stage.progress', '1'); FakeEventSource.instance.emit('stage.progress', '2');
    expect(applied).toEqual([]); vi.advanceTimersByTime(250); expect(applied).toEqual([2]); stop();
  });
});
