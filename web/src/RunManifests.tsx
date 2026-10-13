import { DateText } from './DateText';
import { scopeText } from './model';
import { versionMetadata } from './l1';
import { objectFields } from './versions';
import { manifestPage, manifestPresentation, manifestRoles, manifestVersion, reportedRole } from './runManifest';
import type { EvidenceTarget, L1Run, ManifestRole } from './types';
import { useRunManifests } from './useRunManifests';
import './runManifest.css';

function text(value: unknown): string {
  if (value == null) return '未知';
  return typeof value === 'string' ? value : JSON.stringify(value);
}
function eventId(value: unknown): number | null { return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : null; }
function Diagnostic({ issues }: { issues: string[] }) { return issues.length ? <ul className="manifest-diagnostics">{issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul> : null; }
function IO({ item, project, reported }: { item: unknown; project: string; reported: Record<string, unknown> }) {
  const meta = manifestVersion(item, project); const version = meta.version; const review = version ? versionMetadata(version) : null;
  const ordinal = typeof meta.fields.ordinal === 'number' && Number.isSafeInteger(meta.fields.ordinal) && meta.fields.ordinal >= 0 ? meta.fields.ordinal : null;
  const role = meta.role ? reportedRole(reported, meta.role) : null;
  const mismatch = !role || role.status !== 'reported' || ordinal == null || role.ids[ordinal] !== meta.fields.requested_version_id;
  return <li className="manifest-io" data-io-id={typeof meta.fields.io_id === 'string' ? meta.fields.io_id : undefined}>
    <div className="manifest-heading"><strong>{meta.role ? manifestRoles[meta.role] : '角色未知'}{ordinal != null ? ` · 第 ${ordinal + 1} 项` : ''}</strong><span>候选报告关联 · 不证明实际使用</span></div>
    <p className="manifest-id">报告版本 ID：{text(meta.fields.requested_version_id)}</p>
    <Diagnostic issues={[...meta.issues, ...(mismatch ? ['I/O 条目与报告角色列表的序号或版本 ID 不一致，不能确定对应关系。'] : [])]} />
    {version ? <>
      <p><strong>{meta.origin?.title}</strong> · 版本审核：{review?.review}</p><p className="path">{text(version.path)}</p>
      <dl className="manifest-fields"><div><dt>版本内容标识</dt><dd>{version.algo ?? '算法未知'}:{version.digest ?? '摘要未知'}</dd></div><div><dt>版本表示与依据</dt><dd>{review?.representation} · {review?.basis}</dd></div>
        <div><dt>记录大小</dt><dd>{typeof version.size === 'number' && Number.isSafeInteger(version.size) && version.size >= 0 ? `${version.size} 字节` : '未知'}</dd></div><div><dt>已保存正文摘要</dt><dd>{version.content_sha256 ?? '未提供；不代表运行未输出文件'}</dd></div></dl>
      <p className="muted small">{meta.origin?.note}</p><dl className="manifest-times"><div><dt>版本观察时间</dt><dd><DateText value={version.occurred_at ?? version.observed_at} /></dd></div><div><dt>版本已知时间</dt><dd><DateText value={version.recorded_at} /></dd></div></dl>
      {Array.isArray(version.provenance_warnings) && version.provenance_warnings.map((warning, index) => <p className="missing-note" key={index}>{text(warning)}</p>)}
    </> : <><p className="missing-note">版本未知或当前不可见，不能补造版本内容、路径或实际输入输出。</p>{meta.fields.resolved_version_id != null && <p className="manifest-id">返回解析 ID：{text(meta.fields.resolved_version_id)}（身份未核定）</p>}</>}
  </li>;
}
function Manifest({ item, native, current, onOpen, first, pagination }: { item: unknown; native: L1Run; current: number; onOpen?: (target: EvidenceTarget) => void; first: boolean; pagination: ReturnType<typeof useRunManifests> }) {
  const view = manifestPresentation(item, native); const fields = view.fields; const reported = view.reported;
  const ioValues = objectFields(fields.io);
  const io = manifestPage(fields.io, pagination.revision == null ? 100 : Math.max(100, Array.isArray(ioValues?.items) ? ioValues.items.length : 0)); const source = eventId(fields.evidence_event_id);
  const snapshot = objectFields(fields.declared_snapshot); const scope = objectFields(fields.scope);
  const scopeValid = fields.scope === null || scope != null && Object.values(scope).every(value => typeof value === 'string');
  const seed = typeof reported.seed === 'string' && /^-?\d+$/.test(reported.seed) ? reported.seed : null;
  return <details className="run-manifest" open={first} data-manifest-id={typeof fields.request_id === 'string' ? fields.request_id : undefined}>
    <summary>候选运行清单 · {typeof fields.request_id === 'string' ? fields.request_id : '标识未知'}</summary>
    <p className="manifest-state">直接记录 · 待复核 · {view.binding}</p>
    <p className="manifest-notice">清单是本机显式报告，不能覆写上方原生观测，也不确认研究尝试、文件版本或运行成功。实际 I/O 完整性未知。</p>
    <Diagnostic issues={view.issues} />
    {view.conflict && <p className="manifest-conflict" role="alert">清单报告退出码与原生观测冲突。原生退出码 {native.exit_code ?? '未知'}，清单报告 {view.code ?? '未知'}；两者分别保留，不用报告改写原生状态。</p>}
    <dl className="manifest-fields"><div><dt>报告退出码</dt><dd>{view.code ?? '未知'}{native.state !== 'exited' && view.code != null && '（原生观测未证实结束）'}</dd></div><div><dt>研究尝试 ID</dt><dd>{text(fields.attempt_id)}</dd></div><div><dt>研究范围</dt><dd>{scopeValid ? scopeText(scope as Record<string, string> | null) : '范围字段异常，范围未知'}</dd></div></dl>
    <dl className="manifest-times"><div><dt>清单发生时间</dt><dd><DateText value={typeof fields.occurred_at === 'string' ? fields.occurred_at : null} /></dd></div><div><dt>清单入库时间</dt><dd><DateText value={typeof fields.recorded_at === 'string' ? fields.recorded_at : null} /></dd></div><div><dt>报告开始时间</dt><dd><DateText value={typeof reported.started_at === 'string' ? reported.started_at : null} /></dd></div><div><dt>报告结束时间</dt><dd><DateText value={typeof reported.ended_at === 'string' ? reported.ended_at : null} /></dd></div></dl>
    {source && onOpen ? <button className="text-button" disabled={source === current} onClick={() => onOpen({ event_id: source })}>清单原文 #{source}{source === current ? ' · 当前' : ''}</button> : <p className="missing-note">{source ? `清单原文 #${source}` : '清单原文位置缺失或无效。'}</p>}
    <div className="manifest-role-grid">{(Object.keys(manifestRoles) as ManifestRole[]).map(role => {
      const values = reportedRole(reported, role);
      return <section key={role} data-manifest-role={role}><h5>{manifestRoles[role]}版本</h5><p className={values.status === 'reported' ? 'small muted' : 'missing-note'}>{values.text}</p>{values.ids.length > 0 && <details><summary>查看明确报告的版本 ID</summary><ol className="manifest-requested-ids">{values.ids.map((id, index) => <li key={index}>{id}</li>)}</ol></details>}</section>;
    })}</div>
    <details className="manifest-parameters"><summary>报告参数与随机种子</summary><p className="manifest-id">随机种子：{seed ?? '未知'}（按十进制文本保留，不执行、不转成浏览器整数）</p>{reported.seed != null && seed == null && <p className="missing-note">种子字段格式异常：{text(reported.seed)}</p>}{objectFields(reported.parameters) ? <pre tabIndex={0} aria-label="清单报告参数，仅供阅读">{JSON.stringify(reported.parameters, null, 2)}</pre> : <p className="missing-note">报告参数未知或格式异常。</p>}</details>
    <details className="manifest-snapshot"><summary>明确声明的快照</summary><p className="manifest-id">声明快照 ID：{text(fields.snapshot_id)}</p>{snapshot && snapshot.snapshot_id === fields.snapshot_id ? <><p className="manifest-id">保存提交：{text(snapshot.shadow_commit)}</p><p className="manifest-id">根目录标识：{text(snapshot.root_id)}</p><dl className="manifest-times"><div><dt>快照发生时间</dt><dd><DateText value={typeof snapshot.taken_at === 'string' ? snapshot.taken_at : null} /></dd></div><div><dt>快照入库时间</dt><dd><DateText value={typeof snapshot.recorded_at === 'string' ? snapshot.recorded_at : null} /></dd></div></dl>{snapshot.skipped != null && <p className="missing-note">快照跳过记录：{text(snapshot.skipped)}</p>}{snapshot.async_race === true || snapshot.async_race === 1 ? <p className="missing-note">快照存在异步竞争标记，不能确定它对应运行当时的完整内容。</p> : snapshot.async_race == null && <p className="missing-note">快照异步竞争状态未知。</p>}<p className="small muted">明确声明快照不证明运行实际使用其内容；当前文件哈希不回填此快照。</p></> : <p className="missing-note">声明快照未知、当前不可见或标识不一致，不按最近时间补关联。</p>}</details>
    {view.unknown.length > 0 && <p className="missing-note">清单明确未知字段：{view.unknown.join('、')}</p>}
    <details className="manifest-io-section" open={first}><summary>候选版本关联 · 当前 {io.items.length} 项 / 总计 {io.total ?? '未知'}</summary>
      <Diagnostic issues={io.issues} />{io.partial && <p className="missing-note">版本关联仅显示部分或完整性未知；接口每页最多返回 100 项，遗漏条目不能据此判定未使用。</p>}
      {!io.items.length && <p className="missing-note">当前未返回版本关联；仍以各角色的报告未知/明确空列表区分，实际 I/O 完整性未知。</p>}
      {io.items.length > 0 && <ol className="manifest-io-list" tabIndex={0} aria-label="运行清单版本关联，可纵向滚动">{io.items.map((entry, index) => <IO key={typeof entry.io_id === 'string' ? entry.io_id : index} item={entry} project={native.project_id} reported={reported} />)}</ol>}
      {io.nextOffset != null && typeof fields.request_id === 'string' && <button className="button secondary" disabled={!!pagination.busy || pagination.conflict} onClick={() => { void pagination.moreIO(fields.request_id as string); }}>读取后续版本关联</button>}
    </details>
  </details>;
}
export function RunManifests({ native, current, onOpen, onError }: { native: L1Run; current: number; onOpen?: (target: EvidenceTarget) => void; onError?: (error: unknown) => void }) {
  const pagination = useRunManifests(native, current, onError); const page = pagination.page;
  return <section className="run-manifests" aria-label={`运行 ${native.run_id} 候选清单`}><h5>候选运行清单 <span>当前 {page.items.length} 份 / 总计 {page.total ?? '未知'}</span></h5>
    {page.missing ? <p className="missing-note">此运行的接口未提供清单字段，输入、输出版本是否已记录未知；无法得到完整复现清单。</p> : <>
      <Diagnostic issues={page.issues} />{pagination.error && <div className="manifest-conflict" role="alert">{pagination.error}{pagination.conflict && <p>版本已变化，保留此前列表，不混合新旧分页。请主动刷新重新读取；不会自动续读。</p>}<button className="text-button" disabled={!!pagination.busy} onClick={() => { void pagination.refresh(); }}>刷新运行清单</button></div>}{pagination.busy && <p role="status" className="small muted">正在读取运行清单…</p>}{pagination.revision != null && <p className="small muted">续读固定版本 {pagination.revision} · 已读取 {page.items.length} 份清单</p>}{page.partial && <p className="missing-note">运行清单仅显示部分或完整性未知；原文接口每页最多返回 20 份，不能视为全部报告。</p>}
      {!page.items.length && <p className="missing-note">当前可见运行输入、输出版本未记录为显式清单；不能据此断言没有实际输入输出。</p>}
      {page.items.map((item, index) => <Manifest key={typeof item.request_id === 'string' ? item.request_id : index} item={item} native={native} current={current} onOpen={onOpen} first={index === 0} pagination={pagination} />)}
      {page.nextOffset != null && <button className="button secondary manifest-more" disabled={!!pagination.busy || pagination.conflict} onClick={() => { void pagination.moreManifests(); }}>读取后续运行清单</button>}
    </>}
  </section>;
}
