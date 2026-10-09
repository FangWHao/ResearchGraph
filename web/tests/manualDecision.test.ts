import { describe, expect, it } from 'vitest';
import { canResolveDecision, currentDecisionIntent, currentResolutionIntent, decisionRequest, emptyDecisionDraft, emptyResolutionDraft, requiresDecisionTarget, resolutionRequest } from '../src/manualDecision';
import { adoption, knownScope } from '../src/model';
import type { Claim, DecisionTarget } from '../src/types';

const actor = 'human:合成复核者';
const draft = { ...emptyDecisionDraft(), selector: '  合成对象\n', action: 'accept' as const, why: '  原理由\n第二行  ' };
const target: DecisionTarget = { entity_id: 'object-a', claim_id: 3, kind: 'approach', label: '合成对象', label_truncated: false, label_total_bytes: 12, scope: { data: 'v1' } };
const pending: Claim = { claim_id: 2, entity_id: 'carrier', claim_type: 'decision_event', payload: { claim_type: 'decision_event', target: null }, scope: null,
  basis: 'manual', actor, claim_state: 'candidate', effective_state: 'candidate', occurred_at: null, recorded_at: '', replaces_claim: null,
  replacement_ids: [], review: null, confirmation_source: null, evidence: [], groups: [],
  pending_decision: { request_id: 'synthetic', selector: '对象', action: 'accepted', why: '理由', scope: null, target_id: null, requires_resolution: true, resolved_claim_id: null } };

describe('人工决定与对象复核不能误确认或丢失意图', () => {
  it('原话和理由原样保存，空范围和主动动作选择不可猜造', () => {
    expect(decisionRequest(draft, 'project-a', actor, 3).request).toMatchObject({ selector: draft.selector, why: draft.why, scope: null, action: 'accept' });
    expect(() => decisionRequest({ ...draft, action: '' }, 'project-a', actor, 3)).toThrow('选择决定动作');
  });
  it('版本刷新复用意图，原话、动作、理由、范围、人或项目改变均产生新编号', () => {
    const first = decisionRequest(draft, 'project-a', actor, 3);
    expect(decisionRequest(first.draft, 'project-a', actor, 9).request.request_id).toBe(first.request.request_id);
    for (const changed of [{ ...first.draft, selector: '另一对象' }, { ...first.draft, why: '另一理由' }, { ...first.draft, action: 'reject' as const }, { ...first.draft, scopeText: 'data=v2' }]) {
      expect(decisionRequest(changed, 'project-a', actor, 9).request.request_id).not.toBe(first.request.request_id);
      expect(currentDecisionIntent(changed, 'project-a', actor, first.request.request_id)).toBe(false);
    }
    expect(currentDecisionIntent(first.draft, 'project-b', actor, first.request.request_id)).toBe(false);
    expect(currentDecisionIntent(first.draft, 'project-a', 'human:另一人', first.request.request_id)).toBe(false);
  });
  it('按 UTF8 字节限制原话和理由，拒绝空姓名与纯空白', () => {
    expect(decisionRequest({ ...draft, selector: '字'.repeat(1333) + 'x' }, 'p', actor, 1).request.selector).toHaveLength(1334);
    expect(() => decisionRequest({ ...draft, selector: '字'.repeat(1334) }, 'p', actor, 1)).toThrow('4000');
    expect(() => decisionRequest({ ...draft, why: '字'.repeat(5334) }, 'p', actor, 1)).toThrow('16000');
    expect(() => decisionRequest({ ...draft, why: '\n ' }, 'p', actor, 1)).toThrow('空白');
    expect(() => decisionRequest(draft, 'p', 'human: ', 1)).toThrow('姓名');
    const scopeText = Array.from({ length: 32 }, (_, i) => `field${i}=${'\u0001'.repeat(500)}`).join('\n');
    expect(() => decisionRequest({ ...draft, scopeText }, 'p', actor, 1)).toThrow('整条决定记录');
  });
  it('未确定对象即使缺元数据也禁止普通确认，驳回或替换不能再解决', () => {
    expect(requiresDecisionTarget(pending)).toBe(true);
    expect(requiresDecisionTarget({ ...pending, pending_decision: null })).toBe(true);
    expect(canResolveDecision(pending)).toBe(true);
    expect(canResolveDecision({ ...pending, effective_state: 'dismissed' })).toBe(false);
    expect(canResolveDecision({ ...pending, replacement_ids: [9] })).toBe(false);
  });
  it('对象选择必须显式进行，重试只复用同项目同记录同对象的人为意图', () => {
    expect(() => resolutionRequest(emptyResolutionDraft(), 'p', 2, actor, 1)).toThrow('主动选择');
    const first = resolutionRequest({ ...emptyResolutionDraft(), target }, 'p', 2, actor, 1);
    expect(first.request).toEqual({ target_id: 'object-a', actor, request_id: first.request.request_id, expected_revision: 1 });
    expect(resolutionRequest(first.draft, 'p', 2, actor, 9).request.request_id).toBe(first.request.request_id);
    for (const [p, claim, person, t] of [['other', 2, actor, target], ['p', 8, actor, target], ['p', 2, 'human:另一人', target], ['p', 2, actor, { ...target, entity_id: 'object-b' }]] as const) {
      expect(currentResolutionIntent({ ...first.draft, target: t }, p, claim, person, first.request.request_id)).toBe(false);
    }
  });
  it('缺失、空或明确未知范围的确认采用事件始终不算当前采用', () => {
    const event = { ...pending, payload: { claim_type: 'decision_event', target: 'object-a', action: 'accepted' }, effective_state: 'confirmed', occurred_at: '2026-10-09T10:00:00Z' } as Claim;
    for (const scope of [null, {}, ...['unknown', '未知', '未确定', '不详', 'unspecified', '?', ' UNKNOWN '].map(data => ({ data }))]) {
      expect(knownScope(scope)).toBe(false);
      expect(adoption([{ ...event, scope }], 'object-a', scope)).toBe('unknown_scope');
    }
    expect(adoption([{ ...event, scope: { data: 'v1' } }], 'object-a', { data: 'v1' })).toBe('accepted');
  });
});
