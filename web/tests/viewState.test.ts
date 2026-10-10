import { describe, expect, it } from 'vitest';
import { foldProjection, graphNodeId, projectGraph } from '../src/model';
import { parsePersonalRecord, parsePersonalView, personalIdentity, personalSaveBody, resolvePersonalView, samePersonalReading, viewFingerprint } from '../src/viewState';
import type { Claim, PersonalView, ResearchGraphData } from '../src/types';

function record(id: number, fields: Partial<Claim> = {}): Claim {
  return { claim_id: id, claim_type: 'entity_version', entity_id: `e${id}`, kind: 'finding',
    payload: { claim_type: 'entity_version', kind: 'finding', label: `合成发现${id}` }, scope: { data: 'v1' },
    basis: 'direct_record', actor: 'human:合成', claim_state: 'candidate', effective_state: 'candidate',
    occurred_at: '2026-01-01T00:00:00Z', recorded_at: '2026-01-01T00:00:00Z',
    replaces_claim: null, replacement_ids: [], review: null, confirmation_source: null, evidence: [], groups: [], ...fields };
}
function graph(): ResearchGraphData {
  const entities = [1, 2, 3, 4].map(id => record(id));
  const edges = [1, 2, 3].map((id, index) => record(id + 10, { claim_type: 'relation', kind: undefined,
    payload: { claim_type: 'relation', relation: 'supports', source: `e${id}`, target: `e${id + 1}` },
    evidence: [{ span_id: index + 1, event_id: 1, byte_start: 0, byte_end: 2, quote_sha256: 'synthetic', role: 'support', session_pk: 1, seq: 1, path: '合成', source_byte_start: 0, source_byte_end: 2, occurred_at: null, recorded_at: '' }] }));
  return { project_id: 'p', revision: 20, occurred_until: '2026-10-11T00:00:00.000001Z', known_until: '2026-10-11T00:00:00.000002Z', scope: null, scope_filter: false, total: 7, claims: [...entities, ...edges], partial: false, intent: 'synthetic' };
}
function view(data = graph()): PersonalView {
  return { format_version: 1, reading: { project_id: data.project_id, revision: data.revision, occurred_until: data.occurred_until, known_until: data.known_until },
    show_candidates: true, positions: Object.fromEntries(projectGraph(data.claims).entities.map((claim, i) => [graphNodeId(claim), { x: i * 300, y: 100 }])),
    viewport: { x: -100, y: 40, zoom: 0.7 }, selected_versions: {}, process_group: null };
}
describe('个人视图仅保存布局并重新核对研究记录', () => {
  it('完整节点及同修订展开恢复原边界、端口、证据和位置，组不是保存节点', () => {
    const data = graph(), saved = view(data), before = JSON.stringify(data);
    saved.process_group = { name: '合成过程', members: data.claims.slice(1, 3).map(graphNodeId) };
    const checked = resolvePersonalView(saved, data);
    expect(checked.fold!.boundaryEdges).toHaveLength(2); expect(checked.fold!.internalEdges).toHaveLength(1);
    expect(checked.fold!.evidenceIds).toEqual([1, 2, 3]); expect(Object.keys(saved.positions)).toHaveLength(4);
    expect(foldProjection(checked.graph.edges, checked.fold)).toHaveLength(2);
    expect(resolvePersonalView({ ...saved, process_group: null }, data).graph.edges).toEqual(checked.graph.edges);
    expect(JSON.stringify(data)).toBe(before); expect(saved.positions).toEqual(view(data).positions);
    expect(() => resolvePersonalView({ ...saved, positions: { ...saved.positions, 'view-process-group': { x: 0, y: 0 } } }, data)).toThrow();
  });
  it('新修订、微秒不同、缺页及错误项目都不能混入保存布局', () => {
    const data = graph(), saved = view(data);
    for (const patch of [{ revision: 21 }, { known_until: '2026-10-11T00:00:00.000003Z' }, { project_id: 'other' }, { total: 8 }, { partial: true }])
      expect(() => resolvePersonalView(saved, { ...data, ...patch })).toThrow();
    expect(samePersonalReading(saved.reading, { ...saved.reading, occurred_until: '2026-10-11T08:00:00.000001+08:00' })).toBe(true);
  });
  it('多内容版本只保存明确显示选择，不选最近ID且替代/错节点版本被拒绝', () => {
    const data = graph(); data.claims.push(record(5, { entity_id: 'e2' })); data.total++;
    const saved = view(data), id = graphNodeId(data.claims[1]);
    expect(resolvePersonalView(saved, data).graph.versions.get(id)).toHaveLength(2);
    saved.selected_versions[id] = 5; expect(resolvePersonalView(saved, data).graph.versions.get(id)!.map(c => c.claim_id)).toEqual([2, 5]);
    for (const selected of [4, 999]) expect(() => resolvePersonalView({ ...saved, selected_versions: { [id]: selected } }, data)).toThrow();
    const replaced = { ...data, claims: data.claims.map(c => c.claim_id === 5 ? { ...c, replacement_ids: [9] } : c) };
    expect(() => resolvePersonalView(saved, replaced)).toThrow();
  });
  it('隐藏候选、错误边界与不足的节点位置不能恢复成完整过程', () => {
    const data = graph(), saved = view(data), members = data.claims.slice(1, 3).map(graphNodeId);
    expect(() => parsePersonalView({ ...saved, show_candidates: false, process_group: { name: '组', members } }, 'p')).toThrow();
    expect(() => resolvePersonalView({ ...saved, process_group: { name: '组', members: data.claims.slice(0, 3).map(graphNodeId) } }, data)).toThrow();
    const missing = { ...saved.positions }; delete missing[members[0]];
    expect(() => resolvePersonalView({ ...saved, positions: missing }, data)).toThrow();
    const broken = { ...data, claims: data.claims.map(c => c.claim_id === 11 ? { ...c, payload: { ...c.payload, target: 'not-visible' } } : c) };
    expect(() => resolvePersonalView(saved, broken)).toThrow();
  });
  it('非有限坐标、范围外zoom、非法时间和额外正式状态字段不能保存', () => {
    const saved = view(), id = Object.keys(saved.positions)[0];
    for (const viewport of [{ x: NaN, y: 0, zoom: 1 }, { x: 1e7 + 1, y: 0, zoom: 1 }, { x: 0, y: 0, zoom: 0.11 }, { x: 0, y: 0, zoom: 1.51 }])
      expect(() => parsePersonalView({ ...saved, viewport }, 'p')).toThrow();
    expect(() => parsePersonalView({ ...saved, positions: { [id]: { x: Infinity, y: 0 } } }, 'p')).toThrow();
    expect(() => parsePersonalView({ ...saved, reading: { ...saved.reading, known_until: '2026-02-30T00:00:00Z' } }, 'p')).toThrow();
    expect(() => parsePersonalView({ ...saved, states: { adoption: 'accepted' } }, 'p')).toThrow();
  });
  it('UTF8全请求超限必须拒绝且不截断，身份去空白、CAS保留原值', () => {
    const saved = view(); const body = personalSaveBody('p', ' 合成人员 ', 9, saved);
    expect(body.user).toBe('合成人员'); expect(body.expected_view_id).toBe(9); expect(body.view).toBe(saved);
    const big = { ...saved, positions: Object.fromEntries(Array.from({ length: 100 }, (_, i) => [`${i}${'临'.repeat(500)}`, { x: i, y: 0 }])) };
    const before = viewFingerprint(big); expect(() => personalSaveBody('p', '合成', null, big)).toThrow('65536'); expect(viewFingerprint(big)).toBe(before);
    expect(personalIdentity('p', '甲')).not.toBe(personalIdentity('p', '乙')); expect(personalIdentity('p', '甲')).not.toBe(personalIdentity('q', '甲'));
  });
  it('保存回执必须匹配项目和姓名，坏旧格式保留CAS编号允许显式替代', () => {
    const receipt = { project_id: 'p', user: '甲', current_revision: 21, view_id: 9, saved_at: '2026-10-11T00:00:00Z', view: view(), unavailable_reason: null };
    expect(parsePersonalRecord(receipt, 'p', ' 甲 ')).toEqual(receipt);
    for (const patch of [{ project_id: 'q' }, { user: '乙' }, { saved_at: null }, { view_id: null }, { current_revision: -1 }])
      expect(() => parsePersonalRecord({ ...receipt, ...patch }, 'p', '甲')).toThrow();
    expect(parsePersonalRecord({ ...receipt, view: null, unavailable_reason: '旧格式不可用' }, 'p', '甲').view_id).toBe(9);
    expect(() => parsePersonalRecord({ ...receipt, view: null }, 'p', '甲')).toThrow();
    expect(parsePersonalRecord({ ...receipt, view: null, view_id: null, saved_at: null }, 'p', '甲').view).toBeNull();
  });
});
