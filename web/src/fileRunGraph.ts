import { parseScopeText } from './manualQuestion';

export const l1Collections = ['nodes', 'edges', 'observations', 'evidence', 'unresolved'] as const;
export type L1Collection = typeof l1Collections[number];
export type L1Record = Record<string, unknown>;
export interface L1Node { node_id: string; kind: string; scope: Record<string, string> | null; record: L1Record }
export interface L1Endpoint { node_id: string | null; kind: string; record_id: string | number | null; resolved: boolean }
export interface L1Edge extends L1Record { edge_id: string; relation: string; source: L1Endpoint; target: L1Endpoint; resolved: boolean; evidence_event_ids: number[] }
export interface L1Metadata {
  layer: 'L1'; projection: false;
  project_id: string; revision: number; occurred_until: string; known_until: string;
  scope: Record<string, string> | null; scope_filter: boolean; counts: Record<L1Collection, number>;
  node_kinds: Record<string, number>; l1_graph_complete: boolean; l1_dependencies_complete: boolean;
  actual_io_completeness: string; report_is_execution_fact: boolean; unknown_recorded_time_excluded?: number;
}
export interface L1Page extends L1Metadata { collection: L1Collection; items: L1Record[]; total: number; offset: number; next_offset: number | null }
export interface FileRunData extends L1Metadata { nodes: L1Node[]; edges: L1Edge[]; observations: L1Record[]; evidence: L1Record[]; unresolved: L1Record[]; loadedComplete: boolean; intent: string }
export interface FileRunDraft { occurredUntil: string; knownUntil: string; scopeMode: 'all' | 'exact' | 'unknown'; scopeText: string }
export interface L1EvidenceWindow extends Pick<L1Metadata, 'project_id' | 'revision' | 'occurred_until' | 'known_until'> { event_id: number; text: string; window_start: number; window_end: number; window_sha256: string; total_bytes: number; next_byte_offset: number | null; source_byte_start: number | null; source_byte_end: number | null; occurred_at: string | null; recorded_at: string | null }
export function emptyFileRunDraft(): FileRunDraft { return { occurredUntil: '', knownUntil: '', scopeMode: 'all', scopeText: '' }; }
export function fileRunIntent(project: string, draft: FileRunDraft): string { return JSON.stringify([project, draft.occurredUntil, draft.knownUntil, draft.scopeMode, draft.scopeText]); }
function cutoff(text: string, name: string): string | undefined {
  if (!text.trim()) return undefined;
  const value = text.trim(); const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  const fail = () => new Error(`${name}需填写有效且带时区的 ISO 时间。`);
  if (!match || !Number.isFinite(Date.parse(value))) throw fail();
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
  const days = [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1] || Number(match[4]) > 23 || Number(match[5]) > 59 || Number(match[6] ?? 0) > 59 || Number(match[8] ?? 0) > 23 || Number(match[9] ?? 0) > 59) throw fail();
  return value;
}
export function fileRunRequest(project: string, draft: FileRunDraft): Record<string, string> {
  if (!project) throw new Error('请先选择项目。');
  if (!['all', 'exact', 'unknown'].includes(draft.scopeMode)) throw new Error('阅读范围无效。');
  const occurred = cutoff(draft.occurredUntil, '发生截止'), known = cutoff(draft.knownUntil, '获知截止');
  return { project, ...(occurred ? { occurred_until: occurred } : {}), ...(known ? { known_until: known } : {}),
    ...(draft.scopeMode === 'exact' ? { scope: JSON.stringify(parseScopeText(draft.scopeText, false)) } : draft.scopeMode === 'unknown' ? { scope: 'null' } : {}) };
}
export function object(value: unknown): value is L1Record { return value != null && typeof value === 'object' && !Array.isArray(value); }
function integer(value: unknown): value is number { return Number.isSafeInteger(value) && Number(value) >= 0; }
function scope(value: unknown): boolean { return value === null || object(value) && Object.values(value).every(v => typeof v === 'string'); }
function endpoint(value: unknown): boolean { return object(value) && (value.node_id === null || typeof value.node_id === 'string') && typeof value.kind === 'string' && typeof value.resolved === 'boolean' && (value.record_id === null || typeof value.record_id === 'string' || integer(value.record_id)); }
function canonical(value: unknown): unknown { return Array.isArray(value) ? value.map(canonical) : object(value) ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value; }
export function metadataKey(value: L1Metadata): string {
  return JSON.stringify(canonical([value.layer, value.projection, value.project_id, value.revision, value.occurred_until, value.known_until, value.scope_filter, value.scope, value.counts, value.node_kinds, value.l1_graph_complete, value.l1_dependencies_complete, value.actual_io_completeness, value.report_is_execution_fact, value.unknown_recorded_time_excluded]));
}
export function parseL1Page(value: unknown, project: string, collection: L1Collection, offset: number, fixed?: L1Metadata): L1Page {
  const fail = () => new Error('文件运行图分页响应不一致或格式损坏，未混入旧图；请主动重新读取。');
  if (!object(value) || value.layer !== 'L1' || value.projection !== false || value.project_id !== project || !integer(value.revision) || typeof value.occurred_until !== 'string'
    || typeof value.known_until !== 'string' || !Number.isFinite(Date.parse(value.occurred_until)) || !Number.isFinite(Date.parse(value.known_until))
    || typeof value.scope_filter !== 'boolean' || !scope(value.scope) || !object(value.counts) || !object(value.node_kinds)
    || l1Collections.some(key => !integer((value.counts as L1Record)[key])) || Object.values(value.node_kinds).some(n => !integer(n))
    || value.collection !== collection || value.offset !== offset || !integer(value.total) || value.total !== value.counts[collection]
    || !Array.isArray(value.items) || value.items.length > 100 || offset + value.items.length > value.total
    || !(value.next_offset === null ? offset + value.items.length === value.total : integer(value.next_offset) && value.next_offset === offset + value.items.length && value.next_offset < value.total && value.items.length > 0)
    || typeof value.l1_graph_complete !== 'boolean' || typeof value.l1_dependencies_complete !== 'boolean' || typeof value.report_is_execution_fact !== 'boolean' || typeof value.actual_io_completeness !== 'string') throw fail();
  for (const item of value.items) {
    if (!object(item)) throw fail();
    if (collection === 'nodes' && (typeof item.node_id !== 'string' || !item.node_id || typeof item.kind !== 'string' || !scope(item.scope) || !object(item.record))) throw fail();
    if (collection === 'edges' && (typeof item.edge_id !== 'string' || typeof item.relation !== 'string' || !endpoint(item.source) || !endpoint(item.target) || typeof item.resolved !== 'boolean' || !Array.isArray(item.evidence_event_ids) || item.evidence_event_ids.some(id => !integer(id) || id === 0))) throw fail();
    if (collection === 'observations' && (typeof item.observation_id !== 'string' || typeof item.owner_node_id !== 'string' || typeof item.observation_kind !== 'string')) throw fail();
    if (collection === 'evidence' && (!integer(item.event_id) || item.event_id === 0)) throw fail();
    if (collection === 'unresolved' && typeof item.edge_id !== 'string') throw fail();
  }
  const page = value as unknown as L1Page;
  if (fixed && metadataKey(page) !== metadataKey(fixed)) throw fail();
  return page;
}
export function pageIdentity(collection: L1Collection, record: L1Record): string { return collection === 'observations' ? JSON.stringify([record.observation_kind, record.owner_node_id, record.observation_id]) : String(record[collection === 'nodes' ? 'node_id' : collection === 'edges' || collection === 'unresolved' ? 'edge_id' : 'event_id']); }
export function appendL1Page(pages: Record<L1Collection, L1Record[]>, page: L1Page) {
  const items = pages[page.collection]; if (items.length !== page.offset) throw new Error('图分页偏移不连续，未合并。');
  const ids = new Set(items.map(item => pageIdentity(page.collection, item)));
  for (const item of page.items) { const id = pageIdentity(page.collection, item); if (ids.has(id)) throw new Error('图分页重复记录，未合并。'); ids.add(id); }
  pages[page.collection] = [...items, ...page.items];
}
export function emptyL1Pages(): Record<L1Collection, L1Record[]> { return { nodes: [], edges: [], observations: [], evidence: [], unresolved: [] }; }
export function fileRunData(metadata: L1Metadata, pages: Record<L1Collection, L1Record[]>, intent: string, complete: boolean): FileRunData {
  return { ...metadata, ...pages, nodes: pages.nodes as unknown as L1Node[], edges: pages.edges as unknown as L1Edge[], intent, loadedComplete: complete && l1Collections.every(key => pages[key].length === metadata.counts[key]) };
}
export function fixedL1Query(metadata: L1Metadata): Record<string, string | number> { return { project: metadata.project_id, expected_revision: metadata.revision, occurred_until: metadata.occurred_until, known_until: metadata.known_until, ...(metadata.scope_filter ? { scope: JSON.stringify(metadata.scope) } : {}) }; }
export function parseL1Window(value: unknown, metadata: L1Metadata, event: number, offset: number): L1EvidenceWindow {
  if (!object(value) || value.project_id !== metadata.project_id || value.revision !== metadata.revision || value.occurred_until !== metadata.occurred_until || value.known_until !== metadata.known_until
    || value.event_id !== event || value.window_start !== offset || !integer(value.window_end) || value.window_end < offset || !integer(value.total_bytes) || value.window_end > value.total_bytes || typeof value.text !== 'string'
    || new TextEncoder().encode(value.text).length !== value.window_end - offset || typeof value.window_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.window_sha256)
    || !(value.next_byte_offset === null ? value.window_end === value.total_bytes : value.next_byte_offset === value.window_end && value.window_end > offset && value.window_end < value.total_bytes)) throw new Error('原文窗口的项目、时间、修订或字节位置不一致，未拼接此回包。');
  return value as unknown as L1EvidenceWindow;
}
export const l1KindNames: Record<string, string> = { artifact_version: '文件版本', native_run: '原生运行', run_manifest: '候选运行清单', edit_record: '工具编辑记录', workspace_snapshot: '工作区快照', attempt: '研究尝试' };
export const l1RoleNames: Record<string, string> = { inputs: '输入', scripts: '脚本', patches: '补丁', environment: '环境', outputs: '输出', before_version: '编辑前版本', after_version: '编辑后版本' };
export const l1RelationNames: Record<string, string> = { consumes: '声明输入', produces: '声明输出', describes_run: '描述运行', describes_attempt: '描述尝试', declares_snapshot: '声明快照', captured_in: '保存于快照', discovered_from: '发现线索' };
export function l1NodeTitle(node: L1Node): string {
  const r = node.record;
  if (node.kind === 'artifact_version') return String(r.path ?? r.version_id ?? '路径未知');
  if (node.kind === 'native_run') return String(r.command || r.call_id || r.run_id || '命令未知');
  if (node.kind === 'run_manifest') return `清单 ${String(r.request_id ?? '').slice(0, 12)}`;
  if (node.kind === 'attempt') return `尝试 ${String(r.entity_id ?? '')} · ${Array.isArray(r.versions) ? r.versions.length : '未知'} 个内容版本`;
  if (node.kind === 'workspace_snapshot') return `快照 #${r.snapshot_id ?? '未知'}`;
  return `编辑 #${r.edit_id ?? '未知'} · ${r.path ?? '路径未知'}`;
}
export function nativeState(record: L1Record): string { return record.state === 'exited' ? `已结束 · 退出码 ${record.exit_code ?? '未知'}` : ({ requested: '已请求 · 无结束证明', started: '已开始 · 无结束证明', unknown: '运行状态未知' } as Record<string, string>)[String(record.state)] ?? '运行状态未知'; }
export function drawableL1Edges(data: FileRunData): { edges: L1Edge[]; omitted: L1Edge[] } {
  const nodes = new Map(data.nodes.map(node => [node.node_id, node])); const edges: L1Edge[] = [], omitted: L1Edge[] = [];
  for (const edge of data.edges) {
    const io = ['consumes', 'produces'].includes(edge.relation);
    const validIo = !io || (edge.relation === 'consumes' ? edge.source.kind === 'artifact_version' && ['run_manifest', 'edit_record'].includes(edge.target.kind) : ['run_manifest', 'edit_record'].includes(edge.source.kind) && edge.target.kind === 'artifact_version');
    (edge.resolved && edge.source.resolved && edge.target.resolved && edge.source.node_id && edge.target.node_id
      && nodes.get(edge.source.node_id)?.kind === edge.source.kind && nodes.get(edge.target.node_id)?.kind === edge.target.kind && validIo ? edges : omitted).push(edge);
  }
  return { edges, omitted };
}
export function nodeEvidence(data: FileRunData, node: L1Node): number[] {
  const ids = new Set<number>(); const add = (value: unknown) => { if (integer(value) && value > 0) ids.add(value); };
  for (const name of ['evidence_event_id', 'request_event_id', 'result_event_id']) add(node.record[name]);
  for (const edge of data.edges.filter(e => e.source.node_id === node.node_id || e.target.node_id === node.node_id)) edge.evidence_event_ids.forEach(add);
  for (const item of data.observations.filter(o => o.owner_node_id === node.node_id)) add(item.event_id);
  if (Array.isArray(node.record.versions)) for (const version of node.record.versions) if (object(version) && Array.isArray(version.evidence)) for (const span of version.evidence) if (object(span)) add(span.event_id);
  return [...ids].filter(id => data.evidence.some(item => item.event_id === id));
}
