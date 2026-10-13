import { describe, expect, it } from 'vitest';
import { appendImpactPage, impactRequest, impactTargetKey, parseImpactPage } from '../src/impact';
import type { ImpactTarget } from '../src/impact';

const target: ImpactTarget = { project: 'synthetic-impact-project', revision: 42, entityId: 'root', scope: { data: 'v1' } };
const options = { candidates: false, reported: false };
const reading = { project_id: target.project, revision: 42, occurred_until: '2026-10-10T00:00:00.000001+00:00', known_until: '2026-10-10T01:00:00.000001+00:00' };
function page(offset = 0, total = 21) {
  return { ...reading, scope: target.scope, scope_filter: true,
    seed: { node_id: 'root::v1', layer: 'L2', kind: 'finding', entity_id: 'root', scope: target.scope, content_claim_ids: [1], labels: ['合成来源'] },
    collection: 'affected', total, offset, next_offset: offset + 20 < total ? offset + 20 : null,
    affected_total: total, paths_total: 21, traversal_complete: true, max_depth: 32,
    include_candidates: false, include_reported: false, state_changes: false,
    actual_io_completeness: 'unknown', l1_dependencies_complete: false, semantics: 'downstream_review_only',
    items: Array.from({ length: Math.min(20, total - offset) }, (_, i) => ({ node_id: `downstream-${offset + i}`, layer: 'L2', kind: 'finding', entity_id: `downstream-${offset + i}`, scope: target.scope, content_claim_ids: [offset + i + 2], labels: ['合成下游'], depth: 1, impact_basis: 'confirmed_relation', action: 'review_dependency' })) };
}
describe('下游影响固定读取与只读语义', () => {
  it('请求保留完整范围、修订、微秒截止与明确的潜在报告开关', () => {
    expect(impactRequest(target, options, 'affected', 20, reading)).toMatchObject({ project: target.project, entity_id: 'root', expected_revision: 42, scope: '{"data":"v1"}', occurred_until: reading.occurred_until, known_until: reading.known_until, include_candidates: 'false', include_reported: 'false', limit: 20, offset: 20 });
    expect(impactRequest({ project: target.project, revision: 42, versionId: 'file-v1' }, { candidates: false, reported: true }, 'paths')).toMatchObject({ version_id: 'file-v1', include_reported: 'true', collection: 'paths' });
    expect(impactTargetKey(target)).not.toBe(impactTargetKey({ ...target, project: 'other' }));
    expect(impactTargetKey(target)).not.toBe(impactTargetKey({ ...target, entityId: 'other' }));
  });
  it('拒绝跨对象、项目、范围、时间、修订及不安全科学状态响应', () => {
    const value = page();
    const first = parseImpactPage(value, target, options, 'affected');
    for (const patch of [{ project_id: 'other' }, { revision: 43 }, { scope: { data: 'v2' } }, { scope_filter: false }, { state_changes: true }, { actual_io_completeness: 'complete' }, { semantics: 'withdraw_all' }, { include_reported: true }, { seed: { ...value.seed, entity_id: 'other' } }, { next_offset: 0 }, { items: value.items.slice(1) }])
      expect(() => parseImpactPage({ ...value, ...patch }, target, options, 'affected')).toThrow();
    expect(() => parseImpactPage({ ...value, known_until: '2026-10-10T01:00:00.000002Z' }, target, options, 'affected', 0, first)).toThrow();
    expect(() => parseImpactPage({ ...value, items: value.items.map(item => ({ ...item, action: 'withdraw' })) }, target, options, 'affected')).toThrow();
  });
  it('分页保留第一时刻，拒绝重复节点或改变总数的后续页', () => {
    const first = parseImpactPage(page(), target, options, 'affected');
    const next = parseImpactPage(page(20), target, options, 'affected', 20, first);
    expect(appendImpactPage(first, next).items).toHaveLength(21);
    expect(() => appendImpactPage(first, { ...next, affected_total: 22 })).toThrow();
    expect(() => appendImpactPage(first, { ...next, items: [first.items[0]] })).toThrow();
    expect(() => appendImpactPage(first, { ...next, known_until: '2026-10-10T01:00:00.000002Z' })).toThrow();
  });
});
