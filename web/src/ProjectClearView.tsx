import type { Project } from './types';
import type { ClearPreview } from './projectClear';
import type { useProjectClear } from './useProjectClear';
import './projectClear.css';

const tableNames: Record<string, string> = { projects: '项目', raw_events: '原文事件', events: '原文事件', claims: '研究记录', entities: '研究对象', sessions: '会话', spans: '引用片段', reviews: '审核记录', snapshots: '快照', artifact_versions: '文件版本', run_manifests: '候选运行清单', model_attempts: '模型尝试', extraction_queue: '提取队列', source_files: '来源登记', executions: '运行观察' };
function Summary({ value }: { value: ClearPreview }) {
  return <div className="clear-summary"><dl><div><dt>数据库行</dt><dd>{Object.values(value.rows).reduce((a, b) => a + b, 0)}</dd></div><div><dt>删除对象</dt><dd>{value.objects}</dd></div><div><dt>管理路径</dt><dd>{value.managed_paths}</dd></div><div><dt>共享对象保留</dt><dd>{value.shared_objects_retained}</dd></div></dl>
    <p className="small muted">共享对象仍由其他项目引用，因此保留。数量以本次服务端清单为准。</p>
    <details><summary>查看各类记录数量</summary><ul>{Object.entries(value.rows).filter(([, n]) => n > 0).map(([table, n]) => <li key={table}><span>{tableNames[table] ?? table}</span><span>{n}</span></li>)}</ul></details>
    <p className="small">{value.boundary}</p>
  </div>;
}
export function ProjectClearView({ project, projects, onProject, workspace: w }: {
  project: Project | undefined; projects: Project[]; onProject: (id: string) => void; workspace: ReturnType<typeof useProjectClear>;
}) {
  const pending = w.status?.state === 'pending' ? w.status : null;
  const receipt = w.receipt; const targetName = (id: string) => projects.find(p => p.project_id === id)?.name ?? `项目 ${id}`;
  return <section className="project-clear" aria-label="整项目清除">
    <div className="clear-boundary"><h2>清除本机管理的项目资料</h2><p>删除所选项目在当前数据目录中的原文、研究记录、索引、缓存及管理的快照和导出副本。完成后不能在这里撤销。</p><p>外部 Claude / Codex 日志、研究工作区，以及你另存或分享的副本会保留。已发送给远程模型的资料不会因此收回。</p><p>完成后会重新载入页面，释放当前页面此前读取的资料；所有项目未保存的草稿会丢失。其他已打开的浏览器页面或标签需另行关闭，不能据此认定它们的缓存已清除。</p><p className="small muted">清除期间普通资料读取会暂停。关闭页面或断开连接不代表清除停止；请通过清除状态核对和恢复。</p></div>
    <div className="clear-state-actions"><button type="button" className="button secondary" disabled={w.checking || w.busy} onClick={() => { void w.checkStatus(); }}>{w.checking ? '正在检查清除状态…' : '检查清除状态'}</button>{w.status?.state === 'idle' && <span className="small muted">没有等待恢复的清除任务</span>}{w.busy && <p role="status">正在处理清除请求，请等待完成回执。不会同时发起第二次清除。</p>}</div>
    {pending && <section className="clear-pending" aria-label="等待恢复的清除"><h2>清除尚未完成</h2><p><strong>{targetName(pending.project_id)}</strong></p><p className="small">项目编号 <code>{pending.project_id}</code><br />请求编号 <code>{pending.request_id}</code></p><p>为避免读取不完整资料，当前数据目录的普通访问暂时被阻止。恢复会继续同一个项目和请求，不会重新选择项目。</p><Summary value={pending} /><button className="button danger" disabled={w.busy} onClick={() => { void w.resume(); }}>恢复此项目清除</button></section>}
    {receipt && <section className="clear-complete" aria-label="清除完成回执"><h2>项目清除已完成</h2><p>项目编号 <code>{receipt.project_id}</code></p><p className="small">请求编号 <code>{receipt.request_id}</code><br />完成时间 {receipt.finished_at}</p><Summary value={receipt} /><p>项目列表已重新读取。此回执仅记录数量和执行信息，不含原文。</p></section>}
    {!pending && <div className="clear-project-form"><label>清除所属项目<select aria-label="清除所属项目" value={project?.project_id ?? ''} onChange={event => onProject(event.target.value)}>{!project && <option value="">尚无可选项目</option>}{projects.map(item => <option key={item.project_id} value={item.project_id}>{item.name}</option>)}</select></label>
      <p className="small">当前选择：<strong>{project?.name ?? '项目列表尚未读取；仍可检查清除状态'}</strong></p>
      <button className="button secondary" disabled={!project || w.busy || w.loading} onClick={() => { void w.readPreview(); }}>{w.loading ? '正在读取影响清单…' : '预览清除影响'}</button>
      {w.preview && <section className="clear-preview" aria-label="清除影响预览"><h2>{project?.name} · 清除影响</h2><p className="small muted">记录版本 {w.preview.revision} · 该清单适用于整个项目，不受当前图的阅读条件或分页影响。</p><Summary value={w.preview} />
        {w.preview.blockers.length > 0 ? <div className="error-message" role="alert"><strong>存在归属阻碍，不能执行清除</strong><ul>{w.preview.blockers.map((item, i) => <li key={i}>{item}</li>)}</ul><p>处理阻碍后请重新预览；本次不会跳过记录或只删除一部分。</p></div> : <form onSubmit={event => { event.preventDefault(); void w.execute(); }}><label>输入完整项目名称以确认清除<input aria-label="确认清除项目名称" value={w.confirmation} onChange={event => w.setConfirmation(event.target.value)} autoComplete="off" disabled={w.busy} /></label><p className="small">请输入 <strong>{project?.name}</strong>。执行会永久删除上方清单中的本机管理资料。</p><button className="button danger" type="submit" disabled={w.busy || w.conflict || w.confirmation !== project?.name}>确认并清除整个项目</button></form>}
      </section>}
    </div>}
    {w.error && <div className="error-message clear-error" role="alert">{w.error}{w.conflict && <p>数据或管理副本可能已变化，也可能有进程正在占用。请检查清除状态；没有挂起任务时重新预览并确认，不会自动重发。</p>}</div>}
  </section>;
}
