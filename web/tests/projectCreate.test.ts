import { describe, expect, it } from 'vitest';
import { parseProjectCreated, parseProjectList, projectCreateRequest } from '../src/projectCreate';

const project = { project_id: 'synthetic-created-project', name: '合成研究', remote_model_allowed: 0, sessions: 0, claims: 0 };
const request = { name: project.name, root: '/synthetic/workspace with spaces ', collect_local: false };
describe('本机项目创建与读取失败边界', () => {
  it('只在确实收到完整空列表后判断没有项目，不把缺失、损坏或重复列表当空', () => {
    expect(parseProjectList({ projects: [] })).toEqual([]);
    expect(parseProjectList({ projects: [project] })).toEqual([project]);
    for (const value of [null, {}, { projects: null }, { projects: [null] }, { projects: [project, project] }, { projects: [{ ...project, sessions: -1 }] }]) expect(() => parseProjectList(value)).toThrow();
  });
  it('名称 trim，目录空格按字面保留，采集必须显式勾选，接受服务的Windows或WSL路径', () => {
    expect(projectCreateRequest({ name: ' 合成研究 ', root: request.root, collectLocal: false })).toEqual(request);
    for (const root of ['/mnt/d/Atlas', 'D:\\研究目录', '\\\\server\\research']) expect(projectCreateRequest({ name: project.name, root, collectLocal: true })).toMatchObject({ root, collect_local: true });
    for (const root of ['', 'relative/path', 'D:relative', '/synthetic\nother']) expect(() => projectCreateRequest({ name: project.name, root, collectLocal: false })).toThrow();
  });
  it('重复创建和来源不可用仍保留已保存项目，身份错误或回执缺失必须拒绝', () => {
    const result = { project, created: true, sources: [], revision: 1 };
    expect(parseProjectCreated(result, request).project.project_id).toBe(project.project_id);
    expect(parseProjectCreated({ ...result, created: false, project: { ...project, sessions: 3, claims: 4 } }, request).created).toBe(false);
    expect(parseProjectCreated({ ...result, sources: [{ tool: 'claude', path: '/synthetic/home/.claude/projects', state: 'unavailable' }, { tool: 'codex', path: '/synthetic/home/.codex/sessions', state: 'missing' }] }, { ...request, collect_local: true }).project).toEqual(project);
    for (const patch of [{ project: { ...project, name: 'other' } }, { project: { ...project, project_id: '' } }, { revision: null }, { created: 'true' }, { sources: null }, { sources: [{ tool: 'codex', path: '/synthetic/home/.codex/sessions', state: 'registered' }] }]) expect(() => parseProjectCreated({ ...result, ...patch }, request)).toThrow();
  });
});
