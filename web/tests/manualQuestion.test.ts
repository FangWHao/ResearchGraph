import { describe, expect, it } from 'vitest';
import { allowsUnknownQuestionScope, emptyQuestionDraft, isCurrentQuestionIntent, parseScopeText, questionRequest } from '../src/manualQuestion';
import type { Claim } from '../src/types';

describe('人工问题保留原文、范围与提交身份', () => {
  it('保留正文空白和换行，空范围保持 null', () => {
    const text = '  合成问题？\n第二行  ';
    const result = questionRequest({ ...emptyQuestionDraft(), text }, 'project-a', 'human:合成记录者', 2);
    expect(result.request).toMatchObject({ text, scope: null, project_id: 'project-a', actor: 'human:合成记录者', expected_revision: 2 });
    expect(result.request.request_id).toMatch(/^[a-f0-9-]{36}$/);
  });

  it('同一内容在版本刷新或范围字段重新排序后复用意图编号，内容/人/项目变化重新编号', () => {
    const first = questionRequest({ ...emptyQuestionDraft(), text: '合成问题', scopeText: 'data=v1\nstep=分析' }, 'project-a', 'human:记录者', 1);
    const refreshed = questionRequest({ ...first.draft, scopeText: 'step=分析\ndata=v1' }, 'project-a', 'human:记录者', 9);
    expect(refreshed.request.request_id).toBe(first.request.request_id);
    for (const result of [
      questionRequest({ ...first.draft, text: '另一条问题' }, 'project-a', 'human:记录者', 9),
      questionRequest(first.draft, 'project-b', 'human:记录者', 9),
      questionRequest(first.draft, 'project-a', 'human:另一人', 9),
    ]) expect(result.request.request_id).not.toBe(first.request.request_id);
  });

  it('实测 UTF-8 正文和整条 JSON 字节上限，拒绝空白正文和空姓名', () => {
    const request = (text: string, scopeText = '') => questionRequest({ ...emptyQuestionDraft(), text, scopeText }, 'project-a', 'human:记录者', 1);
    expect(request('问'.repeat(5333) + 'x').request.text).toHaveLength(5334);
    expect(() => request('问'.repeat(5334))).toThrow('16000');
    expect(() => request(' \n\t')).toThrow('空白');
    expect(() => questionRequest({ ...emptyQuestionDraft(), text: '问题' }, 'project-a', 'human: ', 1)).toThrow('姓名');
    const escapedScope = Array.from({ length: 32 }, (_, index) => `field${index}=${'\u0001'.repeat(500)}`).join('\n');
    expect(() => request('问题', escapedScope)).toThrow('整条问题记录');
  });

  it('范围不填时不补造字段；重复、空值和超上限不能进入写入请求', () => {
    expect(parseScopeText('', true)).toBeNull();
    expect(() => parseScopeText('', false)).toThrow('完整范围');
    expect(parseScopeText('__proto__=合成范围', true)).toEqual({ ['__proto__']: '合成范围' });
    for (const value of ['data=v1\ndata=v2', 'data=', '=v1', `${'k'.repeat(101)}=v1`, `data=${'v'.repeat(501)}`,
      Array.from({ length: 33 }, (_, index) => `field${index}=v1`).join('\n')]) {
      expect(() => parseScopeText(value, true)).toThrow();
    }
  });

  it('只有人工直接记录的问题可修改为未知范围，不能放宽模型或其他类型', () => {
    const claim = { claim_type: 'entity_version', payload: { kind: 'question' }, basis: 'manual', actor: 'human:记录者' } as Claim;
    expect(allowsUnknownQuestionScope(claim)).toBe(true);
    expect(allowsUnknownQuestionScope({ ...claim, actor: 'model:1', basis: 'model_inference' })).toBe(false);
    expect(allowsUnknownQuestionScope({ ...claim, payload: { claim_type: 'entity_version', kind: 'approach' } })).toBe(false);
  });

  it('旧请求成功不能归属到内容、范围或记录者已变的新草稿', () => {
    const first = questionRequest({ ...emptyQuestionDraft(), text: '旧问题' }, 'project-a', 'human:记录者', 1);
    expect(isCurrentQuestionIntent(first.draft, 'project-a', 'human:记录者', first.request.request_id)).toBe(true);
    for (const draft of [{ ...first.draft, text: '新问题' }, { ...first.draft, scopeText: 'data=v2' }, { ...first.draft, intent: null }]) {
      expect(isCurrentQuestionIntent(draft, 'project-a', 'human:记录者', first.request.request_id)).toBe(false);
    }
    expect(isCurrentQuestionIntent(first.draft, 'project-a', 'human:另一人', first.request.request_id)).toBe(false);
  });
});
