import { DateText } from './DateText';
import { editPresentation, editRequestValidation, gapText, runStateText, versionMetadata } from './l1';
import type { ArtifactVersion, EvidenceTarget, L1Evidence } from './types';
import './l1.css';
import { RunManifests } from './RunManifests';

function EventLink({ id, label, current, onOpen }: {
  id: number | null; label: string; current: number; onOpen?: (target: EvidenceTarget) => void;
}) {
  if (id == null) return <span className="missing-note">{label}位置缺失</span>;
  if (!onOpen) return <span className="muted small">{label} #{id}</span>;
  return <button className="text-button" disabled={id === current} onClick={() => onOpen({ event_id: id })}>
    {label} #{id}{id === current ? ' · 当前' : ''}
  </button>;
}

function Gap({ value }: { value: string | null | undefined }) {
  return value ? <p className="missing-note">{gapText(value)}<span className="l1-code mono">{value}</span></p> : null;
}

function Times({ occurred, recorded }: { occurred: string | null; recorded: string }) {
  return <dl className="l1-times"><div><dt>发生时间</dt><dd><DateText value={occurred} /></dd></div>
    <div><dt>入库时间</dt><dd><DateText value={recorded} /></dd></div></dl>;
}

export function VersionRecords({ versions, partial }: { versions: ArtifactVersion[]; partial?: boolean }) {
  return <section className="artifact-section l1-section" aria-label="相关文件版本">
    <h4>相关文件版本</h4>
    {partial === true && <p className="missing-note">版本清单超过接口上限，当前只显示部分，不能视为全部版本。</p>}
    {partial == null && <p className="missing-note">接口未提供版本清单完整性标记，无法判断是否还有未显示的版本。</p>}
    {versions.length ? <ul className="l1-versions">{versions.map(version => {
      const meta = versionMetadata(version);
      return <li key={version.version_id} data-version-id={version.version_id}>
        <div className="l1-heading"><strong>{meta.phase}</strong><span>{meta.review}</span></div>
        <p className="path">{version.path}</p>
        <p className="l1-version-meta">依据：{meta.basis} · {meta.representation}</p>
        <code>{version.algo}:{version.digest}</code>
        <small className="muted">来源标识：{version.source}</small>
      </li>;
    })}</ul> : <p className="muted small">当前原文没有关联的文件版本记录。</p>}
  </section>;
}

export function EvidenceRecords({ data, current, onOpen, onError }: {
  data: L1Evidence | null | undefined; current: number; onOpen?: (target: EvidenceTarget) => void; onError?: (error: unknown) => void;
}) {
  if (!data) return <section className="l1-section" aria-label="运行与编辑证据"><h4>运行与编辑证据</h4>
    <p className="missing-note">接口未提供运行与编辑映射，相关证据是否存在未知。</p></section>;
  return <div className="l1-records">
    <section className="l1-section" aria-label="本原文派生状态">
      <h4>本原文派生状态</h4>
      {data.derivation ? <>
        <p className="l1-derivation-state">{{ queued: '等待处理', waiting: '等待关联', done: '已处理', failed: '处理异常' }[data.derivation.state] ?? '处理状态未知'}</p>
        <p className="muted small">更新于 <DateText value={data.derivation.updated_at} /></p>
        {data.derivation.error && <p className="missing-note">处理说明：{gapText(data.derivation.error)}<span className="l1-code">{data.derivation.error}</span></p>}
      </> : <p className="missing-note">没有本原文的派生状态记录，无法判断处理是否完整。</p>}
      <p className="l1-notice">已处理仅表示派生程序处理过本原文，不证明存在退出结果或完整文件版本；等待关联或处理异常时，相关缺口仍保留。</p>
    </section>
    <section className="l1-section" aria-label="运行证据">
      <h4>运行证据 <span>{data.runs.length} 条当前返回记录</span></h4>
      <p className="l1-notice">以下状态来自会话中的原生工具观测。已有启动记录不证明进程现在仍在运行，缺少结束记录不等于失败；入库时间不作为发生时间。</p>
      {data.runs_partial === true && <p className="missing-note">运行记录超过接口上限，当前只显示部分，不能视为全部运行。</p>}
      {data.runs_partial == null && <p className="missing-note">接口未提供运行清单完整性标记，无法判断是否还有未显示的运行。</p>}
      {!data.runs.length && <p className="muted small">当前原文未关联到运行记录，不能据此判断整个会话是否执行过命令。</p>}
      {data.runs.map(run => <article className="l1-card" key={run.run_id} data-run-id={run.run_id}>
        <div className="l1-heading"><strong>{runStateText(run.state, run.exit_code)}</strong><span className="muted">运行记录</span></div>
        {run.command_truncated === true && <p className="missing-note">当前仅为命令预览，完整命令见请求原文。{Number.isSafeInteger(run.command_total_bytes) && run.command_total_bytes >= 0 ? `命令共 ${run.command_total_bytes} UTF-8 字节。` : ''}</p>}
        {run.command_truncated == null && run.command && <p className="missing-note">接口未提供命令完整性标记，当前显示内容是否完整未知，请查看请求原文核对。</p>}
        {run.command ? <pre className="l1-command" tabIndex={0} aria-label={run.command_truncated === true ? '历史命令预览，仅供阅读' : run.command_truncated === false ? '历史命令原文，仅供阅读' : '历史命令内容，完整性未知'}>{run.command}</pre> : <p className="missing-note">命令原文缺失。</p>}
        <p className="path">工作目录：{run.cwd ?? '未知'}</p>
        <EventLink id={run.request_event_id} label="请求原文" current={current} onOpen={onOpen} />
        <Gap value={run.gap} />
        <details className="l1-observations" open><summary>逐条运行观测 · {run.observations.length} 条</summary>
          {run.observations_partial === true && <p className="missing-note">观测超过接口上限，当前列表不完整。</p>}
          {run.observations_partial == null && <p className="missing-note">接口未提供观测清单完整性标记，无法判断是否还有未显示的观测。</p>}
          {!run.observations.length && <p className="missing-note">观测明细缺失，无法核对汇总状态。</p>}
          <ol>{run.observations.map((item, index) => <li key={`${item.event_id}-${index}`}>
            <div className="l1-heading"><strong>{runStateText(item.state, item.exit_code)}</strong>
              <EventLink id={item.event_id} label="观测原文" current={current} onOpen={onOpen} />
            </div>
            <Times occurred={item.occurred_at} recorded={item.recorded_at} />
            {item.executor_session_id != null && <p className="muted small">执行器会话标识：{item.executor_session_id}</p>}
            <Gap value={item.reason} />
            {Object.keys(item.details).length > 0 && <details className="l1-details"><summary>观测关联字段</summary><pre>{JSON.stringify(item.details, null, 2)}</pre></details>}
          </li>)}</ol>
        </details>
        <RunManifests native={run} current={current} onOpen={onOpen} onError={onError} />
      </article>)}
    </section>

    <section className="l1-section" aria-label="编辑证据">
      <h4>编辑证据 <span>{data.edits.length} 条当前返回记录</span></h4>
      {data.edits_partial === true && <p className="missing-note">编辑记录超过接口上限，当前只显示部分，不能视为全部编辑。</p>}
      {data.edits_partial == null && <p className="missing-note">接口未提供编辑清单完整性标记，无法判断是否还有未显示的编辑。</p>}
      {!data.edits.length && <p className="muted small">当前原文未关联到编辑记录。</p>}
      {data.edits.map(edit => {
        const presentation = editPresentation(edit);
        const validation = editRequestValidation(edit);
        return <article className="l1-card" key={edit.edit_id} data-edit-id={edit.edit_id}>
          <div className="l1-heading"><strong>{presentation.title}</strong><span>工具记录</span></div>
          <p className="path">{edit.path ?? '文件路径未知'}</p>
          <div className="l1-links"><EventLink id={edit.request_event_id} label="编辑请求原文" current={current} onOpen={onOpen} />
            <EventLink id={edit.result_event_id} label="编辑结果原文" current={current} onOpen={onOpen} /></div>
          <Times occurred={edit.occurred_at} recorded={edit.recorded_at} />
          {(edit.request_validation != null || edit.operation === 'multiedit') && <div data-request-validation={validation.status}>
            <p className={validation.status === 'matches_request' ? 'l1-notice' : 'missing-note'}><strong>工具请求核验：{validation.title}</strong></p>
            <p className="l1-notice">{validation.reason}</p>
          </div>}
          {validation.reportedAfter && <p className="missing-note">此前登记的编辑后候选仍保留为历史工具报告，不能作为本次已核定的编辑后完整版本。<span className="l1-code mono" data-reported-after-version={validation.reportedAfter}>{validation.reportedAfter}</span></p>}
          <p className="l1-notice">{presentation.reason}</p>
          <Gap value={edit.association_gap} /><Gap value={edit.gap} />
          {edit.diff.gap && edit.diff.gap !== edit.gap && <Gap value={edit.diff.gap} />}
          {edit.user_modified === 1 && <p className="missing-note">工具报告用户同时修改，不能把所有变化归给 Agent。</p>}
          {presentation.text != null && <pre className="l1-diff" aria-label={presentation.kind === 'patch_only' ? '工具报告的补丁正文' : presentation.kind === 'reported_before' ? '工具报告的候选编辑前全文' : presentation.kind === 'reported_after' ? '工具报告的候选编辑后全文' : '候选前后版本差异正文'}>{presentation.text || (presentation.kind === 'patch_only' ? '补丁正文为空，前后文件是否相同仍未知。' : presentation.kind === 'reported_before' ? '工具报告的编辑前全文为空；编辑后版本未知。' : presentation.kind === 'reported_after' ? '工具报告的编辑后全文为空；编辑前版本未知。' : '工具报告的前后文本没有差异。')}</pre>}
          <details className="l1-details"><summary>版本与补丁标识</summary>
            <dl className="l1-identifiers"><div><dt>编辑前版本</dt><dd>{edit.before_version ?? '未知'}</dd></div><div><dt>编辑后版本</dt><dd>{edit.after_version ?? '未知'}</dd></div>
              {validation.reportedAfter && <div><dt>历史报告后候选版本</dt><dd>{validation.reportedAfter}</dd></div>}
              <div><dt>补丁摘要</dt><dd>{edit.patch_sha256 ?? '未知'}</dd></div><div><dt>工具操作标识</dt><dd>{edit.operation}</dd></div></dl>
          </details>
        </article>;
      })}
    </section>
  </div>;
}
