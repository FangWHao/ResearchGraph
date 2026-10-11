import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { DateText } from './components';
import { assertWorktreeContinuation, discoveryCounts, discoveryNames, parseWorktreePolicy, parseWorktrees } from './worktrees';
import type { WorktreePage } from './worktrees';
import './worktrees.css';

export function WorktreePanel({ project, epoch, onError }: { project: string; epoch: number; onError: (error: unknown) => void }) {
  const context = JSON.stringify([project, epoch]);
  const [page, setPage] = useState<{ context: string; offset: number; revision?: number }>({ context, offset: 0 });
  const [retry, setRetry] = useState(0), [response, setResponse] = useState<{ context: string; key: string; data: WorktreePage } | null>(null);
  const [failure, setFailure] = useState<{ context: string; message: string } | null>(null), [pending, setPending] = useState('');
  const [saving, setSaving] = useState(''), [notice, setNotice] = useState<{ context: string; message: string } | null>(null);
  const request = page.context === context ? page : { context, offset: 0 };
  const key = JSON.stringify([context, request.offset, request.revision, retry]);
  const current = useRef(key); current.current = key;
  const first = useRef<{ context: string; data: WorktreePage } | null>(null);
  const action = useRef<{ key: string; controller: AbortController } | null>(null);
  const data = response?.context === context ? response.data : null;
  const error = failure?.context === context ? failure.message : '';
  const busy = pending === key || (!error && response?.key !== key), writing = saving === key;
  function cancelAction() { action.current?.controller.abort(); action.current = null; setSaving(''); }
  function refresh() { cancelAction(); setPage({ context, offset: 0 }); setRetry(value => value + 1); setNotice(null); }
  useEffect(() => {
    const controller = new AbortController(); cancelAction(); setPending(key); setFailure(null);
    const expected = { project, offset: request.offset, limit: 50, revision: request.revision };
    api<unknown>(`/worktrees?${query({ project, limit: 50, offset: request.offset, expected_revision: request.revision })}`, undefined, controller.signal)
      .then(raw => {
        if (controller.signal.aborted || current.current !== key) return;
        const result = parseWorktrees(raw, expected);
        if (expected.revision != null) {
          if (first.current?.context !== context) throw new Error('工作树首屏读取身份缺失，请重新读取第一页。');
          assertWorktreeContinuation(first.current.data, result);
        } else first.current = { context, data: result };
        setResponse({ context, key, data: result });
      }).catch((error: unknown) => {
        if (controller.signal.aborted || current.current !== key) return;
        setFailure({ context, message: error instanceof Error ? error.message : '工作树读取失败，发现状态未知。' });
        if (error instanceof ApiError && error.status === 401) onError(error);
      }).finally(() => { if (!controller.signal.aborted && current.current === key) setPending(''); });
    return () => { controller.abort(); action.current?.controller.abort(); action.current = null; };
  }, [project, context, key, request.offset, request.revision, onError]);
  async function follow(enabled: boolean) {
    if (!data || busy || writing || error) return;
    const controller = new AbortController(), intent = { key, controller }; action.current = intent; setSaving(key); setNotice(null);
    try {
      const result = await api<unknown>('/worktrees/follow', { project_id: project, follow_enabled: enabled, expected_revision: data.revision }, controller.signal);
      if (controller.signal.aborted || current.current !== key || action.current !== intent) return;
      parseWorktreePolicy(result, project, enabled, data.revision);
      setPage({ context, offset: 0 }); setRetry(value => value + 1);
      setNotice({ context, message: enabled ? '自动跟踪策略已开启。新工作树由扫描服务下一轮发现，请稍后重新读取。' : '已停止未来工作树发现；已有根目录和历史记录保留。' });
    } catch (error) {
      if (controller.signal.aborted || current.current !== key || action.current !== intent) return;
      setFailure({ context, message: error instanceof Error ? error.message : '策略保存失败，当前开关未核定；请重新读取。' });
      if (error instanceof ApiError && error.status === 401) onError(error);
    } finally { if (action.current === intent) { action.current = null; setSaving(''); } }
  }
  return <section className="panel worktree-panel" aria-label="工作树自动跟踪">
    <header><div><h3>工作树自动跟踪</h3><span className="eyebrow">当前项目 · 本机登记</span></div><button className="button secondary" onClick={refresh}>重新读取工作树</button></header>
    <p className="health-explanation">默认关闭。开启后，扫描服务可登记与本项目共用同一 Git 共同目录、且项目归属唯一的新工作树，并允许其后续提示快照。停止只暂停未来发现，已有根和历史保留。工作目录不改变会话归属；相同远端不证明同一项目。</p>
    <p className="small muted">这里只核对 Git 元数据，不读取研究文件、不执行会话命令、不调用模型；登记与发现状态不代表科研确认或实际输入输出完整。</p>
    {notice?.context === context && <p className="notice" role="status">{notice.message}</p>}
    {error && <p className="error-message" role="alert">{error}{data && ' 保留上次成功页，未混入失败页。'} 请主动重新读取工作树，核对最新版本后再操作。</p>}
    {busy && <p role="status">正在读取工作树…</p>}
    {data && <>
      <div className="worktree-policy"><strong data-worktree-policy={String(data.follow_enabled)}>自动跟踪{data.follow_enabled ? '已开启' : '已关闭'}</strong>
        <button className="button secondary" disabled={busy || writing || !!error} onClick={() => { void follow(!data.follow_enabled); }}>{writing ? '正在保存策略…' : data.follow_enabled ? '停止自动跟踪' : '开启自动跟踪'}</button>
        <span className="small muted">当前版本 {data.revision} · {data.policy ? <>策略 #{data.policy.action_id} · <DateText value={data.policy.recorded_at} /></> : '尚未保存开启策略'}</span></div>
      <p className="small muted">已登记 Git 根目录 {data.total} · 本页 {data.roots.length} · 偏移 {data.offset}。下列计数分别属于每个根最近核对的清单页，不能相加为全清单或全项目统计。</p>
      <ul className="worktree-roots">{data.roots.map(root => <li key={root.root_id} data-worktree-root={root.root_id}>
        <h4>{root.kind === 'worktree' ? 'Git 工作树' : 'Git 仓库'}<code>{root.path}</code></h4>
        <p className="small muted">根 ID <code>{root.root_id}</code></p>
        <p data-discovery-status={root.discovery.status}><strong>{discoveryNames[root.discovery.status]}</strong></p>
        {root.discovery.recorded_at && <p className="small muted">最近状态入库 <DateText value={root.discovery.recorded_at} /></p>}
        {root.discovery.status === 'not_observed' && <p className="missing-note">发现情况未知，不能据此认为没有新工作树；开启只保存策略，不立即执行发现。</p>}
        {root.discovery.status === 'ambiguous' && <p className="missing-note">该 Git 共同目录对应多个项目，不自动登记或合并，不公开其它项目身份。</p>}
        {root.discovery.details && <>
          <dl className="worktree-counts">{discoveryCounts.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{typeof root.discovery.details?.[key] === 'number' ? String(root.discovery.details[key]) : '未提供'}</dd></div>)}</dl>
          <p className="small muted">清单登记条目 {root.discovery.details.list_total == null ? '未提供' : String(root.discovery.details.list_total)} · 本次核对条目 {root.discovery.details.page_count == null ? '未提供' : String(root.discovery.details.page_count)} · 清单是否读至末页：{root.discovery.details.list_complete === true ? '是' : root.discovery.details.list_complete === false ? '否，仍有缺口' : '未知'}。即使读至末页，也不代表所有路径均可登记。</p>
        </>}
        <details><summary>登记时的 Git 身份</summary><p className="small">Git 元数据状态：{root.git_metadata.status === 'registered' ? '已登记' : '未知或不可用'}</p>
          <p className="small">共同目录 <code>{root.git_common_dir ?? '未记录'}</code></p><p className="small muted">身份保留为登记时信息；当前发现观测不能覆盖旧登记，也不能证明会话归属。</p></details>
      </li>)}</ul>
      {data.roots.length === 0 && <p className="missing-note">本项目没有已登记的 Git 仓库或工作树；发现情况未知。</p>}
      <div className="pagination"><button className="button secondary" disabled={busy || writing || !!error || data.offset === 0} onClick={() => setPage({ context, offset: Math.max(0, data.offset - 50), revision: data.revision })}>上一页工作树</button>
        <button className="button secondary" disabled={busy || writing || !!error || data.next_offset == null} onClick={() => setPage({ context, offset: data.next_offset!, revision: data.revision })}>下一页工作树</button></div>
    </>}
  </section>;
}
