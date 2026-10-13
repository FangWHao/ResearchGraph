import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { basename, fmtSize, fmtTime } from './format';
import type { SessionInfo, Workspace } from './types';
import { TOOL_LABEL } from './types';

type Mode = 'auto' | 'pick' | 'upload';

interface Props {
  first: boolean;
  onCreated: (projectId: number) => void;
  onCancel?: () => void;
}

export function NewProject({ first, onCreated, onCancel }: Props) {
  const [mode, setMode] = useState<Mode>('auto');
  const [name, setName] = useState('');
  const [named, setNamed] = useState(false);
  const [listing, setListing] = useState<{ sessions: SessionInfo[]; workspaces: Workspace[] } | null>(null);
  const [workspace, setWorkspace] = useState<string | null>(null);
  const [follow, setFollow] = useState(true);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [over, setOver] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .sessions()
      .then(setListing)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return (listing?.sessions ?? []).filter(
      (s) => !needle || `${s.title ?? ''} ${s.cwd ?? ''}`.toLowerCase().includes(needle),
    );
  }, [listing, filter]);

  const chooseWorkspace = (cwd: string) => {
    setWorkspace(cwd);
    if (!named) setName(basename(cwd));
  };

  const ready =
    name.trim() &&
    ((mode === 'auto' && workspace) || (mode === 'pick' && picked.size > 0) || (mode === 'upload' && files.length > 0));

  const create = async () => {
    setBusy(true);
    setError('');
    try {
      let id: number;
      if (mode === 'auto') {
        id = (await api.createProject({ name: name.trim(), workspace: workspace!, follow })).id;
      } else if (mode === 'pick') {
        id = (await api.createProject({ name: name.trim(), sessions: [...picked] })).id;
      } else {
        id = (await api.createProject({ name: name.trim(), start: false })).id;
        for (const file of files) await api.upload(id, file);
        await api.organize(id);
      }
      onCreated(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      {first ? (
        <div className="steps">
          <span>① 找到会话</span> → <b>② 新建项目</b> → <span>③ 自动整理时间线</span>
        </div>
      ) : null}
      <h1>新建项目</h1>

      <div className="tabs" role="tablist">
        {(
          [
            ['auto', '按工作目录'],
            ['pick', '选会话'],
            ['upload', '上传文件'],
          ] as [Mode, string][]
        ).map(([key, label]) => (
          <button key={key} type="button" className={mode === key ? 'on' : ''} onClick={() => setMode(key)}>
            {label}
          </button>
        ))}
      </div>

      <section className="card">
        {mode === 'auto' ? (
          <>
            <p className="muted small">选一个工作目录，这个目录和子目录里的 Claude Code / Codex 会话都会加入项目。</p>
            <div className="list">
              {listing === null && !error ? <div className="muted">正在查找会话…</div> : null}
              {listing?.workspaces.length === 0 ? <div className="muted">没有找到带工作目录的会话。</div> : null}
              {listing?.workspaces.map((w) => (
                <label key={w.cwd} className={`choice${workspace === w.cwd ? ' on' : ''}`}>
                  <input type="radio" name="workspace" checked={workspace === w.cwd} onChange={() => chooseWorkspace(w.cwd)} />
                  <span className="choice-body">
                    <strong className="mono">{w.cwd}</strong>
                    <span className="muted small">
                      {w.sessions} 个会话
                      {w.claude ? ` · Claude Code ${w.claude}` : ''}
                      {w.codex ? ` · Codex ${w.codex}` : ''} · 最近 {fmtTime(w.last)}
                    </span>
                  </span>
                </label>
              ))}
            </div>
            <label className="row small">
              <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
              以后在这个目录里的新会话自动加入
            </label>
          </>
        ) : null}

        {mode === 'pick' ? (
          <>
            <input
              className="input"
              placeholder="按标题或目录筛选"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <div className="list">
              {shown.map((s) => (
                <label key={s.path} className={`choice${picked.has(s.path) ? ' on' : ''}`}>
                  <input
                    type="checkbox"
                    checked={picked.has(s.path)}
                    onChange={() => {
                      const next = new Set(picked);
                      if (next.has(s.path)) next.delete(s.path);
                      else next.add(s.path);
                      setPicked(next);
                    }}
                  />
                  <span className="choice-body">
                    <strong>{s.title ?? '无标题'}</strong>
                    <span className="muted small">
                      {TOOL_LABEL[s.tool]} · {fmtTime(s.started_at)} · {fmtSize(s.size)}
                    </span>
                    <span className="mono muted">{s.cwd}</span>
                  </span>
                </label>
              ))}
            </div>
            <div className="small muted">已选 {picked.size} 个会话</div>
          </>
        ) : null}

        {mode === 'upload' ? (
          <>
            <div
              className={`drop${over ? ' over' : ''}`}
              onDragOver={(e) => {
                e.preventDefault();
                setOver(true);
              }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setOver(false);
                setFiles([...files, ...Array.from(e.dataTransfer.files)]);
              }}
            >
              <p>把 Claude Code 或 Codex 的 .jsonl 会话文件拖到这里</p>
              <input
                type="file"
                multiple
                accept=".jsonl,.json"
                onChange={(e) => setFiles([...files, ...Array.from(e.target.files ?? [])])}
              />
            </div>
            {files.map((f, i) => (
              <div key={`${f.name}:${i}`} className="row small">
                <span className="mono">{f.name}</span>
                <span className="muted">{fmtSize(f.size)}</span>
                <span className="spacer" />
                <button type="button" className="link" onClick={() => setFiles(files.filter((_, j) => j !== i))}>
                  移除
                </button>
              </div>
            ))}
          </>
        ) : null}
      </section>

      <label className="field">
        <span>项目名称</span>
        <input
          className="input"
          value={name}
          maxLength={80}
          onChange={(e) => {
            setName(e.target.value);
            setNamed(true);
          }}
        />
      </label>

      {error ? <div className="error">{error}</div> : null}
      <div className="row end">
        {onCancel ? (
          <button type="button" className="btn" onClick={onCancel}>
            取消
          </button>
        ) : null}
        <button type="button" className="btn primary" disabled={!ready || busy} onClick={create}>
          {busy ? '创建中…' : '创建项目'}
        </button>
      </div>
    </div>
  );
}
