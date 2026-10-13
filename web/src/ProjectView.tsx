import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api';
import { fmtChars, fmtTime } from './format';
import { Icon, KindGlyph, Logo } from './Icons';
import { defaultZoom, rowNeighbors, ZOOM_LEVELS, type Align, type ViewMode } from './layout';
import { EventPanel, LanePanel } from './Panel';
import { Timeline, type Selection } from './Timeline';
import type { AppState, ProjectData, ProjectSummary, Status } from './types';
import { KIND_LABEL, KINDS } from './types';

const PHASE: Record<Status['phase'], string> = {
  idle: '',
  sync: '读取会话',
  consent: '等待确认',
  extract: '提取关键步骤',
  threads: '整理研究线',
  done: '整理完成',
  error: '整理出错',
};

function remembered<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = localStorage.getItem(key) as T | null;
    return value && allowed.includes(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // 浏览器不允许存储时只是不记住
  }
}

function zoomLabel(pxPerHour: number): string {
  if (pxPerHour >= 64) return '刻度 1 小时';
  if (pxPerHour >= 32) return '刻度 2 小时';
  if (pxPerHour >= 16) return '刻度 3 小时';
  if (pxPerHour >= 8) return '刻度 6 小时';
  return '刻度 1 天';
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
  const [view, setView] = useState<ViewMode>(() => remembered('rg.view', ['threads', 'sessions'], 'threads'));
  const [align, setAlign] = useState<Align>(() => remembered('rg.align', ['compact', 'time'], 'compact'));
  const [zoom, setZoom] = useState<number | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [jump, setJump] = useState({ n: 0, latest: false });
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

  // 打开项目、切换视图或对齐方式时：紧凑模式回到起点，真实时间模式滚到最新
  useEffect(() => {
    setJump((j) => ({ n: j.n + 1, latest: align === 'time' }));
  }, [project.id, view, align, data !== null]);

  const pxPerHour = useMemo(() => zoom ?? defaultZoom(data?.events ?? []), [zoom, data?.events]);
  const stepZoom = useCallback(
    (direction: 1 | -1) => {
      const i = ZOOM_LEVELS.indexOf(pxPerHour);
      const next = ZOOM_LEVELS[Math.max(0, Math.min(ZOOM_LEVELS.length - 1, i + direction))];
      setZoom(next);
    },
    [pxPerHour],
  );

  const changeView = (next: ViewMode) => {
    setView(next);
    setSelection(null);
    remember('rg.view', next);
  };
  const changeAlign = (next: Align) => {
    setAlign(next);
    remember('rg.align', next);
  };

  // 键盘：Esc 关闭，←/→ 同一行的前后事件，+/− 缩放
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (e.key === 'Escape') setSelection(null);
      if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && selection?.type === 'event' && data) {
        const { prev, next } = rowNeighbors(data.events, view, selection.id);
        const to = e.key === 'ArrowLeft' ? prev : next;
        if (to) {
          e.preventDefault();
          setSelection({ type: 'event', id: to.id });
        }
      }
      if (align === 'time' && (e.key === '+' || e.key === '=')) stepZoom(1);
      if (align === 'time' && e.key === '-') stepZoom(-1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selection, data, view, align, stepZoom]);

  const organize = async (options: { consent?: boolean; full?: boolean } = {}) => {
    try {
      setStatus(await api.organize(project.id, options));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const remove = async () => {
    if (!window.confirm(`删除项目「${project.name}」？只删除整理结果，不会动原始会话文件。`)) return;
    try {
      await api.deleteProject(project.id);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const running = status?.running ?? false;
  const selectedEvent = selection?.type === 'event' ? data?.events.find((e) => e.id === selection.id) ?? null : null;
  const selectEvent = (id: number) => setSelection({ type: 'event', id });
  const progress = running && status?.total ? Math.round((status.done / status.total) * 100) : null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <Logo />
          <span>ResearchGraph</span>
        </div>
        <select className="select project-switch" value={project.id} aria-label="切换项目" onChange={(e) => onSwitch(Number(e.target.value))}>
          {state.projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <button type="button" className="icon-btn" title="新建项目" aria-label="新建项目" onClick={onNew}>
          <Icon name="plus" />
        </button>
        <span className="divider" />
        <Segmented
          label="泳道"
          value={view}
          options={[
            ['threads', '研究线'],
            ['sessions', '会话'],
          ]}
          onChange={changeView}
        />
        <Segmented
          label="横轴"
          value={align}
          options={[
            ['compact', '紧凑'],
            ['time', '真实时间'],
          ]}
          onChange={changeAlign}
        />
        {align === 'time' ? (
          <div className="zoom" role="group" aria-label="缩放">
            <button type="button" className="icon-btn" title="缩小（−）" aria-label="缩小" disabled={pxPerHour === ZOOM_LEVELS[0]} onClick={() => stepZoom(-1)}>
              <Icon name="minus" size={14} />
            </button>
            <span className="zoom-label">{zoomLabel(pxPerHour)}</span>
            <button type="button" className="icon-btn" title="放大（+）" aria-label="放大" disabled={pxPerHour === ZOOM_LEVELS.at(-1)} onClick={() => stepZoom(1)}>
              <Icon name="plus" size={14} />
            </button>
          </div>
        ) : null}
        <span className="spacer" />
        <StatusPill status={status} data={data} />
        <button type="button" className="btn primary" disabled={running} onClick={() => organize()}>
          <Icon name="refresh" size={15} />
          {running ? '整理中' : '更新'}
        </button>
        <Menu
          items={[
            {
              label: '重新整理',
              hint: '保留手动改过的内容',
              disabled: running,
              onClick: () =>
                window.confirm('清掉自动生成的研究线和事件，从头整理？你手动改过和删掉的内容会保留。') &&
                organize({ full: true }),
            },
            { label: '设置', onClick: onSettings },
            { label: '删除项目', danger: true, onClick: remove },
          ]}
        />
      </header>
      <div className="progress-line" aria-hidden="true">
        {progress !== null ? <div style={{ width: `${Math.max(progress, 3)}%` }} /> : running ? <div className="indeterminate" /> : null}
      </div>

      <Banner
        status={status}
        model={state.model}
        onConsent={() => organize({ consent: true })}
        onRetry={() => organize()}
        onSettings={onSettings}
      />

      <main className="workspace">
        {error ? <div className="empty error">{error}</div> : null}
        {!error && data === null ? <div className="empty muted">加载中…</div> : null}
        {!error && data && data.events.length === 0 ? (
          <div className="empty">
            {running ? (
              <>
                <h2>正在整理</h2>
                <p className="muted">完成后这里会出现时间线。</p>
              </>
            ) : status?.phase === 'consent' ? (
              <>
                <h2>确认后开始整理</h2>
                <p className="muted">上面列出了要发送给模型的内容量。</p>
              </>
            ) : (
              <>
                <h2>还没有事件</h2>
                <p className="muted">点“更新”读取新会话并整理。会话很短或只是闲聊时，可能没有值得记录的步骤。</p>
              </>
            )}
          </div>
        ) : null}
        {!error && data && data.events.length > 0 ? (
          <>
            <Timeline
              data={data}
              view={view}
              align={align}
              pxPerHour={pxPerHour}
              selection={selection}
              onSelect={setSelection}
              jump={jump}
            />
            {selectedEvent ? (
              <EventPanel data={data} event={selectedEvent} onChanged={load} onClose={() => setSelection(null)} onSelectEvent={selectEvent} />
            ) : null}
            {selection?.type === 'lane' ? (
              <LanePanel data={data} lane={selection.lane} onChanged={load} onClose={() => setSelection(null)} onSelectEvent={selectEvent} />
            ) : null}
          </>
        ) : null}
      </main>

      <footer className="footbar">
        <div className="legend" aria-label="图例">
          {KINDS.map((k) => (
            <span key={k}>
              <KindGlyph kind={k} />
              {KIND_LABEL[k]}
            </span>
          ))}
        </div>
        {data ? (
          <span className="muted">
            {data.sessions.length} 个会话 · {data.events.length} 个事件
          </span>
        ) : null}
        <span className="spacer" />
        <span className="muted hide-narrow">←/→ 切换事件 · Esc 关闭</span>
        <button type="button" className="btn small ghost" onClick={() => setJump((j) => ({ n: j.n + 1, latest: true }))}>
          最新
          <Icon name="toEnd" size={14} />
        </button>
      </footer>
    </div>
  );
}

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: [T, string][];
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      <span className="segmented-label">{label}</span>
      {options.map(([key, text]) => (
        <button key={key} type="button" role="radio" aria-checked={value === key} className={value === key ? 'on' : ''} onClick={() => onChange(key)}>
          {text}
        </button>
      ))}
    </div>
  );
}

interface MenuItem {
  label: string;
  hint?: string;
  danger?: boolean;
  disabled?: boolean;
  onClick: () => void;
}

function Menu({ items }: { items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('pointerdown', close);
    return () => window.removeEventListener('pointerdown', close);
  }, [open]);
  return (
    <div className="menu" ref={root}>
      <button type="button" className="icon-btn" aria-label="更多" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="more" />
      </button>
      {open ? (
        <div className="menu-list" role="menu">
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              className={item.danger ? 'danger' : ''}
              onClick={() => {
                setOpen(false);
                item.onClick();
              }}
            >
              <span>{item.label}</span>
              {item.hint ? <span className="muted small">{item.hint}</span> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function StatusPill({ status, data }: { status: Status | null; data: ProjectData | null }) {
  if (!status || !data) return null;
  if (status.running) {
    return (
      <span className="pill">
        <span className="spinner" />
        {PHASE[status.phase]}
        {status.total ? <span className="muted"> {status.done}/{status.total}</span> : null}
      </span>
    );
  }
  if (status.phase === 'error') {
    return (
      <span className="pill warn">
        <Icon name="alert" size={13} />
        整理出错
      </span>
    );
  }
  if (status.phase === 'consent') return <span className="pill">等待确认</span>;
  const pending = status.estimate && status.estimate.calls > 0 && data.project.model_allowed;
  return (
    <span className="pill" title={data.project.organized_at ? `上次整理 ${fmtTime(data.project.organized_at)}` : ''}>
      {pending ? (
        <>
          <span className="dot-new" />有新内容 {fmtChars(status.estimate!.chars)}
        </>
      ) : (
        <>
          <Icon name="check" size={13} />
          {data.project.organized_at ? `已整理 ${fmtTime(data.project.organized_at)}` : '还没有整理过'}
        </>
      )}
    </span>
  );
}

function Banner({
  status,
  model,
  onConsent,
  onRetry,
  onSettings,
}: {
  status: Status | null;
  model: AppState['model'];
  onConsent: () => void;
  onRetry: () => void;
  onSettings: () => void;
}) {
  if (!status || status.running) return null;
  const estimate = status.estimate;
  if (status.phase === 'consent' && estimate) {
    return (
      <div className="banner">
        <div className="banner-text">
          <strong>第一次整理这个项目</strong>
          <span>
            会把 {estimate.sessions} 个会话中{fmtChars(estimate.chars)}的文字发送给 {model.model}，大约调用 {estimate.calls}{' '}
            次。之后点“更新”只发送新增的内容。
          </span>
        </div>
        {model.has_key ? (
          <button type="button" className="btn primary" onClick={onConsent}>
            同意并开始整理
          </button>
        ) : (
          <button type="button" className="btn primary" onClick={onSettings}>
            先填写 API Key
          </button>
        )}
      </div>
    );
  }
  if (status.phase === 'error') {
    return (
      <div className="banner warn">
        <Icon name="alert" size={18} />
        <div className="banner-text">
          <strong>整理没有完成</strong>
          {status.errors.length ? (
            status.errors.map((line) => <span key={line}>{line}</span>)
          ) : (
            <span>整理出错，可以重试。</span>
          )}
        </div>
        <button type="button" className="btn" onClick={onSettings}>
          设置
        </button>
        <button type="button" className="btn primary" onClick={onRetry}>
          重试
        </button>
      </div>
    );
  }
  return null;
}
