import { describe, expect, it } from 'vitest';
import { appendL1Page, drawableL1Edges, emptyFileRunDraft, emptyL1Pages, fileRunData, fileRunIntent, fileRunRequest, fixedL1Query, l1NodeTitle, metadataKey, nativeState, nodeEvidence, parseL1Page, parseL1Window } from '../src/fileRunGraph';
import type { L1Edge, L1Metadata, L1Node } from '../src/fileRunGraph';

const metadata: L1Metadata = { layer: 'L1', projection: false, project_id: 'p', revision: 8, occurred_until: '2026-10-09T09:01:00+00:00', known_until: '2026-10-10T00:00:00+00:00', scope_filter: false, scope: null, counts: { nodes: 1, edges: 0, observations: 0, evidence: 0, unresolved: 0 }, node_kinds: { artifact_version: 1 }, l1_graph_complete: true, l1_dependencies_complete: false, actual_io_completeness: 'unknown', report_is_execution_fact: false };
const version: L1Node = { node_id: 'v', kind: 'artifact_version', scope: null, record: { version_id: 'version1', claim_state: 'candidate', path: '<script>never_execute</script>' } };
const manifest: L1Node = { node_id: 'm', kind: 'run_manifest', scope: null, record: { request_id: 'manifest1', claim_state: 'candidate' } };
const native: L1Node = { node_id: 'r', kind: 'native_run', scope: null, record: { run_id: 'run1', state: 'requested', exit_code: null } };
const edge: L1Edge = { edge_id: 'e', relation: 'consumes', source: { node_id: 'v', kind: 'artifact_version', record_id: 'version1', resolved: true }, target: { node_id: 'm', kind: 'run_manifest', record_id: 'manifest1', resolved: true }, resolved: true, basis: 'direct_record', claim_state: 'candidate', association_state: 'reported_only', evidence_event_ids: [12] };
function page(value = {}) { return { ...metadata, collection: 'nodes', offset: 0, total: 1, next_offset: null, items: [version], ...value }; }
describe('完整 L1 图的历史读取与证据边界', () => {
  it('所有、完整和明确未知范围分别编码，时区与无效日期不能悄悄变成当前查询', () => {
    const draft = emptyFileRunDraft(); expect(fileRunRequest('p', draft)).toEqual({ project: 'p' });
    expect(fileRunRequest('p', { ...draft, scopeMode: 'unknown' }).scope).toBe('null');
    expect(JSON.parse(fileRunRequest('p', { ...draft, scopeMode: 'exact', scopeText: 'data=v1\nstep=结果' }).scope)).toEqual({ data: 'v1', step: '结果' });
    for (const value of ['2026-10-10T12:00', '2026-02-30T12:00:00Z', '2026-10-10T25:00:00Z']) expect(() => fileRunRequest('p', { ...draft, occurredUntil: value })).toThrow();
  });
  it('首响应固定修订和双截止，后续页的任一读取条件、总数或类型变化均拒绝', () => {
    const first = parseL1Page(page(), 'p', 'nodes', 0);
    expect(fixedL1Query(first)).toMatchObject({ expected_revision: 8, occurred_until: metadata.occurred_until, known_until: metadata.known_until });
    for (const change of [{ revision: 9 }, { known_until: '2026-10-11T00:00:00Z' }, { scope_filter: true }, { counts: { ...metadata.counts, evidence: 1 } }, { node_kinds: { artifact_version: 2 } }]) expect(() => parseL1Page(page(change), 'p', 'nodes', 0, first)).toThrow();
    expect(() => parseL1Page(page({ projection: true }), 'p', 'nodes', 0)).toThrow();
    expect(() => parseL1Page(page({ project_id: 'q' }), 'p', 'nodes', 0)).toThrow();
    expect(() => parseL1Page(page({ collection: 'edges' }), 'p', 'nodes', 0)).toThrow();
    expect(metadataKey({ ...metadata, node_kinds: { b: 1, a: 2 } })).toBe(metadataKey({ ...metadata, node_kinds: { a: 2, b: 1 } }));
  });
  it('超过 2000 节点仍可按全部页收集，重复或跳页拒绝，未读完绝不标完整', () => {
    const fixed = { ...metadata, counts: { ...metadata.counts, nodes: 2101 }, node_kinds: { artifact_version: 2101 } }; const pages = emptyL1Pages();
    for (let offset = 0; offset < 2101; offset += 100) {
      const items = Array.from({ length: Math.min(100, 2101 - offset) }, (_, i) => ({ ...version, node_id: `v${offset + i}` }));
      const received = parseL1Page({ ...fixed, collection: 'nodes', offset, total: 2101, next_offset: offset + items.length < 2101 ? offset + items.length : null, items }, 'p', 'nodes', offset, fixed);
      appendL1Page(pages, received);
      if (offset === 0) expect(fileRunData(fixed, pages, 'intent', true).loadedComplete).toBe(false);
    }
    expect(fileRunData(fixed, pages, 'intent', true).nodes).toHaveLength(2101);
    expect(fileRunData(fixed, pages, 'intent', true).loadedComplete).toBe(true);
    expect(() => appendL1Page(pages, parseL1Page(page(), 'p', 'nodes', 0))).toThrow();
    expect(() => parseL1Page(page({ next_offset: 0 }), 'p', 'nodes', 0)).toThrow();
  });
  it('跨表相同观察编号各自保留，重复同一观察拒绝', () => {
    const pages = emptyL1Pages(); const fixed = { ...metadata, counts: { ...metadata.counts, observations: 2 } };
    appendL1Page(pages, parseL1Page({ ...fixed, collection: 'observations', offset: 0, total: 2, next_offset: null, items: [{ observation_id: 'same', owner_node_id: 'v', observation_kind: 'artifact' }, { observation_id: 'same', owner_node_id: 'r', observation_kind: 'execution' }] }, 'p', 'observations', 0));
    expect(pages.observations).toHaveLength(2);
    const duplicate = emptyL1Pages(); expect(() => appendL1Page(duplicate, parseL1Page({ ...fixed, collection: 'observations', offset: 0, total: 2, next_offset: null, items: [pages.observations[0], pages.observations[0]] }, 'p', 'observations', 0))).toThrow();
  });
  it('报告输入输出只连报告或编辑，错误原生关联和未知端点保留诊断而不补造节点', () => {
    const pages = emptyL1Pages(); pages.nodes = [version, manifest, native] as unknown as Record<string, unknown>[]; pages.edges = [edge];
    const data = fileRunData(metadata, pages, 'i', false);
    expect(drawableL1Edges(data).edges).toEqual([edge]);
    const invalid = { ...edge, edge_id: 'invalid', target: { ...edge.target, node_id: 'r', kind: 'native_run' } };
    const wrongKind = { ...edge, edge_id: 'wrong-kind', target: { ...edge.target, node_id: 'r' } };
    const missing = { ...edge, edge_id: 'missing', source: { ...edge.source, node_id: 'unknown', resolved: false }, resolved: false };
    expect(drawableL1Edges({ ...data, edges: [edge, invalid, missing, wrongKind] }).omitted).toEqual([invalid, missing, wrongKind]);
    expect(data.nodes).toHaveLength(3); expect(data.nodes[1].record.claim_state).toBe('candidate');
  });
  it('没有结束观察保持未知或已请求，清单退出码不改写原生状态', () => {
    expect(nativeState({ state: 'requested', exit_code: null, reported: { exit_code: 0 } })).toContain('无结束证明');
    expect(nativeState({ state: 'unknown', exit_code: null, reported: { exit_code: 0 } })).toBe('运行状态未知');
    expect(nativeState({ state: 'exited', exit_code: 3, reported: { exit_code: 0 } })).toBe('已结束 · 退出码 3');
  });
  it('尝试保留全部内容版本，名称不偷偷选择最大 claim ID，正文仍为文字', () => {
    const attempt: L1Node = { node_id: 'a', kind: 'attempt', scope: { data: 'v1' }, record: { entity_id: 'attempt1', versions: [{ claim_id: 1, payload: { label: '甲' } }, { claim_id: 999, payload: { label: '乙' } }] } };
    expect(l1NodeTitle(attempt)).toContain('2 个内容版本'); expect(l1NodeTitle(attempt)).not.toContain('乙');
    expect(l1NodeTitle(version)).toBe('<script>never_execute</script>');
  });
  it('原文固定项目/修订/双时间并核对 UTF8 字节连续，错窗口不拼接', () => {
    const value = { project_id: 'p', revision: 8, occurred_until: metadata.occurred_until, known_until: metadata.known_until, event_id: 12, text: '中文', window_start: 0, window_end: 6, total_bytes: 12, next_byte_offset: 6, window_sha256: 'a'.repeat(64) };
    expect(parseL1Window(value, metadata, 12, 0).text).toBe('中文');
    for (const change of [{ revision: 9 }, { event_id: 13 }, { known_until: '2026-10-11T00:00:00Z' }, { window_end: 2 }, { next_byte_offset: 5 }, { project_id: 'q' }]) expect(() => parseL1Window({ ...value, ...change }, metadata, 12, 0)).toThrow();
    expect(fileRunIntent('p', emptyFileRunDraft())).not.toBe(fileRunIntent('q', emptyFileRunDraft()));
  });
  it('节点证据取自身、全部内部关系和观察，截止外引用不借来补齐', () => {
    const pages = emptyL1Pages(); pages.nodes = [manifest] as unknown as Record<string, unknown>[]; pages.edges = [edge]; pages.evidence = [{ event_id: 12 }]; pages.observations = [{ event_id: 99, owner_node_id: 'm' }];
    const data = fileRunData(metadata, pages, 'i', false); expect(nodeEvidence(data, manifest)).toEqual([12]);
  });
});
