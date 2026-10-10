import { useEffect, useMemo, useRef, useState } from 'react';
import { Background, Controls, Handle, Position, ReactFlow, ReactFlowProvider, applyNodeChanges, useNodesInitialized, useReactFlow } from '@xyflow/react';
import type { Edge, Node, NodeProps } from '@xyflow/react';
import type { ELK } from 'elkjs';
import '@xyflow/react/dist/style.css';
import { DateText, Empty } from './components';
import { scopeText } from './model';
import { drawableL1Edges, l1Collections, l1KindNames, l1NodeTitle, l1RelationNames, l1RoleNames, nativeState, nodeEvidence, object } from './fileRunGraph';
import type { FileRunData, L1Edge, L1Node, L1Record } from './fileRunGraph';
import type { Project } from './types';
import type { useFileRunGraph } from './useFileRunGraph';
import './fileRunGraph.css';

type FileNode = Node<{ value: L1Node }>;
let engine: Promise<ELK> | undefined;
const collectionNames = { nodes: '节点', edges: '关系', observations: '观察', evidence: '原件引用', unresolved: '未解析关系' };
const sourceNames: Record<string, string> = { current_file: '当前文件完整读取', shadow_snapshot: '已保存影子快照', agent_edit: '工具报告的编辑文本' };
const representationNames: Record<string, string> = { physical_file_bytes: '物理文件字节', tool_reported_utf8: '工具报告的 UTF-8 文本', symlink_target_bytes: '符号链接目标文字字节（不是被指向文件内容）' };
const associationNames: Record<string, string> = { reported_only: '仅报告关联', tool_reported: '工具报告关联', discovery_only: '仅发现线索', saved_snapshot: '已保存快照关联' };
const bindingNames: Record<string, string> = { native_request: '明确原生请求', native_request_ambiguous: '原生请求有歧义', native_run_unknown_or_not_visible: '原生运行未知或截止内不可见' };
const basisNames: Record<string, string> = { direct_record: '直接记录', manual: '人工记录', time_match: '时间匹配', model_inference: '模型推断' };
const claimNames: Record<string, string> = { candidate: '待复核候选', confirmed: '已确认记录', dismissed: '已驳回记录' };
function text(value: unknown): string { return value == null ? '未知' : typeof value === 'string' ? value : JSON.stringify(value); }
function status(node: L1Node): string {
  if (node.kind === 'native_run') return nativeState(node.record);
  if (node.kind === 'run_manifest') return `候选报告 · 退出码 ${object(node.record.reported) ? text(node.record.reported.exit_code) : '未知'}`;
  if (node.kind === 'artifact_version') return sourceNames[String(node.record.source)] ?? '版本来源未知';
  if (node.kind === 'attempt') return '全部内容版本保留 · 未选定当前内容';
  return '登记记录 · 不证明实际 I/O 完整';
}
function FileCard({ data }: NodeProps<FileNode>) {
  const node = data.value;
  return <div className={`file-run-node file-run-node-${node.kind}`} data-l1-node-id={node.node_id}>
    <Handle type="target" position={Position.Left} /><span className="eyebrow">{l1KindNames[node.kind] ?? `未知类型 ${node.kind}`}</span>
    <strong>{l1NodeTitle(node).slice(0, 180)}{l1NodeTitle(node).length > 180 && '…'}</strong><p>{status(node)}</p>
    {node.record.reported_exit_conflicts_with_native === true && <p className="file-run-conflict">报告退出码与原生观察冲突</p>}
    <Handle type="source" position={Position.Right} />
  </div>;
}
const nodeTypes = { fileRun: FileCard };
function Canvas({ data, focus, onNode, onEdge }: { data: FileRunData; focus: string | null; onNode: (id: string) => void; onEdge: (id: string) => void }) {
  const [nodes, setNodes] = useState<FileNode[]>([]); const [notice, setNotice] = useState('');
  const container = useRef<HTMLDivElement>(null); const initialized = useNodesInitialized(); const { fitView } = useReactFlow();
  const projected = useMemo(() => drawableL1Edges(data), [data]);
  const edges: Edge[] = useMemo(() => projected.edges.map(edge => ({ id: edge.edge_id, source: edge.source.node_id!, target: edge.target.node_id!,
    label: edge.relation === 'consumes' || edge.relation === 'produces' ? `${l1RelationNames[edge.relation]} · ${l1RoleNames[String(edge.role)] ?? text(edge.role)}` : l1RelationNames[edge.relation] ?? edge.relation,
    style: { stroke: edge.association_state === 'discovery_only' ? '#948670' : '#728777', strokeDasharray: edge.claim_state === 'candidate' || edge.association_state === 'reported_only' || edge.association_state === 'tool_reported' ? '5 4' : edge.association_state === 'discovery_only' ? '2 5' : undefined },
    labelStyle: { fontSize: 10 }, labelBgStyle: { fill: '#fbfaf7' }, data: { original: edge } })), [projected]);
  useEffect(() => {
    let cancelled = false; const originals: FileNode[] = data.nodes.map(value => ({ id: value.node_id, type: 'fileRun', position: { x: 0, y: 0 }, data: { value } }));
    engine ??= import('elkjs/lib/elk.bundled.js').then(module => new module.default());
    engine.then(elk => elk.layout({ id: 'root', layoutOptions: { 'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.spacing.nodeNode': '35', 'elk.layered.spacing.nodeNodeBetweenLayers': '150' },
      children: originals.map(node => ({ id: node.id, width: 284, height: 200 })), edges: projected.edges.map(edge => ({ id: edge.edge_id, sources: [edge.source.node_id!], targets: [edge.target.node_id!] })) }))
      .then(layout => { if (!cancelled) { const positions = new Map(layout.children?.map(item => [item.id, { x: item.x ?? 0, y: item.y ?? 0 }])); setNodes(originals.map(node => ({ ...node, position: positions.get(node.id) ?? node.position }))); setNotice(''); } })
      .catch(error => { if (!cancelled) { setNodes(originals.map((node, index) => ({ ...node, position: { x: index % 4 * 340, y: Math.floor(index / 4) * 235 } }))); setNotice(`布局失败，保留全部节点和关系供查阅：${error instanceof Error ? error.message : '原因未知'}`); } });
    return () => { cancelled = true; };
  }, [data, projected]);
  useEffect(() => {
    if (!initialized || !nodes.length || !container.current) return;
    let frame = 0;
    const fit = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => { void fitView({ padding: 0.15, maxZoom: 0.9 }); }); };
    const observer = new ResizeObserver(fit); observer.observe(container.current); fit();
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [nodes, initialized, fitView]);
  const shown = useMemo(() => nodes.map(node => ({ ...node, selected: node.id === focus })), [nodes, focus]);
  return <><div ref={container} className="file-run-canvas" data-testid="file-run-canvas"><ReactFlow<FileNode> nodes={shown} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.15, maxZoom: 0.9 }} minZoom={0.04} maxZoom={1.5} nodesDraggable={false} nodesConnectable={false} onNodesChange={changes => setNodes(current => applyNodeChanges(changes, current))} onNodeClick={(_, node) => onNode(node.id)} onEdgeClick={(_, edge) => onEdge(edge.id)}><Background gap={25} color="#dedfd8" /><Controls showInteractive={false} /></ReactFlow>{!data.nodes.length && <div className="canvas-empty"><Empty title="此条件下没有登记节点">可以调整范围或双截止；没有记录不等于运行失败。</Empty></div>}</div>{notice && <p className="notice">{notice}</p>}</>;
}
function EvidenceButtons({ ids, onOpen }: { ids: number[]; onOpen: (id: number) => void }) { return <div className="file-run-evidence-buttons">{ids.map(id => <button className="text-button" key={id} onClick={() => onOpen(id)}>打开历史原文 E{id}</button>)}{!ids.length && <p className="muted small">此记录在当前双截止内没有会话原文引用；物理观察和快照元数据仍可核对。</p>}</div>; }
function Observation({ item }: { item: L1Record }) {
  return <article className="file-run-observation" data-testid="l1-observation"><strong>{item.observation_kind === 'execution' ? `原生执行观察 · ${nativeState(item)}` : '物理文件观察'}</strong>
    <p className="small muted">发生 <DateText value={typeof item.occurred_at === 'string' ? item.occurred_at : null} /> · 收录 <DateText value={typeof item.recorded_at === 'string' ? item.recorded_at : null} /></p>
    {item.observation_kind === 'artifact' && <><p>完整读取窗口：{text(item.hash_started_at)} → {text(item.hash_finished_at)}{item.cache_reused === true && ' · 缓存复用原窗口，未重新测量'}</p><p>{object(item.snapshot) ? `已保存捕获快照 #${item.snapshot.snapshot_id}` : '没有已保存捕获快照'} · {object(item.discovery_snapshot) ? `发现线索快照 #${item.discovery_snapshot.snapshot_id}（不证明旧字节）` : '发现快照未知'}</p></>}
    {Array.isArray(item.provenance_warnings) && item.provenance_warnings.map((warning, index) => <p key={index} className="notice small">{text(warning)}</p>)}
    <details><summary>完整观察元数据</summary><pre>{JSON.stringify(item, null, 2)}</pre></details>
  </article>;
}
function NodeDetail({ node, data, onEvidence }: { node: L1Node; data: FileRunData; onEvidence: (id: number) => void }) {
  const r = node.record; const reported = object(r.reported) ? r.reported : {}; const observations = data.observations.filter(item => item.owner_node_id === node.node_id);
  const versions = Array.isArray(r.versions) ? r.versions.filter(object) : [];
  return <section className="file-run-detail" aria-label="文件运行节点详情"><span className="eyebrow">{l1KindNames[node.kind] ?? '未知记录类型'}</span><h2>{l1NodeTitle(node)}</h2><p>{status(node)} · {scopeText(node.scope)}</p>
    <p className="small muted">原节点 ID <span className="mono">{node.node_id}</span></p>
    {node.kind === 'native_run' && <><p>此状态由完整的原生执行观察计算，报告退出码不会覆盖它。没有结束记录不等于失败。</p><pre aria-label="历史命令，只读">{text(r.command)}</pre>{r.command_truncated === true && <p className="notice">当前仅为命令预览（完整 {text(r.command_total_bytes)} 字节），完整命令见请求原文。</p>}</>}
    {node.kind === 'run_manifest' && <><p>报告审核：{claimNames[String(r.claim_state)] ?? '审核状态未知'} · 依据：{basisNames[String(r.basis)] ?? text(r.basis)} · {bindingNames[String(r.binding_state)] ?? '运行绑定未知'}</p><p>实际 I/O 完整性未知；报告关联不等于实际读写。</p>{r.reported_exit_conflicts_with_native === true && <p className="notice file-run-conflict">报告退出码 {text(reported.exit_code)} 与原生退出观察冲突，两者分别保留。</p>}
      <div className="file-run-roles">{Object.entries(l1RoleNames).filter(([key]) => !key.endsWith('_version')).map(([key, name]) => <details key={key} data-l1-role={key}><summary>{name}版本：{reported[key] == null ? '未报告（未知）' : Array.isArray(reported[key]) ? reported[key].length ? `明确报告 ${reported[key].length} 项` : '明确报告空列表' : '报告格式未知'}</summary>{Array.isArray(reported[key]) && <ol>{reported[key].map((id, index) => <li key={index}>{text(id)}</li>)}</ol>}</details>)}</div>
      <p>报告开始：{text(reported.started_at)} · 报告结束：{text(reported.ended_at)} · 报告退出码：{text(reported.exit_code)}</p><p>种子（十进制原文）：<span className="mono">{text(reported.seed)}</span></p><details><summary>报告参数（只读文字）</summary><pre>{JSON.stringify(reported.parameters ?? null, null, 2)}</pre></details>
      {Array.isArray(r.unknown_fields) && r.unknown_fields.length > 0 && <p className="notice small">未报告字段：{r.unknown_fields.map(key => l1RoleNames[String(key)] ?? ({ scope: '范围', parameters: '参数', seed: '种子', started_at: '开始时间', ended_at: '结束时间', exit_code: '退出码', attempt_id: '尝试', snapshot_id: '快照', occurred_at: '发生时间' } as Record<string, string>)[String(key)] ?? String(key)).join('、')}</p>}
    </>}
    {node.kind === 'artifact_version' && <><p>{representationNames[String(r.representation)] ?? '字节表示未知'} · 算法 {text(r.algo)} · 大小 {text(r.size)} 字节</p><p>版本 ID：{text(r.version_id)}</p><p>完整摘要：<span className="mono">{text(r.digest)}</span></p><p>内容版本身份不等于研究结论成立，也不证明运行实际读写了这个文件。</p></>}
    {node.kind === 'workspace_snapshot' && <p>快照只证明已登记的捕获信息；跳过、竞态或缺提交均需核对，不证明运行实际输入输出。</p>}
    {node.kind === 'edit_record' && <p>工具报告的编辑记录与版本分别保留。补丁不等于完整编辑前后字节；不能读取当前文件补齐旧记录。</p>}
    {node.kind === 'attempt' && <><p>保留全部 {versions.length} 个内容版本，未按编号或入库时间选择当前事实。</p>{versions.map((version, index) => <article className="file-run-version" key={String(version.claim_id ?? index)}><strong>内容记录 #{text(version.claim_id)} · {claimNames[String(version.effective_state)] ?? '审核状态未知'}{version.replaced === true && ' · 已被替代的历史'}</strong><pre>{JSON.stringify(version, null, 2)}</pre></article>)}</>}
    <EvidenceButtons ids={nodeEvidence(data, node)} onOpen={onEvidence} />
    <h3>独立观察 · {observations.length}</h3>{observations.map(item => <Observation key={String(item.observation_id)} item={item} />)}{!observations.length && <p className="small muted">当前双截止内没有独立观察；不是实际 I/O 已完整记录的证明。</p>}
    <details><summary>完整节点登记元数据</summary><pre>{JSON.stringify(node, null, 2)}</pre></details>
  </section>;
}
function EdgeDetail({ edge, onEvidence }: { edge: L1Edge; onEvidence: (id: number) => void }) {
  return <section className="file-run-detail" aria-label="文件运行关系详情"><h2>{l1RelationNames[edge.relation] ?? edge.relation}{edge.role != null && ` · ${l1RoleNames[String(edge.role)] ?? text(edge.role)}`}</h2><p>{associationNames[String(edge.association_state)] ?? '关联依据未知'} · {basisNames[String(edge.basis)] ?? text(edge.basis)} · {claimNames[String(edge.claim_state)] ?? '无研究审核声明'}</p><p>源：{l1KindNames[edge.source.kind] ?? edge.source.kind} · {text(edge.source.record_id)}{!edge.source.resolved && '（未知或截止内不可见）'}</p><p>目标：{l1KindNames[edge.target.kind] ?? edge.target.kind} · {text(edge.target.record_id)}{!edge.target.resolved && '（未知或截止内不可见）'}</p><p>输入输出声明属于报告或编辑记录，未成为原生运行的实际 I/O 事实。</p><EvidenceButtons ids={edge.evidence_event_ids} onOpen={onEvidence} /><pre>{JSON.stringify(edge, null, 2)}</pre></section>;
}
export function FileRunGraphView({ project, projects, onProject, workspace: w }: { project: Project | undefined; projects: Project[]; onProject: (id: string) => void; workspace: ReturnType<typeof useFileRunGraph> }) {
  const [focused, setFocused] = useState<{ type: 'node' | 'edge'; id: string } | null>(null);
  const data = w.data; const projection = useMemo(() => data ? drawableL1Edges(data) : null, [data]);
  useEffect(() => { setFocused(null); }, [project?.project_id]);
  useEffect(() => { if (w.event != null) requestAnimationFrame(() => document.querySelector('.file-run-source')?.scrollIntoView({ behavior: 'smooth', block: 'start' })); }, [w.event]);
  const selectedNode = focused?.type === 'node' ? data?.nodes.find(node => node.node_id === focused.id) : undefined;
  const selectedEdge = focused?.type === 'edge' ? data?.edges.find(edge => edge.edge_id === focused.id) : undefined;
  function select(type: 'node' | 'edge', id: string) { setFocused({ type, id }); requestAnimationFrame(() => document.querySelector('.file-run-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' })); }
  return <div className="file-run-workspace">
    <form className="file-run-filters" onSubmit={event => { event.preventDefault(); void w.load(); }}><h2>文件版本与运行记录</h2><p>本地读取全部登记页，无需先人工复核。命令和参数仅供阅读，页面不会执行。</p>
      <label>项目<select aria-label="文件运行图项目" value={project?.project_id ?? ''} onChange={event => onProject(event.target.value)}>{projects.map(item => <option key={item.project_id} value={item.project_id}>{item.name}</option>)}</select></label>
      <div className="file-run-filter-grid"><label>发生截止（含时区）<input aria-label="文件运行图发生截止" value={w.draft.occurredUntil} onChange={event => w.change({ ...w.draft, occurredUntil: event.target.value })} placeholder="2026-10-09T09:01:00+00:00" /></label><label>获知截止（含时区）<input aria-label="文件运行图获知截止" value={w.draft.knownUntil} onChange={event => w.change({ ...w.draft, knownUntil: event.target.value })} placeholder="留空：当前已知" /></label></div>
      <label>阅读范围<select aria-label="文件运行图范围" value={w.draft.scopeMode} onChange={event => w.change({ ...w.draft, scopeMode: event.target.value as 'all' | 'exact' | 'unknown' })}><option value="all">所有范围（不猜文件归属）</option><option value="exact">完整范围精确筛选</option><option value="unknown">明确未知范围的报告</option></select></label>{w.draft.scopeMode === 'exact' && <label>完整范围<textarea aria-label="文件运行图完整范围" value={w.draft.scopeText} onChange={event => w.change({ ...w.draft, scopeText: event.target.value })} rows={2} placeholder="每行一个 字段=值" /></label>}
      <p className="small muted">范围筛选按报告明确关联；不把文件路径、运行或快照推定为某个研究范围。双截止留空时固定首次读取时刻，所有后续页沿用。</p>
      <footer>{w.busy ? <button type="button" className="button secondary" onClick={w.cancel}>停止读取等待</button> : <button className="button primary" type="submit">重新读取文件运行图</button>}</footer>
    </form>
    {w.error && <div className="error-message" role="alert">{w.error}<p>{w.conflict ? '修订已变化。旧图和已有证据窗口保留；请主动重新读取，不会自动混页或重试。' : '读取未完成，已展示的旧图或部分记录保留，不能据此判断完整登记图。'}</p></div>}
    {w.info && <p className="notice" role="status">{w.info}</p>}
    {w.busy && <p className="notice" role="status">正在逐页读取：{l1Collections.map(key => `${collectionNames[key]} ${w.progress[key] ?? 0}`).join(' · ')}。已有图仍为此前读取的视图。</p>}
    {w.stale && <p className="notice">阅读条件已改变，下方保留此前条件的图；主动重新读取后才更新。</p>}
    {data && <><section className="file-run-summary" aria-label="文件运行图完整性"><h3>{data.loadedComplete && data.l1_graph_complete ? '已读完登记图' : '仅有部分登记图，仍有读取缺口'}</h3><p>图修订 {data.revision} · {data.scope_filter ? `筛选范围：${scopeText(data.scope)}` : '所有范围（未限定）'}</p><p>发生截止 <DateText value={data.occurred_until} /> · 获知截止 <DateText value={data.known_until} /></p><p>{l1Collections.map(key => `${collectionNames[key]} ${data[key].length}/${data.counts[key]}`).join(' · ')}</p><p className="notice">完整遍历登记记录不等于实际运行依赖完整。实际 I/O 完整性未知，候选报告不替代原生运行事实。</p>{data.unknown_recorded_time_excluded ? <p className="notice">{data.unknown_recorded_time_excluded} 条记录的获知时间未知，无法纳入该历史视图。</p> : null}</section>
      <ReactFlowProvider><Canvas data={data} focus={selectedNode?.node_id ?? null} onNode={id => select('node', id)} onEdge={id => select('edge', id)} /></ReactFlowProvider>
      <p className="small muted">虚线：候选报告或工具报告关联；点线：发现线索。每条登记关系保留原 ID，未解析端点不会补造真实节点。</p>
      <details className="file-run-picker" open><summary>按名称打开节点 · {data.nodes.length}</summary><div aria-label="全部文件运行节点，可滚动" tabIndex={0}>{data.nodes.map(node => <button className={`file-run-node-choice ${selectedNode?.node_id === node.node_id ? 'active' : ''}`} data-node-id={node.node_id} key={node.node_id} onClick={() => select('node', node.node_id)}><span>{l1KindNames[node.kind] ?? node.kind} · {l1NodeTitle(node)}</span><small>{status(node)}</small></button>)}</div></details>
      <details className="file-run-picker"><summary>全部原关系 · {data.edges.length}</summary><div tabIndex={0} aria-label="全部文件运行关系，可滚动">{data.edges.map(edge => <button key={edge.edge_id} data-edge-id={edge.edge_id} className="file-run-node-choice" onClick={() => select('edge', edge.edge_id)}><span>{l1RelationNames[edge.relation] ?? edge.relation}{edge.role != null && ` · ${l1RoleNames[String(edge.role)] ?? text(edge.role)}`} · {text(edge.source.record_id)} → {text(edge.target.record_id)}</span><small>{associationNames[String(edge.association_state)] ?? '关联依据未知'} · {edge.resolved ? '端点已解析' : '端点未知或截止外'}</small></button>)}</div></details>
      {(data.unresolved.length > 0 || projection!.omitted.length > 0) && <section className="file-run-unresolved" aria-label="文件运行图未解析诊断"><h3>保留未解析关系与绘图诊断</h3><p>{data.unresolved.length} 条服务端未解析记录 · {projection!.omitted.length} 条关系的端点缺失、截止内不可见或类型不符，未补造实际 I/O。详情与原证据仍可打开。</p>{projection!.omitted.map(edge => <button className="text-button" key={edge.edge_id} onClick={() => select('edge', edge.edge_id)}>{l1RelationNames[edge.relation] ?? edge.relation} · {text(edge.source.record_id)} → {text(edge.target.record_id)}</button>)}<details><summary>完整诊断记录</summary><pre>{JSON.stringify(data.unresolved, null, 2)}</pre></details></section>}
      {selectedNode && <NodeDetail node={selectedNode} data={data} onEvidence={id => { void w.evidence(id); }} />}{selectedEdge && <EdgeDetail edge={selectedEdge} onEvidence={id => { void w.evidence(id); }} />}
      <details className="file-run-picker"><summary>全部原件引用 · {data.evidence.length}</summary><div>{data.evidence.map(item => <button className="text-button" key={String(item.event_id)} onClick={() => { void w.evidence(Number(item.event_id)); }}>打开历史原文 E{String(item.event_id)}</button>)}</div></details>
    </>}
    {w.event != null && <section className="file-run-source" role="dialog" aria-modal="false" aria-label="文件运行图历史原文"><header><h2>历史原文 E{w.event}</h2><button className="button secondary" onClick={w.closeEvidence}>关闭历史原文</button></header><p className="small muted">仅此图的固定双截止和修订，原始 JSON 字节窗口保留转义；没有加入当前时刻的其它派生观察。</p>{w.windows.map(window => <article key={window.window_start}><p className="small muted">原件字节 {window.window_start}–{window.window_end} / {window.total_bytes} · 来源文件字节 {text(window.source_byte_start)}–{text(window.source_byte_end)}</p><pre data-testid="l1-history-text">{window.text}</pre><p className="small muted">窗口 SHA256 <span className="mono">{window.window_sha256}</span></p></article>)}{w.evidenceError && <p className="error-message" role="alert">{w.evidenceError}</p>}{w.evidenceBusy && <p role="status">正在读取历史原文窗口…</p>}{w.windows.at(-1)?.next_byte_offset != null && <button className="button secondary" disabled={w.evidenceBusy || w.conflict} onClick={() => { void w.evidence(w.event!, true); }}>读取后续原文字节</button>}{w.conflict && <button className="button secondary" onClick={() => { void w.load(); }}>重新读取图后核对原文</button>}</section>}
  </div>;
}
