import { parseScopeText } from './manualQuestion';
import type { Claim, NoteRequest, NoteResult } from './types';

export interface NoteDraft {
  text: string; scopeText: string; aboutText: string;
  intent: { request_id: string; contentKey: string } | null;
}
export function emptyNoteDraft(): NoteDraft { return { text: '', scopeText: '', aboutText: '', intent: null }; }
export function isNoteClaim(claim: Claim): boolean {
  return claim.claim_type === 'entity_version' && (claim.kind ?? claim.payload.kind) === 'note';
}
function identity(draft: NoteDraft, project: string, actor: string): string {
  const scope = parseScopeText(draft.scopeText, true);
  return JSON.stringify({ project, actor, text: draft.text, about: draft.aboutText.trim() || null,
    scope: scope == null ? null : Object.fromEntries(Object.entries(scope).sort(([a], [b]) => a.localeCompare(b))) });
}
export function isCurrentNoteIntent(draft: NoteDraft | undefined, project: string, actor: string, requestId: string): boolean {
  if (!draft?.intent || draft.intent.request_id !== requestId) return false;
  try { return draft.intent.contentKey === identity(draft, project, actor); } catch { return false; }
}
export function noteRequest(draft: NoteDraft, project: string, actor: string, revision: number) {
  if (!project) throw new Error('请先选择保存笔记的项目。');
  if (!actor.startsWith('human:') || !actor.slice(6).trim()) throw new Error('请先填写复核者姓名，再记录笔记。');
  if (!draft.text.trim()) throw new Error('请输入笔记正文，不能只包含空白。');
  if (new TextEncoder().encode(draft.text).length > 16000) throw new Error('笔记超过 16000 UTF-8 字节，请缩短后保存。');
  if (!Number.isSafeInteger(revision) || revision < 0) throw new Error('当前版本尚未读取，请先刷新。');
  if (draft.aboutText.trim().length > 120) throw new Error('关联对象 ID 超过 120 字符，请核对完整对象编号。');
  const contentKey = identity(draft, project, actor);
  const intent = draft.intent?.contentKey === contentKey ? draft.intent : { request_id: crypto.randomUUID(), contentKey };
  const request: NoteRequest = { project_id: project, actor, text: draft.text, about: draft.aboutText.trim() || null,
    scope: parseScopeText(draft.scopeText, true), expected_revision: revision, request_id: intent.request_id };
  if (new TextEncoder().encode(JSON.stringify(request)).length > 65536) throw new Error('整条笔记超过保存上限，请缩短正文或范围。');
  return { request, draft: { ...draft, intent } };
}
export function validateNoteResult(value: NoteResult, request: NoteRequest): NoteResult {
  if (value.request_id !== request.request_id || !Number.isSafeInteger(value.revision) || value.revision < request.expected_revision
    || !Number.isSafeInteger(value.claim_id) || value.claim_id < 1 || !Number.isSafeInteger(value.event_id) || value.event_id < 1
    || typeof value.entity_id !== 'string' || !value.entity_id || typeof value.replayed !== 'boolean'
    || value.claim_state !== 'confirmed' || !['candidate', 'confirmed', 'dismissed'].includes(value.effective_state)
    || !value.replayed && value.effective_state !== 'confirmed') throw new Error('笔记保存回执与请求不一致；输入已保留，请核对后重试。');
  return value;
}
