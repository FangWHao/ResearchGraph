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
function authenticatedFetch(path: string, body?: unknown, signal?: AbortSignal) {
  return fetch(`/api${path}`, {
    method: body ? 'POST' : 'GET',
    headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store', signal,
  });
}
export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await authenticatedFetch(path, body, signal);
  const result: unknown = await response.json();
  if (!response.ok) {
    const message = result && typeof result === 'object' && 'error' in result
      ? String(result.error) : `本地服务返回 ${response.status}`;
    throw new ApiError(response.status, message);
  }
  return result as T;
}
export async function apiArchive(body: unknown, signal?: AbortSignal): Promise<Blob> {
  const response = await authenticatedFetch('/exports', body, signal);
  if (!response.ok) {
    let message = `本地服务返回 ${response.status}`;
    try {
      const value: unknown = await response.json();
      if (value && typeof value === 'object' && 'error' in value) message = String(value.error);
    } catch { /* 错误响应不是 JSON 时仍保留 HTTP 状态。 */ }
    throw new ApiError(response.status, message);
  }
  if (response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/zip') throw new Error('导出返回的文件类型异常，未下载此响应。');
  const blob = await response.blob();
  const signature = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
  if (signature.length !== 4 || signature[0] !== 0x50 || signature[1] !== 0x4b || signature[2] !== 0x03 || signature[3] !== 0x04) throw new Error('导出返回的压缩包格式异常，未下载此响应。');
  return blob;
}
export function query(values: Record<string, string | number | null | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value != null && value !== '') params.set(key, String(value));
  return params.toString();
}
