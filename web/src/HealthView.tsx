import { useEffect, useState } from 'react';
import { api, ApiError, query } from './api';
import { DateText, Empty, Loading } from './components';
import { healthCount, healthMetrics, percentage } from './health';
import type { HealthMetric } from './health';
import type { HealthData } from './types';
import './health.css';

function Metrics({ items }: { items: HealthMetric[] }) {
  return <dl className="health-metrics">{items.map(item =>
    <div key={item.key} data-health-metric={item.key}>
      <dt>{item.label}</dt><dd>{item.value == null ? '统计缺失' : item.value.toLocaleString()}</dd>
    </div>)}</dl>;
}

export function HealthView({ project, epoch, onEvidence, onError }: {
  project: string; epoch: number; onEvidence: (target: { event_id: number }) => void;
  onError: (error: unknown) => void;
}) {
  const [data, setData] = useState<HealthData | null>(null);
  const [offset, setOffset] = useState(0);
  const [retry, setRetry] = useState(0);
  const [failure, setFailure] = useState('');
  useEffect(() => setOffset(0), [project]);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setFailure('');
    api<HealthData>(`/health?${query({ project, offset })}`, undefined, controller.signal)
      .then(result => { if (!controller.signal.aborted) setData(result); })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setFailure(error instanceof Error ? error.message : '本地服务读取失败');
        if (error instanceof ApiError && error.status === 401) onError(error);
      });
    return () => controller.abort();
  }, [project, offset, epoch, retry, onError]);
  if (failure) return <div className="health-retry" role="alert">
    <Empty title="健康统计暂不可用">{failure}。本次未取得统计，无法判断采集是否正常。</Empty>
    <button className="button secondary" onClick={() => setRetry(value => value + 1)}>重新读取健康统计</button>
  </div>;
  if (!data) return <Loading />;
  const usage = data.extraction.daily_usage;
  const metrics = healthMetrics(data);
  return <div className="health-view">
    <div className="summary-grid">
      <div><span>原始事件 · 全库</span><strong>{data.events}</strong><small>L0 原文只追加</small></div>
      <div><span>覆盖缺口 · 当前项目</span><strong>{data.extraction.coverage.pending_event_stages}</strong><small>按事件 × 阶段计数</small></div>
      <div><span>未归属会话 · 全库</span><strong>{data.unassigned_sessions}</strong><small>需要明确项目归属</small></div>
      <div><span>压缩点 · 当前项目</span><strong>{data.compression_points}</strong><small>不视作运行失败</small></div>
    </div>

    <div className="health-columns">
      <section className="panel" aria-labelledby="ingest-heading">
        <h3 id="ingest-heading">采集登记与回执 <span className="eyebrow">全库 · 切换项目不改变这些统计</span></h3>
        <Metrics items={metrics.ingest} />
        <p className="health-explanation">未完成任务包含 queued 与 running 账本记录，不证明采集进程仍在运行。回执按原件计数，与任务数、事件数分别统计。</p>
        {metrics.ingest.some(item => item.value == null) && <p className="missing-note">接口未提供完整的采集统计；缺失项无法判断，不能视为零。</p>}
      </section>
      <section className="panel" aria-labelledby="snapshots-heading">
        <h3 id="snapshots-heading">工作区快照 <span className="eyebrow">当前项目</span></h3>
        <Metrics items={metrics.snapshots} />
        <p className="health-explanation">同一记录可同时带多种标记，这些计数不能相加为异常总数，也不能从记录数相减得到成功数。</p>
        <details className="health-details"><summary>如何理解快照标记</summary>
          <p>跳过表示本次记录了未完成采集的原因；文件遗漏表示快照未包含全部文件。异步采集标记提示请求时点与实际采集时点可能不同，不证明发生了竞态错误。元数据未知表示旧记录缺少这些信息，不能证明快照完整。</p>
        </details>
        {metrics.snapshots.some(item => item.value == null) && <p className="missing-note">接口未提供完整的快照统计；缺失项无法判断，不能视为零。</p>}
      </section>
    </div>

    <div className="health-columns">
      <section className="panel" aria-labelledby="usage-heading">
        <h3 id="usage-heading">模型用量 <span className="eyebrow">全库 · UTC {data.extraction.day_utc}</span></h3>
        <div className="usage-value">{usage.reserved_or_settled_tokens.toLocaleString()} <span>/ {usage.budget_tokens.toLocaleString()} token</span></div>
        <progress aria-label="全库每日模型用量与保守预留" max={usage.budget_tokens} value={usage.reserved_or_settled_tokens} />
        <div className="usage-details"><span>已知输入 {usage.known_input_tokens.toLocaleString()}</span><span>已知输出 {usage.known_output_tokens.toLocaleString()}</span><span>未结算调用 {usage.unsettled_sent_attempts}</span></div>
        <p className="muted small">含实际用量与保守预留；未知用量不补为零。剩余额度 {usage.remaining_tokens.toLocaleString()}。</p>
      </section>
      <section className="panel" aria-labelledby="quality-heading">
        <h3 id="quality-heading">采集质量 <span className="eyebrow">全库</span></h3>
        <dl className="quality-grid"><div><dt>坏行</dt><dd>{data.bad_lines}</dd></div><div><dt>未知类型</dt><dd>{data.unknown}</dd></div><div><dt>人工失败队列</dt><dd>{data.manual_jobs}</dd></div><div><dt>钩子失败</dt><dd>{data.hook_failures ?? '未知'}</dd></div></dl>
        <p className="missing-note">{data.hook_failures_reason}。</p>
      </section>
    </div>

    {data.extraction.alerts.length > 0 && <section className="alerts panel"><h3>需要留意</h3>{data.extraction.alerts.map((alert, index) => <p key={`${alert.code}-${index}`}><span className="mono">{alert.stage ?? alert.code}</span>{alert.message}{alert.count != null ? `（${alert.count}）` : ''}</p>)}</section>}

    <section className="panel table-panel" aria-labelledby="stages-heading">
      <h3 id="stages-heading">模型阶段观测 <span className="eyebrow">当前项目 · UTC {data.extraction.day_utc}</span></h3>
      {!data.extraction.stages.length ? <p className="muted">该项目在此 UTC 日没有模型尝试记录，无法据此判断历史提取是否完整。</p> : <>
        <div className="table-scroll" role="region" aria-label="模型阶段观测表，可横向滚动" tabIndex={0}>
          <table><thead><tr><th scope="col">阶段</th><th scope="col">尝试 / 已发送</th><th scope="col">平均输入预算占比</th><th scope="col">校验样本</th><th scope="col">校验拒绝</th><th scope="col">引用失败</th></tr></thead>
            <tbody>{data.extraction.stages.map(stage => <tr key={stage.stage}>
              <th scope="row" className="mono">{stage.stage}</th>
              <td>{stage.attempts} / {stage.sent}</td>
              <td>{percentage(stage.mean_utilization)}<small>有计数的尝试 {healthCount(stage.utilization_samples) ?? '未知'} 条</small></td>
              <td>{healthCount(stage.validation_samples) ?? '未知'}</td>
              <td>{stage.validation_rejections}</td><td>{stage.citation_failures}</td>
            </tr>)}</tbody></table>
        </div>
        <p className="health-explanation">尝试不等于已发送调用；占比只反映有 token 计数的尝试。校验拒绝包含引用失败，两个计数不能相加；没有计数时占比仍为未知。</p>
      </>}
    </section>

    <section className="panel table-panel" aria-labelledby="sources-heading">
      <h3 id="sources-heading">来源与游标 <span>{data.sources.length}</span><span className="eyebrow">当前项目</span></h3>
      {!data.sources.length ? <p className="muted">没有已导入的来源文件。</p> : <div className="table-scroll" role="region" aria-label="来源与游标表，可横向滚动" tabIndex={0}>
        <table><thead><tr><th scope="col">来源</th><th scope="col">最后导入</th><th scope="col">游标滞后</th><th scope="col">会话清理风险</th></tr></thead><tbody>{data.sources.map(source => <tr key={source.file_instance_id}>
          <td><strong>{source.tool}</strong><span className="path" title={source.path}>{source.path}</span></td><td><DateText value={source.last_read} /></td>
          <td>{source.cursor_lag_bytes == null ? '未知' : `${source.cursor_lag_bytes.toLocaleString()} 字节`}</td>
          <td>{source.cleanup_risk === 'source_missing' ? '来源已缺失，已复制事件仍可查看' : source.cleanup_risk === 'source_truncated' ? '来源长度缩短' : '当前无来源缺失迹象'}<small>{source.status}</small></td>
        </tr>)}</tbody></table>
      </div>}
    </section>

    <section className="panel table-panel" aria-labelledby="coverage-heading">
      <h3 id="coverage-heading">覆盖缺口 <span>{data.extraction.coverage.pending_event_stages}</span><span className="eyebrow">当前项目</span></h3>
      {data.extraction.coverage.gaps.length ? <div className="table-scroll" role="region" aria-label="覆盖缺口表，可横向滚动" tabIndex={0}>
        <table><thead><tr><th scope="col">原文</th><th scope="col">会话</th><th scope="col">阶段</th><th scope="col">片段</th></tr></thead><tbody>{data.extraction.coverage.gaps.map(gap => <tr key={`${gap.event_id}-${gap.stage}`}>
          <td><button className="text-button" onClick={() => onEvidence({ event_id: gap.event_id })}>事件 #{gap.event_id}</button></td><td>{gap.session_pk}</td><td>{gap.stage}</td><td className="mono">{gap.segment_id ?? '未知'}</td>
        </tr>)}</tbody></table>
      </div> : <p className="muted">当前页没有覆盖缺口。</p>}
      <div className="pagination"><button className="button secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</button><span>偏移 {offset}</span><button className="button secondary" disabled={data.extraction.coverage.next_offset == null} onClick={() => setOffset(data.extraction.coverage.next_offset!)}>下一页</button></div>
    </section>
  </div>;
}
