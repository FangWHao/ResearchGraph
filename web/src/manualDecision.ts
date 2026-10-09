import { parseScopeText } from './manualQuestion';
import type { Claim, DecisionAction, DecisionRequest, DecisionTarget, ResolveDecisionRequest } from './types';

type Intent = { request_id: string; contentKey: string };
export interface DecisionDraft { selector: string; action: DecisionAction | ''; why: string; scopeText: string; intent: Intent | null }
export interface ResolutionDraft { query: string; target: DecisionTarget | null; intent: Intent | null }
export function emptyDecisionDraft(): DecisionDraft { return { selector: '', action: '', why: '', scopeText: '', intent: null }; }
export function emptyResolutionDraft(): ResolutionDraft { return { query: '', target: null, intent: null }; }
export function requiresDecisionTarget(claim: Claim): boolean {
  return claim.claim_type === 'decision_event' && (claim.pending_decision?.requires_resolution === true || typeof claim.payload.target !== 'string' || !claim.payload.target);
}
export function canResolveDecision(claim: Claim): boolean {
  return claim.pending_decision?.requires_resolution === true && claim.effective_state === 'candidate'
    && claim.replacement_ids.length === 0 && claim.pending_decision.resolved_claim_id == null;
}
function actorCheck(actor: string) {
  if (!actor.startsWith('human:') || !actor.slice(6).trim()) throw new Error('请先填写复核者姓名。');
}
function canonicalScope(scope: Record<string, string> | null) {
  return scope == null ? null : Object.fromEntries(Object.entries(scope).sort(([a], [b]) => a.localeCompare(b)));
}
function decisionKey(draft: DecisionDraft, project: string, actor: string) {
  return JSON.stringify({ project, actor, selector: draft.selector, action: draft.action, why: draft.why, scope: canonicalScope(parseScopeText(draft.scopeText, true)) });
}
export function currentDecisionIntent(draft: DecisionDraft | undefined, project: string, actor: string, id: string): boolean {
  if (!draft?.intent || draft.intent.request_id !== id) return false;
  try { return draft.intent.contentKey === decisionKey(draft, project, actor); } catch { return false; }
}
export function decisionRequest(draft: DecisionDraft, project: string, actor: string, revision: number) {
  actorCheck(actor);
  if (!project) throw new Error('请先选择保存决定的项目。');
  for (const [text, limit, name] of [[draft.selector, 4000, '对象原话'], [draft.why, 16000, '决定理由']] as const) {
    if (!text.trim()) throw new Error(`${name}不能只包含空白。`);
    if (new TextEncoder().encode(text).length > limit) throw new Error(`${name}超过 ${limit} UTF-8 字节，请缩短后保存。`);
  }
  if (!['accept', 'defer', 'reject', 'withdraw'].includes(draft.action)) throw new Error('请选择决定动作。');
  const key = decisionKey(draft, project, actor);
  const intent = draft.intent?.contentKey === key ? draft.intent : { request_id: crypto.randomUUID(), contentKey: key };
  const request: DecisionRequest = { project_id: project, selector: draft.selector, action: draft.action as DecisionAction,
    why: draft.why, scope: parseScopeText(draft.scopeText, true), actor, request_id: intent.request_id, expected_revision: revision };
  if (new TextEncoder().encode(JSON.stringify(request)).length > 65536) throw new Error('整条决定记录超过保存上限。');
  return { request, draft: { ...draft, intent } };
}
function resolutionKey(draft: ResolutionDraft, project: string, claim: number, actor: string) {
  return JSON.stringify({ project, claim, actor, target: draft.target?.entity_id ?? null });
}
export function currentResolutionIntent(draft: ResolutionDraft | undefined, project: string, claim: number, actor: string, id: string): boolean {
  return draft?.intent?.request_id === id && draft.intent.contentKey === resolutionKey(draft, project, claim, actor);
}
export function resolutionRequest(draft: ResolutionDraft, project: string, claim: number, actor: string, revision: number) {
  actorCheck(actor);
  if (!draft.target) throw new Error('请主动选择一个对象，再确认对象归属。');
  const key = resolutionKey(draft, project, claim, actor);
  const intent = draft.intent?.contentKey === key ? draft.intent : { request_id: crypto.randomUUID(), contentKey: key };
  const request: ResolveDecisionRequest = { target_id: draft.target.entity_id, actor, request_id: intent.request_id, expected_revision: revision };
  return { request, draft: { ...draft, intent } };
}
