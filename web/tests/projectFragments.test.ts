import { describe, expect, it } from 'vitest';
import { appendFragmentDependencies, assertFragmentContinuation, canAssignFragments, fragmentDependencyGroups, fragmentSelection, parseFragmentDependencies, parseFragmentPage, parseFragmentReceipt, selectedFragmentRange } from '../src/projectFragments';
import type { FragmentDependencies, FragmentPage, PhysicalFragment } from '../src/projectFragments';
const record = (id: number, start: number, events = 1): PhysicalFragment => ({ first_event_id: id, byte_start: start, byte_end: start + 30, object_sha256: 'a'.repeat(64), first_seq: id, last_seq: id + events - 1, event_count: events, has_user_turn: true, observation_state: 'absent', observed_at: null, context: { state: 'declared', source: 'codex_turn_context', cwd: '/public/project', cwd_sha256: 'b'.repeat(64), reason: 'native', origin_event_id: 1 }, project_hint: { state: 'candidate_path', basis: 'path_hint', projects: ['project-b'], matching_root_ids: ['root-b'], candidates_partial: false, reason: 'registered_path_hint' }, assignment: { project_id: 'project-a', logical_session_pk: 1, basis: 'session_manual', partition_id: null } });
const records = [record(1, 0, 3), record(5, 40), record(6, 70)];
const page = (): FragmentPage => ({ available: true, revision: 9, session_pk: 1, file_instance_id: 2, current_session_project: 'project-a', assignments_applied: true, assignment_supported: true, unit: 'physical_record', snapshot: 6, observation_snapshot: 9, scope_key: 'c'.repeat(64), records_total: 3, records_observed: 3, records_unavailable: 0, observations_complete: true, after_event: 0, next_after_event: null, records });
const expected = { session: 1, source: 2, after: 0, limit: 20 };

describe('物理片段完整范围与只读线索', () => {
  it('多块事件编号不连续仍按物理记录选连续范围，不把路径候选当归属', () => {
    expect(fragmentSelection(records, 1, 6)).toEqual([1, 5, 6]);
    expect(fragmentSelection(records, 6, 1)).toEqual([1, 5, 6]);
    expect(selectedFragmentRange(records, [6, 1, 5])).toMatchObject({ first: records[0], last: records[2], records: 3, events: 5 });
    expect(parseFragmentPage(page(), expected).records?.[0].assignment?.project_id).toBe('project-a');
    expect(parseFragmentPage(page(), expected).current_session_project).toBe('project-a');
  });
  it('拒绝不连续、跨页、重复或伪造事件范围', () => {
    for (const ids of [[1, 6], [1, 1], [99], []]) expect(selectedFragmentRange(records, ids)).toBeNull();
    expect(fragmentSelection(records, 99, 5)).toEqual([5]);
    expect(fragmentSelection(records, 1, 99)).toEqual([]);
  });
  it('旧后端仅只读，缺字段不能补造零修订或已接通归属', () => {
    const old = page(); delete old.revision; delete old.assignments_applied; delete old.assignment_supported;
    expect(canAssignFragments(parseFragmentPage(old, expected))).toBe(false);
    expect(canAssignFragments({ available: false, reason: 'legacy_schema' })).toBe(false);
    expect(canAssignFragments({ ...page(), assignments_applied: false })).toBe(true);
    expect(canAssignFragments({ ...page(), assignment_supported: false })).toBe(false);
  });
  it('拒绝错误来源、锚点、重叠字节、重复首事件、原件摘要和不完整记录计数', () => {
    const invalid = [{ ...page(), session_pk: 8 }, { ...page(), file_instance_id: 3 }, { ...page(), after_event: 5 }, { ...page(), next_after_event: 1 }, { ...page(), records_observed: 4 }, { ...page(), observations_complete: false }, { ...page(), records: [record(1, 0), record(5, 20)] }, { ...page(), records: [record(1, 0), record(1, 40)] }, { ...page(), records: [{ ...record(1, 0), object_sha256: null }] }];
    for (const raw of invalid) expect(() => parseFragmentPage(raw, expected)).toThrow();
  });
  it('两种截止、归属摘要、修订、总数任一变动都拒绝混页', () => {
    assertFragmentContinuation(page(), { ...page(), after_event: 6, records: [] });
    for (const key of ['snapshot', 'observation_snapshot', 'revision', 'records_total', 'scope_key', 'assignments_applied', 'current_session_project'] as const) {
      const changed = { ...page(), [key]: key === 'scope_key' ? 'd'.repeat(64) : key === 'current_session_project' ? 'other' : key === 'assignments_applied' ? false : 10 };
      expect(() => assertFragmentContinuation(page(), changed)).toThrow();
    }
  });
  it('回执核对原业务身份，重放旧修订不被误拒绝或冒充新对象', () => {
    const intent = { request_id: 'uuid', session: 1, source: 2, first_event: 1, last_event: 6, project_id: null, actor: 'human:合成' };
    const receipt = { request_id: 'uuid', revision: 4, replayed: true, assignments_applied: true, session_pk: 1, file_instance_id: 2, first_event_id: 1, last_event_id: 6, project_id: null, records_assigned: 3, partitions_created: 1 };
    expect(parseFragmentReceipt(receipt, intent, 3).revision).toBe(4);
    for (const change of [{ project_id: 'other' }, { first_event_id: 5 }, { request_id: 'other' }, { records_assigned: 2 }, { assignments_applied: false }]) expect(() => parseFragmentReceipt({ ...receipt, ...change }, intent, 3)).toThrow();
  });
});

const dependencyRecord = (id: number, start: number) => ({ first_event_id: id, session_pk: 3, file_instance_id: 4, byte_start: start, byte_end: start + 30, project_id: 'project-a' });
const dependency = (): FragmentDependencies => ({ revision: 9, scope_key: 'c'.repeat(64), snapshot: 6, observation_snapshot: 9, plan_sha256: 'e'.repeat(64), complete: true, closure_closed: false, assignment_available: false, write_mode: 'dependency_migration', selected_records: 1, dependencies: { entities: 1, claims: 2, review_actions: 1, extraction_runs: 1 }, shared_context: { project_id: 1, session_pk: 2 }, required_records: [dependencyRecord(8, 0)], required_records_total: 2, next_offset: 1, issues: [], migration_applied: false });
describe('科研依赖只读预检', () => {
  it('保留已确认审核和模型上下文依赖，不把核对完成当已迁移或可直接归属', () => {
    const result = parseFragmentDependencies(dependency(), page(), 1, 0, 1);
    expect(result).toMatchObject({ complete: true, assignment_available: false, write_mode: 'dependency_migration', migration_applied: false, dependencies: { claims: 2, review_actions: 1, extraction_runs: 1 } });
    const direct = { ...dependency(), required_records: [], required_records_total: 0, next_offset: null, closure_closed: true, dependencies: {}, assignment_available: true, write_mode: 'direct' };
    expect(parseFragmentDependencies(direct, page(), 1, 0, 20).assignment_available).toBe(true);
  });
  it('来源、修订、双截止、选定范围与分页损坏不能冒充当前预检', () => {
    for (const change of [{ revision: 10 }, { scope_key: 'd'.repeat(64) }, { snapshot: 7 }, { observation_snapshot: 10 }, { selected_records: 2 }, { plan_sha256: 'not-a-hash' }, { next_offset: null }, { required_records: [] }, { dependencies: { claims: -1 } }, { required_records: [{ ...dependencyRecord(8, 0), byte_end: 0 }] }, { migration_applied: true }]) expect(() => parseFragmentDependencies({ ...dependency(), ...change }, page(), 1, 0, 1)).toThrow();
  });
  it('有缺口只保留诊断，不补造没有依赖，也不允许不完整计划直接保存', () => {
    const unknown = { ...dependency(), complete: false, write_mode: 'unverifiable', issues: ['unverifiable_json_reference'] };
    expect(parseFragmentDependencies(unknown, page(), 1, 0, 1)).toMatchObject({ complete: false, assignment_available: false, dependencies: { claims: 2 }, issues: ['unverifiable_json_reference'] });
    for (const change of [{ assignment_available: true }, { write_mode: 'direct' }, { closure_closed: true }, { issues: [] }]) expect(() => parseFragmentDependencies({ ...unknown, ...change }, page(), 1, 0, 1)).toThrow();
  });
  it('续页固定计划并累计完整记录，摘要改变、重复、交叠或计数改变都停止混页', () => {
    const first = dependency(), next = { ...dependency(), required_records: [dependencyRecord(9, 30)], next_offset: null };
    const result = appendFragmentDependencies(first, parseFragmentDependencies(next, page(), 1, 1, 1));
    expect(result.required_records.map(r => r.first_event_id)).toEqual([8, 9]); expect(result.next_offset).toBeNull();
    for (const change of [{ plan_sha256: 'f'.repeat(64) }, { revision: 10 }, { scope_key: 'd'.repeat(64) }, { dependencies: { claims: 3 } }, { required_records: [dependencyRecord(8, 30)] }, { required_records: [dependencyRecord(9, 10)] }]) expect(() => appendFragmentDependencies(first, { ...next, ...change })).toThrow();
  });
  it('每类依赖计数独立保留，未来未知类别不被静默丢弃或加为科学总数', () => {
    const values = { entities: 2, claims: 3, review_actions: 1, run_io: 4, future_dependency: 5 };
    const groups = fragmentDependencyGroups(values);
    expect(Object.fromEntries(groups.flatMap(group => group.items.map(item => [item.key, item.count])))).toEqual(values);
  });
});
