import { useEffect, useMemo, useState } from 'react';
import { api, ApiError, query } from './api';
import { EventLocator } from './EventLocator';
import { Badge, ClaimBody, DateText, Empty, EvidenceLink, EvidencePanel, Icon, Loading, Scope, Source } from './components';
import { actionNames, adoption, entityVersions, evidenceNames, evidenceState, groupQueue, kindNames, label, relatedChildren, scopeKey, timeline } from './model';
import type { Claim, ClaimsPage, GraphData, ReviewState, SearchPage, Span } from './types';

type Common = { data: GraphData; onClaim: (id: number) => void; onEvidence: (span: Span) => void };
export function Questions({ data, onClaim, onEvidence }: Common) {
  const entities = useMemo(() => entityVersions(data.claims), [data.claims]);
  const questions = entities.filter(item => item.payload.kind === 'question');
  const [focused, setFocused] = useState<number | null>(null);
  useEffect(() => { if (focused && !questions.some(item => item.claim_id === focused)) setFocused(null); }, [questions, focused]);
  const question = questions.find(item => item.claim_id === focused) ?? questions[0];
  const childIds = question ? relatedChildren(data.claims, question.entity_id!, question.scope) : [];
  const approaches = entities.filter(item => item.payload.kind === 'approach' && childIds.includes(item.entity_id!) && scopeKey(item.scope) === scopeKey(question.scope));
  const linkedAll = new Set(questions.flatMap(item => relatedChildren(data.claims, item.entity_id!, item.scope).map(id => `${id}:${scopeKey(item.scope)}`)));
  const unlinked = entities.filter(item => item.payload.kind === 'approach' && !linkedAll.has(`${item.entity_id}:${scopeKey(item.scope)}`));
  const confirmed = data.claims.filter(item => item.effective_state === 'confirmed').length;
  function card(claim: Claim) {
    const action = adoption(data.claims, claim.entity_id!, claim.scope);
    const descendants = relatedChildren(data.claims, claim.entity_id!, claim.scope);
    const attempts = entities.filter(item => item.payload.kind === 'attempt' && scopeKey(item.scope) === scopeKey(claim.scope) && descendants.includes(item.entity_id!));
    const relation = data.claims.find(item => item.payload.source === claim.entity_id && item.payload.target === question?.entity_id && item.payload.relation === 'part_of' && item.effective_state !== 'dismissed' && scopeKey(item.scope) === scopeKey(claim.scope));
    return <article className="approach-card" key={claim.claim_id}>
      <div className="card-top"><span className="eyebrow">方案</span><Badge state={claim.effective_state} /></div>
      <button className="card-title" onClick={() => onClaim(claim.claim_id)}>{label(claim)}<Icon name="arrow" size={17} /></button>
      <p className="card-content">{claim.payload.content}</p><Scope value={claim.scope} />
      <div className="dimension-row"><span>采用 <strong>{actionNames[action] ?? (action === 'time_unknown' ? '发生时间未知' : action === 'conflict' ? '记录冲突' : '未知')}</strong></span><span>证据 {evidenceNames[evidenceState(data.claims, claim.entity_id!, claim.scope)]}</span><span>运行 未知</span></div>
      {relation?.effective_state === 'candidate' && <button className="muted-link" onClick={() => onClaim(relation.claim_id)}>与此问题的归属关系待复核 #{relation.claim_id}</button>}
      <div className="card-evidence">{claim.evidence.slice(0, 2).map(span => <EvidenceLink key={span.span_id} span={span} onOpen={onEvidence} />)}</div>
      {attempts.length > 0 && <div className="children-list">{attempts.map(item => <button key={item.claim_id} onClick={() => onClaim(item.claim_id)}><span>{kindNames[item.payload.kind!]}</span>{label(item)}<Badge state={item.effective_state} /></button>)}</div>}
    </article>;
  }
  return <>
    <div className="summary-grid"><div><span>研究问题</span><strong>{questions.length.toString().padStart(2, '0')}</strong><small>来自本地会话记录</small></div><div><span>研究方案</span><strong>{entities.filter(item => item.payload.kind === 'approach').length.toString().padStart(2, '0')}</strong><small>每次采用都带范围</small></div><div><span>已确认记录</span><strong>{confirmed.toString().padStart(2, '0')}</strong><small>人工与原话规则分别标明</small></div><div><span>待复核记录</span><strong>{data.claims.filter(item => item.effective_state === 'candidate').length.toString().padStart(2, '0')}</strong><small>候选动作不计入当前采用</small></div></div>
    {!questions.length ? <Empty title="还没有可查看的研究问题">导入并提取会话后，问题、方案及其证据会出现在这里。没有明确归属的方案单列在下方。</Empty> : <div className="question-layout">
      <div className="question-index"><div className="section-caption">研究问题 <span>{questions.length}</span></div>{questions.map((item, index) => <button key={item.claim_id} className={item.claim_id === question?.claim_id ? 'question-item active' : 'question-item'} onClick={() => setFocused(item.claim_id)}><span className="question-number">Q{(index + 1).toString().padStart(2, '0')}</span><strong>{label(item)}</strong><Badge state={item.effective_state} /></button>)}</div>
      <div className="question-detail"><div className="question-heading"><span className="eyebrow">正在研究的问题</span><h2>{label(question)}</h2><p>{question.payload.content}</p><div className="inline-row"><Scope value={question.scope} /><button className="text-button" onClick={() => onClaim(question.claim_id)}>查看记录与证据 <Icon name="arrow" size={15} /></button></div></div>
        {(['accepted', 'deferred', 'rejected', 'other'] as const).map(group => {
          const items = approaches.filter(item => { const status = adoption(data.claims, item.entity_id!, item.scope); return group === 'other' ? !['accepted', 'deferred', 'rejected'].includes(status) : status === group; });
          const names = { accepted: '当前采用 · 已确认且时序明确', deferred: '暂缓', rejected: '拒绝', other: '提出、撤回或状态未明' };
          return <section className="approach-group" key={group}><h3>{names[group]}<span>{items.length}</span></h3>{items.length ? <div className="approach-grid">{items.map(card)}</div> : <p className="muted small">此分组没有已知方案。</p>}</section>;
        })}
      </div>
    </div>}
    {unlinked.length > 0 && <section className="unlinked-section"><h3>问题归属未确定的方案 <span>{unlinked.length}</span></h3><div className="approach-grid">{unlinked.map(card)}</div></section>}
  </>;
}

export function TimelineView({ data, onClaim, onEvidence }: Common) {
  const entities = entityVersions(data.claims);
  const ids = [...new Set(data.claims.filter(item => item.claim_type === 'decision_event').map(item => item.payload.target!))];
  const [target, setTarget] = useState('');
  const active = ids.includes(target) ? target : ids[0];
  const events = active ? timeline(data.claims, active) : [];
  const scopes = [...new Set(events.map(item => scopeKey(item.scope)))];
  const [scope, setScope] = useState('all');
  const visible = events.filter(item => scope === 'all' || scopeKey(item.scope) === scope);
  return <div className="timeline-view"><div className="filter-bar"><label>决定对象<select value={active ?? ''} onChange={event => { setTarget(event.target.value); setScope('all'); }}>{ids.map(id => <option key={id} value={id}>{entities.find(item => item.entity_id === id)?.payload.label ?? id}</option>)}</select></label><label>范围<select value={scope} onChange={event => setScope(event.target.value)}><option value="all">全部范围（分别判断采用）</option>{scopes.map(key => <option value={key} key={key}>{key}</option>)}</select></label></div>
    {!visible.length ? <Empty title="没有决定事件">提出、采用、暂缓、拒绝和撤回都需要可查证的记录。</Empty> : <><p className="notice">审核确认与决定采用是两个维度。待复核、已驳回事件保留在时间线上；它们不决定当前采用。发生时间缺失时按入库时间展示，采用时序仍标为未知。</p><ol className="timeline-list">{visible.map(item => <li key={item.claim_id}><div className="timeline-point" /><div className="timeline-date"><DateText value={item.occurred_at} /><small>入库 <DateText value={item.recorded_at} /></small></div><article><div className="card-top"><strong>{actionNames[item.payload.action!] ?? item.payload.action}</strong><Badge state={item.effective_state} /></div><button className="muted-link" onClick={() => onClaim(item.claim_id)}>记录 #{item.claim_id} · {item.payload.reason}</button><Scope value={item.scope} /><Source claim={item} /><div className="card-evidence">{item.evidence.map(span => <EvidenceLink key={span.span_id} span={span} onOpen={onEvidence} />)}</div></article></li>)}</ol></>}
  </div>;
}

export { HealthView } from './HealthView';

export function SearchView({ project, text, onEvidence, onError }: {
  project: string; text: string; onEvidence: (target: { event_id: number }) => void; onError: (error: unknown) => void;
}) {
  const [result, setResult] = useState<SearchPage | null>(null); const [offset, setOffset] = useState(0);
  useEffect(() => { setOffset(0); }, [text, project]);
  useEffect(() => {
    if (!text.trim()) return;
    const controller = new AbortController(); setResult(null);
    api<SearchPage>(`/search?${query({ project, q: text, offset, limit: 30 })}`, undefined, controller.signal).then(data => { if (!controller.signal.aborted) setResult(data); }).catch(error => { if (!controller.signal.aborted) onError(error); });
    return () => controller.abort();
  }, [text, project, offset, onError]);
  return <div className="search-view"><EventLocator onEvidence={onEvidence} />
    {!text.trim() ? <Empty title="搜索原文">在顶部输入词语，查看可直接定位的会话证据。</Empty> : !result ? <Loading /> : <>
      <p className="muted">“{text}” · 当前项目 · {result.total} 条原文结果 · 按字面量匹配</p>{!result.results.length ? <Empty title="没有匹配的原文">尝试更短的词语，或切换项目。</Empty> : result.results.map(item => <button key={item.event_id} className="search-result" onClick={() => onEvidence({ event_id: item.event_id })}><div><span className="eyebrow">事件 #{item.event_id} · 会话 {item.session_pk}</span><DateText value={item.occurred_at} /></div><p>{item.text.split(text).map((part, index) => <span key={index}>{index > 0 && <mark>{text}</mark>}{part}</span>)}</p><span className="text-button">查看原文 <Icon name="arrow" size={14} /></span></button>)}<div className="pagination"><button className="button secondary" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>上一页</button><span>{offset + 1}–{Math.min(offset + 30, result.total)} / {result.total}</span><button className="button secondary" disabled={result.next_offset == null} onClick={() => setOffset(result.next_offset!)}>下一页</button></div>
    </>}
  </div>;
}

export function ReviewQueue({ project, epoch, actor, onWrite, onError, onEdit, onEvidence, onClaim }: {
  project: string; epoch: number; actor: string; onWrite: (revision: number) => void;
  onError: (error: unknown) => void; onEdit: (claim: Claim, revision: number) => void; onEvidence: (span: Span) => void;
  onClaim: (id: number) => void;
}) {
  const [data, setData] = useState<ClaimsPage | null>(null); const [state, setState] = useState<ReviewState | 'all'>('candidate');
  const [offset, setOffset] = useState(0); const [activeId, setActiveId] = useState<number | null>(null); const [busy, setBusy] = useState(false);
  useEffect(() => setOffset(0), [state, project]);
  useEffect(() => {
    const controller = new AbortController(); setData(null);
    api<ClaimsPage>(`/claims?${query({ project, state, offset, limit: 50 })}`, undefined, controller.signal).then(result => { if (!controller.signal.aborted) { setData(result); setActiveId(previous => result.claims.some(item => item.claim_id === previous) ? previous : result.claims[0]?.claim_id ?? null); } }).catch(error => { if (!controller.signal.aborted) onError(error); });
    return () => controller.abort();
  }, [state, project, offset, epoch, onError]);
  const active = data?.claims.find(item => item.claim_id === activeId);
  async function review(ids: number[], action: 'confirm' | 'dismiss') {
    if (!data || busy) return;
    setBusy(true);
    try { const result = await api<{ revision: number }>('/review', { claim_ids: ids, action, actor, expected_revision: data.revision }); onWrite(result.revision); }
    catch (error) { onError(error); }
    finally { setBusy(false); }
  }
  async function confirmGroup(group: ReturnType<typeof groupQueue>[number]) {
    if (!data || busy) return;
    const first = group.claims[0].groups[0];
    if (!first) { onError(new Error('此组缺少会话与片段归属，不能推定全部记录')); return; }
    setBusy(true);
    try {
      let next: number | null = 0; const ids: number[] = [];
      while (next != null) {
        const result: ClaimsPage = await api(`/claims?${query({ project, state: 'candidate', session: first.session_pk, segment: first.segment_id ?? 'unknown', limit: 200, offset: next })}`);
        if (result.revision !== data.revision) throw new ApiError(409, '图版本已变化，请刷新后确认整个片段');
        ids.push(...result.claims.map(item => item.claim_id)); next = result.next_offset;
        if (ids.length > 2000) throw new Error('片段超过单次 2000 条复核上限，请分批人工复核');
      }
      if (!ids.length) return;
      const result = await api<{ revision: number }>('/review', { claim_ids: ids, action: 'confirm', actor, expected_revision: data.revision }); onWrite(result.revision);
    } catch (error) { onError(error); } finally { setBusy(false); }
  }
  useEffect(() => {
    function keyboard(event: KeyboardEvent) {
      if (event.target instanceof HTMLElement && (event.target.closest('input,textarea,select,button') || event.target.isContentEditable)) return;
      if (!active || !data || busy || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key.toLowerCase() === 'c') { event.preventDefault(); void review([active.claim_id], 'confirm'); }
      if (event.key.toLowerCase() === 'x') { event.preventDefault(); void review([active.claim_id], 'dismiss'); }
      if (event.key.toLowerCase() === 'e') { event.preventDefault(); onEdit(active, data.revision); }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); const index = data.claims.findIndex(item => item.claim_id === active.claim_id); setActiveId(data.claims[Math.max(0, Math.min(data.claims.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))].claim_id); }
    }
    window.addEventListener('keydown', keyboard); return () => window.removeEventListener('keydown', keyboard);
  });
  return <div className="review-view"><div className="filter-bar"><label>审核状态<select aria-label="审核状态" value={state} onChange={event => setState(event.target.value as ReviewState | 'all')}><option value="candidate">待复核</option><option value="confirmed">已确认</option><option value="dismissed">已驳回</option><option value="all">全部记录</option></select></label><span className="muted small">C 确认 · X 驳回 · E 修改 · ↑↓ 切换候选</span>{data && <span className="count-pill">{data.total} 条</span>}</div>
    {!data ? <Loading /> : !data.claims.length ? <Empty title={state === 'candidate' ? '当前没有待复核记录' : '此筛选下没有记录'}>人工复核与原话规则确认会追加审核记录，保留原始候选。</Empty> : <div className="review-layout"><aside className="queue-index">{groupQueue(data.claims).map(group => <section key={group.key}><header><strong title={group.title}>{group.title}</strong><span>预计约 {Math.max(1, Math.ceil(group.claims.length / 2))} 分钟（按每条 30 秒估计）</span>{state === 'candidate' && <button className="text-button" disabled={busy} onClick={() => { void confirmGroup(group); }}>确认整个片段的待复核记录</button>}</header>{group.claims.map(item => <button className={`queue-item ${item.claim_id === activeId ? 'active' : ''}`} key={item.claim_id} onClick={() => setActiveId(item.claim_id)}><span className="mono">#{item.claim_id}</span><strong>{label(item)}</strong><Badge state={item.effective_state} /></button>)}</section>)}</aside>{active && <div className="review-comparison"><section className="review-original"><div className="section-caption">原文证据 <span>{active.evidence.length}</span></div>{active.evidence[0] ? <><EvidencePanel target={active.evidence[0]} onError={onError} compact />{active.evidence.length > 1 && <div className="card-evidence">{active.evidence.slice(1).map(span => <EvidenceLink key={span.span_id} span={span} onOpen={onEvidence} />)}</div>}</> : <Empty title="原文引用缺失">此记录不能视为已验证。</Empty>}</section><section className="review-structured"><div className="section-caption">结构化记录 <span className="mono">#{active.claim_id}</span></div><ClaimBody claim={active} onClaim={onClaim} /><div className="review-actions"><button className="button primary" disabled={busy || active.replacement_ids.length > 0} onClick={() => { void review([active.claim_id], 'confirm'); }}><Icon name="check" size={16} />确认 <kbd>C</kbd></button><button className="button secondary" disabled={busy || active.replacement_ids.length > 0} onClick={() => { void review([active.claim_id], 'dismiss'); }}>驳回 <kbd>X</kbd></button><button className="text-button" disabled={busy || active.replacement_ids.length > 0} onClick={() => onEdit(active, data.revision)}>修改 <kbd>E</kbd></button></div>{active.replacement_ids.length > 0 && <p className="notice">此记录已有修改版 #{active.replacement_ids.join('、')}，请复核新记录。</p>}</section></div>}</div>}
    {data && <div className="pagination"><button className="button secondary" disabled={offset === 0 || busy} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</button><span>偏移 {offset} · 版本 {data.revision}</span><button className="button secondary" disabled={data.next_offset == null || busy} onClick={() => setOffset(data.next_offset!)}>下一页</button></div>}
  </div>;
}
