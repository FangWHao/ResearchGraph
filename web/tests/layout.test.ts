import { describe, expect, it } from 'vitest';
import { computeLayout, defaultZoom, filterDetail, rowNeighbors, titleLines, type LayoutOptions } from '../src/layout';
import type { Branch, Session, Thread, TimelineEvent } from '../src/types';

const thread = (id: number, title = `线${id}`): Thread => ({ id, project_id: 1, title, summary: '', status: null, edited: 0 });
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
  outcome: 'success',
  importance: 2,
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

describe('详略', () => {
  const withImportance = (e: TimelineEvent, importance: 1 | 2 | 3): TimelineEvent => ({ ...e, importance });
  const input = {
    threads: [thread(1), thread(2)],
    branches: [branch(1, 1, 0), branch(2, 1, 1, { from_event: 2, into_event: 4 }), branch(3, 2, 0)],
    events: [
      withImportance(event(1, '2026-10-01T01:00:00Z', 1), 3),
      withImportance(event(2, '2026-10-01T02:00:00Z', 1), 1),
      withImportance(event(3, '2026-10-01T03:00:00Z', 1), 2),
      withImportance(event(4, '2026-10-01T04:00:00Z', 1), 1),
      withImportance(event(5, '2026-10-01T05:00:00Z', 1), 3),
      withImportance(event(6, '2026-10-01T02:30:00Z', 2), 2),
      withImportance(event(7, '2026-10-02T01:00:00Z', 3), 1),
      withImportance(event(8, '2026-10-02T02:00:00Z', 3), 2),
    ],
    sessions: [session(1)],
  };

  it('只看里程碑时，藏起来的分出点和汇入点移到最近的可见事件，连线不断', () => {
    const { input: out, hidden } = filterDetail(input, 'milestone', 'threads');
    const ids = out.events.map((e) => e.id).sort((a, b) => a - b);
    // 线 1 留下里程碑 1、5；线 2 没有里程碑，留下它最高的一档（关键 8）
    expect(ids).toEqual([1, 5, 8]);
    const side = out.branches.find((b) => b.id === 2)!;
    expect([side.from_event, side.into_event]).toEqual([1, 5]);
    expect(hidden.get(1)).toBe(4); // 2、3、4 藏在 1 后面，整条藏起来的分支（6）算到分出点上
    expect(hidden.get(8)).toBe(1); // 7 在 8 前面，也算到 8 上
    expect(out.events.length + [...hidden.values()].reduce((a, b) => a + b, 0)).toBe(input.events.length);
  });

  it('泳道标签写出显示数和总数', () => {
    const { input: out } = filterDetail(input, 'milestone', 'threads');
    const layout = computeLayout(out, { view: 'threads', align: 'compact', pxPerHour: 8 });
    expect(layout.lanes.find((l) => l.threadId === 1)!.subtitle).toContain('2 / 6 个事件');
  });

  it('展开一张卡片，它这一段往下露出一档；露出来的还能再展开，收起后回到原样', () => {
    const sum = (f: ReturnType<typeof filterDetail>) =>
      f.input.events.length + [...f.hidden.values()].reduce((a, b) => a + b, 0);
    const once = filterDetail(input, 'milestone', 'threads', new Set([1]));
    // 1 后面藏着 2(细节)、3(关键)、4(细节)，以及整条藏起来的分支 2（事件 6，关键）
    expect(once.input.events.map((e) => e.id).sort((a, b) => a - b)).toEqual([1, 3, 5, 6, 8]);
    expect(once.opened.has(1) && once.revealed.has(3) && once.revealed.has(6)).toBe(true);
    expect(once.segments.get(3)).toEqual([2, 4]); // 3 前后的细节都挂在 3 上
    expect(once.hidden.get(3)).toBe(2);
    expect(sum(once)).toBe(input.events.length);
    const side = once.input.branches.find((b) => b.id === 2)!;
    expect([side.from_event, side.into_event]).toEqual([1, 5]); // 分出点 2 还藏着，移到 1 上

    const twice = filterDetail(input, 'milestone', 'threads', new Set([1, 3]));
    expect(twice.input.events.map((e) => e.id)).toEqual(expect.arrayContaining([2, 4]));
    expect(sum(twice)).toBe(input.events.length);
    expect(filterDetail(input, 'milestone', 'threads').input.events.length).toBe(3);
  });

  it('全部时原样返回', () => {
    expect(filterDetail(input, 'all', 'threads').input).toBe(input);
  });
});

describe('卡片高度随标题', () => {
  const long = { ...event(2, at(1, 10), 10), title: '原发 vs 转移样本的差异在三个批次都明确' };
  const input = {
    threads: [thread(1)],
    branches: [branch(10, 1, 0), branch(11, 1, 1, { from_event: 1 })],
    events: [event(1, at(1, 9), 10), long, event(3, at(1, 11), 11)],
    sessions: [session(1)],
  };
  const layout = computeLayout(input, compact());
  const node = (id: number) => layout.nodes.find((n) => n.event.id === id)!;

  it('长标题的那一行整行加高，标题不被截掉，也不压到下一行', () => {
    expect(titleLines('事件1', 500)).toBe(1);
    expect(titleLines(long.title, 500)).toBe(2);
    expect(node(2).lines).toBe(2);
    expect(node(1).h).toBe(node(2).h);
    expect(node(3).lines).toBe(1);
    expect(node(3).h).toBeLessThan(node(2).h);
    expect(node(3).y).toBeGreaterThan(node(2).y + node(2).h);
    const [main] = layout.lanes[0].rows;
    expect(node(2).y + node(2).h).toBeLessThanOrEqual(main.y + main.height);
  });

  it('同一行的连线保持水平', () => {
    const edge = layout.edges.find((e) => e.from === 1 && e.to === 2)!;
    expect(edge.path).toMatch(/^M[\d.]+,([\d.]+) L[\d.]+,\1$/);
  });
});
