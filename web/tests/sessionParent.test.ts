import { describe, expect, it } from 'vitest';
import { parentEvidenceTarget, parseSessionParent } from '../src/sessionParent';
import type { SessionParentData, SessionParentObservation } from '../src/types';

const child = '00000000-0000-0000-0000-000000000001';
const parent = '00000000-0000-0000-0000-000000000002';
const other = '00000000-0000-0000-0000-000000000003';
function observation(event = 3): SessionParentObservation {
  return { event_id: event, state: 'declared', basis: 'both', reason: 'native_header',
    native_id: child, parent_id: parent, other_parent_id: null, recorded_at: '2026-10-11T00:00:00+00:00' };
}
function view(): SessionParentData {
  return { session_pk: 2, tool: 'codex', native_id: child, state: 'linked', parent_session_pk: 1,
    parent_native_id: parent, parent_event_id: 1, observations: [observation()], observations_total: 1,
    observations_partial: false, observation_highwater: 3,
    source_metadata_complete: true, identity_metadata_complete: true };
}

describe('父线程源声明与当前关联分别保存', () => {
  it('未观测、未声明和缺字段保持未知，不生成父原文目标或根线程判定', () => {
    expect(parseSessionParent(undefined, 2)).toBeNull();
    for (const state of ['unobserved', 'no_parent_declared'] as const) {
      const input = { ...view(), state, parent_session_pk: null, parent_native_id: null, parent_event_id: null,
        observations: state === 'unobserved' ? [] : [{ ...observation(), state: 'not_declared' as const,
          basis: 'none' as const, reason: 'parent_not_declared', parent_id: null }],
        observations_total: state === 'unobserved' ? 0 : 1, observation_highwater: state === 'unobserved' ? 0 : 3 };
      const result = parseSessionParent(input, 2)!;
      expect(result.state).toBe(state); expect(parentEvidenceTarget(result)).toBeNull();
    }
  });
  it('唯一同项目父关系仅生成事件编号，不把源文件位置当对象引用窗口', () => {
    const input = { ...view(), source_byte_start: 800, source_byte_end: 1000,
      observations: [{ ...observation(), source_byte_start: 2000, source_byte_end: 2400 }] };
    const result = parseSessionParent(input, 2)!;
    expect(parentEvidenceTarget(result)).toEqual({ event_id: 1 });
    expect(result.observations).toEqual([observation()]);
    expect(() => parseSessionParent({ ...view(), session_pk: 4 }, 2)).toThrow();
    expect(() => parseSessionParent({ ...view(), parent_session_pk: 2 }, 2)).toThrow();
  });
  it('同一源声明后父头迟到可改变关系，不改写先前声明记录', () => {
    const missing: SessionParentData = { ...view(), state: 'missing_parent', parent_session_pk: null,
      parent_native_id: null, parent_event_id: null };
    const before = parseSessionParent(missing, 2)!; const after = parseSessionParent(view(), 2)!;
    expect(before.observations).toEqual(after.observations);
    expect(parentEvidenceTarget(before)).toBeNull(); expect(parentEvidenceTarget(after)).toEqual({ event_id: 1 });
  });
  it('跨项目不得公开解析父会话或引用，源声明UUID仍可核对', () => {
    const outside: SessionParentData = { ...view(), state: 'outside_project', parent_session_pk: null,
      parent_native_id: null, parent_event_id: null };
    const result = parseSessionParent(outside, 2)!;
    expect(result.observations[0].parent_id).toBe(parent); expect(parentEvidenceTarget(result)).toBeNull();
    for (const leaked of [{ parent_session_pk: 1 }, { parent_native_id: parent }, { parent_event_id: 1 }])
      expect(() => parseSessionParent({ ...outside, ...leaked }, 2)).toThrow();
  });
  it('省略的旧头含冲突时保留后端全量结论，不能从最近20条重新选父', () => {
    const input: SessionParentData = { ...view(), state: 'conflicting', parent_session_pk: null,
      parent_native_id: null, parent_event_id: null, observations_total: 21, observations_partial: true,
      observations: Array.from({ length: 20 }, (_, index) => observation(index + 10)), observation_highwater: 29 };
    const result = parseSessionParent(input, 2)!;
    expect(result.state).toBe('conflicting'); expect(parentEvidenceTarget(result)).toBeNull();
    expect(result.observations.every(row => row.state === 'declared' && row.parent_id === parent)).toBe(true);
    expect(() => parseSessionParent({ ...input, observations_partial: false }, 2)).toThrow();
    expect(() => parseSessionParent({ ...input, observations: input.observations.slice(1) }, 2)).toThrow();
  });
  it('冲突、无效、多个匹配和循环都不提供父导航，不任选声明', () => {
    for (const state of ['invalid', 'conflicting', 'ambiguous_parent', 'cycle'] as const) {
      const input: SessionParentData = { ...view(), state, parent_session_pk: null,
        parent_native_id: null, parent_event_id: null, observations: [{ ...observation(),
          state: 'conflict', other_parent_id: other, reason: 'parent_mismatch' }] };
      const result = parseSessionParent(input, 2)!;
      expect(result.state).toBe(state); expect(result.observations[0].other_parent_id).toBe(other);
      expect(parentEvidenceTarget(result)).toBeNull();
    }
  });
  it('身份、事件编号和观测截止异常不被默默当成可关联的头声明', () => {
    for (const patch of [{ state: 'root' }, { tool: 'claude' }, { native_id: child + '\n' },
      { parent_event_id: -1 }, { observation_highwater: 4 }, { observations_total: 2 },
      { observations: [observation(), observation()] }, { observations: [{ ...observation(), event_id: 0 }] }])
      expect(() => parseSessionParent({ ...view(), ...patch }, 2)).toThrow();
  });
  it('积压状态可有零条或多条观测，父导航为空；不能把零条当完整无父', () => {
    for (const observed of [false, true]) {
      const input: SessionParentData = { ...view(), state: 'metadata_incomplete', parent_session_pk: null,
        parent_native_id: null, parent_event_id: null, observations: observed ? [observation()] : [],
        observations_total: observed ? 1 : 0, observation_highwater: observed ? 3 : 0,
        source_metadata_complete: false, identity_metadata_complete: false };
      const result = parseSessionParent(input, 2)!;
      expect(result.state).toBe('metadata_incomplete'); expect(result.observations.length).toBe(observed ? 1 : 0);
      expect(result.source_metadata_complete).toBe(false); expect(parentEvidenceTarget(result)).toBeNull();
      expect(() => parseSessionParent({ ...input, parent_event_id: 1 }, 2)).toThrow();
    }
  });
  it('来源或全库身份补记未齐时，即使响应报告linked也不能生成唯一父导航', () => {
    for (const patch of [{ source_metadata_complete: false }, { identity_metadata_complete: false }]) {
      const result = parseSessionParent({ ...view(), ...patch }, 2)!;
      expect(result).toMatchObject(patch); expect(result.observations).toEqual(view().observations);
      expect(parentEvidenceTarget(result)).toBeNull();
    }
    expect(parentEvidenceTarget(parseSessionParent(view(), 2)!)).toEqual({ event_id: 1 });
  });
  it('缺少或null的旧覆盖字段保持未知，不臆造完整身份，不开放父导航', () => {
    const old = { ...view() }; delete old.source_metadata_complete; delete old.identity_metadata_complete;
    for (const input of [old, { ...view(), source_metadata_complete: null, identity_metadata_complete: null },
      { ...view(), identity_metadata_complete: null }]) {
      const result = parseSessionParent(input, 2)!;
      expect(parentEvidenceTarget(result)).toBeNull();
      expect(result.identity_metadata_complete).toBeNull();
    }
    expect(() => parseSessionParent({ ...view(), source_metadata_complete: 'true' }, 2)).toThrow();
  });
  it('已观察冲突或无效在积压中仍保留，不提升为覆盖完整或清掉原声明', () => {
    for (const state of ['conflicting', 'invalid', 'no_parent_declared'] as const) {
      const input: SessionParentData = { ...view(), state, parent_session_pk: null, parent_native_id: null,
        parent_event_id: null, source_metadata_complete: false, identity_metadata_complete: false };
      const result = parseSessionParent(input, 2)!;
      expect(result.state).toBe(state); expect(result.source_metadata_complete).toBe(false);
      expect(result.observations).toEqual(input.observations); expect(parentEvidenceTarget(result)).toBeNull();
    }
  });
});
