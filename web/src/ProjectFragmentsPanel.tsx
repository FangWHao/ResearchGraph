import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { DateText, Icon } from './components';
import type { Project } from './types';
import { appendFragmentDependencies, assertFragmentContinuation, canAssignFragments, contextNames, dependencyIssueNames, directorySources, fragmentDependencyGroups, fragmentSelection, hintNames, hintReasons, parseFragmentDependencies, parseFragmentPage, parseFragmentReceipt, selectedFragmentRange } from './projectFragments';
import type { FragmentAssignmentIntent, FragmentDependencies, FragmentPage } from './projectFragments';
import './projectFragments.css';

export interface FragmentSource { session_pk: number; file_instance_id: number; path: string; tool: string }
export function ProjectFragmentsPanel({ project, epoch, actor, projects, source, onEvidence, onError, onAssigned, onClose }: {
  project: string; epoch: number; actor: string; projects: Project[]; source: FragmentSource;
  onEvidence: (target: { event_id: number }) => void; onError: (error: unknown) => void;
  onAssigned: (revision: number) => void; onClose: () => void;
}) {
  const context = JSON.stringify([project, epoch, actor, source.session_pk, source.file_instance_id]);
  const [paging, setPaging] = useState({ context, anchors: [0], index: 0, retry: 0 });
  const page = paging.context === context ? paging : { context, anchors: [0], index: 0, retry: 0 };
  const after = page.anchors[page.index], key = JSON.stringify([context, after, page.retry]);
  const [response, setResponse] = useState<{ context: string; key: string; data: FragmentPage } | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const [selection, setSelection] = useState<{ key: string; anchor: number | null; ids: number[] }>({ key, anchor: null, ids: [] });
  const [target, setTarget] = useState(''), [confirmed, setConfirmed] = useState(false), [saving, setSaving] = useState('');
  const [dependency, setDependency] = useState<{ identity: string; data: FragmentDependencies } | null>(null);
  const [dependencyError, setDependencyError] = useState<{ identity: string; message: string } | null>(null), [inspecting, setInspecting] = useState('');
  const current = useRef(''), readKey = useRef(key), first = useRef<{ context: string; data: FragmentPage } | null>(null);
  readKey.current = key;
  const action = useRef<{ controller: AbortController; identity: string } | null>(null);
  const intended = useRef<{ identity: string; value: FragmentAssignmentIntent } | null>(null);
  const data = response?.context === context ? response.data : null, failure = error?.key === key ? error.message : '';
  const ids = selection.key === key ? selection.ids : [], range = selectedFragmentRange(data?.records ?? [], ids);
  const identity = JSON.stringify([key, actor, ids, target]); current.current = identity;
  const busy = response?.key !== key && !failure, writing = saving === identity;
  const dependencies = dependency?.identity === identity ? dependency.data : null;
  const dependencyFailure = dependencyError?.identity === identity ? dependencyError.message : '';
  const checking = inspecting === identity, dependencyBlocked = !!dependencyFailure || dependencies?.assignment_available === false;
  function cancel(clearDependencies = true) { action.current?.controller.abort(); action.current = null; setSaving(''); setInspecting(''); if (clearDependencies) { setDependency(null); setDependencyError(null); } }
  function refresh() { cancel(); setConfirmed(false); setSelection({ key: '', anchor: null, ids: [] }); setPaging({ context, anchors: [0], index: 0, retry: page.retry + 1 }); }
  useEffect(() => {
    const controller = new AbortController(); cancel(); setError(null); setConfirmed(false);
    const fixed = after === 0 ? null : first.current?.context === context ? first.current.data : null;
    if (after && !fixed) { setError({ key, message: '片段首屏截止缺失，请重新读取第一页。' }); return () => controller.abort(); }
    const expected = { session: source.session_pk, source: source.file_instance_id, after, limit: 20 };
    api<unknown>(`/project-fragments?${query({ session: source.session_pk, source: source.file_instance_id, limit: 20, after_event: after, snapshot: fixed?.snapshot, observation_snapshot: fixed?.observation_snapshot, expected_scope_key: fixed?.scope_key })}`, undefined, controller.signal)
      .then(raw => {
        if (controller.signal.aborted || readKey.current !== key) return;
        const result = parseFragmentPage(raw, expected);
        if (fixed) assertFragmentContinuation(fixed, result); else first.current = { context, data: result };
        setResponse({ context, key, data: result });
      }).catch((error: unknown) => {
        if (controller.signal.aborted || readKey.current !== key) return;
        setError({ key, message: error instanceof Error ? error.message : '物理片段读取失败。' });
        if (error instanceof ApiError && error.status === 401) onError(error);
      });
    return () => { controller.abort(); action.current?.controller.abort(); action.current = null; };
  }, [key, context, after, source.session_pk, source.file_instance_id, onError]);
  useEffect(() => { const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); }; window.addEventListener('keydown', escape); return () => window.removeEventListener('keydown', escape); }, [onClose]);
  function select(eventId: number) {
    cancel(); setConfirmed(false);
    const anchor = selection.key === key ? selection.anchor : null;
    const next = fragmentSelection(data?.records ?? [], anchor, eventId);
    setSelection({ key, anchor: anchor ?? eventId, ids: next });
  }
  function intentForRange() {
    if (!range || !target) return null;
    const projectId = target === 'inbox' ? null : target;
    if (projectId !== null && !projects.some(p => p.project_id === projectId)) return null;
    const business = JSON.stringify([source.session_pk, source.file_instance_id, range.first.first_event_id, range.last.first_event_id, projectId, actor]);
    if (intended.current?.identity !== business) intended.current = { identity: business, value: { request_id: crypto.randomUUID(), session: source.session_pk, source: source.file_instance_id, first_event: range.first.first_event_id, last_event: range.last.first_event_id, project_id: projectId, actor } };
    return intended.current.value;
  }
  async function inspect(offset = 0) {
    if (!data || !range || !target || busy || writing || checking || failure || !canAssignFragments(data) || (offset > 0 && dependencies?.next_offset !== offset)) return;
    const intent = intentForRange(); if (!intent) return;
    const previous = offset > 0 ? dependencies : null;
    cancel(false); setDependencyError(null);
    const controller = new AbortController(), operation = { controller, identity }; action.current = operation; setInspecting(identity);
    try {
      const raw = await api<unknown>('/project-fragments/dependencies', { ...intent, expected_revision: data.revision, expected_scope_key: data.scope_key, snapshot: data.snapshot, observation_snapshot: data.observation_snapshot, limit: 20, offset, ...(previous ? { expected_plan_sha256: previous.plan_sha256 } : {}) }, controller.signal);
      if (controller.signal.aborted || current.current !== identity || action.current !== operation) return;
      const result = parseFragmentDependencies(raw, data, range.records, offset, 20);
      setDependency({ identity, data: previous ? appendFragmentDependencies(previous, result) : result });
      if (!result.assignment_available) setConfirmed(false);
    } catch (error) {
      if (controller.signal.aborted || current.current !== identity || action.current !== operation) return;
      setDependencyError({ identity, message: error instanceof ApiError && error.status === 404 ? '此服务尚未提供科研依赖预检，无法核对当前范围的依赖。' : error instanceof Error ? error.message : '科研依赖预检失败，当前结果无法核对。' }); setConfirmed(false);
      if (error instanceof ApiError && error.status === 409) setError({ key, message: error.message });
      if (error instanceof ApiError && error.status === 401) onError(error);
    } finally { if (action.current === operation) { action.current = null; setInspecting(''); } }
  }
  async function assign() {
    if (!data || !range || !confirmed || !target || busy || writing || checking || dependencyBlocked || failure || !canAssignFragments(data)) return;
    const intent = intentForRange(); if (!intent) return;
    const controller = new AbortController(), operation = { controller, identity }; action.current = operation; setSaving(identity);
    try {
      const raw = await api<unknown>('/project-fragments/assign', { ...intent, expected_revision: data.revision, expected_scope_key: data.scope_key, snapshot: data.snapshot, observation_snapshot: data.observation_snapshot }, controller.signal);
      if (controller.signal.aborted || current.current !== identity || action.current !== operation) return;
      const receipt = parseFragmentReceipt(raw, intent, range.records); refresh(); onAssigned(receipt.revision);
    } catch (error) {
      if (controller.signal.aborted || current.current !== identity || action.current !== operation) return;
      setError({ key, message: error instanceof Error ? error.message : '归属保存结果未知，请核对后再主动重试。' });
      if (error instanceof ApiError && error.status === 401) onError(error);
    } finally { if (action.current === operation) { action.current = null; setSaving(''); } }
  }
  function navigate(index: number) {
    cancel(); setConfirmed(false); setSelection({ key: '', anchor: null, ids: [] });
    const anchors = [...page.anchors]; if (index > page.index && data?.next_after_event != null) anchors[index] = data.next_after_event;
    setPaging({ ...page, anchors, index });
  }
  const projectName = (id: string | null) => id === null ? '收件箱（未指定项目）' : projects.find(p => p.project_id === id)?.name ?? id;
  return <aside className="detail-drawer fragment-panel" role="dialog" aria-modal="false" aria-label="物理片段与项目归属">
    <header><div><span className="eyebrow">{source.tool} · 物理来源 #{source.file_instance_id}</span><h2>物理片段与项目归属</h2></div><button className="icon-button" aria-label="关闭片段面板" onClick={onClose}><Icon name="close" /></button></header>
    <div className="drawer-content"><p className="path">{source.path}</p>
      <p className="notice">查看选定物理来源的完整记录。目录只是线索，不自动决定项目；归属不表示已提取、人工确认或当前采用。原文和既有审核记录保留。</p>
      <button className="button secondary" onClick={refresh}>重新读取片段第一页</button>
      {failure && <p className="error-message" role="alert">{failure} {data && '保留上次成功页，操作已停用。'} 409 后需主动重新读取，再核对并提交。</p>}
      {busy && <p role="status">正在读取物理片段…</p>}
      {data?.available === false && <p className="missing-note">当前接口没有可用的片段账本（{data.reason}），无法判断记录边界或保存归属。</p>}
      {data?.available && <><p className="small muted">已保留 {data.records_total} 条物理记录 · 已观测 {data.records_observed} · 不可核对 {data.records_unavailable} · 本页 {data.records?.length}。解析事件数与物理记录数分别统计；保留记录观测完整不代表整个来源没有清除缺口。</p>
        <p data-fragment-privacy-gap className="fragment-privacy-gap missing-note">此物理来源持久清除缺口：跳过记录 {data.privacy_skipped_records ?? '未提供'} 条，跳过字节 {data.privacy_skipped_bytes ?? '未提供'}。{data.privacy_skipped_records === undefined || data.privacy_skipped_bytes === undefined ? '此接口没有完整缺口统计，不能判断整个来源是否无缺口。' : data.privacy_skipped_records || data.privacy_skipped_bytes ? '已清除原件不重新保存；缺口两侧分别处理，目录上下文不跨越缺口继承。' : '本来源账本未记录持久清除缺口；不代表所有采集或研究处理已经完成。'}</p>
        <p className="small muted">原文截止 #{data.snapshot} · 观测截止 #{data.observation_snapshot} · 归属修订 {data.revision ?? '未提供'}</p>
        {!data.observations_complete && <p className="missing-note">目录元数据尚有缺口；不能把未观测当作没有目录声明。</p>}
        {!data.assignment_supported && <p className="missing-note">此接口尚未接通明确归属；当前只读，不提交归属。</p>}
        {data.assignments_applied === false && <p className="small muted">尚未保存物理片段分区；下方整会话登记或收件箱默认信息不是片段归属动作。</p>}
        <p className="small muted">点击一条记录设置起点，再点击另一条扩展为本页连续范围。跨页选择不保留；不拆分同一物理记录内的正文块。</p>
        <button className="text-button" disabled={!ids.length || writing} onClick={() => { cancel(); setSelection({ key, anchor: null, ids: [] }); setConfirmed(false); }}>清除范围选择</button>
        <ol className="fragment-records">{data.records?.map(r => <li key={r.first_event_id} data-fragment-record={r.first_event_id} className={ids.includes(r.first_event_id) ? 'selected' : ''}>
          <div className="fragment-record-head"><button className="button secondary" aria-pressed={ids.includes(r.first_event_id)} disabled={busy || !!failure || writing} onClick={() => select(r.first_event_id)}>选择记录 #{r.first_event_id}</button><button className="text-button" onClick={() => onEvidence({ event_id: r.first_event_id })}>打开记录原文</button></div>
          <dl><div><dt>来源文件字节</dt><dd>[{r.byte_start}, {r.byte_end}) · {r.byte_end - r.byte_start} 字节</dd></div><div><dt>解析事件</dt><dd>{r.event_count} · 顺序 {r.first_seq}–{r.last_seq}</dd></div><div><dt>原件 SHA256</dt><dd className="mono">{r.object_sha256}</dd></div></dl>
          <p>{contextNames[r.context.state] ?? `目录状态未知（${r.context.state}）`}：<code>{r.context.cwd ?? '未提供可用目录'}</code></p>
          {r.context.reason === 'privacy_gap' && <p data-fragment-privacy-context className="missing-note">此前存在清除缺口，目录上下文不可核对；不沿用缺口之前的目录声明。</p>}
          <p className="small muted">{directorySources[r.context.source] ?? r.context.source} · {r.context.origin_event_id ? `${r.context.origin_event_id === r.first_event_id ? '本记录声明' : '同来源沿用'} #${r.context.origin_event_id}` : '线索来源未知'} · 最近观测 <DateText value={r.observed_at} /></p>
          <p>{hintNames[r.project_hint.state] ?? `路径候选状态未知（${r.project_hint.state}）`}{r.project_hint.projects.length ? `：${r.project_hint.projects.map(projectName).join('、')}` : ''}{r.project_hint.candidates_partial && '（候选仅预览，仍有遗漏）'}</p>
          <p className="small muted">{hintReasons[r.project_hint.reason] ?? r.project_hint.reason}</p>
          <p data-fragment-assignment>{r.assignment ? `当前归属：${projectName(r.assignment.project_id)} · ${{ manual: '人工指定片段', inherited_manual: '保留原登记归属', session_manual: '整会话登记', unassigned: '收件箱默认' }[r.assignment.basis] ?? r.assignment.basis} · 逻辑会话 ${r.assignment.logical_session_pk ?? '未提供'} · 分区 ${r.assignment.partition_id ?? '未提供'}` : '归属信息未提供，不能据此推断已分配或未分配。'}</p>
        </li>)}</ol>
        <div className="pagination"><button className="button secondary" disabled={busy || writing || !!failure || page.index === 0} onClick={() => navigate(page.index - 1)}>上一页片段</button><span>第 {page.index + 1} 页</span><button className="button secondary" disabled={busy || writing || !!failure || data.next_after_event == null} onClick={() => navigate(page.index + 1)}>下一页片段</button></div>
        <section className="fragment-confirm" aria-label="明确指定归属"><h3>明确指定归属</h3>
          <label>目标项目<select aria-label="片段目标项目" value={target} disabled={writing} onChange={e => { cancel(); setTarget(e.target.value); setConfirmed(false); }}><option value="">请选择，不按路径自动决定</option><option value="inbox">收件箱（未指定项目）</option>{projects.map(p => <option key={p.project_id} value={p.project_id}>{p.name}</option>)}</select></label>
          {range ? <p data-fragment-selected-range>将修改 {range.records} 条完整物理记录、{range.events} 个解析事件；首事件 #{range.first.first_event_id} 至 #{range.last.first_event_id}，来源文件字节 [{range.first.byte_start}, {range.last.byte_end})。目标：{target ? projectName(target === 'inbox' ? null : target) : '尚未选择'}。</p> : <p data-fragment-invalid-range className="missing-note">{ids.length ? '所选记录之间不连续或存在字节缺口，不能跨缺口指定归属；请清除选择后，只选同一侧连续的完整记录。' : '尚未选择本页连续的完整记录范围。'}</p>}
          <section className="fragment-dependencies" aria-label="科研依赖只读预检">
            <h4>科研依赖只读预检</h4><p className="small muted">核对选定范围关联的证据、科研记录与处理上下文，列出片段外必需的完整记录。只读预检不保存归属、不迁移科研历史、不占用提交编号。</p>
            <button className="button secondary" disabled={!range || !target || busy || writing || checking || !!failure || !canAssignFragments(data)} onClick={() => { void inspect(); }}>{checking ? '正在核对科研依赖…' : '只读预检科研依赖'}</button>
            {dependencyFailure && <p className="error-message" role="alert">{dependencyFailure} {dependencies && '保留上次预检，未混入失败续页。'} 需主动重新读取或预检。</p>}
            {dependencies && <><p data-dependency-result className={dependencies.assignment_available ? 'notice' : 'missing-note'}>{dependencies.write_mode === 'direct' ? '本次预检允许直接保存归属；尚未执行保存。' : dependencies.write_mode === 'dependency_migration' ? '已有科研记录待迁移，当前不能直接保存归属；本次未执行迁移。' : '科研依赖尚无法完整核对，当前不能保存归属；本次未执行迁移。'}</p>
              <p className="small muted">已选 {dependencies.selected_records} 条完整记录 · {dependencies.complete ? '依赖核对完成' : '依赖核对仍有缺口'} · {dependencies.closure_closed ? '依赖均在所选范围内' : '依赖范围尚未闭合'}。预检不表示提取完成或科学确认。</p>
              <div className="fragment-dependency-groups">{fragmentDependencyGroups(dependencies.dependencies).map(group => <div key={group.name}><h5>{group.name}</h5><dl>{group.items.map(item => <div key={item.key}><dt>{item.label}</dt><dd>{item.count}</dd></div>)}</dl></div>)}</div>
              {!Object.keys(dependencies.dependencies).length && <p className="small muted">本次未发现科研依赖记录。</p>}
              {!!Object.keys(dependencies.shared_context).length && <p className="small muted">关联上下文：{Object.entries(dependencies.shared_context).map(([kind, count]) => `${({ project_id: '项目', session_pk: '会话', physical_session_pk: '物理会话', root_id: '已登记根目录' } as Record<string, string>)[kind] ?? `未知资源类别（${kind}）`} ${count}`).join('、')}。资源数量不等于科研记录数量。</p>}
              {!!dependencies.issues.length && <ul className="missing-note">{dependencies.issues.map(issue => <li key={issue}>{dependencyIssueNames[issue] ?? `尚无法核对的依赖问题（${issue}）`}</li>)}</ul>}
              <h5>片段外必需记录</h5><p className="small muted">已读 {dependencies.required_records.length} / {dependencies.required_records_total} 条；不会自动扩大已选范围或移动这些记录。</p>
              <ol className="fragment-required-records">{dependencies.required_records.map(record => <li key={record.first_event_id} data-required-record={record.first_event_id}><strong>完整记录 #{record.first_event_id}</strong><p>物理会话 #{record.session_pk} · 来源 #{record.file_instance_id} · 来源文件字节 [{record.byte_start}, {record.byte_end})</p><p>当前项目：{projectName(record.project_id)}</p><button className="text-button" onClick={() => onEvidence({ event_id: record.first_event_id })}>打开必需记录原文</button></li>)}</ol>
              {dependencies.next_offset !== null && <button className="button secondary" disabled={checking || writing || !!failure || !!dependencyFailure} onClick={() => { void inspect(dependencies.next_offset!); }}>读取后续必需记录</button>}
              <details><summary>预检读取标识</summary><p className="small muted">修订 {dependencies.revision} · 原文截止 #{dependencies.snapshot} · 观测截止 #{dependencies.observation_snapshot}</p><p className="mono small">依赖摘要 {dependencies.plan_sha256}</p></details>
            </>}
          </section>
          <label className="fragment-check"><input type="checkbox" checked={confirmed} disabled={!range || !target || busy || writing || checking || dependencyBlocked || !!failure || !canAssignFragments(data)} onChange={e => setConfirmed(e.target.checked)} />我已核对完整范围与目标；仅保存项目归属，保留原件和审核历史。</label>
          <p className="small muted">操作人 {actor}。提交失败保留范围与目标；不会自动重新发送。停止页面等待不保证服务端停止写入，重新读取可核对实际结果。</p>
          <button className="button primary" disabled={!confirmed || !range || !target || busy || writing || checking || dependencyBlocked || !!failure || !canAssignFragments(data)} onClick={() => { void assign(); }}>{writing ? '正在保存归属…' : '保存明确归属'}</button>
        </section></>}
    </div>
  </aside>;
}
