import { objectFields, versionOrigin } from './versions';
import type { FileVersionRecord, L1Run, ManifestRole } from './types';

export const manifestRoles: Record<ManifestRole, string> = { inputs: '输入', scripts: '脚本', patches: '补丁', environment: '环境', outputs: '输出' };
const fieldNames: Record<string, string> = { ...manifestRoles, attempt_id: '研究尝试', snapshot_id: '声明快照', scope: '研究范围', parameters: '参数', seed: '随机种子', started_at: '报告开始时间', ended_at: '报告结束时间', exit_code: '报告退出码', occurred_at: '发生时间' };
export function manifestField(value: string): string { return fieldNames[value] ?? `未识别字段（${value}）`; }
function count(value: unknown): number | null { return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null; }
export function manifestPage(value: unknown, limit: number) {
  const fields = objectFields(value); const issues: string[] = [];
  if (!fields) return { items: [] as Record<string, unknown>[], total: null, offset: null, nextOffset: null, partial: true, missing: true, issues: ['接口未提供清单，相关记录是否存在未知。'] };
  const raw = Array.isArray(fields.items) ? fields.items : [];
  if (!Array.isArray(fields.items)) issues.push('接口未提供有效列表，不能按空列表解释。');
  const items = raw.slice(0, limit).map(item => objectFields(item) ?? {});
  if (raw.some(item => objectFields(item) == null)) issues.push('列表包含无效记录，保留未知条目供核对。');
  if (raw.length > limit) issues.push(`返回超过 ${limit} 条展示上限，仅显示前 ${limit} 条。`);
  const total = count(fields.total); const offset = count(fields.offset);
  const next = fields.next_offset === null ? null : count(fields.next_offset);
  if (total == null || offset == null || fields.next_offset === undefined || (fields.next_offset !== null && next == null)) issues.push('分页字段缺失或无效，无法判断遗漏。');
  if (total != null && offset != null && (total < offset + raw.length || (next == null && fields.next_offset === null && total > offset + raw.length)
    || (next != null && (next !== offset + raw.length || next >= total || next <= offset)))) issues.push('分页条数、总数或后续位置矛盾，不能视为完整清单。');
  let partial = offset == null || offset > 0 || total == null || total > items.length || next != null || issues.length > 0;
  if (typeof fields.partial === 'boolean' && fields.partial !== partial) { issues.push('完整性标记与分页记录矛盾，不能视为完整清单。'); partial = true; }
  return { items, total, offset, nextOffset: next, partial, missing: false, issues };
}
export function reportedRole(reported: unknown, role: ManifestRole) {
  const value = objectFields(reported)?.[role];
  if (value === null) return { ids: [] as string[], status: 'unknown', text: '报告未知，未声明版本列表。' } as const;
  if (!Array.isArray(value) || value.some(id => typeof id !== 'string' || !id)) return { ids: [] as string[], status: 'invalid', text: '报告字段缺失或格式异常，版本列表未知。' } as const;
  return { ids: value as string[], status: 'reported', text: value.length ? `明确报告 ${value.length} 个版本；仅为报告关联。` : '明确报告空列表，不证明实际没有此类 I/O。' } as const;
}
export function reportedExit(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= -(2 ** 31) && value < 2 ** 31 ? value : null;
}
export function manifestPresentation(value: unknown, native: L1Run) {
  const fields = objectFields(value) ?? {}; const reported = objectFields(fields.reported) ?? {};
  const issues: string[] = [];
  if (fields.run_id !== native.run_id || fields.project_id !== native.project_id) issues.push('清单的运行或项目标识缺失/不一致，不能确定归属。');
  if (fields.claim_state !== 'candidate' || fields.basis !== 'direct_record') issues.push('清单审核状态或依据异常，不能提升为已确认。');
  if (fields.actual_io_completeness !== 'unknown') issues.push('接口实际 I/O 完整性字段缺失或异常；实际完整性仍未知。');
  if (!objectFields(fields.reported)) issues.push('报告字段缺失或异常。');
  const code = reportedExit(reported.exit_code);
  if (reported.exit_code != null && code == null) issues.push('报告退出码格式无效，按未知显示。');
  const knownNative = native.state === 'exited' && reportedExit(native.exit_code) != null;
  const conflict = knownNative && code != null && native.exit_code !== code;
  if (typeof fields.reported_exit_conflicts_with_native !== 'boolean') issues.push('接口未提供退出码冲突标记；同时保留原生观测与报告供核对。');
  else if (fields.reported_exit_conflicts_with_native !== conflict) issues.push('退出码冲突标记与返回的原生/报告数值不一致。');
  const binding = fields.binding_state === 'native_request' ? '报告关联到唯一原生请求'
    : fields.binding_state === 'native_request_ambiguous' ? '原生请求关联存在歧义，不能确定唯一请求'
    : fields.binding_state === 'native_run_unknown_or_not_visible' ? '原生运行未知或当前不可见，报告不补造执行状态'
    : '原生请求关联状态未知';
  if (!Array.isArray(fields.unknown_fields) || fields.unknown_fields.some(field => typeof field !== 'string')) issues.push('未知字段清单缺失或异常。');
  return { fields, reported, code, binding, conflict: conflict || fields.reported_exit_conflicts_with_native === true, issues,
    unknown: Array.isArray(fields.unknown_fields) ? fields.unknown_fields.filter((field): field is string => typeof field === 'string').map(manifestField) : [] };
}
export function manifestVersion(value: unknown, project: string) {
  const fields = objectFields(value) ?? {}; const version = objectFields(fields.version);
  const issues: string[] = [];
  if (fields.association_state !== 'reported_only' || fields.claim_state !== 'candidate' || fields.basis !== 'direct_record') issues.push('版本关联状态或依据异常，不能当作实际使用证明。');
  if (typeof fields.requested_version_id !== 'string' || !fields.requested_version_id) issues.push('报告版本 ID 缺失。');
  const matches = version && version.project_id === project && version.version_id === fields.requested_version_id && version.version_id === fields.resolved_version_id;
  const malformed = version && ['path', 'algo', 'digest', 'source', 'representation', 'basis', 'claim_state', 'content_sha256', 'observed_at', 'occurred_at', 'recorded_at'].some(key => version[key] != null && typeof version[key] !== 'string');
  if (malformed) issues.push('版本元数据字段格式异常，保留报告 ID，不能核定版本内容或时间。');
  const visible = fields.resolution === 'visible_version' && !!matches && !malformed;
  if (fields.resolution === 'visible_version' && !matches) issues.push('可见版本与报告 ID、解析 ID 或项目不一致；版本身份不能核定。');
  if (fields.resolution === 'version_unknown_or_not_visible' && (version != null || fields.resolved_version_id != null)) issues.push('版本未知标记与返回版本信息矛盾。');
  if (!['visible_version', 'version_unknown_or_not_visible'].includes(String(fields.resolution))) issues.push('版本解析状态未知。');
  const role = typeof fields.role === 'string' && Object.hasOwn(manifestRoles, fields.role) ? fields.role as ManifestRole : null;
  if (!role) issues.push('I/O 角色未知。');
  else if (fields.direction !== (role === 'outputs' ? 'out' : 'in')) issues.push('I/O 方向与角色矛盾。');
  return { fields, visible, version: visible ? version as FileVersionRecord : null,
    origin: visible ? versionOrigin(version as FileVersionRecord) : null, role, issues };
}
