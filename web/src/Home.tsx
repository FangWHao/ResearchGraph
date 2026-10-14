import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import { fmtAgo, fmtDate, fmtDayKey, fmtFull, fmtTime } from './format';
import { Icon, KindGlyph, Logo } from './Icons';
import type { BriefItem, HomeData, HomeProject, HomeThread, Phase } from './types';
import { KIND_LABEL, OUTCOME_LABEL, STATUS_LABEL } from './types';

// 从主页跳进项目时要停在哪里
export interface Target {
  event?: number;
  thread?: number;
}

interface Props {
  onOpen: (projectId: number, target?: Target) => void;
  onNew: () => void;
  onSettings: () => void;
}

const PHASE_TEXT: Partial<Record<Phase, string>> = {
  sync: '读取会话',
  extract: '提取关键步骤',
  threads: '归入研究线',
  consolidate: '合并研究线',
  review: '复审研究线',
  brief: '写简报',
};
const DAY = 86_400_000;
const IDLE_DAYS = 14; // 还在进行、但这么多天没动的研究线标出来
const LINES_SHOWN = 7;

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const time = (ts: string | null) => (ts ? new Date(ts).getTime() : Number.NaN);

export function Home({ onOpen, onNew, onSettings }: Props) {
  const [data, setData] = useState<HomeData | null>(null);
  const [error, setError] = useState('');
  const [notes, setNotes] = useState<Record<number, string>>({});
  const asked = useRef(new Set<number>());

  const load = useCallback(async () => {
    try {
      setData(await api.home());
      setError('');
    } catch (e) {
      setError(message(e));
    }
  }, []);

  useEffect(() => {
    void load();
    const onFocus = () => void load();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [load]);

  const busy = data?.projects.some((p) => p.briefing || p.job.running) ?? false;
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [busy, load]);

  // 简报落后于研究线（比如在有简报之前就整理好的项目）：打开主页时补写一次。
  // 只对同意过发送的项目；同样的内容出过错就不再自动写，等用户点“重新生成”。
  useEffect(() => {
    if (!data) return;
    for (const p of data.projects) {
      const b = p.brief;
      if (!p.model_allowed || !b?.ready || !b.stale || p.briefing || p.job.running) continue;
      if (asked.current.has(p.id)) continue;
      asked.current.add(p.id);
      api.brief(p.id, true).then(
        () => load(),
        (e) => setNotes((n) => ({ ...n, [p.id]: message(e) })),
      );
    }
  }, [data, load]);

  const regenerate = async (id: number) => {
    setNotes((n) => ({ ...n, [id]: '' }));
    try {
      await api.brief(id);
      await load();
    } catch (e) {
      setNotes((n) => ({ ...n, [id]: message(e) }));
    }
  };

  return (
    <div className="home">
      <header className="topbar">
        <div className="brand">
          <Logo />
          <span>ResearchGraph</span>
        </div>
        <span className="spacer" />
        <button type="button" className="btn" onClick={onNew}>
          <Icon name="plus" size={15} />
          新建项目
        </button>
        <button type="button" className="icon-btn" title="设置" aria-label="设置" onClick={onSettings}>
          <Icon name="sliders" />
        </button>
      </header>
      <main className="home-main">
        {data ? <Hero data={data} /> : <div className="muted">加载中…</div>}
        {error ? <div className="error">{error}</div> : null}
        {data?.projects.map((p) => (
          <ProjectCard
            key={p.id}
            project={p}
            note={notes[p.id]}
            onOpen={(target) => onOpen(p.id, target)}
            onRegenerate={() => void regenerate(p.id)}
          />
        ))}
      </main>
    </div>
  );
}

function Hero({ data }: { data: HomeData }) {
  const hour = new Date().getHours();
  const greet = hour < 5 ? '夜深了' : hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好';
  const w = data.week;
  const projects = data.projects;
  const latest = projects
    .filter((p) => p.last_session ?? p.last_ts)
    .sort((a, b) => time(b.last_session ?? b.last_ts) - time(a.last_session ?? a.last_ts))[0];
  const active = projects.reduce((n, p) => n + p.threads.filter((t) => (t.status ?? 'active') === 'active').length, 0);
  const open = projects.reduce((n, p) => n + (p.brief?.content?.open.length ?? 0), 0);
  const milestones = projects.reduce((n, p) => n + p.milestones, 0);
  return (
    <section className="home-hero">
      <div className="home-hero-text">
        <div className="home-date">{fmtDayKey(data.today)}</div>
        <h1>{greet}</h1>
        <p className="home-week">
          {w.key || w.sessions ? (
            <>
              近 7 天，<b>{w.projects}</b> 个项目里有 <b>{w.sessions}</b> 次对话，新增 <b>{w.key}</b> 个关键步骤、
              <b>{w.milestones}</b> 个里程碑。
            </>
          ) : latest ? (
            <>
              近 7 天没有新的对话。最近一次是 {fmtAgo(latest.last_session ?? latest.last_ts)}，在「{latest.name}」。
            </>
          ) : (
            '还没有整理过的对话。'
          )}
        </p>
        <dl className="home-stats hero-stats">
          <Stat label="项目" value={projects.length} />
          <Stat label="进行中的研究线" value={active} />
          <Stat label="待解决的问题" value={open} />
          <Stat label="里程碑" value={milestones} />
        </dl>
      </div>
      <Heatmap activity={data.activity} />
    </section>
  );
}

const level = (n: number, max: number) => (n === 0 ? 0 : Math.min(4, Math.ceil((n / max) * 4)));

function Heatmap({ activity }: { activity: HomeData['activity'] }) {
  const max = Math.max(1, ...activity.map((d) => d.key));
  const first = activity.length ? new Date(`${activity[0].day}T00:00:00`) : new Date();
  const lead = (first.getDay() + 6) % 7; // 每列一周，周一在最上面
  const weeks = Math.ceil((activity.length + lead) / 7);
  const months: { col: number; label: string }[] = [];
  activity.forEach((d, i) => {
    const [, m, day] = d.day.split('-').map(Number);
    const col = Math.floor((i + lead) / 7);
    if (day === 1 || (i === 0 && day < 20)) months.push({ col, label: `${m} 月` });
  });
  const cols = { gridTemplateColumns: `repeat(${weeks}, var(--cell))` };
  return (
    <figure className="heat">
      <div className="heat-months" style={cols} aria-hidden="true">
        {months.map((m) => (
          <span key={m.col} style={{ gridColumn: m.col + 1 }}>
            {m.label}
          </span>
        ))}
      </div>
      <div className="heat-grid" style={cols} role="img" aria-label="近 12 周每天新增的关键步骤">
        {Array.from({ length: lead }, (_, i) => (
          <span key={`pad${i}`} className="heat-cell pad" />
        ))}
        {activity.map((d) => (
          <span
            key={d.day}
            className={`heat-cell l${level(d.key, max)}`}
            title={`${fmtDayKey(d.day)}：${d.key ? `${d.key} 个关键步骤` : '没有新的关键步骤'}${d.milestones ? `，${d.milestones} 个里程碑` : ''}`}
          />
        ))}
      </div>
      <figcaption className="heat-legend">
        近 12 周 · 每天新增的关键步骤
        <span className="spacer" />少
        {[0, 1, 2, 3, 4].map((l) => (
          <span key={l} className={`heat-cell l${l}`} />
        ))}
        多
      </figcaption>
    </figure>
  );
}

interface CardProps {
  project: HomeProject;
  note?: string;
  onOpen: (target?: Target) => void;
  onRegenerate: () => void;
}

function ProjectCard({ project: p, note, onOpen, onRegenerate }: CardProps) {
  const last = p.last_session ?? p.last_ts;
  return (
    <article className="home-card">
      <header className="home-card-head">
        <div className="home-card-title">
          <h2>
            <button type="button" className="home-name" onClick={() => onOpen()}>
              {p.name}
            </button>
          </h2>
          <div className="home-card-meta">
            {p.workspace ? (
              <span className="home-path" title={p.workspace}>
                {p.workspace}
              </span>
            ) : null}
            <span>{p.sessions} 个对话</span>
            {last ? <span>最近一次对话 {fmtAgo(last)}</span> : null}
            {p.job.running ? (
              <span className="home-job">
                <span className="pulse" />
                {PHASE_TEXT[p.job.phase] ?? '整理中'}
                {p.job.total ? ` ${p.job.done}/${p.job.total}` : ''}
              </span>
            ) : null}
          </div>
        </div>
        <dl className="home-stats">
          <Stat label="研究线" value={p.threads.length} />
          <Stat label="关键步骤" value={p.key} />
          <Stat label="里程碑" value={p.milestones} />
          <Stat label="失败的尝试" value={p.failed} />
        </dl>
        <button type="button" className="btn primary" onClick={() => onOpen()}>
          打开时间线
          <Icon name="arrowRight" size={15} />
        </button>
      </header>
      <div className="home-card-body">
        <Brief project={p} note={note} onOpen={onOpen} onRegenerate={onRegenerate} />
        <Lines project={p} onOpen={onOpen} />
      </div>
    </article>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="home-stat">
      <dt>{label}</dt>
      <dd>{value.toLocaleString('zh-CN')}</dd>
    </div>
  );
}

function Brief({ project: p, note, onOpen, onRegenerate }: CardProps) {
  const [copied, setCopied] = useState(false);
  const b = p.brief;
  const c = b?.content ?? null;
  const writing = p.briefing || (p.job.running && p.job.phase === 'brief');
  const copy = async () => {
    await copyText(markdown(p));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  let body;
  if (c) {
    body = (
      <>
        <p className="brief-headline">{c.headline}</p>
        <BriefList title="最近进展" variant="progress" items={c.progress} project={p} onOpen={onOpen} />
        <BriefList title="待解决" variant="open" items={c.open} project={p} onOpen={onOpen} />
        {c.next.length ? (
          <BriefList title="可能的下一步" variant="next" items={c.next} project={p} onOpen={onOpen} />
        ) : null}
      </>
    );
  } else if (writing) {
    body = (
      <div className="brief-skeleton" aria-label="正在写简报">
        <span />
        <span />
        <span />
        <p className="muted small">正在根据研究线写简报，大约半分钟…</p>
      </div>
    );
  } else if (!p.model_allowed) {
    body = <p className="brief-empty">还没有同意把这个项目发送给模型。打开项目确认发送量，整理完会自动写一份简报。</p>;
  } else if (!b?.ready) {
    body = (
      <p className="brief-empty">
        {p.events ? '事件还没有归入研究线，整理完会自动写简报。' : '还没有整理出事件，打开项目开始整理。'}
      </p>
    );
  } else {
    body = <p className="brief-empty">还没有简报。</p>;
  }

  return (
    <section className="brief" aria-label="项目简报">
      <div className="home-label">
        <span>项目简报</span>
        {writing && c ? <span className="muted">· 正在重写…</span> : null}
      </div>
      {body}
      {b?.error ? <p className="brief-error small">上次没写成：{b.error}</p> : null}
      {note ? <p className="brief-error small">{note}</p> : null}
      {b?.ready || c ? (
        <div className="brief-foot">
          <span className="muted small">
            {b?.created_at ? `${b.model ? `${b.model} ` : ''}写于 ${fmtAgo(b.created_at)}` : ''}
            {c && b?.stale ? ' · 研究线有新变化' : ''}
          </span>
          <span className="spacer" />
          {c ? (
            <button type="button" className="btn ghost small" onClick={() => void copy()} title="复制成 Markdown，开新会话时贴给 Agent">
              <Icon name="copy" size={14} />
              {copied ? '已复制' : '复制给 Agent'}
            </button>
          ) : null}
          {p.model_allowed && b?.ready ? (
            <button type="button" className="btn ghost small" disabled={writing || p.job.running} onClick={onRegenerate}>
              <Icon name="refresh" size={14} />
              {c ? '重新生成' : '生成简报'}
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function BriefList({
  title,
  variant,
  items,
  project: p,
  onOpen,
}: {
  title: string;
  variant: 'progress' | 'open' | 'next';
  items: BriefItem[];
  project: HomeProject;
  onOpen: (target: Target) => void;
}) {
  return (
    <div className={`brief-list ${variant}`}>
      <h3>{title}</h3>
      {items.length ? (
        <ul>
          {items.map((item, i) => {
            const line = item.thread ? p.threads.find((t) => t.id === item.thread) : undefined;
            return (
              <li key={i}>
                <span>{item.text}</span>
                {byDay(item.events ?? [], p).map(([day, refs]) => (
                  <button
                    key={day}
                    type="button"
                    className="ref-chip"
                    title={refs.map((r) => `${KIND_LABEL[r.kind]}${r.outcome ? ` · ${OUTCOME_LABEL[r.outcome]}` : ''}：${r.title}`).join('\n')}
                    onClick={() => onOpen({ event: refs[0].id })}
                  >
                    {refs.map((r) => (
                      <KindGlyph key={r.id} kind={r.kind} outcome={r.outcome} size={9} />
                    ))}
                    {day}
                  </button>
                ))}
                {line ? (
                  <button type="button" className="ref-chip line" title={line.summary || line.title} onClick={() => onOpen({ thread: line.id })}>
                    <Icon name="branch" size={11} />
                    {line.title}
                  </button>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted small">没有</p>
      )}
    </div>
  );
}

// 简报一条引用的几个步骤按日期合起来：同一天的只显示一个日期
function byDay(ids: number[], p: HomeProject): [string, HomeProject['refs'][string][]][] {
  const groups = new Map<string, HomeProject['refs'][string][]>();
  for (const id of ids) {
    const r = p.refs[id];
    if (!r) continue;
    const day = fmtDate(r.ts);
    groups.set(day, [...(groups.get(day) ?? []), r]);
  }
  return [...groups];
}

function StatusMark({ status }: { status: HomeThread['status'] }) {
  if (status === 'done') return <Icon name="check" size={12} />;
  if (status === 'dropped') return <Icon name="close" size={12} />;
  return <span className="line-dot" />;
}

function Lines({ project: p, onOpen }: { project: HomeProject; onOpen: (target: Target) => void }) {
  const [all, setAll] = useState(false);
  const t0 = time(p.first_ts);
  const span = Math.max(1, time(p.last_ts) - t0);
  const pos = (ts: string | null) => {
    const t = time(ts);
    return Number.isNaN(t) || Number.isNaN(t0) ? 0 : ((t - t0) / span) * 100;
  };
  const now = Date.now();
  const shown = all ? p.threads : p.threads.slice(0, LINES_SHOWN);
  return (
    <section className="lines" aria-label="研究线">
      <div className="home-label">
        <span>研究线</span>
        <span className="muted">按最近活动排列 · 点里程碑跳到时间线</span>
      </div>
      {p.threads.length ? (
        <>
          <ul className="line-list">
            {shown.map((t) => {
              const status = t.status ?? 'active';
              const idle = status === 'active' && t.last_ts ? Math.floor((now - time(t.last_ts)) / DAY) : 0;
              const left = pos(t.first_ts);
              return (
                <li key={t.id} className={`line-row s-${status}`}>
                  <button
                    type="button"
                    className="line-name"
                    title={`${t.title}（${STATUS_LABEL[status]}）${t.summary ? `\n${t.summary}` : ''}`}
                    onClick={() => onOpen({ thread: t.id })}
                  >
                    <StatusMark status={t.status} />
                    <span>{t.title}</span>
                  </button>
                  <div className="line-track">
                    <span className="line-span" style={{ left: `${left}%`, width: `${Math.max(0.8, pos(t.last_ts) - left)}%` }} />
                    {t.milestones.map((m) => (
                      <button
                        key={m.id}
                        type="button"
                        className="line-ms"
                        style={{ left: `${pos(m.ts)}%` }}
                        title={`${fmtTime(m.ts)} ${KIND_LABEL[m.kind]}：${m.title}`}
                        aria-label={`里程碑：${m.title}`}
                        onClick={() => onOpen({ event: m.id })}
                      >
                        <KindGlyph kind={m.kind} outcome={m.outcome} size={9} ring />
                      </button>
                    ))}
                  </div>
                  <span className="line-meta">
                    {t.key} 步
                    {idle >= IDLE_DAYS ? <em> · {idle} 天没动</em> : null}
                  </span>
                </li>
              );
            })}
          <li className="line-axis" aria-hidden="true">
            <span />
            <span className="line-axis-dates">
              <span>{fmtDate(p.first_ts)}</span>
              <span>{fmtDate(p.last_ts)}</span>
            </span>
            <span />
          </li>
          </ul>
          <div className="line-foot">
            {p.threads.length > LINES_SHOWN ? (
              <button type="button" className="link small" onClick={() => setAll(!all)}>
                {all ? '收起' : `还有 ${p.threads.length - LINES_SHOWN} 条研究线`}
              </button>
            ) : null}
            {p.unplaced ? <span className="muted small">另有 {p.unplaced} 个事件还没归入研究线</span> : null}
          </div>
          <Recent project={p} onOpen={onOpen} />
        </>
      ) : (
        <p className="brief-empty">还没有研究线。</p>
      )}
    </section>
  );
}

const RECENT = 5;

function Recent({ project: p, onOpen }: { project: HomeProject; onOpen: (target: Target) => void }) {
  const recent = p.threads
    .flatMap((t) => t.milestones.map((m) => ({ ...m, line: t.title })))
    .sort((a, b) => time(b.ts) - time(a.ts))
    .slice(0, RECENT);
  if (!recent.length) return null;
  return (
    <div className="recent">
      <div className="home-label">
        <span>最近的里程碑</span>
      </div>
      <ul>
        {recent.map((m) => (
          <li key={m.id}>
            <button type="button" className="recent-item" onClick={() => onOpen({ event: m.id })}>
              <KindGlyph kind={m.kind} outcome={m.outcome} size={10} />
              <span className="recent-title">{m.title}</span>
              <span className="recent-line">{m.line}</span>
              <span className="recent-date">{fmtDate(m.ts)}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------- 复制给 Agent ----------

function markdown(p: HomeProject): string {
  const c = p.brief?.content;
  if (!c) return '';
  const lineTitle = (id?: number | null) => (id ? p.threads.find((t) => t.id === id)?.title : undefined);
  const out = [
    `# ${p.name}：项目简报`,
    '',
    `> 由 ResearchGraph 根据 ${p.sessions} 个对话里的 ${p.key} 个关键步骤整理，${fmtFull(p.brief?.created_at)}。`,
    '',
    c.headline,
    '',
  ];
  const section = (title: string, items: BriefItem[]) => {
    if (!items.length) return;
    out.push(`## ${title}`, '');
    for (const item of items) {
      const refs = (item.events ?? [])
        .map((id) => p.refs[id])
        .filter(Boolean)
        .map((r) => `${fmtDate(r.ts)}「${r.title}」`);
      const line = lineTitle(item.thread);
      const notes = [line ? `研究线：${line}` : '', ...refs].filter(Boolean);
      out.push(`- ${item.text}${notes.length ? `（${notes.join('；')}）` : ''}`);
    }
    out.push('');
  };
  section('最近进展', c.progress);
  section('待解决', c.open);
  section('可能的下一步', c.next);
  out.push('## 研究线', '');
  for (const t of p.threads) {
    const status = STATUS_LABEL[t.status ?? 'active'];
    out.push(`- **${t.title}**（${status}，${t.key} 个关键步骤，最近 ${fmtDate(t.last_ts)}）${t.summary ? `：${t.summary}` : ''}`);
  }
  return out.join('\n');
}

async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch {
    // 不是安全上下文（比如通过 SSH 转发的地址）时退回老办法
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  document.execCommand('copy');
  area.remove();
}
