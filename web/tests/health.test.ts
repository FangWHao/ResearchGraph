import { describe, expect, it } from 'vitest';
import { healthCount, healthMetrics, percentage } from '../src/health';

describe('健康观测保留账本口径和未知状态', () => {
  it('保留重叠快照标记，不把四条记录推定成成功或异常总数', () => {
    const snapshots = { total: 4, skipped: 1, async_race: 2, partial: 2, metadata_unknown: 1 };
    const metrics = healthMetrics({ snapshots }).snapshots;
    expect(Object.fromEntries(metrics.map(item => [item.key, item.value]))).toEqual(snapshots);
    expect(snapshots).toEqual({ total: 4, skipped: 1, async_race: 2, partial: 2, metadata_unknown: 1 });
  });

  it('原件回执、来源与未完成任务分别保留，不由任务数推算回执数', () => {
    const ingest = { registered_sources: 2, known_source_paths: 2, spool_receipts: 3, spool_unfinished: 2, spool_failed: 1 };
    expect(Object.fromEntries(healthMetrics({ ingest }).ingest.map(item => [item.key, item.value]))).toEqual(ingest);
  });

  it('接口对象缺失仍未知，完整的零计数仍为零', () => {
    const missing = healthMetrics({});
    expect([...missing.ingest, ...missing.snapshots].every(item => item.value === null)).toBe(true);
    const zero = healthMetrics({ snapshots: { total: 0, skipped: 0, async_race: 0, partial: 0, metadata_unknown: 0 } });
    expect(zero.snapshots.every(item => item.value === 0)).toBe(true);
    expect(zero.ingest.every(item => item.value === null)).toBe(true);
  });

  it('无效计数无法成为正常零，预算占比缺失不补零，也不裁掉超预算观测', () => {
    for (const value of [undefined, null, '0', -1, 1.5, Infinity, NaN]) expect(healthCount(value)).toBeNull();
    expect(healthCount(0)).toBe(0);
    expect(percentage(null)).toBe('未知');
    expect(percentage(NaN)).toBe('未知');
    expect(percentage(0)).toBe('0%');
    expect(percentage(1.25)).toBe('125%');
  });
});
