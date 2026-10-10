import { describe, expect, it } from 'vitest';
import { artifactMetrics } from '../src/ArtifactHealthPanel';
import { exactPathError, metadataBoolean, observationConflict, observationListIssue, observationSignature, parseVersionsPage, versionOrigin, versionsNextOffset } from '../src/versions';

describe('文件版本来源、窗口与只读分页保留证据边界', () => {
  it('工具报告即使声明确认或物理表示，也不能当成实际文件字节', () => {
    for (const version of [
      { source: 'agent_edit', representation: 'physical_file_bytes', algo: 'sha256', claim_state: 'confirmed' as const },
      { source: 'shadow_snapshot', representation: 'tool_reported_utf8', algo: 'git-sha1' },
    ]) expect(versionOrigin(version).kind).toBe('reported');
    expect(versionOrigin({ source: 'current_file', representation: 'physical_file_bytes', algo: 'sha256' }).kind).toBe('current');
    expect(versionOrigin({ source: 'shadow_snapshot', representation: 'physical_file_bytes', algo: 'git-sha1' }).kind).toBe('archived');
  });
  it('链接目标字节不提升成目标文件内容，字段缺失或矛盾保持未知', () => {
    expect(versionOrigin({ source: 'shadow_snapshot', representation: 'symlink_target_bytes', algo: 'git-sha1' }).kind).toBe('link');
    for (const version of [{}, { source: 'shadow_snapshot' }, { source: 'current_file', algo: 'git-sha1', representation: 'physical_file_bytes' }]) expect(versionOrigin(version).kind).toBe('unknown');
    expect(metadataBoolean(1)).toBe('未知');
  });
  it('错误快照关联、不同版本身份与缺缓存来源必须列明矛盾', () => {
    const version = { source: 'current_file', version_id: 'v' };
    expect(observationConflict(version, { snapshot_id: 12 })).not.toBeNull();
    expect(observationConflict(version, { snapshot_id: null, version_id: 'foreign' })).not.toBeNull();
    expect(observationConflict(version, { snapshot_id: null, cache_reused: true, cached_from: null })).not.toBeNull();
    expect(observationConflict(version, { snapshot_id: null, version_id: 'v', cache_reused: true, cached_from: 'old-observation' })).toBeNull();
  });
  it('真实纳秒签名经 JSON 解析会损失末位，不能把舍入结果显示为原记录', () => {
    const encoded = '[2049,98213,6000001,1791619260123456789,1791619260987654321]';
    const parsed: number[] = JSON.parse(encoded);
    expect(JSON.stringify(parsed)).not.toBe(encoded);
    expect(observationSignature(parsed)).toContain('无法在此精确展示');
    expect(observationSignature(parsed)).not.toContain(String(parsed[3]));
    const safe = [2049, 98213, 6000001, Number.MAX_SAFE_INTEGER];
    expect(observationSignature(safe)).toBe(JSON.stringify(safe));
    for (const invalid of [null, {}, ['1791619260123456789'], [1.25], [Infinity]]) expect(observationSignature(invalid)).toBe('未知');
  });
  it('精确绝对路径保留空格与元字符，不把相对或超字节路径送出', () => {
    for (const path of ['', '/synthetic/带 空格;$(not-run).txt ', 'D:\\合成\\文件.txt', '\\\\server\\share\\file']) expect(exactPathError(path)).toBeNull();
    expect(exactPathError('relative.txt')).not.toBeNull();
    expect(exactPathError(`/${'字'.repeat(1366)}`)).not.toBeNull();
  });
  it('拒绝其他项目与错误分页回包，保留未知列表和未知总数', () => {
    expect(() => parseVersionsPage({ versions: [{ project_id: 'other' }] }, 'p', 0, 25)).toThrow(/归属/);
    expect(() => parseVersionsPage({ offset: 25 }, 'p', 0, 25)).toThrow(/分页位置/);
    expect(() => parseVersionsPage({ limit: 50 }, 'p', 0, 25)).toThrow(/分页大小/);
    expect(() => parseVersionsPage({ versions: Array(26).fill({ project_id: 'p' }) }, 'p', 0, 25)).toThrow(/上限/);
    expect(parseVersionsPage({}, 'p', 0, 25)).toMatchObject({ total: undefined, versions: undefined, partial: undefined });
  });
  it('下一页只按已知且一致的分页计算，缺失不能猜为末页或下一页', () => {
    const page = parseVersionsPage({ offset: 0, limit: 25, total: 33, versions: [{ project_id: 'p' }] }, 'p', 0, 25);
    expect(versionsNextOffset(page, 0)).toBe(25);
    expect(versionsNextOffset({ ...page, offset: 25 }, 25)).toBeNull();
    for (const missing of [null, {}, { ...page, total: undefined }, { ...page, versions: undefined }, { ...page, offset: 1 }]) expect(versionsNextOffset(missing, 0)).toBeNull();
  });
  it('缺失、空值与错误类型项目归属不能由当前筛选补猜', () => {
    for (const record of [{}, { project_id: '' }, { project_id: null }, { project_id: 12 }]) expect(() => parseVersionsPage({ versions: [record] }, 'p', 0, 25)).toThrow(/归属缺失/);
    expect(parseVersionsPage({ versions: [{ project_id: 'p' }] }, 'p', 0, 25).versions).toHaveLength(1);
  });
  it('观察条数、总数与完整性矛盾不能成为完整记录，上限仍三条', () => {
    expect(observationListIssue({ observations: [{}, {}, {}], observations_total: 4, observations_partial: true })).toBeNull();
    for (const version of [
      { observations: [{}], observations_total: 0 },
      { observations: [{}], observations_total: 4, observations_partial: false },
      { observations: [{}], observations_total: 1, observations_partial: true },
    ]) expect(observationListIssue(version)).toContain('不一致');
    expect(observationListIssue({ observations: [{}, {}, {}, {}], observations_total: 4 })).toContain('超过 3 条');
    expect(observationListIssue({ observations: [] })).toContain('未知');
  });
  it('健康版本、观察、任务与快照数量分别保留，缺失不能补零', () => {
    const data = { versions: 33, archived: 2, current_hashed: 1, cache_reused: 3,
      jobs: { queued: 1, running: 1, paused: 1, failed: 1, done: 6 }, discovery: { pending: 1, unknown: 1, partial: 1, done: 1 } };
    const metrics = artifactMetrics(data);
    expect(Object.fromEntries(metrics.versions.map(item => [item.key, item.value]))).toEqual({ versions: 33, archived: 2, current_hashed: 1, cache_reused: 3 });
    expect(Object.fromEntries(metrics.jobs.map(item => [item.key, item.value]))).toEqual(data.jobs);
    expect(Object.fromEntries(metrics.discovery.map(item => [item.key, item.value]))).toEqual(data.discovery);
    expect(Object.values(artifactMetrics(undefined)).flat().every(item => item.value === null)).toBe(true);
    expect(artifactMetrics({ versions: 0 }).versions[0].value).toBe(0);
  });
});
