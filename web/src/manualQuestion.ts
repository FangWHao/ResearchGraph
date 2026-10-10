import type { Claim, QuestionRequest } from './types';

export interface QuestionDraft {
  text: string; scopeText: string;
  intent: { request_id: string; contentKey: string } | null;
}

export function emptyQuestionDraft(): QuestionDraft {
  return { text: '', scopeText: '', intent: null };
}

export function allowsUnknownQuestionScope(claim: Claim): boolean {
  return claim.claim_type === 'entity_version' && claim.payload.kind === 'question'
    && claim.basis === 'manual' && claim.actor.startsWith('human:');
}

export function parseScopeText(text: string, allowUnknown: boolean): Record<string, string> | null {
  if (!text.trim()) {
    if (allowUnknown) return null;
    throw new Error('此记录需要填写完整范围，每行一个 字段=值。');
  }
  const parsed: Record<string, string> = Object.create(null);
  for (const line of text.split('\n').filter(value => value.trim())) {
    const index = line.indexOf('=');
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim();
    if (index < 1 || !key || !value || Object.hasOwn(parsed, key)) throw new Error('范围需逐行填写不重复且非空的 字段=值。');
    if ([...key].length > 100 || [...value].length > 500) throw new Error('范围字段最多 100 字符，值最多 500 字符。');
    parsed[key] = value;
    if (Object.keys(parsed).length > 32) throw new Error('研究范围最多填写 32 项。');
  }
  return parsed;
}

function contentKey(project: string, text: string, actor: string, scope: Record<string, string> | null): string {
  return JSON.stringify({ project, text, actor,
    scope: scope == null ? null : Object.fromEntries(Object.entries(scope).sort(([a], [b]) => a.localeCompare(b))),
  });
}

export function isCurrentQuestionIntent(draft: QuestionDraft | undefined, project: string, actor: string, requestId: string): boolean {
  if (!draft?.intent || draft.intent.request_id !== requestId) return false;
  try { return draft.intent.contentKey === contentKey(project, draft.text, actor, parseScopeText(draft.scopeText, true)); }
  catch { return false; }
}

export function questionRequest(draft: QuestionDraft, project: string, actor: string, revision: number) {
  if (!project) throw new Error('请先选择保存问题的项目。');
  if (!actor.startsWith('human:') || !actor.slice(6).trim()) throw new Error('请先填写复核者姓名，再记录研究问题。');
  if (!draft.text.trim()) throw new Error('请输入研究问题，不能只包含空白。');
  if (new TextEncoder().encode(draft.text).length > 16000) throw new Error('研究问题超过 16000 UTF-8 字节，请缩短后保存。');
  const scope = parseScopeText(draft.scopeText, true);
  const identity = contentKey(project, draft.text, actor, scope);
  const intent = draft.intent?.contentKey === identity ? draft.intent : { request_id: crypto.randomUUID(), contentKey: identity };
  const request: QuestionRequest = { project_id: project, text: draft.text, scope, actor, expected_revision: revision, request_id: intent.request_id };
  if (new TextEncoder().encode(JSON.stringify(request)).length > 65536) throw new Error('整条问题记录超过保存上限，请缩短内容或范围。');
  return { request, draft: { ...draft, intent } };
}
