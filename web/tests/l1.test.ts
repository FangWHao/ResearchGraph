import { describe, expect, it } from 'vitest';
import { editPresentation, eventNumber, runStateText, versionMetadata } from '../src/l1';
import type { L1Edit } from '../src/types';

function edit(fields: Partial<L1Edit> = {}): L1Edit {
  return { edit_id: 'synthetic-edit', project_id: 'synthetic', session_pk: 1, call_id: 'edit-call',
    request_event_id: 1, result_event_id: 2, path: '/synthetic/result.txt', root_id: 'root', operation: 'edit',
    patch_sha256: 'synthetic-patch', before_version: 'before', after_version: 'after', gap: null,
    user_modified: null, occurred_at: null, recorded_at: '2026-10-09T00:00:00Z',
    diff: { available: true, format: 'reported_versions', text: '-old\n+new\n', complete_versions: true, gap: null, reason: '合成候选文本' },
    ...fields,
  };
}

describe('运行与编辑证据不超出工具观测', () => {
  it('事件编号不接受指数、小数或超出安全整数的输入，避免定位到错误事件', () => {
    expect(eventNumber('21')).toBe(21);
    expect(eventNumber(' 21 ')).toBe(21);
    for (const value of ['', '0', '-1', '1e2', '1.2', '9007199254740993', '<script>']) expect(eventNumber(value)).toBeNull();
  });
  it('缺少结束信息不判失败，非结束状态不采用附带退出码', () => {
    expect(runStateText('requested', null)).toBe('已请求');
    expect(runStateText('started', null)).toBe('已有启动记录');
    expect(runStateText('unknown', 0)).toBe('运行状态未知');
    expect(runStateText('new_executor_state', 2)).toBe('运行状态未知');
    expect(runStateText('exited', null)).toBe('已结束 · 退出码 未知');
    expect(runStateText('exited', 0)).toBe('已结束 · 退出码 0');
    expect(runStateText('exited', 2)).toBe('已结束 · 退出码 2');
  });

  it('reported_versions 需要明确完整标记和双版本身份，仍只作为待复核文本', () => {
    expect(editPresentation(edit())).toMatchObject({ kind: 'reported_versions', title: '工具报告版本差异 · 待复核', text: '-old\n+new\n' });
    expect(editPresentation(edit({ before_version: null }))).toMatchObject({ kind: 'unavailable', text: null });
    expect(editPresentation(edit({ diff: { ...edit().diff, complete_versions: false } }))).toMatchObject({ kind: 'unavailable', text: null });
  });

  it('仅补丁即使接口附带完整标记也不能被提升成完整历史版本', () => {
    const patch = edit({ before_version: null, after_version: null, gap: 'preimage_unknown',
      diff: { available: true, format: 'patch_only', text: '*** Update File: result.txt\n-old\n+new', complete_versions: true, gap: 'preimage_unknown', reason: '合成补丁' } });
    expect(editPresentation(patch)).toMatchObject({ kind: 'patch_only', title: '仅有补丁' });
    expect(editPresentation(patch).reason).toContain('不能代表完整文件内容');
  });

  it('补丁表示保留已登记的单侧候选版本，不抹掉删除前或新增后报告', () => {
    const patch = { available: true, format: 'patch_only' as const, text: '*** Delete File: result.txt', complete_versions: false, gap: null, reason: '合成补丁' };
    const before = editPresentation(edit({ after_version: null, diff: patch }));
    expect(before).toMatchObject({ kind: 'patch_only', text: patch.text });
    expect(before.reason).toContain('编辑前候选'); expect(before.reason).not.toContain('编辑前后完整版本未知');
    const after = editPresentation(edit({ before_version: null, diff: patch }));
    expect(after.reason).toContain('编辑后候选'); expect(after.reason).not.toContain('编辑前后完整版本未知');
  });

  it('明确单侧格式保留候选全文、空文本与另一侧未知，不补造空文件差异', () => {
    const text = '合成工具报告\n<script>window.nativePatchInjected=true</script>\n正文\u2028保留';
    const before = edit({ after_version: null, diff: { available: true, format: 'reported_before', text, complete_versions: false, gap: 'postimage_unknown', reason: '合成单侧报告' } });
    const after = edit({ before_version: null, diff: { ...before.diff, format: 'reported_after' } });
    expect(editPresentation(before)).toMatchObject({ kind: 'reported_before', text });
    expect(editPresentation(after)).toMatchObject({ kind: 'reported_after', text });
    expect(editPresentation({ ...before, diff: { ...before.diff, text: '' } })).toMatchObject({ kind: 'reported_before', text: '' });
    expect(editPresentation({ ...after, diff: { ...after.diff, text: '' } })).toMatchObject({ kind: 'reported_after', text: '' });
    expect(before.after_version).toBeNull(); expect(after.before_version).toBeNull();
  });

  it('单侧格式与版本身份或完整标记矛盾时不显示错误方向的全文', () => {
    for (const format of ['reported_before', 'reported_after'] as const) {
      const diff = { available: true, format, text: '不可误认的全文', complete_versions: false, gap: null, reason: '合成单侧报告' };
      const valid = edit({ diff, before_version: format === 'reported_before' ? 'before' : null, after_version: format === 'reported_after' ? 'after' : null });
      for (const invalid of [edit({ diff }), { ...valid, before_version: null, after_version: null }, { ...valid, before_version: valid.after_version, after_version: valid.before_version }, { ...valid, diff: { ...diff, complete_versions: true } }, { ...valid, diff: { ...diff, complete_versions: undefined } }]) {
        expect(editPresentation(invalid)).toMatchObject({ kind: 'unavailable', text: null });
      }
      expect(editPresentation({ ...valid, diff: { ...diff, available: false, reason: '超限，不返回部分全文' } })).toMatchObject({ kind: 'unavailable', text: null });
    }
  });

  it('超限或无表示信息不展示正文，也不由版本身份补造差异', () => {
    expect(editPresentation(edit({ diff: { available: false, text: '不应展示的超限内容', gap: null, reason: '差异超过展示上限' } })))
      .toMatchObject({ kind: 'unavailable', text: null, reason: '差异超过展示上限' });
    expect(editPresentation(edit({ diff: { available: true, text: '无类型正文', gap: null, reason: '表示未知' } })))
      .toMatchObject({ kind: 'unavailable', text: null });
  });

  it('直接记录依据不替代审核状态，旧版本元数据缺失仍未知', () => {
    const version = { version_id: 'v', path: '/synthetic/result.txt', algo: 'sha256:tool-utf8', digest: 'synthetic', source: 'agent_edit' };
    expect(versionMetadata({ ...version, phase: 'before', basis: 'direct_record', claim_state: 'candidate', representation: 'tool_reported_utf8' }))
      .toEqual({ phase: '编辑前', review: '待复核', basis: '直接记录', representation: '工具报告的 UTF-8 文本' });
    expect(versionMetadata(version)).toEqual({ phase: '阶段未知', review: '审核状态未知', basis: '依据未知', representation: '版本表示未知' });
  });
});
