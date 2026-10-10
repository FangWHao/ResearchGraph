import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { emptyQaDraft, parseQaPacket, parseQaPreview, qaIntent, qaRequest } from './qa';
import type { QaDraft, QaOptions, QaPacket, QaPreview } from './qa';

interface Workspace {
  draft: QaDraft; options: QaOptions | null; result: QaPacket | null; resultIntent: string | null;
  preview: QaPreview | null; previewIntent: string | null; consent: boolean;
  busy: string | null; error: string; info: string; conflict: boolean;
}
function empty(): Workspace { return { draft: emptyQaDraft(), options: null, result: null, resultIntent: null, preview: null, previewIntent: null, consent: false, busy: null, error: '', info: '', conflict: false }; }
function optionsFor(value: QaOptions, project: string): QaOptions {
  if (value.project_id !== project || !Number.isInteger(value.revision) || typeof value.configured !== 'boolean'
    || typeof value.remote_allowed !== 'boolean' || ![true, false, null].includes(value.remote)) throw new Error('模型配置状态未知；可以刷新后进行本地检索。');
  return value;
}
export function useQaWorkspace({ project, active, authorized, epoch, onError, onPermissionChange }: {
  project: string; active: boolean; authorized: boolean; epoch: number;
  onError: (error: unknown) => void; onPermissionChange: () => void;
}) {
  const [workspaces, setWorkspaces] = useState<Record<string, Workspace>>({});
  const values = useRef(workspaces); const context = useRef({ project, authorized, active, generation: 0 });
  const request = useRef<{ project: string; intent: string; id: number; auth: number; controller: AbortController } | null>(null);
  const serial = useRef(0);
  if (context.current.authorized !== authorized) context.current.generation++;
  context.current = { project, authorized, active, generation: context.current.generation };
  function update(id: string, patch: Partial<Workspace>) {
    values.current = { ...values.current, [id]: { ...(values.current[id] ?? empty()), ...patch } };
    setWorkspaces(values.current);
  }
  useEffect(() => {
    const pending = request.current;
    if (pending && (pending.project !== project || !authorized || !active)) {
      pending.controller.abort(); request.current = null;
      update(pending.project, { busy: null, info: '已停止页面等待；已发送的模型请求可能仍在服务端处理。' });
    }
  }, [project, authorized, active]);
  useEffect(() => {
    if (!project || !authorized || !active) return;
    const controller = new AbortController(); const auth = context.current.generation;
    api<QaOptions>(`/qa/options?${query({ project })}`, undefined, controller.signal).then(value => {
      if (!controller.signal.aborted && context.current.project === project && context.current.generation === auth) update(project, { options: optionsFor(value, project) });
    }).catch(error => {
      if (!controller.signal.aborted && context.current.project === project && context.current.generation === auth) {
        update(project, { error: error instanceof Error ? error.message : '读取配置失败' });
        if (error instanceof ApiError && error.status === 401) onError(error);
      }
    });
    return () => controller.abort();
  }, [project, authorized, active, epoch, onError]);
  function current(pending: NonNullable<typeof request.current>) {
    return request.current?.id === pending.id && !pending.controller.signal.aborted && context.current.authorized
      && context.current.active && context.current.project === pending.project && context.current.generation === pending.auth
      && qaIntent(pending.project, (values.current[pending.project] ?? empty()).draft) === pending.intent;
  }
  function changeDraft(draft: QaDraft) {
    update(project, { draft, preview: null, previewIntent: null, consent: false, error: '', conflict: false });
  }
  async function run(mode: 'query' | 'retrieve' | 'allow' | 'disable') {
    const workspace = values.current[project] ?? empty();
    if (workspace.busy || !authorized) return;
    const pending = { project, intent: qaIntent(project, workspace.draft), id: ++serial.current, auth: context.current.generation, controller: new AbortController() };
    request.current = pending;
    try {
      if (mode === 'disable') {
        if (!workspace.options) throw new Error('请先刷新模型配置。');
        update(project, { busy: '正在撤回项目外发许可…', error: '', conflict: false });
        const saved = await api<{ project_id: string; revision: number; remote_allowed: boolean }>('/qa/disable-remote', { project_id: project, expected_revision: workspace.options.revision }, pending.controller.signal);
        if (saved.project_id !== project || saved.remote_allowed !== false) throw new Error('撤回许可回执无效，请刷新配置核对。');
        if (context.current.authorized && context.current.generation === pending.auth) onPermissionChange();
        if (!current(pending)) return;
        update(project, { options: { ...workspace.options, revision: saved.revision, remote_allowed: false }, preview: null, previewIntent: null, consent: false, info: '项目外发许可已撤回；此前已发送的请求可能仍在处理。' });
        return;
      }
      const body = qaRequest(project, workspace.draft, workspace.options?.revision);
      let options = workspace.options;
      let revision = options?.revision;
      update(project, { busy: mode === 'allow' ? '正在核对预览并开启项目外发…' : '正在本地查找来源…', error: '', info: '', conflict: false });
      if (mode === 'allow') {
        if (!workspace.consent || !workspace.preview || workspace.previewIntent !== pending.intent || workspace.preview.remote !== true || !workspace.preview.source_count || !options?.configured) throw new Error('请查看当前发送预览并主动确认项目外发。');
        const saved = await api<{ project_id: string; revision: number; remote_allowed: boolean }>('/qa/allow-remote', { ...body, expected_revision: workspace.preview.revision, preview_sha: workspace.preview.preview_sha, allow: true }, pending.controller.signal);
        if (saved.project_id !== project || saved.remote_allowed !== true) throw new Error('项目外发许可回执无效，请刷新配置核对。');
        if (context.current.authorized && context.current.generation === pending.auth) onPermissionChange();
        if (!current(pending)) return;
        revision = saved.revision; options = { ...options, revision, remote_allowed: true };
        update(project, { options, preview: null, previewIntent: null, consent: false });
      } else {
        const local = await api<{ context_text: string }>('/qa/retrieve', body, pending.controller.signal);
        if (!current(pending)) return;
        const result = parseQaPacket(local.context_text, project, body.question);
        revision = result.revision;
        update(project, { result, resultIntent: pending.intent, preview: null, previewIntent: null, consent: false });
        if (!result.sources.length) { update(project, { info: '没有找到有效来源，无法据此判断。可调整问题、范围或截止时间。' }); return; }
        if (mode === 'retrieve') { update(project, { info: '已完成本地有界检索，此次未调用模型。' }); return; }
        if (!options?.configured) { update(project, { info: options ? '尚未配置模型，已显示本地检索来源。' : '模型配置状态未知，已显示本地检索来源；刷新后可核对配置。' }); return; }
        if (options.remote === true && !options.remote_allowed) {
          update(project, { busy: '正在本地生成发送预览…' });
          const response = await api<{ context_text: string }>('/qa/preview', { ...body, expected_revision: revision }, pending.controller.signal);
          if (!current(pending)) return;
          const preview = parseQaPreview(response.context_text, project);
          update(project, { preview, previewIntent: pending.intent, info: '本地来源已就绪。首次外发需要查看下方预览并主动开启项目许可。' }); return;
        }
        if (options.remote == null) { update(project, { info: '模型是否远程未知，已显示本地来源；刷新配置后再回答。' }); return; }
      }
      if (!current(pending)) return;
      update(project, { busy: '正在根据来源生成模型解释…' });
      const response = await api<{ context_text: string }>('/qa/answer', { ...body, expected_revision: revision }, pending.controller.signal);
      if (!current(pending)) return;
      const result = parseQaPacket(response.context_text, project, body.question, true);
      update(project, { result, resultIntent: pending.intent, info: '' });
    } catch (error) {
      if (current(pending)) {
        update(project, { error: error instanceof Error ? error.message : '问答请求失败', conflict: error instanceof ApiError && error.status === 409 });
        if (error instanceof ApiError && error.status === 401) onError(error);
      }
    } finally {
      if (request.current?.id === pending.id) { request.current = null; update(pending.project, { busy: null }); }
    }
  }
  function cancel() {
    const pending = request.current;
    if (!pending || pending.project !== project) return;
    pending.controller.abort(); request.current = null;
    update(project, { busy: null, info: '已停止页面等待；已发送的模型请求可能仍在服务端处理。' });
  }
  const workspace = workspaces[project] ?? empty();
  return { ...workspace, stale: workspace.resultIntent != null && workspace.resultIntent !== qaIntent(project, workspace.draft),
    changeDraft, consentChange: (consent: boolean) => update(project, { consent }),
    submit: () => run('query'), retrieve: () => run('retrieve'), allow: () => run('allow'), disable: () => run('disable'), cancel,
    refresh: () => { update(project, { conflict: false, error: '', preview: null, previewIntent: null, consent: false }); onPermissionChange(); },
  };
}
