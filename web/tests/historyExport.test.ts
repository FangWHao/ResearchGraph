import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiArchive, ApiError, setToken } from '../src/api';
import { downloadArchive, emptyExportDraft, exportFilename, exportIntentKey, exportRequest } from '../src/historyExport';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
describe('历史包按明确条件生成，不丢失错误或隐私选择', () => {
  it('默认不含正文，所有范围与明确未知范围不同，不能借用网页投影', () => {
    const draft = emptyExportDraft();
    expect(exportRequest('project-a', draft, 7)).toEqual({ project_id: 'project-a', expected_revision: 7, include_evidence: false });
    expect(exportRequest('project-a', { ...draft, scopeMode: 'unknown' }, 7)).toHaveProperty('scope', null);
    for (const revision of [null, -1, 1.5, Number.MAX_SAFE_INTEGER + 1]) expect(() => exportRequest('project-a', draft, revision)).toThrow();
    expect(() => exportRequest('', draft, 7)).toThrow();
  });
  it('完整范围按每个字段和值选择，空范围、重复字段不会冒充完整', () => {
    const draft = { ...emptyExportDraft(), scopeMode: 'exact' as const, scopeText: 'data=v1\nstep=计算' };
    expect(exportRequest('p', draft, 7).scope).toEqual({ data: 'v1', step: '计算' });
    for (const scopeText of ['', 'data=', 'data=v1\ndata=v2']) expect(() => exportRequest('p', { ...draft, scopeText }, 7)).toThrow();
    expect(exportRequest('p', { ...draft, scopeMode: 'all' }, 7)).not.toHaveProperty('scope');
  });
  it('双截止保留显式时区，拒绝无时区及不存在的日期', () => {
    const draft = { ...emptyExportDraft(), occurredUntil: '2024-02-29T12:00:00+08:00', knownUntil: '2026-10-10T04:00:00Z' };
    expect(exportRequest('p', draft, 7)).toMatchObject({ occurred_until: draft.occurredUntil, known_until: draft.knownUntil });
    for (const occurredUntil of ['2026-10-10T12:00', '2026-02-30T12:00Z', '2025-02-29T12:00Z', '2026-10-10T24:00Z', '2026-13-01T12:00Z']) expect(() => exportRequest('p', { ...draft, occurredUntil }, 7)).toThrow('有效的 ISO');
  });
  it('遮盖规则保留原话，由服务端判断语法，数量和UTF8总量受限', () => {
    const draft = { ...emptyExportDraft(), includeEvidence: true, patternsText: '  ZY\\d+\n\n(?i)SAMPLE-\\d+' };
    expect(exportRequest('p', draft, 7)).toMatchObject({ include_evidence: true, redact_patterns: ['  ZY\\d+', '(?i)SAMPLE-\\d+'] });
    for (const patternsText of ['a\n'.repeat(33), 'a'.repeat(1001), Array.from({ length: 32 }, () => '号'.repeat(1000)).join('\n')]) expect(() => exportRequest('p', { ...draft, patternsText }, 7)).toThrow();
    expect(Object.keys(exportRequest('p', draft, 7))).not.toEqual(expect.arrayContaining(['path', 'output', 'model', 'endpoint', 'key']));
  });
  it('项目或阅读/隐私条件改变使旧下载意图失效，文件名不接受路径', () => {
    const draft = emptyExportDraft(); const key = exportIntentKey('p', draft);
    for (const update of [{ occurredUntil: '2026-10-10T00:00Z' }, { knownUntil: '2026-10-10T00:00Z' }, { scopeMode: 'unknown' as const }, { scopeText: 'data=v2' }, { includeEvidence: true }, { patternsText: 'ZY\\d+' }]) expect(exportIntentKey('p', { ...draft, ...update })).not.toBe(key);
    expect(exportIntentKey('other', draft)).not.toBe(key);
    expect(exportFilename('../名字/对象', 7)).not.toMatch(/[\/\\]/);
  });
  it('ZIP下载沿现有令牌和AbortSignal，只把有效ZIP响应当文件', async () => {
    vi.stubGlobal('sessionStorage', { setItem: vi.fn() }); setToken('synthetic-unit-token');
    const fetch = vi.fn().mockResolvedValue(new Response(new Uint8Array([0x50, 0x4b, 3, 4, 1]), { headers: { 'Content-Type': 'application/zip' } })); vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    expect((await apiArchive({ project_id: 'p' }, controller.signal)).size).toBe(5);
    expect(fetch).toHaveBeenCalledWith('/api/exports', expect.objectContaining({ method: 'POST', headers: { Authorization: 'Bearer synthetic-unit-token', 'Content-Type': 'application/json' }, signal: controller.signal, cache: 'no-store' }));
  });
  it('400/401/409保留诊断，200错误内容类型或坏ZIP不能静默下载', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    for (const status of [400, 401, 409]) {
      fetch.mockResolvedValueOnce(new Response(JSON.stringify({ error: '合成导出诊断' }), { status, headers: { 'Content-Type': 'application/json' } }));
      await expect(apiArchive({ project_id: 'p' })).rejects.toMatchObject({ status, message: '合成导出诊断' });
    }
    fetch.mockResolvedValueOnce(new Response('bad gateway', { status: 503 }));
    await expect(apiArchive({ project_id: 'p' })).rejects.toBeInstanceOf(ApiError);
    fetch.mockResolvedValueOnce(new Response('<html>错误</html>', { headers: { 'Content-Type': 'text/html' } }));
    await expect(apiArchive({ project_id: 'p' })).rejects.toThrow('文件类型异常');
    fetch.mockResolvedValueOnce(new Response('not zip', { headers: { 'Content-Type': 'application/zip' } }));
    await expect(apiArchive({ project_id: 'p' })).rejects.toThrow('压缩包格式异常');
  });
  it('临时下载URL定时回收，提前离开或点击失败也只回收一次', () => {
    vi.useFakeTimers(); const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:synthetic-export'); const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const anchor = { href: '', download: '', click: vi.fn(), remove: vi.fn() }; const append = vi.fn();
    vi.stubGlobal('document', { createElement: vi.fn().mockReturnValue(anchor), body: { append } });
    const dispose = downloadArchive(new Blob(['zip']), '合成.zip');
    expect(create).toHaveBeenCalledOnce(); expect(anchor.click).toHaveBeenCalledOnce(); expect(anchor.remove).toHaveBeenCalledOnce(); expect(revoke).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000); dispose(); expect(revoke).toHaveBeenCalledOnce();
    const early = downloadArchive(new Blob(['zip']), '合成.zip'); early(); vi.advanceTimersByTime(1000); expect(revoke).toHaveBeenCalledTimes(2);
    anchor.click.mockImplementationOnce(() => { throw new Error('合成浏览器拒绝'); });
    expect(() => downloadArchive(new Blob(['zip']), '合成.zip')).toThrow('合成浏览器拒绝'); expect(revoke).toHaveBeenCalledTimes(3);
  });
});
