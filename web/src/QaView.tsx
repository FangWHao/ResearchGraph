import { DateText, Icon } from './components';
import { actionNames, evidenceNames, reviewNames, scopeText } from './model';
import { qaEvidence } from './qa';
import type { QaSource } from './qa';
import type { useQaWorkspace } from './useQaWorkspace';
import './qa.css';

const stateNames: Record<string, string> = { ...actionNames, ...evidenceNames, unknown: '未知', unknown_scope: '范围未知', time_unknown: '发生时间未知', conflict: '存在冲突' };
const basisNames: Record<string, string> = { manual: '人工记录', model: '模型候选', model_inference: '模型推断', rule: '规则记录', source: '原话记录', direct_record: '直接原话', time_match: '时间匹配' };
function Source({ source, onEvidence }: { source: QaSource; onEvidence: (target: ReturnType<typeof qaEvidence>) => void }) {
  return <article className="qa-source" id={`qa-source-${source.citation_id}`} tabIndex={-1} data-testid="qa-source">
    <header><strong>{source.citation_id} · 原文事件 #{source.event_id}</strong><button className="text-button" onClick={() => onEvidence(qaEvidence(source))}>打开此处原文 <Icon name="arrow" size={14} /></button></header>
    <p className="muted small">发生 <DateText value={source.occurred_at} /> · 收录 <DateText value={source.recorded_at} /></p>
    <pre className="qa-text" data-testid="qa-source-text">{source.text}</pre>
    <p className="small muted">原文窗口字节 {source.window_start}–{source.window_end}{source.source_byte_start != null && ` · 来源文件字节 ${source.source_byte_start}–${source.source_byte_end}`}</p>
    {source.window_truncated && <p className="notice small">当前仅为有界原文窗口，不能据此断言完整会话已覆盖。可打开原文继续查看。</p>}
    <details><summary>关联记录与独立状态（{source.records.length}）</summary>
      {!source.records.length && <p className="muted small">此处尚无关联结构化记录，原文仍可检索。</p>}
      {source.records.map(record => <section className="qa-record" key={record.claim_id}>
        <p><strong>记录 #{record.claim_id}</strong> <span className={`badge ${record.effective_state}`}>{reviewNames[record.effective_state as keyof typeof reviewNames] ?? `状态未知（${record.effective_state}）`}</span>{record.replaced && <span className="small"> 已被替换</span>}</p>
        <p className="small muted">{basisNames[record.basis] ?? record.basis} · {record.actor} · {scopeText(record.scope)}</p>
        <p className="small muted">发生 <DateText value={record.occurred_at} /> · 获知 <DateText value={record.recorded_at} /></p>
        <pre className="qa-text small">{record.payload_preview}</pre>{record.payload_preview_truncated && <p className="small missing-note">记录内容仅为预览。</p>}
        <div className="qa-state-row">{Object.entries(record.computed_states ?? {}).map(([axis, value]) => <span className="small" key={axis}>{axis === 'adoption' ? '采用状态' : axis === 'evidence_state' ? '证据状态' : axis}：{stateNames[value.state] ?? `未知（${value.state}）`}{value.claim_ids?.length ? ` · 依据记录 ${value.claim_ids.map(id => `#${id}`).join('、')}` : ''}{value.claim_ids_partial && '（依据记录仅展示部分）'}</span>)}</div>
      </section>)}{source.records_partial && <p className="notice small">关联记录仅显示部分，不能按此列表判断项目全貌。</p>}
    </details>
  </article>;
}
export function QaView({ workspace: w, onEvidence }: { workspace: ReturnType<typeof useQaWorkspace>; onEvidence: (target: ReturnType<typeof qaEvidence>) => void }) {
  function locate(id: string) { const card = document.getElementById(`qa-source-${id}`); card?.scrollIntoView({ behavior: 'smooth', block: 'center' }); card?.focus({ preventScroll: true }); }
  return <div className="qa-workspace">
    <form className="qa-form" onSubmit={event => { event.preventDefault(); void w.submit(); }}>
      <label>想了解什么？<textarea aria-label="研究问答问题" value={w.draft.question} onChange={event => w.changeDraft({ ...w.draft, question: event.target.value })} rows={3} placeholder="例如：为什么暂缓这个方案？目前有哪些依据？" required /></label>
      <details className="qa-filters"><summary>范围与历史时间（可选）</summary><p className="muted small">留空范围表示检索所有范围，并不表示某条决定有确定范围。截止时间留空表示当前已记录的历史。</p>
        <label>完整范围筛选<textarea aria-label="问答完整范围" value={w.draft.scopeText} onChange={event => w.changeDraft({ ...w.draft, scopeText: event.target.value })} rows={2} placeholder="每行一个 字段=值；留空检索所有范围" /></label>
        <div className="qa-filter-grid"><label>发生截止（含时区）<input aria-label="问答发生截止" value={w.draft.occurredUntil} onChange={event => w.changeDraft({ ...w.draft, occurredUntil: event.target.value })} placeholder="2026-10-10T12:00:00+08:00" /></label><label>获知截止（含时区）<input aria-label="问答获知截止" value={w.draft.knownUntil} onChange={event => w.changeDraft({ ...w.draft, knownUntil: event.target.value })} placeholder="2026-10-10T12:00:00+08:00" /></label><label>最多来源条数<input aria-label="问答来源条数" type="number" min={1} max={100} value={w.draft.k} onChange={event => w.changeDraft({ ...w.draft, k: Number(event.target.value) })} /></label><label>每条原文窗口（字节）<input aria-label="问答窗口字节" type="number" min={4} max={24000} value={w.draft.maxBytes} onChange={event => w.changeDraft({ ...w.draft, maxBytes: Number(event.target.value) })} /></label></div>
      </details>
      <div className="qa-form-footer"><p className="small muted">先在本地找来源；模型解释不会确认候选或改写研究记录。<br />{w.options ? w.options.configured ? `服务端模型：${w.options.model ?? '名称未知'} · ${w.options.remote === true ? '远程' : w.options.remote === false ? '本地' : '位置未知'} · ${w.options.remote_allowed ? '项目已允许外发' : '项目未允许外发'}` : '尚未配置模型，仍可本地检索。' : '正在读取模型配置；本地检索保持可用。'}</p><div className="qa-query-actions"><button className="button secondary" disabled={!!w.busy} type="button" onClick={() => { void w.retrieve(); }}>仅本地检索</button><button className="button primary" disabled={!!w.busy} type="submit">{w.busy ?? '查找答案'}</button></div></div>
    </form>
    {w.busy && <p className="notice" role="status">{w.busy} <button className="text-button" onClick={w.cancel}>停止页面等待</button></p>}
    {w.error && <div className="error-message" role="alert">{w.error}{w.conflict && <><p>资料版本已变化。刷新后请重新点击查询或授权，不会自动重发。</p><button className="button secondary" onClick={w.refresh}>刷新问答状态</button></>}</div>}
    {w.info && <p className="notice" role="status">{w.info}</p>}
    {w.preview && <section className="qa-consent" aria-label="首次外发授权"><span className="eyebrow">首次外发 · 本地预览</span><h2>查看将发送的内容</h2><p>开启后，整个项目的自动提取、模型计数和问答都可使用已配置的远程模型，后续新增资料也包括在内。无需先人工复核候选。密钥由服务端管理。</p><p className="small">问题{w.preview.input.question_redacted ? '已遮盖敏感字段' : '未触发遮盖'} · {w.preview.source_count} 个来源 · 版本 {w.preview.revision}</p>
      {w.preview.input.sources.map((source, index) => <p className="small muted" key={index}>{source.citation_id}：原文{source.text_redacted ? '已遮盖' : '未触发遮盖'}，记录{source.records_redacted ? '已遮盖' : '未触发遮盖'}</p>)}
      <details open><summary>实际发送的输入字段与样例</summary><pre className="qa-text qa-preview" data-testid="qa-preview" tabIndex={0} aria-label="实际发送输入，可滚动">{JSON.stringify(w.preview.input, null, 2)}</pre></details>
      <p className="small muted">预览校验值 <span className="mono">{w.preview.preview_sha}</span></p><p className="small muted">{w.preview.notice}</p>
      <label className="qa-checkbox"><input type="checkbox" checked={w.consent} onChange={event => w.consentChange(event.target.checked)} disabled={!!w.busy} /><span>我已查看此预览，允许本项目及其后续新增资料按上述范围外发到服务端已配置的远程模型。</span></label><button className="button primary" onClick={() => { void w.allow(); }} disabled={!!w.busy || !w.consent || !w.preview.source_count || w.preview.remote !== true}>开启项目外发并回答</button>
    </section>}
    {w.result && <div className="qa-results">
      {w.policyStale && <p className="notice">下方结果使用此前的项目遮盖规则，保留供追溯；请重新查询以使用当前规则。</p>}
      {w.stale && <p className="notice">下方结果来自此前输入；当前问题、范围或截止已变化，请重新查询。</p>}
      <p className="small muted">检索版本 {w.result.revision} · {w.result.scope_filter ? `完整范围：${scopeText(w.result.scope)}` : '所有范围（未限定）'} · 发生截止 {w.result.occurred_until ? <DateText value={w.result.occurred_until} /> : '未限定'} · 获知截止 {w.result.known_until ? <DateText value={w.result.known_until} /> : '当前已知'}</p>
      {w.result.status && <section className="qa-answer" aria-label="模型解释"><span className="eyebrow">模型解释 · 不是已确认事实</span><h2>{w.result.status === 'answered' ? '根据这些来源' : w.result.status === 'insufficient' ? '现有证据不足以判断' : '模型暂时无法回答'}</h2>
        {w.result.records_changed_during_answer && <p className="notice">生成期间研究记录发生变化。此解释基于上述检索版本，请重新查询以核对最新记录。</p>}
        {w.result.statements?.map((statement, index) => <article className="qa-statement" key={index}><p>{statement.text}</p>{statement.citations.map((citation, n) => <div className="qa-citation" key={n}><button className="text-button" onClick={() => locate(citation.id)}>定位来源 {citation.id}</button><blockquote>{citation.quote}</blockquote>{!w.result!.sources.find(source => source.citation_id === citation.id)?.text.includes(citation.quote) && <p className="small muted">引文可能包含发送前的遮盖文本；请对照预览及本地原文。</p>}</div>)}</article>)}
        {!!w.result.caveats?.length && <div><h3>不确定事项</h3><ul>{w.result.caveats.map((text, index) => <li key={index}>{text}</li>)}</ul></div>}
      </section>}
      <section aria-label="问答来源"><h2>本地来源 <span className="muted">{w.result.sources.length}</span></h2><p className="muted small">候选、已确认、已驳回和被替换记录保留各自状态；检索结果不会自动变成已确认事实。</p><div className="qa-sources">{w.result.sources.map(source => <Source key={source.citation_id} source={source} onEvidence={onEvidence} />)}</div>{!w.result.sources.length && <p className="notice">没有有效来源，无法判断。</p>}</section>
      <section className="qa-gaps"><h3>覆盖与缺口</h3><p className="small">匹配记录 {w.result.matched_claims} · 原文匹配检查 {w.result.raw_matches_examined} · 已知缺口 {w.result.gaps_total}</p>{w.result.retrieval_partial && <p className="notice">此次检索有范围或数量边界，仍有未覆盖内容；不能据此认定研究历史完整。</p>}{w.result.gaps.map((gap, index) => <pre className="qa-text small" key={index}>{JSON.stringify(gap, null, 2)}</pre>)}{w.result.gaps_total > w.result.gaps.length && <p className="small muted">当前仅展示部分缺口。</p>}<p className="small muted">{w.result.notice}</p></section>
    </div>}
    {w.options?.remote_allowed && <div className="qa-permission"><p className="small muted">本项目已允许远程模型处理；撤回后可继续本地检索。</p><button className="text-button" disabled={!!w.busy} onClick={() => { void w.disable(); }}>撤回项目外发许可</button></div>}
  </div>;
}
