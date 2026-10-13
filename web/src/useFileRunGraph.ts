import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { appendL1Page, emptyFileRunDraft, emptyL1Pages, fileRunData, fileRunIntent, fileRunRequest, fixedL1Query, l1Collections, parseL1Page, parseL1Window } from './fileRunGraph';
import type { FileRunData, FileRunDraft, L1Collection, L1EvidenceWindow, L1Metadata } from './fileRunGraph';

interface Workspace {
  draft: FileRunDraft; data: FileRunData | null; busy: boolean; error: string; conflict: boolean; info: string;
  progress: Partial<Record<L1Collection, number>>; event: number | null; windows: L1EvidenceWindow[]; evidenceBusy: boolean; evidenceError: string;
}
function empty(): Workspace { return { draft: emptyFileRunDraft(), data: null, busy: false, error: '', conflict: false, info: '', progress: {}, event: null, windows: [], evidenceBusy: false, evidenceError: '' }; }
export function useFileRunGraph({ project, active, authorized, epoch, onError }: { project: string; active: boolean; authorized: boolean; epoch: number; onError: (error: unknown) => void }) {
  const [workspaces, setWorkspaces] = useState<Record<string, Workspace>>({}); const values = useRef(workspaces);
  const context = useRef({ project, active, authorized, epoch, generation: 0, intent: '' });
  const callbacks = useRef(onError); callbacks.current = onError;
  const graphRequest = useRef<{ project: string; generation: number; intent: string; controller: AbortController } | null>(null);
  const evidenceRequest = useRef<AbortController | null>(null);
  const draft = workspaces[project]?.draft ?? emptyFileRunDraft(); const intent = fileRunIntent(project, draft);
  if (context.current.project !== project || context.current.active !== active || context.current.authorized !== authorized || context.current.epoch !== epoch) context.current.generation++;
  context.current = { project, active, authorized, epoch, generation: context.current.generation, intent };
  function update(id: string, patch: Partial<Workspace>) { values.current = { ...values.current, [id]: { ...(values.current[id] ?? empty()), ...patch } }; setWorkspaces(values.current); }
  function cancelRequests() {
    const pending = graphRequest.current; pending?.controller.abort(); graphRequest.current = null;
    evidenceRequest.current?.abort(); evidenceRequest.current = null;
    if (pending) update(pending.project, { busy: false });
  }
  function valid(pending: NonNullable<typeof graphRequest.current>) { return graphRequest.current === pending && !pending.controller.signal.aborted && context.current.authorized && context.current.active && context.current.generation === pending.generation && context.current.intent === pending.intent; }
  async function load() {
    if (!context.current.authorized || !context.current.active || !project || graphRequest.current) return;
    const workspace = values.current[project] ?? empty(); const capturedIntent = fileRunIntent(project, workspace.draft);
    const pending = { project, generation: context.current.generation, intent: capturedIntent, controller: new AbortController() }; graphRequest.current = pending;
    const pages = emptyL1Pages(); let first: L1Metadata | undefined;
    try {
      const parameters = fileRunRequest(project, workspace.draft);
      evidenceRequest.current?.abort(); evidenceRequest.current = null;
      update(project, { busy: true, error: '', conflict: false, info: '', progress: {}, event: null, windows: [], evidenceBusy: false, evidenceError: '' });
      for (const collection of l1Collections) {
        let offset = 0;
        while (true) {
          const response = await api<unknown>(`/l1-graph?${query({ ...(first ? fixedL1Query(first) : parameters), collection, limit: 100, offset })}`, undefined, pending.controller.signal);
          if (!valid(pending)) return;
          const page = parseL1Page(response, project, collection, offset, first); first ??= page;
          appendL1Page(pages, page);
          update(project, { progress: Object.fromEntries(l1Collections.map(key => [key, pages[key].length])) });
          if (page.next_offset === null) break; offset = page.next_offset;
        }
      }
      if (valid(pending) && first) update(project, { data: fileRunData(first, pages, capturedIntent, true), info: '已读取五个集合的全部登记记录。实际运行输入输出完整性仍未知。' });
    } catch (error) {
      if (!valid(pending)) return;
      update(project, { error: error instanceof Error ? error.message : '读取文件运行图失败。', conflict: error instanceof ApiError && error.status === 409,
        ...(!workspace.data && first ? { data: fileRunData(first, pages, capturedIntent, false) } : {}) });
      if (error instanceof ApiError && error.status === 401) callbacks.current(error);
    } finally { if (graphRequest.current === pending) { graphRequest.current = null; update(project, { busy: false }); } }
  }
  useEffect(() => {
    const pending = graphRequest.current;
    if (pending && pending.generation !== context.current.generation) { pending.controller.abort(); graphRequest.current = null; update(pending.project, { busy: false }); }
    evidenceRequest.current?.abort(); evidenceRequest.current = null;
    if (active && authorized && project && !(values.current[project]?.conflict)) void load();
    return () => { graphRequest.current?.controller.abort(); graphRequest.current = null; evidenceRequest.current?.abort(); evidenceRequest.current = null; };
  }, [project, active, authorized, epoch]);
  function change(next: FileRunDraft) {
    cancelRequests(); context.current.intent = fileRunIntent(project, next);
    update(project, { draft: next, error: '', event: null, windows: [], evidenceBusy: false, evidenceError: '' });
  }
  async function evidence(event: number, more = false) {
    if (evidenceRequest.current || !authorized || !active) return;
    const workspace = values.current[project] ?? empty(); const data = workspace.data;
    if (!data || !data.evidence.some(item => item.event_id === event)) return;
    const previous = more && workspace.event === event ? workspace.windows : []; const offset = previous.at(-1)?.next_byte_offset ?? 0;
    if (more && previous.at(-1)?.next_byte_offset == null) return;
    const controller = new AbortController(); evidenceRequest.current = controller; const generation = context.current.generation;
    const current = () => evidenceRequest.current === controller && !controller.signal.aborted && context.current.active && context.current.authorized && context.current.generation === generation && values.current[project]?.data === data;
    try {
      update(project, { event, windows: previous, evidenceBusy: true, evidenceError: '' });
      const response = await api<unknown>(`/l1-evidence?${query({ project, event_id: event, occurred_until: data.occurred_until, known_until: data.known_until, expected_revision: data.revision, byte_offset: offset, max_bytes: 4000 })}`, undefined, controller.signal);
      if (!current()) return;
      const window = parseL1Window(response, data, event, offset);
      const bytes = new TextEncoder().encode(window.text); const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(n => n.toString(16).padStart(2, '0')).join('');
      if (!current()) return; if (hash !== window.window_sha256) throw new Error('原文窗口摘要不符，未加入此窗口。');
      update(project, { windows: [...previous, window] });
    } catch (error) {
      if (!current()) return;
      update(project, { evidenceError: error instanceof Error ? error.message : '原文读取失败，已有窗口保留。', ...(error instanceof ApiError && error.status === 409 ? { conflict: true } : {}) });
      if (error instanceof ApiError && error.status === 401) callbacks.current(error);
    } finally { if (evidenceRequest.current === controller) { evidenceRequest.current = null; update(project, { evidenceBusy: false }); } }
  }
  const workspace = workspaces[project] ?? empty();
  return { ...workspace, stale: workspace.data != null && workspace.data.intent !== intent, change, load,
    cancel: () => { cancelRequests(); update(project, { busy: false, evidenceBusy: false, info: '已停止页面等待，已有图保留；没有调用模型或执行历史命令。' }); },
    evidence, closeEvidence: () => { evidenceRequest.current?.abort(); evidenceRequest.current = null; update(project, { event: null, windows: [], evidenceBusy: false, evidenceError: '' }); },
  };
}
