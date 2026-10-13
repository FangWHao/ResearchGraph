// 时间线布局：纯函数，不依赖 DOM。
// 横轴按天分段；同一天内按先后和连线依赖排列，不按真实时间间隔拉伸。
import type { Branch, Session, Thread, TimelineEvent } from './types';
import { TOOL_LABEL } from './types';

export const NODE_W = 176;
export const NODE_H = 66;
export const COL_W = 204;
export const ROW_H = 86;
export const LANE_PAD = 10;
export const DAY_GAP = 28;
export const LEFT_PAD = 20;

export type ViewMode = 'threads' | 'sessions';
export type EdgeKind = 'next' | 'branch' | 'merge' | 'fork';

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
  x: number;
  y: number;
  col: number;
  rowId: string;
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

export interface Layout {
  lanes: Lane[];
  nodes: PlacedNode[];
  edges: Edge[];
  days: Day[];
  width: number;
  height: number;
}

export interface TimelineInput {
  threads: Thread[];
  branches: Branch[];
  events: TimelineEvent[];
  sessions: Session[];
}

export function compareEvents(a: TimelineEvent, b: TimelineEvent): number {
  const ta = a.ts ?? '';
  const tb = b.ts ?? '';
  if (ta !== tb) return ta < tb ? -1 : 1;
  return a.session_id - b.session_id || a.anchor - b.anchor || a.id - b.id;
}

export function dayKey(ts: string | null): string {
  if (!ts) return '';
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

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
      return { thread, own };
    })
    .filter(({ own }) => own.length > 0)
    .sort((a, b) => first(a.own.flatMap((x) => members.get(x.id)!)) - first(b.own.flatMap((x) => members.get(x.id)!)));

  for (const { thread, own } of threads) {
    const [main, ...rest] = own;
    rest.sort((a, b) => first(members.get(a.id)!) - first(members.get(b.id)!));
    const rows: Row[] = [];
    let count = 0;
    for (const branch of [main, ...rest]) {
      const ids = members.get(branch.id)!;
      count += ids.length;
      const rowId = `b${branch.id}`;
      rows.push({
        id: rowId,
        label: branch.title || (branch === main ? '主线' : '分支'),
        y: 0,
        branchId: branch.id,
      });
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

export function edgePath(a: PlacedNode, b: PlacedNode): string {
  const x1 = a.x + NODE_W;
  const y1 = a.y + NODE_H / 2;
  const x2 = b.x;
  const y2 = b.y + NODE_H / 2;
  if (y1 === y2) return `M${x1},${y1} L${x2},${y2}`;
  const dx = Math.max(24, Math.min(90, Math.abs(x2 - x1) / 2));
  return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
}

export function computeLayout(input: TimelineInput, mode: ViewMode): Layout {
  const events = [...input.events].sort(compareEvents);
  const grouping = mode === 'threads' ? byThreads(input, events) : bySessions(input, events);

  // 泳道和行的纵向位置
  const lanes: Lane[] = [];
  const rowY = new Map<string, number>();
  let y = 0;
  for (const lane of grouping.lanes) {
    const top = y;
    const rows = lane.rows.map((row, i) => {
      const rowTop = top + LANE_PAD + i * ROW_H;
      rowY.set(row.id, rowTop);
      return { ...row, y: rowTop };
    });
    const height = LANE_PAD * 2 + rows.length * ROW_H;
    lanes.push({ ...lane, rows, y: top, height });
    y += height;
  }

  // 列：按天分段，同一天里遵守“同一行先后”和“连线的起点在前”
  const preds = new Map<number, number[]>();
  for (const edge of grouping.edges) {
    const list = preds.get(edge.to) ?? [];
    list.push(edge.from);
    preds.set(edge.to, list);
  }
  const colOf = new Map<number, number>();
  const lastInRow = new Map<string, number>();
  const dayRanges: { key: string; start: number; end: number }[] = [];
  let offset = 0;
  let current: { key: string; start: number; end: number } | null = null;
  for (const event of events) {
    const key = dayKey(event.ts);
    if (!current || current.key !== key) {
      if (current) offset = current.end;
      current = { key, start: offset, end: offset };
      dayRanges.push(current);
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
    const index = dayRanges.findIndex((d) => col >= d.start && col < d.end);
    return LEFT_PAD + col * COL_W + Math.max(0, index) * DAY_GAP;
  };
  const nodes: PlacedNode[] = events.map((event) => {
    const col = colOf.get(event.id)!;
    const rowId = grouping.rowOf.get(event.id)!;
    return {
      event,
      col,
      rowId,
      x: xOfCol(col),
      y: (rowY.get(rowId) ?? 0) + (ROW_H - NODE_H) / 2,
    };
  });
  const byId = new Map(nodes.map((n) => [n.event.id, n]));
  const edges: Edge[] = grouping.edges
    .filter((e) => byId.has(e.from) && byId.has(e.to) && e.from !== e.to)
    .map((e) => ({
      ...e,
      id: `${e.kind}:${e.from}:${e.to}`,
      path: edgePath(byId.get(e.from)!, byId.get(e.to)!),
    }));
  const days: Day[] = dayRanges.map((d) => ({
    key: d.key,
    x: xOfCol(d.start) - DAY_GAP / 2,
    width: (d.end - d.start) * COL_W + DAY_GAP,
  }));
  const lastCol = Math.max(0, ...nodes.map((n) => n.col));
  const width = nodes.length ? xOfCol(lastCol) + COL_W + LEFT_PAD : 600;
  return { lanes, nodes, edges, days, width, height: Math.max(y, ROW_H) };
}
