import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { parsePrivacyPolicy, privacyIntent, privacyRequest } from './privacy';
import type { PrivacyPolicy } from './privacy';

interface Workspace { saved: PrivacyPolicy | null; draft: string | null; loading: boolean; busy: boolean; error: string; info: string; conflict: boolean }
function empty(): Workspace { return { saved: null, draft: null, loading: false, busy: false, error: '', info: '', conflict: false }; }
export function useProjectPrivacy({ project, actor, active, authorized, epoch, onError, onPolicyChange }: {
  project: string; actor: string; active: boolean; authorized: boolean; epoch: number;
  onError: (error: unknown) => void; onPolicyChange: (project: string) => void;
}) {
  const [workspaces, setWorkspaces] = useState<Record<string, Workspace>>({});
  const values = useRef(workspaces); const [reload, setReload] = useState(0);
  const context = useRef({ project, actor, active, authorized, generation: 0 });
  const callbacks = useRef({ onError, onPolicyChange }); callbacks.current = { onError, onPolicyChange };
  const pending = useRef<{ project: string; intent: string; generation: number; controller: AbortController } | null>(null);
  useEffect(() => () => { pending.current?.controller.abort(); pending.current = null; }, []);
  if (context.current.project !== project || context.current.active !== active || context.current.authorized !== authorized || context.current.actor !== actor) context.current.generation++;
  context.current = { project, actor, active, authorized, generation: context.current.generation };
  function update(id: string, patch: Partial<Workspace>) {
    values.current = { ...values.current, [id]: { ...(values.current[id] ?? empty()), ...patch } };
    setWorkspaces(values.current);
  }
  useEffect(() => {
    if (pending.current && pending.current.generation !== context.current.generation) {
      const old = pending.current; old.controller.abort(); pending.current = null;
      update(old.project, { busy: false, info: '已停止页面等待。保存可能已在服务端完成，返回此项目后请核对当前规则。' });
    }
  }, [project, active, authorized, actor]);
  useEffect(() => {
    if (!project || !active || !authorized) return;
    const controller = new AbortController(); const generation = context.current.generation;
    update(project, { loading: true });
    api<unknown>(`/privacy?${query({ project })}`, undefined, controller.signal).then(response => {
      if (controller.signal.aborted || context.current.generation !== generation) return;
      const saved = parsePrivacyPolicy(response, project); const workspace = values.current[project] ?? empty();
      if (workspace.saved && workspace.saved.policy_id !== saved.policy_id) callbacks.current.onPolicyChange(project);
      update(project, { saved, draft: workspace.draft ?? saved.patterns.join('\n'), loading: false, conflict: false, error: '' });
    }).catch(error => {
      if (controller.signal.aborted || context.current.generation !== generation) return;
      update(project, { loading: false, error: error instanceof Error ? error.message : '读取项目遮盖规则失败。' });
      if (error instanceof ApiError && error.status === 401) callbacks.current.onError(error);
    });
    return () => controller.abort();
  }, [project, actor, active, authorized, epoch, reload]);
  function valid(request: NonNullable<typeof pending.current>) {
    return pending.current === request && !request.controller.signal.aborted && context.current.generation === request.generation
      && context.current.authorized && context.current.active && privacyIntent(context.current.project, values.current[request.project]?.draft ?? '', context.current.actor) === request.intent;
  }
  async function save() {
    const workspace = values.current[project] ?? empty();
    if (pending.current || workspace.conflict || workspace.loading || !active || !authorized) return;
    const request = { project, intent: privacyIntent(project, workspace.draft ?? '', actor), generation: context.current.generation, controller: new AbortController() };
    pending.current = request;
    try {
      const body = privacyRequest(project, workspace.draft ?? '', actor, workspace.saved?.revision);
      update(project, { busy: true, error: '', info: '' });
      const saved = parsePrivacyPolicy(await api<unknown>('/privacy', body, request.controller.signal), project);
      if (JSON.stringify(saved.patterns) !== JSON.stringify(body.patterns)) throw new Error('保存回执的规则与本次提交不一致，请读取当前配置核对；草稿已保留。');
      // 服务端成功仍使旧预览失效；只有本次意图才能更新当前草稿和提示。
      if (!request.controller.signal.aborted && context.current.authorized) callbacks.current.onPolicyChange(project);
      if (!valid(request)) return;
      update(project, { saved, draft: saved.patterns.join('\n'), conflict: false, info: '项目规则已保存。请重新查看发送预览；历史导出将使用新规则和最新版本。已有外发许可保持原状态。' });
    } catch (error) {
      if (!valid(request)) return;
      update(project, { error: error instanceof Error ? error.message : '保存失败，草稿已保留。', conflict: error instanceof ApiError && error.status === 409 });
      if (error instanceof ApiError && error.status === 401) callbacks.current.onError(error);
    } finally {
      if (pending.current === request) { pending.current = null; update(project, { busy: false }); }
    }
  }
  const workspace = workspaces[project] ?? empty();
  return { ...workspace, draft: workspace.draft ?? '',
    dirty: workspace.saved != null && workspace.draft !== workspace.saved.patterns.join('\n'),
    change: (draft: string) => update(project, { draft, error: '', info: '' }), save,
    refresh: () => { setReload(previous => previous + 1); },
  };
}
