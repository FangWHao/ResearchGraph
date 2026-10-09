import { useRef, useState } from 'react';
import { api, ApiError } from './api';
import { Icon } from './components';
import { questionRequest } from './manualQuestion';
import type { QuestionDraft } from './manualQuestion';
import type { Project, QuestionResult } from './types';
import './manualQuestion.css';

export function ManualQuestionDialog({ project, revision, actor, draft, onDraft, onClose, onCreated, onError, onRefresh }: {
  project: Project; revision: number | null; actor: string; draft: QuestionDraft;
  onDraft: (draft: QuestionDraft) => void; onClose: () => void;
  onCreated: (result: QuestionResult) => void; onError: (error: unknown) => void; onRefresh: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const inFlight = useRef(false);
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current || needsRefresh || revision == null) return;
    try {
      const prepared = questionRequest(draft, project.project_id, actor, revision);
      onDraft(prepared.draft);
      inFlight.current = true; setBusy(true); setFailure('');
      const result = await api<QuestionResult>('/records/question', prepared.request);
      onCreated(result);
    } catch (error) {
      setFailure(error instanceof Error ? error.message : '保存研究问题失败，输入已保留。');
      if (error instanceof ApiError && error.status === 409) setNeedsRefresh(true);
      if (inFlight.current) onError(error);
    } finally {
      inFlight.current = false; setBusy(false);
    }
  }
  return <div className="modal-backdrop" onClick={() => { if (!busy) onClose(); }}>
    <section className="edit-modal manual-question-modal" role="dialog" aria-modal="true" aria-labelledby="manual-question-title" onClick={event => event.stopPropagation()}>
      <header><div><span className="eyebrow">人工直接记录</span><h2 id="manual-question-title">新增研究问题</h2></div>
        <button className="icon-button" aria-label="关闭新增问题" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
      <p className="manual-project">保存到项目：<strong>{project.name}</strong></p>
      <form onSubmit={event => { void submit(event); }}>
        <label>研究问题正文<textarea aria-label="研究问题正文" autoFocus value={draft.text} onChange={event => onDraft({ ...draft, text: event.target.value, intent: null })} rows={5} required disabled={busy} />
          <span className="muted small">按你输入的原文保存，不删改前后空白或换行。</span></label>
        <label>研究范围（可留空）<textarea aria-label="研究范围（可留空）" value={draft.scopeText} onChange={event => onDraft({ ...draft, scopeText: event.target.value, intent: null })} rows={3} className="mono" disabled={busy} />
          <span className="muted small">已知范围需完整填写，每行一个 字段=值；留空保存为范围未知。</span></label>
        <p className="notice">由你亲自输入的问题保存为人工确认记录，并保留可查看的原文。</p>
        {failure && <p className="error-message" role="alert">{failure}</p>}
        {needsRefresh && <div className="manual-refresh"><p>请先读取最新版本，再手动保存；输入已保留。</p>
          <button type="button" className="button secondary" onClick={() => { onRefresh(); setNeedsRefresh(false); setFailure(''); }}>读取最新版本</button></div>}
        <footer><span className="muted small">记录者 {actor.slice(6) || '姓名未填写'} · 版本 {revision ?? '读取中'}</span>
          <button type="submit" className="button primary" disabled={busy || needsRefresh || revision == null}>{busy ? '正在保存…' : '保存研究问题'}</button></footer>
      </form>
    </section>
  </div>;
}
