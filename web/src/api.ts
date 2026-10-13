import type {
  AppState,
  Kind,
  Message,
  ProjectData,
  SessionInfo,
  Status,
  Workspace,
} from './types';

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'X-RG': '1' };
  let payload: BodyInit | undefined;
  if (body instanceof Blob) {
    payload = body;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const response = await fetch(path, { method, headers, body: payload });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error((data as { error?: string }).error ?? `请求失败（${response.status}）`);
  }
  return data as T;
}

export interface SettingsInput {
  sources?: string[];
  api_key?: string;
  base_url?: string;
  model?: string;
}

export interface NewProjectInput {
  name: string;
  workspace?: string;
  follow?: boolean;
  sessions?: string[];
  start?: boolean;
}

export interface EventPatch {
  title?: string;
  summary?: string;
  kind?: Kind;
  branch_id?: number;
  hidden?: boolean;
}

export const api = {
  state: () => request<AppState>('GET', '/api/state'),
  saveSettings: (input: SettingsInput) => request<AppState>('POST', '/api/settings', input),
  checkModel: (input: SettingsInput) =>
    request<{ ok: boolean; message: string }>('POST', '/api/settings/check', input),
  sessions: () => request<{ sessions: SessionInfo[]; workspaces: Workspace[] }>('GET', '/api/sessions'),
  createProject: (input: NewProjectInput) => request<{ id: number }>('POST', '/api/projects', input),
  upload: (projectId: number, file: File) =>
    request<{ added: number; title: string | null }>(
      'POST',
      `/api/projects/${projectId}/upload?name=${encodeURIComponent(file.name)}`,
      file,
    ),
  deleteProject: (projectId: number) => request<{ ok: boolean }>('DELETE', `/api/projects/${projectId}`),
  project: (projectId: number) => request<ProjectData>('GET', `/api/projects/${projectId}`),
  status: (projectId: number) => request<Status>('GET', `/api/projects/${projectId}/status`),
  organize: (projectId: number, options: { consent?: boolean; full?: boolean } = {}) =>
    request<Status>('POST', `/api/projects/${projectId}/organize`, options),
  patchEvent: (eventId: number, patch: EventPatch) =>
    request<{ ok: boolean }>('PATCH', `/api/events/${eventId}`, patch),
  messages: (eventId: number, context: number) =>
    request<{ session: { id: number; title: string | null; tool: string; missing: number }; messages: Message[] }>(
      'GET',
      `/api/events/${eventId}/messages?context=${context}`,
    ),
  patchThread: (threadId: number, patch: { title?: string; summary?: string }) =>
    request<{ ok: boolean }>('PATCH', `/api/threads/${threadId}`, patch),
  createThread: (projectId: number, title: string) =>
    request<{ thread_id: number; branch_id: number }>('POST', `/api/projects/${projectId}/threads`, { title }),
  createBranch: (threadId: number, title: string, fromEvent: number | null) =>
    request<{ branch_id: number }>('POST', `/api/threads/${threadId}/branches`, {
      title,
      from_event: fromEvent,
    }),
  patchBranch: (
    branchId: number,
    patch: { title?: string; from_event?: number | null; into_event?: number | null },
  ) => request<{ ok: boolean }>('PATCH', `/api/branches/${branchId}`, patch),
};
