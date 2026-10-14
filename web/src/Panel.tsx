import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { fmtFull, fmtTime } from './format';
import { Icon, KindGlyph } from './Icons';
import { compareEvents, type Lane } from './layout';
import { highlight } from './Search';
import { api, type EventPatch } from './api';
import type { Branch, Importance, Kind, Message, Outcome, ProjectData, ThreadStatus, TimelineEvent } from './types';
import { IMPORTANCE_LABEL, KIND_LABEL, KINDS, OUTCOME_LABEL, OUTCOMES, STATUS_LABEL, TOOL_LABEL } from './types';

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
      <KindGlyph kind={event.kind} outcome={event.outcome} />
      <span className="event-link-title">{event.title}</span>
      {children}
    </button>
  );
}

// ---------- 事件 ----------

export function EventPanel({
  data,
  event,
  focus,
  terms,
  segment = [],
  opened = false,
  onToggle,
  onReveal,
  onChanged,
  onClose,
  onSelectEvent,
}: Common & {
  event: TimelineEvent;
  focus?: number;
  terms?: string[];
  // 这张卡片代表的那一段：当前详略下藏在它前后的事件
  segment?: TimelineEvent[];
  opened?: boolean;
  onToggle?: () => void;
  onReveal?: (eventId: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const quotes = useRef<HTMLElement>(null);
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
      .messages(event.id, context, focus)
      .then((r) => alive && setMessages(r.messages))
      .catch((e) => alive && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [event.id, context, focus]);

  // 从搜索结果点进来：把命中的那条原话滚到眼前
  useEffect(() => {
    quotes.current?.querySelector('.quote.focus')?.scrollIntoView({ block: 'center' });
  }, [messages]);

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
            <KindGlyph kind={event.kind} outcome={event.outcome} />
            {KIND_LABEL[event.kind]}
            {event.outcome ? ` · ${OUTCOME_LABEL[event.outcome]}` : ''}
          </span>
          {event.importance === 3 ? (
            <span className="chip">
              <Icon name="flag" size={12} />
              里程碑
            </span>
          ) : event.importance === 1 ? (
            <span className="chip muted">细节</span>
          ) : null}
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
        <section className={`panel-intro event-intro k-${event.kind}${event.importance === 3 ? ' milestone' : ''}${event.outcome === 'failed' ? ' failed' : ''}`}>
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

      {segment.length || opened ? (
        <section className="event-list">
          <div className="section-row">
            <span className="section-title">这一段的经过 · {segment.length}</span>
            {onToggle ? (
              <button type="button" className="link small" onClick={onToggle}>
                {opened ? '在时间线上收起' : '在时间线上展开'}
              </button>
            ) : null}
          </div>
          {[...segment].sort(compareEvents).map((e) => (
            <EventLink key={e.id} event={e} onSelect={(id) => (onReveal ? onReveal(id) : onSelectEvent(id))}>
              <span className="event-link-time">{fmtTime(e.ts)}</span>
            </EventLink>
          ))}
        </section>
      ) : null}

      <section className="quotes" ref={quotes}>
        <div className="section-row">
          <span className="section-title">原话</span>
          <button type="button" className="link small" onClick={() => setContext(context ? 0 : 3)}>
            {context ? '只看引用' : '显示前后 3 条'}
          </button>
        </div>
        {loadError ? <div className="error small">{loadError}</div> : null}
        {messages === null && !loadError ? <div className="muted small">读取中…</div> : null}
        {messages?.map((m) => (
          <div key={m.idx} className={`quote ${m.role}${m.ref ? ' ref' : ''}${m.focus ? ' focus' : ''}`}>
            <div className="quote-head">
              <span>{ROLE[m.role]}</span>
              <span>{fmtTime(m.ts)}</span>
              <span className="muted">#{m.idx}</span>
              {m.focus ? <span className="quote-tag">搜索命中</span> : null}
            </div>
            <div className="quote-text">{terms?.length ? highlight(m.text, terms) : m.text}</div>
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
  onSave: (patch: EventPatch) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(event.title);
  const [summary, setSummary] = useState(event.summary);
  // 旧版本的“问题”不能再选，编辑时先当成决定
  const [kind, setKind] = useState<Kind>(KINDS.includes(event.kind) ? event.kind : 'decision');
  const [outcome, setOutcome] = useState<Outcome>(event.outcome ?? 'open');
  const [importance, setImportance] = useState<Importance>(event.importance);
  const patch: EventPatch = {};
  if (title.trim() !== event.title) patch.title = title.trim();
  if (summary !== event.summary) patch.summary = summary;
  if (kind !== event.kind) patch.kind = kind;
  if (kind === 'attempt' && outcome !== event.outcome) patch.outcome = outcome;
  if (importance !== event.importance) patch.importance = importance;
  const dirty = Object.keys(patch).length > 0;
  return (
    <form
      className="editor"
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty && title.trim()) onSave(patch);
      }}
    >
      <label className="field">
        <span>标题</span>
        <input className="input" value={title} maxLength={40} autoFocus onChange={(e) => setTitle(e.target.value)} />
      </label>
      <div className="field">
        <span>种类</span>
        <div className="kind-picker" role="radiogroup" aria-label="种类">
          {KINDS.map((k) => (
            <button key={k} type="button" role="radio" aria-checked={kind === k} className={kind === k ? 'on' : ''} onClick={() => setKind(k)}>
              <KindGlyph kind={k} outcome={k === 'attempt' ? outcome : null} />
              {KIND_LABEL[k]}
            </button>
          ))}
        </div>
      </div>
      {kind === 'attempt' ? (
        <div className="field">
          <span>结果</span>
          <div className="kind-picker" role="radiogroup" aria-label="尝试的结果">
            {OUTCOMES.map((o) => (
              <button key={o} type="button" role="radio" aria-checked={outcome === o} className={outcome === o ? 'on' : ''} onClick={() => setOutcome(o)}>
                {o === 'success' ? '✓ ' : o === 'failed' ? '✗ ' : ''}
                {OUTCOME_LABEL[o]}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="field">
        <span>重要性</span>
        <div className="kind-picker" role="radiogroup" aria-label="重要性">
          {([3, 2, 1] as Importance[]).map((n) => (
            <button key={n} type="button" role="radio" aria-checked={importance === n} className={importance === n ? 'on' : ''} onClick={() => setImportance(n)}>
              {IMPORTANCE_LABEL[n]}
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

// ---------- 项目概览 ----------

export function OverviewPanel({ data, onClose, onSelectEvent }: Common) {
  const lines = useMemo(() => {
    const threadOf = new Map(data.branches.map((b) => [b.id, b.thread_id]));
    const groups = new Map<number, TimelineEvent[]>();
    for (const e of [...data.events].sort(compareEvents)) {
      const tid = e.branch_id === null ? undefined : threadOf.get(e.branch_id);
      if (tid === undefined) continue;
      groups.set(tid, [...(groups.get(tid) ?? []), e]);
    }
    return data.threads
      .filter((t) => groups.has(t.id))
      .map((t) => ({ thread: t, events: groups.get(t.id)! }))
      .sort((a, b) => compareEvents(a.events[0], b.events[0]));
  }, [data]);
  const order: Record<string, number> = { active: 0, done: 1, dropped: 2 };
  const counts = lines.reduce<Record<string, number>>((acc, l) => {
    const key = l.thread.status ?? 'active';
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <Shell chips={<span className="chip">项目概览</span>} actions={<IconButton icon="close" label="关闭（Esc）" onClick={onClose} />}>
      <section className="panel-intro">
        <h2>{data.project.name}</h2>
        <p className="muted small">
          {lines.length} 条研究线
          {Object.entries(counts)
            .sort(([a], [b]) => order[a] - order[b])
            .map(([k, n]) => ` · ${STATUS_LABEL[k as ThreadStatus]} ${n}`)
            .join('')}
          {' · '}
          {data.events.filter((e) => e.importance === 3).length} 个里程碑
        </p>
      </section>
      {lines.map(({ thread, events }) => {
        const milestones = events.filter((e) => e.importance === 3);
        const failed = events.filter((e) => e.outcome === 'failed').length;
        return (
          <section key={thread.id} className="overview-line">
            <div className="overview-head">
              <button
                type="button"
                className="overview-title"
                title="在时间线上跳到这条线"
                onClick={() => onSelectEvent((milestones[0] ?? events[0]).id)}
              >
                {thread.title}
              </button>
              {thread.status ? <span className="chip muted">{STATUS_LABEL[thread.status]}</span> : null}
              <span className="muted small">
                {span(events[0].ts, events[events.length - 1].ts)} · {events.length} 个事件
                {failed ? ` · ${failed} 次失败的尝试` : ''}
              </span>
            </div>
            {thread.summary ? (
              <p className="overview-summary" title={thread.summary}>
                {thread.summary}
              </p>
            ) : null}
            {milestones.map((e) => (
              <EventLink key={e.id} event={e} onSelect={onSelectEvent}>
                <span className="event-link-time">{fmtTime(e.ts)}</span>
              </EventLink>
            ))}
          </section>
        );
      })}
    </Shell>
  );
}

function span(start: string | null, end: string | null): string {
  const day = (ts: string | null) => (ts ? fmtTime(ts).split(' ')[0] : '?');
  return day(start) === day(end) ? day(start) : `${day(start)}–${day(end)}`;
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
  const [status, setStatus] = useState<ThreadStatus>(thread.status ?? 'active');
  const { error, busy, run } = useAction(onChanged);
  const branches = data.branches.filter((b) => b.thread_id === threadId).sort((a, b) => a.position - b.position);
  const events = useMemo(() => [...data.events].sort(compareEvents), [data.events]);
  const own = events.filter((e) => branches.some((b) => b.id === e.branch_id));
  const count = own.length;
  const milestones = own.filter((e) => e.importance === 3);
  const others = data.threads.filter((t) => t.id !== threadId);
  const [into, setInto] = useState('');
  const merge = async () => {
    const target = others.find((t) => t.id === Number(into));
    if (!target) return;
    if (!window.confirm(`把「${thread.title}」并入「${target.title}」？它的 ${count} 个事件都保留，原来的主线成为「${target.title}」的一条分支。`)) return;
    if (await run(() => api.mergeThread(threadId, target.id))) onClose();
  };

  return (
    <Shell
      chips={
        <>
          <span className="chip">研究线</span>
          {thread.status ? <span className="chip muted">{STATUS_LABEL[thread.status]}</span> : null}
        </>
      }
      actions={
        <>
          <IconButton icon="edit" label="编辑研究线" active={editing} onClick={() => setEditing(!editing)} />
          <IconButton icon="close" label="关闭（Esc）" onClick={onClose} />
        </>
      }
    >
      {editing ? (
        <form
          className="editor"
          onSubmit={async (e) => {
            e.preventDefault();
            if (await run(() => api.patchThread(threadId, { title: title.trim(), summary, status }))) setEditing(false);
          }}
        >
          <label className="field">
            <span>名称</span>
            <input className="input" value={title} maxLength={30} autoFocus onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="field">
            <span>进展</span>
            <textarea className="textarea" rows={3} value={summary} onChange={(e) => setSummary(e.target.value)} />
          </label>
          <div className="field">
            <span>状态</span>
            <div className="kind-picker" role="radiogroup" aria-label="状态">
              {(['active', 'done', 'dropped'] as ThreadStatus[]).map((st) => (
                <button key={st} type="button" role="radio" aria-checked={status === st} className={status === st ? 'on' : ''} onClick={() => setStatus(st)}>
                  {STATUS_LABEL[st]}
                </button>
              ))}
            </div>
          </div>
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
      {milestones.length ? (
        <section className="event-list">
          <div className="section-title">里程碑 · {milestones.length}</div>
          {milestones.map((e) => (
            <EventLink key={e.id} event={e} onSelect={onSelectEvent}>
              <span className="event-link-time">{fmtTime(e.ts)}</span>
            </EventLink>
          ))}
        </section>
      ) : null}
      <section className="branches">
        <div className="section-title">分支</div>
        {branches.map((b, i) => (
          <BranchRow
            key={b.id}
            data={data}
            branch={b}
            heir={i === 0 ? null : branches[0]}
            events={events}
            onChanged={onChanged}
            onSelectEvent={onSelectEvent}
          />
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
      {others.length ? (
        <section className="merge">
          <div className="section-title">并入另一条研究线</div>
          <p className="muted small">两条线其实是同一个方向时用。事件都保留，这条线的主线成为对方的一条分支。</p>
          <div className="row">
            <select className="select" value={into} disabled={busy} onChange={(e) => setInto(e.target.value)} aria-label="并入哪条研究线">
              <option value="">选择研究线…</option>
              {others.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                </option>
              ))}
            </select>
            <button type="button" className="btn" disabled={busy || !into} onClick={merge}>
              并入
            </button>
          </div>
        </section>
      ) : null}
      {error ? <div className="error small">{error}</div> : null}
    </Shell>
  );
}

function BranchRow({
  data,
  branch,
  heir,
  events,
  onChanged,
  onSelectEvent,
}: {
  data: ProjectData;
  branch: Branch;
  heir: Branch | null; // 删掉这条分支后事件并入哪条；主线没有，不能删
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
        {heir ? (
          <IconButton
            icon="trash"
            label="删除分支"
            onClick={() => {
              const name = branchName(branch, data.branches);
              const to = branchName(heir, data.branches);
              if (window.confirm(`删除分支「${name}」？它的 ${own.length} 个事件会并入「${to}」。`)) void run(() => api.deleteBranch(branch.id));
            }}
          />
        ) : null}
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
