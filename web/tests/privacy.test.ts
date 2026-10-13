import { describe, expect, it } from 'vitest';
import { parsePrivacyPolicy, privacyIntent, privacyPatterns, privacyRequest } from '../src/privacy';

const policy = { project_id: 'p', revision: 8, rule_id: 2, policy_id: 'a'.repeat(64), patterns: ['HOSP-\\d+'], builtin_masking: true, raw_unchanged: true };
describe('项目遮盖规则合同与意图隔离', () => {
  it('原样保留正则的空格、反斜杠和 Python 语法，空草稿明确移除额外规则', () => {
    expect(privacyPatterns(' HOSP-\\d+ \n\n(?i)case-\\d+\n  ')).toEqual([' HOSP-\\d+ ', '(?i)case-\\d+']);
    expect(privacyRequest('p', '', 'human:验收人', 8)).toEqual({ project_id: 'p', patterns: [], actor: 'human:验收人', expected_revision: 8 });
    expect(privacyRequest('p', '[', 'human:验收人', 8).patterns).toEqual(['[']); // 语法交给服务端，不以 JS 方言替代。
  });
  it('限制条目与 Unicode 字符数量，不允许未知项目版本或空人工身份提交', () => {
    expect(privacyPatterns('😀'.repeat(1000))).toHaveLength(1);
    for (const text of ['😀'.repeat(1001), Array(33).fill('x').join('\n')]) expect(() => privacyPatterns(text)).toThrow();
    for (const revision of [undefined, -1, 1.2, Infinity]) expect(() => privacyRequest('p', '', 'human:验收人', revision)).toThrow();
    for (const actor of ['model:验收', 'human:', 'human:   ']) expect(() => privacyRequest('p', '', actor, 8)).toThrow();
  });
  it('回执必须属于请求项目且保留内置遮盖和原文不改写保证，损坏回包不能覆盖草稿', () => {
    expect(parsePrivacyPolicy(policy, 'p')).toEqual(policy);
    for (const invalid of [null, { ...policy, project_id: 'other' }, { ...policy, raw_unchanged: false }, { ...policy, builtin_masking: false }, { ...policy, policy_id: 'bad' }, { ...policy, rule_id: -1 }, { ...policy, patterns: [4] }]) expect(() => parsePrivacyPolicy(invalid, 'p')).toThrow();
  });
  it('项目、原样规则和人工身份变化均创建不同意图，不借相同文本串错项目', () => {
    const current = privacyIntent('p', 'HOSP-\\d+', 'human:甲');
    for (const next of [privacyIntent('q', 'HOSP-\\d+', 'human:甲'), privacyIntent('p', 'HOSP-\\d+ ', 'human:甲'), privacyIntent('p', 'HOSP-\\d+', 'human:乙')]) expect(next).not.toBe(current);
  });
});
