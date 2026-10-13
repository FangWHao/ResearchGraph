import { describe, expect, it } from 'vitest';
import { assertAutoAssignmentContinuation, parseAutoAssignmentPage, parseAutoAssignmentReceipt } from '../src/fragmentAutoPolicy';
import { parseFragmentPage } from '../src/projectFragments';

const project = 'synthetic-project', scope = 'a'.repeat(64);
function root(n: number) { return { root_id: `root-${n}`, project_id: project, host_id: 'local', path: `/synthetic/目录-${n}`, kind: 'data', applicable: true, reason: 'registered_root' }; }
function raw(offset = 0) { return { available: true, project_id: project, revision: 7, enabled: false, scope_key: scope, total: 101, next_offset: offset === 0 ? 100 : null, policy: null, roots: offset === 0 ? Array.from({ length: 100 }, (_, n) => root(n)) : [root(100)], boundary: 'future_unassigned_records', future_roots: true }; }
const expected = { project, offset: 0, limit: 100 };
describe('未来自动归属的读取身份和授权边界', () => {
  it('续页同时固定修订、摘要、策略和根身份，不能混入另项目或重复根', () => {
    const first = parseAutoAssignmentPage(raw(), expected);
    const later = parseAutoAssignmentPage(raw(100), { ...expected, offset: 100, revision: 7, scopeKey: scope });
    expect(() => assertAutoAssignmentContinuation(first, later)).not.toThrow();
    for (const changed of [{ ...raw(100), revision: 8 }, { ...raw(100), scope_key: 'b'.repeat(64) }, { ...raw(100), project_id: 'other' }, { ...raw(100), total: 102 }, { ...raw(100), next_offset: 100 }]) {
      expect(() => parseAutoAssignmentPage(changed, { ...expected, offset: 100, revision: 7, scopeKey: scope })).toThrow();
    }
    expect(() => assertAutoAssignmentContinuation(first, { ...later, roots: first.roots.slice(0, 1) })).toThrow();
    expect(() => parseAutoAssignmentPage({ ...raw(), roots: [root(0), root(0), ...raw().roots.slice(2)] }, expected)).toThrow();
    expect(() => parseAutoAssignmentPage({ ...raw(), roots: [{ ...root(0), project_id: 'other' }, ...raw().roots.slice(1)] }, expected)).toThrow();
  });
  it('未提供的配置保持未知，开启必须有真实保存策略和未来边界', () => {
    expect(parseAutoAssignmentPage({ available: false, reason: 'legacy_schema' }, expected)).toEqual({ available: false, reason: 'legacy_schema' });
    for (const changed of [{ ...raw(), enabled: true }, { ...raw(), boundary: 'all_history' }, { ...raw(), future_roots: false }, { ...raw(), available: undefined }]) {
      expect(() => parseAutoAssignmentPage(changed, expected)).toThrow();
    }
  });
  it('借用、跨项目重叠、非本机和超限都不能变成可用规则', () => {
    for (const reason of ['borrowed_membership', 'overlapping_projects', 'invalid_path', 'nonlocal_root', 'root_registry_limit']) {
      const value = { ...raw(), roots: [{ ...root(0), applicable: false, reason }, ...raw().roots.slice(1)] };
      expect(parseAutoAssignmentPage(value, expected).roots[0].applicable).toBe(false);
      expect(() => parseAutoAssignmentPage({ ...value, roots: [{ ...root(0), reason }, ...raw().roots.slice(1)] }, expected)).toThrow();
    }
  });
  it('保存回执必须属于当前人、项目和开关意图；不接受倒退边界或空策略', () => {
    const before = parseAutoAssignmentPage(raw(), expected);
    const valid = { ...raw(), enabled: true, revision: 8, policy: { action_id: 1, project_id: project, enabled: 1, actor: 'human:公开甲', recorded_at: '2026-10-12T00:00:00Z', after_event_id: 55 } };
    expect(parseAutoAssignmentReceipt(valid, before, 'human:公开甲', true).policy?.after_event_id).toBe(55);
    for (const changed of [{ ...valid, project_id: 'other' }, { ...valid, enabled: false }, { ...valid, policy: null }, { ...valid, revision: 6 }, { ...valid, policy: { ...valid.policy, actor: 'human:公开乙' } }]) {
      expect(() => parseAutoAssignmentReceipt(changed, before, 'human:公开甲', true)).toThrow();
    }
    const previous = parseAutoAssignmentPage(valid, expected);
    expect(() => parseAutoAssignmentReceipt({ ...valid, policy: { ...valid.policy, after_event_id: 54 } }, previous, 'human:公开甲', true)).toThrow();
  });
  it('片段保留人工或自动依据及未知原因，不把旧缺字段补成自动成功', () => {
    const record = { first_event_id: 2, byte_start: 0, byte_end: 20, object_sha256: 'c'.repeat(64), first_seq: 1, last_seq: 1, event_count: 1, has_user_turn: true, observation_state: 'declared', observed_at: '2026-10-12T00:00:00Z', context: { state: 'declared', source: 'claude_record', cwd: '/synthetic/目录', cwd_sha256: 'd'.repeat(64), reason: 'native_record', origin_event_id: 2 }, project_hint: { state: 'candidate_path', basis: 'path_hint', projects: [project], matching_root_ids: ['r'], candidates_partial: false, reason: 'registered_path_hint' }, assignment: { project_id: project, logical_session_pk: 3, partition_id: 'p', basis: 'path_rule', reason: 'registered_path_rule' } };
    const value = { available: true, session_pk: 1, file_instance_id: 1, after_event: 0, unit: 'physical_record', snapshot: 2, observation_snapshot: 2, scope_key: scope, current_session_project: null, records_total: 1, records_observed: 1, records_unavailable: 0, observations_complete: true, next_after_event: null, records: [record] };
    const parsed = parseFragmentPage(value, { session: 1, source: 1, after: 0, limit: 20 });
    expect(parsed.records?.[0].assignment).toEqual(record.assignment);
    const manual = { ...record.assignment, basis: 'manual' }; delete (manual as { reason?: string }).reason;
    expect(parseFragmentPage({ ...value, records: [{ ...record, assignment: manual }] }, { session: 1, source: 1, after: 0, limit: 20 }).records?.[0].assignment?.reason).toBeUndefined();
    expect(() => parseFragmentPage({ ...value, records: [{ ...record, assignment: { ...record.assignment, reason: 7 } }] }, { session: 1, source: 1, after: 0, limit: 20 })).toThrow();
  });
});
