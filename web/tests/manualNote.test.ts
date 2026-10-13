import { describe, expect, it } from 'vitest';
import { emptyNoteDraft, isCurrentNoteIntent, isNoteClaim, noteRequest, validateNoteResult } from '../src/manualNote';
import { adoption, evidenceState, graphNodeId, projectGraph } from '../src/model';
import type { Claim } from '../src/types';

const actor = 'human:公开复核者';
const claim: Claim = { claim_id: 1, claim_type: 'entity_version', entity_id: 'note-id', kind: 'note', payload: { claim_type: 'entity_version', kind: 'note', label: '独立笔记', content: '独立笔记', about: 'approach-id' }, scope: { data: 'v1' }, basis: 'model_inference', actor: 'model:mcp', claim_state: 'candidate', effective_state: 'candidate', occurred_at: null, recorded_at: '2026-01-02T00:00:00Z', replaces_claim: null, replacement_ids: [], review: null, confirmation_source: null, evidence: [], groups: [] };

describe('人工笔记的原文和意图身份', () => {
  it('原样保存正文，未填范围与关联对象不猜归属', () => {
    const prepared = noteRequest({ ...emptyNoteDraft(), text: '  原话\n<script>字面</script>  ' }, 'project', actor, 2);
    expect(prepared.request).toMatchObject({ text: '  原话\n<script>字面</script>  ', about: null, scope: null, actor });
    expect(isCurrentNoteIntent(prepared.draft, 'project', actor, prepared.request.request_id)).toBe(true);
  });
  it('网络重试和版本刷新保留同意图UUID，内容或身份变化另建请求', () => {
    const prepared = noteRequest({ ...emptyNoteDraft(), text: '笔记', aboutText: ' approach-id ', scopeText: 'data=v1' }, 'project', actor, 2);
    const retried = noteRequest(prepared.draft, 'project', actor, 4);
    expect(retried.request.request_id).toBe(prepared.request.request_id);
    expect(retried.request.about).toBe('approach-id');
    for (const changed of [{ ...prepared.draft, text: '笔记\n' }, { ...prepared.draft, aboutText: 'other-id' }, { ...prepared.draft, scopeText: '' }]) {
      expect(noteRequest(changed, 'project', actor, 4).request.request_id).not.toBe(prepared.request.request_id);
      expect(isCurrentNoteIntent(changed, 'project', actor, prepared.request.request_id)).toBe(false);
    }
    expect(noteRequest(prepared.draft, 'other-project', actor, 4).request.request_id).not.toBe(prepared.request.request_id);
    expect(noteRequest(prepared.draft, 'project', 'human:另一姓名', 4).request.request_id).not.toBe(prepared.request.request_id);
  });
  it('同内容范围字段换序不改变意图，关联对象不会补入范围', () => {
    const draft = { ...emptyNoteDraft(), text: '笔记', aboutText: 'approach-id', scopeText: 'step=统计\ndata=v1' };
    const first = noteRequest(draft, 'project', actor, 2);
    expect(noteRequest({ ...first.draft, scopeText: 'data=v1\nstep=统计' }, 'project', actor, 3).request.request_id).toBe(first.request.request_id);
    expect(noteRequest({ ...first.draft, scopeText: '' }, 'project', actor, 3).request.scope).toBeNull();
  });
  it('只清理相同请求意图；不同ID、非法范围或新草稿不会误判旧成功', () => {
    const first = noteRequest({ ...emptyNoteDraft(), text: '旧笔记' }, 'project', actor, 2);
    expect(isCurrentNoteIntent(first.draft, 'project', actor, crypto.randomUUID())).toBe(false);
    expect(isCurrentNoteIntent({ ...first.draft, text: '新笔记' }, 'project', actor, first.request.request_id)).toBe(false);
    expect(isCurrentNoteIntent({ ...first.draft, scopeText: '错误' }, 'project', actor, first.request.request_id)).toBe(false);
  });
  it('拒绝纯空白和UTF8超限，不截断正文', () => {
    expect(() => noteRequest({ ...emptyNoteDraft(), text: ' \n ' }, 'project', actor, 2)).toThrow();
    expect(() => noteRequest({ ...emptyNoteDraft(), text: '字'.repeat(5334) }, 'project', actor, 2)).toThrow();
    expect(noteRequest({ ...emptyNoteDraft(), text: '字'.repeat(5333) }, 'project', actor, 2).request.text).toHaveLength(5333);
  });
  it('回执须对应请求且确为人工确认，不能把错误响应清成成功', () => {
    const { request } = noteRequest({ ...emptyNoteDraft(), text: '笔记' }, 'project', actor, 2);
    const receipt = { revision: 3, request_id: request.request_id, claim_id: 8, entity_id: 'note-id', event_id: 4, replayed: false, claim_state: 'confirmed' as const, effective_state: 'confirmed' as const };
    expect(validateNoteResult(receipt, request)).toBe(receipt);
    expect(validateNoteResult({ ...receipt, replayed: true, effective_state: 'dismissed' }, request).effective_state).toBe('dismissed');
    expect(() => validateNoteResult({ ...receipt, request_id: crypto.randomUUID() }, request)).toThrow();
    expect(() => validateNoteResult({ ...receipt, claim_state: 'candidate' }, request)).toThrow();
  });
});

it('独立候选笔记及about不生成科学决定、证据或自动关系', () => {
  expect(isNoteClaim(claim)).toBe(true);
  expect(projectGraph([claim]).edges).toEqual([]);
  expect(projectGraph([claim]).entities.map(graphNodeId)).toEqual([graphNodeId(claim)]);
  expect(adoption([claim], 'approach-id', claim.scope)).toBe('unknown');
  expect(evidenceState([claim], 'approach-id', claim.scope)).toBe('unassessed');
  expect(isNoteClaim({ ...claim, kind: 'approach', payload: { ...claim.payload, kind: 'approach' } })).toBe(false);
});
