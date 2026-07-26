export const mutationHeaders = (idempotencyKey: string = crypto.randomUUID()) => ({ 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey });

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.method && init.method !== 'GET') {
    headers.set('Origin', window.location.origin);
    if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(`/api/v1${path}`, { ...init, headers, credentials: 'same-origin' });
  if (!response.ok) {
    const value = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail));
  }
  return response.json() as Promise<T>;
}
