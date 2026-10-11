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
  privacy_skipped_records?: number; privacy_skipped_bytes?: number;
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
export interface FragmentDependencyRecord {
  first_event_id: number; session_pk: number; file_instance_id: number;
  byte_start: number; byte_end: number; project_id: string | null;
}
export interface FragmentDependencies {
  revision: number; scope_key: string; snapshot: number; observation_snapshot: number;
  plan_sha256: string; complete: boolean; closure_closed: boolean; assignment_available: boolean;
  write_mode: 'direct' | 'dependency_migration' | 'unverifiable'; selected_records: number;
  dependencies: Record<string, number>; shared_context: Record<string, number>;
  required_records: FragmentDependencyRecord[]; required_records_total: number;
  next_offset: number | null; issues: string[]; migration_applied: false;
}
export interface FragmentMigrationIntent extends FragmentAssignmentIntent {
  project_id: string; expected_plan_sha256: string; include_required_records: true;
}
export interface FragmentMigrationReceipt {
  request_id: string; revision: number; replayed: boolean; migration_applied: true; assignments_applied: true;
  project_id: string; session_pk: number; file_instance_id: number; first_event_id: number; last_event_id: number;
  plan_sha256: string; records_assigned: number; selected_records: number; required_records_included: number;
  partitions_created: number; dependencies_migrated: Record<string, number>; model_calls: 0; scientific_records_rewritten: 0;
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
  for (const key of ['privacy_skipped_records', 'privacy_skipped_bytes']) {
    if (raw[key] !== undefined && !integer(raw[key])) return malformed();
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
  for (const key of ['available', 'session_pk', 'file_instance_id', 'current_session_project', 'assignments_applied', 'assignment_supported', 'revision', 'snapshot', 'observation_snapshot', 'scope_key', 'records_total', 'records_observed', 'records_unavailable', 'observations_complete', 'privacy_skipped_records', 'privacy_skipped_bytes'] as const) {
    if (first[key] !== next[key]) throw new Error('片段分页的截止、归属或计数已变化，请重新读取第一页；未混合不同读取页。');
  }
}
export function selectedFragmentRange(records: PhysicalFragment[], selected: number[]) {
  if (!selected.length || new Set(selected).size !== selected.length) return null;
  const positions = selected.map(id => records.findIndex(r => r.first_event_id === id)).sort((a, b) => a - b);
  if (positions[0] < 0 || positions.some((p, i) => p !== positions[0] + i)) return null;
  const range = positions.map(p => records[p]);
  if (range.some((record, i) => i > 0 && range[i - 1].byte_end !== record.byte_start)) return null;
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
const counts = (value: unknown): value is Record<string, number> => object(value) && Object.values(value).every(n => integer(n));
export function parseFragmentDependencies(raw: unknown, page: FragmentPage, selected: number, offset: number, limit: number): FragmentDependencies {
  if (!object(raw) || !integer(raw.revision) || raw.revision !== page.revision || raw.scope_key !== page.scope_key || raw.snapshot !== page.snapshot || raw.observation_snapshot !== page.observation_snapshot || !hash(raw.plan_sha256) || typeof raw.complete !== 'boolean' || typeof raw.closure_closed !== 'boolean' || typeof raw.assignment_available !== 'boolean' || !['direct', 'dependency_migration', 'unverifiable'].includes(String(raw.write_mode)) || raw.selected_records !== selected || !counts(raw.dependencies) || !counts(raw.shared_context) || !integer(raw.required_records_total) || !Array.isArray(raw.required_records) || !strings(raw.issues) || raw.migration_applied !== false || offset > raw.required_records_total) throw new Error('科研依赖预检响应或读取条件不一致，未采用此结果，请重新读取片段第一页。');
  if (raw.complete !== (raw.issues.length === 0) || raw.closure_closed !== (raw.complete && raw.required_records_total === 0) || raw.assignment_available !== (raw.write_mode === 'direct') || (!raw.complete && raw.write_mode !== 'unverifiable') || (raw.complete && raw.write_mode === 'unverifiable') || raw.required_records.length !== Math.min(limit, raw.required_records_total - offset) || raw.next_offset !== (offset + raw.required_records.length < raw.required_records_total ? offset + raw.required_records.length : null)) throw new Error('科研依赖预检的完整性或分页矛盾，未将其视为可保存。');
  const ids = new Set<number>(); let previous: FragmentDependencyRecord | null = null;
  for (const r of raw.required_records) {
    if (!object(r) || !integer(r.first_event_id, 1) || !integer(r.session_pk, 1) || !integer(r.file_instance_id, 1) || !integer(r.byte_start) || !integer(r.byte_end, 1) || r.byte_end <= r.byte_start || !textOrNull(r.project_id) || ids.has(r.first_event_id) || (previous && (r.file_instance_id < previous.file_instance_id || (r.file_instance_id === previous.file_instance_id && r.byte_start < previous.byte_end)))) throw new Error('片段外必需记录的完整边界或身份异常，未混入本次预检。');
    ids.add(r.first_event_id); previous = r as unknown as FragmentDependencyRecord;
  }
  return raw as unknown as FragmentDependencies;
}
const stableCounts = (value: Record<string, number>) => JSON.stringify(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)));
export function canMigrateFragments(page: FragmentPage, plan: FragmentDependencies | null, selected: number, target: string | null): boolean {
  return canAssignFragments(page) && !!target && target !== 'inbox' && selected > 0 && !!plan && plan.complete && plan.issues.length === 0 && plan.write_mode === 'dependency_migration' && !plan.assignment_available && plan.revision === page.revision && plan.scope_key === page.scope_key && plan.snapshot === page.snapshot && plan.observation_snapshot === page.observation_snapshot && plan.selected_records === selected && plan.next_offset === null && plan.required_records.length === plan.required_records_total;
}
export function parseFragmentMigrationReceipt(raw: unknown, intent: FragmentMigrationIntent, plan: FragmentDependencies): FragmentMigrationReceipt {
  if (!object(raw) || raw.request_id !== intent.request_id || !integer(raw.revision) || typeof raw.replayed !== 'boolean' || raw.migration_applied !== true || raw.assignments_applied !== true || raw.project_id !== intent.project_id || raw.session_pk !== intent.session || raw.file_instance_id !== intent.source || raw.first_event_id !== intent.first_event || raw.last_event_id !== intent.last_event || raw.plan_sha256 !== intent.expected_plan_sha256 || raw.plan_sha256 !== plan.plan_sha256 || raw.selected_records !== plan.selected_records || raw.required_records_included !== plan.required_records_total || raw.records_assigned !== plan.selected_records + plan.required_records_total || !integer(raw.partitions_created) || !counts(raw.dependencies_migrated) || stableCounts(raw.dependencies_migrated) !== stableCounts(plan.dependencies) || raw.model_calls !== 0 || raw.scientific_records_rewritten !== 0) throw new Error('迁移回执与完整计划、范围或目标不一致，未更新页面；请重新读取核对实际结果。');
  return raw as unknown as FragmentMigrationReceipt;
}
export function appendFragmentDependencies(first: FragmentDependencies, next: FragmentDependencies): FragmentDependencies {
  for (const key of ['revision', 'scope_key', 'snapshot', 'observation_snapshot', 'plan_sha256', 'complete', 'closure_closed', 'assignment_available', 'write_mode', 'selected_records', 'required_records_total', 'migration_applied'] as const) {
    if (first[key] !== next[key]) throw new Error('科研依赖摘要或读取条件已变化，未混合续页；请主动重新预检。');
  }
  if (stableCounts(first.dependencies) !== stableCounts(next.dependencies) || stableCounts(first.shared_context) !== stableCounts(next.shared_context) || JSON.stringify(first.issues) !== JSON.stringify(next.issues) || first.next_offset !== first.required_records.length) throw new Error('科研依赖分页计数不一致，未混合续页。');
  const records = [...first.required_records, ...next.required_records];
  for (let i = 0; i < records.length; i++) {
    const previous = records[i - 1], record = records[i];
    if (records.slice(0, i).some(r => r.first_event_id === record.first_event_id) || (previous && (record.file_instance_id < previous.file_instance_id || (record.file_instance_id === previous.file_instance_id && record.byte_start < previous.byte_end)))) throw new Error('科研依赖续页重复或交叠，保留已读结果并停止续读。');
  }
  return { ...first, required_records: records, next_offset: next.next_offset };
}
const dependencyGroups: { name: string; labels: Record<string, string> }[] = [
  { name: '证据与科研对象', labels: { entities: '科研对象', claims: '科研记录', evidence_spans: '证据片段', claim_evidence: '证据引用' } },
  { name: '审核与资格核对', labels: { review_actions: '人工审核', claim_source_checks: '来源核对', rule_reference_checks: '规则证明核对' } },
  { name: '模型上下文与处理', labels: { extraction_runs: '提取运行', extraction_plans: '提取计划', model_attempts: '模型尝试', candidate_locations: '候选定位', link_progress: '关联进度', jobs: '处理任务', extraction_queue: '提取队列任务', extraction_queue_events: '提取队列记录', pipeline_queue: '处理队列任务', pipeline_queue_events: '处理队列记录' } },
  { name: '运行、编辑与文件版本', labels: { runs: '运行登记', run_io: '输入输出关联', run_observations: '原生运行观察', edit_records: '编辑记录', edit_request_checks: '编辑请求核对', run_manifests: '候选运行清单', artifact_versions: '文件版本', artifact_observations: '文件观察', artifact_jobs: '文件处理任务', artifact_job_events: '文件任务记录', artifact_discoveries: '文件发现', artifact_discovery_attempts: '文件发现尝试', workspace_snapshots: '工作区快照' } },
  { name: '直接记录与对象选择', labels: { explicit_records: '直接记录回执', note_requests: '笔记请求', decision_requests: '决定请求', decision_resolutions: '对象选择记录' } },
];
export function fragmentDependencyGroups(values: Record<string, number>) {
  const known = new Set(dependencyGroups.flatMap(group => Object.keys(group.labels)));
  const groups = dependencyGroups.map(group => ({ name: group.name, items: Object.entries(group.labels).filter(([key]) => values[key] !== undefined).map(([key, label]) => ({ key, label, count: values[key] })) })).filter(group => group.items.length);
  const other = Object.entries(values).filter(([key]) => !known.has(key)).map(([key, count]) => ({ key, label: `尚未识别的依赖类别（${key}）`, count }));
  if (other.length) groups.push({ name: '其它依赖记录', items: other });
  return groups;
}
export const dependencyIssueNames: Record<string, string> = { schema_contract_changed: '数据库依赖结构尚无法核对', missing_reference: '存在缺失的依赖引用', missing_physical_first_record: '依赖记录缺少完整物理首事件', unverifiable_json_reference: '依赖引用内容损坏或超限', invalid_reference_container: '依赖引用结构不符合要求', invalid_reference_type: '依赖引用身份类型不符合要求', sql_step_budget: '本次依赖核对达到计算上限', node_budget: '本次依赖记录超过核对上限', edge_budget: '本次依赖连接超过核对上限', metadata_byte_budget: '本次依赖元数据超过核对上限' };
export const contextNames: Record<string, string> = { declared: '原生目录声明', absent: '本记录没有目录声明', unobserved: '目录元数据尚未观测', invalid: '目录声明无效', unavailable: '目录元数据不可核对' };
export const hintNames: Record<string, string> = { candidate_path: '路径候选，尚非归属', ambiguous_path: '路径对应多个项目，归属不明', unassigned: '没有可用路径候选' };
export const directorySources: Record<string, string> = { codex_session: 'Codex 原生会话头', codex_turn: 'Codex 原生轮次上下文', claude_record: 'Claude 原生记录字段', none: '没有可用声明来源' };
export const hintReasons: Record<string, string> = { root_registry_limit: '根目录登记超出本次核对上限，不能判断路径候选', no_registered_path: '未匹配已登记根目录', registered_path_hint: '仅依据已登记路径产生候选', unobserved: '目录观测尚未记录，线索未知', invalid: '目录声明无效，不继续推断', unavailable: '目录元数据不可核对', absent: '没有可用的目录声明' };
