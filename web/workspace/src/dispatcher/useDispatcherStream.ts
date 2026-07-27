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
  connectionState: 'connecting' | 'connected' | 'degraded';
  lastEvent: DispatcherWorkflowEvent | null;
  reconciliationToken: number;
}
export interface DispatcherWorkflowEvent {
  sequence: number;
  type?: string;
  run_id?: string;
  invocation_id?: string;
  phase_id?: string;
  role_id?: string;
  slot_id?: string;
  payload?: Record<string, unknown>;
}

export function useDispatcherStream({ projectId, initialProjection, initialFingerprint = '' }: DispatcherStreamOptions): DispatcherStreamState & { refresh: () => Promise<void> } {
  const [projection, setProjection] = useState<DispatcherProjection>(initialProjection ?? EMPTY_PROJECTION);
  const [fingerprint, setFingerprint] = useState(initialFingerprint);
  const [resyncing, setResyncing] = useState(!initialProjection);
  const [error, setError] = useState('');
  const [connectionState, setConnectionState] = useState<'connecting' | 'connected' | 'degraded'>('connecting');
  const [lastEvent, setLastEvent] = useState<DispatcherWorkflowEvent | null>(null);
  const [reconciliationToken, setReconciliationToken] = useState(0);
  const cursorRef = useRef(0);
  const closedRef = useRef(false);
  const revisionRef = useRef(initialProjection?.revision ?? 0);
  const hasBootstrapProjectionRef = useRef(Boolean(initialProjection));
  const activeRef = useRef<Promise<void> | null>(null);
  const pendingRef = useRef(false);
  const lastStartRef = useRef(Number.NEGATIVE_INFINITY);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestedRef = useRef(0);
  const completedRef = useRef(0);
  const waitersRef = useRef<Array<{ target: number; resolve: () => void }>>([]);
  const lastLoadSucceededRef = useRef(true);
  const resyncingRef = useRef(!initialProjection);
  const projectRef = useRef(projectId);
  const generationRef = useRef(0);

  const loadSnapshot = useCallback(async () => {
    const generation = generationRef.current;
    try {
      const snapshot = await api<SnapshotWithDispatcher>(`/projects/${projectId}/workflow`);
      if (generation !== generationRef.current) return;
      const next = snapshot.dispatcher ?? EMPTY_PROJECTION;
      if (next.revision < revisionRef.current) {
        setError(`snapshot revision ${next.revision} is older than current revision ${revisionRef.current}`);
        lastLoadSucceededRef.current = false;
        return;
      }
      revisionRef.current = next.revision;
      setFingerprint(snapshot.workflow_fingerprint);
      setProjection(next);
      setError('');
      lastLoadSucceededRef.current = true;
    } catch (fetchError) {
      if (generation !== generationRef.current) return;
      setError(fetchError instanceof Error ? fetchError.message : 'snapshot fetch failed');
      lastLoadSucceededRef.current = false;
    }
  }, [projectId]);

  const pump = useCallback(() => {
    if (closedRef.current || activeRef.current || !pendingRef.current) return;
    const delay = Math.max(0, 1000 - (Date.now() - lastStartRef.current));
    if (delay > 0) {
      if (!timerRef.current) timerRef.current = setTimeout(() => {
        timerRef.current = null;
        pump();
      }, delay);
      return;
    }
    pendingRef.current = false;
    lastStartRef.current = Date.now();
    const target = requestedRef.current;
    const request = loadSnapshot();
    activeRef.current = request;
    void request.finally(() => {
      activeRef.current = null;
      completedRef.current = Math.max(completedRef.current, target);
      const ready = waitersRef.current.filter((waiter) => waiter.target <= completedRef.current);
      waitersRef.current = waitersRef.current.filter((waiter) => waiter.target > completedRef.current);
      ready.forEach((waiter) => waiter.resolve());
      pump();
    });
  }, [loadSnapshot]);

  const queueRefresh = useCallback((waitForCompletion = true) => {
    const target = ++requestedRef.current;
    pendingRef.current = true;
    const complete = waitForCompletion
      ? new Promise<void>((resolve) => waitersRef.current.push({ target, resolve }))
      : Promise.resolve();
    pump();
    return complete;
  }, [pump]);

  const resync = useCallback(async () => {
    resyncingRef.current = true;
    setResyncing(true);
    await queueRefresh();
    if (lastLoadSucceededRef.current) {
      resyncingRef.current = false;
      setResyncing(false);
      return true;
    }
    return false;
  }, [queueRefresh]);
  const refresh = useCallback(async () => { await resync(); }, [resync]);

  useEffect(() => {
    closedRef.current = false;
    const projectChanged = projectRef.current !== projectId;
    if (projectChanged) {
      projectRef.current = projectId;
      generationRef.current += 1;
      cursorRef.current = 0;
      revisionRef.current = 0;
      hasBootstrapProjectionRef.current = false;
      lastLoadSucceededRef.current = true;
      resyncingRef.current = true;
      setProjection(EMPTY_PROJECTION);
      setFingerprint('');
      setResyncing(true);
      setError('');
      setConnectionState('connecting');
      setLastEvent(null);
      setReconciliationToken(0);
    }
    let eventSource: EventSource | null = null;
    const connect = () => {
      if (closedRef.current) return;
      try {
      // Существующий SSE-маршрут; передаём последний sequence как cursor
      eventSource = new EventSource(`/api/v1/projects/${projectId}/events/stream?cursor=${cursorRef.current}`, { withCredentials: true });
      eventSource.addEventListener('workflow', (event) => {
        try {
          const payload = JSON.parse((event as MessageEvent).data) as DispatcherWorkflowEvent;
          if (typeof payload.sequence !== 'number' || payload.sequence <= cursorRef.current) return;
          if (cursorRef.current > 0 && payload.sequence !== cursorRef.current + 1) {
            cursorRef.current = 0;
            void resync();
            return;
          }
          cursorRef.current = payload.sequence;
          setLastEvent(payload);
          void queueRefresh(false);
        } catch {
          // игнорируем мусорные события; снимок останется прежним
        }
      });
      eventSource.addEventListener('resync', () => {
        cursorRef.current = 0;
        void resync();
      });
      eventSource.addEventListener('open', () => {
        setConnectionState('connected');
        if (!resyncingRef.current) setError('');
      });
      eventSource.addEventListener('error', () => {
        // EventSource переподключается сам с Last-Event-ID; параллельный опрос запрещён.
        if (closedRef.current) return;
        setConnectionState('degraded');
        setError('Поток событий временно недоступен, выполняется переподключение');
      });
      } catch (sourceError) {
        setError(sourceError instanceof Error ? sourceError.message : 'event stream unavailable');
      }
    };
    // Родитель уже получил общий снимок; без него загружаем его перед SSE.
    if (!projectChanged && hasBootstrapProjectionRef.current) connect(); else void resync().then((loaded) => {
      if (loaded) connect();
    });
    return () => {
      closedRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
      waitersRef.current.splice(0).forEach((waiter) => waiter.resolve());
      if (eventSource) eventSource.close();
    };
  }, [projectId, queueRefresh, resync]);

  const workActive = Object.values(projection.jobs).some((lease) => lease.state === 'running')
    || Boolean(projection.agent_phases?.some((phase) => phase.roles.some((role) => role.running > 0 || role.queued > 0)));
  useEffect(() => {
    const delay = connectionState === 'degraded' ? 5_000 : connectionState === 'connected' && workActive ? 30_000 : 0;
    if (!delay) return;
    const timer = window.setInterval(() => {
      setReconciliationToken((value) => value + 1);
      void queueRefresh(false);
    }, delay);
    return () => clearInterval(timer);
  }, [connectionState, workActive, queueRefresh]);

  return { projection, fingerprint, resyncing, error, connectionState, lastEvent, reconciliationToken, refresh };
}
