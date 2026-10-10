import { DateText } from './components';
import { healthCount, queueMetrics, queueNextOffset, queueScope, queueStates, queueText } from './health';
import type { ExtractionQueue, ExtractionQueueState } from './types';

export function ExtractionQueuePanel({ queue, offset, limit, onOffset }: {
  queue: ExtractionQueue | null | undefined; offset: number; limit: number; onOffset: (offset: number) => void;
}) {
  const metrics = queueMetrics(queue);
  const tasks = Array.isArray(queue?.tasks) ? queue.tasks : null;
  const next = queueNextOffset(queue, offset);
  return <section className="panel extraction-queue-panel" aria-labelledby="extraction-queue-heading">
    <h3 id="extraction-queue-heading">持续提取队列 <span className="eyebrow">{queueScope(queue?.scope)}</span></h3>
    <dl className="health-queue-metrics">{metrics.map(item => <div key={item.key} data-queue-metric={item.key}>
      <dt>{item.label}</dt><dd>{item.value == null ? '未知' : item.value.toLocaleString()}</dd>
    </div>)}</dl>
    <p className="health-explanation">计数为此范围的全部任务，不随任务列表分页改变。“处理中”仅表示账本记录，不证明进程仍活着。本次任务完成不代表会话全部原文已覆盖，也不代表候选事实已确认。</p>
    <p className="health-explanation">每日额度不足时自动等待下一个 UTC 日；下次尝试时间不是已执行的证明。部分结果的覆盖缺口仍保留，默认不反复重发；可在下方覆盖缺口区查看原文。</p>
    {metrics.some(item => item.value == null) && <p className="missing-note">接口未提供完整队列统计；未知项不能视为零，也不能从当前页推算完整数量。</p>}
    {!tasks ? <p className="missing-note">队列任务列表未知，无法判断是否存在待处理任务。</p> : tasks.length === 0 ? <p className="muted">当前页没有队列任务；会话覆盖和事实审核需分别查看。</p> : <ol className="extraction-tasks" aria-label="提取任务列表，可纵向滚动" tabIndex={0}>{tasks.map((task, index) => {
      const state = typeof task.state === 'string' && Object.hasOwn(queueStates, task.state) ? task.state as ExtractionQueueState : null;
      return <li key={task.queue_id ?? `missing-${index}`} data-queue-id={task.queue_id}>
        <div className="queue-task-heading"><strong>任务 #{healthCount(task.queue_id) ?? '未知'}</strong><span className={`queue-state ${state ?? 'unknown'}`}>{state ? queueStates[state] : '状态未知'}</span></div>
        <dl className="queue-task-metadata"><div><dt>会话</dt><dd>{healthCount(task.session_pk) ?? '未知'}</dd></div><div><dt>项目 ID</dt><dd>{queueText(task.project_id)}</dd></div>
          <div><dt>模型</dt><dd>{queueText(task.model)}</dd></div><div><dt>原文边界</dt><dd>事件 #{healthCount(task.max_event_id) ?? '未知'}</dd></div><div><dt>尝试次数</dt><dd>{healthCount(task.attempts) ?? '未知'}</dd></div>
          <div><dt>创建时间</dt><dd><DateText value={task.created_at} /></dd></div><div><dt>更新时间</dt><dd><DateText value={task.updated_at} /></dd></div>
          <div><dt>下次尝试</dt><dd>{task.next_attempt_at === null ? '未安排' : <DateText value={task.next_attempt_at} />}</dd></div>
        </dl>
        <p className="queue-task-reason">等待原因：{task.defer_reason === null ? '无等待原因记录' : task.defer_reason === 'daily_budget' ? '每日模型额度不足' : task.defer_reason === 'remote_disabled' ? '尚未允许远程模型调用' : queueText(task.defer_reason)}</p>
        <p className="queue-task-reason">错误记录：{task.error === null ? '无错误记录' : queueText(task.error)}</p>
        {state === 'partial' && <p className="missing-note">部分结果仍有缺口，不能视为全部完成。</p>}
      </li>;
    })}</ol>}
    <div className="pagination"><button className="button secondary" disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - limit))}>上一页提取任务</button><span>共享分页 · 偏移 {offset}</span><button className="button secondary" disabled={next == null} onClick={() => { if (next != null) onOffset(next); }}>下一页提取任务</button></div>
    <p className="health-explanation">任务列表与覆盖缺口共用健康查询的分页位置；翻页不会重新提取或确认记录。</p>
    {queue && queue.next_offset === undefined && <p className="missing-note">队列分页信息未知，无法判断是否还有下一页。</p>}
  </section>;
}
