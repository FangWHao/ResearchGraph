export interface PrivacyPolicy {
  project_id: string; revision: number; rule_id: number; policy_id: string;
  patterns: string[]; builtin_masking: true; raw_unchanged: true;
}
export function privacyPatterns(text: string): string[] {
  const patterns = text.split('\n').filter(line => line.trim() !== '');
  if (patterns.length > 32 || patterns.some(line => [...line].length > 1000)) throw new Error('项目遮盖规则最多 32 条，每条最多 1000 字符。');
  return patterns;
}
export function parsePrivacyPolicy(value: unknown, project: string): PrivacyPolicy {
  const invalid = () => new Error('项目遮盖配置回执格式异常，请重新读取；当前草稿已保留。');
  if (!value || typeof value !== 'object') throw invalid();
  const policy = value as PrivacyPolicy;
  if (policy.project_id !== project || !Number.isSafeInteger(policy.revision) || policy.revision < 0
    || !Number.isSafeInteger(policy.rule_id) || policy.rule_id < 0 || typeof policy.policy_id !== 'string' || !/^[a-f0-9]{64}$/.test(policy.policy_id)
    || policy.builtin_masking !== true || policy.raw_unchanged !== true || !Array.isArray(policy.patterns)
    || policy.patterns.length > 32 || policy.patterns.some(p => typeof p !== 'string' || !p || [...p].length > 1000)) throw invalid();
  return policy;
}
export function privacyRequest(project: string, text: string, actor: string, revision: number | undefined) {
  if (!project || revision == null || !Number.isSafeInteger(revision) || revision < 0) throw new Error('请先读取当前项目的遮盖配置，再保存。');
  if (!actor.startsWith('human:') || !actor.slice(6).trim() || [...actor].length > 120) throw new Error('请填写有效的复核者姓名，再保存项目规则。');
  return { project_id: project, patterns: privacyPatterns(text), actor, expected_revision: revision };
}
export function privacyIntent(project: string, text: string, actor: string): string { return JSON.stringify([project, text, actor]); }
