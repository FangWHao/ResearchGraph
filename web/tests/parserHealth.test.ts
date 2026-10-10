import { describe, expect, it } from 'vitest';
import { assertParserHealthContinuation, parseParserHealth } from '../src/parserHealth';
import type { ParserHealthData, ParserHealthType } from '../src/parserHealth';

function type(index = 0): ParserHealthType {
  return { tool: 'claude', parser_version: '1', tool_version: '2.synthetic', version_basis: 'direct_record',
    category: 'content', type_name: `synthetic-${index}`, recognized: false, occurrences: 3,
    records: 1, source_files: 1, first_event_id: index + 1, last_event_id: index + 1,
    last_recorded_at: '2026-10-10T09:00:00+00:00' };
}
function page(offset = 0): ParserHealthData {
  return { available: true, records_total: 3, records_observed: 2, records_unobserved: 1,
    records_with_unknown_types: 1, bad_json_records: 1, invalid_record_records: 0, parser_error_records: 0,
    records_unknown_version: 1, records_invalid_version: 0, known_version_count: 1,
    type_groups_total: 25, snapshot_id: 19, scope_key: 'synthetic-scope', offset,
    next_offset: offset === 0 ? 20 : null, types: Array.from({ length: offset === 0 ? 20 : 5 },
      (_, index) => type(offset + index)), alerts: [{ code: 'unknown_type', count: 1, message: '存在未知类型' }] };
}

describe('解析器账本的缺失与固定页身份', () => {
  it('物理记录含多个类型时保留各自出现次数，不相加成记录数或确认状态', () => {
    const input = { ...page(), type_groups_total: 2, next_offset: null, types: [type(0), type(1)] };
    const data = parseParserHealth(input, { offset: 0 });
    expect(data).toEqual(input);
    if (!data.available) throw new Error('测试响应应有账本');
    expect(data.types.map(item => [item.occurrences, item.records, item.recognized])).toEqual([[3, 1, false], [3, 1, false]]);
    expect(data.records_with_unknown_types).toBe(1);
    expect(data.records_unobserved).toBe(1);
  });
  it('旧库只有不可用原因时保持统计缺失，不补零或生成空的完整页', () => {
    expect(parseParserHealth({ available: false, reason: 'legacy_schema' }, { offset: 0 }))
      .toEqual({ available: false, reason: 'legacy_schema' });
    expect(() => parseParserHealth({ available: false, reason: 'legacy_schema' }, { offset: 20, snapshot: 19 }))
      .toThrow();
    const { records_unobserved: _missing, ...incomplete } = page();
    expect(() => parseParserHealth(incomplete, { offset: 0 })).toThrow();
  });
  it('非整型、负数、字符串计数或缺失版本依据均不能冒充正常账本', () => {
    for (const count of [-1, 1.5, '3', null, Number.NaN]) {
      expect(() => parseParserHealth({ ...page(), records_total: count }, { offset: 0 })).toThrow();
    }
    expect(() => parseParserHealth({ ...page(), types: [{ ...type(), version_basis: null }] }, { offset: 0 })).toThrow();
  });
  it('下一页必须匹配请求的偏移、首个快照及范围键', () => {
    const next = page(20);
    const expected = { offset: 20, snapshot: 19, scopeKey: 'synthetic-scope' };
    expect(parseParserHealth(next, expected)).toEqual(next);
    for (const patch of [{ offset: 0 }, { snapshot_id: 20 }, { scope_key: 'another-project' }]) {
      expect(() => parseParserHealth({ ...next, ...patch }, expected)).toThrow();
    }
  });
  it('同读取身份下统计变化也拒绝混页，新的主动首读可以建立新身份', () => {
    const first = page(); const next = page(20);
    expect(() => assertParserHealthContinuation(first, next)).not.toThrow();
    for (const changed of [{ records_unknown_version: 2 }, { snapshot_id: 20 }, { scope_key: 'changed' }]) {
      expect(() => assertParserHealthContinuation(first, { ...next, ...changed })).toThrow();
    }
    expect(parseParserHealth({ ...first, snapshot_id: 21, scope_key: 'new' }, { offset: 0 }))
      .toMatchObject({ snapshot_id: 21, scope_key: 'new' });
  });
  it('分页遗漏、重复分组或原文事件身份异常不会被静默少画', () => {
    for (const changed of [
      { next_offset: null }, { next_offset: 21 }, { types: [type(), type()] },
      { types: [{ ...type(), first_event_id: 0 }] }, { records_observed: 3 },
    ]) expect(() => parseParserHealth({ ...page(), ...changed }, { offset: 0 })).toThrow();
  });
  it('保留未知类别和文字，不猜版本或把已识别当科学确认', () => {
    const item = { ...type(), recognized: true, category: 'future_category', tool_version: null,
      version_basis: 'unknown' as const, type_name: '<script>synthetic()</script>' };
    const result = parseParserHealth({ ...page(), type_groups_total: 1, next_offset: null, types: [item] }, { offset: 0 });
    expect(result).toMatchObject({ types: [item], records_unknown_version: 1 });
  });
});
