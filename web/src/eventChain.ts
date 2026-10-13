import type { EventChainData, EventChainState, EvidenceTarget } from './types';

export const eventChainNames: Record<EventChainState, string> = {
  unsupported: '该工具不适用', unobserved: '父链尚未观测', uuid_unavailable: '记录 UUID 不可用',
  parent_not_declared: '源记录未声明父 UUID', null_parent: '源记录明确声明空父 UUID',
  invalid_parent: '父 UUID 声明无效', conflicting_record: '同来源记录存在矛盾',
  ambiguous_parent: '父 UUID 对应多个不同记录组', outside_project: '父记录仅在其它项目找到',
  missing_parent: '声明的父记录尚未找到', linked: '当前父记录已关联', cycle: '父链存在循环',
  metadata_incomplete: '父链元数据仍有补记积压',
};
export const eventChainDescriptions: Record<EventChainState, string> = {
  unsupported: '不使用此 Claude 事件父链判定。',
  unobserved: '该物理记录的父链元数据尚未登记，不能据此判断根记录或主线程。',
  uuid_unavailable: '源记录 UUID 缺失或无效，不按文字相同或邻近时间猜父链。',
  parent_not_declared: '缺少 parentUuid 字段，不等同明确空父，也不能判断根会话。',
  null_parent: '本条原记录的 parentUuid 为 null；这不证明根会话或完整祖先历史。',
  invalid_parent: '父 UUID 字段无效，原记录仍可查看，不替它选择其它父记录。',
  conflicting_record: '同一来源文件中的相同 UUID 出现不同声明或正文，不选择第一条或最新一条。',
  ambiguous_parent: '当前可匹配多个不等价声明组，不按入库顺序挑选父记录。',
  outside_project: '只在其它项目找到匹配，不公开其原文引用；本条源声明仍可核对。',
  missing_parent: '当前本地没有可匹配的父记录；后续导入或补记可能补齐。',
  linked: '当前已存元数据中找到等价父记录。各副本保留独立来源，不代表研究内容的使用、采用或科学确认。',
  cycle: '祖先元数据指向当前记录形成循环，不能当作正常事件层级。',
  metadata_incomplete: '相关来源的旧元数据尚未补齐，无法确定唯一父记录；不能据此判断无父、无循环或完整祖先链。',
};
export const ancestryNames: Record<EventChainData['ancestry_state'], string> = {
  ...eventChainNames, multiple_source_contexts: '后续祖先存在多个来源上下文，停止继续判定',
  depth_limit: '已达到祖先元数据检查上限',
};
export const resolutionNames: Record<EventChainData['resolution_scope'], string> = {
  source_file: '本来源文件内精确父 UUID', project_uuid: '当前项目的父 UUID 等价声明组', none: '未建立父引用',
};
const invalid = () => new Error('事件父链响应或原文身份不一致，关系暂不可判断。');
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  return value as Record<string, unknown>;
}
function integer(value: unknown, minimum = 1): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum) throw invalid();
  return value;
}
function uuid(value: unknown): string | null {
  if (value === null) return null;
  if (typeof value !== 'string' || value.length !== 36
    || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value)) throw invalid();
  return value;
}
function choice<T extends string>(value: unknown, values: readonly T[]): T {
  if (typeof value !== 'string' || !values.includes(value as T)) throw invalid();
  return value as T;
}
function bool(value: unknown): boolean {
  if (typeof value !== 'boolean') throw invalid();
  return value;
}

export function parseEventChain(value: unknown, event: number, session: number): EventChainData | null {
  if (value == null) return null;
  const data = object(value);
  if (integer(data.event_id) !== event || integer(data.session_pk) !== session) throw invalid();
  const file = integer(data.file_instance_id);
  const state = choice(data.state, Object.keys(eventChainNames) as EventChainState[]);
  if (typeof data.tool !== 'string' || !data.tool || (data.tool !== 'claude' && state !== 'unsupported')) throw invalid();
  const record = data.record_event_id === null ? null : integer(data.record_event_id);
  const native = uuid(data.native_uuid); const parent = uuid(data.parent_uuid);
  const uuidState = choice(data.uuid_state, ['valid', 'invalid', 'missing'] as const);
  const parentState = choice(data.parent_state, ['declared', 'null', 'missing', 'invalid'] as const);
  const sidechainState = choice(data.sidechain_state, ['declared', 'missing', 'invalid'] as const);
  const sidechain = data.is_sidechain === null ? null : bool(data.is_sidechain);
  if ((uuidState === 'valid') !== (native !== null) || (parentState === 'declared') !== (parent !== null)
    || (sidechainState === 'declared') !== (sidechain !== null) || (record !== null && record > event)) throw invalid();
  const observed = state !== 'unobserved' && state !== 'unsupported';
  if (observed !== (record !== null) || (observed ? data.basis !== 'direct_record'
    || typeof data.recorded_at !== 'string' || !data.recorded_at : data.basis !== null || data.recorded_at !== null)) throw invalid();
  const scope = choice(data.resolution_scope, ['source_file', 'project_uuid', 'none'] as const);
  const total = integer(data.parent_references_total, 0); const partial = bool(data.parent_references_partial);
  const contexts = integer(data.parent_source_contexts, 0);
  if (!Array.isArray(data.parent_references) || data.parent_references.length !== Math.min(total, 20)
    || partial !== (total > 20) || contexts > total && state !== 'cycle') throw invalid();
  const references = data.parent_references.map(value => {
    const row = object(value); const referenceFile = integer(row.file_instance_id);
    if (scope === 'source_file' && referenceFile !== file) throw invalid();
    return { event_id: integer(row.event_id), file_instance_id: referenceFile };
  });
  if (references.some((row, index) => index > 0 && row.event_id <= references[index - 1].event_id)
    || (state === 'linked' ? total === 0 || parentState !== 'declared' || uuidState !== 'valid' || scope === 'none'
      : total !== 0) || (state === 'null_parent' && parentState !== 'null')
    || (state === 'parent_not_declared' && parentState !== 'missing')) throw invalid();
  const ancestry = choice(data.ancestry_state, Object.keys(ancestryNames) as EventChainData['ancestry_state'][]);
  const steps = integer(data.ancestry_steps, 0); const limit = integer(data.ancestry_limit);
  if (limit !== 64 || steps > limit || (ancestry === 'depth_limit' && steps !== limit)) throw invalid();
  return { event_id: event, session_pk: session, file_instance_id: file, tool: data.tool, state,
    record_event_id: record, native_uuid: native, parent_uuid: parent, uuid_state: uuidState, parent_state: parentState,
    is_sidechain: sidechain, sidechain_state: sidechainState, recorded_at: data.recorded_at as string | null,
    basis: observed ? 'direct_record' : null, resolution_scope: scope, parent_references: references,
    parent_references_total: total, parent_references_partial: partial, parent_source_contexts: contexts,
    ancestry_state: ancestry, ancestry_steps: steps, ancestry_limit: limit,
    source_metadata_complete: bool(data.source_metadata_complete), scope_metadata_incomplete: bool(data.scope_metadata_incomplete) };
}

export function eventChainTargets(data: EventChainData): EvidenceTarget[] {
  return data.state === 'linked' ? data.parent_references.map(row => ({ event_id: row.event_id })) : [];
}
