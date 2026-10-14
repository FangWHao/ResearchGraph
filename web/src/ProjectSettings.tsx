import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { Switch } from './Controls';
import { fmtSize, fmtTime } from './format';
import { KindGlyph } from './Icons';
import type { HiddenEvent, ProjectData, SessionInfo } from './types';
import { TOOL_LABEL } from './types';

interface Props {
  projectId: number;
  onBack: () => void;
}

export function ProjectSettings({ projectId, onBack }: Props) {
  const [data, setData] = useState<ProjectData | null>(null);
  const [hidden, setHidden] = useState<HiddenEvent[]>([]);
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');

  const load = useCallback(async () => {
    try {
      const [next, gone] = await Promise.all([api.project(projectId), api.hidden(projectId)]);
      setData(next);
      setHidden(gone.events);
      setName((current) => current || next.project.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (action: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    setError('');
    setNote('');
    try {
      await action();
      await load();
      if (done) setNote(done);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const counts = useMemo(() => {
    const map = new Map<number, number>();
    for (const e of data?.events ?? []) map.set(e.session_id, (map.get(e.session_id) ?? 0) + 1);
    return map;
  }, [data]);

  if (!data) {
    return <div className="page">{error ? <div className="error">{error}</div> : <div className="muted">加载中…</div>}</div>;
  }
  const project = data.project;

  return (
    <div className="page">
      <header className="page-head">
        <h1>项目设置</h1>
        <p>{project.name}</p>
      </header>

      <section className="card">
        <h2>项目</h2>
        <label className="field">
          <span>名称</span>
          <div className="row">
            <input className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
            <button
              type="button"
              className="btn"
              disabled={busy || !name.trim() || name.trim() === project.name}
              onClick={() => act(() => api.patchProject(projectId, { name: name.trim() }), '已改名')}
            >
              改名
            </button>
          </div>
        </label>
        {project.workspace ? (
          <div className="option">
            <div className="option-text">
              <span className="option-title">跟随工作目录</span>
              <span className="option-hint mono">{project.workspace}</span>
              <span className="option-hint">打开时，这个目录和子目录里新开的会话自动加入项目。移出过的会话不会再加回来。</span>
            </div>
            <Switch
              checked={!!project.follow}
              label="跟随工作目录"
              onChange={(follow) => act(() => api.patchProject(projectId, { follow }))}
            />
          </div>
        ) : (
          <p className="muted small">这个项目是手动挑选会话建的，不会自动加入新会话；可以在下面添加。</p>
        )}
      </section>

      <section className="card">
        <h2>会话 · {data.sessions.length}</h2>
        <p>移出项目时，它的消息和事件（包括手动改过的）一起删掉；会话文件本身不动。</p>
        <div className="list">
          {data.sessions.map((s) => (
            <div key={s.id} className="session-row">
              <div className="choice-body">
                <strong>{s.title ?? '无标题'}</strong>
                <span className="muted small">
                  {TOOL_LABEL[s.tool]} · {fmtTime(s.started_at)} · {s.message_count} 条消息 · {counts.get(s.id) ?? 0} 个事件
                  {s.fork_of ? ' · fork' : ''}
                  {s.missing ? ' · 原文件已不在' : ''}
                </span>
              </div>
              <button
                type="button"
                className="btn small ghost"
                disabled={busy}
                onClick={() => {
                  const n = counts.get(s.id) ?? 0;
                  if (window.confirm(`把「${s.title ?? '无标题'}」移出项目？它的 ${n} 个事件会一起删掉，会话文件不受影响。`)) {
                    void act(() => api.removeSession(projectId, s.id), '已移出');
                  }
                }}
              >
                移出
              </button>
            </div>
          ))}
        </div>
      </section>

      <AddSessions
        projectId={projectId}
        known={new Set(data.sessions.map((s) => s.path))}
        busy={busy}
        act={act}
      />

      <section className="card">
        <h2>已删除的事件 · {hidden.length}</h2>
        {hidden.length ? (
          <>
            <p>恢复后回到原来的位置；原来的分支已经没了，就跟着同一会话里最近的事件走。</p>
            <div className="list">
              {hidden.map((e) => (
                <div key={e.id} className="session-row">
                  <KindGlyph kind={e.kind} />
                  <div className="choice-body">
                    <strong>{e.title}</strong>
                    <span className="muted small">
                      {fmtTime(e.ts)} · {e.session_title ?? '无标题'}
                    </span>
                  </div>
                  <button type="button" className="btn small" disabled={busy} onClick={() => act(() => api.restoreEvent(e.id), '已恢复')}>
                    恢复
                  </button>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="muted small">没有删除过事件。</p>
        )}
      </section>

      {error ? <div className="error">{error}</div> : null}
      <div className="row end">
        {note ? <span className="ok small">✓ {note}</span> : null}
        <span className="spacer" />
        <button type="button" className="btn primary" onClick={onBack}>
          回到时间线
        </button>
      </div>
    </div>
  );
}

function AddSessions({
  projectId,
  known,
  busy,
  act,
}: {
  projectId: number;
  known: Set<string>;
  busy: boolean;
  act: (action: () => Promise<unknown>, done?: string) => Promise<void>;
}) {
  const [mode, setMode] = useState<'pick' | 'upload'>('pick');
  const [listing, setListing] = useState<SessionInfo[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('');
  const [over, setOver] = useState(false);

  useEffect(() => {
    if (mode === 'pick' && listing === null) {
      api.sessions().then(
        (r) => setListing(r.sessions),
        () => setListing([]),
      );
    }
  }, [mode, listing]);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return (listing ?? []).filter(
      (s) => !known.has(s.path) && (!needle || `${s.title ?? ''} ${s.cwd ?? ''}`.toLowerCase().includes(needle)),
    );
  }, [listing, filter, known]);

  const upload = (files: File[]) =>
    act(async () => {
      for (const file of files) await api.upload(projectId, file);
    }, `已添加 ${files.length} 个文件，回到时间线后点“更新”整理`);

  return (
    <section className="card">
      <h2>添加会话</h2>
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={mode === 'pick'} className={mode === 'pick' ? 'on' : ''} onClick={() => setMode('pick')}>
          从已发现的会话中选
        </button>
        <button type="button" role="tab" aria-selected={mode === 'upload'} className={mode === 'upload' ? 'on' : ''} onClick={() => setMode('upload')}>
          上传文件
        </button>
      </div>
      {mode === 'pick' ? (
        <>
          <input className="input" placeholder="按标题或目录筛选" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <div className="list">
            {listing === null ? <div className="muted">正在查找会话…</div> : null}
            {listing && !shown.length ? <div className="muted small">没有可以添加的会话。</div> : null}
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
          <div className="row end">
            <button
              type="button"
              className="btn"
              disabled={busy || picked.size === 0}
              onClick={() =>
                act(async () => {
                  await api.addSessions(projectId, [...picked]);
                  setPicked(new Set());
                }, `已添加 ${picked.size} 个会话，回到时间线后点“更新”整理`)
              }
            >
              {picked.size ? `添加 ${picked.size} 个会话` : '添加会话'}
            </button>
          </div>
        </>
      ) : (
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
            void upload(Array.from(e.dataTransfer.files));
          }}
        >
          <p>把 Claude Code 或 Codex 的 .jsonl 会话文件拖到这里</p>
          <input type="file" multiple accept=".jsonl,.json" disabled={busy} onChange={(e) => void upload(Array.from(e.target.files ?? []))} />
        </div>
      )}
    </section>
  );
}
