import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, query, readToken, setToken } from './api';
import { Badge, ClaimBody, Empty, EvidenceLink, EvidencePanel, Icon, Loading } from './components';
import { actionNames, evidenceNames } from './model';
import { canResolveDecision, requiresDecisionTarget } from './manualDecision';
import { useManualDecisions } from './useManualDecisions';
import { ManualQuestionDialog } from './ManualQuestionDialog';
import { allowsUnknownQuestionScope, emptyQuestionDraft, isCurrentQuestionIntent, parseScopeText } from './manualQuestion';
import type { QuestionDraft } from './manualQuestion';
import type { Claim, GraphData, Project, QuestionResult, Span } from './types';
import { HealthView, Questions, ReviewQueue, SearchView, TimelineView } from './Views';
import { QaView } from './QaView';
import { useQaWorkspace } from './useQaWorkspace';

const GraphView = lazy(() => import('./GraphView').then(module => ({ default: module.GraphView })));

type View = 'questions' | 'review' | 'timeline' | 'graph' | 'health' | 'search' | 'qa';
type EvidenceTarget = { event_id: number; byte_start?: number; byte_end?: number; quote_sha256?: string };
const viewMeta: Record<View, { title: string; subtitle: string; icon: string }> = {
  questions: { title: '研究问题', subtitle: '从问题出发，找到每一步决定的依据。', icon: 'questions' },
  qa: { title: '研究问答', subtitle: '从本地来源找答案，保留引用、历史时间和不确定事项。', icon: 'evidence' },
  review: { title: '复核队列', subtitle: '对照原文确认候选，记录由你判断。', icon: 'review' },
  timeline: { title: '决定时间线', subtitle: '提出、采用与撤回，都留下可查证的原话。', icon: 'history' },
  graph: { title: '研究图', subtitle: '查看问题、方案、尝试和发现之间的记录关系。', icon: 'graph' },
  health: { title: '采集健康', subtitle: '查看来源、覆盖缺口和模型用量。', icon: 'health' },
  search: { title: '原文检索', subtitle: '按字面量查找会话，并回到证据位置。', icon: 'search' },
};

function EditDialog({ claim, revision, actor, onClose, onWrite, onError }: {
  claim: Claim; revision: number; actor: string; onClose: () => void;
  onWrite: (revision: number, id?: number) => void; onError: (error: unknown) => void;
}) {
  const [payload, setPayload] = useState({ ...claim.payload });
  const [scope, setScope] = useState(Object.entries(claim.scope ?? {}).map(([key, value]) => `${key}=${value}`).join('\n'));
  const [busy, setBusy] = useState(false); const [failure, setFailure] = useState('');
  const allowUnknownScope = allowsUnknownQuestionScope(claim);
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setFailure('');
    try {
      const parsed = parseScopeText(scope, allowUnknownScope);
      const result = await api<{ revision: number; claim_id: number }>(`/claims/${claim.claim_id}/edit`, { payload, scope: parsed, actor, expected_revision: revision });
      onWrite(result.revision, result.claim_id); onClose();
    } catch (error) { setFailure((error as Error).message); onError(error); } finally { setBusy(false); }
  }
  return <div className="modal-backdrop" onClick={onClose}><section className="edit-modal" role="dialog" aria-modal="true" aria-labelledby="edit-title" onClick={event => event.stopPropagation()}><header><div><span className="eyebrow">人工修改 · 记录 #{claim.claim_id}</span><h2 id="edit-title">保留原文，追加你的判断</h2></div><button className="icon-button" aria-label="关闭修改" onClick={onClose}><Icon name="close" /></button></header><form onSubmit={event => { void submit(event); }}>
    {payload.label != null && <label>标题<input autoFocus value={payload.label} onChange={event => setPayload({ ...payload, label: event.target.value })} required maxLength={500} /></label>}
    {payload.content != null && <label>内容<textarea aria-label="内容" value={payload.content} onChange={event => setPayload({ ...payload, content: event.target.value })} rows={4} required /></label>}
    {payload.reason != null && <label>理由<textarea aria-label="理由" autoFocus={payload.label == null} value={payload.reason} onChange={event => setPayload({ ...payload, reason: event.target.value })} rows={4} required /></label>}
    {payload.action != null && <label>决定动作<select aria-label="决定动作" value={payload.action} onChange={event => setPayload({ ...payload, action: event.target.value })}>{Object.entries(actionNames).map(([key, name]) => <option value={key} key={key}>{name}</option>)}</select></label>}
    {payload.state != null && <label>证据状态<select value={payload.state} onChange={event => setPayload({ ...payload, state: event.target.value })}>{Object.entries(evidenceNames).map(([key, name]) => <option value={key} key={key}>{name}</option>)}</select></label>}
    <label>{allowUnknownScope ? '研究范围（可留空）' : '完整范围'}<span className="muted small">{allowUnknownScope ? '已知范围需完整填写，每行一个 字段=值；留空保存为范围未知。' : '每行一个 字段=值，未知值明确填写 unknown。'}</span><textarea aria-label={allowUnknownScope ? '研究范围（可留空）' : '完整范围'} value={scope} onChange={event => setScope(event.target.value)} rows={3} required={!allowUnknownScope} className="mono" /></label>
    <p className="notice">原记录与引用不改写；新记录标为人工确认，并关联到旧记录。对象和关系的指向保留。</p>{failure && <p className="error-message" role="alert">{failure}</p>}<footer><span className="muted small">复核者 {actor} · 版本 {revision}</span><button type="submit" className="button primary" disabled={busy}>{busy ? '正在保存…' : '修改并确认'}</button></footer>
  </form></section></div>;
}

function ClaimDrawer({ id, epoch, actor, onClose, onEvidence, onError, onWrite, onEdit, onClaim, onResolve }: {
  id: number; epoch: number; actor: string; onClose: () => void; onEvidence: (span: Span) => void;
  onError: (error: unknown) => void; onWrite: (revision: number, id?: number) => void;
  onEdit: (claim: Claim, revision: number) => void; onClaim: (id: number) => void; onResolve: (id: number) => void;
}) {
  const [data, setData] = useState<{ revision: number; claim: Claim } | null>(null); const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); setData(null);
    api<{ revision: number; claim: Claim }>(`/claims/${id}`, undefined, controller.signal).then(result => { if (!controller.signal.aborted) setData(result); }).catch(error => { if (!controller.signal.aborted) onError(error); });
    return () => controller.abort();
  }, [id, epoch, onError]);
  async function review(action: 'confirm' | 'dismiss') {
    if (!data) return;
    if (action === 'confirm' && requiresDecisionTarget(data.claim)) { onError(new Error('此决定尚未确定对象，请先确定对象。')); return; }
    setBusy(true);
    try { const result = await api<{ revision: number }>('/review', { claim_ids: [id], action, actor, expected_revision: data.revision }); onWrite(result.revision); }
    catch (error) { onError(error); } finally { setBusy(false); }
  }
  return <aside className="detail-drawer" role="dialog" aria-modal="false" aria-label={`记录 ${id} 详情`}><header><span className="eyebrow">记录 #{id}</span><button className="icon-button" aria-label="关闭详情" onClick={onClose}><Icon name="close" /></button></header>{!data ? <Loading /> : <><div className="drawer-content"><Badge state={data.claim.effective_state} /><ClaimBody claim={data.claim} onClaim={onClaim} /><section className="drawer-evidence"><h4>原文引用 <span>{data.claim.evidence.length}</span></h4>{data.claim.evidence.map(span => <EvidenceLink key={span.span_id} span={span} onOpen={onEvidence} />)}{!data.claim.evidence.length && <p className="missing-note">原文引用缺失。</p>}</section>{data.claim.review_history && data.claim.review_history.length > 0 && <section><h4>审核历史</h4>{data.claim.review_history.map(item => <div className="review-history" key={item.action_id}><strong>{item.action === 'confirm' ? '确认' : item.action === 'dismiss' ? '驳回' : '修改'}</strong><span>{item.actor}</span>{item.actor.startsWith('rule:') && <p>独立原话规则确认；审计保存原文位置和对象。</p>}{item.new_claim_id && <button className="text-button" onClick={() => onClaim(item.new_claim_id!)}>打开修改版 #{item.new_claim_id}</button>}</div>)}</section>}</div><footer className="drawer-actions">{canResolveDecision(data.claim) && <button className="button primary" onClick={() => onResolve(id)}>确定对象</button>}<button className="button primary" disabled={busy || data.claim.replacement_ids.length > 0 || requiresDecisionTarget(data.claim)} onClick={() => { void review('confirm'); }}>确认</button><button className="button secondary" disabled={busy || data.claim.replacement_ids.length > 0} onClick={() => { void review('dismiss'); }}>驳回</button><button className="text-button" disabled={busy || data.claim.replacement_ids.length > 0 || requiresDecisionTarget(data.claim)} onClick={() => onEdit(data.claim, data.revision)}>修改</button></footer></>}</aside>;
}

export function App() {
  const [authorized, setAuthorized] = useState(() => !!readToken());
  const [tokenInput, setTokenInput] = useState(''); const [epoch, setEpoch] = useState(0);
  const [projects, setProjects] = useState<Project[]>([]); const [projectId, setProjectId] = useState('');
  const [graph, setGraph] = useState<GraphData | null>(null); const [view, setView] = useState<View>(() => {
    const requested = new URLSearchParams(window.location.search).get('view');
    return requested && Object.hasOwn(viewMeta, requested) ? requested as View : 'questions';
  });
  const [searchInput, setSearchInput] = useState(''); const [searchText, setSearchText] = useState('');
  const [name, setName] = useState(() => localStorage.getItem('rg_reviewer') ?? '本机用户');
  const [message, setMessage] = useState(''); const [conflict, setConflict] = useState(false);
  const [selectedClaim, setSelectedClaim] = useState<number | null>(null); const [selectedEvidence, setSelectedEvidence] = useState<EvidenceTarget | null>(null);
  const [editing, setEditing] = useState<{ claim: Claim; revision: number } | null>(null);
  const [questionProject, setQuestionProject] = useState<string | null>(null);
  const [questionDrafts, setQuestionDrafts] = useState<Record<string, QuestionDraft>>({});
  const currentProject = useRef(projectId);
  const currentAuthorization = useRef(authorized);
  const currentDrafts = useRef(questionDrafts);
  const currentQuestionProject = useRef(questionProject);
  const currentActor = useRef(`human:${name.trim()}`);
  currentProject.current = projectId; currentAuthorization.current = authorized;
  currentDrafts.current = questionDrafts; currentQuestionProject.current = questionProject;
  const actor = `human:${name.trim()}`;
  currentActor.current = actor;
  const onError = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.status === 401) setAuthorized(false);
    if (error instanceof ApiError && error.status === 409) setConflict(true);
    setMessage(error instanceof Error ? error.message : '本地读取失败');
  }, []);
  const onEvidence = useCallback((target: EvidenceTarget) => { setSelectedEvidence(target); }, []);
  const onClaim = useCallback((id: number) => { setSelectedClaim(id); setSelectedEvidence(null); }, []);
  const onWrite = useCallback((revision: number, id?: number) => {
    setEpoch(previous => previous + 1); setConflict(false); setMessage(`记录已追加，当前版本 ${revision}`);
    if (id) setSelectedClaim(id);
  }, []);
  const onEdit = useCallback((claim: Claim, revision: number) => {
    if (requiresDecisionTarget(claim)) { onError(new Error('未确定对象的决定不能直接修改，请先确定对象。')); return; }
    setEditing({ claim, revision });
  }, [onError]);
  function refresh() { setGraph(null); setEpoch(previous => previous + 1); setConflict(false); setMessage(''); }
  function changeQuestionDraft(draftProject: string, draft: QuestionDraft) {
    currentDrafts.current = { ...currentDrafts.current, [draftProject]: draft };
    setQuestionDrafts(currentDrafts.current);
  }
  function closeQuestion() { currentQuestionProject.current = null; setQuestionProject(null); }
  function createdQuestion(savedProject: string, result: QuestionResult) {
    setEpoch(previous => previous + 1);
    if (!isCurrentQuestionIntent(currentDrafts.current[savedProject], savedProject, currentActor.current, result.request_id)) return;
    changeQuestionDraft(savedProject, emptyQuestionDraft());
    if (currentProject.current === savedProject && currentAuthorization.current && currentQuestionProject.current === savedProject) {
      closeQuestion(); setConflict(false); setSelectedEvidence(null); setSelectedClaim(result.claim_id);
      setMessage(`${result.replayed ? '已找回保存的问题' : '研究问题已保存'}，记录 #${result.claim_id} · 当前版本 ${result.revision}`);
    }
  }
  useEffect(() => {
    if (!authorized) return;
    const controller = new AbortController();
    api<{ projects: Project[] }>('/projects', undefined, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      setProjects(result.projects);
      setProjectId(current => result.projects.some(item => item.project_id === current) ? current : result.projects[0]?.project_id ?? '');
    }).catch(error => { if (!controller.signal.aborted) onError(error); });
    return () => controller.abort();
  }, [authorized, epoch, onError]);
  useEffect(() => {
    setSelectedClaim(null); setSelectedEvidence(null); setEditing(null); currentQuestionProject.current = null; setQuestionProject(null);
  }, [projectId]);
  useEffect(() => {
    setGraph(null);
    if (!projectId || !authorized) return;
    const controller = new AbortController();
    api<GraphData>(`/graph?${query({ project: projectId })}`, undefined, controller.signal).then(result => { if (!controller.signal.aborted) setGraph(result); }).catch(error => { if (!controller.signal.aborted) onError(error); });
    return () => controller.abort();
  }, [projectId, authorized, epoch, onError]);
  useEffect(() => { localStorage.setItem('rg_reviewer', name); }, [name]);
  useEffect(() => {
    function acceptLink() {
      if (new URLSearchParams(window.location.hash.slice(1)).has('token')) {
        readToken(); setAuthorized(true); setEpoch(previous => previous + 1); setMessage('');
      }
    }
    window.addEventListener('hashchange', acceptLink);
    return () => window.removeEventListener('hashchange', acceptLink);
  }, []);
  const project = projects.find(item => item.project_id === projectId);
  const qa = useQaWorkspace({ project: projectId, active: view === 'qa', authorized, epoch, onError, onPermissionChange: () => setEpoch(previous => previous + 1) });
  const decisions = useManualDecisions({ project, actor, authorized, revision: graph?.revision ?? null, epoch, onError, onRefresh: refresh,
    onDataChanged: () => setEpoch(previous => previous + 1),
    onSaved: (claim, revision, text) => { setConflict(false); setSelectedEvidence(null); setSelectedClaim(claim); setMessage(`${text}，记录 #${claim} · 当前版本 ${revision}`); }, onClaim });
  useEffect(() => {
    function close(event: KeyboardEvent) { if (event.key === 'Escape') { if (decisions.visible) decisions.close(); else if (questionProject) closeQuestion(); else if (editing) setEditing(null); else if (selectedEvidence) setSelectedEvidence(null); else setSelectedClaim(null); } }
    window.addEventListener('keydown', close); return () => window.removeEventListener('keydown', close);
  }, [editing, selectedEvidence, questionProject, decisions.visible, decisions.close]);
  const candidateCount = graph?.claims.filter(item => item.effective_state === 'candidate').length ?? 0;
  if (!authorized) return <div className="login-page"><section><div className="brand-mark">R<span>G</span></div><span className="eyebrow">ResearchGraph · 本地研究工作区</span><h1>打开你的研究决定史</h1><p>请使用本机服务启动时给出的链接，或输入该次启动的访问令牌。</p><form onSubmit={event => { event.preventDefault(); setToken(tokenInput); setAuthorized(true); refresh(); }}><label>访问令牌<input type="password" value={tokenInput} onChange={event => setTokenInput(event.target.value)} autoComplete="off" required /></label><button type="submit" className="button primary">进入本地工作区 <Icon name="arrow" size={16} /></button></form>{message && <p className="error-message" role="alert">{message}</p>}<small>资料保存在本机 · 远程模型须先获得项目授权</small></section></div>;
  return <div className="app-shell"><aside className="sidebar"><div className="brand"><div className="brand-mark">R<span>G</span></div><div><strong>ResearchGraph</strong><span>研究决定史</span></div></div><div className="project-picker"><label htmlFor="project-picker">当前项目</label><select id="project-picker" value={projectId} onChange={event => setProjectId(event.target.value)}>{projects.map(item => <option key={item.project_id} value={item.project_id}>{item.name}</option>)}{!projects.length && <option value="">尚无项目</option>}</select><span>{project?.sessions ?? 0} 个会话 · {project?.claims ?? 0} 条记录</span></div><div className="nav-label">研究工作区</div><nav aria-label="研究工作区">{(['questions', 'qa', 'review', 'timeline', 'graph', 'health'] as View[]).map(key => <button key={key} className={view === key ? 'active' : ''} onClick={() => { setView(key); setSelectedClaim(null); setSelectedEvidence(null); }}><Icon name={viewMeta[key].icon} />{viewMeta[key].title}{key === 'review' && <span className="nav-count">{candidateCount}</span>}</button>)}</nav><div className="sidebar-bottom"><label>复核者<input aria-label="复核者姓名" value={name} onChange={event => setName(event.target.value)} maxLength={100} /></label><div className="local-status"><span />本机连接 · 127.0.0.1</div><p>原文只追加<br />每条决定都能追溯</p></div></aside><div className="main-shell"><header className="topbar"><div className="breadcrumb">研究工作区 <span>/</span> {project?.name ?? '项目未选择'}</div><form className="global-search" onSubmit={event => { event.preventDefault(); setSearchText(searchInput.trim()); setView('search'); setSelectedClaim(null); setSelectedEvidence(null); }}><Icon name="search" size={16} /><input aria-label="搜索会话原文" placeholder="搜索原文与决定依据…" value={searchInput} onChange={event => setSearchInput(event.target.value)} maxLength={500} /><button type="submit" aria-label="开始搜索"><Icon name="arrow" size={15} /></button></form><button className="icon-button" aria-label="刷新数据" onClick={refresh}><Icon name="refresh" /></button></header><main><div className="page-heading"><div><span className="eyebrow">{project?.name ?? '本地研究工作区'} / {view === 'health' ? '采集与覆盖' : '研究记忆'}</span><h1>{viewMeta[view].title}</h1><p>{viewMeta[view].subtitle}</p></div><div className="revision-tag">图版本 <strong>{graph?.revision ?? '—'}</strong><span>本地资料</span></div></div>
      {message && <div className={`message-banner ${conflict ? 'conflict' : ''}`} role={conflict ? 'alert' : 'status'}><span>{message}</span>{conflict ? <button className="text-button" onClick={refresh}>刷新后重新复核</button> : <button className="icon-button" aria-label="关闭提示" onClick={() => setMessage('')}><Icon name="close" size={15} /></button>}</div>}
      {graph?.partial && <p className="notice">此视图仅载入前 {graph.limit} 条记录，不能据此判断项目全貌。完整记录可在复核队列分页查看。</p>}
      {!projectId ? <Empty title="尚未登记项目">使用项目登记与会话导入后，这里会读取实际研究记录。</Empty> : !graph && ['questions', 'timeline', 'graph'].includes(view) ? <Loading /> : <>
        {view === 'questions' && graph && <Questions data={graph} onClaim={onClaim} onEvidence={onEvidence} onCreateQuestion={() => {
          if (!currentDrafts.current[projectId]) changeQuestionDraft(projectId, emptyQuestionDraft());
          setSelectedClaim(null); setSelectedEvidence(null); setEditing(null); currentQuestionProject.current = projectId; setQuestionProject(projectId);
        }} />}
        {view === 'timeline' && graph && <TimelineView data={graph} onClaim={onClaim} onEvidence={onEvidence} onCreateDecision={decisions.openCreate} onResolve={decisions.openResolve} />}
        {view === 'review' && <ReviewQueue onResolve={decisions.openResolve} project={projectId} epoch={epoch} actor={actor} onWrite={onWrite} onError={onError} onEdit={onEdit} onEvidence={onEvidence} onClaim={onClaim} />}
        {view === 'qa' && <QaView workspace={qa} onEvidence={onEvidence} />}
        {view === 'health' && <HealthView project={projectId} epoch={epoch} onEvidence={onEvidence} onError={onError} />}
        {view === 'graph' && graph && <Suspense fallback={<Loading />}><GraphView data={graph} onClaim={onClaim} /></Suspense>}
        {view === 'search' && <SearchView project={projectId} text={searchText} onEvidence={onEvidence} onError={onError} />}
      </>}
      <footer className="page-footer"><span>ResearchGraph · 可查证的研究决定史</span><span>审核、采用、证据、运行分别记录</span></footer>
    </main></div>
    {selectedClaim && <ClaimDrawer onResolve={decisions.openResolve} id={selectedClaim} epoch={epoch} actor={actor} onClose={() => setSelectedClaim(null)} onEvidence={onEvidence} onError={onError} onWrite={onWrite} onEdit={onEdit} onClaim={onClaim} />}
    {selectedEvidence && <aside className="evidence-drawer" role="dialog" aria-label="原文证据"><header><div><span className="eyebrow">来源证据</span><h2>原文 #{selectedEvidence.event_id}</h2></div><button className="icon-button" aria-label="关闭原文" onClick={() => setSelectedEvidence(null)}><Icon name="close" /></button></header><div className="drawer-content"><EvidencePanel target={selectedEvidence} onError={onError} onEvidence={onEvidence} /></div></aside>}
    {editing && <EditDialog key={editing.claim.claim_id} claim={editing.claim} revision={editing.revision} actor={actor} onClose={() => setEditing(null)} onWrite={onWrite} onError={onError} />}
    {decisions.dialogs}
    {questionProject === projectId && project && questionDrafts[projectId] && <ManualQuestionDialog key={projectId} project={project} revision={graph?.revision ?? null} actor={actor} draft={questionDrafts[projectId]}
      onDraft={draft => changeQuestionDraft(project.project_id, draft)} onClose={closeQuestion}
      onCreated={result => createdQuestion(project.project_id, result)} onError={onError} onRefresh={refresh} />}
  </div>;
}
