// 时间线布局：纯函数，不依赖 DOM。
// 紧凑：按天分段，同一天里按先后和连线依赖排列，不按真实间隔拉伸。
// 真实时间：横轴是真实时间刻度，事件画成线上的点，同一行里放得下时显示标题。
import type { Branch, Session, Thread, TimelineEvent } from './types';
import { TOOL_LABEL } from './types';

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
export const DAY_GAP = 32;
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

export function dayKey(ts: string | null): string {
  if (!ts) return '';
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? '' : localDay(date);
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
      subtitle: `${count} 个事件${rest.length ? ` · ${rest.length} 个分支` : ''}`,
      threadId: thread.id,
      rows,
    });
  }
  if (misc.length) {
    for (const id of misc) grouping.rowOf.set(id, 'misc');
    chain(misc, 'next', grouping.edges);
    grouping.lanes.push({
      id: 'misc',
      title: '尚未归入研究线',
      subtitle: `${misc.length} 个事件`,
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
      subtitle: `${tool} · ${ids.length} 个事件${session?.fork_of ? ' · fork' : ''}`,
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

function compactNodes(events: TimelineEvent[], grouping: Grouping, rowY: Map<string, number>) {
  const preds = new Map<number, number[]>();
  for (const edge of grouping.edges) {
    const list = preds.get(edge.to) ?? [];
    list.push(edge.from);
    preds.set(edge.to, list);
  }
  const colOf = new Map<number, number>();
  const lastInRow = new Map<string, number>();
  const ranges: { key: string; start: number; end: number }[] = [];
  let offset = 0;
  let current: { key: string; start: number; end: number } | null = null;
  for (const event of events) {
    const key = dayKey(event.ts);
    if (!current || current.key !== key) {
      if (current) offset = current.end;
      current = { key, start: offset, end: offset };
      ranges.push(current);
    }
    const rowId = grouping.rowOf.get(event.id)!;
    let col = offset;
    const last = lastInRow.get(rowId);
    if (last !== undefined) col = Math.max(col, last + 1);
    for (const p of preds.get(event.id) ?? []) {
      const pc = colOf.get(p);
      if (pc !== undefined) col = Math.max(col, pc + 1);
    }
    colOf.set(event.id, col);
    lastInRow.set(rowId, col);
    current.end = Math.max(current.end, col + 1);
  }
  const xOfCol = (col: number) => {
    const index = ranges.findIndex((d) => col >= d.start && col < d.end);
    return LEFT_PAD + col * COL_W + Math.max(0, index) * DAY_GAP;
  };
  const nodes: PlacedNode[] = events.map((event) => {
    const rowId = grouping.rowOf.get(event.id)!;
    return {
      event,
      rowId,
      x: xOfCol(colOf.get(event.id)!),
      y: (rowY.get(rowId) ?? 0) + (CARD_ROW_H - CARD_H) / 2,
      labelWidth: 0,
    };
  });
  const days: Day[] = ranges.map((d) => ({
    key: d.key,
    x: xOfCol(d.start) - DAY_GAP / 2,
    width: (d.end - d.start) * COL_W + DAY_GAP,
  }));
  const lastCol = Math.max(0, ...colOf.values());
  const width = nodes.length ? xOfCol(lastCol) + CARD_W + LEFT_PAD * 2 : 600;
  return { nodes, days, ticks: [] as Tick[], width };
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
  const placed =
    options.align === 'compact'
      ? compactNodes(events, grouping, rowY)
      : timeNodes(events, grouping, rowY, options.pxPerHour);
  const byId = new Map(placed.nodes.map((n) => [n.event.id, n]));
  const shape = options.align === 'compact' ? cardPath : dotPath;
  const edges: Edge[] = grouping.edges
    .filter((e) => byId.has(e.from) && byId.has(e.to) && e.from !== e.to)
    .map((e) => ({ ...e, id: `${e.kind}:${e.from}:${e.to}`, path: shape(byId.get(e.from)!, byId.get(e.to)!) }));
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
