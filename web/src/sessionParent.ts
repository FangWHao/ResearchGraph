import type { EvidenceTarget, SessionParentData, SessionParentObservation, SessionParentState } from './types';

export const sessionParentNames: Record<SessionParentState, string> = {
  unobserved: '父线程未知', no_parent_declared: '头记录未声明父线程', invalid: '父线程声明无效',
  conflicting: '父线程声明冲突', missing_parent: '声明的父线程尚未找到',
  ambiguous_parent: '父线程身份不唯一', cycle: '父线程关联存在循环',
  linked: '同项目父线程已关联', outside_project: '父线程属于其他项目', unsupported: '不支持父线程判定',
};
export const sessionParentDescriptions: Record<SessionParentState, string> = {
  unobserved: '尚未登记可用的头记录观测，不能据此判断为根线程。',
  no_parent_declared: '已查看的头记录未声明父线程，不能据此判断为根线程。',
  invalid: '头记录身份或父线程声明无效；保留来源，不猜关联。',
  conflicting: '头记录的父线程声明不一致，不能任选一条建立关系。',
  missing_parent: '原文声明了父线程，但本地尚未找到可核对的父头记录；后续导入可能补齐关联。',
  ambiguous_parent: '声明的父线程身份对应多个会话，不能按邻近记录或入库顺序选择。',
  cycle: '父线程关系形成循环，不能当作正常父子层级。',
  linked: '当前本地头声明指向同项目内唯一父线程；这不代表研究内容的采用、使用或科学确认。',
  outside_project: '当前关联跨项目，不公开父会话编号或原文引用；源声明仍可从本会话头记录核对。',
  unsupported: '该工具不使用此 Codex 父线程判定。',
};
export const parentObservationNames: Record<SessionParentObservation['state'], string> = {
  declared: '已声明父线程', not_declared: '未声明父线程', invalid: '声明无效', conflict: '同一头记录声明冲突',
};
export const parentBasisNames: Record<SessionParentObservation['basis'], string> = {
  none: '未记录父来源字段', top_level: '会话头直接字段', thread_spawn: '子线程生成来源', both: '两处来源字段',
};
export const parentReasonNames: Record<string, string> = {
  invalid_payload: '头记录负载结构无效', invalid_identity: '线程身份字段无效',
  identity_mismatch: '头记录线程身份与登记身份不一致', parent_mismatch: '两处父线程身份不一致',
  invalid_top_parent: '直接字段的父线程身份无效', invalid_spawn_parent: '子线程来源的父线程身份无效',
  invalid_spawn_depth: '子线程来源的深度字段无效', self_parent: '声明指向线程自身',
  native_header: '来自原生会话头声明', parent_not_declared: '头记录未声明父线程',
};

const invalid = () => new Error('父线程响应或会话身份不一致，关系暂不可判断。');
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  return value as Record<string, unknown>;
}
function integer(value: unknown, minimum = 1): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum) throw invalid();
  return value;
}
function text(value: unknown): string {
  if (typeof value !== 'string' || !value) throw invalid();
  return value;
}
function uuid(value: unknown): string | null {
  if (value === null) return null;
  if (typeof value !== 'string' || value.length !== 36
    || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value)) throw invalid();
  return value;
}

export function parseSessionParent(value: unknown, session: number): SessionParentData | null {
  if (value == null) return null;
  const data = object(value);
  const state = text(data.state);
  if (integer(data.session_pk) !== session || !Object.hasOwn(sessionParentNames, state)) throw invalid();
  const tool = text(data.tool);
  if ((tool === 'codex') === (state === 'unsupported')) throw invalid();
  const parent = data.parent_session_pk === null ? null : integer(data.parent_session_pk);
  const parentNative = uuid(data.parent_native_id);
  const parentEvent = data.parent_event_id === null ? null : integer(data.parent_event_id);
  if (state === 'linked') {
    if (parent === null || parent === session || parentNative === null) throw invalid();
  } else if (parent !== null || parentNative !== null || parentEvent !== null) throw invalid();
  const total = integer(data.observations_total, 0);
  const highwater = integer(data.observation_highwater, 0);
  if (!Array.isArray(data.observations) || data.observations.length !== Math.min(total, 20)
    || typeof data.observations_partial !== 'boolean'
    || data.observations_partial !== (total > 20)) throw invalid();
  const observations: SessionParentObservation[] = data.observations.map(value => {
    const row = object(value);
    const rowState = text(row.state); const basis = text(row.basis);
    if (!Object.hasOwn(parentObservationNames, rowState) || !Object.hasOwn(parentBasisNames, basis)) throw invalid();
    return { event_id: integer(row.event_id), state: rowState as SessionParentObservation['state'],
      basis: basis as SessionParentObservation['basis'], reason: text(row.reason), native_id: uuid(row.native_id),
      parent_id: uuid(row.parent_id), other_parent_id: uuid(row.other_parent_id), recorded_at: text(row.recorded_at) };
  });
  if (observations.some((row, index) => index > 0 && row.event_id <= observations[index - 1].event_id)
    || (observations.at(-1)?.event_id ?? 0) !== highwater
    || ((state === 'unobserved' || state === 'unsupported') && total !== 0)
    || (!['unobserved', 'unsupported'].includes(state) && total === 0)) throw invalid();
  return { session_pk: session, tool, native_id: uuid(data.native_id), state: state as SessionParentState,
    parent_session_pk: parent, parent_native_id: parentNative, parent_event_id: parentEvent,
    observations, observations_total: total, observations_partial: data.observations_partial,
    observation_highwater: highwater };
}

export function parentEvidenceTarget(data: SessionParentData): EvidenceTarget | null {
  return data.state === 'linked' && data.parent_event_id !== null ? { event_id: data.parent_event_id } : null;
}
