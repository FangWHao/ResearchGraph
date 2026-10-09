import { useRef, useState } from 'react';
import { api, ApiError } from './api';
import { Icon } from './components';
import { decisionRequest } from './manualDecision';
import type { DecisionDraft } from './manualDecision';
import type { DecisionAction, DecisionResult, Project } from './types';
import './manualDecision.css';

export function ManualDecisionDialog({ project, revision, actor, draft, onDraft, onClose, onCreated, onError, onRefresh }: {
  project: Project; revision: number | null; actor: string; draft: DecisionDraft;
  onDraft: (draft: DecisionDraft) => void; onClose: () => void; onCreated: (result: DecisionResult) => void;
  onError: (error: unknown, requestId: string) => void; onRefresh: () => void;
}) {
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState(''), [needsRefresh, setNeedsRefresh] = useState(false);
  const inFlight = useRef(false);
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current || needsRefresh || revision == null) return;
    let requestId: string | null = null;
    try {
      const prepared = decisionRequest(draft, project.project_id, actor, revision);
      requestId = prepared.request.request_id; onDraft(prepared.draft);
      inFlight.current = true; setBusy(true); setFailure('');
      onCreated(await api<DecisionResult>('/records/decide', prepared.request));
    } catch (error) {
      setFailure(error instanceof Error ? error.message : '保存决定失败，输入已保留。');
      if (error instanceof ApiError && error.status === 409) setNeedsRefresh(true);
      if (requestId) onError(error, requestId);
    } finally { inFlight.current = false; setBusy(false); }
  }
  const field = (values: Partial<DecisionDraft>) => onDraft({ ...draft, ...values, intent: null });
  return <div className="modal-backdrop" onClick={() => { if (!busy) onClose(); }}><section className="edit-modal manual-decision-modal" role="dialog" aria-modal="true" aria-label="记录人工决定" onClick={event => event.stopPropagation()}>
    <header><div><span className="eyebrow">人工直接记录</span><h2>记录人工决定</h2></div><button className="icon-button" aria-label="关闭人工决定" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
    <p className="manual-project">保存到项目：<strong>{project.name}</strong></p>
    <form onSubmit={event => { void submit(event); }}>
      <label>对象原话或对象 ID<textarea aria-label="决定对象原话" autoFocus value={draft.selector} onChange={event => field({ selector: event.target.value })} rows={2} required disabled={busy} /></label>
      <label>决定动作<select aria-label="人工决定动作" value={draft.action} onChange={event => field({ action: event.target.value as DecisionAction | '' })} required disabled={busy}>
        <option value="">请选择动作</option><option value="accept">采用</option><option value="defer">暂缓</option><option value="reject">拒绝</option><option value="withdraw">撤回</option></select></label>
      <label>决定理由<textarea aria-label="人工决定理由" value={draft.why} onChange={event => field({ why: event.target.value })} rows={3} required disabled={busy} /></label>
      <label>研究范围（可留空）<textarea aria-label="人工决定范围" value={draft.scopeText} onChange={event => field({ scopeText: event.target.value })} rows={2} className="mono" disabled={busy} />
        <span className="muted small">已知范围需完整填写，每行一个 字段=值。留空或明确未知时，不判断当前采用。</span></label>
      <p className="notice">对象唯一时保存人工确认；同名或找不到对象时进入对象复核。确认决定不会同时确认对象候选。</p>
      {failure && <p className="error-message" role="alert">{failure}</p>}
      {needsRefresh && <div className="manual-refresh"><p>请先读取最新版本，再手动保存；输入已保留。</p><button type="button" className="button secondary" onClick={() => { onRefresh(); setNeedsRefresh(false); setFailure(''); }}>读取最新版本</button></div>}
      <footer><span className="muted small">记录者 {actor.slice(6) || '姓名未填写'} · 版本 {revision ?? '读取中'}</span><button type="submit" className="button primary" disabled={busy || needsRefresh || revision == null}>{busy ? '正在保存…' : '保存人工决定'}</button></footer>
    </form>
  </section></div>;
}
