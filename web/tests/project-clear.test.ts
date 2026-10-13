import { describe, expect, it } from 'vitest';
import { clearIntent, parseClearPreview, parseClearStatus, sameClearPreview } from '../src/projectClear';
import type { ClearPreview } from '../src/projectClear';

const project = '00000000-0000-4000-8000-000000000001'; const request = '00000000-0000-4000-8000-000000000002';
const preview = { project_id: project, revision: 12, rows: { raw_events: 3, claims: 5 }, objects: 3, shared_objects_retained: 1, managed_paths: 4, blockers: [], boundary: '外部资料保留', preview_sha256: 'a'.repeat(64) };
const intent = { project_id: project, request_id: request, preview_sha256: preview.preview_sha256 };
const pending = { ...preview, ...intent, state: 'pending', started_at: '2026-10-10T12:00:00+08:00' };
describe('整项目清除身份与完成证据', () => {
  it('预览只接受当前项目的完整数量和摘要，不用部分图推断删除范围', () => {
    expect(parseClearPreview(preview, project)).toEqual(preview);
    for (const broken of [null, { ...preview, project_id: request }, { ...preview, rows: { claims: -1 } }, { ...preview, objects: NaN }, { ...preview, blockers: [3] }, { ...preview, managed_paths: ['path'] }, { ...preview, preview_sha256: 'abc' }]) expect(() => parseClearPreview(broken, project)).toThrow();
  });
  it('确认名称必须完整相等，无阻碍且项目一致才建立执行意图', () => {
    expect(clearIntent(preview, project, '合成项目', '合成项目', request)).toEqual(intent);
    const invalid: [ClearPreview, string, string, string, string][] = [[preview, request, '合成项目', '合成项目', request], [preview, project, '合成项目', '合成项目 ', request], [{ ...preview, blockers: ['跨项目引用'] }, project, '合成项目', '合成项目', request], [preview, project, '', '', request], [preview, project, '合成项目', '合成项目', 'bad']];
    for (const [value, id, name, confirmation, uuid] of invalid) expect(() => clearIntent(value, id, name, confirmation, uuid)).toThrow();
  });
  it('同摘要重试可保留请求身份，另一项目或新清单不能复用意图', () => {
    expect(sameClearPreview(preview, { ...preview, revision: 13 })).toBe(true);
    expect(sameClearPreview(preview, { ...preview, project_id: request })).toBe(false);
    expect(sameClearPreview(preview, { ...preview, preview_sha256: 'b'.repeat(64) })).toBe(false);
  });
  it('挂起保留独立恢复身份，未完成不冒充完成，空闲也不代表某次成功', () => {
    expect(parseClearStatus(pending, intent).state).toBe('pending');
    expect(parseClearStatus({ state: 'idle', boundary: '外部保留' }).state).toBe('idle');
    expect(parseClearStatus({ ...pending, state: 'complete', finished_at: '2026-10-10T12:01:00+08:00' }, intent).state).toBe('complete');
    for (const broken of [{ ...pending, state: 'complete' }, { ...pending, state: 'done' }, { ...pending, request_id: project }, { ...pending, project_id: request }, { ...pending, preview_sha256: 'b'.repeat(64) }, { ...pending, started_at: 'bad' }]) expect(() => parseClearStatus(broken, intent)).toThrow();
  });
});
