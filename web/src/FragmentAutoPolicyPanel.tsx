import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { DateText } from './components';
import { assertAutoAssignmentContinuation, autoRootReasons, parseAutoAssignmentPage, parseAutoAssignmentReceipt } from './fragmentAutoPolicy';
import type { AutoAssignmentPage } from './fragmentAutoPolicy';
import './fragmentAutoPolicy.css';

export function FragmentAutoPolicyPanel({ project, epoch, actor, onError, onSaved }: {
  project: string; epoch: number; actor: string; onError: (error: unknown) => void; onSaved: () => void;
}) {
  const context = JSON.stringify([project, epoch, actor]);
  const [paging, setPaging] = useState<{ context: string; offset: number; revision?: number; scopeKey?: string; retry: number }>({ context, offset: 0, retry: 0 });
  const page = paging.context === context ? paging : { context, offset: 0, retry: 0 };
  const key = JSON.stringify([context, page.offset, page.revision, page.scopeKey, page.retry]);
  const current = useRef(key); current.current = key;
  const [response, setResponse] = useState<{ context: string; key: string; data: AutoAssignmentPage } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const [saving, setSaving] = useState('');
  const [notice, setNotice] = useState<{ context: string; message: string } | null>(null);
  const first = useRef<{ context: string; data: AutoAssignmentPage } | null>(null);
  const seen = useRef(new Map<number, string[]>());
  const reader = useRef<AbortController | null>(null);
  const action = useRef<{ key: string; enabled: boolean; controller: AbortController } | null>(null);
  const data = response?.context === context ? response.data : null;
  const error = failure?.key === key ? failure.message : '';
  const busy = response?.key !== key && !error, writing = saving === key;
  function cancel() { reader.current?.abort(); action.current?.controller.abort(); action.current = null; setSaving(''); }
  function refresh() { cancel(); setNotice(null); setPaging({ context, offset: 0, retry: page.retry + 1 }); }
  useEffect(() => {
    const controller = new AbortController(); reader.current = controller;
    action.current?.controller.abort(); action.current = null; setSaving(''); setFailure(null);
    const expected = { project, offset: page.offset, limit: 100, revision: page.revision, scopeKey: page.scopeKey };
    api<unknown>(`/project-assignment?${query({ project, limit: 100, offset: page.offset, expected_revision: page.revision, expected_scope_key: page.scopeKey })}`, undefined, controller.signal)
      .then(raw => {
        if (controller.signal.aborted || current.current !== key) return;
        const result = parseAutoAssignmentPage(raw, expected);
        if (page.offset > 0) {
          if (first.current?.context !== context) throw new Error('自动归属首屏规则身份缺失，请重新读取第一页。');
          assertAutoAssignmentContinuation(first.current.data, result);
        } else first.current = { context, data: result };
        if (result.available) {
          if (page.offset === 0 && page.revision === undefined) seen.current.clear();
          for (const [offset, ids] of seen.current) {
            if (offset !== page.offset && result.roots.some(root => ids.includes(root.root_id))) throw new Error('归属规则续页包含重复根，未混入此页；请重新读取第一页。');
          }
          seen.current.set(page.offset, result.roots.map(root => root.root_id));
        }
        setResponse({ context, key, data: result });
      }).catch((error: unknown) => {
        if (controller.signal.aborted || current.current !== key) return;
        setFailure({ key, message: error instanceof ApiError && error.status === 404 ? '此服务尚未提供自动归属配置，当前策略未知，不能开启。' : error instanceof Error ? error.message : '自动归属配置读取失败。' });
        if (error instanceof ApiError && error.status === 401) onError(error);
      });
    return () => { controller.abort(); action.current?.controller.abort(); action.current = null; };
  }, [project, context, key, page.offset, page.revision, page.scopeKey, onError]);
  async function configure(enabled: boolean) {
    if (!data?.available || busy || writing || error || !actor.startsWith('human:') || !actor.slice(6).trim()) return;
    const controller = new AbortController(), operation = { key, enabled, controller };
    action.current = operation; setSaving(key); setNotice(null);
    try {
      const raw = await api<unknown>('/project-assignment/configure', { project_id: project, enabled, actor, expected_revision: data.revision, expected_scope_key: data.scope_key }, controller.signal);
      if (controller.signal.aborted || current.current !== key || action.current !== operation) return;
      const result = parseAutoAssignmentReceipt(raw, data, actor, enabled);
      first.current = { context, data: result };
      setResponse({ context, key, data: result });
      setNotice({ context, message: enabled ? '未来记录自动归属规则已开启；保存策略不表示已有记录已分配或科研处理完成。' : '已停止本项目未来记录自动归属；已有归属、原文和科研历史保留。' });
      setPaging({ context, offset: 0, retry: page.retry + 1 }); onSaved();
    } catch (error: unknown) {
      if (controller.signal.aborted || current.current !== key || action.current !== operation) return;
      setFailure({ key, message: error instanceof Error ? error.message : '策略保存结果未知，请重新读取后核对。' });
      if (error instanceof ApiError && error.status === 401) onError(error);
    } finally { if (action.current === operation) { action.current = null; setSaving(''); } }
  }
  function navigate(offset: number) {
    if (!data?.available || busy || writing || error) return;
    cancel(); setNotice(null);
    setPaging({ context, offset, revision: data.revision, scopeKey: data.scope_key, retry: page.retry });
  }
  return <section className="panel fragment-auto-policy" aria-label="未来记录自动归属">
    <header><div><h3>未来记录自动归属</h3><span className="eyebrow">当前项目 · 本机目录规则</span></div><button className="button secondary" onClick={refresh}>重新读取归属规则</button></header>
    <p>默认关闭。开启后，仅新导入且未人工指定项目的记录，可按真实目录声明与已登记目录规则归属至本项目；未知或冲突进入收件箱。后续明确登记的目录、已授权工作树发现的新目录也纳入规则。</p>
    <p className="small muted">人工绑定的来源或会话保持人工归属。旧记录与科研状态不回写；停止仅暂停本项目未来自动归属。借用科研来源根不构成授权，跨项目目录重叠不自动决定归属。此功能不调用模型，也不产生科研确认。</p>
    {notice?.context === context && <p role="status" className="notice">{notice.message}</p>}
    {error && <p role="alert" className="error-message">{error}{data && ' 保留上次成功页，未混入失败页。'} 请主动重新读取规则第一页，核对最新版本后再点击保存；不会自动重发。</p>}
    {busy && <p role="status">正在读取归属规则…</p>}
    {data?.available === false && <p className="missing-note">自动归属配置不可用（{data.reason}），不能把缺失信息当作已关闭。</p>}
    {data?.available && <>
      <div className="fragment-auto-switch"><strong data-auto-assignment-enabled={String(data.enabled)}>未来自动归属{data.enabled ? '已开启' : '已关闭'}</strong>
        <button className="button secondary" disabled={busy || writing || !!error || !actor.slice(6).trim()} onClick={() => { void configure(!data.enabled); }}>{writing ? '正在保存规则…' : data.enabled ? '停止未来自动归属' : '开启未来自动归属'}</button></div>
      <p className="small muted">读取修订 {data.revision} · 操作人 {actor}</p>
      {data.policy ? <p data-auto-assignment-boundary className="small">策略 #{data.policy.action_id} · 由 {data.policy.actor} 于 <DateText value={data.policy.recorded_at} /> 保存。只适用于此后新导入的未人工归属记录；保存时事件边界 #{data.policy.after_event_id}，不是原件的发生时间截止。</p> : <p className="small muted">尚未保存项目规则，默认关闭；不会补分过去导入的记录。</p>}
      <p className="small muted">已登记目录 {data.total} · 本页 {data.roots.length} · 偏移 {data.offset}。适用性逐项显示，读到末页不代表采集或科研处理完成。</p>
      <ul className="fragment-auto-roots">{data.roots.map(root => <li key={root.root_id} data-auto-assignment-root={root.root_id}>
        <h4><code>{root.path}</code></h4><p data-auto-root-reason={root.reason}>{autoRootReasons[root.reason]}</p>
        <p className="small muted">登记类型 {({ auto: '工作区', repo: 'Git 仓库', worktree: 'Git 工作树', data: '数据目录', alias: '路径别名' } as Record<string, string>)[root.kind] ?? root.kind} · 根 ID <code>{root.root_id}</code></p>
      </li>)}</ul>
      {!data.roots.length && <p className="missing-note">本项目没有可显示的已登记目录；开启策略不会凭空产生目录规则。</p>}
      <div className="pagination"><button className="button secondary" disabled={busy || writing || !!error || data.offset === 0} onClick={() => navigate(Math.max(0, data.offset - 100))}>上一页归属规则</button>
        <button className="button secondary" disabled={busy || writing || !!error || data.next_offset == null} onClick={() => navigate(data.next_offset!)}>下一页归属规则</button></div>
    </>}
  </section>;
}
