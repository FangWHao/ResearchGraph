import { describe, expect, it } from 'vitest';
import { eventChainTargets, parseEventChain } from '../src/eventChain';
import type { EventChainData } from '../src/types';

const child = '00000000-0000-0000-0000-000000000002';
const parent = '00000000-0000-0000-0000-000000000001';
function view(): EventChainData {
  return { event_id: 4, session_pk: 2, file_instance_id: 7, tool: 'claude', state: 'linked',
    record_event_id: 3, native_uuid: child, parent_uuid: parent, uuid_state: 'valid', parent_state: 'declared',
    is_sidechain: null, sidechain_state: 'missing', recorded_at: '2026-10-11T00:00:00Z', basis: 'direct_record',
    resolution_scope: 'source_file', parent_references: [{ event_id: 1, file_instance_id: 7 }],
    parent_references_total: 1, parent_references_partial: false, parent_source_contexts: 1,
    ancestry_state: 'null_parent', ancestry_steps: 1, ancestry_limit: 64,
    source_metadata_complete: true, scope_metadata_incomplete: false };
}
function unresolved(state: EventChainData['state']): EventChainData {
  return { ...view(), state, resolution_scope: 'none', parent_references: [], parent_references_total: 0,
    parent_source_contexts: 0, ancestry_state: state, ancestry_steps: 0 };
}

describe('Claude来源父链与正文去重分开核对', () => {
  it('相同记录UUID可保留不同fork来源声明，不把来源变体合并为一条父引用', () => {
    const first = parseEventChain(view(), 4, 2)!;
    const other = parseEventChain({ ...view(), file_instance_id: 8,
      parent_uuid: '00000000-0000-0000-0000-000000000009',
      parent_references: [{ event_id: 2, file_instance_id: 8 }] }, 4, 2)!;
    expect(first.native_uuid).toBe(other.native_uuid);
    expect(first.parent_uuid).not.toBe(other.parent_uuid);
    expect(first.file_instance_id).not.toBe(other.file_instance_id);
    expect(eventChainTargets(first)).toEqual([{ event_id: 1 }]);
    expect(eventChainTargets(other)).toEqual([{ event_id: 2 }]);
    expect(() => parseEventChain({ ...view(), file_instance_id: 8 }, 4, 2)).toThrow();
  });
  it('旁支缺失和非法保持null，明确false独立保留；空父不等同未声明', () => {
    for (const sidechain_state of ['missing', 'invalid'] as const) {
      const result = parseEventChain({ ...view(), sidechain_state }, 4, 2)!;
      expect(result.is_sidechain).toBeNull(); expect(result.sidechain_state).toBe(sidechain_state);
      expect(() => parseEventChain({ ...view(), sidechain_state, is_sidechain: false }, 4, 2)).toThrow();
    }
    for (const is_sidechain of [true, false]) {
      expect(parseEventChain({ ...view(), sidechain_state: 'declared', is_sidechain }, 4, 2)!.is_sidechain).toBe(is_sidechain);
    }
    const absent = parseEventChain({ ...unresolved('parent_not_declared'), parent_state: 'missing', parent_uuid: null }, 4, 2)!;
    const empty = parseEventChain({ ...unresolved('null_parent'), parent_state: 'null', parent_uuid: null }, 4, 2)!;
    expect(absent.state).not.toBe(empty.state); expect(eventChainTargets(empty)).toEqual([]);
    expect(() => parseEventChain({ ...empty, parent_state: 'missing' }, 4, 2)).toThrow();
  });
  it('父头迟到改变匹配结果但保持原记录声明与首块身份', () => {
    const before = parseEventChain(unresolved('missing_parent'), 4, 2)!;
    const after = parseEventChain(view(), 4, 2)!;
    expect([before.native_uuid, before.parent_uuid, before.record_event_id, before.recorded_at])
      .toEqual([after.native_uuid, after.parent_uuid, after.record_event_id, after.recorded_at]);
    expect(eventChainTargets(before)).toEqual([]); expect(eventChainTargets(after)).toEqual([{ event_id: 1 }]);
  });
  it('多块共享原记录声明，只生成event_id导航，不借来源文件字节窗口', () => {
    const input = { ...view(), source_byte_start: 6000, source_byte_end: 7000,
      parent_references: [{ event_id: 1, file_instance_id: 7, byte_start: 8000, byte_end: 9000 }] };
    const laterBlock = parseEventChain(input, 4, 2)!;
    const firstBlock = parseEventChain({ ...input, event_id: 3 }, 3, 2)!;
    expect(laterBlock.record_event_id).toBe(firstBlock.record_event_id);
    expect(laterBlock.parent_references).toEqual(firstBlock.parent_references);
    expect(eventChainTargets(laterBlock)).toEqual([{ event_id: 1 }]);
    expect(() => parseEventChain(input, 5, 2)).toThrow();
    expect(() => parseEventChain(input, 4, 9)).toThrow();
    expect(() => parseEventChain({ ...input, record_event_id: 5 }, 4, 2)).toThrow();
  });
  it('多副本与省略清单不代选唯一父原文，祖先多来源也不能伪装完整', () => {
    const input: EventChainData = { ...view(), resolution_scope: 'project_uuid',
      parent_references: Array.from({ length: 20 }, (_, index) => ({ event_id: index + 10, file_instance_id: index + 10 })),
      parent_references_total: 21, parent_references_partial: true, parent_source_contexts: 21,
      ancestry_state: 'multiple_source_contexts' };
    const result = parseEventChain(input, 4, 2)!;
    expect(eventChainTargets(result)).toHaveLength(20);
    expect(result.parent_references_total).toBe(21); expect(result.parent_references_partial).toBe(true);
    expect(result.ancestry_state).toBe('multiple_source_contexts');
    expect(() => parseEventChain({ ...input, parent_references_partial: false }, 4, 2)).toThrow();
    expect(() => parseEventChain({ ...input, parent_references: input.parent_references.slice(1) }, 4, 2)).toThrow();
  });
  it('冲突、歧义、循环及跨项目均不生成父引用，原声明仍可保留', () => {
    for (const state of ['conflicting_record', 'ambiguous_parent', 'cycle', 'outside_project', 'metadata_incomplete'] as const) {
      const result = parseEventChain(unresolved(state), 4, 2)!;
      expect(result.parent_uuid).toBe(parent); expect(eventChainTargets(result)).toEqual([]);
      expect(() => parseEventChain({ ...view(), state }, 4, 2)).toThrow();
    }
  });
  it('积压与64步上限不被隐藏，旧接口没有统计不能补造完整或零', () => {
    expect(parseEventChain(undefined, 4, 2)).toBeNull();
    const gap = parseEventChain({ ...view(), source_metadata_complete: false, scope_metadata_incomplete: true,
      ancestry_state: 'depth_limit', ancestry_steps: 64 }, 4, 2)!;
    expect(gap.source_metadata_complete).toBe(false); expect(gap.scope_metadata_incomplete).toBe(true);
    expect(gap.ancestry_steps).toBe(64);
    const ancestryGap = parseEventChain({ ...gap, ancestry_state: 'metadata_incomplete', ancestry_steps: 1 }, 4, 2)!;
    expect(ancestryGap.ancestry_state).toBe('metadata_incomplete');
    expect(eventChainTargets(ancestryGap)).toEqual([{ event_id: 1 }]);
    expect(() => parseEventChain({ ...gap, ancestry_steps: 63 }, 4, 2)).toThrow();
    expect(() => parseEventChain({ ...gap, source_metadata_complete: undefined }, 4, 2)).toThrow();
  });
  it('无效身份和伪造引用不能显示为有效匹配', () => {
    for (const patch of [{ native_uuid: child + '\n' }, { uuid_state: 'missing' }, { parent_uuid: null },
      { sidechain_state: 'declared' }, { ancestry_limit: 1000 }, { ancestry_state: 'root' },
      { parent_references: [{ event_id: 0, file_instance_id: 7 }] }])
      expect(() => parseEventChain({ ...view(), ...patch }, 4, 2)).toThrow();
  });
});
