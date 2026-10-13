import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { fmtChars, fmtTime } from './format';
import type { ViewMode } from './layout';
import { EventPanel, LanePanel } from './Panel';
import { Timeline, type Selection } from './Timeline';
import type { AppState, ProjectData, ProjectSummary, Status } from './types';
import { KIND_LABEL, KINDS } from './types';

const PHASE: Record<Status['phase'], string> = {
  idle: '',
  sync: '正在读取会话',
  consent: '等待确认',
  extract: '正在提取关键步骤',
  threads: '正在整理研究线',
  done: '整理完成',
  error: '整理出错',
};
const VIEW_KEY = 'rg.view';

function savedView(): ViewMode {
  try {
    return localStorage.getItem(VIEW_KEY) === 'sessions' ? 'sessions' : 'threads';
  } catch {
    return 'threads';
  }
}

interface Props {
  project: ProjectSummary;
  state: AppState;
  onSwitch: (projectId: number) => void;
  onNew: () => void;
  onSettings: () => void;
  onDeleted: () => void;
}

export function ProjectView({ project, state, onSwitch, onNew, onSettings, onDeleted }: Props) {
  const [data, setData] = useState<ProjectData | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [mode, setMode] = useState<ViewMode>(savedView);
  const [selection, setSelection] = useState<Selection>(null);
  const [menu, setMenu] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const next = await api.project(project.id);
      setData(next);
      setStatus(next.status);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [project.id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!status?.running) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.status(project.id);
        setStatus(next);
        if (!next.running) void load();
      } catch {
        // 下一次轮询再试
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [status?.running, project.id, load]);

  const changeMode = (next: ViewMode) => {
    setMode(next);
    setSelection(null);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      // 浏览器不允许存储时只是不记住
    }
  };

  const organize = async (options: { consent?: boolean; full?: boolean } = {}) => {
    setMenu(false);
    try {
      setStatus(await api.organize(project.id, options));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const remove = async () => {
    setMenu(false);
    if (!window.confirm(`删除项目「${project.name}」？只删除整理结果，不会动原始会话文件。`)) return;
    try {
      await api.deleteProject(project.id);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const running = status?.running ?? false;
  const selectedEvent =
    selection?.type === 'event' ? data?.events.find((e) => e.id === selection.id) ?? null : null;

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">ResearchGraph</span>
        <select className="select" style={{ width: 'auto' }} value={project.id} onChange={(e) => onSwitch(Number(e.target.value))}>
          {state.projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <button type="button" className="btn small" onClick={onNew}>
          ＋ 新建项目
        </button>
        <div className="tabs">
          <button type="button" className={mode === 'threads' ? 'on' : ''} onClick={() => changeMode('threads')}>
            按研究线
          </button>
          <button type="button" className={mode === 'sessions' ? 'on' : ''} onClick={() => changeMode('sessions')}>
            按会话
          </button>
        </div>
        <span className="spacer" />
        <div className="legend">
          {KINDS.map((k) => (
            <span key={k} className={`kind-${k}`}>
              <i style={{ background: 'var(--kind)' }} />
              {KIND_LABEL[k]}
            </span>
          ))}
        </div>
        <button type="button" className="btn" disabled={running} onClick={() => organize()}>
          更新
        </button>
        <div className="menu">
          <button type="button" className="btn" aria-label="更多" onClick={() => setMenu(!menu)}>
            ⋯
          </button>
          {menu ? (
            <div className="menu-list">
              <button
                type="button"
                disabled={running}
                onClick={() =>
                  window.confirm('清掉自动生成的研究线和事件，从头整理？你手动改过和删掉的内容会保留。') &&
                  organize({ full: true })
                }
              >
                重新整理
              </button>
              <button type="button" onClick={onSettings}>
                设置
              </button>
              <button type="button" className="error" onClick={remove}>
                删除项目
              </button>
            </div>
          ) : null}
        </div>
      </header>

      <StatusBar
        status={status}
        data={data}
        model={state.model}
        onConsent={() => organize({ consent: true })}
        onRetry={() => organize()}
        onSettings={onSettings}
      />

      <main className="workspace">
        {error ? <div className="empty error">{error}</div> : null}
        {!error && data === null ? <div className="empty">加载中…</div> : null}
        {!error && data && data.events.length === 0 ? (
          <div className="empty">
            {running ? (
              <p>正在整理，完成后这里会出现时间线。</p>
            ) : status?.phase === 'consent' ? (
              <p>确认上方的发送量后开始整理。</p>
            ) : (
              <>
                <p>还没有事件。</p>
                <p className="small">点“更新”读取新会话并整理；会话很短或只是闲聊时可能没有可记录的步骤。</p>
              </>
            )}
          </div>
        ) : null}
        {!error && data && data.events.length > 0 ? (
          <>
            <Timeline data={data} mode={mode} selection={selection} onSelect={setSelection} />
            {selectedEvent ? (
              <EventPanel
                data={data}
                event={selectedEvent}
                onChanged={load}
                onClose={() => setSelection(null)}
              />
            ) : null}
            {selection?.type === 'lane' ? (
              <LanePanel data={data} lane={selection.lane} onChanged={load} onClose={() => setSelection(null)} />
            ) : null}
          </>
        ) : null}
      </main>
    </div>
  );
}

function StatusBar({
  status,
  data,
  model,
  onConsent,
  onRetry,
  onSettings,
}: {
  status: Status | null;
  data: ProjectData | null;
  model: AppState['model'];
  onConsent: () => void;
  onRetry: () => void;
  onSettings: () => void;
}) {
  if (!status || !data) return <div className="statusbar muted">…</div>;
  if (status.running) {
    const percent = status.total ? Math.round((status.done / status.total) * 100) : 0;
    return (
      <div className="statusbar">
        <span>{PHASE[status.phase]}</span>
        {status.total ? (
          <>
            <div className="progress">
              <div style={{ width: `${percent}%` }} />
            </div>
            <span className="muted">
              {status.done} / {status.total}
            </span>
          </>
        ) : null}
      </div>
    );
  }
  const estimate = status.estimate;
  if (status.phase === 'consent' && estimate) {
    return (
      <div className="statusbar warn">
        <span>
          第一次整理这个项目：会把 {estimate.sessions} 个会话中{fmtChars(estimate.chars)}的文字发送给 {model.model}，
          大约调用 {estimate.calls} 次。之后点“更新”只发送新增的内容。
        </span>
        {model.has_key ? (
          <button type="button" className="btn primary small" onClick={onConsent}>
            同意并开始整理
          </button>
        ) : (
          <button type="button" className="btn small" onClick={onSettings}>
            先填写 API Key
          </button>
        )}
      </div>
    );
  }
  const pending = estimate && estimate.calls > 0 && data.project.model_allowed;
  return (
    <div className={`statusbar${status.phase === 'error' ? ' warn' : ''}`}>
      {status.phase === 'error' ? (
        <>
          <span className="error">{status.errors.join('；') || '整理出错'}</span>
          <button type="button" className="btn small" onClick={onRetry}>
            重试
          </button>
        </>
      ) : (
        <span className="muted">
          {data.project.organized_at ? `上次整理 ${fmtTime(data.project.organized_at)}` : '还没有整理过'} ·{' '}
          {data.sessions.length} 个会话 · {data.events.length} 个事件
          {pending ? ` · 有新内容（${fmtChars(estimate.chars)}），点“更新”整理` : ''}
        </span>
      )}
    </div>
  );
}
