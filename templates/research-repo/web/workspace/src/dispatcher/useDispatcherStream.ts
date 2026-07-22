// Хук подписки на операционную проекцию диспетчера.
//
// Использует существующий снимок ``/api/v1/projects/{id}/workflow`` для
// начального состояния и существующий SSE ``/api/v1/projects/{id}/events/stream``
// для обновлений. Отдельный поток или параллельный опрос диспетчера не
// создаётся. При ``resync_required`` локальные переходы очищаются и снимок
// перечитывается.

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { EMPTY_PROJECTION, type DispatcherProjection } from './projection';

export interface SnapshotWithDispatcher {
  workflow_fingerprint: string;
  dispatcher?: DispatcherProjection;
}

interface DispatcherStreamOptions {
  projectId: string;
  initialProjection?: DispatcherProjection;
  initialFingerprint?: string;
}

export interface DispatcherStreamState {
  projection: DispatcherProjection;
  fingerprint: string;
  resyncing: boolean;
  error: string;
}

export function useDispatcherStream({ projectId, initialProjection, initialFingerprint = '' }: DispatcherStreamOptions): DispatcherStreamState & { refresh: () => Promise<void> } {
  const [projection, setProjection] = useState<DispatcherProjection>(initialProjection ?? EMPTY_PROJECTION);
  const [fingerprint, setFingerprint] = useState(initialFingerprint);
  const [resyncing, setResyncing] = useState(!initialProjection);
  const [error, setError] = useState('');
  const cursorRef = useRef(0);
  const closedRef = useRef(false);

  const loadSnapshot = useCallback(async () => {
    try {
      const snapshot = await api<SnapshotWithDispatcher>(`/projects/${projectId}/workflow`);
      setFingerprint(snapshot.workflow_fingerprint);
      setProjection(snapshot.dispatcher ?? EMPTY_PROJECTION);
      setError('');
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : 'snapshot fetch failed');
    } finally {
      setResyncing(false);
    }
  }, [projectId]);

  const refresh = useCallback(async () => {
    await loadSnapshot();
  }, [loadSnapshot]);

  useEffect(() => {
    closedRef.current = false;
    let eventSource: EventSource | null = null;
    const connect = () => {
      if (closedRef.current) return;
      try {
      // Существующий SSE-маршрут; передаём последний sequence как cursor
      eventSource = new EventSource(`/api/v1/projects/${projectId}/events/stream?cursor=${cursorRef.current}`, { withCredentials: true });
      eventSource.addEventListener('workflow', (event) => {
        try {
          const payload = JSON.parse((event as MessageEvent).data) as { sequence: number; payload?: { revision?: number; kind?: string } };
          if (typeof payload.sequence !== 'number' || payload.sequence <= cursorRef.current) return;
          if (cursorRef.current > 0 && payload.sequence !== cursorRef.current + 1) {
            setResyncing(true);
            cursorRef.current = 0;
            void loadSnapshot().finally(() => setResyncing(false));
            return;
          }
          cursorRef.current = payload.sequence;
          // при любом событии диспетчера перечитываем снимок, чтобы получить консистентную проекцию с тем же revision
          if (payload.payload?.kind?.startsWith('dispatcher.')) {
            void loadSnapshot();
          }
        } catch {
          // игнорируем мусорные события; снимок останется прежним
        }
      });
      eventSource.addEventListener('resync', () => {
        setResyncing(true);
        cursorRef.current = 0;
        void loadSnapshot().finally(() => setResyncing(false));
      });
      eventSource.addEventListener('open', () => {
        setError('');
      });
      eventSource.addEventListener('error', () => {
        // EventSource переподключается сам с Last-Event-ID; параллельный опрос запрещён.
        if (closedRef.current) return;
        setError('Поток событий временно недоступен, выполняется переподключение');
      });
      } catch (sourceError) {
        setError(sourceError instanceof Error ? sourceError.message : 'event stream unavailable');
      }
    };
    // Родитель уже получил общий снимок; без него загружаем его перед SSE.
    if (initialProjection) connect(); else void loadSnapshot().then(connect);
    return () => {
      closedRef.current = true;
      if (eventSource) eventSource.close();
    };
  }, [projectId, loadSnapshot, initialProjection]);

  return { projection, fingerprint, resyncing, error, refresh };
}
