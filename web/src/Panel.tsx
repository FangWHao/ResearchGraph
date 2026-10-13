import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from './api';
import { fmtFull, fmtTime } from './format';
import { Icon, KindGlyph } from './Icons';
import { compareEvents, type Lane } from './layout';
import type { Branch, Kind, Message, ProjectData, TimelineEvent } from './types';
import { KIND_LABEL, KINDS, TOOL_LABEL } from './types';

const ROLE: Record<Message['role'], string> = { user: '用户', assistant: 'Agent', tool: '工具' };
const NEW_THREAD = '__new__';

interface Common {
  data: ProjectData;
  onChanged: () => void;
  onClose: () => void;
  onSelectEvent: (eventId: number) => void;
}

function useAction(onChanged: () => void) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await action();
      onChanged();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { error, busy, run };
}

function branchName(branch: Branch | undefined, branches: Branch[]): string {
  if (!branch) return '';
  if (branch.title) return branch.title;
  const first = Math.min(...branches.filter((b) => b.thread_id === branch.thread_id).map((b) => b.position));
  return branch.position === first ? '主线' : '分支';
}

function Shell({ chips, actions, children }: { chips: ReactNode; actions: ReactNode; children: ReactNode }) {
  return (
    <aside className="panel" onClick={(e) => e.stopPropagation()}>
      <header className="panel-head">
        <div className="panel-chips">{chips}</div>
        <div className="panel-actions">{actions}</div>
      </header>
      <div className="panel-body">{children}</div>
    </aside>
  );
}

function IconButton({ icon, label, onClick, active }: { icon: Parameters<typeof Icon>[0]['name']; label: string; onClick: () => void; active?: boolean }) {
  return (
    <button type="button" className={`icon-btn${active ? ' on' : ''}`} aria-label={label} title={label} onClick={onClick}>
      <Icon name={icon} />
    </button>
  );
}

function EventLink({ event, onSelect, children }: { event: TimelineEvent; onSelect: (id: number) => void; children?: ReactNode }) {
  return (
    <button type="button" className="event-link" onClick={() => onSelect(event.id)}>
      <KindGlyph kind={event.kind} />
      <span className="event-link-title">{event.title}</span>
      {children}
    </button>
  );
}

// ---------- 事件 ----------

export function EventPanel({ data, event, onChanged, onClose, onSelectEvent }: Common & { event: TimelineEvent }) {
  const [editing, setEditing] = useState(false);
  const [context, setContext] = useState(0);
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [loadError, setLoadError] = useState('');
  const { error, busy, run } = useAction(onChanged);
  const session = data.sessions.find((s) => s.id === event.session_id);
  const branch = data.branches.find((b) => b.id === event.branch_id);
  const thread = data.threads.find((t) => t.id === branch?.thread_id);

  useEffect(() => setEditing(false), [event.id]);

  useEffect(() => {
    let alive = true;
    setMessages(null);
    setLoadError('');
    api
      .messages(event.id, context)
      .then((r) => alive && setMessages(r.messages))
      .catch((e) => alive && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [event.id, context]);

  const relations = useMemo(() => {
    const byId = new Map(data.events.map((e) => [e.id, e]));
    const members = (branchId: number | null) =>
      data.events.filter((e) => e.branch_id === branchId).sort(compareEvents);
    const own = members(event.branch_id);
    const i = own.findIndex((e) => e.id === event.id);
    const isFirst = i === 0;
    const isLast = i === own.length - 1;
    return {
      prev: own[i - 1] ?? null,
      next: own[i + 1] ?? null,
      from: isFirst && branch?.from_event ? byId.get(branch.from_event) ?? null : null,
      into: isLast && branch?.into_event ? byId.get(branch.into_event) ?? null : null,
      out: data.branches
        .filter((b) => b.from_event === event.id)
        .map((b) => ({ branch: b, first: members(b.id)[0] }))
        .filter((x) => x.first),
      incoming: data.branches
        .filter((b) => b.into_event === event.id)
        .map((b) => ({ branch: b, last: members(b.id).at(-1) }))
        .filter((x) => x.last),
    };
  }, [data, event, branch]);

  return (
    <Shell
      chips={
        <>
          <span className="chip">
            <KindGlyph kind={event.kind} />
            {KIND_LABEL[event.kind]}
          </span>
          {event.edited ? <span className="chip muted">手动改过</span> : null}
        </>
      }
      actions={
        <>
          <IconButton icon="edit" label="编辑" active={editing} onClick={() => setEditing(!editing)} />
          <IconButton icon="close" label="关闭（Esc）" onClick={onClose} />
        </>
      }
    >
      {editing ? (
        <EventEditor
          event={event}
          busy={busy}
          onCancel={() => setEditing(false)}
          onSave={async (patch) => {
            if (await run(() => api.patchEvent(event.id, patch))) setEditing(false);
          }}
        />
      ) : (
        <section className="panel-intro">
          <h2>{event.title}</h2>
          {event.summary ? <p>{event.summary}</p> : null}
        </section>
      )}

      <dl className="facts">
        <dt>时间</dt>
        <dd>{fmtFull(event.ts)}</dd>
        <dt>会话</dt>
        <dd>
          {session ? (
            <>
              <span className="muted">{TOOL_LABEL[session.tool]} · </span>
              {session.title ?? '无标题'}
            </>
          ) : (
            `会话 ${event.session_id}`
          )}
        </dd>
        <dt>位置</dt>
        <dd>
          <PlacePicker data={data} event={event} busy={busy} run={run} />
          {thread ? null : <span className="muted small">尚未归入研究线</span>}
        </dd>
      </dl>

      {relations.prev || relations.next || relations.from || relations.into || relations.out.length || relations.incoming.length ? (
        <section className="relations">
          <div className="section-title">前后</div>
          {relations.from ? (
            <div className="relation">
              <span className="relation-label"><Icon name="branch" size={13} />分出自</span>
              <EventLink event={relations.from} onSelect={onSelectEvent} />
            </div>
          ) : null}
          {relations.prev ? (
            <div className="relation">
              <span className="relation-label"><Icon name="arrowLeft" size={13} />上一步</span>
              <EventLink event={relations.prev} onSelect={onSelectEvent} />
            </div>
          ) : null}
          {relations.next ? (
            <div className="relation">
              <span className="relation-label"><Icon name="arrowRight" size={13} />下一步</span>
              <EventLink event={relations.next} onSelect={onSelectEvent} />
            </div>
          ) : null}
          {relations.into ? (
            <div className="relation">
              <span className="relation-label"><Icon name="merge" size={13} />汇入</span>
              <EventLink event={relations.into} onSelect={onSelectEvent} />
            </div>
          ) : null}
          {relations.out.map(({ branch: b, first }) => (
            <div key={`o${b.id}`} className="relation">
              <span className="relation-label"><Icon name="branch" size={13} />分出「{branchName(b, data.branches)}」</span>
              <EventLink event={first} onSelect={onSelectEvent} />
            </div>
          ))}
          {relations.incoming.map(({ branch: b, last }) => (
            <div key={`i${b.id}`} className="relation">
              <span className="relation-label"><Icon name="merge" size={13} />「{branchName(b, data.branches)}」汇入</span>
              <EventLink event={last!} onSelect={onSelectEvent} />
            </div>
          ))}
        </section>
      ) : null}

      <section className="quotes">
        <div className="section-row">
          <span className="section-title">原话</span>
          <button type="button" className="link small" onClick={() => setContext(context ? 0 : 3)}>
            {context ? '只看引用' : '显示前后 3 条'}
          </button>
        </div>
        {loadError ? <div className="error small">{loadError}</div> : null}
        {messages === null && !loadError ? <div className="muted small">读取中…</div> : null}
        {messages?.map((m) => (
          <div key={m.idx} className={`quote ${m.role}${m.ref ? ' ref' : ''}`}>
            <div className="quote-head">
              <span>{ROLE[m.role]}</span>
              <span>{fmtTime(m.ts)}</span>
              <span className="muted">#{m.idx}</span>
            </div>
            <div className="quote-text">{m.text}</div>
          </div>
        ))}
      </section>

      {error ? <div className="error small">{error}</div> : null}
      <footer className="panel-foot">
        <button
          type="button"
          className="link danger small"
          disabled={busy}
          onClick={() => {
            if (window.confirm('从时间线上删除这个事件？以后自动整理也不会再加回来。')) {
              void run(async () => {
                await api.patchEvent(event.id, { hidden: true });
                onClose();
              });
            }
          }}
        >
          <Icon name="trash" size={13} />
          删除这个事件
        </button>
      </footer>
    </Shell>
  );
}

function EventEditor({
  event,
  busy,
  onSave,
  onCancel,
}: {
  event: TimelineEvent;
  busy: boolean;
  onSave: (patch: { title: string; summary: string; kind: Kind }) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(event.title);
  const [summary, setSummary] = useState(event.summary);
  const [kind, setKind] = useState<Kind>(event.kind);
  const dirty = title !== event.title || summary !== event.summary || kind !== event.kind;
  return (
    <form
      className="editor"
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty && title.trim()) onSave({ title: title.trim(), summary, kind });
      }}
    >
      <label className="field">
        <span>标题</span>
        <input className="input" value={title} maxLength={40} autoFocus onChange={(e) => setTitle(e.target.value)} />
      </label>
      <div className="field">
        <span>种类</span>
        <div className="kind-picker" role="radiogroup">
          {KINDS.map((k) => (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={kind === k}
              className={kind === k ? 'on' : ''}
              onClick={() => setKind(k)}
            >
              <KindGlyph kind={k} />
              {KIND_LABEL[k]}
            </button>
          ))}
        </div>
      </div>
      <label className="field">
        <span>摘要</span>
        <textarea className="textarea" rows={4} value={summary} onChange={(e) => setSummary(e.target.value)} />
      </label>
      <div className="row end">
        <button type="button" className="btn" onClick={onCancel}>
          取消
        </button>
        <button type="submit" className="btn primary" disabled={!dirty || busy || !title.trim()}>
          保存
        </button>
      </div>
    </form>
  );
}

function PlacePicker({
  data,
  event,
  busy,
  run,
}: {
  data: ProjectData;
  event: TimelineEvent;
  busy: boolean;
  run: (action: () => Promise<unknown>) => Promise<boolean>;
}) {
  const options = useMemo(() => {
    const threads = new Map(data.threads.map((t) => [t.id, t]));
    return data.branches
      .filter((b) => threads.has(b.thread_id))
      .sort((a, b) => a.thread_id - b.thread_id || a.position - b.position)
      .map((b) => ({ id: b.id, label: `${threads.get(b.thread_id)!.title} › ${branchName(b, data.branches)}` }));
  }, [data.branches, data.threads]);
  const move = (value: string) => {
    if (value === NEW_THREAD) {
      const name = window.prompt('新研究线的名称');
      if (!name?.trim()) return;
      void run(async () => {
        const created = await api.createThread(data.project.id, name.trim());
        await api.patchEvent(event.id, { branch_id: created.branch_id });
      });
    } else if (value) {
      void run(() => api.patchEvent(event.id, { branch_id: Number(value) }));
    }
  };
  return (
    <select className="select subtle" value={event.branch_id ?? ''} disabled={busy} onChange={(e) => move(e.target.value)}>
      {event.branch_id === null ? <option value="">（尚未归入研究线）</option> : null}
      {options.map((o) => (
        <option key={o.id} value={o.id}>
          {o.label}
        </option>
      ))}
      <option value={NEW_THREAD}>＋ 放进新研究线…</option>
    </select>
  );
}

// ---------- 泳道：研究线 / 会话 ----------

export function LanePanel({ data, lane, onChanged, onClose, onSelectEvent }: Common & { lane: Lane }) {
  const thread = data.threads.find((t) => t.id === lane.threadId);
  const session = data.sessions.find((s) => s.id === lane.sessionId);
  const close = <IconButton icon="close" label="关闭（Esc）" onClick={onClose} />;
  if (thread) {
    return (
      <ThreadPanel key={thread.id} data={data} threadId={thread.id} onChanged={onChanged} onClose={onClose} onSelectEvent={onSelectEvent} />
    );
  }
  if (session) {
    const events = data.events.filter((e) => e.session_id === session.id).sort(compareEvents);
    return (
      <Shell chips={<span className="chip">{TOOL_LABEL[session.tool]} 会话</span>} actions={close}>
        <section className="panel-intro">
          <h2>{session.title ?? '无标题'}</h2>
          {session.fork_of ? <p>从另一个会话 fork 出来，fork 之后的内容作为一条新分支。</p> : null}
        </section>
        <dl className="facts">
          <dt>时间</dt>
          <dd>
            {fmtFull(session.started_at)}
            <br />
            <span className="muted">到 {fmtFull(session.ended_at)}</span>
          </dd>
          <dt>进度</dt>
          <dd>
            已整理 {session.extracted_upto} / {session.message_count} 条消息
            {session.copied ? <span className="muted">（开头 {session.copied} 条复制自原会话）</span> : null}
          </dd>
          <dt>文件</dt>
          <dd className="mono">{session.path}</dd>
          {session.cwd ? (
            <>
              <dt>目录</dt>
              <dd className="mono">{session.cwd}</dd>
            </>
          ) : null}
        </dl>
        {session.missing ? <div className="callout warn small">原文件已经不在了，已读入的内容仍然保留。</div> : null}
        <EventList events={events} onSelect={onSelectEvent} />
      </Shell>
    );
  }
  return (
    <Shell chips={<span className="chip">未归类</span>} actions={close}>
      <p className="muted">这些事件还没有放进研究线，通常是整理中断了。点“更新”会重新安排，也可以点开事件手动指定位置。</p>
    </Shell>
  );
}

function EventList({ events, onSelect }: { events: TimelineEvent[]; onSelect: (id: number) => void }) {
  return (
    <section className="event-list">
      <div className="section-title">{events.length} 个事件</div>
      {events.map((e) => (
        <EventLink key={e.id} event={e} onSelect={onSelect}>
          <span className="event-link-time">{fmtTime(e.ts)}</span>
        </EventLink>
      ))}
    </section>
  );
}

function ThreadPanel({
  data,
  threadId,
  onChanged,
  onClose,
  onSelectEvent,
}: Omit<Common, 'data'> & { data: ProjectData; threadId: number }) {
  const thread = data.threads.find((t) => t.id === threadId)!;
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(thread.title);
  const [summary, setSummary] = useState(thread.summary);
  const { error, busy, run } = useAction(onChanged);
  const branches = data.branches.filter((b) => b.thread_id === threadId).sort((a, b) => a.position - b.position);
  const events = useMemo(() => [...data.events].sort(compareEvents), [data.events]);
  const count = events.filter((e) => branches.some((b) => b.id === e.branch_id)).length;

  return (
    <Shell
      chips={<span className="chip">研究线</span>}
      actions={
        <>
          <IconButton icon="edit" label="改名" active={editing} onClick={() => setEditing(!editing)} />
          <IconButton icon="close" label="关闭（Esc）" onClick={onClose} />
        </>
      }
    >
      {editing ? (
        <form
          className="editor"
          onSubmit={async (e) => {
            e.preventDefault();
            if (await run(() => api.patchThread(threadId, { title: title.trim(), summary }))) setEditing(false);
          }}
        >
          <label className="field">
            <span>名称</span>
            <input className="input" value={title} maxLength={30} autoFocus onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="field">
            <span>说明</span>
            <textarea className="textarea" rows={3} value={summary} onChange={(e) => setSummary(e.target.value)} />
          </label>
          <div className="row end">
            <button type="button" className="btn" onClick={() => setEditing(false)}>
              取消
            </button>
            <button type="submit" className="btn primary" disabled={busy || !title.trim()}>
              保存
            </button>
          </div>
        </form>
      ) : (
        <section className="panel-intro">
          <h2>{thread.title}</h2>
          {thread.summary ? <p>{thread.summary}</p> : null}
          <p className="muted small">
            {count} 个事件 · {branches.length} 条分支（含主线）
          </p>
        </section>
      )}
      <section className="branches">
        <div className="section-title">分支</div>
        {branches.map((b) => (
          <BranchRow key={b.id} data={data} branch={b} events={events} onChanged={onChanged} onSelectEvent={onSelectEvent} />
        ))}
        <button
          type="button"
          className="btn small ghost"
          disabled={busy}
          onClick={() => {
            const name = window.prompt('新分支的名称');
            if (name?.trim()) void run(() => api.createBranch(threadId, name.trim(), null));
          }}
        >
          <Icon name="plus" size={14} />
          新建分支
        </button>
      </section>
      {error ? <div className="error small">{error}</div> : null}
    </Shell>
  );
}

function BranchRow({
  data,
  branch,
  events,
  onChanged,
  onSelectEvent,
}: {
  data: ProjectData;
  branch: Branch;
  events: TimelineEvent[];
  onChanged: () => void;
  onSelectEvent: (id: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(branch.title);
  const { error, busy, run } = useAction(onChanged);
  const own = events.filter((e) => e.branch_id === branch.id);
  const byId = new Map(events.map((e) => [e.id, e]));
  const source = branch.from_event ? byId.get(branch.from_event) : undefined;
  const target = branch.into_event ? byId.get(branch.into_event) : undefined;
  const options = events.filter((e) => e.branch_id !== branch.id);
  const picker = (value: number | null, field: 'from_event' | 'into_event') => (
    <select
      className="select subtle"
      value={value ?? ''}
      disabled={busy}
      onChange={(e) => run(() => api.patchBranch(branch.id, { [field]: e.target.value ? Number(e.target.value) : null }))}
    >
      <option value="">（无）</option>
      {options.map((e) => (
        <option key={e.id} value={e.id}>
          {fmtTime(e.ts)} · {KIND_LABEL[e.kind]} · {e.title}
        </option>
      ))}
    </select>
  );
  return (
    <div className="branch">
      <div className="branch-head">
        <strong>{branchName(branch, data.branches)}</strong>
        <span className="muted small">{own.length} 个事件</span>
        <span className="spacer" />
        <IconButton icon="edit" label="编辑分支" active={editing} onClick={() => setEditing(!editing)} />
      </div>
      {!editing ? (
        <div className="branch-links">
          {source ? (
            <div className="relation">
              <span className="relation-label"><Icon name="branch" size={13} />分出自</span>
              <EventLink event={source} onSelect={onSelectEvent} />
            </div>
          ) : null}
          {own[0] ? (
            <div className="relation">
              <span className="relation-label"><Icon name="arrowRight" size={13} />第一步</span>
              <EventLink event={own[0]} onSelect={onSelectEvent} />
            </div>
          ) : null}
          {target ? (
            <div className="relation">
              <span className="relation-label"><Icon name="merge" size={13} />汇入</span>
              <EventLink event={target} onSelect={onSelectEvent} />
            </div>
          ) : null}
        </div>
      ) : (
        <div className="editor">
          <div className="row">
            <input className="input" value={title} placeholder="分支名称" maxLength={30} onChange={(e) => setTitle(e.target.value)} />
            <button type="button" className="btn" disabled={busy || title === branch.title} onClick={() => run(() => api.patchBranch(branch.id, { title }))}>
              改名
            </button>
          </div>
          <label className="field">
            <span>从哪个事件分出</span>
            {picker(branch.from_event, 'from_event')}
          </label>
          <label className="field">
            <span>汇入哪个事件</span>
            {picker(branch.into_event, 'into_event')}
          </label>
        </div>
      )}
      {error ? <div className="error small">{error}</div> : null}
    </div>
  );
}
