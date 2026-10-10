import { parseScopeText } from './manualQuestion';

export interface QaDraft { question: string; scopeText: string; occurredUntil: string; knownUntil: string; k: number; maxBytes: number }
export interface QaOptions { project_id: string; revision: number; configured: boolean; remote: boolean | null; remote_allowed: boolean; input_budget: number; output_budget: number; model: string | null }
export interface QaRecord {
  claim_id: number; claim_type: string; entity_id: string | null; scope: Record<string, string> | null;
  basis: string; actor: string; claim_state: string; effective_state: string; occurred_at: string | null;
  recorded_at: string | null; replaced: boolean; replacement_ids: number[]; payload_preview: string;
  payload_preview_truncated: boolean; computed_states: Record<string, { state: string; claim_ids: number[]; claim_ids_partial?: boolean }>;
}
export interface QaSource {
  citation_id: string; event_id: number; span_id: number | null; text: string;
  occurred_at: string | null; recorded_at: string | null; source_byte_start: number | null; source_byte_end: number | null;
  window_start: number; window_end: number; window_sha256: string; window_truncated: boolean;
  records: QaRecord[]; records_partial?: boolean;
}
export interface QaPacket {
  project_id: string; revision: number; question: string; scope: Record<string, string> | null; scope_filter: boolean;
  occurred_until: string | null; known_until: string | null; sources: QaSource[];
  gaps: Record<string, unknown>[]; gaps_total: number; matched_claims: number; raw_matches_examined: number;
  retrieval_partial: boolean; notice: string; status?: 'answered' | 'insufficient' | 'unavailable';
  model_interpretation?: boolean; statements?: { text: string; citations: { id: string; quote: string }[] }[];
  caveats?: string[]; run_id?: number | string; records_changed_during_answer?: boolean;
}
export interface QaPreview {
  project_id: string; revision: number; preview_sha: string; source_count: number; remote: boolean | null; notice: string;
  input: { question: string; question_redacted: boolean; sources: { citation_id: string; text: string; text_redacted: boolean; records_redacted: boolean; [key: string]: unknown }[]; [key: string]: unknown };
}
export function emptyQaDraft(): QaDraft { return { question: '', scopeText: '', occurredUntil: '', knownUntil: '', k: 12, maxBytes: 4000 }; }
function cutoff(value: string, name: string): string | undefined {
  if (!value.trim()) return undefined;
  const text = value.trim();
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.test(text) || !Number.isFinite(Date.parse(text))) throw new Error(`${name}需填写带时区的 ISO 时间，例如 2026-10-10T12:00:00+08:00。`);
  return text;
}
export function qaRequest(project: string, draft: QaDraft, revision?: number) {
  if (!project || !draft.question.trim()) throw new Error('请选择项目并填写问题。');
  if ([...draft.question].length > 2000) throw new Error('问题最多 2000 个字符。');
  if (!Number.isInteger(draft.k) || draft.k < 1 || draft.k > 100) throw new Error('来源条数需为 1 至 100 的整数。');
  if (!Number.isInteger(draft.maxBytes) || draft.maxBytes < 4 || draft.maxBytes > 24000) throw new Error('每条原文窗口需为 4 至 24000 字节的整数。');
  const scope = draft.scopeText.trim() ? parseScopeText(draft.scopeText, false)! : undefined;
  return { project_id: project, question: draft.question, k: draft.k, max_bytes: draft.maxBytes,
    ...(scope ? { scope: Object.fromEntries(Object.entries(scope).sort(([a], [b]) => a.localeCompare(b))) } : {}),
    ...(cutoff(draft.occurredUntil, '发生截止') ? { occurred_until: cutoff(draft.occurredUntil, '发生截止') } : {}),
    ...(cutoff(draft.knownUntil, '获知截止') ? { known_until: cutoff(draft.knownUntil, '获知截止') } : {}),
    ...(revision == null ? {} : { expected_revision: revision }),
  };
}
export function qaIntent(project: string, draft: QaDraft): string {
  // Invalid edits still invalidate an in-flight response without discarding the user's input.
  try { return JSON.stringify(qaRequest(project, draft)); } catch { return JSON.stringify({ project, draft }); }
}
export function sameQaIntent(project: string, draft: QaDraft, intent: string): boolean { return qaIntent(project, draft) === intent; }
function object(value: unknown): value is Record<string, unknown> { return value != null && typeof value === 'object' && !Array.isArray(value); }
function validRecord(value: unknown): boolean {
  if (!object(value) || !Number.isInteger(value.claim_id) || typeof value.claim_type !== 'string'
    || !(value.entity_id === null || typeof value.entity_id === 'string')
    || !(value.scope === null || object(value.scope) && Object.values(value.scope).every(item => typeof item === 'string'))
    || typeof value.basis !== 'string' || typeof value.actor !== 'string'
    || !['candidate', 'confirmed', 'dismissed'].includes(String(value.claim_state))
    || !['candidate', 'confirmed', 'dismissed'].includes(String(value.effective_state))
    || typeof value.replaced !== 'boolean' || !Array.isArray(value.replacement_ids) || value.replacement_ids.some(id => !Number.isInteger(id))
    || typeof value.payload_preview !== 'string' || typeof value.payload_preview_truncated !== 'boolean' || !object(value.computed_states)) return false;
  return Object.values(value.computed_states).every(state => object(state) && typeof state.state === 'string'
    && Array.isArray(state.claim_ids) && state.claim_ids.every(id => Number.isInteger(id)));
}
export function parseQaContext(text: string): Record<string, unknown> {
  const match = /^<rg-context v="1" id="ctx_[a-f0-9]{20}">\n([\s\S]*)\n<\/rg-context>$/.exec(text.trim());
  if (!match) throw new Error('问答响应封装无效，已保留此前的本地来源。');
  let parsed: unknown;
  try { parsed = JSON.parse(match[1]); } catch { throw new Error('问答响应不是有效 JSON，已保留此前的本地来源。'); }
  if (!object(parsed)) throw new Error('问答响应内容无效。');
  return parsed;
}
export function parseQaPacket(text: string, project: string, question: string, answer = false): QaPacket {
  const value = parseQaContext(text);
  if (value.project_id !== project || value.question !== question || !Number.isInteger(value.revision)
    || typeof value.scope_filter !== 'boolean' || typeof value.retrieval_partial !== 'boolean'
    || !Array.isArray(value.sources) || !Array.isArray(value.gaps)) throw new Error('问答响应与当前项目或问题不符，已保留此前的本地来源。');
  const ids = new Set<string>();
  for (const source of value.sources) {
    if (!object(source) || typeof source.citation_id !== 'string' || !/^[SE]\d+$/.test(source.citation_id)
      || ids.has(source.citation_id) || !Number.isInteger(source.event_id) || typeof source.text !== 'string'
      || !Number.isInteger(source.window_start) || !Number.isInteger(source.window_end)
      || (source.window_start as number) < 0 || (source.window_end as number) < (source.window_start as number)
      || typeof source.window_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(source.window_sha256)
      || typeof source.window_truncated !== 'boolean' || !Array.isArray(source.records)) throw new Error('来源位置或引用编号无效，无法定位原文。');
    if (source.records.some(record => !validRecord(record))) throw new Error('关联记录格式无效，已保留此前的本地来源。');
    ids.add(source.citation_id);
  }
  if (answer) {
    if (!['answered', 'insufficient', 'unavailable'].includes(String(value.status)) || value.model_interpretation !== true
      || !Array.isArray(value.statements) || !Array.isArray(value.caveats) || value.caveats.some(item => typeof item !== 'string')) throw new Error('模型解释格式无效，已保留本地来源。');
    for (const statement of value.statements) {
      if (!object(statement) || typeof statement.text !== 'string' || !Array.isArray(statement.citations) || !statement.citations.length
        || statement.citations.some(citation => !object(citation) || typeof citation.id !== 'string' || !ids.has(citation.id) || typeof citation.quote !== 'string' || !citation.quote.trim())) throw new Error('模型解释引用无效，已保留本地来源。');
    }
    if ((value.status === 'answered') !== (value.statements.length > 0)) throw new Error('模型解释状态与内容不符，已保留本地来源。');
  }
  return value as unknown as QaPacket;
}
export function parseQaPreview(text: string, project: string): QaPreview {
  const value = parseQaContext(text);
  if (value.project_id !== project || !Number.isInteger(value.revision) || typeof value.preview_sha !== 'string'
    || !/^[a-f0-9]{64}$/.test(value.preview_sha) || !Number.isInteger(value.source_count)
    || !object(value.input) || typeof value.input.question !== 'string' || !Array.isArray(value.input.sources)
    || typeof value.input.question_redacted !== 'boolean' || ![true, false, null].includes(value.remote as boolean | null)
    || value.source_count !== value.input.sources.length
    || value.input.sources.some(source => !object(source) || typeof source.citation_id !== 'string' || !/^[SE]\d+$/.test(source.citation_id) || typeof source.text !== 'string' || typeof source.text_redacted !== 'boolean' || typeof source.records_redacted !== 'boolean')) throw new Error('发送预览无效，不能开启外发。');
  return value as unknown as QaPreview;
}
export function qaEvidence(source: QaSource) {
  return { event_id: source.event_id, byte_start: source.window_start, byte_end: source.window_end, quote_sha256: source.window_sha256 };
}
