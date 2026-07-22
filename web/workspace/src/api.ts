export type Stage = {
  id: string; title: string; kind: 'deterministic' | 'agent'; dependencies: string[];
  status: string; ready: boolean; blockers: string[]; progress?: number; run_id?: string;
  artifact?: string; configuration_schema?: Record<string, unknown>; prompt_default?: string;
  agent_profile?: { id: string; name?: string; provider?: string; model?: string };
};
export type Workflow = { project: { id: string; name: string; root: string; manifest_hash: string; setup: Record<string, unknown> }; stages: Stage[]; generated_at: string };

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api/v1${path}`, { ...init, headers, credentials: 'same-origin' });
  const value = response.status === 204 ? null : await response.json().catch(() => ({ detail: response.statusText }));
  if (!response.ok) throw new Error(typeof value?.detail === 'string' ? value.detail : JSON.stringify(value?.detail || { status: response.status }));
  return value as T;
}

export function mutationHeaders(id = crypto.randomUUID()): HeadersInit {
  return { 'Idempotency-Key': id };
}

export function streamProject(projectId: string, onEvent: (event: MessageEvent, id: number) => void, onState: (online: boolean) => void): () => void {
  const source = new EventSource(`/api/v1/events?project_id=${encodeURIComponent(projectId)}`);
  const seen = new Set<number>(); let timer = 0; let queued: [MessageEvent, number] | undefined;
  const handle = (event: MessageEvent) => {
    const id = Number(event.lastEventId || 0);
    if (id && seen.has(id)) return;
    if (id) seen.add(id);
    queued = [event, id];
    if (!timer) timer = window.setTimeout(() => { timer = 0; if (queued) onEvent(...queued); queued = undefined; }, 250);
  };
  ['run.queued', 'run.started', 'stage.progress', 'log.append', 'approval.required', 'artifact.ready', 'run.completed', 'run.failed', 'run.cancelled', 'run.interrupted', 'reset'].forEach(type => source.addEventListener(type, handle as EventListener));
  source.onopen = () => onState(true);
  source.onerror = () => onState(false);
  return () => { source.close(); clearTimeout(timer); };
}
