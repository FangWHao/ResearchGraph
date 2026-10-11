import { describe, expect, it } from 'vitest';
import { assertWorktreeContinuation, parseWorktreePolicy, parseWorktrees } from '../src/worktrees';
function row(id = 'root') {
  return { root_id: id, project_id: 'P', path: '/synthetic/public', kind: 'repo', host_id: 'local', git_common_dir: '/synthetic/public/.git', git_metadata: { status: 'registered' }, discovery: { status: 'not_observed' } };
}
function raw() { return { project_id: 'P', revision: 8, follow_enabled: false, policy: null, roots: [row()], total: 1, next_offset: null }; }
const request = { project: 'P', offset: 0, limit: 50 };
describe('工作树元数据、归属与固定分页', () => {
  it('无观测保持未知，不补诊断或零计数；默认策略未开启', () => {
    const page = parseWorktrees(raw(), request);
    expect(page.roots[0].discovery).toEqual({ status: 'not_observed' });
    expect(page.policy).toBeNull(); expect(page.follow_enabled).toBe(false);
  });
  it('分页核对项目、修订、总量及连续偏移；错误页不能并入旧页', () => {
    const first = parseWorktrees({ ...raw(), roots: [row('A')], total: 2, next_offset: 1 }, { ...request, limit: 1 });
    const next = parseWorktrees({ ...raw(), roots: [row('B')], total: 2 }, { ...request, limit: 1, offset: 1, revision: 8 });
    expect(() => assertWorktreeContinuation(first, next)).not.toThrow();
    expect(() => parseWorktrees({ ...raw(), project_id: 'other' }, request)).toThrow();
    expect(() => parseWorktrees({ ...raw(), revision: 9 }, { ...request, revision: 8 })).toThrow();
    expect(() => parseWorktrees({ ...raw(), next_offset: 0 }, request)).toThrow();
    expect(() => assertWorktreeContinuation(first, { ...next, total: 3 })).toThrow();
  });
  it('重复根、其它项目根或诊断身份不匹配均拒绝；不提供其它项目引用', () => {
    expect(() => parseWorktrees({ ...raw(), total: 2, roots: [row(), row()] }, request)).toThrow();
    expect(() => parseWorktrees({ ...raw(), roots: [{ ...row(), project_id: 'other' }] }, request)).toThrow();
    const discovery = { status: 'ambiguous', observation_id: 1, project_id: 'other', root_id: 'root', recorded_at: '2026-10-11T00:00:00Z', details: { created: 0, list_complete: false } };
    expect(() => parseWorktrees({ ...raw(), roots: [{ ...row(), discovery }] }, request)).toThrow();
  });
  it('清单本页 partial 原样保留，未提供计数仍未提供，不按总量补成功', () => {
    const discovery = { status: 'partial', observation_id: 1, project_id: 'P', root_id: 'root', recorded_at: '2026-10-11T00:00:00Z', details: { created: 3, list_total: 54, page_count: 16, list_complete: false } };
    const page = parseWorktrees({ ...raw(), roots: [{ ...row(), discovery }] }, request);
    expect(page.roots[0].discovery.details).toEqual(discovery.details);
    expect(page.roots[0].discovery.details?.known).toBeUndefined();
    expect(() => parseWorktrees({ ...raw(), roots: [{ ...row(), discovery: { ...discovery, details: { created: -1 } } }] }, request)).toThrow();
  });
  it('策略回执必须属于请求项目和开关意图，未知或旧修订不能更新开关', () => {
    expect(parseWorktreePolicy({ project_id: 'P', follow_enabled: true, revision: 9 }, 'P', true, 8).revision).toBe(9);
    for (const value of [{ project_id: 'other', follow_enabled: true, revision: 9 }, { project_id: 'P', follow_enabled: false, revision: 9 }, { project_id: 'P', follow_enabled: true, revision: 7 }]) expect(() => parseWorktreePolicy(value, 'P', true, 8)).toThrow();
    expect(() => parseWorktrees({ ...raw(), follow_enabled: true }, request)).toThrow();
  });
});
