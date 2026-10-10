import { useEffect, useMemo, useRef, useState } from 'react';
import { Background, Controls, Handle, Position, ReactFlow, ReactFlowProvider, useReactFlow, applyNodeChanges } from '@xyflow/react';
import type { Edge, Node, NodeProps } from '@xyflow/react';
import type { ElkNode, ELK } from 'elkjs';
import '@xyflow/react/dist/style.css';
import { Badge, Empty, Icon } from './components';
import { adoption, actionNames, edgeText, evidenceNames, evidenceStateNames, evidenceState, foldGroup, foldProjection, graphNodeId, joinNames, joinRecords, kindNames, label, projectGraph, scopeKey, scopeText } from './model';
import type { FoldResult } from './model';
import type { Claim, GraphData, Kind } from './types';

type ResearchNode = Node<{ claim: Claim; versions: Claim[]; selectedVersion?: number; selectVersion: (id: string, version: string) => void; claims: Claim[]; showCandidates: boolean; group?: FoldResult; title?: string }>;
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
    <div className="node-top"><span>{data.group ? '手工过程组 · 仅视图' : claim ? kindNames[claim.payload.kind as Kind] : '多版本对象'}</span>{!data.group && claim && <Badge state={claim.effective_state} />}</div>
    <strong>{data.group ? data.title : claim ? label(claim) : `${data.versions.length} 个有效内容版本 · 未选择`}</strong>
    <p>{data.group ? `${data.group.members.length} 个节点 · ${data.group.evidenceIds.length} 条原证据` : scopeText(identity.scope)}</p>
    {data.group ? <div className="group-retention"><p>保留 {records.length} 条记录 · {records.filter(item => item.effective_state === 'candidate').length} 条待复核</p><p>决定 {records.filter(item => item.claim_type === 'decision_event').length} · 证据状态 {records.filter(item => item.claim_type === 'evidence_event').length} · 范围 {new Set(records.map(item => scopeKey(item.scope))).size}</p><span>全部原关系、共同输入与版本见下方保留清单。</span></div> : <>
      {data.versions.length > 1 && <label className="node-version nodrag">显示内容版本<select aria-label={`显示内容版本 ${identity.entity_id}`} value={claim?.claim_id ?? ''} onChange={event => data.selectVersion(id, event.target.value)}><option value="">未选择（不推定当前版本）</option>{data.versions.map(item => <option key={item.claim_id} value={item.claim_id}>#{item.claim_id} {label(item)} · {item.effective_state === 'candidate' ? '待复核' : '已确认'}</option>)}</select><span>关系指向对象与范围，未指定内容版本。</span></label>}
      <div className="node-dimensions"><span>采用 {actionNames[decision] ?? (decision === 'unknown_scope' ? '范围未知' : decision === 'time_unknown' ? '时间未知' : decision === 'conflict' ? '有冲突' : '未知')}</span><span>证据 {evidenceStateNames[evidenceState(data.claims, identity.entity_id!, identity.scope)]}</span><span>运行 未知</span></div>
      {joins.map(join => <div className="join-semantics" key={join.claim_id}>#{join.claim_id} {joinNames[join.payload.semantics ?? ''] ?? '汇合语义未知'}{join.effective_state === 'candidate' && ' · 待复核'}<p>{join.payload.inputs?.map(input => `${input.port}: ${input.ref} ${join.payload.semantics === 'compare_then_select' ? input.ref === join.payload.selected ? '（被选输入）' : '（仅比较，未选）' : ''}`).join('；')}</p></div>)}
      {joins.length > 1 && <p>多条汇合记录，逐条保留；不推定唯一语义。</p>}
    </>}
    {data.group ? data.group.boundaryEdges.filter(edge => data.group!.members.includes(edge.source)).map((edge, index, all) => <Handle key={edge.id} id={`out-${edge.id}`} type="source" position={Position.Right} style={{ top: `${(index + 1) * 100 / (all.length + 1)}%` }} />) : <Handle type="source" position={Position.Right} />}
  </div>;
}
const nodeTypes = { research: ResearchCard };

function Canvas({ data, onClaim }: { data: GraphData; onClaim: (id: number) => void }) {
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
  const { fitView } = useReactFlow();
  const projected = useMemo(() => projectGraph(data.claims, showCandidates), [data.claims, showCandidates]);
  const entities = projected.entities, semanticEdges = projected.edges;
  const graphKey = `${data.revision}:${showCandidates}:${entities.map(graphNodeId).join('|')}`;
  useEffect(() => { setFold(null); setSelected([]); setVersions({}); setFocused(null); setNotice(''); }, [data.revision, showCandidates, data.claims]);
  useEffect(() => {
    let cancelled = false;
    const groupId = 'view-process-group';
    const folded = new Set(fold?.members ?? []);
    const mapped = foldProjection(semanticEdges, fold);
    const selectVersion = (id: string, version: string) => setVersions(current => ({ ...current, [id]: Number(version) }));
    const rawNodes: ResearchNode[] = entities.filter(item => !folded.has(graphNodeId(item))).map(claim => ({ id: graphNodeId(claim), type: 'research', selected: selectedRef.current.includes(graphNodeId(claim)), position: { x: 0, y: 0 }, data: { claim, versions: projected.versions.get(graphNodeId(claim))!, selectedVersion: versions[graphNodeId(claim)], selectVersion, claims: data.claims, showCandidates } }));
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
      if (initial) requestAnimationFrame(() => { void fitView({ padding: 0.18, maxZoom: 1, duration: 250 }); });
    }
    if (layoutCache.current?.key === graphKey) applyPositions(layoutCache.current.positions);
    else layoutGraph({ id: 'root', layoutOptions: { 'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.spacing.nodeNode': '44', 'elk.layered.spacing.nodeNodeBetweenLayers': '110' }, children: rawNodes.map(node => ({ id: node.id, width: 292, height: node.data.claim.payload.kind === 'join' ? 370 : node.data.versions.length > 1 ? 320 : 220 })), edges: mapped.filter(edge => edge.semantic !== false).map(edge => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })) })
      .then(layout => {
        if (cancelled) return;
        const positions = new Map(layout.children!.map(item => [item.id, { x: item.x ?? 0, y: item.y ?? 0 }]));
        layoutCache.current = { key: graphKey, positions }; applyPositions(positions, true);
      }).catch(error => { if (!cancelled) { setNotice(`布局失败：${error.message}`); applyPositions(new Map(rawNodes.map((node, index) => [node.id, { x: index * 330, y: 80 }])), true); } });
    return () => { cancelled = true; };
  }, [entities, semanticEdges, fold, groupName, data.claims, fitView, showCandidates, versions, projected.versions, graphKey]);
  function collapse() {
    try { setFold(foldGroup(selected, semanticEdges, data.claims, !data.partial && showCandidates && projected.unresolvedClaimIds.length === 0)); setFocused(null); setNotice('过程组只改变视图，原节点、关系、决定、共同输入与引用均保留。'); }
    catch (error) { setNotice((error as Error).message); }
  }
  function choose(id: string, checked: boolean) {
    setSelected(current => checked ? [...new Set([...current, id])] : current.filter(value => value !== id));
    setNodes(current => current.map(node => node.id === id ? { ...node, selected: checked } : node));
  }
  const shownRecords = fold ? data.claims.filter(item => fold.claimIds.includes(item.claim_id)) : focused ? data.claims.filter(item => graphNodeId(item) === focused || item.payload.target === entities.find(entity => graphNodeId(entity) === focused)?.entity_id) : [];
  return <div className="graph-workspace">
    {data.partial && <p className="notice">当前是部分记录投影，不能证明完整边界，不能折叠。</p>}
    {projected.unresolvedClaimIds.length > 0 && <p className="notice">{projected.unresolvedClaimIds.length} 条关系缺少同范围端点或完整汇合语义，保留为未显示记录，当前图不能折叠：{projected.unresolvedClaimIds.map(id => <button className="text-button" key={id} onClick={() => onClaim(id)}>#{id}</button>)}</p>}
    <div className="graph-tools"><label><input type="checkbox" checked={showCandidates} onChange={event => setShowCandidates(event.target.checked)} />显示待复核记录</label><span>虚线 = 待复核 · 点线 = 仅比较或相同主题</span><button className="button secondary" onClick={() => { void fitView({ padding: 0.18, maxZoom: 1, duration: 250 }); }}><Icon name="refresh" size={15} />适配画布</button></div>
    <div className="graph-canvas" data-testid="research-canvas"><ReactFlow<ResearchNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView nodesDraggable={false} nodesConnectable={false} onNodesChange={changes => setNodes(current => applyNodeChanges(changes, current))}
      onNodeClick={(event, node) => { if (event.shiftKey || (event.target as HTMLElement).closest('select')) return; if (node.data.group) setFocused(null); else { setFocused(node.id); const version = node.data.versions.length === 1 ? node.data.claim : node.data.versions.find(item => item.claim_id === node.data.selectedVersion); if (version) onClaim(version.claim_id); } }}
      onEdgeClick={(_, edge) => onClaim(Number(edge.data?.claimId))}
      onSelectionChange={({ nodes: selection }) => setSelected(current => { const next = selection.map(node => node.id); return next.length === current.length && next.every(id => current.includes(id)) ? current : next; })} selectionOnDrag panOnDrag={[1, 2]} multiSelectionKeyCode="Shift"
      minZoom={0.12} maxZoom={1.5}><Background color="#dedfd8" gap={24} size={1} /><Controls showInteractive={false} /></ReactFlow>
      {!entities.length && <div className="canvas-empty"><Empty title="此筛选下没有研究节点">实体候选完成提取后会出现在这里。</Empty></div>}
    </div>
    <details className="graph-node-picker"><summary>选择过程节点（也可 Shift + 单击画布）</summary><div>{entities.map(item => <label key={graphNodeId(item)}><input type="checkbox" disabled={!!fold} data-node-id={graphNodeId(item)} aria-label={`选择节点 ${projected.versions.get(graphNodeId(item))!.length > 1 ? `多版本对象 ${item.entity_id}` : label(item)} ${scopeText(item.scope)}`} checked={selected.includes(graphNodeId(item))} onChange={event => choose(graphNodeId(item), event.target.checked)} /><span>{projected.versions.get(graphNodeId(item))!.length > 1 ? `多版本对象 · ${item.entity_id}` : label(item)}<small>{scopeText(item.scope)}</small></span></label>)}</div></details>
    <div className="process-controls"><span>已选 {selected.length} 个节点</span><input aria-label="过程组名称" value={groupName} onChange={event => setGroupName(event.target.value)} maxLength={60} /><button className="button secondary" disabled={!!fold} onClick={collapse}>折叠所选（{selected.length}）</button><button className="text-button" disabled={!fold} onClick={() => { setFold(null); setNotice('已在同一修订恢复全部原节点、内部与边界关系、决定、共同输入、证据及节点位置。'); }}>展开过程组</button>{fold && <button className="text-button" onClick={() => { void fitView({ nodes: [{ id: 'view-process-group' }], padding: 0.35, maxZoom: 1, duration: 250 }); }}>聚焦过程组</button>}</div>
    {notice && <p role="status" className="notice graph-notice">{notice}</p>}
    {shownRecords.length > 0 && <section className="graph-retained" aria-label="保留的原始记录"><h3>{fold ? '过程组保留记录' : '对象版本与历史'} · {shownRecords.length}</h3><p className="muted small">撤回、阴性证据、各范围版本和待复核记录逐条保留；审核与采用独立。单击记录可查看原文。</p>{fold && <details open><summary>原关系与边界端口 · {fold.internalEdges.length + fold.boundaryEdges.length}</summary>{[...fold.internalEdges, ...fold.boundaryEdges].map(edge => <div className="graph-original-edge" key={edge.id} data-edge-id={edge.id} data-original-edge={JSON.stringify(edge)}><button className="text-button" onClick={() => onClaim(edge.claimId)}>#{edge.claimId} {edgeText(edge)}</button><span>{edge.sourceEntity ?? edge.source} → {edge.targetEntity ?? edge.target}{edge.targetPort && ` · 端口 ${edge.targetPort}`}</span><small>原关系 {edge.id} · 证据 {edge.evidenceIds?.join('、') || '缺失'}</small></div>)}</details>}{shownRecords.map(item => <article key={item.claim_id} data-claim-id={item.claim_id}><div><button className="text-button" onClick={() => onClaim(item.claim_id)}>#{item.claim_id} {item.replacement_ids.length > 0 && '已被替代 · 历史记录 · '}{recordText(item)}</button><Badge state={item.effective_state} /></div><p>{scopeText(item.scope)} · 依据 {basisText(item.basis)} · {item.evidence.length} 条原证据</p>{item.replacement_ids.length > 0 && <p>替代记录 {item.replacement_ids.map(id => `#${id}`).join('、')}</p>}</article>)}</section>}
    <p className="muted small">单击节点或关系查看原始引用。未选比较对象不表示使用，相同主题仅供检索。此画面是当前接口的记录投影；此版本未提供影响传播。</p>
  </div>;
}
export function GraphView(props: { data: GraphData; onClaim: (id: number) => void }) { return <ReactFlowProvider><Canvas {...props} /></ReactFlowProvider>; }
