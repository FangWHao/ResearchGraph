import type { ExtractionQueue, ExtractionQueueState, HealthData } from './types';

export type HealthMetric = { key: string; label: string; value: number | null };

export function healthCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

export function healthMetrics(data: Pick<HealthData, 'ingest' | 'snapshots'>) {
  const ingest = data.ingest;
  const snapshots = data.snapshots;
  return {
    ingest: [
      { key: 'registered_sources', label: '已注册采集来源', value: healthCount(ingest?.registered_sources) },
      { key: 'known_source_paths', label: '已知来源路径', value: healthCount(ingest?.known_source_paths) },
      { key: 'spool_receipts', label: '原件回执', value: healthCount(ingest?.spool_receipts) },
      { key: 'spool_unfinished', label: '未完成采集任务', value: healthCount(ingest?.spool_unfinished) },
      { key: 'spool_failed', label: '采集失败任务', value: healthCount(ingest?.spool_failed) },
    ] satisfies HealthMetric[],
    snapshots: [
      { key: 'total', label: '快照记录', value: healthCount(snapshots?.total) },
      { key: 'skipped', label: '跳过标记', value: healthCount(snapshots?.skipped) },
      { key: 'async_race', label: '异步采集标记', value: healthCount(snapshots?.async_race) },
      { key: 'partial', label: '文件遗漏标记', value: healthCount(snapshots?.partial) },
      { key: 'metadata_unknown', label: '元数据未知', value: healthCount(snapshots?.metadata_unknown) },
    ] satisfies HealthMetric[],
  };
}

export function percentage(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? `${(value * 100).toLocaleString('zh-CN', { maximumFractionDigits: 1 })}%`
    : '未知';
}

export const queueStates: Record<ExtractionQueueState, string> = {
  queued: '等待处理', running: '处理中（账本）', done: '本次任务完成', partial: '部分结果 · 有缺口',
  paused: '等待每日额度', blocked: '受阻', cancelled: '已取消',
};
export function queueScope(scope: unknown): string {
  return scope === 'project' ? '当前项目' : scope === 'all_projects' ? '全库 · 所有项目' : '范围未知';
}
export function queueMetrics(queue: ExtractionQueue | null | undefined): HealthMetric[] {
  return [
    { key: 'total', label: '队列任务总数', value: healthCount(queue?.total) },
    ...Object.entries(queueStates).map(([key, label]) => ({ key, label, value: healthCount(queue?.counts?.[key as ExtractionQueueState]) })),
  ];
}
export function queueNextOffset(queue: ExtractionQueue | null | undefined, offset: number): number | null {
  const next = healthCount(queue?.next_offset);
  return next != null && next > offset ? next : null;
}
export function queueText(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : '未知';
}
