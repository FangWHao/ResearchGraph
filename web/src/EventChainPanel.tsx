import { DateText } from './DateText';
import { ancestryNames, eventChainDescriptions, eventChainNames, eventChainTargets, parseEventChain, resolutionNames } from './eventChain';
import type { EvidenceTarget } from './types';
import './eventChain.css';

export function EventChainPanel({ value, session, responseEvent, expectedEvent, onOpen }: {
  value: unknown; session: number; responseEvent: number; expectedEvent: number;
  onOpen?: (target: EvidenceTarget) => void;
}) {
  let data; let error = '';
  try {
    if (responseEvent !== expectedEvent) throw new Error('原文响应身份不一致，事件父链暂不显示。');
    data = parseEventChain(value, expectedEvent, session);
  } catch (failure) { error = failure instanceof Error ? failure.message : '事件父链暂不可判断。'; }
  if (data?.state === 'unsupported') return null;
  const targets = data ? eventChainTargets(data) : [];
  return <section className="event-chain" aria-label="Claude 事件父链依据">
    <header><h3>事件父链依据</h3><span className="eyebrow">Claude 原记录声明 · 按来源分别核对</span></header>
    {error ? <p className="error-message" role="alert">{error}</p>
      : !data ? <p className="missing-note">接口未提供事件父链元数据，关系和旁支均未知，不能判断根会话。</p>
      : <><p data-chain-state={data.state}><strong>{eventChainNames[data.state]}</strong></p>
        <p className="small muted">{eventChainDescriptions[data.state]}</p>
        {(!data.source_metadata_complete || data.scope_metadata_incomplete) && <p className="missing-note" data-chain-incomplete="true">
          {!data.source_metadata_complete && '本来源的父链元数据尚未完整登记。'}
          {data.scope_metadata_incomplete && '当前项目仍有来源元数据补记缺口。'}正常后续扫描可继续补记；当前关联和引用不代表完整父链。</p>}
        <dl className="event-chain-metadata">
          <div><dt>本事件的来源实例</dt><dd>#{data.file_instance_id}</dd></div>
          <div><dt>原记录首个内容块</dt><dd>{data.record_event_id === null ? '尚未观测'
            : onOpen ? <button className="text-button" onClick={() => onOpen({ event_id: data.record_event_id! })}>打开本记录原文 #{data.record_event_id}</button> : `#${data.record_event_id}`}</dd></div>
          <div><dt>源记录 UUID</dt><dd><code>{data.native_uuid ?? (data.uuid_state === 'invalid' ? '无效' : '未记录')}</code></dd></div>
          <div><dt>源声明父 UUID</dt><dd><code>{data.parent_uuid ?? (data.parent_state === 'null' ? '明确为 null' : data.parent_state === 'invalid' ? '无效' : '未声明')}</code></dd></div>
          <div><dt>旁支字段</dt><dd data-chain-sidechain={data.sidechain_state}>{data.sidechain_state === 'declared'
            ? data.is_sidechain ? '源记录声明为旁支（true）' : '源记录声明非旁支（false）'
            : data.sidechain_state === 'invalid' ? '字段无效，旁支未知' : '未声明，旁支未知'}</dd></div>
          <div><dt>关联核对范围</dt><dd>{resolutionNames[data.resolution_scope]}</dd></div>
          <div><dt>依据</dt><dd>{data.basis === 'direct_record' ? '原记录直接声明' : '尚无观测'}</dd></div>
          <div><dt>元数据入库时间</dt><dd><DateText value={data.recorded_at} /></dd></div>
        </dl>
        {targets.length > 0 && <div className="event-chain-parents"><p>可核对的等价父记录 · 展示 {targets.length}／共 {data.parent_references_total} 条，{data.parent_source_contexts} 个来源上下文</p>
          <p className="small muted">请按来源打开所需副本；界面不替你选择一个作为唯一父原文。</p>
          {data.parent_references_partial && <p className="missing-note" data-chain-partial="true">父引用仅展示前二十条，清单有省略，不代表全部副本。</p>}
          <ul>{data.parent_references.map((row, index) => <li key={row.event_id} data-chain-reference={row.event_id}>
            <span>来源实例 #{row.file_instance_id}</span>{onOpen ? <button className="text-button" onClick={() => onOpen(targets[index])}>打开父记录原文 #{row.event_id}</button> : <span>原文 #{row.event_id}</span>}</li>)}</ul>
        </div>}
        <div className="event-chain-ancestry" data-chain-ancestry={data.ancestry_state}>
          <p>祖先元数据检查：{ancestryNames[data.ancestry_state]} · {data.ancestry_steps}／{data.ancestry_limit} 步</p>
          <p className="small muted">只检查已存元数据，遇缺口、多来源或上限会停止；不证明完整祖先链或根会话。</p>
        </div>
      </>}
  </section>;
}
