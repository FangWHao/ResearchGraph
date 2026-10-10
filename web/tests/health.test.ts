import { describe, expect, it } from 'vitest';
import { healthCount, healthMetrics, percentage, pipelineMetrics, pipelineReason, pipelineTarget, queueMetrics, queueNextOffset, queueScope } from '../src/health';

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

  it('队列总数和状态计数来自全范围统计，不从当前页任务推算', () => {
    const queue = { total: 53, counts: { queued: 47, running: 1, done: 1, partial: 1, paused: 1, blocked: 1, cancelled: 1 }, tasks: [{ state: 'queued' }] };
    expect(Object.fromEntries(queueMetrics(queue).map(item => [item.key, item.value]))).toEqual({ total: 53, ...queue.counts });
    expect(queueMetrics({ ...queue, tasks: [] })).toEqual(queueMetrics(queue));
  });

  it('队列旧字段未知不能补零或猜范围，显式零值保持', () => {
    expect(queueMetrics(undefined).every(item => item.value === null)).toBe(true);
    const incomplete = queueMetrics({ total: 0, counts: { queued: 0, running: -1 } });
    expect(incomplete.find(item => item.key === 'queued')?.value).toBe(0);
    expect(incomplete.find(item => item.key === 'running')?.value).toBeNull();
    expect(incomplete.find(item => item.key === 'done')?.value).toBeNull();
    expect(queueScope(null)).toBe('范围未知');
    expect(queueScope('all_projects')).not.toBe(queueScope('project'));
  });

  it('任务分页仅接受向前的已知整数，未知与末页不能猜成下一页', () => {
    expect(queueNextOffset({ next_offset: 50 }, 0)).toBe(50);
    for (const queue of [undefined, {}, { next_offset: null }, { next_offset: -1 }, { next_offset: 0 }, { next_offset: 1.5 }]) expect(queueNextOffset(queue, 0)).toBeNull();
    expect(queueNextOffset({ next_offset: 50 }, 50)).toBeNull();
  });

  it('关联与概览的总数只读自己的账本，暂停不能推定成日额度', () => {
    const queue = { total: 54, counts: { queued: 45, running: 1, done: 2, partial: 1, paused: 3, blocked: 1, cancelled: 1 }, tasks: [{ state: 'done' }] };
    expect(Object.fromEntries(pipelineMetrics(queue).map(item => [item.key, item.value]))).toEqual({ total: 54, ...queue.counts });
    expect(pipelineMetrics({ ...queue, tasks: [] })).toEqual(pipelineMetrics(queue));
    expect(pipelineMetrics(undefined).every(item => item.value === null)).toBe(true);
    expect(pipelineReason(null)).toBe('无等待原因记录');
    expect(pipelineReason(undefined)).toBe('未知');
    expect(pipelineReason('daily_budget')).toContain('UTC');
    for (const reason of ['CountingUnavailable', 'RuntimeError', 'stage_busy']) {
      expect(pipelineReason(reason)).not.toContain('额度');
      expect(pipelineReason(reason)).toContain('下次尝试');
    }
    expect(pipelineReason('new_reason')).toBe('new_reason');
  });

  it('阶段目标必须与会话编号一致，缺失与矛盾不能猜为整个项目', () => {
    expect(pipelineTarget({ target_key: 'project', session_pk: null })).toBe('整个项目');
    expect(pipelineTarget({ target_key: 'session:12', session_pk: 12 })).toBe('会话 #12');
    for (const task of [{}, { target_key: 'project' }, { target_key: 'session:12' }, { target_key: 'session:01', session_pk: 1 }]) expect(pipelineTarget(task)).toBe('目标未知');
    for (const task of [{ target_key: 'project', session_pk: 12 }, { target_key: 'session:12', session_pk: null }, { target_key: 'session:12', session_pk: 1 }]) expect(pipelineTarget(task)).toBe('目标字段不一致');
    expect(pipelineTarget({ target_key: 'session:9007199254740992', session_pk: 9007199254740992 })).toBe('目标字段不一致');
  });
});
