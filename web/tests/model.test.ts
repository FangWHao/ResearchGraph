import { describe, expect, it } from 'vitest';
import { adoption, entityVersions, evidenceState, foldGroup, graphNodeId, joinRecords, projectGraph, relatedChildren, scopeKey, timeline } from '../src/model';
import type { SemanticEdge } from '../src/model';
import type { Claim } from '../src/types';

function claim(id: number, fields: Partial<Claim> = {}): Claim {
  return {
    claim_id: id, claim_type: 'entity_version', entity_id: `e${id}`,
    payload: { claim_type: 'entity_version', kind: 'approach', label: `方案 ${id}` },
    scope: { dataset: 'v1' }, basis: 'model_inference', actor: 'model:1', claim_state: 'candidate',
    effective_state: 'candidate', occurred_at: '2026-10-09T10:00:00Z', recorded_at: '2026-10-09T10:01:00Z',
    replaces_claim: null, replacement_ids: [], review: null, confirmation_source: null,
    evidence: [{ span_id: id, event_id: 1, byte_start: 0, byte_end: 3, quote_sha256: 'synthetic', role: 'support', session_pk: 1, seq: 1, path: 'fixture', source_byte_start: 0, source_byte_end: 3, occurred_at: null, recorded_at: '' }],
    groups: [{ segment_id: 'segment', session_pk: 1 }], ...fields,
  };
}
function decision(id: number, action: string, state: Claim['effective_state'] = 'confirmed', scope = { dataset: 'v1' }): Claim {
  return claim(id, { claim_type: 'decision_event', entity_id: 'approach', effective_state: state, payload: { claim_type: 'decision_event', target: 'approach', action }, scope });
}
describe('审核、采用、证据和范围独立', () => {
  it('汇合语义只取同范围记录，多条候选或确认不任意挑成当前事实', () => {
    const v1 = { dataset: 'v1' }, v2 = { dataset: 'v2' };
    const older = claim(1, { claim_type: 'join_ports', payload: { claim_type: 'join_ports', target: 'join', semantics: 'all_required' }, effective_state: 'confirmed', scope: v1 });
    const other = claim(2, { ...older, claim_id: 2, scope: v2, payload: { claim_type: 'join_ports', target: 'join', semantics: 'evidence_synthesis' } });
    const candidate = claim(3, { ...older, claim_id: 3, effective_state: 'candidate', payload: { claim_type: 'join_ports', target: 'join', semantics: 'compare_then_select' } });
    expect(joinRecords([other, older, candidate], 'join', v1).map(item => item.claim_id)).toEqual([1, 3]);
    expect(joinRecords([other, older, candidate], 'join', v1, false).map(item => item.claim_id)).toEqual([1]);
    expect(joinRecords([other, older, { ...candidate, effective_state: 'dismissed' }], 'join', v1).map(item => item.claim_id)).toEqual([1]);
    expect(joinRecords([older, candidate], 'join', v2)).toEqual([]);
  });
  it('候选采用、已驳回撤回均不改变当前采用', () => {
    expect(adoption([decision(1, 'accepted', 'candidate')], 'approach', { dataset: 'v1' })).toBe('unknown');
    expect(adoption([decision(1, 'accepted'), decision(2, 'withdrawn', 'dismissed')], 'approach', { dataset: 'v1' })).toBe('accepted');
  });
  it('不同范围独立，即使字段顺序不同仍识别同一范围', () => {
    expect(scopeKey({ step: 'a', dataset: 'v1' })).toBe(scopeKey({ dataset: 'v1', step: 'a' }));
    expect(adoption([decision(1, 'accepted'), decision(2, 'rejected', 'confirmed', { dataset: 'v2' })], 'approach', { dataset: 'v1' })).toBe('accepted');
    expect(adoption([decision(1, 'accepted'), decision(2, 'rejected', 'confirmed', { dataset: 'v2' })], 'approach', { dataset: 'v2' })).toBe('rejected');
  });
  it('同刻相反确认不任意挑选，发生时间缺失不当作已知', () => {
    expect(adoption([decision(1, 'accepted'), decision(2, 'withdrawn')], 'approach', { dataset: 'v1' })).toBe('conflict');
    expect(adoption([{ ...decision(1, 'accepted'), occurred_at: null }], 'approach', { dataset: 'v1' })).toBe('time_unknown');
    const a = claim(1, { claim_type: 'evidence_event', payload: { claim_type: 'evidence_event', target: 'finding', state: 'supported' }, effective_state: 'confirmed' });
    const b = { ...a, claim_id: 2, payload: { ...a.payload, state: 'refuted' } };
    expect(evidenceState([a, b], 'finding', { dataset: 'v1' })).toBe('needs_review');
    expect(evidenceState([{ ...a, effective_state: 'candidate' }], 'finding', { dataset: 'v1' })).toBe('unassessed');
  });
  it('并行 scope 版本保留，候选不能覆盖已确认版本，编辑版替换旧版', () => {
    const v1 = claim(1, { entity_id: 'shared', effective_state: 'confirmed' });
    const v2 = claim(2, { entity_id: 'shared', effective_state: 'confirmed', scope: { dataset: 'v2' } });
    const candidate = claim(3, { entity_id: 'shared' });
    expect(entityVersions([v1, v2, candidate]).map(item => item.claim_id)).toEqual([1, 2]);
    const edited = claim(4, { entity_id: 'shared', effective_state: 'confirmed', replaces_claim: 1 });
    expect(entityVersions([{ ...v1, effective_state: 'dismissed' }, v2, edited]).map(item => item.claim_id)).toEqual([2, 4]);
    expect(graphNodeId(v1)).not.toBe(graphNodeId(v2));
  });
  it('时间线保留撤回、再次采用和驳回候选，不删除历史', () => {
    const events = ['proposed', 'accepted', 'withdrawn', 'accepted'].map((action, index) => ({ ...decision(index + 1, action), occurred_at: `2026-10-09T1${index}:00:00Z` }));
    expect(timeline([...events, decision(9, 'rejected', 'dismissed')], 'approach')).toHaveLength(5);
    expect(adoption(events, 'approach', { dataset: 'v1' })).toBe('accepted');
  });
  it('问题、方案与尝试的归属严格匹配关系和卡片的 scope', () => {
    const v1 = { dataset: 'v1' }, v2 = { dataset: 'v2' };
    const edges = [claim(10, { claim_type: 'relation', scope: v1, payload: { claim_type: 'relation', source: 'approach', target: 'question', relation: 'part_of' } }), claim(11, { claim_type: 'relation', scope: v2, payload: { claim_type: 'relation', source: 'attempt', target: 'approach', relation: 'part_of' } })];
    expect(relatedChildren(edges, 'question', v1)).toEqual(['approach']);
    expect(relatedChildren(edges, 'question', v2)).toEqual([]);
    expect(relatedChildren(edges, 'approach', v1)).toEqual([]);
    expect(relatedChildren(edges, 'approach', v2)).toEqual(['attempt']);
  });
  it('同一瞬间的时区表示不同仍需澄清，不能按字符串挑当前事实', () => {
    const accepted = decision(1, 'accepted');
    const withdrawn = { ...decision(2, 'withdrawn'), occurred_at: '2026-10-09T18:00:00+08:00' };
    expect(adoption([accepted, withdrawn], 'approach', { dataset: 'v1' })).toBe('conflict');
  });
  it('关系缺少同范围端点时不跨 scope 回退，记录保留并禁止折叠', () => {
    const a = claim(1), b = claim(2, { scope: { dataset: 'v2' } });
    const relation = claim(8, { claim_type: 'relation', scope: { dataset: 'v2' }, payload: { claim_type: 'relation', source: 'e1', target: 'e2', relation: 'supports' } });
    const projected = projectGraph([a, b, relation]);
    expect(projected.entities).toHaveLength(2);
    expect(projected.edges).toEqual([]);
    expect(projected.unresolvedClaimIds).toEqual([8]);
    expect(() => foldGroup(projected.entities.map(graphNodeId), projected.edges, [a, b, relation], projected.unresolvedClaimIds.length === 0)).toThrow('完整研究图');
  });
});
describe('纯视图过程组保持证据', () => {
  const nodes = [claim(1), claim(2), claim(3), claim(4)];
  const nodeIds = nodes.map(graphNodeId);
  const edges: SemanticEdge[] = [0, 1, 2].map(index => ({ id: `edge-${index}`, source: nodeIds[index], target: nodeIds[index + 1], claimId: index + 10, relation: 'supports' }));
  const middleEvidence = claim(11, { claim_type: 'relation', entity_id: null });
  it('单入口单出口组保留所有内部节点与关系 span IDs，原对象不变', () => {
    const original = JSON.stringify({ nodes, edges });
    expect(foldGroup([nodeIds[1], nodeIds[2]], edges, [...nodes, middleEvidence]).evidenceIds).toEqual([2, 3, 11]);
    expect(JSON.stringify({ nodes, edges })).toBe(original);
    expect(edges.filter(edge => ![nodeIds[1], nodeIds[2]].includes(edge.source) || ![nodeIds[1], nodeIds[2]].includes(edge.target)).map(edge => edge.claimId)).toEqual([10, 12]);
  });
  it('多入口、无出口、断开的组一律拒绝；same_topic 不成为语义入口', () => {
    expect(() => foldGroup([nodeIds[1], nodeIds[2]], edges, nodes, false)).toThrow('完整研究图');
    expect(() => foldGroup([nodeIds[1], nodeIds[2]], [...edges, { id: 'extra', source: nodeIds[0], target: nodeIds[2], claimId: 20, relation: 'supports' }], nodes)).toThrow('单入口单出口');
    expect(() => foldGroup([nodeIds[1], nodeIds[2]], edges.slice(0, 2), nodes)).toThrow('单入口单出口');
    expect(() => foldGroup([nodeIds[1], nodeIds[2]], edges.filter(edge => edge.claimId !== 11), nodes)).toThrow('连通');
    const topic = { id: 'topic', source: nodeIds[0], target: nodeIds[2], claimId: 20, relation: 'same_topic' };
    expect(foldGroup([nodeIds[1], nodeIds[2]], [...edges, topic], nodes).members).toEqual([nodeIds[1], nodeIds[2]]);
  });
});
