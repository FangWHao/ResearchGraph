import type { Project } from './types';
import type { useHistoryExport } from './useHistoryExport';
import './historyExport.css';

export function HistoryExportView({ project, projects, onProject, workspace }: { project: Project | undefined; projects: Project[]; onProject: (id: string) => void; workspace: ReturnType<typeof useHistoryExport> }) {
  const { draft, change } = workspace;
  return <section className="history-export" aria-label="历史导出阅读条件">
    <div className="export-intro"><h2>下载可查证的历史阅读包</h2><p>导出项目：<strong>{project?.name ?? '项目未选择'}</strong></p>
      <label className="export-project-picker">切换导出项目<select aria-label="切换导出项目" value={project?.project_id ?? ''} onChange={event => onProject(event.target.value)}>{projects.map(item => <option key={item.project_id} value={item.project_id}>{item.name}</option>)}</select></label>
      <p>包内保留记录、图关系、来源引用、候选与审核历史，以及独立的采用、证据和运行状态。阅读条件和已知缺口随包保存；按下方条件读取数据库中的历史，网页当前显示的部分记录不限制导出。</p>
      <p className="missing-note">包不包含完整会话和二进制文件，也不保证完整环境或实际 I/O。候选仍是候选，下载不会确认研究结论。</p></div>
    <form className="export-form" onSubmit={event => { event.preventDefault(); void workspace.download(); }}>
      <h3>阅读条件</h3><p className="muted small">双时间分别控制事情何时发生、资料何时进入库。留空按导出时刻读取；填写时间必须明确时区。</p>
      <div className="export-time-grid"><label>发生截止<input aria-label="导出发生截止" value={draft.occurredUntil} onChange={event => change({ ...draft, occurredUntil: event.target.value })} placeholder="2026-10-10T12:00:00+08:00" /></label>
        <label>获知截止<input aria-label="导出获知截止" value={draft.knownUntil} onChange={event => change({ ...draft, knownUntil: event.target.value })} placeholder="2026-10-10T12:00:00+08:00" /></label></div>
      <label>阅读范围<select aria-label="导出阅读范围" value={draft.scopeMode} onChange={event => change({ ...draft, scopeMode: event.target.value as typeof draft.scopeMode })}>
        <option value="all">所有范围</option><option value="exact">完整相等的已知范围</option><option value="unknown">仅范围未知</option></select></label>
      {draft.scopeMode === 'exact' ? <label>完整范围<textarea aria-label="导出完整范围" rows={3} className="mono" value={draft.scopeText} onChange={event => change({ ...draft, scopeText: event.target.value })} placeholder={'dataset_version=数据版本\nanalysis_step=分析步骤'} /><span className="muted small">每行一个 字段=值，所有字段和值完全相等才纳入；不会按关键词猜范围。</span></label>
        : <p className="muted small">{draft.scopeMode === 'all' ? '包含各个范围及范围未知的可见记录，独立保存它们的状态。' : '只选择明确缺少范围的记录，范围未知仍然保留。'}</p>}
      <div className="export-privacy"><h3>分享前的隐私选择</h3><label className="export-checkbox"><input type="checkbox" aria-label="加入遮盖后的证据片段" checked={draft.includeEvidence} onChange={event => change({ ...draft, includeEvidence: event.target.checked })} /><span>加入遮盖后的证据片段<small>默认只含引用位置。选中后仅加入被引用片段，经等长 UTF-8 字节遮盖后保存；原证据库不改动。</small></span></label>
        <details><summary>额外遮盖规则</summary><label>本次导出的自定义正则<textarea aria-label="导出自定义遮盖规则" value={draft.patternsText} onChange={event => change({ ...draft, patternsText: event.target.value })} rows={3} className="mono" placeholder={'ZY\\d+\nSAMPLE-\\d+'} /><span className="muted small">每行一条，最多 32 条、每条最多 1000 字符，由服务端校验。适用于导出文字中的住院号、病案号或样本编号，仅用于本次下载。</span></label></details>
        <p className="muted small">常见个人编号、手机号、邮箱和密钥格式会遮盖。分享前仍需核对本项目的编号规则和阅读条件。</p></div>
      {workspace.failure && <p className="error-message" role="alert">{workspace.failure}</p>}
      {workspace.needsRefresh && <div className="export-refresh"><p className="missing-note">资料已变化，已保留阅读条件。请先读取最新版本，再主动点击下载。</p><button type="button" className="button secondary" onClick={workspace.refresh}>读取最新版本</button></div>}
      {workspace.status && <p className="export-status" role="status">{workspace.status}</p>}
      <footer><span className="muted small">本机生成 · 不调用模型 · 读取版本 {workspace.revision ?? '加载中'}</span><div>
        {workspace.busy && <button type="button" className="button secondary" onClick={workspace.cancel}>取消等待</button>}
        <button className="button primary" type="submit" disabled={workspace.busy || workspace.needsRefresh || workspace.revision == null || !project}>{workspace.busy ? '正在生成…' : '下载历史 ZIP'}</button></div></footer>
    </form>
  </section>;
}
