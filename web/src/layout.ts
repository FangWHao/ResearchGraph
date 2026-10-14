// 时间线布局：纯函数，不依赖 DOM。
// 紧凑：每条泳道各自从最左边排起，只看泳道内部的先后，泳道之间不对齐时间。
// 真实时间：横轴是真实时间刻度，事件画成线上的点，同一行里放得下时显示标题。
import type { Branch, Session, Thread, TimelineEvent } from './types';
import { STATUS_LABEL, TOOL_LABEL } from './types';

export type ViewMode = 'threads' | 'sessions';
export type Align = 'compact' | 'time';
export type EdgeKind = 'next' | 'branch' | 'merge' | 'fork';

export const CARD_W = 188;
export const CARD_H = 64;
export const COL_W = 212;
export const CARD_ROW_H = 84;
export const DOT_ROW_H = 40;
export const DOT_HIT = 24;
export const DOT_R = 6;
export const LANE_PAD = 10;
export const LEFT_PAD = 24;
// 真实时间模式的缩放档位：每小时多少像素
export const ZOOM_LEVELS = [1, 2, 4, 8, 16, 32, 64, 128];
const HOUR = 3_600_000;
const LABEL_MIN = 64;
const LABEL_MAX = 200;

export interface Row {
  id: string;
  label: string;
  y: number;
  branchId?: number;
}

export interface Lane {
  id: string;
  title: string;
  subtitle: string;
  y: number;
  height: number;
  threadId?: number;
  sessionId?: number;
  // 悬停泳道标签时显示：研究线的进展叙述
  note?: string;
  rows: Row[];
}

export interface PlacedNode {
  event: TimelineEvent;
  rowId: string;
  // 紧凑：卡片左上角；真实时间：圆点中心
  x: number;
  y: number;
  // 真实时间模式下标题的可用宽度，0 表示放不下、只在悬停时显示
  labelWidth: number;
}

export interface Edge {
  id: string;
  from: number;
  to: number;
  kind: EdgeKind;
  // 两端在不同泳道；紧凑模式下平时不画，选中端点时才画
  cross: boolean;
  path: string;
}

export interface Day {
  key: string;
  x: number;
  width: number;
}

export interface Tick {
  x: number;
  label: string;
}

export interface Layout {
  align: Align;
  lanes: Lane[];
  nodes: PlacedNode[];
  edges: Edge[];
  days: Day[];
  ticks: Tick[];
  width: number;
  height: number;
}

export interface TimelineInput {
  threads: Thread[];
  branches: Branch[];
  events: TimelineEvent[];
  sessions: Session[];
  // 详略过滤前每条泳道的事件总数（t研究线编号 / s会话编号），泳道标签上写“显示数 / 总数”
  totals?: Map<string, number>;
}

export interface LayoutOptions {
  view: ViewMode;
  align: Align;
  pxPerHour: number;
}

export function compareEvents(a: TimelineEvent, b: TimelineEvent): number {
  const ta = a.ts ?? '';
  const tb = b.ts ?? '';
  if (ta !== tb) return ta < tb ? -1 : 1;
  return a.session_id - b.session_id || a.anchor - b.anchor || a.id - b.id;
}

const pad = (n: number) => String(n).padStart(2, '0');

function localDay(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function startOfDay(ms: number): Date {
  const date = new Date(ms);
  date.setHours(0, 0, 0, 0);
  return date;
}

// ---------- 分组：泳道、行和结构连线 ----------

interface Grouping {
  lanes: Omit<Lane, 'y' | 'height'>[];
  rowOf: Map<number, string>;
  edges: { from: number; to: number; kind: EdgeKind }[];
}

function chain(ids: number[], kind: EdgeKind, out: Grouping['edges']) {
  for (let i = 1; i < ids.length; i += 1) out.push({ from: ids[i - 1], to: ids[i], kind });
}

function byThreads(input: TimelineInput, events: TimelineEvent[]): Grouping {
  const branches = new Map(input.branches.map((b) => [b.id, b]));
  const visible = new Set(events.map((e) => e.id));
  const members = new Map<number, number[]>();
  const misc: number[] = [];
  for (const event of events) {
    if (event.branch_id !== null && branches.has(event.branch_id)) {
      const list = members.get(event.branch_id) ?? [];
      list.push(event.id);
      members.set(event.branch_id, list);
    } else {
      misc.push(event.id);
    }
  }
  const order = new Map(events.map((e, i) => [e.id, i]));
  const first = (ids: number[]) => Math.min(...ids.map((id) => order.get(id) ?? 0));
  const grouping: Grouping = { lanes: [], rowOf: new Map(), edges: [] };
  const threads = input.threads
    .map((thread) => {
      const own = input.branches
        .filter((b) => b.thread_id === thread.id && (members.get(b.id)?.length ?? 0) > 0)
        .sort((a, b) => a.position - b.position || a.id - b.id);
      return { thread, own, start: first(own.flatMap((b) => members.get(b.id) ?? [])) };
    })
    .filter(({ own }) => own.length > 0)
    .sort((a, b) => a.start - b.start);

  for (const { thread, own } of threads) {
    const [main, ...rest] = own;
    rest.sort((a, b) => first(members.get(a.id)!) - first(members.get(b.id)!));
    const rows: Row[] = [];
    let count = 0;
    for (const branch of [main, ...rest]) {
      const ids = members.get(branch.id)!;
      count += ids.length;
      const rowId = `b${branch.id}`;
      rows.push({ id: rowId, label: branch.title || (branch === main ? '主线' : '分支'), y: 0, branchId: branch.id });
      for (const id of ids) grouping.rowOf.set(id, rowId);
      chain(ids, 'next', grouping.edges);
      if (branch.from_event !== null && visible.has(branch.from_event)) {
        grouping.edges.push({ from: branch.from_event, to: ids[0], kind: 'branch' });
      }
      if (branch.into_event !== null && visible.has(branch.into_event)) {
        grouping.edges.push({ from: ids[ids.length - 1], to: branch.into_event, kind: 'merge' });
      }
    }
    grouping.lanes.push({
      id: `t${thread.id}`,
      title: thread.title,
      subtitle: `${thread.status && thread.status !== 'active' ? `${STATUS_LABEL[thread.status]} · ` : ''}${counted(count, input.totals?.get(`t${thread.id}`))}${rest.length ? ` · ${rest.length} 个分支` : ''}`,
      threadId: thread.id,
      note: thread.summary || undefined,
      rows,
    });
  }
  if (misc.length) {
    for (const id of misc) grouping.rowOf.set(id, 'misc');
    chain(misc, 'next', grouping.edges);
    grouping.lanes.push({
      id: 'misc',
      title: '尚未归入研究线',
      subtitle: counted(misc.length, input.totals?.get('tmisc')),
      rows: [{ id: 'misc', label: '', y: 0 }],
    });
  }
  return grouping;
}

function bySessions(input: TimelineInput, events: TimelineEvent[]): Grouping {
  const grouping: Grouping = { lanes: [], rowOf: new Map(), edges: [] };
  const visible = new Set(events.map((e) => e.id));
  const sessions = new Map(input.sessions.map((s) => [s.id, s]));
  const members = new Map<number, number[]>();
  for (const event of events) {
    const list = members.get(event.session_id) ?? [];
    list.push(event.id);
    members.set(event.session_id, list);
  }
  for (const [sessionId, ids] of members) {
    const session = sessions.get(sessionId);
    const rowId = `s${sessionId}`;
    for (const id of ids) grouping.rowOf.set(id, rowId);
    chain(ids, 'next', grouping.edges);
    if (session?.fork_event != null && visible.has(session.fork_event)) {
      grouping.edges.push({ from: session.fork_event, to: ids[0], kind: 'fork' });
    }
    const tool = session ? TOOL_LABEL[session.tool] : '';
    grouping.lanes.push({
      id: rowId,
      title: session?.title || `会话 ${sessionId}`,
      subtitle: `${tool} · ${counted(ids.length, input.totals?.get(`s${sessionId}`))}${session?.fork_of ? ' · fork' : ''}`,
      sessionId,
      rows: [{ id: rowId, label: '', y: 0 }],
    });
  }
  return grouping;
}

// ---------- 连线形状 ----------

export function cardPath(a: PlacedNode, b: PlacedNode): string {
  const x1 = a.x + CARD_W;
  const y1 = a.y + CARD_H / 2;
  const x2 = b.x;
  const y2 = b.y + CARD_H / 2;
  if (y1 === y2) return `M${x1},${y1} L${x2},${y2}`;
  const dx = Math.max(28, Math.min(96, Math.abs(x2 - x1) / 2));
  return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
}

// 紧凑模式下跨泳道的连线：两条泳道各自排列，起点可能在终点右边，所以竖着连卡片的上下边。
export function crossPath(a: PlacedNode, b: PlacedNode): string {
  const ax = a.x + CARD_W / 2;
  const bx = b.x + CARD_W / 2;
  const down = b.y > a.y;
  const y1 = down ? a.y + CARD_H : a.y;
  const y2 = down ? b.y : b.y + CARD_H;
  const mid = (y1 + y2) / 2;
  return `M${ax},${y1} C${ax},${mid} ${bx},${mid} ${bx},${y2}`;
}

export function dotPath(a: PlacedNode, b: PlacedNode): string {
  const r = DOT_R + 2;
  if (a.y === b.y) return `M${a.x + r},${a.y} L${Math.max(a.x + r, b.x - r)},${b.y}`;
  const dx = b.x - a.x;
  if (dx < 2 * r + 8) {
    // 几乎同时发生：竖着连
    const sign = Math.sign(b.y - a.y);
    const mid = (a.y + b.y) / 2;
    return `M${a.x},${a.y + sign * r} C${a.x},${mid} ${b.x},${mid} ${b.x},${b.y - sign * r}`;
  }
  const c = Math.max(12, Math.min(80, dx / 2));
  return `M${a.x + r},${a.y} C${a.x + c},${a.y} ${b.x - c},${b.y} ${b.x - r},${b.y}`;
}

// ---------- 主函数 ----------

function placeLanes(grouping: Grouping, rowH: number) {
  const lanes: Lane[] = [];
  const rowY = new Map<string, number>();
  let y = 0;
  for (const lane of grouping.lanes) {
    const top = y;
    const rows = lane.rows.map((row, i) => {
      const rowTop = top + LANE_PAD + i * rowH;
      rowY.set(row.id, rowTop);
      return { ...row, y: rowTop };
    });
    const height = LANE_PAD * 2 + rows.length * rowH;
    lanes.push({ ...lane, rows, y: top, height });
    y += height;
  }
  return { lanes, rowY, height: y };
}

// 紧凑：每条泳道各自从最左边排起，只看泳道内部的先后，泳道之间不对齐时间。
// 跨泳道的连线不参与排列（否则晚开始的研究线会被推到右边）。
function compactNodes(
  events: TimelineEvent[],
  grouping: Grouping,
  rowY: Map<string, number>,
  laneOf: (eventId: number) => string,
) {
  const preds = new Map<number, number[]>();
  for (const edge of grouping.edges) {
    if (laneOf(edge.from) !== laneOf(edge.to)) continue;
    const list = preds.get(edge.to) ?? [];
    list.push(edge.from);
    preds.set(edge.to, list);
  }
  const colOf = new Map<number, number>();
  const lastInRow = new Map<string, number>();
  for (const event of events) {
    const rowId = grouping.rowOf.get(event.id)!;
    let col = 0;
    const last = lastInRow.get(rowId);
    if (last !== undefined) col = last + 1;
    for (const p of preds.get(event.id) ?? []) {
      const pc = colOf.get(p);
      if (pc !== undefined) col = Math.max(col, pc + 1);
    }
    colOf.set(event.id, col);
    lastInRow.set(rowId, col);
  }
  const nodes: PlacedNode[] = events.map((event) => {
    const rowId = grouping.rowOf.get(event.id)!;
    return {
      event,
      rowId,
      x: LEFT_PAD + colOf.get(event.id)! * COL_W,
      y: (rowY.get(rowId) ?? 0) + (CARD_ROW_H - CARD_H) / 2,
      labelWidth: 0,
    };
  });
  const lastCol = Math.max(0, ...colOf.values());
  const width = nodes.length ? LEFT_PAD * 2 + lastCol * COL_W + CARD_W : 600;
  return { nodes, days: [] as Day[], ticks: [] as Tick[], width };
}

function tickStep(pxPerHour: number): number {
  if (pxPerHour >= 64) return 1;
  if (pxPerHour >= 32) return 2;
  if (pxPerHour >= 16) return 3;
  if (pxPerHour >= 8) return 6;
  return 0;
}

function timeNodes(
  events: TimelineEvent[],
  grouping: Grouping,
  rowY: Map<string, number>,
  pxPerHour: number,
) {
  const times = events.map((e) => (e.ts ? Date.parse(e.ts) : Number.NaN));
  const known = times.filter((t) => !Number.isNaN(t));
  const start = startOfDay(known.length ? Math.min(...known) : Date.now());
  const endDay = startOfDay(known.length ? Math.max(...known) : Date.now());
  endDay.setDate(endDay.getDate() + 1);
  const t0 = start.getTime();
  const xOf = (t: number) => LEFT_PAD + ((t - t0) / HOUR) * pxPerHour;

  const nodes: PlacedNode[] = events.map((event, i) => {
    const rowId = grouping.rowOf.get(event.id)!;
    const t = Number.isNaN(times[i]) ? t0 : times[i];
    return { event, rowId, x: xOf(t), y: (rowY.get(rowId) ?? 0) + DOT_ROW_H / 2, labelWidth: 0 };
  });
  // 同一行里，标题只用到下一个点之前的空间
  const byRow = new Map<string, PlacedNode[]>();
  for (const node of nodes) byRow.set(node.rowId, [...(byRow.get(node.rowId) ?? []), node]);
  for (const row of byRow.values()) {
    row.sort((a, b) => a.x - b.x);
    row.forEach((node, i) => {
      const room = i + 1 < row.length ? row[i + 1].x - node.x - DOT_HIT : LABEL_MAX;
      node.labelWidth = room >= LABEL_MIN ? Math.min(room, LABEL_MAX) : 0;
    });
  }

  const days: Day[] = [];
  const ticks: Tick[] = [];
  const step = tickStep(pxPerHour);
  for (const day = new Date(start); day < endDay; day.setDate(day.getDate() + 1)) {
    const next = new Date(day);
    next.setDate(next.getDate() + 1);
    days.push({ key: localDay(day), x: xOf(day.getTime()), width: xOf(next.getTime()) - xOf(day.getTime()) });
    if (step) {
      for (let h = step; h < 24; h += step) {
        const tick = new Date(day);
        tick.setHours(h);
        ticks.push({ x: xOf(tick.getTime()), label: `${pad(h)}:00` });
      }
    }
  }
  return { nodes, days, ticks, width: xOf(endDay.getTime()) + LEFT_PAD + LABEL_MAX };
}

export function computeLayout(input: TimelineInput, options: LayoutOptions): Layout {
  const events = [...input.events].sort(compareEvents);
  const grouping = options.view === 'threads' ? byThreads(input, events) : bySessions(input, events);
  const rowH = options.align === 'compact' ? CARD_ROW_H : DOT_ROW_H;
  const { lanes, rowY, height } = placeLanes(grouping, rowH);
  const laneOfRow = new Map(grouping.lanes.flatMap((lane) => lane.rows.map((row) => [row.id, lane.id] as const)));
  const laneOf = (eventId: number) => laneOfRow.get(grouping.rowOf.get(eventId) ?? '') ?? '';
  const compact = options.align === 'compact';
  const placed = compact
    ? compactNodes(events, grouping, rowY, laneOf)
    : timeNodes(events, grouping, rowY, options.pxPerHour);
  const byId = new Map(placed.nodes.map((n) => [n.event.id, n]));
  const edges: Edge[] = grouping.edges
    .filter((e) => byId.has(e.from) && byId.has(e.to) && e.from !== e.to)
    .map((e) => {
      const cross = laneOf(e.from) !== laneOf(e.to);
      const [a, b] = [byId.get(e.from)!, byId.get(e.to)!];
      const path = compact ? (cross ? crossPath(a, b) : cardPath(a, b)) : dotPath(a, b);
      return { ...e, id: `${e.kind}:${e.from}:${e.to}`, cross, path };
    });
  return { align: options.align, lanes, edges, height: Math.max(height, rowH), ...placed };
}

/** 默认缩放：让整个时间范围大约占 8000 像素，再取最近的档位。 */
export function defaultZoom(events: TimelineEvent[]): number {
  const known = events.map((e) => (e.ts ? Date.parse(e.ts) : Number.NaN)).filter((t) => !Number.isNaN(t));
  if (known.length < 2) return 16;
  const hours = Math.max(24, (Math.max(...known) - Math.min(...known)) / HOUR + 24);
  const ideal = 8000 / hours;
  return ZOOM_LEVELS.reduce((best, z) => (Math.abs(Math.log(z / ideal)) < Math.abs(Math.log(best / ideal)) ? z : best));
}

/** 同一分支（研究线视图）或同一会话（会话视图）里的前后事件。 */
export function rowNeighbors(
  events: TimelineEvent[],
  view: ViewMode,
  eventId: number,
): { prev: TimelineEvent | null; next: TimelineEvent | null } {
  const current = events.find((e) => e.id === eventId);
  if (!current) return { prev: null, next: null };
  const same = events
    .filter((e) => (view === 'threads' ? e.branch_id === current.branch_id : e.session_id === current.session_id))
    .sort(compareEvents);
  const i = same.findIndex((e) => e.id === eventId);
  return { prev: same[i - 1] ?? null, next: same[i + 1] ?? null };
}

function counted(shown: number, total: number | undefined): string {
  return total && total !== shown ? `${shown} / ${total} 个事件` : `${shown} 个事件`;
}

// ---------- 详略 ----------

export type Detail = 'milestone' | 'key' | 'all';
const DETAIL_MIN: Record<Detail, number> = { milestone: 3, key: 2, all: 1 };

export interface Filtered {
  input: TimelineInput;
  // 每个可见事件代表着几个被藏起来的事件（同一分支里，到下一个可见事件为止）
  hidden: Map<number, number>;
  // 这些被藏起来的事件本身，按时间排好；面板里“这一段的经过”用
  segments: Map<number, number[]>;
  // 展开过、确实带着一段内容的事件（卡片上显示“收起”）
  opened: Set<number>;
  // 因为展开才露出来的事件
  revealed: Set<number>;
}

/**
 * 按详略过滤事件。每条研究线（按会话看时是每个会话）至少留下它最高的一档，整条泳道不会消失。
 * 被藏起来的事件挂在同一分支里前面最近的可见事件上（开头的挂在第一个可见事件上）；
 * expanded 里的事件把它这一段往下展开一档，展开出来的事件又可以再展开。
 * 整条分支都被藏起来时挂在它的分出点上，展开分出点时一起出来。
 * 被藏起来的分出点、汇入点移到同一分支里最近的可见事件上，连线不断。
 */
export function filterDetail(
  input: TimelineInput,
  detail: Detail,
  view: ViewMode,
  expanded: ReadonlySet<number> = new Set(),
): Filtered {
  const hidden = new Map<number, number>();
  const segments = new Map<number, number[]>();
  const opened = new Set<number>();
  const revealed = new Set<number>();
  if (detail === 'all') return { input, hidden, segments, opened, revealed };
  const threadOf = new Map(input.branches.map((b) => [b.id, b.thread_id]));
  const group = (e: TimelineEvent) =>
    view === 'sessions' ? `s${e.session_id}` : `t${e.branch_id === null ? 'misc' : threadOf.get(e.branch_id)}`;
  const top = new Map<string, number>();
  for (const e of input.events) top.set(group(e), Math.max(top.get(group(e)) ?? 0, e.importance));
  const base = (e: TimelineEvent) => Math.min(DETAIL_MIN[detail], top.get(group(e)) ?? 0);

  const lineOf = (e: TimelineEvent) => (view === 'sessions' ? `s${e.session_id}` : `b${e.branch_id ?? 'misc'}`);
  const lines = new Map<string, TimelineEvent[]>();
  for (const e of [...input.events].sort(compareEvents)) {
    const list = lines.get(lineOf(e)) ?? [];
    list.push(e);
    lines.set(lineOf(e), list);
  }

  const keep = new Set<number>();
  const hide = (owner: number, seg: TimelineEvent[]) => {
    segments.set(owner, [...(segments.get(owner) ?? []), ...seg.map((e) => e.id)]);
    hidden.set(owner, (hidden.get(owner) ?? 0) + seg.length);
  };
  // 在一段按时间排好的事件里挑出门槛以上的，其余挂到前面最近的可见事件上；展开过的再往下一档挑。
  // 一个都挑不出来时返回 false（整条分支都藏起来了）。
  const pick = (list: TimelineEvent[], min: number, nested: boolean): boolean => {
    const m = nested ? Math.min(min, Math.max(...list.map((e) => e.importance))) : min;
    if (!list.some((e) => e.importance >= m)) return false;
    const segs = new Map<number, TimelineEvent[]>();
    const lead: TimelineEvent[] = [];
    let owner: number | null = null;
    for (const e of list) {
      if (e.importance >= m) {
        keep.add(e.id);
        if (nested) revealed.add(e.id);
        if (owner === null && lead.length) segs.set(e.id, [...lead]);
        owner = e.id;
      } else if (owner === null) {
        lead.push(e);
      } else {
        segs.set(owner, [...(segs.get(owner) ?? []), e]);
      }
    }
    for (const [id, seg] of segs) {
      if (expanded.has(id) && m > 1) {
        opened.add(id);
        pick(seg, m - 1, true);
      } else {
        hide(id, seg);
      }
    }
    return true;
  };
  const orphans = new Map<string, TimelineEvent[]>();
  for (const [line, list] of lines) {
    if (!pick(list, base(list[0]), false)) orphans.set(line, list);
  }

  const nearestOf = () => {
    const nearest = new Map<number, { before: number | null; after: number | null }>();
    for (const list of lines.values()) {
      let before: number | null = null;
      for (const e of list) {
        if (keep.has(e.id)) before = e.id;
        nearest.set(e.id, { before, after: null });
      }
      let after: number | null = null;
      for (const e of [...list].reverse()) {
        if (keep.has(e.id)) after = e.id;
        nearest.get(e.id)!.after = after;
      }
    }
    return (id: number | null, prefer: 'before' | 'after'): number | null => {
      if (id === null || keep.has(id)) return id;
      const near = nearest.get(id);
      if (!near) return null;
      return prefer === 'before' ? near.before ?? near.after : near.after ?? near.before;
    };
  };
  // 整条藏起来的分支挂在分出点上；分出点展开过就往下一档露出来
  let remap = nearestOf();
  for (const b of input.branches) {
    const list = orphans.get(`b${b.id}`);
    const host = list ? remap(b.from_event, 'before') : null;
    if (!list || host === null) continue;
    if (expanded.has(host)) {
      opened.add(host);
      pick(list, base(list[0]) - 1, true);
    } else {
      hide(host, list);
    }
  }
  remap = nearestOf();
  const branches = input.branches.map((b) => ({
    ...b,
    from_event: remap(b.from_event, 'before'),
    into_event: remap(b.into_event, 'after'),
  }));
  const totals = new Map<string, number>();
  for (const e of input.events) {
    const lane = view === 'sessions' ? `s${e.session_id}` : `t${e.branch_id === null ? 'misc' : threadOf.get(e.branch_id)}`;
    totals.set(lane, (totals.get(lane) ?? 0) + 1);
  }
  const events = input.events.filter((e) => keep.has(e.id));
  return { input: { ...input, events, branches, totals }, hidden, segments, opened, revealed };
}
