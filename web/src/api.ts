export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

let token = '';
export function readToken(): string {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  const supplied = params.get('token');
  if (supplied) {
    sessionStorage.setItem('rg_token', supplied);
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  }
  token = supplied ?? sessionStorage.getItem('rg_token') ?? '';
  return token;
}
export function setToken(value: string): void {
  token = value.trim();
  sessionStorage.setItem('rg_token', token);
}
export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body ? 'POST' : 'GET',
    headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store', signal,
  });
  const result: unknown = await response.json();
  if (!response.ok) {
    const message = result && typeof result === 'object' && 'error' in result
      ? String(result.error) : `本地服务返回 ${response.status}`;
    throw new ApiError(response.status, message);
  }
  return result as T;
}
export function query(values: Record<string, string | number | null | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value != null && value !== '') params.set(key, String(value));
  return params.toString();
}
