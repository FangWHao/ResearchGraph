import { describe, expect, it } from 'vitest';
import { assertHookErrorContinuation, parseHookErrors } from '../src/hookErrors';
import type { HookErrorPage, HookErrorReport } from '../src/hookErrors';

const hash = 'a'.repeat(64);
function report(id: number): HookErrorReport {
  return { report_id: id, source_id: 1, byte_start: id * 10, byte_end: id * 10 + 10, line_sha256: hash,
    status: 'reported', occurred_at: '2026-10-10T00:00:00+00:00', stage: 'hook', exception_class: 'RuntimeError',
    exception_class_sha256: null, recorded_at: '2026-10-10T00:00:01+00:00' };
}
function page(offset = 0): HookErrorPage {
  return { available: true, reason: null, scope: 'data_directory', snapshot_id: 2, source_instances: 2,
    observation: { check_id: 2, source_id: 2, status: 'partial_line', source_bytes: 100,
      committed_offset: 90, report_highwater: 25, recorded_at: '2026-10-10T00:00:02+00:00' },
    counts: { records_total: 25, reported_failures: 25, unknown_records: 0, unknown_exception_classes: 0 },
    reports: Array.from({ length: offset === 0 ? 20 : 5 }, (_, index) => report(25 - offset - index)),
    offset, limit: 20, next_offset: offset === 0 ? 20 : null, complete_failure_history: false };
}
function parsed(value: HookErrorPage) {
  const result = parseHookErrors(value, { offset: value.offset, snapshot: value.snapshot_id });
  if (!('snapshot_id' in result)) throw new Error('测试响应应有观测快照');
  return result;
}

describe('钩子报告的采集未知、隐私字段与冻结分页', () => {
  it('末行未完整和历史轮换保留报告，不能将可读统计变成完整失败历史', () => {
    expect(parsed(page())).toEqual(page());
    expect(parsed({ ...page(), observation: { ...page().observation!, status: 'missing', source_id: null,
      source_bytes: null, committed_offset: null } })).toMatchObject({
      counts: { reported_failures: 25 }, observation: { status: 'missing' }, complete_failure_history: false,
    });
    expect(() => parseHookErrors({ ...page(), complete_failure_history: true }, { offset: 0 })).toThrow();
  });
  it('未观测和旧schema统计保持未知，已核对的空日志才有报告零值', () => {
    const unknown = { ...page(), available: false, reason: 'not_observed' as const, counts: null,
      source_instances: 0, reports: [], next_offset: null,
      observation: { ...page().observation!, status: 'missing' as const, source_id: null,
        source_bytes: null, committed_offset: null, report_highwater: 0 } };
    expect(parsed(unknown).counts).toBeNull();
    expect(parseHookErrors({ available: false, reason: 'legacy_schema', scope: 'data_directory' }, { offset: 0 }))
      .toEqual({ available: false, reason: 'legacy_schema', scope: 'data_directory' });
    const empty = { ...page(), reports: [], next_offset: null,
      counts: { records_total: 0, reported_failures: 0, unknown_records: 0, unknown_exception_classes: 0 },
      observation: { ...page().observation!, status: 'synced' as const, source_bytes: 0,
        committed_offset: 0, report_highwater: 0 } };
    expect(parsed(empty)).toMatchObject({ counts: { reported_failures: 0 }, complete_failure_history: false });
    expect(() => parsed({ ...unknown, counts: empty.counts })).toThrow();
  });
  it('未知日志行与未知类别分别计数，解析后不保留错误正文或任意额外字段', () => {
    const unknown: HookErrorReport = { ...report(2), status: 'unknown_record', occurred_at: null,
      stage: null, exception_class: null };
    const privateClass = { ...report(1), exception_class: null, exception_class_sha256: hash,
      raw_error: '<script>synthetic_secret()</script>', exception_message: '合成私密正文' };
    const input = { ...page(), reports: [unknown, privateClass], next_offset: null,
      counts: { records_total: 2, reported_failures: 1, unknown_records: 1, unknown_exception_classes: 1 } };
    const result = parsed(input);
    expect(result.reports).toEqual([unknown, { ...report(1), exception_class: null, exception_class_sha256: hash }]);
    expect(JSON.stringify(result)).not.toContain('合成私密正文');
    expect(() => parsed({ ...input, reports: [{ ...privateClass, exception_class: 'RuntimeError' }, unknown] })).toThrow();
  });
  it('续页必须匹配首个快照和偏移，观察及统计变化拒绝拼页', () => {
    const next = page(20);
    expect(parseHookErrors(next, { offset: 20, snapshot: 2 })).toEqual(next);
    expect(() => parseHookErrors(next, { offset: 20 })).toThrow();
    for (const patch of [{ snapshot_id: 3 }, { offset: 0 }, { scope: 'project' }])
      expect(() => parseHookErrors({ ...next, ...patch }, { offset: 20, snapshot: 2 })).toThrow();
    expect(() => assertHookErrorContinuation(page(), next)).not.toThrow();
    for (const patch of [{ source_instances: 3 }, { counts: { ...next.counts!, reported_failures: 24 } },
      { observation: { ...next.observation!, status: 'synced' as const } }])
      expect(() => assertHookErrorContinuation(page(), { ...next, ...patch })).toThrow();
    expect(() => parseHookErrors({ available: false, reason: 'legacy_schema', scope: 'data_directory' },
      { offset: 20, snapshot: 2 })).toThrow();
  });
  it('遗漏、重复报告、混乱来源位置和未来报告不能冒充完整页', () => {
    for (const patch of [{ next_offset: null }, { next_offset: 21 }, { reports: [report(1), report(1)] },
      { reports: [report(26)] }, { reports: [{ ...report(25), byte_end: 1 }] },
      { observation: { ...page().observation!, committed_offset: 101 } }])
      expect(() => parseHookErrors({ ...page(), ...patch }, { offset: 0 })).toThrow();
  });
  it('非法数字、摘要、异常类别和缺字段不被当作零值或可信类别', () => {
    for (const patch of [{ source_instances: -1 }, { counts: null }, { limit: 200 }, { reports: [{ ...report(25),
      line_sha256: hash + '\n' }] }, { reports: [{ ...report(25), exception_class: 'RuntimeError\n' }] },
      { reports: [{ ...report(25), exception_class: '<script>synthetic()</script>' }] }])
      expect(() => parseHookErrors({ ...page(), ...patch }, { offset: 0 })).toThrow();
    const { unknown_records: _missing, ...counts } = page().counts!;
    expect(() => parseHookErrors({ ...page(), counts }, { offset: 0 })).toThrow();
  });
});
