import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { appendResearchPage, emptyResearchDraft, parseResearchPage, researchData, researchIntent, researchQuery, researchRequest } from './researchGraph';
import type { ResearchDraft, ResearchPage } from './researchGraph';
import type { Claim, ResearchGraphData } from './types';

interface Workspace { draft: ResearchDraft; data: ResearchGraphData | null; busy: boolean; received: number; total: number | null; error: string; conflict: boolean; info: string }
function empty(): Workspace { return { draft: emptyResearchDraft(), data: null, busy: false, received: 0, total: null, error: '', conflict: false, info: '' }; }
export function useResearchGraph({ project, active, authorized, epoch, onError }: { project: string; active: boolean; authorized: boolean; epoch: number; onError: (error: unknown) => void }) {
  const [workspaces, setWorkspaces] = useState<Record<string, Workspace>>({}); const values = useRef(workspaces);
  const current = useRef({ project, active, authorized, epoch, generation: 0, intent: '' });
  const callbacks = useRef(onError); callbacks.current = onError;
  const request = useRef<{ project: string; generation: number; intent: string; controller: AbortController } | null>(null);
  const draft = workspaces[project]?.draft ?? emptyResearchDraft(); const intent = researchIntent(project, draft);
  if (current.current.project !== project || current.current.active !== active || current.current.authorized !== authorized || current.current.epoch !== epoch) current.current.generation++;
  current.current = { project, active, authorized, epoch, generation: current.current.generation, intent };
  function update(id: string, patch: Partial<Workspace>) { values.current = { ...values.current, [id]: { ...(values.current[id] ?? empty()), ...patch } }; setWorkspaces(values.current); }
  function cancel() {
    const pending = request.current; pending?.controller.abort(); request.current = null;
    if (pending) update(pending.project, { busy: false });
  }
  function valid(pending: NonNullable<typeof request.current>) {
    return request.current === pending && !pending.controller.signal.aborted && current.current.authorized && current.current.active
      && current.current.project === pending.project && current.current.generation === pending.generation && current.current.intent === pending.intent;
  }
  async function load(options?: { draft?: ResearchDraft; expectedRevision?: number }): Promise<ResearchGraphData | null> {
    if (!current.current.authorized || !current.current.active || !project || request.current) return null;
    if (options?.draft) { current.current.intent = researchIntent(project, options.draft); update(project, { draft: options.draft }); }
    const workspace = values.current[project] ?? empty(); const capturedIntent = researchIntent(project, workspace.draft);
    const pending = { project, generation: current.current.generation, intent: capturedIntent, controller: new AbortController() }; request.current = pending;
    let first: ResearchPage | undefined; let claims: Claim[] = [];
    try {
      update(project, { busy: true, error: '', conflict: false, info: '', received: 0, total: null });
      const parameters = { ...researchRequest(project, workspace.draft), ...(options?.expectedRevision !== undefined ? { expected_revision: String(options.expectedRevision) } : {}) }; let offset = 0;
      while (true) {
        const response = await api<unknown>(`/semantic-graph?${query({ ...(first ? researchQuery(first) : parameters), collection: 'claims', limit: 100, offset })}`, undefined, pending.controller.signal);
        if (!valid(pending)) return null;
        const page = parseResearchPage(response, project, offset, first, parameters); first ??= page;
        if (options?.expectedRevision !== undefined && first.revision !== options.expectedRevision) throw new Error('保存视图的修订不再一致，未恢复。');
        claims = appendResearchPage(claims, page); update(project, { received: claims.length, total: first.total });
        if (page.next_offset === null) break; offset = page.next_offset;
      }
      if (valid(pending) && first) { const data = researchData(first, claims, capturedIntent); update(project, { data, info: '已读取该项目在双时间内的全部 L2 历史记录；文件运行与实际输入输出完整性另行核对。' }); return data; }
    } catch (error) {
      if (!valid(pending)) return null;
      update(project, { error: error instanceof Error ? error.message : '完整研究图读取失败，未混入旧图。', conflict: error instanceof ApiError && error.status === 409,
        ...(!workspace.data && first ? { data: researchData(first, claims, capturedIntent) } : {}) });
      if (error instanceof ApiError && error.status === 401) callbacks.current(error);
    } finally { if (request.current === pending) { request.current = null; update(project, { busy: false }); } }
    return null;
  }
  useEffect(() => {
    cancel();
    if (active && authorized && project) void load();
    return () => { const pending = request.current; pending?.controller.abort(); request.current = null; if (pending) update(pending.project, { busy: false }); };
  }, [project, active, authorized, epoch]);
  const workspace = workspaces[project] ?? empty();
  return { ...workspace, project, intent, stale: workspace.data !== null && workspace.data.intent !== intent, load,
    change: (next: ResearchDraft) => { cancel(); current.current.intent = researchIntent(project, next); update(project, { draft: next, error: '', conflict: false, info: '' }); },
    cancel: () => { cancel(); update(project, { info: '已停止页面等待；已有图保留，本次未读完的页不能证明完整边界。' }); } };
}
