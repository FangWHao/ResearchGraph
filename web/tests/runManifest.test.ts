import { describe, expect, it } from 'vitest';
import { manifestPage, manifestPresentation, manifestVersion, reportedExit, reportedRole } from '../src/runManifest';
import type { L1Run } from '../src/types';

const native = { run_id: 'run-a', project_id: 'project-a', state: 'exited', exit_code: 0 } as L1Run;
const report = { run_id: 'run-a', project_id: 'project-a', claim_state: 'candidate', basis: 'direct_record',
  binding_state: 'native_request', actual_io_completeness: 'unknown', reported_exit_conflicts_with_native: false,
  unknown_fields: ['environment'], reported: { exit_code: 0, environment: null, seed: '9007199254740993123456789', parameters: { script: '<script>坏命令</script>' } } };
const io = { role: 'inputs', direction: 'in', requested_version_id: 'v-a', resolved_version_id: 'v-a', resolution: 'visible_version',
  association_state: 'reported_only', claim_state: 'candidate', basis: 'direct_record',
  version: { version_id: 'v-a', project_id: 'project-a', source: 'shadow_snapshot', algo: 'git-sha1', representation: 'physical_file_bytes', claim_state: 'candidate' } };

describe('运行清单不能替代原生运行事实', () => {
  it('旧接口缺字段不同于已知空清单，两个有限分页均保留遗漏', () => {
    expect(manifestPage(undefined, 20)).toMatchObject({ missing: true, partial: true, total: null });
    expect(manifestPage({ items: [], total: 0, offset: 0, next_offset: null }, 20)).toMatchObject({ missing: false, partial: false, total: 0 });
    expect(manifestPage({ items: Array.from({ length: 20 }, () => ({})), total: 21, offset: 0, next_offset: 20 }, 20)).toMatchObject({ partial: true, nextOffset: 20, issues: [] });
    expect(manifestPage({ items: Array.from({ length: 100 }, () => ({})), total: 101, offset: 0, next_offset: 100, partial: true }, 100)).toMatchObject({ partial: true, nextOffset: 100, issues: [] });
  });
  it('分页缺失、超限、总数与完整性矛盾不能读成完整', () => {
    for (const page of [
      { items: [{}], total: 0, offset: 0, next_offset: null },
      { items: [{}], total: 2, offset: 0, next_offset: null },
      { items: [{}], total: 2, offset: 0, next_offset: 0 },
      { items: [{}], total: 2, offset: 0, next_offset: 1, partial: false },
      { items: [null], total: 1, offset: 0, next_offset: null },
      { total: 0, offset: 0, next_offset: null },
    ]) { const parsed = manifestPage(page, 20); expect(parsed.partial).toBe(true); expect(parsed.issues.length).toBeGreaterThan(0); }
    const over = manifestPage({ items: Array.from({ length: 21 }, () => ({})), total: 21, offset: 0, next_offset: null }, 20);
    expect(over.items).toHaveLength(20); expect(over.partial).toBe(true);
  });
  it('角色未知、明确空列表和已报告ID分别保留，不推断实际I/O', () => {
    expect(reportedRole({ inputs: null }, 'inputs').status).toBe('unknown');
    expect(reportedRole({ inputs: [] }, 'inputs')).toMatchObject({ status: 'reported', ids: [] });
    expect(reportedRole({ inputs: ['v-a', 'v-a'] }, 'inputs').ids).toEqual(['v-a', 'v-a']);
    for (const value of [undefined, 'v-a', [null], ['']]) expect(reportedRole({ inputs: value }, 'inputs').status).toBe('invalid');
  });
  it('报告与原生退出码冲突时保留两者，错误冲突标记也须诊断', () => {
    const changed = { ...report, reported: { ...report.reported, exit_code: 2 } };
    expect(manifestPresentation(changed, native)).toMatchObject({ conflict: true, code: 2 });
    expect(manifestPresentation(changed, native).issues).toContain('退出码冲突标记与返回的原生/报告数值不一致。');
    expect(native.exit_code).toBe(0); expect(native.state).toBe('exited');
    expect(manifestPresentation({ ...report, reported_exit_conflicts_with_native: true }, native).conflict).toBe(true);
  });
  it('报告退出0不能把未知原生运行升级；报告的确认或完整字段异常保留诊断', () => {
    const unknown = { ...native, state: 'unknown', exit_code: null } as L1Run;
    expect(manifestPresentation(report, unknown)).toMatchObject({ code: 0, conflict: false }); expect(unknown.state).toBe('unknown');
    const bad = manifestPresentation({ ...report, claim_state: 'confirmed', actual_io_completeness: 'complete', project_id: 'project-b' }, native);
    expect(bad.issues).toHaveLength(3);
  });
  it('版本必须同项目且明确ID一致；存在元数据不能补造未知版本', () => {
    expect(manifestVersion(io, 'project-a')).toMatchObject({ visible: true, origin: { kind: 'archived' } });
    for (const bad of [{ ...io, requested_version_id: 'other' }, { ...io, resolved_version_id: 'other' }, { ...io, version: { ...io.version, project_id: 'project-b' } }, { ...io, resolution: 'version_unknown_or_not_visible' }, { ...io, version: { ...io.version, digest: { invalid: true } } }, { ...io, version: { ...io.version, occurred_at: [] } }]) {
      const result = manifestVersion(bad, 'project-a'); expect(result.visible).toBe(false); expect(result.version).toBeNull(); expect(result.issues.length).toBeGreaterThan(0);
    }
  });
  it('工具文本不冒充物理字节，关联仍是报告，错误方向有诊断', () => {
    const tool = manifestVersion({ ...io, role: 'outputs', direction: 'in', version: { ...io.version, source: 'agent_edit', representation: 'tool_reported_utf8', algo: 'sha256:tool-utf8' } }, 'project-a');
    expect(tool.origin?.kind).toBe('reported'); expect(tool.issues).toContain('I/O 方向与角色矛盾。');
    expect(manifestVersion({ ...io, association_state: 'actual_use' }, 'project-a').issues.length).toBeGreaterThan(0);
  });
  it('大种子与参数原话保持文本，整数精度与退出码边界不混用', () => {
    const shown = manifestPresentation(report, native);
    expect(shown.reported.seed).toBe('9007199254740993123456789'); expect(shown.reported.parameters).toEqual({ script: '<script>坏命令</script>' });
    expect(reportedExit(-(2 ** 31))).toBe(-(2 ** 31));
    for (const value of ['0', 2 ** 31, NaN, 1.5]) expect(reportedExit(value)).toBeNull();
  });
});
