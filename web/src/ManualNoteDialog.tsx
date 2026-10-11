import { useRef, useState } from 'react';
import { api, ApiError } from './api';
import { Icon } from './components';
import { noteRequest, validateNoteResult } from './manualNote';
import type { NoteDraft } from './manualNote';
import type { NoteResult, Project } from './types';
import './manualQuestion.css';
import './notes.css';

export function ManualNoteDialog({ project, revision, actor, draft, onDraft, onClose, onCreated, onFailure, onRefresh }: {
  project: Project; revision: number | null; actor: string; draft: NoteDraft;
  onDraft: (draft: NoteDraft) => void; onClose: () => void; onCreated: (result: NoteResult) => void;
  onFailure: (error: unknown, requestId: string) => void; onRefresh: () => void;
}) {
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState(''), [needsRefresh, setNeedsRefresh] = useState(false);
  const inFlight = useRef(false);
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault(); if (inFlight.current || needsRefresh || revision == null) return;
    let requestId = '';
    try {
      const prepared = noteRequest(draft, project.project_id, actor, revision); requestId = prepared.request.request_id;
      onDraft(prepared.draft); inFlight.current = true; setBusy(true); setFailure('');
      onCreated(validateNoteResult(await api<NoteResult>('/records/note', prepared.request), prepared.request));
    } catch (error) {
      setFailure(error instanceof Error ? error.message : '保存笔记失败，输入已保留。');
      if (error instanceof ApiError && error.status === 409) setNeedsRefresh(true);
      if (requestId) onFailure(error, requestId);
    } finally { inFlight.current = false; setBusy(false); }
  }
  return <div className="modal-backdrop" onClick={() => { if (!busy) onClose(); }}>
    <section className="edit-modal manual-question-modal" role="dialog" aria-modal="true" aria-labelledby="manual-note-title" onClick={event => event.stopPropagation()}>
      <header><div><span className="eyebrow">人工直接记录</span><h2 id="manual-note-title">新增研究笔记</h2></div><button className="icon-button" aria-label="关闭新增笔记" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
      <p className="manual-project">保存到项目：<strong>{project.name}</strong></p>
      <form onSubmit={event => { void submit(event); }}>
        <label>笔记正文<textarea aria-label="笔记正文" autoFocus rows={5} required disabled={busy} value={draft.text} onChange={event => onDraft({ ...draft, text: event.target.value, intent: null })} /><span className="muted small">原样保存空白与换行。笔记不代替采用或证据判断。</span></label>
        <label>关联对象 ID（可留空）<input aria-label="关联对象 ID（可留空）" className="mono" value={draft.aboutText} disabled={busy} onChange={event => onDraft({ ...draft, aboutText: event.target.value, intent: null })} /><span className="muted small">只关联本项目对象；留空不指定对象，不从对象继承范围。</span></label>
        <label>笔记范围（可留空）<textarea aria-label="笔记范围（可留空）" className="mono" rows={2} value={draft.scopeText} disabled={busy} onChange={event => onDraft({ ...draft, scopeText: event.target.value, intent: null })} /><span className="muted small">每行一个 字段=值，已知时填写完整范围；留空记为范围未知。</span></label>
        <p className="notice">你的笔记保存为人工确认记录，保留原文和关联对象；Agent 提议的笔记仍需复核。</p>
        {failure && <p className="error-message" role="alert">{failure}</p>}
        {needsRefresh && <div className="manual-refresh"><p>请先读取最新版本，再手动保存；输入已保留。</p><button type="button" className="button secondary" onClick={() => { onRefresh(); setNeedsRefresh(false); setFailure(''); }}>读取最新版本</button></div>}
        <footer><span className="muted small">记录者 {actor.slice(6) || '姓名未填写'} · 版本 {revision ?? '读取中'}</span><button type="submit" className="button primary" disabled={busy || needsRefresh || revision == null}>{busy ? '正在保存…' : '保存研究笔记'}</button></footer>
      </form>
    </section>
  </div>;
}
