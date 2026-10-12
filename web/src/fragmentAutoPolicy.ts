export type AutoRootReason = 'registered_root' | 'borrowed_membership' | 'overlapping_projects' | 'invalid_path' | 'nonlocal_root' | 'root_registry_limit';
export interface AutoAssignmentRoot {
  root_id: string; project_id: string; host_id: string; path: string; kind: string;
  applicable: boolean; reason: AutoRootReason;
}
export interface AutoAssignmentPolicy {
  action_id: number; project_id: string; enabled: 0 | 1; actor: string;
  recorded_at: string; after_event_id: number;
}
export interface AutoAssignmentPage {
  available: boolean; reason?: string; project_id: string; revision: number; enabled: boolean;
  scope_key: string; total: number; next_offset: number | null; offset: number;
  policy: AutoAssignmentPolicy | null; roots: AutoAssignmentRoot[];
  boundary: 'future_unassigned_records'; future_roots: true;
}
export interface AutoAssignmentRequest {
  project: string; offset: number; limit: number; revision?: number; scopeKey?: string;
}
export const autoRootReasons: Record<AutoRootReason, string> = {
  registered_root: '已明确登记，可用于本项目规则',
  borrowed_membership: '仅为借用科研来源，不授权自动归属',
  overlapping_projects: '目录与其它项目重叠，不自动归属',
  invalid_path: '登记路径无法核对，不自动归属',
  nonlocal_root: '非本机目录，不用于本机归属',
  root_registry_limit: '全库登记规则超过核对上限，不自动匹配',
};
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const integer = (value: unknown, min = 0): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= min;
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0;
const hash = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
function fail(): never { throw new Error('自动归属响应身份或规则分页不一致，未采用此页；请重新读取第一页。'); }
export function parseAutoAssignmentPage(raw: unknown, expected: AutoAssignmentRequest): AutoAssignmentPage {
  if (!object(raw) || typeof raw.available !== 'boolean') fail();
  if (!raw.available) {
    if (!text(raw.reason)) fail();
    return { available: false, reason: raw.reason } as AutoAssignmentPage;
  }
  if (raw.project_id !== expected.project || !integer(raw.revision) || typeof raw.enabled !== 'boolean'
    || !hash(raw.scope_key) || !integer(raw.total) || expected.offset > raw.total
    || raw.boundary !== 'future_unassigned_records' || raw.future_roots !== true
    || !Array.isArray(raw.roots) || raw.roots.length !== Math.min(expected.limit, raw.total - expected.offset)
    || (expected.revision !== undefined && raw.revision !== expected.revision)
    || (expected.scopeKey !== undefined && raw.scope_key !== expected.scopeKey)) fail();
  const next = expected.offset + raw.roots.length < raw.total ? expected.offset + raw.roots.length : null;
  if (raw.next_offset !== next) fail();
  const policy = raw.policy;
  if (policy === null) { if (raw.enabled) fail(); }
  else if (!object(policy) || !integer(policy.action_id, 1) || policy.project_id !== expected.project
    || ![0, 1].includes(policy.enabled as number) || Boolean(policy.enabled) !== raw.enabled
    || !text(policy.actor) || !policy.actor.startsWith('human:') || !policy.actor.slice(6).trim()
    || !text(policy.recorded_at) || !integer(policy.after_event_id)) fail();
  const seen = new Set<string>();
  for (const root of raw.roots) {
    if (!object(root) || !text(root.root_id) || seen.has(root.root_id) || root.project_id !== expected.project
      || !text(root.host_id) || !text(root.path) || !text(root.kind) || typeof root.applicable !== 'boolean'
      || !text(root.reason) || !Object.hasOwn(autoRootReasons, root.reason)
      || root.applicable !== (root.reason === 'registered_root')) fail();
    seen.add(root.root_id);
  }
  return { ...raw, offset: expected.offset } as unknown as AutoAssignmentPage;
}
export function assertAutoAssignmentContinuation(first: AutoAssignmentPage, page: AutoAssignmentPage): void {
  if (!first.available || !page.available || first.project_id !== page.project_id || first.revision !== page.revision
    || first.scope_key !== page.scope_key || first.total !== page.total || first.enabled !== page.enabled
    || first.policy?.action_id !== page.policy?.action_id || first.policy?.after_event_id !== page.policy?.after_event_id
    || first.policy?.actor !== page.policy?.actor || first.policy?.recorded_at !== page.policy?.recorded_at) fail();
  if (first.offset !== page.offset && page.roots.some(root => first.roots.some(old => old.root_id === root.root_id))) fail();
}
export function parseAutoAssignmentReceipt(raw: unknown, before: AutoAssignmentPage, actor: string, enabled: boolean): AutoAssignmentPage {
  const page = parseAutoAssignmentPage(raw, { project: before.project_id, offset: 0, limit: 100 });
  if (!page.available || page.enabled !== enabled || page.revision < before.revision || !page.policy
    || page.policy.actor !== actor || (page.policy.action_id === before.policy?.action_id && before.enabled !== enabled)
    || page.policy.after_event_id < (before.policy?.after_event_id ?? 0)) fail();
  return page;
}
