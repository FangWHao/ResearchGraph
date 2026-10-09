import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { requiresDecisionTarget } from './manualDecision';
import { api, query } from './api';
import { DateText } from './DateText';
import { EvidenceRecords, VersionRecords } from './EvidenceRecords';
import { actionNames, evidenceNames, kindNames, label, reviewNames, scopeText } from './model';
import type { Claim, EvidenceData, EvidenceTarget, EventWindow, Span } from './types';

export { DateText } from './DateText';

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    questions: <><path d="M4 4h16v12H9l-5 4V4Z" /><path d="M9 8h6M9 12h3" /></>,
    review: <><path d="M6 4h12v16H6zM9 2h6v4H9zM9 13l2 2 4-5" /></>,
    graph: <><circle cx="5" cy="6" r="2" /><circle cx="19" cy="6" r="2" /><circle cx="12" cy="18" r="2" /><path d="m6 8 5 8m7-8-5 8M7 6h10" /></>,
    health: <><path d="M3 12h4l3-8 4 16 3-8h4" /></>,
    search: <><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6" /></>,
    evidence: <><path d="M5 3h10l4 4v14H5zM14 3v5h5M8 12h8M8 16h6" /></>,
    arrow: <><path d="M5 12h14m-5-5 5 5-5 5" /></>,
    close: <><path d="m6 6 12 12M6 18 18 6" /></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    history: <><path d="M3 4v5h5M4 8a9 9 0 1 1-1 7M12 7v6l4 2" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] ?? paths.evidence}</svg>;
}
export function Badge({ state }: { state: Claim['effective_state'] }) {
  return <span className={`badge ${state}`}><span className="badge-dot" />{reviewNames[state]}</span>;
}
export function Source({ claim }: { claim: Claim }) {
  const name = claim.confirmation_source === 'human' ? '人工确认' : claim.confirmation_source === 'rule' ? '原话规则确认' : '尚未确认';
  return <span className="source-note">记录：{claim.actor.startsWith('model:') ? '模型候选' : claim.actor.startsWith('human:') ? '人工' : claim.actor} · {name}{claim.review ? ` · ${claim.review.actor}` : ''}</span>;
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty-state"><Icon name="evidence" size={28} /><h3>{title}</h3>{children && <p>{children}</p>}</div>;
}
export function Loading() { return <div className="loading" role="status"><span className="spinner" />正在读取本地研究记录…</div>; }
export function Scope({ value }: { value: Claim['scope'] }) { return <span className="scope" title={scopeText(value)}>{scopeText(value)}</span>; }

export function EvidenceLink({ span, onOpen }: { span: Span; onOpen: (span: Span) => void }) {
  return <button className="evidence-link" onClick={() => onOpen(span)}><Icon name="evidence" size={14} />原文 #{span.event_id}<span className="mono">{span.byte_start}–{span.byte_end}</span></button>;
}
function RawWindow({ event, focus = false }: { event: EventWindow; focus?: boolean }) {
  return <section className={`raw-event ${focus ? 'focused' : ''}`}>
    <header><strong>事件 #{event.event_id}</strong><span>{event.role ?? event.kind} · 顺序 {event.seq}</span><DateText value={event.occurred_at} /></header>
    {event.exclude_reason && <p className="notice">此事件已排除：{event.exclude_reason}，仅作为上下文查看。</p>}
    <pre>{event.before}{event.quote && <mark data-testid="evidence-quote">{event.quote}</mark>}{event.after}</pre>
    {event.window_truncated && <p className="muted small">当前窗口 {event.window_start}–{event.window_end} / {event.total_bytes} UTF-8 字节，未显示部分保留在对象库。</p>}
  </section>;
}
export function EvidencePanel({ target, onError, compact = false, onEvidence }: {
  target: EvidenceTarget;
  onError: (error: unknown) => void; compact?: boolean; onEvidence?: (target: EvidenceTarget) => void;
}) {
  const [data, setData] = useState<EvidenceData | null>(null);
  const [context, setContext] = useState(compact ? 0 : 2);
  const [failure, setFailure] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setFailure('');
    api<EvidenceData>(`/evidence/${target.event_id}?${query({ context, start: target.byte_start, end: target.byte_end })}`, undefined, controller.signal)
      .then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(error => { if (!controller.signal.aborted) { setFailure(error.message); onError(error); } });
    return () => controller.abort();
  }, [target.event_id, target.byte_start, target.byte_end, context, onError]);
  if (failure) return <Empty title="原文暂不可用">{failure}</Empty>;
  if (!data) return <Loading />;
  const hashMatches = !target.quote_sha256 || target.quote_sha256 === data.event.quote_sha256;
  return <div className="evidence-panel">
    <div className="evidence-toolbar"><div><span className="eyebrow">本地证据 · UTF-8 字节定位</span><p className="path" title={data.event.path}>{data.event.path}</p><p className="muted small">源文件位置 {data.event.source_byte_start}–{data.event.source_byte_end} · 会话 {data.event.session_pk}</p></div>
      <label className="context-selector">前后上下文<select value={context} onChange={event => setContext(Number(event.target.value))}>{[0, 1, 2, 3, 5].map(value => <option key={value} value={value}>{value} 条</option>)}</select></label>
    </div>
    {!hashMatches && <p className="error-message" role="alert">引用摘要与原文不一致，此证据不能视为已验证。</p>}
    {target.byte_start != null && <p className="small muted">引用位置 {target.byte_start}–{target.byte_end} · {hashMatches ? '原文摘要一致' : '摘要不一致'}</p>}
    {data.before.map(event => <RawWindow key={event.event_id} event={event} />)}
    <RawWindow event={data.event} focus />
    {data.after.map(event => <RawWindow key={event.event_id} event={event} />)}
    {!compact && <>
      <EvidenceRecords data={data.l1} current={data.event.event_id} onOpen={onEvidence} />
      <VersionRecords versions={data.artifact_versions} partial={data.artifact_versions_partial} />
      {!data.artifact_diff.available && <p className="missing-note">差异缺失：{data.artifact_diff.reason}。</p>}
      {data.artifact_diff.available && !data.l1?.edits.length && <p className="missing-note">接口提示存在差异，但未返回可展示的编辑详情。</p>}
    </>}
  </div>;
}
export function ClaimBody({ claim, onClaim }: { claim: Claim; onClaim?: (id: number) => void }) {
  const payload = claim.payload;
  return <div className="claim-body">
    <p className="eyebrow">{payload.kind ? kindNames[payload.kind] : claim.claim_type}</p>
    <h3>{label(claim)}</h3>
    {requiresDecisionTarget(claim) && <section className="pending-decision-note"><strong>{claim.pending_decision?.resolved_claim_id ? '原决定的对象已在替代版中确定' : '原决定未指向明确对象'}</strong><p>对象原话：{claim.pending_decision?.selector ?? '未知'}</p>
      {claim.pending_decision?.resolved_claim_id && onClaim ? <button className="text-button" onClick={() => onClaim(claim.pending_decision!.resolved_claim_id!)}>查看确定对象的替代版 #{claim.pending_decision.resolved_claim_id}</button>
        : <p>请通过确定对象追加复核记录；普通确认与修改不能替代对象选择。</p>}</section>}
    {payload.content && <p className="claim-content">{payload.content}</p>}
    {payload.action && <p className="dimension">决定事件：<strong>{actionNames[payload.action] ?? payload.action}</strong>{claim.effective_state !== 'confirmed' && <span> · 此动作尚未确认</span>}</p>}
    {payload.state && <p className="dimension">证据状态记录：<strong>{evidenceNames[payload.state] ?? payload.state}</strong></p>}
    {payload.reason && payload.reason !== label(claim) && <p>{payload.reason}</p>}
    <Scope value={claim.scope} />
    <Source claim={claim} />
    <dl className="metadata"><div><dt>发生时间</dt><dd><DateText value={claim.occurred_at} /></dd></div><div><dt>入库时间</dt><dd><DateText value={claim.recorded_at} /></dd></div><div><dt>依据</dt><dd>{claim.basis}</dd></div></dl>
    {claim.replaces && <details className="edit-diff"><summary>人工修改前后 · 原记录 #{claim.replaces.claim_id}</summary><div><pre>{JSON.stringify(claim.replaces.payload, null, 2)}</pre><pre>{JSON.stringify(claim.payload, null, 2)}</pre></div><p>范围：{scopeText(claim.replaces.scope)} → {scopeText(claim.scope)}</p></details>}
    {claim.human_comparisons?.filter(item => item.differing_fields.length > 0).map(item => <details className="edit-diff" key={item.claim_id} open><summary>与人工已确认记录 #{item.claim_id} 存在差异</summary><p>{item.actor} · {scopeText(item.scope)}</p><div><pre>{JSON.stringify(Object.fromEntries(item.differing_fields.map(key => [key, item.payload[key]])), null, 2)}</pre><pre>{JSON.stringify(Object.fromEntries(item.differing_fields.map(key => [key, claim.payload[key]])), null, 2)}</pre></div>{onClaim && <button className="text-button" onClick={() => onClaim(item.claim_id)}>打开原人工确认记录 #{item.claim_id}</button>}</details>)}
  </div>;
}
