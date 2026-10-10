import { DateText } from './DateText';
import { parentBasisNames, parentEvidenceTarget, parentObservationNames, parentReasonNames,
  parseSessionParent, sessionParentDescriptions, sessionParentNames } from './sessionParent';
import type { EvidenceTarget } from './types';
import './sessionParent.css';

export function SessionParentPanel({ value, session, responseEvent, expectedEvent, onOpen }: {
  value: unknown; session: number; responseEvent: number; expectedEvent: number;
  onOpen?: (target: EvidenceTarget) => void;
}) {
  let data;
  let error = '';
  try {
    if (responseEvent !== expectedEvent) throw new Error('原文响应身份不一致，父线程关系暂不显示。');
    data = parseSessionParent(value, session);
  } catch (failure) { error = failure instanceof Error ? failure.message : '父线程关系暂不可判断。'; }
  if (data?.state === 'unsupported') return null;
  const parent = data ? parentEvidenceTarget(data) : null;
  return <section className="session-parent" aria-label="Codex 父线程依据">
    <header><h3>父线程依据</h3><span className="eyebrow">会话头源声明 · 当前关联单独核对</span></header>
    {error ? <p className="error-message" role="alert">{error}</p>
      : !data ? <p className="missing-note">接口未提供父线程观测，关系未知；不能据此判断为根线程。</p>
      : <><p className="session-parent-state" data-parent-state={data.state}><strong>{sessionParentNames[data.state]}</strong></p>
        <p className="small muted">{sessionParentDescriptions[data.state]}</p>
        {data.state === 'linked' && <div className="session-parent-linked"><span>同项目父会话 #{data.parent_session_pk}</span>
          <code>{data.parent_native_id}</code>
          {parent && onOpen ? <button className="text-button" onClick={() => onOpen(parent)}>打开父线程头原文 #{parent.event_id}</button>
            : <span className="small muted">父头原文导航暂不可用。</span>}</div>}
        {data.observations_partial && <p className="missing-note" data-parent-partial="true">仅展示最近 {data.observations.length} / {data.observations_total} 条头观测。当前关联状态由后端核对全部观测得出，不能只凭本页推断完整声明历史。</p>}
        {data.observations.length > 0 && <details className="session-parent-observations" open>
          <summary>头记录源声明 · 本页 {data.observations.length} 条／共 {data.observations_total} 条</summary>
          <ul>{data.observations.map(row => <li key={row.event_id} data-parent-observation={row.event_id}>
            <div><strong>{parentObservationNames[row.state]}</strong>
              {onOpen && <button className="text-button" onClick={() => onOpen({ event_id: row.event_id })}>打开声明原文 #{row.event_id}</button>}</div>
            <dl><div><dt>来源依据</dt><dd>{parentBasisNames[row.basis]}</dd></div>
              <div><dt>诊断</dt><dd>{parentReasonNames[row.reason] ?? `未知诊断（${row.reason}）`}</dd></div>
              <div><dt>源声明的父线程 ID</dt><dd><code>{row.parent_id ?? '未声明或无有效值'}</code></dd></div>
              {row.other_parent_id && <div><dt>另一处源声明</dt><dd><code>{row.other_parent_id}</code></dd></div>}
              <div><dt>观测入库时间</dt><dd><DateText value={row.recorded_at} /></dd></div></dl>
          </li>)}</ul>
        </details>}
      </>}
  </section>;
}
