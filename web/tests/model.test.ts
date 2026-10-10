import { describe, expect, it } from 'vitest';
import { adoption, entityVersions, evidenceState, foldGroup, foldProjection, graphNodeId, isSemanticEdge, joinRecords, projectGraph, relatedChildren, scopeKey, timeline } from '../src/model';
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
  it('图保留同范围所有有效内容版本，不按确认/入库顺序推定一个当前版本', () => {
    const first = claim(1, { entity_id: 'shared', effective_state: 'confirmed' });
    const second = claim(2, { entity_id: 'shared', effective_state: 'confirmed' });
    const candidate = claim(3, { entity_id: 'shared' });
    const graph = projectGraph([second, candidate, first]);
    expect(graph.entities).toHaveLength(1);
    expect(graph.versions.get(graphNodeId(first))?.map(item => item.claim_id)).toEqual([2, 3, 1]);
    expect(projectGraph([second, candidate, first], false).versions.get(graphNodeId(first))?.map(item => item.claim_id)).toEqual([2, 1]);
  });
  it('已有替代版的旧确认不成为有效内容、关系、采用或汇合，原历史仍可查', () => {
    const nodes = [claim(1, { effective_state: 'confirmed' }), claim(2, { effective_state: 'confirmed' })];
    const oldEntity = { ...nodes[0], claim_id: 3, replacement_ids: [1] };
    const relation = claim(10, { claim_type: 'relation', entity_id: null, effective_state: 'confirmed', replacement_ids: [11], payload: { claim_type: 'relation', source: 'e1', target: 'e2', relation: 'part_of' } });
    const join = claim(12, { claim_type: 'join_ports', effective_state: 'confirmed', replacement_ids: [13], payload: { claim_type: 'join_ports', target: 'e2', semantics: 'all_required', inputs: [{ port: '旧端口', ref: 'e1' }] } });
    const oldDecision = { ...decision(20, 'accepted'), replacement_ids: [21] };
    const oldEvidence = claim(30, { claim_type: 'evidence_event', effective_state: 'confirmed', replacement_ids: [31], payload: { claim_type: 'evidence_event', target: 'e2', state: 'supported' } });
    const history = [...nodes, oldEntity, relation, join, oldDecision, oldEvidence];
    const original = JSON.stringify(history);
    const graph = projectGraph(history);
    expect(graph.versions.get(graphNodeId(nodes[0]))?.map(item => item.claim_id)).toEqual([1]);
    expect(entityVersions(history).map(item => item.claim_id)).toEqual([1, 2]);
    expect(graph.edges).toEqual([]);
    expect(graph.unresolvedClaimIds).toEqual([]);
    expect(joinRecords(history, 'e2', nodes[1].scope)).toEqual([]);
    expect(relatedChildren(history, 'e2', nodes[1].scope)).toEqual([]);
    expect(adoption(history, 'approach', nodes[0].scope)).toBe('unknown');
    expect(evidenceState(history, 'e2', nodes[1].scope)).toBe('unassessed');
    expect(timeline(history, 'approach').map(item => item.claim_id)).toEqual([20]);
    expect(JSON.stringify(history)).toBe(original);
  });
  it('比较只把选中端口标成使用，共同输入和综合证据分别保留各端口', () => {
    const nodes = [claim(1), claim(2), claim(3)];
    const compare = claim(10, { claim_type: 'join_ports', entity_id: null, payload: { claim_type: 'join_ports', target: 'e3', semantics: 'compare_then_select', selected: 'e1', inputs: [{ port: '甲', ref: 'e1' }, { port: '乙', ref: 'e2' }] } });
    const projected = projectGraph([...nodes, compare]);
    expect(projected.edges.map(edge => [edge.role, edge.targetPort, isSemanticEdge(edge)])).toEqual([['selected_input', '甲', true], ['compared_input', '乙', false]]);
    expect(projected.edges.every(edge => edge.claimId === 10 && edge.evidenceIds?.includes(10))).toBe(true);
    for (const semantics of ['all_required', 'evidence_synthesis']) {
      const graph = projectGraph([...nodes, { ...compare, payload: { ...compare.payload, semantics, selected: null } }]);
      expect(graph.edges.filter(isSemanticEdge)).toHaveLength(2);
      expect(graph.edges.map(edge => edge.joinSemantics)).toEqual([semantics, semantics]);
    }
    expect(projectGraph([...nodes, { ...compare, payload: { ...compare.payload, selected: 'missing' } }]).unresolvedClaimIds).toEqual([10]);
  });
});
describe('纯视图过程组保持证据', () => {
  const nodes = [claim(1), claim(2), claim(3), claim(4)];
  const nodeIds = nodes.map(graphNodeId);
  const edges: SemanticEdge[] = [0, 1, 2].map(index => ({ id: `edge-${index}`, source: nodeIds[index], target: nodeIds[index + 1], claimId: index + 10, relation: 'supports' }));
  const relations = [10, 11, 12, 20].map(id => claim(id, { claim_type: 'relation', entity_id: null }));
  const records = [...nodes, ...relations];
  it('单入口单出口组保留所有内部及边界原关系和证据，原对象不变', () => {
    const original = JSON.stringify({ nodes, edges });
    const folded = foldGroup([nodeIds[1], nodeIds[2]], edges, records);
    expect(folded.evidenceIds).toEqual([2, 3, 10, 11, 12]);
    expect(folded.internalEdges).toEqual([edges[1]]);
    expect(folded.boundaryEdges).toEqual([edges[0], edges[2]]);
    expect(foldProjection(edges, folded).map(edge => [edge.id, edge.claimId, edge.relation])).toEqual([['edge-0', 10, 'supports'], ['edge-2', 12, 'supports']]);
    expect(foldProjection(edges, null)).toEqual(edges);
    expect(JSON.stringify({ nodes, edges })).toBe(original);
    expect(edges.filter(edge => ![nodeIds[1], nodeIds[2]].includes(edge.source) || ![nodeIds[1], nodeIds[2]].includes(edge.target)).map(edge => edge.claimId)).toEqual([10, 12]);
  });
  it('多入口、无出口、断开的组一律拒绝；same_topic 不成为语义入口', () => {
    expect(() => foldGroup([nodeIds[1], nodeIds[2]], edges, records, false)).toThrow('完整研究图');
    expect(() => foldGroup([nodeIds[1], nodeIds[2]], [...edges, { id: 'extra', source: nodeIds[0], target: nodeIds[2], claimId: 20, relation: 'supports' }], records)).toThrow('单入口单出口');
    expect(() => foldGroup([nodeIds[1], nodeIds[2]], edges.slice(0, 2), records)).toThrow('单入口单出口');
    expect(() => foldGroup([nodeIds[1], nodeIds[2]], edges.filter(edge => edge.claimId !== 11), records)).toThrow('连通');
    const topic = { id: 'topic', source: nodeIds[0], target: nodeIds[2], claimId: 20, relation: 'same_topic' };
    expect(foldGroup([nodeIds[1], nodeIds[2]], [...edges, topic], records).members).toEqual([nodeIds[1], nodeIds[2]]);
  });
  it('撤回、阴性证据、范围改变与未处理审核连同其原证据在组内保留', () => {
    const withdrawal = { ...decision(30, 'withdrawn'), entity_id: 'e2', payload: { claim_type: 'decision_event', target: 'e2', action: 'withdrawn' } };
    const negative = claim(31, { claim_type: 'evidence_event', entity_id: null, effective_state: 'confirmed', payload: { claim_type: 'evidence_event', target: 'e3', state: 'refuted' } });
    const scopeChange = claim(32, { entity_id: 'e2', scope: { dataset: 'v2' } });
    const history = [...records, withdrawal, negative, scopeChange];
    const before = JSON.stringify(history);
    const folded = foldGroup([nodeIds[1], nodeIds[2]], edges, history);
    expect(folded.claimIds).toEqual(expect.arrayContaining([2, 3, 10, 11, 12, 30, 31, 32]));
    expect(folded.evidenceIds).toEqual([2, 3, 10, 11, 12, 30, 31, 32]);
    expect(adoption(history, 'e2', nodes[1].scope)).toBe('withdrawn');
    expect(evidenceState(history, 'e3', nodes[2].scope)).toBe('refuted');
    expect(JSON.stringify(history)).toBe(before);
  });
  it('比较和主题边跨边界仍保留原端点、端口、ID及证据，不计为共同使用', () => {
    const compared: SemanticEdge = { id: 'compared', source: nodeIds[0], target: nodeIds[2], sourceEntity: 'e1', targetEntity: 'e3', targetPort: '备选', joinSemantics: 'compare_then_select', role: 'compared_input', semantic: false, claimId: 20, relation: 'input:备选', evidenceIds: [20] };
    const withCompare = [...edges, compared];
    const folded = foldGroup([nodeIds[1], nodeIds[2]], withCompare, records);
    expect(folded.boundaryEdges).toContainEqual(compared);
    expect(folded.evidenceIds).toContain(20);
    const shown = foldProjection(withCompare, folded).find(edge => edge.id === 'compared')!;
    expect(shown).toMatchObject({ sourceEntity: 'e1', targetEntity: 'e3', targetPort: '备选', claimId: 20, role: 'compared_input', evidenceIds: [20] });
    expect(isSemanticEdge(shown)).toBe(false);
    expect(foldProjection(withCompare, null)).toEqual(withCompare);
  });
  it('循环关系不删除；真实共同输入多入口与缺原记录仍拒绝折叠', () => {
    const loop = { ...edges[1], id: 'loop', source: nodeIds[2], target: nodeIds[1], claimId: 20 };
    expect(foldGroup([nodeIds[1], nodeIds[2]], [...edges, loop], records).internalEdges).toEqual([edges[1], loop]);
    const secondInput = { ...loop, source: nodeIds[0], role: 'required_input' as const, semantic: true };
    expect(() => foldGroup([nodeIds[1], nodeIds[2]], [...edges, secondInput], records)).toThrow('单入口单出口');
    expect(() => foldGroup([nodeIds[1], nodeIds[2]], edges, nodes)).toThrow('原记录缺失');
    const comparisonOnly = edges.map(edge => edge.claimId === 11 ? { ...edge, role: 'compared_input' as const, semantic: false } : edge);
    expect(() => foldGroup([nodeIds[1], nodeIds[2]], comparisonOnly, records)).toThrow('连通');
  });
});
