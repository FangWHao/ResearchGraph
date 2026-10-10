import type { ArtifactObservation, FileVersionRecord } from './types';
import { objectFields } from './versions';

export interface VersionDiffDraft { before: string; after: string; occurredUntil: string; knownUntil: string }
export interface VersionDiffRequest { project: string; before_version_id: string; after_version_id: string; expected_revision: number; occurred_until?: string; known_until?: string }
export interface DiffVersion extends FileVersionRecord { version_id: string; project_id: string; path: string; modes: string[]; content_verified: boolean;
  saved_observation?: Partial<ArtifactObservation> & { occurred_at?: string | null; snapshot?: Record<string, unknown> | null; discovery_snapshot?: Record<string, unknown> | null } | null;
}
export interface DiffNewlines { lf: number; crlf: number; final_newline: boolean }
export interface VersionDiffResult {
  project_id: string; revision: number; occurred_until: string; known_until: string; before: DiffVersion; after: DiffVersion;
  same_file: true; byte_identity: 'unverified' | 'same' | 'different'; mode_changed: boolean | null; notice: string;
  diff: { available: boolean; reason: string | null; text: string | null; complete: boolean; kind: 'unified_utf8';
    limits: { max_input_bytes: number; max_lines: number; max_diff_bytes: number }; before_lines: number | null; after_lines: number | null;
    before_newlines: DiffNewlines | null; after_newlines: DiffNewlines | null };
}
export function emptyVersionDiffDraft(): VersionDiffDraft { return { before: '', after: '', occurredUntil: '', knownUntil: '' }; }
function cutoff(value: string, name: string): string | undefined {
  const text = value.trim(); if (!text) return undefined;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?(Z|[+-](\d{2}):(\d{2}))$/.exec(text);
  const fail = () => new Error(`${name}需填写有效且带时区的 ISO 时间，小数秒最多6位，例如 2026-10-10T12:00:00+08:00。`);
  if (!parts || !Number.isFinite(Date.parse(text))) throw fail();
  const y = Number(parts[1]), m = Number(parts[2]), d = Number(parts[3]);
  const days = [31, y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (y < 1 || m < 1 || m > 12 || d < 1 || d > days[m - 1] || Number(parts[4]) > 23 || Number(parts[5]) > 59 || Number(parts[6] ?? 0) > 59 || Number(parts[8] ?? 0) > 23 || Number(parts[9] ?? 0) > 59) throw fail();
  return text;
}
function microseconds(text: string): bigint {
  const fraction = /\.(\d{1,6})(?=Z|[+-]\d{2}:\d{2}$)/.exec(text);
  const whole = fraction ? text.replace(fraction[0], '') : text;
  return BigInt(Date.parse(whole)) * 1000n + BigInt((fraction?.[1] ?? '').padEnd(6, '0'));
}
export function versionDiffRequest(project: string, draft: VersionDiffDraft, revision: number | null): VersionDiffRequest {
  if (!project || !draft.before.trim() || !draft.after.trim()) throw new Error('请明确选择或输入两侧完整版本标识。');
  if (revision == null || !Number.isSafeInteger(revision) || revision < 0) throw new Error('请先读取当前项目的版本列表。');
  const occurred = cutoff(draft.occurredUntil, '发生截止'), known = cutoff(draft.knownUntil, '获知截止');
  return { project, before_version_id: draft.before, after_version_id: draft.after, expected_revision: revision,
    ...(occurred ? { occurred_until: occurred } : {}), ...(known ? { known_until: known } : {}) };
}
export function versionDiffIntent(project: string, draft: VersionDiffDraft): string { return JSON.stringify([project, draft.before, draft.after, draft.occurredUntil, draft.knownUntil]); }
function count(v: unknown): v is number { return Number.isSafeInteger(v) && (v as number) >= 0; }
function newlines(v: unknown): boolean {
  if (v === null) return true; const n = objectFields(v);
  return !!n && count(n.lf) && count(n.crlf) && typeof n.final_newline === 'boolean';
}
function version(v: unknown, id: string, project: string): v is DiffVersion {
  const card = objectFields(v);
  const observed = objectFields(card?.saved_observation);
  return !!card && card.version_id === id && card.project_id === project && typeof card.path === 'string'
    && !!card.path
    && typeof card.root_id === 'string' && !!card.root_id
    && ['source', 'algo', 'digest', 'representation', 'claim_state'].every(key => card[key] == null || typeof card[key] === 'string')
    && ['occurred_at', 'recorded_at'].every(key => card[key] == null || typeof card[key] === 'string')
    && (card.provenance_warnings == null || Array.isArray(card.provenance_warnings) && card.provenance_warnings.every(w => typeof w === 'string'))
    && (card.saved_observation == null || !!observed && observed.version_id === id)
    && Array.isArray(card.modes) && card.modes.every(m => typeof m === 'string') && typeof card.content_verified === 'boolean';
}
function physicalProof(card: DiffVersion): boolean {
  return card.source === 'shadow_snapshot' && card.algo === 'git-sha1'
    && /^[a-f0-9]{40}$/.test(card.digest ?? '') && /^[a-f0-9]{64}$/.test(card.content_sha256 ?? '') && count(card.size)
    && card.modes.length === 1
    && (card.representation === 'physical_file_bytes' && ['100644', '100755'].includes(card.modes[0])
      || card.representation === 'symlink_target_bytes' && card.modes[0] === '120000')
    && (card.saved_observation == null || card.saved_observation.mode === card.modes[0]);
}
export function parseVersionDiff(value: unknown, request: VersionDiffRequest): VersionDiffResult {
  const v = objectFields(value), d = objectFields(v?.diff), limits = objectFields(d?.limits);
  const invalid = () => new Error('文件差异回包的身份、时间或完整性异常，未展示正文；请重新读取版本并比较。');
  if (!v || !d || !limits || v.project_id !== request.project || v.revision !== request.expected_revision
    || typeof v.occurred_until !== 'string' || !cutoff(v.occurred_until, '回包发生截止') || typeof v.known_until !== 'string' || !cutoff(v.known_until, '回包获知截止')
    || (request.occurred_until && microseconds(v.occurred_until) !== microseconds(request.occurred_until)) || (request.known_until && microseconds(v.known_until) !== microseconds(request.known_until))
    || !version(v.before, request.before_version_id, request.project) || !version(v.after, request.after_version_id, request.project)
    || v.same_file !== true || v.before.path !== v.after.path || v.before.root_id !== v.after.root_id
    || !['unverified', 'same', 'different'].includes(String(v.byte_identity)) || (v.mode_changed != null && typeof v.mode_changed !== 'boolean') || typeof v.notice !== 'string'
    || typeof d.available !== 'boolean' || typeof d.complete !== 'boolean' || d.kind !== 'unified_utf8'
    || limits.max_input_bytes !== 64000 || limits.max_lines !== 2000 || limits.max_diff_bytes !== 64000
    || (d.before_lines !== null && !count(d.before_lines)) || (d.after_lines !== null && !count(d.after_lines)) || !newlines(d.before_newlines) || !newlines(d.after_newlines)) throw invalid();
  if ([v.before, v.after].some(card => card.content_verified && !physicalProof(card))
    || v.byte_identity !== 'unverified' && (!v.before.content_verified || !v.after.content_verified)
    || v.mode_changed != null && (!physicalProof(v.before) || !physicalProof(v.after)
      || v.mode_changed !== (v.before.modes[0] !== v.after.modes[0]))) throw invalid();
  if (d.available) {
    if (!d.complete || typeof d.text !== 'string' || d.reason !== null || !v.before.content_verified || !v.after.content_verified || v.byte_identity === 'unverified'
      || d.before_lines === null || d.after_lines === null || (d.before_lines as number) > 2000 || (d.after_lines as number) > 2000
      || d.before_newlines === null || d.after_newlines === null || v.before.representation !== v.after.representation
      || (v.before.size as number) + (v.after.size as number) > 64000 || new TextEncoder().encode(d.text).length > 64000) throw invalid();
  } else if (d.text !== null || typeof d.reason !== 'string' || !d.reason) throw invalid();
  return { ...v, mode_changed: typeof v.mode_changed === 'boolean' ? v.mode_changed : null } as unknown as VersionDiffResult;
}
const reasons: Record<string, string> = {
  unsupported_source: '仅影子快照保存的字节可比较；工具报告和当前文件哈希观察不能代替保存正文。', missing_saved_content: '正文没有保存，无法从摘要还原差异；不会补读当前文件。',
  invalid_provenance: '来源观察或快照证明不完整，无法核对已保存字节。', input_byte_limit: '两侧原字节合计超过 64000 字节，不截取片段冒充完整差异。',
  object_unavailable: '已登记的内容对象不可读取，不能判断差异。', object_integrity: '内容对象的完整性校验失败，未展示正文。', representation_changed: '两侧字节表示不同，不能将链接目标文字与普通文件正文混合比较。',
  binary: '内容包含二进制数据，不展示 UTF-8 文本差异。', invalid_utf8: '内容不是有效 UTF-8，不替换非法字节后生成文本差异。', line_limit: '至少一侧超过 2000 行，不截断显示。',
  diff_byte_limit: '完整差异超过 64000 UTF-8 字节，不截断显示。',
};
export function versionDiffReason(reason: string | null): string { return reason ? reasons[reason] ?? `无法展示差异，服务端原因：${reason}` : '未提供原因，无法判断差异。'; }
