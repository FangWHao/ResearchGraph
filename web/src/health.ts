import type { HealthData } from './types';

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
