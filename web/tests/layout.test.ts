import { describe, expect, it } from 'vitest';
import { computeLayout, defaultZoom, rowNeighbors, type LayoutOptions } from '../src/layout';
import type { Branch, Session, Thread, TimelineEvent } from '../src/types';

const thread = (id: number, title = `线${id}`): Thread => ({ id, project_id: 1, title, summary: '', edited: 0 });
const branch = (id: number, thread_id: number, position: number, extra: Partial<Branch> = {}): Branch => ({
  id,
  thread_id,
  title: '',
  position,
  from_event: null,
  into_event: null,
  edited: 0,
  ...extra,
});
const event = (id: number, ts: string, branch_id: number | null, session_id = 1): TimelineEvent => ({
  id,
  session_id,
  branch_id,
  kind: 'attempt',
  title: `事件${id}`,
  summary: '',
  ts,
  anchor: id,
  refs: [id],
  edited: 0,
});
const session = (id: number, extra: Partial<Session> = {}): Session => ({
  id,
  tool: 'claude',
  path: `/s/${id}.jsonl`,
  title: `会话${id}`,
  cwd: '/w',
  started_at: null,
  ended_at: null,
  message_count: 10,
  extracted_upto: 10,
  missing: 0,
  fork_of: null,
  copied: 0,
  fork_event: null,
  ...extra,
});

// 本地时区的同一天
const at = (day: number, hour: number, minute = 0) => new Date(2026, 9, day, hour, minute).toISOString();
const compact = (view: LayoutOptions['view'] = 'threads'): LayoutOptions => ({ view, align: 'compact', pxPerHour: 16 });
const timed = (pxPerHour = 16): LayoutOptions => ({ view: 'threads', align: 'time', pxPerHour });

describe('研究线视图', () => {
  // 草图：线1 主线 e1 → e2 → e4，分支 e3 从 e1 分出、汇入 e4
  const input = {
    threads: [thread(1)],
    branches: [branch(10, 1, 0), branch(11, 1, 1, { from_event: 1, into_event: 4, title: '试 scVI' })],
    events: [event(1, at(1, 9), 10), event(2, at(1, 10), 10), event(3, at(1, 11), 11), event(4, at(2, 9), 10)],
    sessions: [session(1)],
  };
  const layout = computeLayout(input, compact());
  const node = (id: number) => layout.nodes.find((n) => n.event.id === id)!;

  it('主线在上，分支在下一行', () => {
    expect(layout.lanes).toHaveLength(1);
    expect(layout.lanes[0].rows.map((r) => r.label)).toEqual(['主线', '试 scVI']);
    expect(node(1).y).toBe(node(2).y);
    expect(node(3).y).toBeGreaterThan(node(1).y);
  });

  it('分支在分出点之后，汇入点在分支之后', () => {
    expect(node(3).x).toBeGreaterThan(node(1).x);
    expect(node(4).x).toBeGreaterThan(node(3).x);
    expect(node(4).x).toBeGreaterThan(node(2).x);
  });

  it('画出分出和汇入的连线', () => {
    const kinds = layout.edges.map((e) => `${e.kind}:${e.from}->${e.to}`).sort();
    expect(kinds).toEqual(['branch:1->3', 'merge:3->4', 'next:1->2', 'next:2->4']);
  });

  it('紧凑模式没有共用的日期轴', () => {
    expect(layout.days).toEqual([]);
  });
});

describe('排版', () => {
  it('平行的研究线各自从左边排起，不按开始时间错开', () => {
    // 线1 第 1 天做了三步，线2 第 10 天才开始，而且是从线1 的最后一步分出来的
    const layout = computeLayout(
      {
        threads: [thread(1), thread(2)],
        branches: [branch(10, 1, 0), branch(20, 2, 0, { from_event: 3 })],
        events: [
          event(1, at(1, 9), 10),
          event(2, at(1, 10), 10),
          event(3, at(1, 11), 10),
          event(4, at(10, 9), 20),
          event(5, at(10, 10), 20),
        ],
        sessions: [session(1)],
      },
      compact(),
    );
    const x = (id: number) => layout.nodes.find((n) => n.event.id === id)!.x;
    expect(x(4)).toBe(x(1));
    expect(x(5)).toBe(x(2));
    expect(layout.lanes.map((l) => l.title)).toEqual(['线1', '线2']);
    // 跨研究线的分出关系保留，但标记为跨泳道，不影响排列
    const crossing = layout.edges.find((e) => e.kind === 'branch')!;
    expect([crossing.from, crossing.to, crossing.cross]).toEqual([3, 4, true]);
    expect(layout.edges.filter((e) => e.kind === 'next').every((e) => !e.cross)).toBe(true);
  });

  it('同一研究线里的分支仍然排在分出点之后', () => {
    const layout = computeLayout(
      {
        threads: [thread(1)],
        branches: [branch(10, 1, 0), branch(11, 1, 1, { from_event: 2 })],
        events: [event(1, at(1, 9), 10), event(2, at(1, 10), 10), event(3, at(5, 9), 11)],
        sessions: [session(1)],
      },
      compact(),
    );
    const x = (id: number) => layout.nodes.find((n) => n.event.id === id)!.x;
    expect(x(3)).toBeGreaterThan(x(2));
    expect(layout.edges.find((e) => e.kind === 'branch')!.cross).toBe(false);
  });

  it('没有归入研究线的事件单独成一条泳道，不会丢', () => {
    const layout = computeLayout(
      {
        threads: [thread(1)],
        branches: [branch(10, 1, 0)],
        events: [event(1, at(3, 9), 10), event(2, at(3, 10), null), event(3, at(3, 11), 999)],
        sessions: [session(1)],
      },
      compact(),
    );
    expect(layout.nodes).toHaveLength(3);
    expect(layout.lanes.map((l) => l.id)).toEqual(['t1', 'misc']);
  });

  it('分出点被删掉时不画悬空的连线', () => {
    const layout = computeLayout(
      {
        threads: [thread(1)],
        branches: [branch(10, 1, 0), branch(11, 1, 1, { from_event: 77 })],
        events: [event(1, at(3, 9), 10), event(2, at(3, 10), 11)],
        sessions: [session(1)],
      },
      compact(),
    );
    expect(layout.edges.filter((e) => e.kind === 'branch')).toEqual([]);
  });
});

describe('会话视图', () => {
  it('每个会话一条泳道，fork 从分出点连过去', () => {
    const layout = computeLayout(
      {
        threads: [],
        branches: [],
        events: [event(1, at(1, 9), null, 1), event(2, at(1, 10), null, 1), event(3, at(2, 9), null, 2)],
        sessions: [session(1), session(2, { fork_of: 1, fork_event: 1 })],
      },
      compact('sessions'),
    );
    expect(layout.lanes.map((l) => l.title)).toEqual(['会话1', '会话2']);
    expect(layout.edges.map((e) => `${e.kind}:${e.from}->${e.to}`).sort()).toEqual(['fork:1->3', 'next:1->2']);
    // 每个会话也从左边排起
    expect(layout.nodes.find((n) => n.event.id === 3)!.x).toBe(layout.nodes.find((n) => n.event.id === 1)!.x);
  });
});

describe('真实时间', () => {
  const input = {
    threads: [thread(1)],
    branches: [branch(10, 1, 0), branch(11, 1, 1, { from_event: 1 })],
    events: [
      event(1, at(1, 9), 10),
      event(2, at(1, 9, 30), 10),
      event(3, at(1, 15), 10),
      event(4, at(3, 9), 11),
    ],
    sessions: [session(1)],
  };

  it('横坐标与真实时间成正比', () => {
    const layout = computeLayout(input, timed(16));
    const x = (id: number) => layout.nodes.find((n) => n.event.id === id)!.x;
    expect(x(2) - x(1)).toBeCloseTo(8); // 半小时 = 8px
    expect(x(3) - x(1)).toBeCloseTo(96); // 6 小时
    expect(x(4) - x(1)).toBeCloseTo(48 * 16); // 两天
    const wider = computeLayout(input, timed(32));
    expect(wider.nodes[2].x - wider.nodes[0].x).toBeCloseTo(192);
  });

  it('按天画日期，缩放够大时画小时刻度', () => {
    const layout = computeLayout(input, timed(16));
    expect(layout.days.map((d) => d.width)).toEqual([384, 384, 384]);
    expect(layout.ticks.map((t) => t.label).slice(0, 3)).toEqual(['03:00', '06:00', '09:00']);
    expect(computeLayout(input, timed(4)).ticks).toEqual([]);
  });

  it('标题只在放得下时显示', () => {
    const layout = computeLayout(input, timed(16));
    const label = (id: number) => layout.nodes.find((n) => n.event.id === id)!.labelWidth;
    expect(label(1)).toBe(0); // 半小时后就是下一个点
    expect(label(2)).toBeGreaterThan(60);
    expect(label(4)).toBeGreaterThan(60); // 行里最后一个
  });

  it('默认缩放让整个范围约 8000 像素', () => {
    expect(defaultZoom(input.events)).toBe(128); // 3 天 → 约 110px/小时
  });
});

describe('前后事件', () => {
  it('按分支或按会话找上一步、下一步', () => {
    const events = [event(1, at(1, 9), 10, 1), event(2, at(1, 10), 11, 1), event(3, at(1, 11), 10, 2)];
    expect(rowNeighbors(events, 'threads', 1).next?.id).toBe(3);
    expect(rowNeighbors(events, 'sessions', 1).next?.id).toBe(2);
    expect(rowNeighbors(events, 'threads', 3)).toEqual({ prev: events[0], next: null });
  });
});
