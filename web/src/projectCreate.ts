import type { Project } from './types';

export interface ProjectCreateDraft { name: string; root: string; collectLocal: boolean }
export interface ProjectCreateRequest { name: string; root: string; collect_local: boolean }
export interface ProjectSource { tool: 'claude' | 'codex'; path: string; state: 'registered' | 'missing' | 'unavailable' }
export interface ProjectCreateResult { project: Project; created: boolean; sources: ProjectSource[]; revision: number }
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0;
function project(v: unknown): v is Project { return object(v) && text(v.project_id) && text(v.name) && integer(v.sessions) && integer(v.claims) && [0, 1].includes(v.remote_model_allowed as number); }
export function projectCreateRequest(draft: ProjectCreateDraft): ProjectCreateRequest {
  const name = draft.name.trim();
  if (!name || /[\u0000-\u001f\u007f]/.test(name)) throw new Error('请填写有效的项目名称。');
  if (!draft.root || /[\u0000-\u001f\u007f]/.test(draft.root) || !(/^(?:\/|[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+)/.test(draft.root))) throw new Error('请填写服务运行这台机器上的绝对工作区目录。');
  return { name, root: draft.root, collect_local: draft.collectLocal };
}
export function parseProjectList(raw: unknown): Project[] {
  if (!object(raw) || !Array.isArray(raw.projects) || raw.projects.some(item => !project(item))
    || new Set(raw.projects.map(item => item.project_id)).size !== raw.projects.length) throw new Error('项目列表响应不完整，无法判断是否已有项目；请重新读取。');
  return raw.projects as Project[];
}
export function parseProjectCreated(raw: unknown, request: ProjectCreateRequest): ProjectCreateResult {
  if (!object(raw) || !project(raw.project) || raw.project.name !== request.name || typeof raw.created !== 'boolean' || !integer(raw.revision)
    || !Array.isArray(raw.sources) || raw.sources.some(item => !object(item) || !['claude', 'codex'].includes(String(item.tool)) || !text(item.path) || !['registered', 'missing', 'unavailable'].includes(String(item.state)))
    || new Set(raw.sources.map(item => item.tool)).size !== raw.sources.length || (!request.collect_local && raw.sources.length > 0)) throw new Error('创建回执不完整或与提交不一致，请重新读取项目列表核对保存结果。');
  return raw as unknown as ProjectCreateResult;
}
