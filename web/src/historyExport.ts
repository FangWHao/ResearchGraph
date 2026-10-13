import { parseScopeText } from './manualQuestion';

export interface ExportDraft { occurredUntil: string; knownUntil: string; scopeMode: 'all' | 'exact' | 'unknown'; scopeText: string; includeEvidence: boolean; patternsText: string }
export interface ExportRequest { project_id: string; expected_revision: number; include_evidence: boolean; occurred_until?: string; known_until?: string; scope?: Record<string, string> | null; redact_patterns?: string[] }
export function emptyExportDraft(): ExportDraft { return { occurredUntil: '', knownUntil: '', scopeMode: 'all', scopeText: '', includeEvidence: false, patternsText: '' }; }
function cutoff(value: string, name: string): string | undefined {
  if (!value.trim()) return undefined;
  const text = value.trim();
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-](\d{2}):(\d{2}))$/.exec(text);
  const invalid = () => new Error(`${name}需填写带时区且有效的 ISO 时间，例如 2026-10-10T12:00:00+08:00。`);
  if (!parts || !Number.isFinite(Date.parse(text))) throw invalid();
  const year = Number(parts[1]); const month = Number(parts[2]); const day = Number(parts[3]);
  const days = [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1] || Number(parts[4]) > 23 || Number(parts[5]) > 59 || Number(parts[6] ?? 0) > 59 || Number(parts[8] ?? 0) > 23 || Number(parts[9] ?? 0) > 59) throw invalid();
  return text;
}
export function exportRequest(project: string, draft: ExportDraft, revision: number | null): ExportRequest {
  if (!project) throw new Error('请先选择导出的项目。');
  if (revision == null || !Number.isSafeInteger(revision) || revision < 0) throw new Error('请先读取项目的最新版本，再下载历史包。');
  if (!['all', 'exact', 'unknown'].includes(draft.scopeMode)) throw new Error('请选择有效的阅读范围。');
  const occurred = cutoff(draft.occurredUntil, '发生截止'); const known = cutoff(draft.knownUntil, '获知截止');
  const patterns = draft.patternsText.split('\n').filter(line => line.trim() !== '');
  if (patterns.length > 32 || patterns.some(line => [...line].length > 1000)) throw new Error('遮盖规则最多 32 条，每条最多 1000 字符。');
  const request: ExportRequest = { project_id: project, expected_revision: revision, include_evidence: draft.includeEvidence,
    ...(occurred ? { occurred_until: occurred } : {}), ...(known ? { known_until: known } : {}),
    ...(draft.scopeMode === 'exact' ? { scope: parseScopeText(draft.scopeText, false)! } : draft.scopeMode === 'unknown' ? { scope: null } : {}),
    ...(patterns.length ? { redact_patterns: patterns } : {}),
  };
  if (new TextEncoder().encode(JSON.stringify(request)).length > 65536) throw new Error('导出条件超过 65536 UTF-8 字节，请缩短范围或遮盖规则。');
  return request;
}
export function exportIntentKey(project: string, draft: ExportDraft): string { return JSON.stringify([project, draft.occurredUntil, draft.knownUntil, draft.scopeMode, draft.scopeText, draft.includeEvidence, draft.patternsText]); }
export function exportFilename(project: string, revision: number): string { return `researchgraph-history-${project.slice(0, 12).replace(/[^A-Za-z0-9_-]/g, '_')}-r${revision}.zip`; }
export function downloadArchive(blob: Blob, filename: string): () => void {
  const url = URL.createObjectURL(blob); let released = false; let timer: ReturnType<typeof setTimeout> | undefined;
  const release = () => { if (!released) { released = true; if (timer != null) clearTimeout(timer); URL.revokeObjectURL(url); } };
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename;
  try { document.body.append(anchor); anchor.click(); timer = setTimeout(release, 1000); }
  catch (error) { release(); throw error; }
  finally { anchor.remove(); }
  return release;
}
