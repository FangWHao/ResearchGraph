export type DiscoveryStatus = 'not_observed' | 'observed' | 'partial' | 'ambiguous' | 'missing_or_changed_path' | 'unavailable' | 'changed_identity' | 'invalid_or_oversized_list' | 'policy_or_identity_changed';
export interface WorktreeRequest { project: string; offset: number; limit: number; revision?: number }
export interface WorktreeRoot {
  root_id: string; project_id: string; path: string; kind: 'repo' | 'worktree'; host_id: string;
  git_common_dir: string | null; git_metadata: Record<string, unknown>;
  discovery: { status: DiscoveryStatus; observation_id?: number; recorded_at?: string; details?: Record<string, unknown> };
}
export interface WorktreePage {
  project_id: string; revision: number; follow_enabled: boolean;
  policy: { action_id: number; project_id: string; enabled: boolean | 0 | 1; recorded_at: string } | null;
  roots: WorktreeRoot[]; total: number; next_offset: number | null; offset: number;
}
export const discoveryNames: Record<DiscoveryStatus, string> = {
  not_observed: '尚无发现观测', observed: '本次清单页已核对', partial: '清单仍有后续页',
  ambiguous: '共同目录归属不唯一，停止发现', missing_or_changed_path: '已登记路径缺失或改变',
  unavailable: '本次无法读取 Git 身份或清单', changed_identity: '当前 Git 身份与登记不一致',
  invalid_or_oversized_list: '工作树清单无效或超限', policy_or_identity_changed: '发现期间策略或归属已改变',
};
export const discoveryCounts = [
  ['created', '本页新登记'], ['known', '本页已登记'], ['conflict', '本页路径归属冲突'],
  ['privacy_blocked', '本页隐私屏障阻止'], ['unavailable', '本页无法核对'],
  ['changed_identity', '本页身份改变'], ['ready', '本页核对可登记'],
] as const;
function object(value: unknown): value is Record<string, unknown> { return !!value && typeof value === 'object' && !Array.isArray(value); }
function integer(value: unknown): value is number { return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0; }
function text(value: unknown): value is string { return typeof value === 'string' && value.length > 0; }
function fail(): never { throw new Error('工作树响应身份或分页不一致，未使用此页；请重新读取第一页。'); }
export function parseWorktrees(value: unknown, expected: WorktreeRequest): WorktreePage {
  if (!object(value) || value.project_id !== expected.project || !integer(value.revision)
    || (expected.revision != null && value.revision !== expected.revision) || typeof value.follow_enabled !== 'boolean'
    || !integer(value.total) || expected.offset > value.total || !Array.isArray(value.roots)
    || value.roots.length !== Math.min(expected.limit, value.total - expected.offset)) fail();
  const next = expected.offset + value.roots.length < value.total ? expected.offset + value.roots.length : null;
  if (value.next_offset !== next) fail();
  if (value.policy !== null && (!object(value.policy) || !integer(value.policy.action_id)
    || value.policy.project_id !== expected.project || ![true, false, 0, 1].includes(value.policy.enabled as boolean)
    || Boolean(value.policy.enabled) !== value.follow_enabled || !text(value.policy.recorded_at))) fail();
  if (value.policy === null && value.follow_enabled) fail();
  const seen = new Set<string>();
  for (const root of value.roots) {
    if (!object(root) || !text(root.root_id) || seen.has(root.root_id) || root.project_id !== expected.project
      || !text(root.path) || !['repo', 'worktree'].includes(root.kind as string) || !text(root.host_id)
      || !(root.git_common_dir === null || text(root.git_common_dir)) || !object(root.git_metadata) || !object(root.discovery)
      || !text(root.discovery.status) || !Object.prototype.hasOwnProperty.call(discoveryNames, root.discovery.status)) fail();
    seen.add(root.root_id);
    const d = root.discovery;
    if (d.status !== 'not_observed') {
      if (!integer(d.observation_id) || d.project_id !== expected.project || d.root_id !== root.root_id
        || !text(d.recorded_at) || !object(d.details)) fail();
      for (const [key] of discoveryCounts) if (d.details[key] !== undefined && !integer(d.details[key])) fail();
      for (const key of ['list_total', 'page_count']) if (d.details[key] !== undefined && !integer(d.details[key])) fail();
      if (d.details.list_complete !== undefined && typeof d.details.list_complete !== 'boolean') fail();
    }
  }
  return { ...value, offset: expected.offset } as unknown as WorktreePage;
}
export function assertWorktreeContinuation(first: WorktreePage, page: WorktreePage): void {
  if (first.project_id !== page.project_id || first.revision !== page.revision || first.total !== page.total
    || first.follow_enabled !== page.follow_enabled || first.policy?.action_id !== page.policy?.action_id) fail();
}
export function parseWorktreePolicy(value: unknown, project: string, follow: boolean, revision: number) {
  if (!object(value) || value.project_id !== project || value.follow_enabled !== follow || !integer(value.revision) || value.revision < revision) fail();
  return value as unknown as { project_id: string; follow_enabled: boolean; revision: number };
}
