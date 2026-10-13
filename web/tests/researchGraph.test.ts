import { describe, expect, it } from 'vitest';
import { appendResearchPage, emptyResearchDraft, parseResearchDetail, parseResearchPage, parseResearchRecord, researchData, researchIntent, researchQuery, researchRequest, validateResearchEvidence } from '../src/researchGraph';
import type { Claim, EvidenceData, ResearchReading } from '../src/types';

const reading: ResearchReading = { project_id: 'synthetic-project', revision: 42,
  occurred_until: '2026-10-10T00:00:00.000001+00:00', known_until: '2026-10-10T01:00:00.000001+00:00' };
function claim(id: number): Claim {
  return { claim_id: id, claim_type: 'entity_version', entity_id: 'synthetic-entity',
    payload: { claim_type: 'entity_version', kind: 'finding', label: '合成发现' }, scope: { data: 'v1' },
    basis: 'direct_record', actor: 'human:合成', claim_state: 'candidate', effective_state: 'candidate',
    occurred_at: '2026-01-01T00:00:00Z', recorded_at: '2026-01-01T00:00:01Z',
    replaces_claim: null, replacement_ids: [], replaced: false, review: null, confirmation_source: null,
    evidence: [], groups: [] };
}
function page(offset = 0, total = 201, length = 100) {
  return { ...reading, scope: null, scope_filter: false, layer: 'L2', projection: false,
    semantic_graph_complete: true, collection: 'claims', total, claims_total: total, offset,
    next_offset: offset + length < total ? offset + length : null,
    items: Array.from({ length }, (_, index) => claim(offset + index + 1)) };
}
describe('完整研究图的阅读意图和页身份', () => {
  it('双截止必须带时区且精确到微秒，留空才由首次响应固定', () => {
    expect(researchRequest('p', emptyResearchDraft())).toEqual({ project: 'p' });
    const draft = { occurredUntil: '2026-10-10T08:00:00.000001+08:00', knownUntil: '' };
    const request = researchRequest(reading.project_id, draft);
    expect(parseResearchPage(page(), reading.project_id, 0, undefined, request).occurred_until).toBe(reading.occurred_until);
    expect(() => parseResearchPage({ ...page(), occurred_until: '2026-10-10T00:00:00.000002Z' }, reading.project_id, 0, undefined, request)).toThrow();
    for (const occurredUntil of ['2026-10-10T00:00', '2026-02-30T00:00:00Z', '0099-02-29T00:00:00Z'])
      expect(() => researchRequest('p', { occurredUntil, knownUntil: '' })).toThrow();
    expect(researchIntent('p', draft)).not.toBe(researchIntent('q', draft));
    expect(researchIntent('p', draft)).not.toBe(researchIntent('p', { ...draft, knownUntil: reading.known_until }));
  });
  it('超过原2000条仍完整遍历，全部引用不会被限为20条，完成与读取中分开', () => {
    let records: Claim[] = []; const first = parseResearchPage(page(0, 2101), reading.project_id, 0);
    for (let offset = 0; offset < 2101; offset += 100) {
      const current = parseResearchPage(page(offset, 2101, Math.min(100, 2101 - offset)), reading.project_id, offset, first);
      records = appendResearchPage(records, current);
      expect(researchData(first, records, 'intent').partial).toBe(records.length !== 2101);
    }
    expect(records).toHaveLength(2101); expect(new Set(records.map(item => item.claim_id)).size).toBe(2101);
    const proof = Array.from({ length: 35 }, (_, index) => ({ span_id: index + 1, event_id: 1,
      byte_start: 0, byte_end: 3, quote_sha256: 'a'.repeat(64), role: 'support', session_pk: 1, seq: 0,
      path: 'synthetic-fixture://source', source_byte_start: 0, source_byte_end: 3, occurred_at: null, recorded_at: '2026-01-01T00:00:00Z' }));
    expect(parseResearchRecord({ ...claim(1), evidence: proof }).evidence).toEqual(proof);
    expect(researchQuery(first)).toEqual({ project: reading.project_id, expected_revision: 42,
      occurred_until: reading.occurred_until, known_until: reading.known_until });
  });
  it('不拼错项目、修订、scope、双截止或总数，既有页保持不变', () => {
    const first = parseResearchPage(page(), reading.project_id, 0);
    const original = JSON.stringify(first);
    for (const patch of [{ project_id: 'other' }, { revision: 43 }, { scope_filter: true }, { scope: {} },
      { known_until: '2026-10-10T01:00:00.000002+00:00' }, { occurred_until: '2026-10-10T00:00:00.000002+00:00' },
      { total: 202, claims_total: 202 }, { claims_total: 202 }, { semantic_graph_complete: false }, { projection: true }])
      expect(() => parseResearchPage({ ...page(100), ...patch }, reading.project_id, 100, first)).toThrow();
    expect(JSON.stringify(first)).toBe(original);
  });
  it('缺页、空进度、伪结束、重复ID与坏记录都拒绝，不能当空项目或完整图', () => {
    const first = parseResearchPage(page(), reading.project_id, 0); const records = appendResearchPage([], first);
    for (const patch of [{ offset: 101 }, { next_offset: 101 }, { next_offset: null }, { items: [] }, { items: [...page(100).items, claim(999)] }])
      expect(() => parseResearchPage({ ...page(100), ...patch }, reading.project_id, 100, first)).toThrow();
    const repeated = parseResearchPage({ ...page(100), items: page(100).items.map((item, index) => index ? item : claim(1)) }, reading.project_id, 100, first);
    expect(() => appendResearchPage(records, repeated)).toThrow(); expect(records).toHaveLength(100);
    expect(() => appendResearchPage([], first)).not.toThrow();
    expect(() => parseResearchRecord({ ...claim(1), replacement_ids: [2], replaced: false })).toThrow();
    expect(() => parseResearchRecord({ ...claim(1), payload: { claim_type: 'entity_version', label: {} } })).toThrow();
    expect(researchData(parseResearchPage(page(0, 0, 0), reading.project_id, 0), [], 'empty').partial).toBe(false);
  });
  it('异常方向与端口的旧记录保持原结构，解析器不把它们改为正常事实', () => {
    const input = { ...claim(1), claim_type: 'join_ports', payload: { claim_type: 'join_ports', target: 'not-a-join',
      semantics: 'all_required', selected: 'old-invalid-selected', inputs: [{ port: 'duplicate', ref: 'a' }, { port: 'duplicate', ref: 'b' }] } };
    const serialized = JSON.stringify(input); expect(parseResearchRecord(input)).toEqual(input);
    expect(JSON.stringify(input)).toBe(serialized);
  });
  it('历史详情沿原修订和双时间，不把错误ID或今天的审核回包当旧视图', () => {
    const value = { ...reading, history_context: true, claim: claim(1) };
    expect(parseResearchDetail(value, reading, 1).claim.claim_id).toBe(1);
    for (const patch of [{ project_id: 'other' }, { revision: 43 }, { history_context: false },
      { known_until: '2026-10-10T01:00:00.000002+00:00' }, { claim: claim(2) }])
      expect(() => parseResearchDetail({ ...value, ...patch }, reading, 1)).toThrow();
  });
  it('历史原文不接受未来上下文、错项目/事件或当前派生伪装', () => {
    const window = { event_id: 1, recorded_at: '2026-01-01T00:00:01Z', occurred_at: null,
      before: '', quote: '<script>原话</script>', after: '', total_bytes: 100, window_start: 0, window_end: 100 };
    const value = { ...reading, history_context: true, derived_context_loaded: false, event: window, before: [], after: [] } as unknown as EvidenceData;
    expect(() => validateResearchEvidence(value, { event_id: 1, reading })).not.toThrow();
    for (const patch of [{ revision: 43 }, { history_context: false }, { derived_context_loaded: true },
      { event: { ...window, event_id: 2 } }, { after: [{ ...window, event_id: 2, recorded_at: '2026-10-10T01:00:00.000002Z' }] }])
      expect(() => validateResearchEvidence({ ...value, ...patch } as EvidenceData, { event_id: 1, reading })).toThrow();
  });
});
