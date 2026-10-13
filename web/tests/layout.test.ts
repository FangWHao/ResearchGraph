import { describe, expect, it } from 'vitest';
import { computeLayout } from '../src/layout';
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
const at = (day: number, hour: number) => new Date(2026, 9, day, hour).toISOString();

describe('研究线视图', () => {
  // 草图：线1 主线 e1 → e2 → e4，分支 e3 从 e1 分出、汇入 e4
  const input = {
    threads: [thread(1)],
    branches: [branch(10, 1, 0), branch(11, 1, 1, { from_event: 1, into_event: 4, title: '试 scVI' })],
    events: [event(1, at(1, 9), 10), event(2, at(1, 10), 10), event(3, at(1, 11), 11), event(4, at(2, 9), 10)],
    sessions: [session(1)],
  };
  const layout = computeLayout(input, 'threads');
  const node = (id: number) => layout.nodes.find((n) => n.event.id === id)!;

  it('主线在上，分支在下一行', () => {
    expect(layout.lanes).toHaveLength(1);
    expect(layout.lanes[0].rows.map((r) => r.label)).toEqual(['主线', '试 scVI']);
    expect(node(1).y).toBe(node(2).y);
    expect(node(3).y).toBeGreaterThan(node(1).y);
  });

  it('分支在分出点之后，汇入点在分支之后', () => {
    expect(node(3).col).toBeGreaterThan(node(1).col);
    expect(node(4).col).toBeGreaterThan(node(3).col);
    expect(node(4).col).toBeGreaterThan(node(2).col);
  });

  it('画出分出和汇入的连线', () => {
    const kinds = layout.edges.map((e) => `${e.kind}:${e.from}->${e.to}`).sort();
    expect(kinds).toEqual(['branch:1->3', 'merge:3->4', 'next:1->2', 'next:2->4']);
  });

  it('不同的天分段，后一天的事件在前一天所有事件之后', () => {
    expect(layout.days).toHaveLength(2);
    expect(node(4).x).toBeGreaterThan(Math.max(node(1).x, node(2).x, node(3).x));
  });
});

describe('排版', () => {
  it('同一天不同研究线的事件可以共用一列', () => {
    const layout = computeLayout(
      {
        threads: [thread(1), thread(2)],
        branches: [branch(10, 1, 0), branch(20, 2, 0)],
        events: [event(1, at(3, 9), 10), event(2, at(3, 15), 20)],
        sessions: [session(1)],
      },
      'threads',
    );
    expect(layout.nodes.map((n) => n.col)).toEqual([0, 0]);
    expect(layout.lanes.map((l) => l.title)).toEqual(['线1', '线2']);
  });

  it('没有归入研究线的事件单独成一条泳道，不会丢', () => {
    const layout = computeLayout(
      {
        threads: [thread(1)],
        branches: [branch(10, 1, 0)],
        events: [event(1, at(3, 9), 10), event(2, at(3, 10), null), event(3, at(3, 11), 999)],
        sessions: [session(1)],
      },
      'threads',
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
      'threads',
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
      'sessions',
    );
    expect(layout.lanes.map((l) => l.title)).toEqual(['会话1', '会话2']);
    expect(layout.edges.map((e) => `${e.kind}:${e.from}->${e.to}`).sort()).toEqual(['fork:1->3', 'next:1->2']);
  });
});
