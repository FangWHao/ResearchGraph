export type HookCollectionState = 'synced' | 'partial_line' | 'backlog' | 'oversized_line'
  | 'missing' | 'unsafe' | 'read_error' | 'changed_during_read';
export type HookErrorCounts = {
  records_total: number; reported_failures: number; unknown_records: number; unknown_exception_classes: number;
};
export type HookErrorObservation = {
  check_id: number; source_id: number | null; status: HookCollectionState;
  source_bytes: number | null; committed_offset: number | null; report_highwater: number; recorded_at: string;
};
export type HookErrorReport = {
  report_id: number; source_id: number; byte_start: number; byte_end: number; line_sha256: string;
  status: 'reported' | 'unknown_record'; occurred_at: string | null; stage: 'hook' | null;
  exception_class: string | null; exception_class_sha256: string | null; recorded_at: string;
};
export type HookErrorPage = {
  available: boolean; reason: null | 'not_observed'; scope: 'data_directory'; snapshot_id: number;
  source_instances: number; observation: HookErrorObservation | null; counts: HookErrorCounts | null;
  reports: HookErrorReport[]; offset: number; limit: number; next_offset: number | null;
  complete_failure_history: false;
};
export type HookErrorResult = HookErrorPage | { available: false; reason: 'legacy_schema'; scope: 'data_directory' };
export type HookErrorRequest = { offset: number; snapshot?: number };

export const hookCollectionNames: Record<HookCollectionState, string> = {
  synced: '已读到本次日志末尾', partial_line: '末行尚未写完整', backlog: '仍有待采集记录',
  oversized_line: '遇到超限行，后续尚未采集', missing: '本次未找到日志',
  unsafe: '日志不能安全读取', read_error: '本次读取失败', changed_during_read: '读取期间日志变化',
};
export const hookCountNames = [
  ['reported_failures', '已记录钩子失败报告'], ['records_total', '已采集错误日志行'],
  ['unknown_records', '无法识别的日志行'], ['unknown_exception_classes', '报告中的未知异常类别'],
] as const;

const invalid = () => new Error('钩子报告响应格式或读取快照不一致，不能据此判断失败是否完整。');
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  return value as Record<string, unknown>;
}
function integer(value: unknown, minimum = 0): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum) throw invalid();
  return value;
}
function nullableInteger(value: unknown, minimum = 0) { return value === null ? null : integer(value, minimum); }
function text(value: unknown): string {
  if (typeof value !== 'string' || !value) throw invalid();
  return value;
}
function nullableText(value: unknown) { return value === null ? null : text(value); }
function sha(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value) || value.length !== 64) throw invalid();
  return value;
}

export function parseHookErrors(value: unknown, expected: HookErrorRequest): HookErrorResult {
  if (expected.offset > 0 && expected.snapshot == null) throw invalid();
  const data = object(value);
  if (typeof data.available !== 'boolean' || data.scope !== 'data_directory') throw invalid();
  if (!data.available && data.reason === 'legacy_schema') {
    if (expected.offset !== 0 || expected.snapshot != null) throw invalid();
    return { available: false, reason: 'legacy_schema', scope: 'data_directory' };
  }
  if (data.reason !== (data.available ? null : 'not_observed') || data.complete_failure_history !== false) throw invalid();
  const snapshot = integer(data.snapshot_id);
  const offset = integer(data.offset);
  const limit = integer(data.limit, 1);
  if (offset !== expected.offset || limit !== 20 || (expected.snapshot != null && snapshot !== expected.snapshot)) throw invalid();
  const sources = integer(data.source_instances);
  let counts: HookErrorCounts | null = null;
  if (data.available) {
    const raw = object(data.counts);
    counts = { records_total: integer(raw.records_total), reported_failures: integer(raw.reported_failures),
      unknown_records: integer(raw.unknown_records), unknown_exception_classes: integer(raw.unknown_exception_classes) };
    if (sources < 1 || counts.reported_failures + counts.unknown_records !== counts.records_total
      || counts.unknown_exception_classes > counts.reported_failures) throw invalid();
  } else if (data.counts !== null || sources !== 0) throw invalid();
  let observation: HookErrorObservation | null = null;
  if (data.observation !== null) {
    const raw = object(data.observation);
    const status = text(raw.status);
    if (!Object.hasOwn(hookCollectionNames, status) || integer(raw.check_id, 1) !== snapshot) throw invalid();
    observation = { check_id: snapshot, source_id: nullableInteger(raw.source_id, 1), status: status as HookCollectionState,
      source_bytes: nullableInteger(raw.source_bytes), committed_offset: nullableInteger(raw.committed_offset),
      report_highwater: integer(raw.report_highwater), recorded_at: text(raw.recorded_at) };
    if (observation.source_bytes !== null && observation.committed_offset !== null
      && observation.committed_offset > observation.source_bytes) throw invalid();
  } else if (snapshot !== 0 || data.available) throw invalid();
  if (!Array.isArray(data.reports) || data.reports.length > limit) throw invalid();
  const reports: HookErrorReport[] = data.reports.map(value => {
    const raw = object(value);
    if (raw.status !== 'reported' && raw.status !== 'unknown_record') throw invalid();
    const report: HookErrorReport = { report_id: integer(raw.report_id, 1), source_id: integer(raw.source_id, 1),
      byte_start: integer(raw.byte_start), byte_end: integer(raw.byte_end, 1), line_sha256: sha(raw.line_sha256),
      status: raw.status, occurred_at: nullableText(raw.occurred_at), stage: raw.stage === null ? null : 'hook',
      exception_class: nullableText(raw.exception_class),
      exception_class_sha256: raw.exception_class_sha256 === null ? null : sha(raw.exception_class_sha256),
      recorded_at: text(raw.recorded_at) };
    if ((raw.stage !== null && raw.stage !== 'hook') || report.byte_end <= report.byte_start
      || !observation || report.report_id > observation.report_highwater
      || (report.exception_class !== null && (/^[A-Za-z_][A-Za-z_0-9]*$/.exec(report.exception_class)?.[0] !== report.exception_class
        || report.exception_class.length > 128 || report.exception_class_sha256 !== null))
      || (report.status === 'reported' && (report.stage !== 'hook' || report.occurred_at === null
        || (report.exception_class === null && report.exception_class_sha256 === null)))
      || (report.status === 'unknown_record' && (report.occurred_at !== null || report.stage !== null
        || report.exception_class !== null || report.exception_class_sha256 !== null))) throw invalid();
    return report;
  });
  const total = counts?.records_total ?? 0;
  const next = data.next_offset === null ? null : integer(data.next_offset, 1);
  if (reports.some((item, index) => index > 0 && item.report_id >= reports[index - 1].report_id)
    || (!data.available && reports.length !== 0) || offset + reports.length > Math.max(total, offset)
    || (next !== null && (next !== offset + reports.length || next >= total || !reports.length))
    || (next === null && offset + reports.length < total)) throw invalid();
  return { available: data.available, reason: data.reason as HookErrorPage['reason'], scope: 'data_directory',
    snapshot_id: snapshot, source_instances: sources, observation, counts, reports, offset, limit,
    next_offset: next, complete_failure_history: false };
}

export function assertHookErrorContinuation(first: HookErrorPage, next: HookErrorPage): void {
  if (first.snapshot_id !== next.snapshot_id || first.available !== next.available
    || first.source_instances !== next.source_instances || JSON.stringify(first.counts) !== JSON.stringify(next.counts)
    || JSON.stringify(first.observation) !== JSON.stringify(next.observation)) throw invalid();
}
