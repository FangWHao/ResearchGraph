export const parserHealthMetrics = [
  ['records_total', '物理记录总数'],
  ['records_observed', '已登记类型观测的记录'],
  ['records_unobserved', '旧记录未观测'],
  ['records_with_unknown_types', '含未知类型的记录'],
  ['bad_json_records', 'JSON 无法解析的记录'],
  ['invalid_record_records', '记录结构异常'],
  ['parser_error_records', '解析器故障记录'],
  ['records_unknown_version', '工具版本未知的记录'],
  ['records_invalid_version', '工具版本非法的记录'],
  ['known_version_count', '已知工具版本种类'],
  ['type_groups_total', '类型分组总数'],
] as const;

type MetricKey = typeof parserHealthMetrics[number][0];
export type ParserHealthType = {
  tool: string;
  parser_version: string;
  tool_version: string | null;
  version_basis: 'direct_record' | 'file_context' | 'unknown' | 'invalid';
  category: string;
  type_name: string;
  recognized: boolean;
  occurrences: number;
  records: number;
  source_files: number;
  first_event_id: number;
  last_event_id: number;
  last_recorded_at: string;
};
export type ParserHealthData = Record<MetricKey, number> & {
  available: true;
  snapshot_id: number;
  scope_key: string;
  offset: number;
  next_offset: number | null;
  types: ParserHealthType[];
  alerts: { code: string; count: number; message: string }[];
};
export type ParserHealthResult = ParserHealthData | { available: false; reason: string | null };
export type ParserHealthPage = { offset: number; snapshot?: number; scopeKey?: string };

const invalid = () => new Error('解析器账本响应格式或读取条件不一致，不能据此判断记录是否完整。');
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  return value as Record<string, unknown>;
}
function integer(value: unknown, minimum = 0): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum) throw invalid();
  return value;
}
function text(value: unknown): string {
  if (typeof value !== 'string' || !value.length) throw invalid();
  return value;
}
function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw invalid();
  return value;
}

export function parserTypeKey(item: ParserHealthType): string {
  return JSON.stringify([item.tool, item.parser_version, item.tool_version, item.version_basis,
    item.category, item.type_name, item.recognized]);
}

export function parseParserHealth(value: unknown, expected: ParserHealthPage): ParserHealthResult {
  const data = object(value);
  if (!boolean(data.available)) {
    if (expected.offset !== 0 || expected.snapshot != null || expected.scopeKey != null) throw invalid();
    if (data.reason != null && typeof data.reason !== 'string') throw invalid();
    return { available: false, reason: data.reason as string | null | undefined ?? null };
  }
  const counts = Object.fromEntries(parserHealthMetrics.map(([key]) => [key, integer(data[key])])) as Record<MetricKey, number>;
  const snapshot = integer(data.snapshot_id);
  const scope = text(data.scope_key);
  const offset = integer(data.offset);
  if (offset !== expected.offset || (expected.snapshot != null && snapshot !== expected.snapshot)
    || (expected.scopeKey != null && scope !== expected.scopeKey)) throw invalid();
  const next = data.next_offset === null ? null : integer(data.next_offset);
  if (!Array.isArray(data.types) || data.types.length > 20 || !Array.isArray(data.alerts)) throw invalid();
  const types = data.types.map(value => {
    const item = object(value);
    if (item.tool_version !== null && typeof item.tool_version !== 'string') throw invalid();
    if (!['direct_record', 'file_context', 'unknown', 'invalid'].includes(String(item.version_basis))) throw invalid();
    const first = integer(item.first_event_id, 1);
    const last = integer(item.last_event_id, 1);
    if (last < first) throw invalid();
    return {
      tool: text(item.tool), parser_version: text(item.parser_version), tool_version: item.tool_version,
      version_basis: item.version_basis as ParserHealthType['version_basis'],
      category: text(item.category), type_name: text(item.type_name), recognized: boolean(item.recognized),
      occurrences: integer(item.occurrences, 1), records: integer(item.records, 1),
      source_files: integer(item.source_files, 1), first_event_id: first, last_event_id: last,
      last_recorded_at: text(item.last_recorded_at),
    } as ParserHealthType;
  });
  if (new Set(types.map(parserTypeKey)).size !== types.length
    || counts.records_observed + counts.records_unobserved !== counts.records_total
    || offset + types.length > counts.type_groups_total
    || (next !== null && (next !== offset + types.length || next >= counts.type_groups_total || !types.length))
    || (next === null && offset + types.length < counts.type_groups_total)) throw invalid();
  const alerts = data.alerts.map(value => {
    const item = object(value);
    return { code: text(item.code), count: integer(item.count), message: text(item.message) };
  });
  return { ...counts, available: true, snapshot_id: snapshot, scope_key: scope,
    offset, next_offset: next, types, alerts };
}

export function assertParserHealthContinuation(first: ParserHealthData, next: ParserHealthData): void {
  if (first.snapshot_id !== next.snapshot_id || first.scope_key !== next.scope_key
    || first.available !== next.available
    || parserHealthMetrics.some(([key]) => first[key] !== next[key])) throw invalid();
}

export const parserVersionBasis: Record<ParserHealthType['version_basis'], string> = {
  direct_record: '该记录明确报告', file_context: '来自同来源文件上下文',
  unknown: '版本未知', invalid: '版本字段非法',
};

export const parserCategories: Record<string, string> = {
  record: '顶层记录', payload: 'Codex 负载子类', content: '消息内容块',
  tool_result_content: 'Claude 工具结果内容', output_content: 'Codex 调用回执内容',
};
