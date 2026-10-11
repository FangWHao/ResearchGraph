export interface FragmentAssignment {
  project_id: string | null; logical_session_pk: number | null; basis: string; partition_id: string | null;
}
export interface PhysicalFragment {
  first_event_id: number; byte_start: number; byte_end: number; object_sha256: string;
  first_seq: number; last_seq: number; event_count: number; has_user_turn: boolean;
  observation_state: string; observed_at: string | null;
  context: { state: string; source: string; cwd: string | null; cwd_sha256: string | null; reason: string; origin_event_id: number | null };
  project_hint: { state: string; basis: string; projects: string[]; matching_root_ids: string[]; candidates_partial: boolean; reason: string };
  assignment?: FragmentAssignment | null;
}
export interface FragmentPage {
  available: boolean; reason?: string; revision?: number; session_pk?: number; file_instance_id?: number;
  current_session_project?: string | null; assignments_applied?: boolean; assignment_supported?: boolean; unit?: string;
  snapshot?: number; observation_snapshot?: number; scope_key?: string; records_total?: number;
  records_observed?: number; records_unavailable?: number; observations_complete?: boolean;
  after_event?: number; next_after_event?: number | null; records?: PhysicalFragment[];
}
export interface FragmentAssignmentIntent {
  request_id: string; session: number; source: number; first_event: number; last_event: number;
  project_id: string | null; actor: string;
}
export interface FragmentReceipt {
  request_id: string; revision: number; replayed: boolean; assignments_applied: true;
  session_pk: number; file_instance_id: number; first_event_id: number; last_event_id: number;
  project_id: string | null; records_assigned: number; partitions_created: number;
}
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const integer = (value: unknown, min = 0): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= min;
const textOrNull = (value: unknown) => value === null || typeof value === 'string';
const hash = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const strings = (value: unknown) => Array.isArray(value) && value.every(x => typeof x === 'string');
function malformed(): never { throw new Error('片段响应格式或读取身份不一致，请重新读取第一页。'); }

export function parseFragmentPage(raw: unknown, expected: { session: number; source: number; after: number; limit: number }): FragmentPage {
  if (!object(raw) || typeof raw.available !== 'boolean') return malformed();
  if (!raw.available) {
    if (typeof raw.reason !== 'string') return malformed();
    return raw as unknown as FragmentPage;
  }
  if (raw.session_pk !== expected.session || raw.file_instance_id !== expected.source || raw.after_event !== expected.after || raw.unit !== 'physical_record' || !integer(raw.snapshot) || !integer(raw.observation_snapshot) || !hash(raw.scope_key) || !textOrNull(raw.current_session_project) || (raw.revision !== undefined && !integer(raw.revision)) || (raw.assignments_applied !== undefined && typeof raw.assignments_applied !== 'boolean') || !integer(raw.records_total) || !integer(raw.records_observed) || !integer(raw.records_unavailable) || raw.records_observed > raw.records_total || raw.records_unavailable > raw.records_observed || typeof raw.observations_complete !== 'boolean' || raw.observations_complete !== (raw.records_total === raw.records_observed) || !Array.isArray(raw.records) || raw.records.length > expected.limit || (raw.next_after_event !== null && !integer(raw.next_after_event, 1))) return malformed();
  let end = -1;
  const ids = new Set<number>();
  for (const r of raw.records) {
    if (!object(r) || !integer(r.first_event_id, 1) || r.first_event_id > raw.snapshot || ids.has(r.first_event_id) || !integer(r.byte_start) || !integer(r.byte_end, 1) || r.byte_start < end || r.byte_end <= r.byte_start || !hash(r.object_sha256) || !integer(r.first_seq) || !integer(r.last_seq) || r.last_seq < r.first_seq || !integer(r.event_count, 1) || typeof r.has_user_turn !== 'boolean' || typeof r.observation_state !== 'string' || !textOrNull(r.observed_at)) return malformed();
    const c = r.context, h = r.project_hint, a = r.assignment;
    if (!object(c) || typeof c.state !== 'string' || typeof c.source !== 'string' || !textOrNull(c.cwd) || !(c.cwd_sha256 === null || hash(c.cwd_sha256)) || typeof c.reason !== 'string' || !(c.origin_event_id === null || integer(c.origin_event_id, 1)) || !object(h) || typeof h.state !== 'string' || typeof h.basis !== 'string' || !strings(h.projects) || !strings(h.matching_root_ids) || typeof h.reason !== 'string' || typeof h.candidates_partial !== 'boolean') return malformed();
    if (a !== undefined && a !== null && (!object(a) || !textOrNull(a.project_id) || !(a.logical_session_pk === null || integer(a.logical_session_pk, 1)) || !textOrNull(a.partition_id) || typeof a.basis !== 'string')) return malformed();
    ids.add(r.first_event_id); end = r.byte_end;
  }
  if (raw.next_after_event !== null && (raw.records.length === 0 || raw.next_after_event !== raw.records.at(-1).first_event_id)) return malformed();
  return raw as unknown as FragmentPage;
}
export function assertFragmentContinuation(first: FragmentPage, next: FragmentPage) {
  for (const key of ['available', 'session_pk', 'file_instance_id', 'current_session_project', 'assignments_applied', 'assignment_supported', 'revision', 'snapshot', 'observation_snapshot', 'scope_key', 'records_total', 'records_observed', 'records_unavailable', 'observations_complete'] as const) {
    if (first[key] !== next[key]) throw new Error('片段分页的截止、归属或计数已变化，请重新读取第一页；未混合不同读取页。');
  }
}
export function selectedFragmentRange(records: PhysicalFragment[], selected: number[]) {
  if (!selected.length || new Set(selected).size !== selected.length) return null;
  const positions = selected.map(id => records.findIndex(r => r.first_event_id === id)).sort((a, b) => a - b);
  if (positions[0] < 0 || positions.some((p, i) => p !== positions[0] + i)) return null;
  const range = positions.map(p => records[p]);
  return { first: range[0], last: range.at(-1)!, records: range.length, events: range.reduce((n, r) => n + r.event_count, 0) };
}
export function fragmentSelection(records: PhysicalFragment[], anchor: number | null, clicked: number): number[] {
  const a = records.findIndex(r => r.first_event_id === anchor), b = records.findIndex(r => r.first_event_id === clicked);
  if (b < 0) return [];
  return a < 0 ? [clicked] : records.slice(Math.min(a, b), Math.max(a, b) + 1).map(r => r.first_event_id);
}
export function canAssignFragments(page: FragmentPage): boolean {
  return page.available && page.assignment_supported === true && integer(page.revision) && !!page.scope_key && !!page.records?.length && page.records.every(r => r.assignment != null);
}
export function parseFragmentReceipt(raw: unknown, intent: FragmentAssignmentIntent, records: number): FragmentReceipt {
  if (!object(raw) || raw.request_id !== intent.request_id || raw.session_pk !== intent.session || raw.file_instance_id !== intent.source || raw.first_event_id !== intent.first_event || raw.last_event_id !== intent.last_event || raw.project_id !== intent.project_id || !integer(raw.revision) || typeof raw.replayed !== 'boolean' || raw.assignments_applied !== true || raw.records_assigned !== records || !integer(raw.partitions_created)) throw new Error('归属回执与当前提交不一致，未更新页面；请重新读取核对。');
  return raw as unknown as FragmentReceipt;
}
export const contextNames: Record<string, string> = { declared: '原生目录声明', absent: '本记录没有目录声明', unobserved: '目录元数据尚未观测', invalid: '目录声明无效', unavailable: '目录元数据不可核对' };
export const hintNames: Record<string, string> = { candidate_path: '路径候选，尚非归属', ambiguous_path: '路径对应多个项目，归属不明', unassigned: '没有可用路径候选' };
export const directorySources: Record<string, string> = { codex_session: 'Codex 原生会话头', codex_turn: 'Codex 原生轮次上下文', claude_record: 'Claude 原生记录字段', none: '没有可用声明来源' };
export const hintReasons: Record<string, string> = { root_registry_limit: '根目录登记超出本次核对上限，不能判断路径候选', no_registered_path: '未匹配已登记根目录', registered_path_hint: '仅依据已登记路径产生候选', unobserved: '目录观测尚未记录，线索未知', invalid: '目录声明无效，不继续推断', unavailable: '目录元数据不可核对', absent: '没有可用的目录声明' };
