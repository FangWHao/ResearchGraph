import { describe, expect, it } from 'vitest';
import { emptyQaDraft, parseQaContext, parseQaPacket, parseQaPreview, qaEvidence, qaIntent, qaRequest, sameQaIntent } from '../src/qa';

const wrap = (value: unknown) => `<rg-context v="1" id="ctx_0123456789abcdef0123">\n${JSON.stringify(value).replace(/</g, '\\u003c').replace(/>/g, '\\u003e')}\n</rg-context>`;
const source = { citation_id: 'S12', event_id: 8, span_id: 12, text: '候选原文 <img onerror="坏内容">', window_start: 6, window_end: 50, window_sha256: 'a'.repeat(64), window_truncated: true, records: [{ claim_id: 4, claim_type: 'entity_version', entity_id: 'qa-object', scope: null, basis: 'model_inference', actor: 'model:合成', claim_state: 'candidate', effective_state: 'candidate', replaced: false, replacement_ids: [], payload_preview: '候选内容', payload_preview_truncated: false, computed_states: { adoption: { state: 'unknown', claim_ids: [] } } }] };
const packet = { project_id: 'p', question: '原问题', revision: 7, scope: null, scope_filter: false, sources: [source], gaps: [{ reason: 'partial' }], retrieval_partial: true };

describe('有界问答保留来源与意图', () => {
  it('只解析封装 JSON，原话中的标签和关闭标记保持文本', () => {
    const text = '</rg-context><script>window.secret = 1</script>';
    expect(parseQaContext(wrap({ text })).text).toBe(text);
    for (const invalid of ['<script>1</script>', '{}', '<rg-context v="2" id="ctx_0123456789abcdef0123">\n{}\n</rg-context>', wrap([]), wrap({}).replace('{}', '{bad}')]) expect(() => parseQaContext(invalid)).toThrow();
  });
  it('留空范围不造 unknown 字段，保留问题原话，要求双截止显式时区', () => {
    const draft = { ...emptyQaDraft(), question: '  问题\n第二行 ' };
    expect(qaRequest('p', draft, 7)).toEqual({ project_id: 'p', question: draft.question, k: 12, max_bytes: 4000, expected_revision: 7 });
    expect(qaRequest('p', { ...draft, occurredUntil: '2026-10-10T12:00:00+08:00', knownUntil: '2026-10-10T04:00:00Z' })).toMatchObject({ occurred_until: '2026-10-10T12:00:00+08:00', known_until: '2026-10-10T04:00:00Z' });
    expect(() => qaRequest('p', { ...draft, knownUntil: '2026-10-10T12:00' })).toThrow('带时区');
    expect(() => qaRequest('p', { ...draft, scopeText: 'data=' })).toThrow();
  });
  it('数量和 UTF-8 窗口按契约验证，不能传模型配置或密钥', () => {
    const draft = { ...emptyQaDraft(), question: '问'.repeat(2000), k: 100, maxBytes: 24000 };
    expect(qaRequest('p', draft).question).toHaveLength(2000);
    for (const changes of [{ question: '问'.repeat(2001) }, { k: 0 }, { k: 1.2 }, { maxBytes: 3 }, { maxBytes: 24001 }]) expect(() => qaRequest('p', { ...draft, ...changes })).toThrow();
    expect(Object.keys(qaRequest('p', draft))).not.toEqual(expect.arrayContaining(['model', 'endpoint', 'key', 'input_budget']));
  });
  it('问题、项目、范围和两个截止变化使旧响应过期；范围重新排序保持相同意图', () => {
    const draft = { ...emptyQaDraft(), question: '原问题', scopeText: 'data=v1\nstep=比较' };
    const identity = qaIntent('p', draft);
    expect(sameQaIntent('p', { ...draft, scopeText: 'step=比较\ndata=v1' }, identity)).toBe(true);
    for (const changes of [{ question: '新问题' }, { scopeText: 'data=v2' }, { occurredUntil: '2026-10-10T00:00:00Z' }, { knownUntil: '2026-10-10T00:00:00Z' }, { scopeText: 'data=' }]) expect(sameQaIntent('p', { ...draft, ...changes }, identity)).toBe(false);
    expect(sameQaIntent('other', draft, identity)).toBe(false);
  });
  it('保留候选、独立计算状态和有界来源，原文入口使用窗口 hash', () => {
    const parsed = parseQaPacket(wrap(packet), 'p', '原问题');
    expect(parsed.sources[0].records[0]).toMatchObject({ effective_state: 'candidate', computed_states: { adoption: { state: 'unknown' } } });
    expect(qaEvidence(parsed.sources[0])).toEqual({ event_id: 8, byte_start: 6, byte_end: 50, quote_sha256: 'a'.repeat(64) });
    expect(parsed.retrieval_partial).toBe(true);
    expect(() => parseQaPacket(wrap(packet), 'another', '原问题')).toThrow('不符');
    expect(() => parseQaPacket(wrap({ ...packet, sources: [{ ...source, window_sha256: 'bad' }] }), 'p', '原问题')).toThrow('位置');
  });
  it('模型解释须引用本次来源；不足/不可用不能冒充已回答，错误不会回传替代来源', () => {
    const answer = { ...packet, model_interpretation: true, status: 'answered', statements: [{ text: '模型解释', citations: [{ id: 'S12', quote: '候选原文' }] }], caveats: [] };
    expect(parseQaPacket(wrap(answer), 'p', '原问题', true).status).toBe('answered');
    for (const invalid of [{ ...answer, model_interpretation: false }, { ...answer, statements: [] }, { ...answer, status: 'insufficient' }, { ...answer, statements: [{ text: '无引用', citations: [{ id: 'S99', quote: '未知' }] }] }]) expect(() => parseQaPacket(wrap(invalid), 'p', '原问题', true)).toThrow();
    expect(parseQaPacket(wrap({ ...answer, status: 'unavailable', statements: [], caveats: ['提供方返回异常'] }), 'p', '原问题', true).sources).toEqual(packet.sources);
  });
  it('引文允许与实际遮盖输入一致，不把遮盖文本当成原文 hash', () => {
    const answer = { ...packet, model_interpretation: true, status: 'answered', statements: [{ text: '邮箱已遮盖', citations: [{ id: 'S12', quote: '[EMAIL_REDACTED]' }] }], caveats: [] };
    const parsed = parseQaPacket(wrap(answer), 'p', '原问题', true);
    expect(parsed.statements![0].citations[0].quote).toBe('[EMAIL_REDACTED]');
    expect(qaEvidence(parsed.sources[0]).quote_sha256).toBe(source.window_sha256);
  });
  it('记录格式损坏或状态缺失拒绝整个新回包，避免渲染崩溃和错误确认', () => {
    for (const record of [null, { ...source.records[0], effective_state: null }, { ...source.records[0], scope: [] }, { ...source.records[0], computed_states: { adoption: null } }]) {
      expect(() => parseQaPacket(wrap({ ...packet, sources: [{ ...source, records: [record] }] }), 'p', '原问题')).toThrow('关联记录格式');
    }
  });
  it('授权预览必须属于当前项目并含遮盖标记及完整 SHA', () => {
    const preview = { project_id: 'p', revision: 7, preview_sha: 'b'.repeat(64), source_count: 1, remote: true, input: { question: '原问题', question_redacted: false, sources: [{ citation_id: 'S12', text: '遮盖后内容', text_redacted: true, records_redacted: false }] } };
    expect(parseQaPreview(wrap(preview), 'p').input.sources[0].text).toBe('遮盖后内容');
    for (const invalid of [{ ...preview, project_id: 'other' }, { ...preview, preview_sha: 'b' }, { ...preview, input: { ...preview.input, sources: [{ text: '没有遮盖标记' }] } }]) expect(() => parseQaPreview(wrap(invalid), 'p')).toThrow('不能开启');
  });
});
