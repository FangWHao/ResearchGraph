import { useEffect, useRef, useState } from 'react';
import { api, ApiError, query } from './api';
import { DateText, Empty, Loading } from './components';
import { healthCount, queueText } from './health';
import { versionMetadata } from './l1';
import { exactPathError, metadataBoolean, objectFields, observationConflict, observationListIssue, observationSignature, parseVersionsPage, versionOrigin, versionsNextOffset } from './versions';
import type { FileVersionRecord, VersionsPage } from './types';
import './versions.css';

function Observations({ version }: { version: FileVersionRecord }) {
  const observations = Array.isArray(version.observations) ? version.observations : null;
  const count = healthCount(version.observations_total);
  const issue = observationListIssue(version);
  return <details className="version-observations"><summary>最近观察 · {count == null ? '总数未知' : `${count} 条记录`}</summary>
    {issue && <p className="missing-note">{issue}</p>}
    {version.observations_partial === true && <p className="missing-note">观察清单不完整，接口仅返回最近 {observations?.length ?? '未知'} 条，不能视为全部历史。</p>}
    {version.observations_partial == null && <p className="missing-note">接口未提供观察完整性标记，是否还有更早观察未知。</p>}
    {!observations ? <p className="missing-note">观察明细缺失，无法核对完整读取与保存状态。</p> : observations.length === 0 ? <p className="missing-note">当前返回的观察列表为空；不补造读取窗口或快照关联。</p> : <ol>{observations.slice(0, 3).map((value, index) => {
      const item = objectFields(value) ?? {};
      const details = objectFields(item.details);
      const conflict = observationConflict(version, item);
      const signature = observationSignature(item.signature);
      return <li key={typeof item.observation_id === 'string' ? item.observation_id : `missing-${index}`} data-observation-id={typeof item.observation_id === 'string' ? item.observation_id : undefined}>
        <div className="version-card-heading"><strong>{item.cache_reused === true ? '复用完整哈希缓存' : item.cache_reused === false ? version.source === 'shadow_snapshot' ? '快照保存记录' : '完整读取记录' : '读取方式未知'}</strong><span>观察 #{index + 1}</span></div>
        {conflict && <p className="missing-note">{conflict}</p>}
        {item.cache_reused === true && <p className="version-explanation">缓存复用沿用原实际完整读取窗口，不是本次入库时间的一次新读取。</p>}
        <dl className="version-metadata">
          <div><dt>实际完整读取开始</dt><dd><DateText value={typeof item.hash_started_at === 'string' ? item.hash_started_at : null} /></dd></div>
          <div><dt>实际完整读取结束</dt><dd><DateText value={typeof item.hash_finished_at === 'string' ? item.hash_finished_at : null} /></dd></div>
          <div><dt>本观察入库时间</dt><dd><DateText value={typeof item.recorded_at === 'string' ? item.recorded_at : null} /></dd></div>
          <div><dt>正文已复制保存</dt><dd>{metadataBoolean(details?.content_copied)}</dd></div>
          <div><dt>完整身份记录</dt><dd>{metadataBoolean(details?.complete)}</dd></div>
          <div><dt>发现线索匹配</dt><dd>{metadataBoolean(details?.matches_discovery_hint)}</dd></div>
          <div><dt>实际快照关联</dt><dd>{item.snapshot_id === null ? '无快照关联' : healthCount(item.snapshot_id) ?? '未知'}</dd></div>
          <div><dt>发现线索快照</dt><dd>{healthCount(item.discovery_snapshot_id) ?? '未知'}</dd></div>
          <div><dt>后台任务 ID</dt><dd>{healthCount(item.job_id) ?? '未知'}</dd></div>
          <div><dt>文件模式</dt><dd>{queueText(item.mode)}</dd></div>
        </dl>
        <details className="version-diagnostics"><summary>身份与观察诊断 · 仅元数据</summary><dl className="version-metadata">
          <div><dt>观察标识</dt><dd>{queueText(item.observation_id)}</dd></div><div><dt>观察版本标识</dt><dd>{queueText(item.version_id)}</dd></div>
          <div><dt>缓存来源观察</dt><dd>{item.cached_from === null ? '未复用其他观察' : queueText(item.cached_from)}</dd></div>
          <div><dt>影子提交</dt><dd>{queueText(details?.shadow_commit)}</dd></div><div><dt>文件签名（原记录）</dt><dd>{signature}</dd></div>
        </dl></details>
      </li>;
    })}</ol>}
  </details>;
}

export function VersionsView({ project, epoch, onError }: { project: string; epoch: number; onError: (error: unknown) => void }) {
  const [selection, setSelection] = useState({ project, draft: '', path: '', offset: 0 });
  const [retry, setRetry] = useState(0);
  const [inputError, setInputError] = useState<{ project: string; message: string } | null>(null);
  const [response, setResponse] = useState<{ key: string; data: VersionsPage } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const limit = 25;
  const { draft, path, offset } = selection.project === project ? selection : { draft: '', path: '', offset: 0 };
  const key = JSON.stringify([project, path, offset, epoch, retry]);
  const current = useRef(key); current.current = key;
  const data = response?.key === key ? response.data : null;
  const error = failure?.key === key ? failure.message : '';
  const validation = inputError?.project === project ? inputError.message : '';
  const next = versionsNextOffset(data, offset);
  useEffect(() => { setSelection({ project, draft: '', path: '', offset: 0 }); setInputError(null); }, [project]);
  useEffect(() => {
    const controller = new AbortController();
    api<unknown>(`/versions?${query({ project, path: path || undefined, offset, limit })}`, undefined, controller.signal)
      .then(result => {
        if (controller.signal.aborted || current.current !== key) return;
        const parsed = parseVersionsPage(result, project, offset, limit);
        setResponse({ key, data: parsed });
      }).catch((cause: unknown) => {
        if (controller.signal.aborted || current.current !== key) return;
        setFailure({ key, message: cause instanceof Error ? cause.message : '文件版本读取失败' });
        if (cause instanceof ApiError && cause.status === 401) onError(cause);
      });
    return () => controller.abort();
  }, [project, path, offset, key, onError]);
  function filter(value: string) {
    const invalid = exactPathError(value);
    setInputError(invalid ? { project, message: invalid } : null);
    if (invalid) return;
    setSelection({ project, draft: value, path: value, offset: 0 }); setRetry(value => value + 1);
  }
  return <div className="versions-view">
    <section className="panel versions-intro" aria-label="文件版本范围与依据">
      <p>按当前项目查看已记录的文件身份与观察，不读取或展示文件正文。版本记录不能证明运行实际输入输出，完整复现清单需另有运行关联。</p>
      <form onSubmit={event => { event.preventDefault(); filter(draft); }} className="version-filter">
        <label htmlFor="version-path">精确绝对路径<span>留空查看全部；空格、大小写和特殊符号按原样匹配。</span></label>
        <input id="version-path" value={draft} onChange={event => { setSelection({ project, draft: event.target.value, path, offset }); setInputError(null); }} placeholder="/项目/文件路径" />
        <div className="version-filter-actions"><button className="button primary" type="submit">筛选版本</button><button className="button secondary" type="button" onClick={() => filter('')}>查看全部版本</button></div>
      </form>
      {validation && <p className="error-message" role="alert">{validation}</p>}
      {path && <p className="version-active-filter">当前精确筛选：<span>{path}</span></p>}
    </section>
    {error ? <section className="panel versions-retry" role="alert"><Empty title="文件版本暂不可用">{error} 此时无法判断版本是否存在。</Empty><button className="button secondary" onClick={() => setRetry(value => value + 1)}>重新读取文件版本</button></section> : !data ? <Loading /> : <>
      <section className="panel versions-list-panel" aria-labelledby="versions-list-heading">
        <h3 id="versions-list-heading">文件版本记录 <span className="eyebrow">当前项目</span></h3>
        <p className="version-page-count">{healthCount(data.total) == null ? '总数未知' : `此筛选共 ${data.total} 条`} · 记录版本 {healthCount(data.revision) ?? '未知'}</p>
        {data.partial === true && <p className="missing-note">当前只显示部分版本；其他页与更早观察仍需分别查看。</p>}
        {data.partial == null && <p className="missing-note">接口未提供版本完整性标记，无法判断是否还有未显示的版本。</p>}
        {!data.versions ? <p className="missing-note">文件版本列表缺失，不能视为没有版本。</p> : data.versions.length === 0 ? <Empty title="当前页没有文件版本">{path ? '没有符合此精确路径的当前页记录；这不证明文件从未存在。' : '当前项目没有返回此页记录；这不证明项目从未使用过文件。'}</Empty> : <ol className="file-version-list">{data.versions.map((version, index) => {
          const origin = versionOrigin(version); const metadata = versionMetadata(version);
          return <li className={`file-version-card ${origin.kind}`} key={version.version_id ?? `missing-${index}`} data-file-version-id={version.version_id}>
            <div className="version-card-heading"><strong>{origin.title}</strong><span>{metadata.review}</span></div>
            <p className="version-path">{queueText(version.path)}</p>
            <p className="version-explanation">{origin.note}</p>
            <dl className="version-metadata">
              <div><dt>原算法与摘要</dt><dd className="version-digest">{queueText(version.algo)}:{queueText(version.digest)}</dd></div>
              <div><dt>记录大小</dt><dd>{healthCount(version.size) == null ? '未知' : `${version.size!.toLocaleString()} 字节`}</dd></div>
              <div><dt>版本记录的观察时间</dt><dd><DateText value={version.observed_at} /></dd></div>
              <div><dt>记录依据与阶段</dt><dd>{metadata.basis} · {metadata.phase}</dd></div>
              <div><dt>版本表示</dt><dd>{metadata.representation}</dd></div><div><dt>原来源标识</dt><dd>{queueText(version.source)}</dd></div>
            </dl>
            {typeof version.path === 'string' && !exactPathError(version.path) && version.path !== path && <button className="text-button" onClick={() => filter(version.path!)}>只看此路径版本</button>}
            <Observations version={version} />
            <details className="version-diagnostics"><summary>版本身份 · 仅元数据</summary><dl className="version-metadata">
              <div><dt>版本标识</dt><dd>{queueText(version.version_id)}</dd></div><div><dt>项目 ID</dt><dd>{queueText(version.project_id)}</dd></div>
              <div><dt>根目录 ID</dt><dd>{queueText(version.root_id)}</dd></div><div><dt>内容对象标识（记录值）</dt><dd>{version.content_sha256 === null ? '无内容副本记录' : queueText(version.content_sha256)}</dd></div>
              <div><dt>原文事件编号（记录值）</dt><dd>{healthCount(version.evidence_event_id) ?? '未知'}</dd></div>
            </dl></details>
          </li>;
        })}</ol>}
        <div className="pagination"><button className="button secondary" disabled={offset === 0} onClick={() => setSelection({ project, draft, path, offset: Math.max(0, offset - limit) })}>上一页文件版本</button><span>偏移 {offset} · 每页 {limit}</span><button className="button secondary" disabled={next == null} onClick={() => { if (next != null) setSelection({ project, draft, path, offset: next }); }}>下一页文件版本</button></div>
        {(data.limit == null || data.offset == null || data.total == null) && <p className="missing-note">分页数量或位置未知，无法推算下一页。</p>}
      </section>
    </>}
  </div>;
}
