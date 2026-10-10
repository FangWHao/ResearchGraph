import { useEffect, useMemo, useRef, useState } from 'react';
import { Background, Controls, Handle, Position, ReactFlow, ReactFlowProvider, useReactFlow, useNodesInitialized, applyNodeChanges } from '@xyflow/react';
import type { Edge, Node, NodeProps } from '@xyflow/react';
import type { ElkNode, ELK } from 'elkjs';
import '@xyflow/react/dist/style.css';
import { Badge, Empty, Icon, Loading } from './components';
import type { useResearchGraph } from './useResearchGraph';
import { adoption, actionNames, edgeText, evidenceNames, evidenceStateNames, evidenceState, foldGroup, foldProjection, graphKind, graphNodeId, joinNames, joinRecords, kindNames, label, projectGraph, scopeKey, scopeText } from './model';
import type { FoldResult } from './model';
import { useViewState } from './useViewState';
import { resolvePersonalView } from './viewState';
import { researchIntent } from './researchGraph';
import type { Claim, ResearchGraphData, Kind, PersonalView } from './types';

type ResearchNode = Node<{ claim: Claim; versions: Claim[]; selectedVersion?: number; selectVersion: (id: string, version: string) => void; claims: Claim[]; showCandidates: boolean; diagnostics?: { claimId: number; reasons: string[] }[]; group?: FoldResult; title?: string }>;
let engine: Promise<ELK> | undefined;
function layoutGraph(graph: ElkNode): Promise<ElkNode> {
  engine ??= import('elkjs/lib/elk.bundled.js').then(module => new module.default());
  return engine.then(elk => elk.layout(graph));
}
function recordText(claim: Claim): string {
  if (claim.claim_type === 'decision_event') return `决定 · ${actionNames[claim.payload.action ?? ''] ?? '动作未知'} · ${claim.payload.reason ?? ''}`;
  if (claim.claim_type === 'evidence_event') return `证据状态 · ${evidenceNames[claim.payload.state ?? ''] ?? '未知'}`;
  if (claim.claim_type === 'join_ports') return `${joinNames[claim.payload.semantics ?? ''] ?? '汇合语义未知'} · ${claim.payload.inputs?.map(input => `${input.port}: ${input.ref}`).join('；')}`;
  if (claim.claim_type === 'merge') return `人工合并 · ${claim.payload.source} → ${claim.payload.target}`;
  return label(claim);
}
function basisText(basis: string): string { return ({ direct_record: '直接原话', manual: '人工记录', time_match: '时间匹配', model_inference: '模型推断' } as Record<string, string>)[basis] ?? `未知依据（${basis}）`; }
function ResearchCard({ id, data }: NodeProps<ResearchNode>) {
  const identity = data.claim;
  const claim = data.versions.length === 1 ? identity : data.versions.find(item => item.claim_id === data.selectedVersion);
  const decision = adoption(data.claims, identity.entity_id!, identity.scope);
  const joins = joinRecords(data.claims, identity.entity_id!, identity.scope, data.showCandidates);
  const records = data.group ? data.claims.filter(item => data.group!.claimIds.includes(item.claim_id)) : [];
  return <div className={`research-node ${data.group ? 'process-node' : ''}`}>
    {data.group ? data.group.boundaryEdges.filter(edge => data.group!.members.includes(edge.target)).map((edge, index, all) => <Handle key={edge.id} id={`in-${edge.id}`} type="target" position={Position.Left} style={{ top: `${(index + 1) * 100 / (all.length + 1)}%` }} />) : <Handle type="target" position={Position.Left} />}
    <div className="node-top"><span>{data.group ? '手工过程组 · 仅视图' : kindNames[graphKind(data.versions) as Kind] ?? '对象种类未知或矛盾'}</span>{!data.group && claim && <Badge state={claim.effective_state} />}</div>
    <strong>{data.group ? data.title : claim ? label(claim) : `${data.versions.length} 个有效内容版本 · 未选择`}</strong>
    <p>{data.group ? `${data.group.members.length} 个节点 · ${data.group.evidenceIds.length} 条原证据` : scopeText(identity.scope)}</p>
    {data.group ? <div className="group-retention"><p>保留 {records.length} 条记录 · {records.filter(item => item.effective_state === 'candidate').length} 条待复核</p><p>决定 {records.filter(item => item.claim_type === 'decision_event').length} · 证据状态 {records.filter(item => item.claim_type === 'evidence_event').length} · 范围 {new Set(records.map(item => scopeKey(item.scope))).size}</p><span>全部原关系、共同输入与版本见下方保留清单。</span></div> : <>
      {data.versions.length > 1 && <label className="node-version nodrag">显示内容版本<select aria-label={`显示内容版本 ${identity.entity_id}`} value={claim?.claim_id ?? ''} onChange={event => data.selectVersion(id, event.target.value)}><option value="">未选择（不推定当前版本）</option>{data.versions.map(item => <option key={item.claim_id} value={item.claim_id}>#{item.claim_id} {label(item)} · {item.effective_state === 'candidate' ? '待复核' : '已确认'}</option>)}</select><span>关系指向对象与范围，未指定内容版本。</span></label>}
      <div className="node-dimensions"><span>采用 {actionNames[decision] ?? (decision === 'unknown_scope' ? '范围未知' : decision === 'time_unknown' ? '时间未知' : decision === 'conflict' ? '有冲突' : '未知')}</span><span>证据 {evidenceStateNames[evidenceState(data.claims, identity.entity_id!, identity.scope)]}</span><span>运行 未知</span></div>
      {joins.map(join => <div className="join-semantics" key={join.claim_id}>#{join.claim_id} {joinNames[join.payload.semantics ?? ''] ?? '汇合语义未知'}{join.effective_state === 'candidate' && ' · 待复核'}<p>{join.payload.inputs?.map(input => `${input.port}: ${input.ref} ${join.payload.semantics === 'compare_then_select' ? input.ref === join.payload.selected ? '（被选输入）' : '（仅比较，未选）' : ''}`).join('；')}</p>{data.diagnostics?.find(item => item.claimId === join.claim_id) && <p className="error-message">异常源声明：{data.diagnostics.find(item => item.claimId === join.claim_id)!.reasons.join('；')}；不作为有效输入边界。</p>}</div>)}
      {joins.length > 1 && <p>多条汇合记录，逐条保留；不推定唯一语义。</p>}
    </>}
    {data.group ? data.group.boundaryEdges.filter(edge => data.group!.members.includes(edge.source)).map((edge, index, all) => <Handle key={edge.id} id={`out-${edge.id}`} type="source" position={Position.Right} style={{ top: `${(index + 1) * 100 / (all.length + 1)}%` }} />) : <Handle type="source" position={Position.Right} />}
  </div>;
}
const nodeTypes = { research: ResearchCard };

function Canvas({ data, allowFold, onClaim, personal, restored, restoring, onRestore, onEdit, user, onUser }: { data: ResearchGraphData; allowFold: boolean; onClaim: (id: number) => void; personal: ReturnType<typeof useViewState>; restored: { token: number; view: PersonalView } | null; restoring: boolean; onRestore: () => void; onEdit: () => void; user: string; onUser: (user: string) => void }) {
  const editRef = useRef(onEdit); editRef.current = onEdit;
  const [nodes, setNodes] = useState<ResearchNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const [fold, setFold] = useState<FoldResult | null>(null);
  const [notice, setNotice] = useState('');
  const [showCandidates, setShowCandidates] = useState(true);
  const [groupName, setGroupName] = useState('研究过程');
  const [versions, setVersions] = useState<Record<string, number>>({});
  const [focused, setFocused] = useState<string | null>(null);
  const layoutCache = useRef<{ key: string; positions: Map<string, { x: number; y: number }> } | null>(null);
  const { fitView, getViewport, setViewport } = useReactFlow();
  const [layoutReady, setLayoutReady] = useState(false);
  const pendingViewport = useRef<PersonalView['viewport'] | null>(null);
  const graphIdentity = (visible: boolean, claims: Claim[]) => `${data.project_id}:${data.occurred_until}:${data.known_until}:${data.revision}:${visible}:${claims.map(graphNodeId).join('|')}`;
  const initialized = useNodesInitialized(); const pendingFit = useRef(false);
  const projected = useMemo(() => projectGraph(data.claims, showCandidates), [data.claims, showCandidates]);
  const entities = projected.entities, semanticEdges = projected.edges;
  const graphKey = graphIdentity(showCandidates, entities);
  useEffect(() => { setFold(null); setSelected([]); setVersions({}); setFocused(null); setNotice(''); setLayoutReady(false); }, [data.revision, data.claims]);
  useEffect(() => {
    if (!restored) return;
    try {
      const checked = resolvePersonalView(restored.view, data);
      pendingFit.current = false; pendingViewport.current = restored.view.viewport;
      layoutCache.current = { key: graphIdentity(restored.view.show_candidates, checked.graph.entities), positions: new Map(Object.entries(restored.view.positions)) };
      setLayoutReady(false); setShowCandidates(restored.view.show_candidates); setVersions(restored.view.selected_versions); setFold(checked.fold);
      setGroupName(restored.view.process_group?.name ?? '研究过程'); setSelected([]); setFocused(null); setNotice('已按保存条件重新核对全部记录并恢复个人布局；正式状态仍来自研究记录。');
    } catch (error) { setNotice((error as Error).message); }
  }, [restored, data]);
  useEffect(() => {
    let cancelled = false;
    const groupId = 'view-process-group';
    const folded = new Set(fold?.members ?? []);
    const mapped = foldProjection(semanticEdges, fold);
    const selectVersion = (id: string, version: string) => { editRef.current(); setVersions(current => { const next = { ...current }; if (version) next[id] = Number(version); else delete next[id]; return next; }); };
    const rawNodes: ResearchNode[] = entities.filter(item => !folded.has(graphNodeId(item))).map(claim => ({ id: graphNodeId(claim), type: 'research', selected: selectedRef.current.includes(graphNodeId(claim)), position: { x: 0, y: 0 }, data: { claim, versions: projected.versions.get(graphNodeId(claim))!, selectedVersion: versions[graphNodeId(claim)], selectVersion, claims: data.claims, showCandidates, diagnostics: projected.diagnostics } }));
    if (fold) {
      const first = entities.find(item => graphNodeId(item) === fold.entry);
      if (first) rawNodes.push({ id: groupId, type: 'research', position: { x: 0, y: 0 }, data: { claim: first, versions: [], selectVersion, claims: data.claims, showCandidates, group: fold, title: groupName } });
    }
    const flowEdges: Edge[] = mapped.map(edge => {
      const claim = data.claims.find(item => item.claim_id === edge.claimId)!;
      return { id: edge.id, source: edge.source, target: edge.target, sourceHandle: edge.sourceHandle, targetHandle: edge.targetHandle, label: edgeText(edge), data: { claimId: edge.claimId, original: semanticEdges.find(original => original.id === edge.id) }, style: { stroke: '#758078', strokeDasharray: edge.semantic === false ? '2 6' : claim.effective_state === 'candidate' ? '5 4' : undefined }, labelStyle: { fontSize: 11 }, labelBgStyle: { fill: '#fbfaf7' } };
    });
    function applyPositions(positions: Map<string, { x: number; y: number }>, initial = false) {
      if (cancelled) return;
      setNodes(rawNodes.map(node => ({ ...node, position: positions.get(node.id) ?? (fold ? positions.get(fold.entry) : undefined) ?? { x: 0, y: 0 } })));
      setEdges(flowEdges);
      if (initial) pendingFit.current = true;
      else if (!pendingViewport.current && !pendingFit.current) setLayoutReady(true);
      if (!rawNodes.length && layoutCache.current?.key === graphKey) { pendingFit.current = false; setLayoutReady(true); }
    }
    if (layoutCache.current?.key === graphKey) applyPositions(layoutCache.current.positions);
    else { setLayoutReady(false); layoutGraph({ id: 'root', layoutOptions: { 'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.spacing.nodeNode': '44', 'elk.layered.spacing.nodeNodeBetweenLayers': '110' }, children: rawNodes.map(node => ({ id: node.id, width: 292, height: graphKind(node.data.versions) === 'join' ? 370 : node.data.versions.length > 1 ? 320 : 220 })), edges: mapped.filter(edge => edge.semantic !== false).map(edge => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })) })
      .then(layout => {
        if (cancelled) return;
        const positions = new Map(layout.children!.map(item => [item.id, { x: item.x ?? 0, y: item.y ?? 0 }]));
        layoutCache.current = { key: graphKey, positions }; applyPositions(positions, true);
      }).catch(error => { if (!cancelled) { setNotice(`布局失败：${error.message}`); applyPositions(new Map(rawNodes.map((node, index) => [node.id, { x: index * 330, y: 80 }])), true); } }); }
    return () => { cancelled = true; };
  }, [entities, semanticEdges, fold, groupName, data.claims, fitView, showCandidates, versions, projected.versions, graphKey]);
  useEffect(() => {
    if (!initialized || !nodes.length || (!pendingFit.current && !pendingViewport.current)) return;
    let cancelled = false;
    const frame = requestAnimationFrame(() => {
      const viewport = pendingViewport.current; pendingViewport.current = null; pendingFit.current = false;
      void (viewport ? setViewport(viewport, { duration: 0 }) : fitView({ padding: 0.18, maxZoom: 1, duration: 250 })).then(() => { if (!cancelled) setLayoutReady(true); });
    });
    return () => { cancelled = true; cancelAnimationFrame(frame); };
  }, [initialized, nodes, fitView, setViewport]);
  async function save() {
    try {
      if (!layoutReady || !allowFold || layoutCache.current?.key !== graphKey) throw new Error('完整研究图及布局尚未就绪，不能保存。');
      const view: PersonalView = { format_version: 1, reading: { project_id: data.project_id, revision: data.revision, occurred_until: data.occurred_until, known_until: data.known_until }, show_candidates: showCandidates, positions: Object.fromEntries(layoutCache.current.positions), viewport: getViewport(), selected_versions: versions, process_group: fold ? { name: groupName, members: fold.members } : null };
      resolvePersonalView(view, data); await personal.save(view);
    } catch (error) { setNotice((error as Error).message); }
  }
  function collapse() {
    onEdit();
    try { setFold(foldGroup(selected, semanticEdges, data.claims, allowFold && !data.partial && showCandidates && projected.unresolvedClaimIds.length === 0)); setFocused(null); setNotice('过程组只改变视图，原节点、关系、决定、共同输入与引用均保留。'); }
    catch (error) { setNotice((error as Error).message); }
  }
  function choose(id: string, checked: boolean) {
    setSelected(current => checked ? [...new Set([...current, id])] : current.filter(value => value !== id));
    setNodes(current => current.map(node => node.id === id ? { ...node, selected: checked } : node));
  }
  const shownRecords = fold ? data.claims.filter(item => fold.claimIds.includes(item.claim_id)) : focused ? data.claims.filter(item => graphNodeId(item) === focused || item.payload.target === entities.find(entity => graphNodeId(entity) === focused)?.entity_id) : [];
  return <div className="graph-workspace">
    <section aria-label="个人研究图视图" className="notice" data-view-ready={layoutReady && allowFold}>
      <div className="process-controls"><label style={{ minWidth: 0, flex: '1 1 220px' }}>本机个人视图标识<input aria-label="个人视图标识" style={{ width: '100%' }} value={user} maxLength={100} onChange={event => onUser(event.target.value)} /></label><button className="button secondary" disabled={!user.trim() || !personal.known || personal.busy || restoring || !layoutReady || !allowFold} onClick={() => { void save(); }}>保存个人视图</button><button className="button secondary" disabled={!user.trim() || personal.busy || restoring} onClick={onRestore}>读取已保存视图</button>{(personal.busy || restoring) && <button className="text-button" onClick={onEdit}>停止个人视图等待</button>}</div>
      <p className="small">此姓名只区分本机个人布局，不是账户鉴权。主动保存节点位置、显示版本、过程组及视角；不会修改原文、审核或采用状态。可以拖动节点固定位置。</p>
      {personal.record?.view_id && <p className="small" data-view-id={personal.record.view_id}>保存记录 #{personal.record.view_id} · {personal.record.saved_at}</p>}
      {personal.info && <p role="status" data-personal-info>{personal.info}</p>}{personal.error && <p role="alert" className="error-message" data-personal-error>{personal.error}</p>}
    </section>
    {data.partial && <p className="notice">当前是部分记录投影，不能证明完整边界，不能折叠。</p>}
    {projected.unresolvedClaimIds.length > 0 && <section className="notice" aria-label="研究图异常记录"><p>{projected.unresolvedClaimIds.length} 条记录缺少同范围端点、有效方向或完整汇合语义；原记录保留，当前图不能折叠。</p>{projected.diagnostics.map(item => <p className="small" key={item.claimId}><button className="text-button" onClick={() => onClaim(item.claimId)}>#{item.claimId}</button> {item.reasons.join('；')}</p>)}</section>}
    <div className="graph-tools"><label><input type="checkbox" checked={showCandidates} onChange={event => { onEdit(); setFold(null); setVersions({}); setShowCandidates(event.target.checked); }} />显示待复核记录</label><span>虚线 = 待复核 · 点线 = 仅比较或相同主题</span><button className="button secondary" onClick={() => { onEdit(); void fitView({ padding: 0.18, maxZoom: 1, duration: 250 }); }}><Icon name="refresh" size={15} />适配画布</button></div>
    <div className="graph-canvas" data-testid="research-canvas"><ReactFlow<ResearchNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes} nodesDraggable nodesConnectable={false} onNodesChange={changes => setNodes(current => applyNodeChanges(changes, current))}
      onNodeDragStart={onEdit} onNodeDragStop={(_, node) => { layoutCache.current?.positions.set(node.id === 'view-process-group' && fold ? fold.entry : node.id, { ...node.position }); }} onMoveStart={event => { if (event) onEdit(); }}
      onNodeClick={(event, node) => { if (event.shiftKey || (event.target as HTMLElement).closest('select')) return; if (node.data.group) setFocused(null); else { setFocused(node.id); const version = node.data.versions.length === 1 ? node.data.claim : node.data.versions.find(item => item.claim_id === node.data.selectedVersion); if (version) onClaim(version.claim_id); } }}
      onEdgeClick={(_, edge) => onClaim(Number(edge.data?.claimId))}
      onSelectionChange={({ nodes: selection }) => setSelected(current => { const next = selection.map(node => node.id); return next.length === current.length && next.every(id => current.includes(id)) ? current : next; })} selectionOnDrag panOnDrag={[1, 2]} multiSelectionKeyCode="Shift"
      minZoom={0.12} maxZoom={1.5}><Background color="#dedfd8" gap={24} size={1} /><Controls showInteractive={false} onZoomIn={onEdit} onZoomOut={onEdit} onFitView={onEdit} /></ReactFlow>
      {!entities.length && <div className="canvas-empty"><Empty title="此筛选下没有研究节点">实体候选完成提取后会出现在这里。</Empty></div>}
    </div>
    <details className="graph-node-picker"><summary>选择过程节点（也可 Shift + 单击画布）</summary><div>{entities.map(item => <label key={graphNodeId(item)}><input type="checkbox" disabled={!!fold} data-node-id={graphNodeId(item)} aria-label={`选择节点 ${projected.versions.get(graphNodeId(item))!.length > 1 ? `多版本对象 ${item.entity_id}` : label(item)} ${scopeText(item.scope)}`} checked={selected.includes(graphNodeId(item))} onChange={event => choose(graphNodeId(item), event.target.checked)} /><span>{projected.versions.get(graphNodeId(item))!.length > 1 ? `多版本对象 · ${item.entity_id}` : label(item)}<small>{scopeText(item.scope)}</small></span></label>)}</div></details>
    <div className="process-controls"><span>已选 {selected.length} 个节点</span><input aria-label="过程组名称" value={groupName} onChange={event => { onEdit(); setGroupName(event.target.value); }} maxLength={60} /><button className="button secondary" disabled={!!fold} onClick={collapse}>折叠所选（{selected.length}）</button><button className="text-button" disabled={!fold} onClick={() => { onEdit(); setFold(null); setNotice('已在同一修订恢复全部原节点、内部与边界关系、决定、共同输入、证据及节点位置。'); }}>展开过程组</button>{fold && <button className="text-button" onClick={() => { onEdit(); void fitView({ nodes: [{ id: 'view-process-group' }], padding: 0.35, maxZoom: 1, duration: 250 }); }}>聚焦过程组</button>}</div>
    {notice && <p role="status" className="notice graph-notice">{notice}</p>}
    {shownRecords.length > 0 && <section className="graph-retained" aria-label="保留的原始记录"><h3>{fold ? '过程组保留记录' : '对象版本与历史'} · {shownRecords.length}</h3><p className="muted small">撤回、阴性证据、各范围版本和待复核记录逐条保留；审核与采用独立。单击记录可查看原文。</p>{fold && <details open><summary>原关系与边界端口 · {fold.internalEdges.length + fold.boundaryEdges.length}</summary>{[...fold.internalEdges, ...fold.boundaryEdges].map(edge => <div className="graph-original-edge" key={edge.id} data-edge-id={edge.id} data-original-edge={JSON.stringify(edge)}><button className="text-button" onClick={() => onClaim(edge.claimId)}>#{edge.claimId} {edgeText(edge)}</button><span>{edge.sourceEntity ?? edge.source} → {edge.targetEntity ?? edge.target}{edge.targetPort && ` · 端口 ${edge.targetPort}`}</span><small>原关系 {edge.id} · 证据 {edge.evidenceIds?.join('、') || '缺失'}</small></div>)}</details>}{shownRecords.map(item => <article key={item.claim_id} data-claim-id={item.claim_id}><div><button className="text-button" onClick={() => onClaim(item.claim_id)}>#{item.claim_id} {item.replacement_ids.length > 0 && '已被替代 · 历史记录 · '}{recordText(item)}</button><Badge state={item.effective_state} /></div><p>{scopeText(item.scope)} · 依据 {basisText(item.basis)} · {item.evidence.length} 条原证据</p>{item.replacement_ids.length > 0 && <p>替代记录 {item.replacement_ids.map(id => `#${id}`).join('、')}</p>}</article>)}</section>}
    <p className="muted small">单击节点或关系按相同阅读条件查看只读历史及原始引用。未选比较对象不表示使用，相同主题仅供检索。文件运行与实际输入输出完整性另行核对；此版本未提供影响传播。</p>
  </div>;
}
export function GraphView({ workspace, onClaim, user, onUser, authorized, epoch, onError }: { workspace: ReturnType<typeof useResearchGraph>; onClaim: (id: number) => void; user: string; onUser: (user: string) => void; authorized: boolean; epoch: number; onError: (error: unknown) => void }) {
  const { data, draft, busy, stale, error } = workspace;
  const personal = useViewState({ project: workspace.project, user, intent: workspace.intent, authorized, epoch, onError });
  const [restored, setRestored] = useState<{ token: number; view: PersonalView } | null>(null);
  const [restoring, setRestoring] = useState(false); const [restoreError, setRestoreError] = useState('');
  const restoreActive = useRef(false);
  const sequence = useRef(0); const context = useRef(''); const key = JSON.stringify([workspace.project, user.trim(), authorized, epoch]); context.current = JSON.stringify([key, workspace.intent]);
  function edit() { sequence.current++; personal.cancel(); if (restoreActive.current) workspace.cancel(); restoreActive.current = false; setRestoring(false); setRestoreError(''); setRestored(null); }
  useEffect(() => { sequence.current++; setRestored(null); setRestoring(false); setRestoreError(''); return () => { sequence.current++; if (restoreActive.current) workspace.cancel(); restoreActive.current = false; }; }, [key]);
  async function restore() {
    const token = ++sequence.current; const originalContext = context.current;
    restoreActive.current = true; setRestoring(true); setRestoreError('');
    try {
      const record = await personal.read();
      if (token !== sequence.current || context.current !== originalContext || !record) return;
      if (!record.view) { setRestoreError(record.unavailable_reason ?? '此标识尚未保存个人视图。'); return; }
      if (record.current_revision !== record.view.reading.revision) throw new Error('研究图修订已变化；原保存记录仍保留，不能直接恢复或混入当前图。');
      const nextDraft = { occurredUntil: record.view.reading.occurred_until, knownUntil: record.view.reading.known_until };
      const destination = JSON.stringify([key, researchIntent(workspace.project, nextDraft)]);
      const complete = await workspace.load({ draft: nextDraft, expectedRevision: record.view.reading.revision });
      if (token !== sequence.current || context.current !== destination || !complete) return;
      resolvePersonalView(record.view, complete); setRestored({ token, view: record.view });
    } catch (failure) { if (token === sequence.current) setRestoreError((failure as Error).message); }
    finally { if (token === sequence.current) { restoreActive.current = false; setRestoring(false); } }
  }
  return <section aria-label="完整研究图"><form className="filter-bar" onSubmit={event => { event.preventDefault(); edit(); void workspace.load(); }}>
    <label style={{ flex: '1 1 250px', minWidth: 0 }}>发生时间截止<input aria-label="研究图发生时间截止" className="mono" style={{ width: '100%' }} value={draft.occurredUntil} placeholder="留空：首次响应时间；输入须带时区" onChange={event => { edit(); workspace.change({ ...draft, occurredUntil: event.target.value }); }} /></label>
    <label style={{ flex: '1 1 250px', minWidth: 0 }}>当时已知截止<input aria-label="研究图已知时间截止" className="mono" style={{ width: '100%' }} value={draft.knownUntil} placeholder="如 2026-10-10T12:00:00+08:00" onChange={event => { edit(); workspace.change({ ...draft, knownUntil: event.target.value }); }} /></label>
    <button className="button secondary" type="submit" disabled={busy}>{busy ? '正在读取全部记录…' : '重新读取研究图'}</button>
    {busy && <button type="button" className="text-button" onClick={workspace.cancel}>停止页面等待</button>}
  </form><p className="small muted">范围：项目内所有范围，逐项保留。两个截止留空时由首页响应固定，后续各页与详情沿用同一修订和双时间。</p>
    {busy && <p role="status">正在读取 {workspace.received} / {workspace.total ?? '未知'} 条；未读完的记录不能证明完整边界。</p>}
    {error && <p role="alert" className="error-message">{error} {data && '此前的图保留，未拼接新旧页。'}{workspace.conflict && '修订冲突，需要主动重新读取，不自动切换到新修订。'}</p>}
    {stale && <p className="notice">阅读条件已改变，下方仍是旧条件的图；请主动重新读取，暂不允许折叠。</p>}
    {workspace.info && <p className="notice">{workspace.info}</p>}
    {restoreError && <p role="alert" className="error-message" data-restore-error>{restoreError}</p>}
    {data && <p className="small" data-research-summary data-reading={JSON.stringify({ project_id: data.project_id, revision: data.revision, occurred_until: data.occurred_until, known_until: data.known_until })}>已读取 {data.claims.length} / {data.total} 条 · {data.partial ? '登记记录未读齐' : '双时间内全部 L2 登记记录'} · 修订 {data.revision}<br />发生截止 {data.occurred_until}<br />已知截止 {data.known_until}</p>}
    {data ? <ReactFlowProvider key={data.project_id}><Canvas data={data} allowFold={!busy && !error && !stale} onClaim={onClaim} personal={personal} restored={restored} restoring={restoring} onRestore={() => { void restore(); }} onEdit={edit} user={user} onUser={value => { edit(); onUser(value); }} /></ReactFlowProvider> : busy ? <Loading /> : <Empty title="完整研究图暂不可用">请主动读取，读取失败不当作空项目。</Empty>}
  </section>;
}
