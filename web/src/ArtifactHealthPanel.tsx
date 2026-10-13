import { healthCount } from './health';
import type { ArtifactHealth } from './types';

export function artifactMetrics(data: ArtifactHealth | null | undefined) {
  return {
    versions: [
      { key: 'versions', label: '文件版本记录', value: healthCount(data?.versions) },
      { key: 'archived', label: '快照字节版本', value: healthCount(data?.archived) },
      { key: 'current_hashed', label: '当前文件哈希版本', value: healthCount(data?.current_hashed) },
      { key: 'cache_reused', label: '缓存复用观察', value: healthCount(data?.cache_reused) },
    ],
    jobs: Object.entries({ queued: '等待处理', running: '处理中（账本）', paused: '等待重试', failed: '失败记录', done: '任务完成记录' })
      .map(([key, label]) => ({ key, label, value: healthCount(data?.jobs?.[key as keyof NonNullable<ArtifactHealth['jobs']>]) })),
    discovery: Object.entries({ pending: '待发现', unknown: '元数据未知', partial: '部分发现', done: '发现已完成' })
      .map(([key, label]) => ({ key, label, value: healthCount(data?.discovery?.[key as keyof NonNullable<ArtifactHealth['discovery']>]) })),
  };
}
export function ArtifactHealthPanel({ data, onVersions }: { data: ArtifactHealth | null | undefined; onVersions?: () => void }) {
  const metrics = artifactMetrics(data);
  const group = (name: keyof typeof metrics) => <dl className="artifact-health-metrics">{metrics[name].map(item => <div key={item.key} data-artifact-metric={`${name}.${item.key}`}>
    <dt>{item.label}</dt><dd>{item.value == null ? '统计缺失' : item.value.toLocaleString()}</dd>
  </div>)}</dl>;
  return <section className="panel artifact-health-panel" aria-labelledby="artifact-health-heading">
    <h3 id="artifact-health-heading">文件版本与后台摘要 <span className="eyebrow">当前项目</span></h3>
    {group('versions')}
    <div className="artifact-health-groups"><div><h4>后台任务 · 按任务计数</h4>{group('jobs')}</div><div><h4>快照发现状态 · 按快照计数</h4>{group('discovery')}</div></div>
    <p className="health-explanation">版本、缓存复用观察、后台任务和快照发现分别统计，不能相加推算完整文件数量。处理中只是账本记录，不证明进程仍活着；任务完成不证明运行输入输出已记录。</p>
    <p className="health-explanation">快照字节保存、当前文件完整哈希与工具报告文本依据不同；缓存复用保留原实际读取窗口，不能当成新读取。</p>
    {Object.values(metrics).flat().some(item => item.value == null) && <p className="missing-note">接口未提供完整文件版本统计；缺失不能视为零，也不能从版本当前页推算。</p>}
    {onVersions && <button className="button secondary" onClick={onVersions}>查看文件版本</button>}
  </section>;
}
