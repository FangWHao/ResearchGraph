import { describe, expect, it } from 'vitest';
import { adoption, evidenceNames, evidenceStateNames, evidenceState, foldGroup, graphNodeId, projectGraph, timeline } from '../src/model';
import { claimInstant, exactInstant } from '../src/exactTime';
import type { Claim } from '../src/types';

const scope = { data: 'synthetic_state_time_v1', step: '精确状态验收' };
function event(id: number, kind: 'decision_event' | 'evidence_event', value: string, occurred: string | null, canonical?: string | null): Claim {
  return { claim_id: id, claim_type: kind, entity_id: 'entity', scope, payload: { claim_type: kind, target: 'entity', [kind === 'decision_event' ? 'action' : 'state']: value },
    basis: 'direct_record', actor: 'human:合成验收', claim_state: 'candidate', effective_state: 'confirmed', occurred_at: occurred,
    recorded_at: id === 1 ? '2026-10-10T10:00:01Z' : '2026-10-10T10:00:02Z', replaces_claim: null, replacement_ids: [], review: null,
    confirmation_source: 'human', groups: [], evidence: [], ...(canonical !== undefined ? { occurred_at_utc: canonical } : {}) };
}
const decision = (id: number, action: string, occurred: string | null, canonical?: string | null) => event(id, 'decision_event', action, occurred, canonical);
const evidence = (id: number, state: string, occurred: string | null, canonical?: string | null) => event(id, 'evidence_event', state, occurred, canonical);
describe('采用和证据的精确时序与未知边界', () => {
  it('公历与UTC偏移核对到微秒，年0099不变1900年代，Unix前与越界不误读', () => {
    expect(exactInstant('1970-01-01T00:00:00.000001Z')).toBe(1n);
    expect(exactInstant('1969-12-31T23:59:59.999999Z')).toBe(-1n);
    expect(exactInstant('0099-01-01T00:00:00Z')).toBe(-59042995200000000n);
    expect(exactInstant('1000-01-01T00:00:00Z')).toBe(-30610224000000000n);
    expect(exactInstant('0001-01-01T00:00:00Z')).toBe(-62135596800000000n);
    expect(exactInstant('2024-02-29T00:00:00Z')).not.toBeNull();
    for (const invalid of ['1900-02-29T00:00:00Z', '2100-02-29T00:00:00Z', '0000-01-01T00:00:00Z', '0001-01-01T00:00:00+14:00', '9999-12-31T23:59:59-14:00', '2026-01-01T00:00:00+08:60', '2026-01-01T00:00:60Z', '2026-01-01 00:00:00Z', '20260101T000000Z']) expect(exactInstant(invalid)).toBeNull();
    expect(exactInstant('2026-10-10T18:00:00.123456+08:00')).toBe(exactInstant('2026-10-10T10:00:00.123456Z'));
    for (const ending of ['\n', '\r', '\r\n']) {
      expect(exactInstant('2026-10-10T10:00:00Z' + ending)).toBeNull();
      expect(claimInstant(decision(1, 'accepted', '2026-10-10T10:00:00Z', '2026-10-10T10:00:00.000001+00:00' + ending), 'occurred')).toBeNull();
    }
  });
  it('同毫秒不同微秒按发生顺序，倒序入库与数组方向不改变状态', () => {
    const newer = decision(1, 'accepted', '2026-10-10T10:00:00.000002Z');
    const older = decision(2, 'rejected', '2026-10-10T10:00:00.000001Z');
    const refuted = evidence(1, 'refuted', newer.occurred_at), supported = evidence(2, 'supported', older.occurred_at);
    for (const rows of [[newer, older], [older, newer]]) expect(adoption(rows, 'entity', scope)).toBe('accepted');
    for (const rows of [[refuted, supported], [supported, refuted]]) expect(evidenceState(rows, 'entity', scope)).toBe('refuted');
    expect(timeline([newer, older], 'entity').map(item => item.claim_id)).toEqual([2, 1]);
  });
  it('等价时区同一微秒相反事件都冲突，不按入库或ID取胜', () => {
    expect(adoption([decision(1, 'accepted', '2026-10-10T10:00:00.123456Z'), decision(2, 'withdrawn', '2026-10-10T18:00:00.123456+08:00')], 'entity', scope)).toBe('conflict');
    expect(evidenceState([evidence(1, 'supported', '2026-10-10T10:00:00.123456Z'), evidence(2, 'refuted', '2026-10-10T18:00:00.123456+08:00')], 'entity', scope)).toBe('conflict');
  });
  it('单条或多条有效确认事件的时间缺失都未知，入库时间不能补发生时间', () => {
    for (const value of [null, '2026-10-10T10:00:00', '2026-02-30T10:00:00Z', '2026-10-10T24:00:00Z', '2026-10-10T10:00:00.1234567Z']) {
      expect(adoption([decision(1, 'accepted', value)], 'entity', scope)).toBe('time_unknown');
      expect(evidenceState([evidence(1, 'supported', value)], 'entity', scope)).toBe('time_unknown');
      expect(evidenceState([evidence(1, 'supported', value), evidence(2, 'refuted', '2026-10-10T10:00:01Z')], 'entity', scope)).toBe('time_unknown');
    }
  });
  it('canonical显式null覆盖看似有效原值，特殊原始ISO只依据有效canonical', () => {
    expect(adoption([decision(1, 'accepted', '2026-10-10T10:00:00Z', null)], 'entity', scope)).toBe('time_unknown');
    expect(evidenceState([evidence(1, 'supported', '2026-10-10T10:00:00Z', null)], 'entity', scope)).toBe('time_unknown');
    expect(adoption([decision(1, 'accepted', '2026-W41-6T10:00:00Z', '2026-10-10T10:00:00.000001+00:00')], 'entity', scope)).toBe('accepted');
    expect(evidenceState([evidence(1, 'refuted', '2026-W41-6T10:00:00Z', '2026-10-10T10:00:00.000001+00:00')], 'entity', scope)).toBe('refuted');
    const invalid = decision(1, 'accepted', '2026-10-10T10:00:00Z', '2026-02-30T10:00:00.000000+00:00');
    expect(claimInstant(invalid, 'occurred')).toBeNull();
    expect(claimInstant({ ...invalid, recorded_at_utc: null }, 'recorded')).toBeNull();
  });
  it('展示诊断不进入人工证据编辑枚举，缺少证据仍未评估', () => {
    expect(evidenceNames).not.toHaveProperty('time_unknown'); expect(evidenceNames).not.toHaveProperty('conflict');
    expect(evidenceStateNames.time_unknown).toBeTruthy(); expect(evidenceStateNames.conflict).toBeTruthy();
    expect(evidenceState([], 'entity', scope)).toBe('unassessed');
  });
  it('未知scope不能生成采用或受支持，完整scope仍严格相等', () => {
    const scopes: Claim['scope'][] = [null, {}, { data: 'unknown' }, { data: ' 未确定 ' }];
    for (const unknown of scopes) {
      const d = { ...decision(1, 'accepted', '2026-10-10T10:00:00Z'), scope: unknown }, e = { ...evidence(1, 'supported', '2026-10-10T10:00:00Z'), scope: unknown };
      expect(adoption([d], 'entity', unknown)).toBe('unknown_scope');
      expect(evidenceState([e], 'entity', unknown)).toBe('needs_review');
    }
    expect(evidenceState([evidence(1, 'refuted', '2026-10-10T10:00:00Z')], 'entity', { data: scope.data })).toBe('unassessed');
  });
  it('候选、驳回和已替换的晚到或无时间记录不改变有效状态', () => {
    for (const inactive of [{ effective_state: 'candidate' as const }, { effective_state: 'dismissed' as const }, { replacement_ids: [100] }]) {
      expect(adoption([decision(1, 'accepted', '2026-10-10T10:00:00Z'), { ...decision(2, 'withdrawn', null), ...inactive }], 'entity', scope)).toBe('accepted');
      expect(evidenceState([evidence(1, 'refuted', '2026-10-10T10:00:00Z'), { ...evidence(2, 'supported', null), ...inactive }], 'entity', scope)).toBe('refuted');
    }
  });
  it('折叠只改投影，微秒决定与阴性证据及原记录集合不变', () => {
    const nodes = [1, 2, 3, 4].map(id => ({ ...decision(id, 'accepted', '2026-10-10T10:00:00Z'), claim_type: 'entity_version', entity_id: `finding-${id}`, payload: { claim_type: 'entity_version', kind: 'finding' as const, label: `精确合成发现${id}` } }));
    const edges = [10, 11, 12].map((id, index) => ({ ...decision(id, 'accepted', '2026-10-10T10:00:00Z'), claim_type: 'relation', payload: { claim_type: 'relation', source: nodes[index].entity_id, target: nodes[index + 1].entity_id, relation: 'supports' } }));
    const states = [decision(20, 'accepted', '2026-10-10T10:00:00.000002Z'), decision(21, 'rejected', '2026-10-10T10:00:00.000001Z'), evidence(22, 'refuted', '2026-10-10T10:00:00.000002Z'), evidence(23, 'supported', '2026-10-10T10:00:00.000001Z')].map(item => ({ ...item, payload: { ...item.payload, target: 'finding-2' } }));
    const claims = [...nodes, ...edges, ...states], original = JSON.stringify(claims), graph = projectGraph(claims);
    const fold = foldGroup(nodes.slice(1, 3).map(graphNodeId), graph.edges, claims);
    expect(fold.claimIds).toEqual(expect.arrayContaining(states.map(item => item.claim_id)));
    expect(adoption(claims, 'finding-2', scope)).toBe('accepted'); expect(evidenceState(claims, 'finding-2', scope)).toBe('refuted');
    expect(JSON.stringify(claims)).toBe(original);
  });
});
