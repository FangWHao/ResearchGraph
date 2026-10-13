import type { Project } from './types';
import type { useProjectPrivacy } from './useProjectPrivacy';
import './privacy.css';

export function ProjectPrivacyPanel({ project, projects, onProject, workspace: w }: {
  project: Project | undefined; projects: Project[]; onProject: (id: string) => void; workspace: ReturnType<typeof useProjectPrivacy>;
}) {
  return <section className="project-privacy" aria-label="项目编号遮盖配置">
    <details><summary>项目编号遮盖规则 <span className="small muted">{w.saved ? `已保存 ${w.saved.patterns.length} 条` : '尚未读取'}</span></summary>
      <p className="small">为住院号、病案号或样本编号添加规则。保存后用于本项目的模型计数、生成和历史导出；内置个人信息与密钥遮盖继续生效。本地原文不改写。</p>
      <label className="privacy-project">规则所属项目<select aria-label="遮盖规则所属项目" value={project?.project_id ?? ''} onChange={event => onProject(event.target.value)}>{projects.map(item => <option key={item.project_id} value={item.project_id}>{item.name}</option>)}</select></label>
      {w.saved && <div className="privacy-saved"><strong className="small">当前已保存的项目规则</strong>{w.saved.patterns.length ? <ul>{w.saved.patterns.map((pattern, index) => <li key={index}><code>{pattern}</code></li>)}</ul> : <p className="small muted">尚无额外项目规则，仍使用内置遮盖。</p>}<p className="small muted">规则版本 {w.saved.rule_id} · 记录版本 {w.saved.revision}</p></div>}
      <form onSubmit={event => { event.preventDefault(); void w.save(); }}>
        <label>每行一个正则表达式<textarea aria-label="项目遮盖正则规则" value={w.draft} onChange={event => w.change(event.target.value)} rows={4} placeholder={'HOSP-\\d+\nCASE-\\d+\nSAMPLE-[A-Z0-9]+'} disabled={w.loading && !w.saved} /></label>
        <p className="small muted">最多 32 条，每条最多 1000 字符，由服务端校验正则语法。清空并保存会移除额外项目规则；保存前的草稿不生效。历史导出中的额外规则仅用于那次下载。</p>
        {w.error && <div className="error-message" role="alert">{w.error}{w.conflict && <p>版本已变化或项目正在发送。读取最新规则后请核对草稿，再主动保存；不会自动重发。</p>}</div>}
        {w.info && <p className="notice small" role="status">{w.info}</p>}
        <footer><span className="small muted">{w.loading ? '正在读取项目规则…' : !w.saved ? '已保存规则尚未读取' : w.dirty ? '草稿尚未保存' : '当前草稿与保存规则一致'}</span><div><button className="button secondary" type="button" disabled={w.busy || w.loading} onClick={w.refresh}>读取最新规则</button><button className="button primary" type="submit" disabled={w.busy || w.loading || !w.saved || w.conflict}>{w.busy ? '正在保存项目规则…' : '保存项目规则'}</button></div></footer>
      </form>
      <p className="small muted">保存规则不会关闭已有外发许可，也不能收回已发送的资料。涉及临床资料时，建议使用本地模型。</p>
    </details>
  </section>;
}
