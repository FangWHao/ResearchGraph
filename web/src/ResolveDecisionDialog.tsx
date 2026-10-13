import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { DateText, Empty, Icon, Loading, Scope } from './components';
import { canResolveDecision, resolutionRequest } from './manualDecision';
import type { ResolutionDraft } from './manualDecision';
import { actionNames, kindNames } from './model';
import type { Claim, DecisionTargetsPage, Project, ResolveDecisionResult } from './types';
import './manualDecision.css';

export function ResolveDecisionDialog({ project, claimId, actor, epoch, draft, onDraft, onClose, onResolved, onError, onReadError, onRefresh, onClaim }: {
  project: Project; claimId: number; actor: string; epoch: number; draft: ResolutionDraft;
  onDraft: (draft: ResolutionDraft) => void; onClose: () => void; onResolved: (result: ResolveDecisionResult) => void;
  onError: (error: unknown, requestId: string) => void; onReadError: (error: unknown) => void;
  onRefresh: () => void; onClaim: (id: number) => void;
}) {
  const [original, setOriginal] = useState<{ revision: number; claim: Claim } | null>(null);
  const [targets, setTargets] = useState<DecisionTargetsPage | null>(null), [offset, setOffset] = useState(0);
  const [failure, setFailure] = useState(''), [busy, setBusy] = useState(false), [needsRefresh, setNeedsRefresh] = useState(false);
  const inFlight = useRef(false);
  useEffect(() => {
    const controller = new AbortController(); setOriginal(null); setTargets(null); setFailure('');
    api<{ revision: number; claim: Claim }>(`/claims/${claimId}`, undefined, controller.signal)
      .then(value => { if (!controller.signal.aborted) setOriginal(value); })
      .catch(error => { if (!controller.signal.aborted) { setFailure(error.message); onReadError(error); } });
    return () => controller.abort();
  }, [claimId, epoch, onReadError]);
  useEffect(() => { setOffset(0); }, [draft.query, claimId]);
  useEffect(() => {
    if (!original || !canResolveDecision(original.claim)) return;
    const controller = new AbortController(); setTargets(null); setFailure('');
    api<DecisionTargetsPage>(`/decision-targets?${query({ project: project.project_id, q: draft.query, scope: JSON.stringify(original.claim.scope), limit: 200, offset })}`, undefined, controller.signal)
      .then(value => {
        if (controller.signal.aborted) return;
        if (value.revision !== original.revision) { setNeedsRefresh(true); setFailure('对象列表与原记录的版本已变化，请读取最新版本后继续。'); return; }
        setTargets(value);
      }).catch(error => { if (!controller.signal.aborted) { setFailure(error.message); onReadError(error); } });
    return () => controller.abort();
  }, [original, project.project_id, draft.query, offset, onReadError]);
  function refresh() { setOriginal(null); setTargets(null); setFailure(''); setNeedsRefresh(false); onRefresh(); }
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current || needsRefresh || !original || !targets || !canResolveDecision(original.claim)) return;
    let requestId: string | null = null;
    try {
      const prepared = resolutionRequest(draft, project.project_id, claimId, actor, targets.revision);
      requestId = prepared.request.request_id; onDraft(prepared.draft);
      inFlight.current = true; setBusy(true); setFailure('');
      onResolved(await api<ResolveDecisionResult>(`/records/decisions/${claimId}/resolve`, prepared.request));
    } catch (error) {
      setFailure(error instanceof Error ? error.message : '确定对象失败，选择已保留。');
      if (error instanceof ApiError && error.status === 409) setNeedsRefresh(true);
      if (requestId) onError(error, requestId);
    } finally { inFlight.current = false; setBusy(false); }
  }
  const pending = original?.claim.pending_decision;
  return <div className="modal-backdrop" onClick={() => { if (!busy) onClose(); }}><section className="edit-modal manual-decision-modal resolve-decision-modal" role="dialog" aria-modal="true" aria-label="确定决定对象" onClick={event => event.stopPropagation()}>
    <header><div><span className="eyebrow">对象复核 · 原记录 #{claimId}</span><h2>确定决定对象</h2></div><button className="icon-button" aria-label="关闭确定对象" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
    <p className="manual-project">项目：<strong>{project.name}</strong></p>
    {original ? <section className="resolve-original"><h3>原人工决定</h3><p>对象原话：{pending?.selector ?? '记录缺失'}</p>
      <p>动作：{actionNames[pending?.action ?? original.claim.payload.action ?? ''] ?? '未知'}</p><p>理由：{pending?.why ?? original.claim.payload.reason}</p><Scope value={original.claim.scope} />
      <p className="muted small">发生时间 <DateText value={original.claim.occurred_at} /> · 入库 <DateText value={original.claim.recorded_at} /></p>
      <p className="notice">这里只确定对象，原动作、理由和范围保持不变。范围未知时，选择对象后仍不判断当前采用。</p></section> : !failure && <Loading />}
    {original && !canResolveDecision(original.claim) && <div className="notice">此记录已驳回、已替换或不再需要确定对象，不能继续提交。
      {pending?.resolved_claim_id != null && <button className="text-button" onClick={() => onClaim(pending.resolved_claim_id!)}>查看已确定对象的记录 #{pending.resolved_claim_id}</button>}</div>}
    {original && canResolveDecision(original.claim) && <form onSubmit={event => { void submit(event); }}>
      <label>按字面查找对象<input aria-label="查找决定对象" value={draft.query} maxLength={500} disabled={busy} onChange={event => onDraft({ query: event.target.value, target: null, intent: null })} /></label>
      {targets ? <>
        <p className="muted small">全项目有效对象 · 共 {targets.total} 项 · 当前 {targets.offset + 1}–{targets.offset + targets.targets.length}；列表不会自动选择对象。</p>
        <fieldset className="decision-target-list"><legend>请选择明确对象</legend>{targets.targets.map(target => <label key={`${target.entity_id}:${target.claim_id}`} className="decision-target" data-target-id={target.entity_id}>
          <input type="radio" name="decision-target" checked={draft.target?.claim_id === target.claim_id} disabled={busy} onChange={() => onDraft({ ...draft, target, intent: null })} />
          <span><strong>{target.label}</strong><small>{kindNames[target.kind]} · 对象 ID {target.entity_id} · 版本记录 #{target.claim_id}</small><Scope value={target.scope} />
            {target.label_truncated === true && <small className="missing-note">当前仅为名称预览，共 {target.label_total_bytes} UTF-8 字节；不能视为完整名称。</small>}
            {target.label_truncated == null && <small className="missing-note">名称完整性未知，请按对象 ID 和版本记录核对。</small>}</span>
        </label>)}</fieldset>
        {!targets.targets.length && <Empty title="没有匹配的有效对象">可以调整字面搜索，不能把未显示的对象猜成唯一匹配。</Empty>}
        <div className="pagination"><button type="button" className="button secondary" disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 200))}>上一页对象</button>
          <button type="button" className="button secondary" disabled={busy || targets.next_offset == null} onClick={() => setOffset(targets.next_offset!)}>下一页对象</button></div>
      </> : !failure && <Loading />}
      {draft.target && <p className="decision-selection">你已选择对象 ID：<code>{draft.target.entity_id}</code><span>版本记录 #{draft.target.claim_id}</span></p>}
      {failure && <p className="error-message" role="alert">{failure}</p>}
      {(needsRefresh || failure && !targets) && <button type="button" className="button secondary" onClick={refresh}>读取最新版本</button>}
      <footer><span className="muted small">记录者 {actor.slice(6) || '姓名未填写'}</span><button type="submit" className="button primary" disabled={busy || needsRefresh || !targets || !draft.target}>{busy ? '正在保存…' : '确认选择的对象'}</button></footer>
    </form>}
    {!original && failure && <><p className="error-message" role="alert">{failure}</p><button className="button secondary" onClick={refresh}>读取最新版本</button></>}
  </section></div>;
}
