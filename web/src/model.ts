import type { Claim, Kind, ReviewState } from './types';
import { claimInstant, compareInstants } from './exactTime';

export const kindNames: Record<Kind, string> = {
  question: '问题', approach: '方案', attempt: '尝试', finding: '发现', decision: '决定', join: '汇合',
};
export const reviewNames: Record<ReviewState, string> = { candidate: '待复核', confirmed: '已确认', dismissed: '已驳回' };
export const actionNames: Record<string, string> = {
  proposed: '提出', accepted: '采用', deferred: '暂缓', rejected: '拒绝', withdrawn: '撤回', superseded: '被替代',
};
export const relationNames: Record<string, string> = {
  part_of: '属于', supports: '支持', challenges: '质疑', supersedes: '替代', selects: '选择',
  same_topic: '相同主题（仅检索）', merge: '人工合并',
};
export const evidenceNames: Record<string, string> = {
  unassessed: '未评估', supported: '受支持', contested: '有争议', refuted: '被否定',
  insufficient: '不足', needs_review: '需复核',
};
export const evidenceStateNames: Record<string, string> = { ...evidenceNames, time_unknown: '发生时间未知', conflict: '记录冲突' };
export const joinNames: Record<string, string> = {
  all_required: '共同输入：所有输入都必需', compare_then_select: '比较后选择', evidence_synthesis: '证据综合',
};

export function scopeKey(scope: Record<string, string> | null): string {
  return scope ? JSON.stringify(Object.entries(scope).sort(([a], [b]) => a.localeCompare(b))) : 'unknown';
}
export function scopeText(scope: Record<string, string> | null): string {
  if (!scope || !Object.keys(scope).length) return '范围未知';
  const fields = Object.entries(scope).map(([key, value]) => `${key}=${value}`).join(' · ');
  return knownScope(scope) ? fields : `范围未知 · ${fields}`;
}
export function knownScope(scope: Record<string, string> | null): boolean {
  const unknown = new Set(['unknown', '未知', '未确定', '不详', 'unspecified', '?']);
  return scope != null && Object.keys(scope).length > 0
    && Object.values(scope).every(value => value.trim() !== '' && !unknown.has(value.trim().toLowerCase()));
}
export function label(claim: Claim): string {
  return claim.payload.label ?? claim.payload.reason ?? relationNames[claim.payload.relation ?? ''] ?? claim.claim_type;
}
function activeClaim(claim: Claim): boolean { return claim.effective_state !== 'dismissed' && claim.replacement_ids.length === 0 && claim.replaced !== true; }
export function entityVersions(claims: Claim[]): Claim[] {
  const candidates = new Map<string, Claim[]>();
  for (const claim of claims) {
    if (claim.claim_type !== 'entity_version' || !activeClaim(claim) || !claim.entity_id) continue;
    const identity = `${claim.entity_id}:${scopeKey(claim.scope)}`;
    const values = candidates.get(identity) ?? [];
    values.push(claim);
    candidates.set(identity, values);
  }
  return Array.from(candidates.values()).map(values => {
    const confirmed = values.filter(item => item.effective_state === 'confirmed');
    return (confirmed.length ? confirmed : values).at(-1)!;
  });
}
export function timeline(claims: Claim[], entityId: string): Claim[] {
  return claims.filter(item => item.claim_type === 'decision_event' && item.payload.target === entityId)
    .sort((a, b) => compareInstants(eventOrder(a), eventOrder(b)) || a.claim_id - b.claim_id);
}
function eventOrder(claim: Claim): bigint { return claimInstant(claim, 'occurred') ?? claimInstant(claim, 'recorded') ?? 0n; }
function eventState(events: Claim[], field: 'action' | 'state', unknown: string): string {
  const stamped = events.filter(item => item.effective_state === 'confirmed' && activeClaim(item)).map(item => ({ item, time: claimInstant(item, 'occurred') }));
  if (!stamped.length) return unknown;
  if (stamped.some(event => event.time == null)) return 'time_unknown';
  const latest = stamped.reduce((max, event) => event.time! > max ? event.time! : max, stamped[0].time!);
  const states = new Set(stamped.filter(event => event.time === latest).map(event => event.item.payload[field] ?? unknown));
  return states.size === 1 ? [...states][0] : 'conflict';
}
export function adoption(claims: Claim[], entityId: string, scope: Record<string, string> | null): string {
  if (!knownScope(scope)) return 'unknown_scope';
  return eventState(claims.filter(item => item.claim_type === 'decision_event' && item.payload.target === entityId && scopeKey(item.scope) === scopeKey(scope)), 'action', 'unknown');
}
export function evidenceState(claims: Claim[], entityId: string, scope: Record<string, string> | null): string {
  if (!knownScope(scope)) return 'needs_review';
  const events = claims.filter(item => item.claim_type === 'evidence_event' && item.payload.target === entityId
    && scopeKey(item.scope) === scopeKey(scope));
  return eventState(events, 'state', 'unassessed');
}
export function graphNodeId(claim: Claim): string { return `${claim.entity_id}::${scopeKey(claim.scope)}`; }
export function relatedChildren(claims: Claim[], parent: string, scope: Claim['scope'], includeCandidates = true): string[] {
  return Array.from(new Set(claims.filter(item => item.claim_type === 'relation'
    && item.payload.relation === 'part_of' && item.payload.target === parent
    && scopeKey(item.scope) === scopeKey(scope)
    && activeClaim(item) && (includeCandidates || item.effective_state === 'confirmed'))
    .map(item => item.payload.source!).filter(Boolean)));
}
export function groupQueue(claims: Claim[]): { key: string; title: string; claims: Claim[] }[] {
  const groups = new Map<string, { key: string; title: string; claims: Claim[] }>();
  for (const claim of claims) {
    const first = claim.groups[0];
    const key = first ? `${first.session_pk}:${first.segment_id ?? 'unknown'}` : 'unknown';
    const group = groups.get(key) ?? {
      key, title: first ? `会话 ${first.session_pk} · 片段 ${first.segment_id ?? '未知'}` : '会话与片段未知', claims: [],
    };
    group.claims.push(claim);
    groups.set(key, group);
  }
  return Array.from(groups.values());
}

export interface SemanticEdge {
  id: string; source: string; target: string; claimId: number; relation: string;
  sourceEntity?: string; targetEntity?: string; targetPort?: string;
  joinSemantics?: string; role?: 'required_input' | 'selected_input' | 'compared_input' | 'synthesis_input' | 'unknown_input';
  semantic?: boolean; evidenceIds?: number[];
  diagnostic?: string;
}
export interface FoldResult {
  members: string[]; entry: string; exit: string; evidenceIds: number[]; claimIds: number[];
  internalEdges: SemanticEdge[]; boundaryEdges: SemanticEdge[];
}
export function edgeText(edge: SemanticEdge): string {
  if (edge.diagnostic) return `异常记录 · ${edge.diagnostic}`;
  const ports: Record<string, string> = { required_input: '必需输入', selected_input: '被选输入', compared_input: '仅比较（未选）', synthesis_input: '综合证据输入', unknown_input: '输入语义未知' };
  return edge.role ? `${ports[edge.role]} · ${edge.targetPort}` : relationNames[edge.relation] ?? edge.relation;
}
export function isSemanticEdge(edge: SemanticEdge): boolean { return edge.semantic !== false && edge.relation !== 'same_topic' && !edge.diagnostic; }

export function joinRecords(claims: Claim[], target: string, scope: Claim['scope'], includeCandidates = true): Claim[] {
  return claims.filter(claim => claim.claim_type === 'join_ports'
    && claim.payload.target === target && scopeKey(claim.scope) === scopeKey(scope)
    && activeClaim(claim)
    && (includeCandidates || claim.effective_state === 'confirmed'));
}

export function graphKind(records: Claim[]): string | null {
  const registered = new Set(records.flatMap(record => record.kind === undefined ? [] : [record.kind]));
  const declared = new Set(records.flatMap(record => record.payload.kind == null ? [] : [record.payload.kind]));
  const values = registered.size ? registered : declared;
  const kind = [...values][0];
  return values.size === 1 && typeof kind === 'string' && Object.hasOwn(kindNames, kind) ? kind : null;
}

export function projectGraph(claims: Claim[], showCandidates = true): { entities: Claim[]; versions: Map<string, Claim[]>; edges: SemanticEdge[]; unresolvedClaimIds: number[]; diagnostics: { claimId: number; reasons: string[] }[] } {
  const versions = new Map<string, Claim[]>();
  for (const claim of claims) {
    if (claim.claim_type !== 'entity_version' || !claim.entity_id || !activeClaim(claim) || (!showCandidates && claim.effective_state !== 'confirmed')) continue;
    const id = graphNodeId(claim);
    versions.set(id, [...(versions.get(id) ?? []), claim]);
  }
  // This representative supplies object identity only. Multiple content versions remain unselected in the graph UI.
  const entities = [...versions.values()].map(values => values[0]);
  const diagnostics = new Map<number, Set<string>>();
  function diagnose(id: number, reason: string) { diagnostics.set(id, new Set([...(diagnostics.get(id) ?? []), reason])); }
  const kinds = new Map<string, string | null>();
  for (const [id, records] of versions) {
    const kind = graphKind(records);
    kinds.set(id, kind);
    if (!kind || records.some(record => record.payload.kind != null && record.payload.kind !== kind))
      for (const record of records) diagnose(record.claim_id, '登记对象种类与内容版本未知或矛盾，不按内容版本顺序推定种类');
  }
  function resolve(entityId: string, scope: Claim['scope']): string | undefined {
    const exact = entities.find(item => item.entity_id === entityId && scopeKey(item.scope) === scopeKey(scope));
    return exact ? graphNodeId(exact) : undefined;
  }
  const edges: SemanticEdge[] = [];
  const validPairs: Record<string, string[]> = { part_of: ['attempt:approach', 'approach:question'],
    supports: ['finding:question', 'finding:finding'], challenges: ['finding:question', 'finding:finding'],
    selects: ['join:approach', 'join:attempt', 'join:finding'] };
  for (const claim of claims) {
    if (!activeClaim(claim) || (!showCandidates && claim.effective_state !== 'confirmed')) continue;
    const target = resolve(claim.payload.target ?? '', claim.scope);
    if (claim.claim_type === 'relation' || claim.claim_type === 'merge') {
      const source = resolve(claim.payload.source ?? '', claim.scope);
      const relation = claim.claim_type === 'merge' ? 'merge' : claim.payload.relation ?? '';
      if (!source || !target) diagnose(claim.claim_id, '关系缺少同范围端点');
      else {
        const sourceKind = kinds.get(source), targetKind = kinds.get(target);
        const valid = validPairs[relation] ? validPairs[relation].includes(`${sourceKind}:${targetKind}`)
          : relation === 'supersedes' || relation === 'merge' ? !!sourceKind && sourceKind === targetKind : relation === 'same_topic';
        if (!valid) diagnose(claim.claim_id, '关系方向与对象种类不符，或关系类型未知');
        edges.push({ id: `claim-${claim.claim_id}`, source, target, sourceEntity: claim.payload.source,
          targetEntity: claim.payload.target!, claimId: claim.claim_id, relation, semantic: valid && relation !== 'same_topic',
          ...(!valid ? { diagnostic: '关系方向与对象种类不符，或关系类型未知' } : {}), evidenceIds: claim.evidence.map(span => span.span_id) });
      }
    }
    if (claim.claim_type === 'join_ports') {
      const semantics = claim.payload.semantics;
      const inputs = claim.payload.inputs ?? [];
      if (!target) diagnose(claim.claim_id, '汇合缺少同范围所属对象');
      else if (kinds.get(target) !== 'join') diagnose(claim.claim_id, '端口声明的所属对象不是汇合');
      if (!['all_required', 'compare_then_select', 'evidence_synthesis'].includes(semantics ?? '')) diagnose(claim.claim_id, '汇合语义未知');
      if (inputs.length < 2) diagnose(claim.claim_id, '汇合必须有至少两个输入');
      if (inputs.some(input => !input.port.trim()) || new Set(inputs.map(input => input.port)).size !== inputs.length) diagnose(claim.claim_id, '输入端口缺失或重复');
      if (semantics === 'compare_then_select' ? !inputs.some(input => input.ref === claim.payload.selected) : claim.payload.selected != null) diagnose(claim.claim_id, semantics === 'compare_then_select' ? '被选对象不在比较输入中' : '非比较汇合不允许声明被选对象');
      if (inputs.some(input => !resolve(input.ref, claim.scope))) diagnose(claim.claim_id, '输入缺少同范围端点');
      for (const [index, input] of (claim.payload.inputs ?? []).entries()) {
        const source = resolve(input.ref, claim.scope);
        const role = semantics === 'all_required' ? 'required_input' : semantics === 'evidence_synthesis' ? 'synthesis_input' : semantics === 'compare_then_select' ? input.ref === claim.payload.selected ? 'selected_input' : 'compared_input' : 'unknown_input';
        const invalid = diagnostics.get(claim.claim_id);
        if (source && target) edges.push({ id: `claim-${claim.claim_id}-${index}-${input.port}`, source, target,
          sourceEntity: input.ref, targetEntity: claim.payload.target!, targetPort: input.port, joinSemantics: semantics, role,
          semantic: !invalid && role !== 'compared_input' && role !== 'unknown_input',
          ...(invalid ? { diagnostic: [...invalid].join('；') } : {}), claimId: claim.claim_id,
          relation: `input:${input.port}`, evidenceIds: claim.evidence.map(span => span.span_id) });
      }
    }
  }
  return { entities, versions, edges, unresolvedClaimIds: [...diagnostics.keys()],
    diagnostics: [...diagnostics].map(([claimId, reasons]) => ({ claimId, reasons: [...reasons] })) };
}

export function foldGroup(selected: string[], edges: SemanticEdge[], claims: Claim[], complete = true): FoldResult {
  if (!complete || edges.some(edge => edge.diagnostic)) throw new Error('需要完整研究图才能验证单入口单出口，部分记录未显示或关系异常时不能折叠');
  const members = new Set(selected);
  if (members.size < 2) throw new Error('过程组至少选择两个节点');
  if ([...members].some(id => !claims.some(claim => claim.claim_type === 'entity_version' && graphNodeId(claim) === id))
    || edges.some(edge => !claims.some(claim => claim.claim_id === edge.claimId))) throw new Error('节点或关系原记录缺失，不能折叠');
  const semantic = edges.filter(isSemanticEdge);
  const incoming = semantic.filter(edge => !members.has(edge.source) && members.has(edge.target));
  const outgoing = semantic.filter(edge => members.has(edge.source) && !members.has(edge.target));
  if (incoming.length !== 1 || outgoing.length !== 1) throw new Error('只允许单入口单出口：需恰好一条外部输入和一条外部输出');
  const internal = semantic.filter(edge => members.has(edge.source) && members.has(edge.target));
  const reached = new Set([incoming[0].target]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const edge of internal) if (reached.has(edge.source) && !reached.has(edge.target)) { reached.add(edge.target); grew = true; }
  }
  if ([...members].some(id => !reached.has(id)) || !reached.has(outgoing[0].source)) throw new Error('过程组必须从入口连通到出口，不允许孤立分支');
  const reachesExit = new Set([outgoing[0].source]);
  grew = true;
  while (grew) { grew = false; for (const edge of internal) if (reachesExit.has(edge.target) && !reachesExit.has(edge.source)) { reachesExit.add(edge.source); grew = true; } }
  if ([...members].some(id => !reachesExit.has(id))) throw new Error('过程组所有节点都必须连通到同一出口');
  const internalEdges = edges.filter(edge => members.has(edge.source) && members.has(edge.target));
  const boundaryEdges = edges.filter(edge => members.has(edge.source) !== members.has(edge.target));
  const edgeIds = new Set([...internalEdges, ...boundaryEdges].map(edge => edge.claimId));
  const entityIds = new Set(claims.filter(claim => claim.claim_type === 'entity_version' && members.has(graphNodeId(claim))).map(claim => claim.entity_id));
  const groupClaims = claims.filter(claim => entityIds.has(claim.entity_id) || entityIds.has(claim.payload.target ?? null)
    || (claim.claim_type === 'relation' || claim.claim_type === 'merge') && entityIds.has(claim.payload.source ?? null)
    || claim.claim_type === 'join_ports' && claim.payload.inputs?.some(input => entityIds.has(input.ref))
    || edgeIds.has(claim.claim_id));
  return {
    members: [...members], entry: incoming[0].target, exit: outgoing[0].source,
    claimIds: groupClaims.map(claim => claim.claim_id), internalEdges, boundaryEdges,
    evidenceIds: [...new Set(groupClaims.flatMap(claim => claim.evidence.map(span => span.span_id)))].sort((a, b) => a - b),
  };
}

export function foldProjection(edges: SemanticEdge[], fold: FoldResult | null): (SemanticEdge & { sourceHandle?: string; targetHandle?: string })[] {
  if (!fold) return edges.map(edge => ({ ...edge }));
  const members = new Set(fold.members);
  return edges.filter(edge => !(members.has(edge.source) && members.has(edge.target))).map(edge => ({
    ...edge, source: members.has(edge.source) ? 'view-process-group' : edge.source, target: members.has(edge.target) ? 'view-process-group' : edge.target,
    sourceHandle: members.has(edge.source) ? `out-${edge.id}` : undefined,
    targetHandle: members.has(edge.target) ? `in-${edge.id}` : undefined,
  }));
}
