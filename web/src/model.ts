import type { Claim, Kind, ReviewState } from './types';

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
export const joinNames: Record<string, string> = {
  all_required: '共同输入：所有输入都必需', compare_then_select: '比较后选择', evidence_synthesis: '证据综合',
};

export function scopeKey(scope: Record<string, string> | null): string {
  return scope ? JSON.stringify(Object.entries(scope).sort(([a], [b]) => a.localeCompare(b))) : 'unknown';
}
export function scopeText(scope: Record<string, string> | null): string {
  return scope && Object.keys(scope).length ? Object.entries(scope).map(([key, value]) => `${key}=${value}`).join(' · ') : '范围未知';
}
export function label(claim: Claim): string {
  return claim.payload.label ?? claim.payload.reason ?? relationNames[claim.payload.relation ?? ''] ?? claim.claim_type;
}
export function entityVersions(claims: Claim[]): Claim[] {
  const candidates = new Map<string, Claim[]>();
  for (const claim of claims) {
    if (claim.claim_type !== 'entity_version' || claim.effective_state === 'dismissed' || !claim.entity_id) continue;
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
    .sort((a, b) => eventOrder(a) - eventOrder(b) || a.claim_id - b.claim_id);
}
function occurredTime(claim: Claim): number | null {
  const result = claim.occurred_at ? Date.parse(claim.occurred_at) : NaN;
  return Number.isFinite(result) ? result : null;
}
function eventOrder(claim: Claim): number { return occurredTime(claim) ?? (Date.parse(claim.recorded_at) || 0); }
export function adoption(claims: Claim[], entityId: string, scope: Record<string, string> | null): string {
  const events = timeline(claims, entityId).filter(item => item.effective_state === 'confirmed' && scopeKey(item.scope) === scopeKey(scope));
  if (!events.length) return 'unknown';
  if (events.some(item => occurredTime(item) == null)) return 'time_unknown';
  const latest = events.at(-1)!;
  const atLatest = events.filter(item => occurredTime(item) === occurredTime(latest));
  if (new Set(atLatest.map(item => item.payload.action)).size > 1) return 'conflict';
  return latest.payload.action ?? 'unknown';
}
export function evidenceState(claims: Claim[], entityId: string, scope: Record<string, string> | null): string {
  const events = claims.filter(item => item.claim_type === 'evidence_event' && item.payload.target === entityId
    && item.effective_state === 'confirmed' && scopeKey(item.scope) === scopeKey(scope));
  if (!events.length) return 'unassessed';
  if (events.length > 1 && events.some(item => occurredTime(item) == null)) return 'needs_review';
  const latest = events.sort((a, b) => eventOrder(a) - eventOrder(b)).at(-1)!;
  if (new Set(events.filter(item => occurredTime(item) === occurredTime(latest)).map(item => item.payload.state)).size > 1) return 'needs_review';
  return latest.payload.state ?? 'unassessed';
}
export function graphNodeId(claim: Claim): string { return `${claim.entity_id}::${scopeKey(claim.scope)}`; }
export function relatedChildren(claims: Claim[], parent: string, scope: Claim['scope'], includeCandidates = true): string[] {
  return Array.from(new Set(claims.filter(item => item.claim_type === 'relation'
    && item.payload.relation === 'part_of' && item.payload.target === parent
    && scopeKey(item.scope) === scopeKey(scope)
    && item.effective_state !== 'dismissed' && (includeCandidates || item.effective_state === 'confirmed'))
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

export interface SemanticEdge { id: string; source: string; target: string; claimId: number; relation: string }
export interface FoldResult { members: string[]; entry: string; exit: string; evidenceIds: number[] }

export function joinRecords(claims: Claim[], target: string, scope: Claim['scope'], includeCandidates = true): Claim[] {
  return claims.filter(claim => claim.claim_type === 'join_ports'
    && claim.payload.target === target && scopeKey(claim.scope) === scopeKey(scope)
    && claim.effective_state !== 'dismissed'
    && (includeCandidates || claim.effective_state === 'confirmed'));
}

export function projectGraph(claims: Claim[], showCandidates = true): { entities: Claim[]; edges: SemanticEdge[]; unresolvedClaimIds: number[] } {
  const entities = entityVersions(claims).filter(item => showCandidates || item.effective_state === 'confirmed');
  function resolve(entityId: string, scope: Claim['scope']): string | undefined {
    const exact = entities.find(item => item.entity_id === entityId && scopeKey(item.scope) === scopeKey(scope));
    return exact ? graphNodeId(exact) : undefined;
  }
  const edges: SemanticEdge[] = [], unresolved = new Set<number>();
  for (const claim of claims) {
    if (claim.effective_state === 'dismissed' || (!showCandidates && claim.effective_state !== 'confirmed')) continue;
    const target = resolve(claim.payload.target ?? '', claim.scope);
    if (claim.claim_type === 'relation' || claim.claim_type === 'merge') {
      const source = resolve(claim.payload.source ?? '', claim.scope);
      if (source && target) edges.push({ id: `claim-${claim.claim_id}`, source, target, claimId: claim.claim_id, relation: claim.payload.relation ?? 'merge' });
      else unresolved.add(claim.claim_id);
    }
    if (claim.claim_type === 'join_ports') {
      for (const input of claim.payload.inputs ?? []) {
        const source = resolve(input.ref, claim.scope);
        if (source && target) edges.push({ id: `claim-${claim.claim_id}-${input.port}`, source, target, claimId: claim.claim_id, relation: `input:${input.port}` });
        else unresolved.add(claim.claim_id);
      }
    }
  }
  return { entities, edges, unresolvedClaimIds: [...unresolved] };
}

export function foldGroup(selected: string[], edges: SemanticEdge[], claims: Claim[], complete = true): FoldResult {
  if (!complete) throw new Error('需要完整研究图才能验证单入口单出口，部分记录未显示时不能折叠');
  const members = new Set(selected);
  if (members.size < 2) throw new Error('过程组至少选择两个节点');
  const semantic = edges.filter(edge => edge.relation !== 'same_topic');
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
  const groupClaims = claims.filter(claim => members.has(graphNodeId(claim)) || internal.some(edge => edge.claimId === claim.claim_id));
  return {
    members: [...members], entry: incoming[0].target, exit: outgoing[0].source,
    evidenceIds: [...new Set(groupClaims.flatMap(claim => claim.evidence.map(span => span.span_id)))].sort((a, b) => a - b),
  };
}
