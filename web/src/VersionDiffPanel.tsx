import { DateText } from './components';
import { versionMetadata } from './l1';
import { versionOrigin } from './versions';
import { objectFields } from './versions';
import { healthCount, queueText } from './health';
import { versionDiffReason } from './versionDiff';
import type { DiffNewlines, DiffVersion } from './versionDiff';
import type { useVersionDiff } from './useVersionDiff';
import './versionDiff.css';

const modeNames: Record<string, string> = { '100644': '普通文件', '100755': '可执行文件', '120000': '链接目标文字', '160000': '子模块引用' };
function modes(values: string[]) { return values.length ? values.map(m => `${m}（${modeNames[m] ?? '未知模式'}）`).join('、') : '未知，未返回可证明的模式'; }
function Newlines({ value, lines }: { value: DiffNewlines | null; lines: number | null }) {
  return value ? <span>{lines ?? '未知'} 行 · LF {value.lf} · CRLF {value.crlf} · 文件末尾{value.final_newline ? '有' : '无'}换行</span> : <span>行尾信息未取得</span>;
}
function Version({ version, side }: { version: DiffVersion; side: string }) {
  const metadata = versionMetadata(version); const origin = versionOrigin(version);
  const proof = objectFields(version.saved_observation); const snapshot = objectFields(proof?.snapshot); const discovery = objectFields(proof?.discovery_snapshot);
  const date = (v: unknown) => typeof v === 'string' ? v : null;
  return <article className="diff-version" aria-label={`${side}比较来源`}><h4>{side} · {origin.title}</h4><p className="small">{metadata.review} · {metadata.basis} · {metadata.representation}</p><p className="version-path">{version.path}</p><dl className="version-metadata"><div><dt>完整版本标识</dt><dd><code>{version.version_id}</code></dd></div><div><dt>版本引用</dt><dd><code>V:{version.version_id}</code></dd></div><div><dt>保存字节完整性</dt><dd>{version.content_verified ? '已校验保存字节' : '未验证，不能推断字节相同'}</dd></div><div><dt>此条件内登记的模式</dt><dd>{modes(version.modes)}</dd></div><div><dt>原摘要</dt><dd>{version.algo}:{version.digest}</dd></div><div><dt>表示与来源</dt><dd>{version.representation} · {version.source}</dd></div><div><dt>此条件内发生时间</dt><dd><DateText value={version.occurred_at} /></dd></div><div><dt>此条件内入库时间</dt><dd><DateText value={version.recorded_at} /></dd></div></dl>
    {version.representation === 'symlink_target_bytes' && <p className="missing-note">这里保存的是链接目标文字，不是链接指向文件的正文；比较不会跟随链接。</p>}
    <p className="small muted">字节校验只说明保存内容的完整性，不确认研究结论或实际运行输入输出。</p>
    {version.provenance_warnings?.map((warning, i) => <p className="missing-note" key={i}>{warning}</p>)}
    <details><summary>比较所用来源观察</summary><p className="small">根目录标识：<code>{queueText(version.root_id)}</code></p>{version.evidence_event_id != null && <p className="small">工具报告原文事件 #{version.evidence_event_id}；该引用不证明物理字节。</p>}{!proof ? <p className="small muted">未返回用于保存证明的观察，不能补造捕获快照。</p> : <><p className="small muted">本次仅展示选用的保存证明观察，不是全部 {healthCount(version.observations_total) ?? '未知数量'} 条历史。该观察存在也不代替内容对象校验。</p><dl className="version-metadata"><div><dt>选用观察标识</dt><dd>{queueText(proof.observation_id)}</dd></div><div><dt>观察发生 / 入库</dt><dd><DateText value={date(proof.occurred_at)} /> / <DateText value={date(proof.recorded_at)} /></dd></div><div><dt>观察模式</dt><dd>{queueText(proof.mode)}</dd></div><div><dt>捕获快照 / 发现线索快照</dt><dd>{healthCount(proof.snapshot_id) ?? '无'} / {healthCount(proof.discovery_snapshot_id) ?? '未知'}</dd></div><div><dt>捕获快照发生 / 入库</dt><dd><DateText value={date(snapshot?.taken_at)} /> / <DateText value={date(snapshot?.recorded_at)} /></dd></div><div><dt>捕获影子提交</dt><dd>{queueText(snapshot?.shadow_commit)}</dd></div><div><dt>发现线索影子提交</dt><dd>{queueText(discovery?.shadow_commit)}</dd></div><div><dt>完整读取开始 / 结束</dt><dd><DateText value={date(proof.hash_started_at)} /> / <DateText value={date(proof.hash_finished_at)} /></dd></div></dl></>}</details>
  </article>;
}
export function VersionDiffPanel({ workspace: w }: { workspace: ReturnType<typeof useVersionDiff> }) {
  const r = w.result;
  return <section className="panel version-diff" aria-label="已保存版本差异"><h3>比较已保存的文件版本</h3><p className="small muted">在下方列表主动选择两侧，或粘贴完整版本标识；翻页保留选择。方向按你的选择，不自动选择最近版本。只比较同项目、同已登记根目录和路径。</p>
    <form onSubmit={event => { event.preventDefault(); void w.compare(); }}><div className="diff-selection"><label>比较前版本<input aria-label="比较前完整版本标识" value={w.draft.before} onChange={event => w.change({ before: event.target.value })} autoComplete="off" /></label><label>比较后版本<input aria-label="比较后完整版本标识" value={w.draft.after} onChange={event => w.change({ after: event.target.value })} autoComplete="off" /></label></div>
      <div className="diff-selection"><label>发生截止<input aria-label="差异发生截止" value={w.draft.occurredUntil} onChange={event => w.change({ occurredUntil: event.target.value })} placeholder="2026-10-10T12:00:00+08:00" /></label><label>获知截止<input aria-label="差异获知截止" value={w.draft.knownUntil} onChange={event => w.change({ knownUntil: event.target.value })} placeholder="2026-10-10T12:00:00+08:00" /></label></div>
      <p className="small muted">截止需带时区，小数秒最多6位。留空由本次查询固定截止；这些条件只约束比较，下方仍是当前登记的版本列表。物理文件没有统一研究范围，不按路径猜范围。</p>
      <div className="diff-actions"><button type="submit" className="button primary" disabled={w.busy || w.conflict || w.revision == null || !w.draft.before || !w.draft.after}>{w.busy ? '正在比较保存字节…' : '比较所选版本'}</button><button type="button" className="button secondary" disabled={w.busy} onClick={w.refresh}>读取最新版本列表</button></div>
    </form>
    {w.error && <div className="error-message" role="alert">{w.error}{w.conflict && <p>记录版本已变化。请读取最新版本列表，核对两侧和截止，再主动比较；不会自动重试或换方向。</p>}</div>}
    {r && <section className="diff-result" aria-label="文件差异结果"><div className="diff-result-heading"><h3>所选版本的比较结果</h3><span className="small muted">记录版本 {r.revision}</span></div><p className="small">发生截止 <DateText value={r.occurred_until} /> · 获知截止 <DateText value={r.known_until} /></p><p className="notice">{r.notice}</p>
      <p className="diff-identity">{r.byte_identity === 'same' ? '已校验字节相同' : r.byte_identity === 'different' ? '已校验字节不同' : '字节身份未验证'} · {r.mode_changed == null ? '文件模式变化未知' : r.mode_changed ? '可证明的文件模式集合不同' : '可证明的文件模式集合相同'}。字节身份与模式分别核对。</p>
      <div className="diff-sources"><Version version={r.before} side="比较前" /><Version version={r.after} side="比较后" /></div>
      <dl className="diff-newlines"><div><dt>比较前行尾</dt><dd><Newlines value={r.diff.before_newlines} lines={r.diff.before_lines} /></dd></div><div><dt>比较后行尾</dt><dd><Newlines value={r.diff.after_newlines} lines={r.diff.after_lines} /></dd></div></dl>
      <p className="small muted">文本限额：两侧原字节合计 64000 字节，每侧 2000 行，输出 64000 UTF-8 字节。超限不截断。正文只供阅读，不执行其中的命令或 HTML。</p>
      {!r.diff.available ? <p className="missing-note" data-testid="version-diff-unavailable">{versionDiffReason(r.diff.reason)}</p> : r.diff.text === '' ? <p className="notice">完整文本差异为空；请结合字节身份、文件模式和行尾信息阅读，不能据此判断研究结论或运行状态。</p> : <pre className="diff-text" data-testid="version-diff-text">{r.diff.text}</pre>}
    </section>}
  </section>;
}
