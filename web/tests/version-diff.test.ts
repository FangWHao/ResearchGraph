import { describe, expect, it } from 'vitest';
import { emptyVersionDiffDraft, parseVersionDiff, versionDiffIntent, versionDiffReason, versionDiffRequest } from '../src/versionDiff';

const draft = { ...emptyVersionDiffDraft(), before: 'physical:before', after: 'physical:after', occurredUntil: '2026-10-10T12:00:00+08:00', knownUntil: '2026-10-10T04:00:00Z' };
const request = versionDiffRequest('project', draft, 7);
const card = { project_id: 'project', path: '/synthetic/file', root_id: 'root', source: 'shadow_snapshot', algo: 'git-sha1', digest: 'a'.repeat(40), content_sha256: 'b'.repeat(64), size: 10, representation: 'physical_file_bytes', claim_state: 'candidate', modes: ['100644'], content_verified: true };
const packet = {
  project_id: 'project', revision: 7, occurred_until: '2026-10-10T04:00:00+00:00', known_until: '2026-10-10T04:00:00+00:00',
  before: { ...card, version_id: 'physical:before' }, after: { ...card, version_id: 'physical:after' }, same_file: true, byte_identity: 'different', mode_changed: false, notice: '候选状态不变',
  diff: { available: true, reason: null, text: '-old\n+<script>unsafe</script>\n', complete: true, kind: 'unified_utf8', limits: { max_input_bytes: 64000, max_lines: 2000, max_diff_bytes: 64000 }, before_lines: 1, after_lines: 1, before_newlines: { lf: 1, crlf: 0, final_newline: true }, after_newlines: { lf: 0, crlf: 0, final_newline: false } },
};
describe('已保存物理版本比较的身份、时间和完整性', () => {
  it('方向和完整ID原样发送，无研究scope，不自动选择或交换版本', () => {
    expect(request).toEqual({ project: 'project', before_version_id: draft.before, after_version_id: draft.after, expected_revision: 7, occurred_until: draft.occurredUntil, known_until: draft.knownUntil });
    expect(versionDiffRequest('p', { ...draft, before: ' ID with space ' }, 0).before_version_id).toBe(' ID with space ');
    for (const next of [{ ...draft, before: draft.after, after: draft.before }, { ...draft, knownUntil: '' }, { ...draft, after: 'new' }]) expect(versionDiffIntent('project', next)).not.toBe(versionDiffIntent('project', draft));
    expect(versionDiffIntent('other', draft)).not.toBe(versionDiffIntent('project', draft));
  });
  it('未知revision、空选择及无时区或无效日期不得发起比较', () => {
    for (const revision of [null, -1, Infinity, 1.5]) expect(() => versionDiffRequest('p', draft, revision)).toThrow();
    for (const time of ['2026-10-10T12:00:00', '2026-02-30T12:00:00Z', '2026-01-01T24:00:00Z', '2026-01-01T10:00:00+24:00', '2026-01-01T10:00:00.1234567Z']) expect(() => versionDiffRequest('p', { ...draft, occurredUntil: time }, 1)).toThrow();
    expect(() => versionDiffRequest('p', { ...draft, before: ' ' }, 1)).toThrow();
  });
  it('微秒不同不能借毫秒精度误认双截止；相同时刻不同时区可以核对', () => {
    const precise = versionDiffRequest('project', { ...draft, occurredUntil: '2026-10-10T12:00:00.123456+08:00', knownUntil: '2026-10-10T04:00:00.000001Z' }, 7);
    const matching = { ...packet, occurred_until: '2026-10-10T04:00:00.123456Z', known_until: '2026-10-10T12:00:00.000001+08:00' };
    expect(parseVersionDiff(matching, precise).diff.available).toBe(true);
    for (const mismatch of [{ ...matching, occurred_until: '2026-10-10T04:00:00.123457Z' }, { ...matching, known_until: '2026-10-10T04:00:00.000002Z' }]) expect(() => parseVersionDiff(mismatch, precise)).toThrow();
  });
  it('工具报告、缺摘要、矛盾模式或另一版本的观察不能被展示成已校验保存证明', () => {
    for (const bad of [{ source: 'tool_report' }, { algo: 'sha256' }, { digest: 'wrong' }, { content_sha256: null }, { size: -1 }, { modes: ['100644', '100755'] }, { modes: ['120000'] }, { representation: 'tool_text' }, { saved_observation: { version_id: 'another', mode: '100644' } }, { saved_observation: { version_id: packet.before.version_id, mode: '100755' } }]) expect(() => parseVersionDiff({ ...packet, before: { ...packet.before, ...bad } }, request)).toThrow();
    const unavailable = { ...packet, before: { ...packet.before, content_verified: false, modes: ['100644', '100755'] }, byte_identity: 'unverified', mode_changed: null, diff: { ...packet.diff, available: false, text: null, complete: false, reason: 'invalid_provenance' } };
    expect(parseVersionDiff(unavailable, request).before.modes).toEqual(['100644', '100755']);
    expect(() => parseVersionDiff({ ...unavailable, before: { ...unavailable.before, saved_observation: { version_id: 'another' } } }, request)).toThrow();
    expect(() => parseVersionDiff({ ...packet, before: { ...packet.before, size: 64000 } }, request)).toThrow();
  });
  it('绑定项目、版本、双截止、修订与同一文件，不能把另一方向或未来响应当当前结果', () => {
    expect(parseVersionDiff(packet, request).before.claim_state).toBe('candidate');
    for (const broken of [{ ...packet, project_id: 'other' }, { ...packet, revision: 8 }, { ...packet, occurred_until: '2027-01-01T00:00:00Z' }, { ...packet, before: packet.after }, { ...packet, same_file: false }, { ...packet, after: { ...packet.after, root_id: 'other' } }]) expect(() => parseVersionDiff(broken, request)).toThrow();
  });
  it('单侧验证不证明字节相同，模式变化与字节身份独立，候选保持候选', () => {
    const unavailable = { ...packet, byte_identity: 'unverified', after: { ...packet.after, content_verified: false }, diff: { ...packet.diff, available: false, complete: false, text: null, reason: 'object_integrity' } };
    expect(parseVersionDiff(unavailable, request).before.content_verified).toBe(true);
    expect(parseVersionDiff(unavailable, request).after.content_verified).toBe(false);
    expect(parseVersionDiff({ ...unavailable, mode_changed: null }, request).mode_changed).toBeNull();
    expect(parseVersionDiff({ ...unavailable, mode_changed: undefined }, request).mode_changed).toBeNull();
    expect(() => parseVersionDiff({ ...unavailable, byte_identity: 'same' }, request)).toThrow();
    expect(parseVersionDiff({ ...packet, after: { ...packet.after, modes: ['100755'] }, byte_identity: 'same', mode_changed: true, diff: { ...packet.diff, text: '' } }, request).mode_changed).toBe(true);
  });
  it('超限、部分或不可用正文不伪装完整，HTML和行尾按原文本保留', () => {
    expect(parseVersionDiff(packet, request).diff.text).toBe(packet.diff.text);
    for (const diff of [{ ...packet.diff, complete: false }, { ...packet.diff, text: '中'.repeat(22000) }, { ...packet.diff, after_lines: 2001 }, { ...packet.diff, after_newlines: null }, { ...packet.diff, available: false, reason: 'binary' }]) expect(() => parseVersionDiff({ ...packet, diff }, request)).toThrow();
    const unknown = { ...packet, diff: { ...packet.diff, available: false, text: null, complete: false, reason: 'future_reason' } };
    expect(parseVersionDiff(unknown, request).diff.reason).toBe('future_reason'); expect(versionDiffReason('future_reason')).toContain('future_reason');
  });
});
