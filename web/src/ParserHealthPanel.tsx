import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { DateText } from './components';
import { assertParserHealthContinuation, parseParserHealth, parserCategories, parserHealthMetrics,
  parserTypeKey, parserVersionBasis } from './parserHealth';
import type { ParserHealthData, ParserHealthPage, ParserHealthResult } from './parserHealth';
import './parserHealth.css';

export function ParserHealthPanel({ project, epoch, onEvidence, onError }: {
  project: string; epoch: number;
  onEvidence: (target: { event_id: number }) => void;
  onError: (error: unknown) => void;
}) {
  const context = JSON.stringify([project, epoch]);
  const [pagination, setPagination] = useState<ParserHealthPage & { context: string }>({ context, offset: 0 });
  const [retry, setRetry] = useState(0);
  const [response, setResponse] = useState<{ context: string; data: ParserHealthResult } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const [pending, setPending] = useState('');
  const page = pagination.context === context ? pagination : { context, offset: 0 };
  const key = JSON.stringify([context, page.offset, page.snapshot, page.scopeKey, retry]);
  const currentKey = useRef(key); currentKey.current = key;
  const first = useRef<{ context: string; data: ParserHealthData } | null>(null);
  const result = response?.context === context ? response.data : null;
  const busy = pending === key;
  const error = failure?.key === key ? failure.message : '';

  useEffect(() => {
    const controller = new AbortController();
    setPending(key); setFailure(null);
    const expected = { offset: page.offset, snapshot: page.snapshot, scopeKey: page.scopeKey };
    api<unknown>(`/parser-health?${query({ project, limit: 20, offset: page.offset,
      snapshot: page.snapshot, expected_scope_key: page.scopeKey })}`, undefined, controller.signal)
      .then(raw => {
        if (controller.signal.aborted || currentKey.current !== key) return;
        const data = parseParserHealth(raw, expected);
        if (expected.snapshot != null) {
          if (!data.available || first.current?.context !== context) throw new Error('解析器账本读取身份缺失，请重新读取。');
          assertParserHealthContinuation(first.current.data, data);
        } else first.current = data.available ? { context, data } : null;
        setResponse({ context, data });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || currentKey.current !== key) return;
        const missing = error instanceof ApiError && error.status === 404;
        setFailure({ key, message: missing
          ? '本地服务尚未提供解析器账本接口；类型和工具版本统计缺失。'
          : error instanceof Error ? error.message : '解析器账本读取失败，无法判断记录是否完整。' });
        if (error instanceof ApiError && error.status === 401) onError(error);
      })
      .finally(() => { if (!controller.signal.aborted && currentKey.current === key) setPending(''); });
    return () => controller.abort();
  }, [project, context, key, page.offset, page.snapshot, page.scopeKey, onError]);

  function refresh() {
    setPagination({ context, offset: 0 });
    setRetry(value => value + 1);
  }
  function changePage(offset: number, data: ParserHealthData) {
    setPagination({ context, offset, snapshot: data.snapshot_id, scopeKey: data.scope_key });
  }
  return <section className="panel parser-health" aria-label="解析器类型与工具版本账本">
    <header><div><h3>解析器类型与工具版本</h3><span className="eyebrow">当前项目 · 物理记录及副本</span></div>
      <button className="button secondary" disabled={busy} onClick={refresh}>重新读取解析器账本</button></header>
    <p className="health-explanation">每个来源文件中的物理记录分别登记，包含副本；不等同去重后的原始事件数。一个记录可含多个类型，类型出现次数、各分组记录数与总记录数不能相加。已识别只表示解析器识别该类型，不代表可提取、提取覆盖完整或研究确认。</p>
    {error && <p className="error-message" role="alert">{error}{result?.available && ' 当前保留上一页成功读取的账本；没有混入失败页，请重新读取。'}</p>}
    {busy && <p role="status">{result ? '正在读取，当前仍显示上一次成功返回的账本。' : '正在读取解析器账本…'}</p>}
    {!busy && !result && !error && <p className="missing-note">统计缺失，无法判断类型和工具版本。</p>}
    {result && !result.available && <p className="missing-note">解析器账本不可用，统计缺失；不能视为零或已经观测旧记录。{result.reason && <code>{result.reason}</code>}</p>}
    {result?.available && <>
      <dl className="parser-health-metrics">{parserHealthMetrics.map(([key, name]) =>
        <div key={key} data-parser-metric={key}><dt>{name}</dt><dd>{result[key].toLocaleString()}</dd></div>)}</dl>
      <p className="missing-note">旧记录未观测表示没有当时的解析类型／版本账本，不反向补造。版本未知与版本非法分别保留，不能从邻近文件猜版本。非法版本也计入版本未知，故障与未知标记可重叠，不能相加为异常总数。</p>
      {result.alerts.length > 0 && <div className="parser-health-alerts" aria-label="解析器账本诊断">{result.alerts.map((alert, index) =>
        <p key={`${alert.code}-${index}`}><strong>{alert.message}（{alert.count.toLocaleString()}）</strong><code>{alert.code}</code></p>)}</div>}
      <div className="parser-health-page"><span>类型分组 {result.type_groups_total.toLocaleString()} · 本页 {result.types.length} · 偏移 {result.offset}</span>
        <span>读取快照 #{result.snapshot_id}</span></div>
      {result.types.length === 0 ? <p className="missing-note">本页没有类型分组，仍需结合未观测与故障记录判断，不能据此宣称解析完整。</p>
        : <ul className="parser-health-types">{result.types.map(item => <li key={parserTypeKey(item)}
          data-parser-type-name={item.type_name} data-parser-recognized={String(item.recognized)}>
          <header><strong>{item.tool === 'claude' ? 'Claude Code' : item.tool === 'codex' ? 'Codex' : item.tool}</strong>
            <span className={item.recognized ? 'parser-type-recognized' : 'parser-type-unknown'}>{item.recognized ? '已识别' : '未识别'}</span></header>
          <p className="parser-type-name"><span>{parserCategories[item.category] ?? item.category}</span><code>{item.type_name}</code></p>
          <dl><div><dt>解析器版本</dt><dd>{item.parser_version}</dd></div>
            <div><dt>工具版本</dt><dd>{item.tool_version ?? (item.version_basis === 'invalid' ? '无有效版本值' : '未知')}</dd></div>
            <div><dt>版本依据</dt><dd>{parserVersionBasis[item.version_basis]}</dd></div>
            <div><dt>类型出现次数</dt><dd>{item.occurrences.toLocaleString()}</dd></div>
            <div><dt>相关物理记录</dt><dd>{item.records.toLocaleString()}</dd></div>
            <div><dt>来源文件</dt><dd>{item.source_files.toLocaleString()}</dd></div></dl>
          <p className="small muted">最近入库 <DateText value={item.last_recorded_at} /></p>
          <div className="parser-health-evidence"><button className="text-button" onClick={() => onEvidence({ event_id: item.first_event_id })}>首次原文 #{item.first_event_id}</button>
            {item.last_event_id !== item.first_event_id && <button className="text-button" onClick={() => onEvidence({ event_id: item.last_event_id })}>最近原文 #{item.last_event_id}</button>}</div>
        </li>)}</ul>}
      <div className="pagination"><button className="button secondary" disabled={busy || !!error || result.offset === 0}
        onClick={() => changePage(Math.max(0, result.offset - 20), result)}>上一页解析类型</button>
        <button className="button secondary" disabled={busy || !!error || result.next_offset === null}
          onClick={() => changePage(result.next_offset!, result)}>下一页解析类型</button></div>
    </>}
  </section>;
}
