import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { DateText } from './components';
import { assertHookErrorContinuation, hookCollectionNames, hookCountNames, parseHookErrors } from './hookErrors';
import type { HookErrorPage, HookErrorRequest, HookErrorResult } from './hookErrors';
import './hookErrors.css';

export function HookErrorPanel({ project, epoch, onError }: {
  project: string; epoch: number; onError: (error: unknown) => void;
}) {
  const context = JSON.stringify([project, epoch]);
  const [pagination, setPagination] = useState<HookErrorRequest & { context: string }>({ context, offset: 0 });
  const [retry, setRetry] = useState(0);
  const [response, setResponse] = useState<{ context: string; data: HookErrorResult } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const [pending, setPending] = useState('');
  const page = pagination.context === context ? pagination : { context, offset: 0 };
  const key = JSON.stringify([context, page.offset, page.snapshot, retry]);
  const currentKey = useRef(key); currentKey.current = key;
  const first = useRef<{ context: string; data: HookErrorPage } | null>(null);
  const result = response?.context === context ? response.data : null;
  const data = result && 'snapshot_id' in result ? result : null;
  const busy = pending === key;
  const error = failure?.key === key ? failure.message : '';

  useEffect(() => {
    const controller = new AbortController();
    setPending(key); setFailure(null);
    const expected = { offset: page.offset, snapshot: page.snapshot };
    api<unknown>(`/hook-errors?${query({ project, limit: 20, offset: page.offset, snapshot: page.snapshot })}`,
      undefined, controller.signal).then(raw => {
      if (controller.signal.aborted || currentKey.current !== key) return;
      const data = parseHookErrors(raw, expected);
      if (expected.snapshot != null) {
        if (!('snapshot_id' in data) || first.current?.context !== context) throw new Error('钩子报告读取身份缺失，请重新读取。');
        assertHookErrorContinuation(first.current.data, data);
      } else first.current = 'snapshot_id' in data ? { context, data } : null;
      setResponse({ context, data });
    }).catch((error: unknown) => {
      if (controller.signal.aborted || currentKey.current !== key) return;
      setFailure({ key, message: error instanceof ApiError && error.status === 404
        ? '钩子报告暂不可用：未找到接口或所选项目，采集情况和失败次数未知。'
        : error instanceof Error ? error.message : '钩子报告读取失败，失败次数未知。' });
      if (error instanceof ApiError && error.status === 401) onError(error);
    }).finally(() => { if (!controller.signal.aborted && currentKey.current === key) setPending(''); });
    return () => controller.abort();
  }, [project, context, key, page.offset, page.snapshot, onError]);

  function refresh() { setPagination({ context, offset: 0 }); setRetry(value => value + 1); }
  function changePage(offset: number) { if (data) setPagination({ context, offset, snapshot: data.snapshot_id }); }

  return <section className="panel hook-errors" aria-label="钩子失败报告">
    <header><div><h3>钩子失败报告</h3><span className="eyebrow">全库 · 当前数据目录</span></div>
      <button className="button secondary" disabled={busy} onClick={refresh}>重新读取钩子报告</button></header>
    <p className="health-explanation">切换项目不改变这里的全库范围。只统计已采集的钩子错误报告，副本或轮换中的同一报告会分别计数，不能据此推算实际失败次数。未记录或尚未采集的失败未知，完整失败历史始终未知。日志轮换后已保存的历史仍保留；本页不显示错误正文或原始异常信息，不执行日志中的内容。</p>
    {error && <p className="error-message" role="alert">{error}{data && ' 当前保留上次成功页，没有混入失败页；请主动重新读取第一页。'}</p>}
    {busy && <p role="status">{result ? '正在读取，暂时保留上次成功返回的报告。' : '正在读取钩子报告…'}</p>}
    {result?.reason === 'legacy_schema' && <p className="missing-note">旧数据库尚无钩子报告账本，统计缺失；不能视为零次失败。</p>}
    {data && <>
      <dl className="hook-error-metrics">{hookCountNames.map(([key, label]) => <div key={key} data-hook-metric={key}>
        <dt>{label}</dt><dd>{data.counts ? data.counts[key].toLocaleString() : '未知'}</dd></div>)}</dl>
      <div className="hook-error-observation"><h4>最近保存的采集状态</h4>
        {data.observation ? <><p data-hook-status={data.observation.status}><strong>{hookCollectionNames[data.observation.status]}</strong></p>
          <dl><div><dt>当前日志字节</dt><dd>{data.observation.source_bytes?.toLocaleString() ?? '未知'}</dd></div>
            <div><dt>已提交读取位置</dt><dd>{data.observation.committed_offset?.toLocaleString() ?? '未知'}</dd></div>
            <div><dt>本次日志实例</dt><dd>{data.observation.source_id == null ? '未知' : `#${data.observation.source_id}`}</dd></div>
            <div><dt>状态入库时间</dt><dd><DateText value={data.observation.recorded_at} /></dd></div></dl>
          <p className="small muted">时间为最近持久观测或状态变化的入库时间，不代表最近一次轮询；状态不变时不会追加记录。</p>
          <p className="missing-note">这是采集账本，不证明钩子或采集进程仍在运行。即使已读到本次末尾，也不代表未记录的失败不存在。</p></>
          : <p className="missing-note">尚未登记采集观测；日志状态与失败次数未知。</p>}
        {!data.available && <p className="missing-note">尚未观测到可读取的钩子错误日志；保留已登记的检查状态，不能据此推断零次失败。</p>}
      </div>
      <div className="hook-error-page"><span>已登记日志实例 {data.source_instances} · 本页报告 {data.reports.length} · 偏移 {data.offset}</span>
        <span>读取快照 #{data.snapshot_id}</span></div>
      {data.reports.length === 0 ? <p className="missing-note">本页没有已保存报告；完整失败历史仍未知。</p>
        : <ul className="hook-error-reports">{data.reports.map(report => <li key={report.report_id} data-hook-report={report.report_id}>
          <header><strong>报告 #{report.report_id}</strong><span>{report.status === 'reported' ? '已记录失败报告' : '无法识别的日志行'}</span></header>
          <dl><div><dt>发生时间</dt><dd><DateText value={report.occurred_at} /></dd></div>
            <div><dt>入库时间</dt><dd><DateText value={report.recorded_at} /></dd></div>
            <div><dt>阶段</dt><dd>{report.stage === 'hook' ? '钩子' : '未知'}</dd></div>
            <div><dt>异常类别</dt><dd>{report.exception_class ?? '未知'}</dd></div>
            <div><dt>日志实例</dt><dd>#{report.source_id}</dd></div>
            <div><dt>来源字节位置</dt><dd>{report.byte_start}–{report.byte_end}</dd></div></dl>
          {report.exception_class_sha256 && <p className="small muted">未知异常类别仅保留摘要<code>{report.exception_class_sha256}</code></p>}
          <details><summary>日志行核对摘要</summary><code>{report.line_sha256}</code></details>
        </li>)}</ul>}
      <div className="pagination"><button className="button secondary" disabled={busy || !!error || data.offset === 0}
        onClick={() => changePage(Math.max(0, data.offset - 20))}>上一页钩子报告</button>
        <button className="button secondary" disabled={busy || !!error || data.next_offset === null}
          onClick={() => changePage(data.next_offset!)}>下一页钩子报告</button></div>
    </>}
  </section>;
}
