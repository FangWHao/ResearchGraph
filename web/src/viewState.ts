import { exactInstant } from './exactTime';
import { foldGroup, graphNodeId, projectGraph } from './model';
import type { PersonalView, PersonalViewRecord, ResearchGraphData, ResearchReading } from './types';

const invalid = () => new Error('个人视图格式、身份或阅读条件不一致，未恢复或覆盖当前图。');
function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function fields(value: unknown, keys: string[]): value is Record<string, unknown> { return object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key)); }
function integer(value: unknown, min = 0): value is number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= min; }
function coordinate(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 1e7; }
function text(value: unknown, max: number): value is string { return typeof value === 'string' && value.length > 0 && [...value].length <= max && !value.includes('\0'); }
export function personalIdentity(project: string, user: string): string { return JSON.stringify([project, user.trim()]); }
export function parsePersonalView(value: unknown, project: string): PersonalView {
  if (!fields(value, ['format_version', 'reading', 'show_candidates', 'positions', 'viewport', 'selected_versions', 'process_group'])
    || value.format_version !== 1 || typeof value.show_candidates !== 'boolean'
    || !fields(value.reading, ['project_id', 'revision', 'occurred_until', 'known_until'])
    || value.reading.project_id !== project || !integer(value.reading.revision)
    || exactInstant(value.reading.occurred_until) === null || exactInstant(value.reading.known_until) === null
    || !fields(value.viewport, ['x', 'y', 'zoom']) || !Object.values(value.viewport).every(coordinate)
    || (value.viewport.zoom as number) < 0.12 || (value.viewport.zoom as number) > 1.5
    || !object(value.positions) || !object(value.selected_versions)) throw invalid();
  for (const [id, position] of Object.entries(value.positions)) {
    if (!text(id, 4096) || !fields(position, ['x', 'y']) || !Object.values(position).every(coordinate)) throw invalid();
  }
  for (const [id, version] of Object.entries(value.selected_versions)) if (!text(id, 4096) || !integer(version, 1)) throw invalid();
  const group = value.process_group;
  if (group !== null && (!fields(group, ['name', 'members']) || !text(group.name, 60) || !group.name.trim()
    || !Array.isArray(group.members) || group.members.length < 2 || group.members.some(member => !text(member, 4096))
    || new Set(group.members).size !== group.members.length || !value.show_candidates)) throw invalid();
  return value as unknown as PersonalView;
}
export function parsePersonalRecord(value: unknown, project: string, user: string): PersonalViewRecord {
  if (!fields(value, ['project_id', 'user', 'view_id', 'saved_at', 'current_revision', 'view', 'unavailable_reason'])
    || value.project_id !== project || value.user !== user.trim() || !integer(value.current_revision)
    || !(value.view_id === null || integer(value.view_id, 1))
    || !(value.saved_at === null || typeof value.saved_at === 'string' && exactInstant(value.saved_at) !== null)
    || !(value.unavailable_reason === null || typeof value.unavailable_reason === 'string')
    || (value.view_id === null) !== (value.saved_at === null)
    || value.view_id === null && (value.view !== null || value.unavailable_reason !== null)
    || value.view_id !== null && value.view === null && !value.unavailable_reason
    || value.view !== null && value.unavailable_reason !== null) throw invalid();
  if (value.view !== null) parsePersonalView(value.view, project);
  return value as unknown as PersonalViewRecord;
}
export function samePersonalReading(a: ResearchReading, b: ResearchReading): boolean {
  if ([a.occurred_until, a.known_until, b.occurred_until, b.known_until].some(value => exactInstant(value) === null)) return false;
  return a.project_id === b.project_id && a.revision === b.revision
    && exactInstant(a.occurred_until) === exactInstant(b.occurred_until)
    && exactInstant(a.known_until) === exactInstant(b.known_until);
}
export function resolvePersonalView(view: PersonalView, data: ResearchGraphData) {
  parsePersonalView(view, data.project_id);
  if (data.partial || data.claims.length !== data.total || !samePersonalReading(view.reading, data)) throw new Error('个人视图与完整研究图的修订或双截止不符，不能直接恢复。');
  const graph = projectGraph(data.claims, view.show_candidates);
  if (graph.diagnostics.length) throw new Error('研究图有未解析或异常关系，不能保存或恢复个人布局。');
  const nodes = new Set(graph.entities.map(graphNodeId));
  if (Object.keys(view.positions).length !== nodes.size || Object.keys(view.positions).some(id => !nodes.has(id))) throw new Error('个人视图缺少完整节点位置或包含不可见节点，未恢复。');
  for (const [id, version] of Object.entries(view.selected_versions)) {
    if (!graph.versions.get(id)?.some(record => record.claim_id === version)) throw new Error('显示版本不是当前项目、范围的有效记录，未恢复。');
  }
  const fold = view.process_group ? foldGroup(view.process_group.members, graph.edges, data.claims, view.show_candidates) : null;
  return { graph, fold };
}
export function personalSaveBody(project: string, user: string, expected: number | null, view: PersonalView) {
  if (!text(user.trim(), 100) || !(expected === null || integer(expected, 1))) throw new Error('请填写有效的本机个人视图标识并读取保存记录。');
  parsePersonalView(view, project);
  const body = { project_id: project, user: user.trim(), expected_view_id: expected, view };
  if (new TextEncoder().encode(JSON.stringify(body)).byteLength > 65536) throw new Error('个人视图请求超过65536字节，未保存或截断；此完整布局不能在当前上限内保存。');
  return body;
}
export function viewFingerprint(value: unknown): string {
  function ordered(item: unknown): unknown { return Array.isArray(item) ? item.map(ordered) : object(item) ? Object.fromEntries(Object.keys(item).sort().map(key => [key, ordered(item[key])])) : item; }
  return JSON.stringify(ordered(value));
}
