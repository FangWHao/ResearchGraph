import { useEffect, useMemo, useState } from 'react';
import { Background, Controls, Handle, Position, ReactFlow, ReactFlowProvider, useReactFlow } from '@xyflow/react';
import { applyNodeChanges } from '@xyflow/react';
import type { Edge, Node, NodeProps } from '@xyflow/react';
import type { ElkNode, ELK } from 'elkjs';
import '@xyflow/react/dist/style.css';
import { Badge, Empty, Icon } from './components';
import { adoption, actionNames, evidenceNames, evidenceState, foldGroup, graphNodeId, joinNames, joinRecords, kindNames, label, projectGraph, relationNames, scopeText } from './model';
import type { FoldResult } from './model';
import type { Claim, GraphData, Kind } from './types';

type ResearchNode = Node<{ claim: Claim; claims: Claim[]; showCandidates: boolean; group?: FoldResult; title?: string }>;
let engine: Promise<ELK> | undefined;
function layoutGraph(graph: ElkNode): Promise<ElkNode> {
  engine ??= import('elkjs/lib/elk.bundled.js').then(module => new module.default());
  return engine.then(elk => elk.layout(graph));
}

function ResearchCard({ data }: NodeProps<ResearchNode>) {
  const claim = data.claim;
  const decision = adoption(data.claims, claim.entity_id!, claim.scope);
  const joins = joinRecords(data.claims, claim.entity_id!, claim.scope, data.showCandidates);
  const join = joins.length === 1 ? joins[0] : null;
  return <div className={`research-node ${data.group ? 'process-node' : ''}`}>
    <Handle type="target" position={Position.Left} />
    <div className="node-top"><span>{data.group ? '手工过程组 · 仅视图' : kindNames[claim.payload.kind as Kind]}</span><Badge state={claim.effective_state} /></div>
    <strong>{data.title ?? label(claim)}</strong>
    <p>{data.group ? `${data.group.members.length} 个节点 · ${data.group.evidenceIds.length} 条原证据` : scopeText(claim.scope)}</p>
    {!data.group && <div className="node-dimensions"><span>采用 {actionNames[decision] ?? (decision === 'unknown_scope' ? '范围未知' : decision === 'time_unknown' ? '时间未知' : decision === 'conflict' ? '有冲突' : '未知')}</span><span>证据 {evidenceNames[evidenceState(data.claims, claim.entity_id!, claim.scope)]}</span><span>运行 未知</span></div>}
    {join && !data.group && <div className="join-semantics">{joinNames[join.payload.semantics!] ?? '汇合语义未知'}<span>{join.effective_state === 'candidate' ? ' · 待复核' : ''}</span><p>{join.payload.inputs?.map(input => `${input.port}: ${input.ref.slice(0, 8)}`).join('；')}</p></div>}
    {joins.length > 1 && !data.group && <div className="join-semantics">汇合记录 {joins.length} 条 · 需逐条核对<p>{joins.map(item => `#${item.claim_id} ${joinNames[item.payload.semantics!] ?? '语义未知'}`).join('；')}</p><span>单击输入关系查看各条原文。</span></div>}
    <Handle type="source" position={Position.Right} />
  </div>;
}
const nodeTypes = { research: ResearchCard };

function Canvas({ data, onClaim }: { data: GraphData; onClaim: (id: number) => void }) {
  const [nodes, setNodes] = useState<ResearchNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [fold, setFold] = useState<FoldResult | null>(null);
  const [notice, setNotice] = useState('');
  const [showCandidates, setShowCandidates] = useState(true);
  const [groupName, setGroupName] = useState('研究过程');
  const { fitView } = useReactFlow();
  const projected = useMemo(() => projectGraph(data.claims, showCandidates), [data.claims, showCandidates]);
  const entities = projected.entities, semanticEdges = projected.edges;
  useEffect(() => { setFold(null); setSelected([]); }, [data.revision, showCandidates]);
  useEffect(() => {
    let cancelled = false;
    const groupId = 'view-process-group';
    const folded = new Set(fold?.members ?? []);
    const projectedEntities = entities.filter(item => !folded.has(graphNodeId(item)));
    const mapped = semanticEdges.filter(edge => !(folded.has(edge.source) && folded.has(edge.target))).map(edge => ({
      ...edge, source: folded.has(edge.source) ? groupId : edge.source, target: folded.has(edge.target) ? groupId : edge.target,
    }));
    const rawNodes: ResearchNode[] = projectedEntities.map(claim => ({ id: graphNodeId(claim), type: 'research', position: { x: 0, y: 0 }, data: { claim, claims: data.claims, showCandidates } }));
    if (fold) {
      const first = entities.find(item => graphNodeId(item) === fold.entry)!;
      rawNodes.push({ id: groupId, type: 'research', position: { x: 0, y: 0 }, data: { claim: first, claims: data.claims, showCandidates, group: fold, title: groupName } });
    }
    const flowEdges: Edge[] = mapped.map(edge => {
      const claim = data.claims.find(item => item.claim_id === edge.claimId)!;
      return { id: edge.id, source: edge.source, target: edge.target, label: relationNames[edge.relation] ?? edge.relation.replace('input:', '输入 '), data: { claimId: edge.claimId }, style: { stroke: '#758078', strokeDasharray: claim.effective_state === 'candidate' ? '5 4' : undefined }, labelStyle: { fontSize: 11 }, labelBgStyle: { fill: '#fbfaf7' } };
    });
    layoutGraph({ id: 'root', layoutOptions: { 'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.spacing.nodeNode': '44', 'elk.layered.spacing.nodeNodeBetweenLayers': '90' }, children: rawNodes.map(node => ({ id: node.id, width: 292, height: node.data.claim.payload.kind === 'join' ? 290 : 220 })), edges: mapped.map(edge => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })) })
      .then(layout => {
        if (cancelled) return;
        setNodes(rawNodes.map(node => { const position = layout.children!.find(item => item.id === node.id)!; return { ...node, position: { x: position.x ?? 0, y: position.y ?? 0 } }; }));
        setEdges(flowEdges);
        requestAnimationFrame(() => { void fitView({ padding: 0.18, maxZoom: 1, duration: 250 }); });
      }).catch(error => { if (!cancelled) { setNotice(`布局失败：${error.message}`); setNodes(rawNodes.map((node, index) => ({ ...node, position: { x: index * 330, y: 80 } }))); setEdges(flowEdges); } });
    return () => { cancelled = true; };
  }, [entities, semanticEdges, fold, groupName, data.claims, fitView, showCandidates]);
  function collapse() {
    try { setFold(foldGroup(selected, semanticEdges, data.claims, !data.partial && showCandidates && projected.unresolvedClaimIds.length === 0)); setNotice('过程组只改变视图，原节点、关系与引用均保留。'); }
    catch (error) { setNotice((error as Error).message); }
  }
  return <div className="graph-workspace">
    <>{projected.unresolvedClaimIds.length > 0 && <p className="notice">{projected.unresolvedClaimIds.length} 条关系缺少同范围端点，保留为未显示记录，当前图不能折叠：{projected.unresolvedClaimIds.map(id => <button className="text-button" key={id} onClick={() => onClaim(id)}>#{id}</button>)}</p>}<div className="graph-tools"><label><input type="checkbox" checked={showCandidates} onChange={event => setShowCandidates(event.target.checked)} />显示待复核记录</label><span>虚线 = 待复核关系 · 颜色只表达审核</span><button className="button secondary" onClick={() => { void fitView({ padding: 0.18, maxZoom: 1, duration: 250 }); }}><Icon name="refresh" size={15} />适配画布</button></div></>
    <div className="graph-canvas" data-testid="research-canvas"><ReactFlow<ResearchNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView nodesDraggable={false} nodesConnectable={false} onNodesChange={changes => setNodes(current => applyNodeChanges(changes, current))}
      onNodeClick={(event, node) => { if (event.shiftKey) return; if (node.data.group) { setNotice(`组内节点：${node.data.group.members.join('、')}；原证据 span IDs：${node.data.group.evidenceIds.join('、') || '缺失'}`); } else onClaim(node.data.claim.claim_id); }}
      onEdgeClick={(_, edge) => onClaim(Number(edge.data?.claimId))}
      onSelectionChange={({ nodes: selection }) => setSelected(selection.map(node => node.id))} selectionOnDrag panOnDrag={[1, 2]} multiSelectionKeyCode="Shift"
      minZoom={0.12} maxZoom={1.5}><Background color="#dedfd8" gap={24} size={1} /><Controls showInteractive={false} /></ReactFlow>
      {!entities.length && <div className="canvas-empty"><Empty title="此筛选下没有研究节点">实体候选完成提取后会出现在这里。</Empty></div>}
    </div>
    <div className="process-controls"><span>Shift + 单击选择过程节点</span><input aria-label="过程组名称" value={groupName} onChange={event => setGroupName(event.target.value)} maxLength={60} /><button className="button secondary" disabled={!!fold} onClick={collapse}>折叠所选（{selected.length}）</button><button className="text-button" disabled={!fold} onClick={() => { setFold(null); setNotice('已展开，全部原节点与证据恢复。'); }}>展开过程组</button></div>
    {notice && <p role="status" className="notice graph-notice">{notice}</p>}
    <p className="muted small">单击节点或关系查看原始引用。共同输入、比较与证据综合按记录区分；此版本未提供影响传播。</p>
  </div>;
}
export function GraphView(props: { data: GraphData; onClaim: (id: number) => void }) { return <ReactFlowProvider><Canvas {...props} /></ReactFlowProvider>; }
