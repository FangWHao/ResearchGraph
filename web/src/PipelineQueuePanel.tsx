import { DateText } from './components';
import { healthCount, pipelineMetrics, pipelineReason, pipelineStage, pipelineStates, pipelineTarget, queueNextOffset, queueScope, queueText } from './health';
import type { ExtractionQueueState, PipelineQueue, PipelineQueueTask } from './types';

export function PipelineQueuePanel({ queue, offset, limit, onOffset }: {
  queue: PipelineQueue | null | undefined; offset: number; limit: number; onOffset: (offset: number) => void;
}) {
  const metrics = pipelineMetrics(queue);
  const tasks = Array.isArray(queue?.tasks) ? queue.tasks : null;
  const next = queueNextOffset(queue, offset);
  return <section className="panel pipeline-queue-panel" aria-labelledby="pipeline-queue-heading">
    <h3 id="pipeline-queue-heading">自动关联与概览 <span className="eyebrow">{queueScope(queue?.scope)}</span></h3>
    <dl className="health-queue-metrics">{metrics.map(item => <div key={item.key} data-pipeline-metric={item.key}>
      <dt>{item.label}</dt><dd>{item.value == null ? '未知' : item.value.toLocaleString()}</dd>
    </div>)}</dl>
    <p className="health-explanation">这是独立于提取的阶段队列；计数来自此范围的全部任务，不随分页改变。等待处理与处理中仅表示账本记录，不证明进程仍活着。阶段任务完成不代表研究事实已确认，概览不作为证据。</p>
    <p className="health-explanation">额度不足等待下一个 UTC 日；服务暂不可用或阶段忙碌则按记录的下次尝试时间重试。下次尝试时间不证明已执行。查看与翻页只读取记录。</p>
    {metrics.some(item => item.value == null) && <p className="missing-note">接口未提供完整关联与概览统计；未知项不能视为零，当前页不能代替完整数量。</p>}
    {!tasks ? <p className="missing-note">关联与概览任务列表未知，无法判断是否存在待处理任务。</p> : tasks.length === 0 ? <p className="muted">当前页没有关联与概览任务；原文覆盖与事实审核需分别查看。</p> : <ol className="extraction-tasks pipeline-tasks" aria-label="关联与概览任务列表，可纵向滚动" tabIndex={0}>{tasks.map((value, index) => {
      const task: Partial<PipelineQueueTask> = value && typeof value === 'object' ? value : {};
      const state = typeof task.state === 'string' && Object.hasOwn(pipelineStates, task.state) ? task.state as ExtractionQueueState : null;
      return <li key={task.queue_id ?? `missing-${index}`} data-pipeline-id={task.queue_id}>
        <div className="queue-task-heading"><strong>{pipelineStage(task.stage)} · 任务 #{healthCount(task.queue_id) ?? '未知'}</strong><span className={`queue-state ${state ?? 'unknown'}`}>{state ? pipelineStates[state] : '状态未知'}</span></div>
        <dl className="queue-task-metadata">
          <div><dt>任务目标</dt><dd>{pipelineTarget(task)}</dd></div><div><dt>项目 ID</dt><dd>{queueText(task.project_id)}</dd></div>
          <div><dt>目标标识</dt><dd>{queueText(task.target_key)}</dd></div><div><dt>尝试次数</dt><dd>{healthCount(task.attempts) ?? '未知'}</dd></div>
          <div><dt>创建时间</dt><dd><DateText value={task.created_at} /></dd></div><div><dt>更新时间</dt><dd><DateText value={task.updated_at} /></dd></div>
          <div><dt>下次尝试</dt><dd>{task.next_attempt_at === null ? '未安排' : <DateText value={task.next_attempt_at} />}</dd></div>
        </dl>
        <p className="queue-task-reason">等待原因：{pipelineReason(task.defer_reason)}</p>
        <p className="queue-task-reason">错误记录：{task.error === null ? '无错误记录' : queueText(task.error)}</p>
        {state === 'partial' && <p className="missing-note">该阶段保留未完成项，不能视为全部完成。</p>}
        {task.result === undefined ? <p className="missing-note">阶段结果记录未知。</p> : task.result !== null && <details className="health-details queue-result"><summary>阶段结果记录 · 仅诊断</summary><pre>{queueText(task.result)}</pre></details>}
      </li>;
    })}</ol>}
    <div className="pagination"><button className="button secondary" disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - limit))}>上一页关联与概览</button><span>独立分页 · 偏移 {offset}</span><button className="button secondary" disabled={next == null} onClick={() => { if (next != null) onOffset(next); }}>下一页关联与概览</button></div>
    {queue && queue.next_offset === undefined && <p className="missing-note">关联与概览分页信息未知，无法判断是否还有下一页。</p>}
  </section>;
}
