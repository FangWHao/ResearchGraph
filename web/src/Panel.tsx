import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { fmtFull, fmtTime } from './format';
import { compareEvents, type Lane } from './layout';
import type { Kind, Message, ProjectData, TimelineEvent } from './types';
import { KIND_LABEL, KINDS, TOOL_LABEL } from './types';

const ROLE: Record<Message['role'], string> = { user: '用户', assistant: 'Agent', tool: '工具' };
const NEW_THREAD = '__new__';

interface Common {
  data: ProjectData;
  onChanged: () => void;
  onClose: () => void;
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
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return { error, busy, run };
}

function eventLabel(event: TimelineEvent) {
  return `${fmtTime(event.ts)} · ${KIND_LABEL[event.kind]} · ${event.title}`;
}

export function EventPanel({ data, event, onChanged, onClose }: Common & { event: TimelineEvent }) {
  const [title, setTitle] = useState(event.title);
  const [summary, setSummary] = useState(event.summary);
  const [kind, setKind] = useState<Kind>(event.kind);
  const [context, setContext] = useState(0);
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [loadError, setLoadError] = useState('');
  const { error, busy, run } = useAction(onChanged);
  const session = data.sessions.find((s) => s.id === event.session_id);

  useEffect(() => {
    setTitle(event.title);
    setSummary(event.summary);
    setKind(event.kind);
  }, [event]);

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

  const places = useMemo(() => {
    const threads = new Map(data.threads.map((t) => [t.id, t]));
    return data.branches
      .filter((b) => threads.has(b.thread_id))
      .sort((a, b) => a.thread_id - b.thread_id || a.position - b.position)
      .map((b) => ({
        id: b.id,
        label: `${threads.get(b.thread_id)!.title} · ${b.title || (b.position === 0 ? '主线' : '分支')}`,
      }));
  }, [data.branches, data.threads]);

  const dirty = title !== event.title || summary !== event.summary || kind !== event.kind;

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
    <aside className={`panel kind-${event.kind}`} onClick={(e) => e.stopPropagation()}>
      <div className="panel-head">
        <span className="chip kind">{KIND_LABEL[event.kind]}</span>
        {event.edited ? <span className="chip">人工改过</span> : null}
        <span className="spacer" />
        <button type="button" className="btn small" onClick={onClose}>
          关闭
        </button>
      </div>
      <label className="field">
        <span>标题</span>
        <input className="input" value={title} maxLength={40} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="field">
        <span>种类</span>
        <select className="select" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {KIND_LABEL[k]}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>摘要</span>
        <textarea className="textarea" value={summary} onChange={(e) => setSummary(e.target.value)} />
      </label>
      <div className="row end">
        <button
          type="button"
          className="btn primary"
          disabled={!dirty || busy || !title.trim()}
          onClick={() => run(() => api.patchEvent(event.id, { title, summary, kind }))}
        >
          保存修改
        </button>
      </div>

      <div className="meta">
        <div>{fmtFull(event.ts)}</div>
        <div className="muted">
          {session ? `${TOOL_LABEL[session.tool]} ·「${session.title ?? '无标题'}」` : `会话 ${event.session_id}`}
        </div>
      </div>

      <label className="field">
        <span>放在</span>
        <select className="select" value={event.branch_id ?? ''} onChange={(e) => move(e.target.value)} disabled={busy}>
          {event.branch_id === null ? <option value="">（尚未归入研究线）</option> : null}
          {places.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
          <option value={NEW_THREAD}>＋ 新研究线…</option>
        </select>
      </label>

      <div className="row">
        <span className="section-title">原话</span>
        <span className="spacer" />
        <button type="button" className="link small" onClick={() => setContext(context ? 0 : 3)}>
          {context ? '只看引用' : '显示前后 3 条'}
        </button>
      </div>
      <div className="quotes">
        {loadError ? <div className="error">{loadError}</div> : null}
        {messages === null && !loadError ? <div className="muted">读取中…</div> : null}
        {messages?.map((m) => (
          <div key={m.idx} className={`quote${m.ref ? ' ref' : ''}`}>
            <div className="quote-head">
              #{m.idx} · {ROLE[m.role]} · {fmtTime(m.ts)}
            </div>
            <div className="quote-text">{m.text}</div>
          </div>
        ))}
      </div>

      {error ? <div className="error">{error}</div> : null}
      <div className="row">
        <button
          type="button"
          className="btn danger small"
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
          删除事件
        </button>
      </div>
    </aside>
  );
}

export function LanePanel({ data, lane, onChanged, onClose }: Common & { lane: Lane }) {
  const thread = data.threads.find((t) => t.id === lane.threadId);
  const session = data.sessions.find((s) => s.id === lane.sessionId);
  return (
    <aside className="panel" onClick={(e) => e.stopPropagation()}>
      <div className="panel-head">
        <span className="chip">{thread ? '研究线' : session ? '会话' : '未归类'}</span>
        <span className="spacer" />
        <button type="button" className="btn small" onClick={onClose}>
          关闭
        </button>
      </div>
      {thread ? <ThreadEditor key={thread.id} data={data} threadId={thread.id} onChanged={onChanged} /> : null}
      {session ? (
        <div className="meta">
          <h2>{session.title ?? '无标题'}</h2>
          <div>{TOOL_LABEL[session.tool]}</div>
          <div>
            {fmtFull(session.started_at)} — {fmtTime(session.ended_at)}
          </div>
          <div>
            已整理 {session.extracted_upto} / {session.message_count} 条消息
            {session.copied ? `（开头 ${session.copied} 条复制自原会话）` : ''}
          </div>
          {session.fork_of ? <div>从另一个会话 fork 出来，按新分支处理</div> : null}
          {session.missing ? <div className="error">原文件已不在，已读入的内容仍然保留</div> : null}
          <div className="mono muted">{session.path}</div>
          {session.cwd ? <div className="mono muted">工作目录 {session.cwd}</div> : null}
        </div>
      ) : null}
      {!thread && !session ? (
        <p className="muted">
          这些事件还没有放进研究线，通常是整理中断了。点“更新”会重新安排；也可以点开事件手动指定。
        </p>
      ) : null}
    </aside>
  );
}

function ThreadEditor({ data, threadId, onChanged }: { data: ProjectData; threadId: number; onChanged: () => void }) {
  const thread = data.threads.find((t) => t.id === threadId)!;
  const [title, setTitle] = useState(thread.title);
  const [summary, setSummary] = useState(thread.summary);
  const { error, busy, run } = useAction(onChanged);
  const branches = data.branches.filter((b) => b.thread_id === threadId).sort((a, b) => a.position - b.position);
  const events = useMemo(() => [...data.events].sort(compareEvents), [data.events]);

  return (
    <>
      <label className="field">
        <span>研究线名称</span>
        <input className="input" value={title} maxLength={30} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="field">
        <span>说明</span>
        <textarea className="textarea" value={summary} onChange={(e) => setSummary(e.target.value)} />
      </label>
      <div className="row end">
        <button
          type="button"
          className="btn primary"
          disabled={busy || !title.trim() || (title === thread.title && summary === thread.summary)}
          onClick={() => run(() => api.patchThread(threadId, { title, summary }))}
        >
          保存
        </button>
      </div>
      <div className="section-title">分支</div>
      {branches.map((branch, i) => (
        <BranchEditor
          key={branch.id}
          branchId={branch.id}
          data={data}
          events={events}
          isMain={i === 0}
          onChanged={onChanged}
        />
      ))}
      <button
        type="button"
        className="btn small"
        disabled={busy}
        onClick={() => {
          const name = window.prompt('新分支的名称');
          if (name?.trim()) void run(() => api.createBranch(threadId, name.trim(), null));
        }}
      >
        ＋ 新建分支
      </button>
      {error ? <div className="error">{error}</div> : null}
    </>
  );
}

function BranchEditor({
  data,
  branchId,
  events,
  isMain,
  onChanged,
}: {
  data: ProjectData;
  branchId: number;
  events: TimelineEvent[];
  isMain: boolean;
  onChanged: () => void;
}) {
  const branch = data.branches.find((b) => b.id === branchId)!;
  const [title, setTitle] = useState(branch.title);
  const { error, busy, run } = useAction(onChanged);
  const own = events.filter((e) => e.branch_id === branchId).length;
  const options = events.filter((e) => e.branch_id !== branchId);
  const select = (value: number | null, field: 'from_event' | 'into_event') => (
    <select
      className="select"
      value={value ?? ''}
      disabled={busy}
      onChange={(e) => run(() => api.patchBranch(branchId, { [field]: e.target.value ? Number(e.target.value) : null }))}
    >
      <option value="">（无）</option>
      {options.map((e) => (
        <option key={e.id} value={e.id}>
          {eventLabel(e)}
        </option>
      ))}
    </select>
  );
  return (
    <div className="branch-edit">
      <div className="row">
        <input
          className="input"
          value={title}
          placeholder={isMain ? '主线' : '分支名称'}
          maxLength={30}
          onChange={(e) => setTitle(e.target.value)}
        />
        <button
          type="button"
          className="btn small"
          disabled={busy || title === branch.title}
          onClick={() => run(() => api.patchBranch(branchId, { title }))}
        >
          改名
        </button>
      </div>
      <div className="small muted">{own} 个事件</div>
      <label className="field">
        <span>从哪个事件分出</span>
        {select(branch.from_event, 'from_event')}
      </label>
      <label className="field">
        <span>汇入哪个事件</span>
        {select(branch.into_event, 'into_event')}
      </label>
      {error ? <div className="error">{error}</div> : null}
    </div>
  );
}
