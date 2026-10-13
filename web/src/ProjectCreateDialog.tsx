import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { Icon } from './components';
import { parseProjectCreated, projectCreateRequest } from './projectCreate';
import type { ProjectCreateDraft, ProjectCreateResult } from './projectCreate';
import './projectCreate.css';

export function ProjectCreateDialog({ onClose, onCreated, onError }: { onClose: () => void; onCreated: (result: ProjectCreateResult) => void; onError: (error: unknown) => void }) {
  const [draft, setDraft] = useState<ProjectCreateDraft>({ name: '', root: '', collectLocal: false });
  const [busy, setBusy] = useState(false); const [failure, setFailure] = useState('');
  const active = useRef(true); const inFlight = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault(); if (inFlight.current) return;
    let sending = false;
    try {
      const body = projectCreateRequest(draft);
      inFlight.current = true; sending = true; setBusy(true); setFailure('');
      const raw = await api<unknown>('/projects', body);
      if (!active.current) return;
      onCreated(parseProjectCreated(raw, body));
    } catch (error) {
      if (!active.current) return;
      setFailure(error instanceof Error ? error.message : '创建结果暂不可核对；输入已保留，请重新读取项目列表后核对。');
      if (sending) onError(error);
    } finally { inFlight.current = false; if (active.current) setBusy(false); }
  }
  return <div className="modal-backdrop" onClick={() => { if (!inFlight.current) onClose(); }}>
    <section className="edit-modal project-create-modal" role="dialog" aria-modal="true" aria-labelledby="project-create-title" onClick={event => event.stopPropagation()} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); if (!inFlight.current) onClose(); } }}>
      <header><div><span className="eyebrow">本机研究工作区</span><h2 id="project-create-title">创建研究项目</h2></div><button className="icon-button" aria-label="关闭创建项目" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
      <form onSubmit={event => { void submit(event); }}>
        <label>项目名称<input aria-label="项目名称" autoFocus value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} disabled={busy} required placeholder="例如 Atlas" /></label>
        <label>工作区绝对目录<input aria-label="工作区绝对目录" value={draft.root} onChange={event => setDraft({ ...draft, root: event.target.value })} disabled={busy} required placeholder="/mnt/d/Documents/Atlas" /><span className="small muted">填写运行服务这台机器上的现存目录。WSL 使用 /mnt/d/…，Windows 使用 D:\…。</span></label>
        <label className="project-collect-option"><input type="checkbox" checked={draft.collectLocal} onChange={event => setDraft({ ...draft, collectLocal: event.target.checked })} disabled={busy} />登记本机 Claude Code / Codex 会话来源</label>
        <p className="small muted">勾选后登记服务用户已有的本地来源，按工作区目录识别项目；仅本地采集，远程模型许可另行配置。</p>
        {failure && <p className="error-message" role="alert">{failure} 输入已保留；不会自动重发。</p>}
        <footer><span className="small muted">同名同目录重复提交会打开已有项目。</span><button className="button primary" type="submit" disabled={busy}>{busy ? '正在创建…' : '创建并打开项目'}</button></footer>
      </form>
    </section>
  </div>;
}
