import { expect, test } from '@playwright/test';
import { openWorkspaceView } from './navigation';
import type { Project } from '../../src/types';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
test('文件版本下游只读核对报告依赖，路径分页沿第一双截止且不改科研状态', async ({ page, request }) => {
  const projects: Project[] = (await (await request.get('/api/projects', { headers })).json()).projects;
  const project = projects.find(item => item.name === '验收运行清单项目')!;
  const before = await (await request.get(`/api/graph?project=${project.project_id}`, { headers })).json();
  const writes: string[] = []; const calls: URL[] = [];
  page.on('request', event => { const url = new URL(event.url()); if (url.pathname.startsWith('/api/') && event.method() !== 'GET') writes.push(url.pathname); if (url.pathname === '/api/impact') calls.push(url); });
  await page.goto('/?view=versions#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption(project.project_id);
  const row = page.locator('[data-file-version-id="synthetic-manifest-version:input"]');
  await expect(row).toBeVisible();
  expect(calls).toHaveLength(0);
  await row.getByText('下游影响 · 只读核对', { exact: true }).click();
  const panel = row.locator('.impact-panel');
  await expect(panel.getByLabel('包含报告依赖（潜在）')).toBeChecked();
  await panel.getByRole('button', { name: '读取下游核对项', exact: true }).click();
  const result = panel.getByRole('region', { name: '下游影响结果' });
  await expect(result).toContainText('潜在依赖');
  await expect(result).toContainText('实际 I/O 仍未知');
  await expect(result.locator('.impact-results > li')).not.toHaveCount(0);
  await panel.getByRole('button', { name: '核对关系路径', exact: true }).click();
  await panel.getByRole('button', { name: '读取关系路径', exact: true }).click();
  await expect(result.locator('.impact-results > li')).toHaveCount(20);
  await panel.getByRole('button', { name: '读取更多下游路径', exact: true }).click();
  await expect(result.locator('.impact-results > li')).toHaveCount(40);
  expect(calls).toHaveLength(3);
  expect(calls.every(url => url.searchParams.get('project') === project.project_id && url.searchParams.get('version_id') === 'synthetic-manifest-version:input' && url.searchParams.get('include_reported') === 'true')).toBe(true);
  expect(calls[1].searchParams.get('known_until')).toBeTruthy();
  expect(calls[2].searchParams.get('known_until')).toBe(calls[1].searchParams.get('known_until'));
  expect(calls[2].searchParams.get('occurred_until')).toBe(calls[1].searchParams.get('occurred_until'));
  expect(calls[2].searchParams.get('offset')).toBe('20');
  expect(writes).toEqual([]);
  expect(await (await request.get(`/api/graph?project=${project.project_id}`, { headers })).json()).toEqual(before);
});

test('切换项目后忽略已离开详情的迟到鉴权错误，不重试或退出当前工作区', async ({ page, request }) => {
  const projects: Project[] = (await (await request.get('/api/projects', { headers })).json()).projects;
  const primary = projects.find(item => item.name === '合成研究 · 界面信号验证')!;
  const empty = projects.find(item => item.name === '合成空项目')!;
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = (input, init) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof Request ? input.url : input.toString(), location.href);
      if (url.pathname !== '/api/impact') return original(input, init);
      return original(input, { ...init, signal: undefined }).then(response => {
        const json = response.json.bind(response);
        response.json = async () => { const value = await json(); setTimeout(() => { (window as typeof window & { impactSettled?: boolean }).impactSettled = true; }, 0); return value; };
        return response;
      });
    };
  });
  let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; });
  let count = 0; let observed!: () => void; const started = new Promise<void>(resolve => { observed = resolve; });
  await page.route('**/api/impact?**', async route => { count++; const original = await route.fetch(); expect(original.status()).toBe(200); observed(); await held; await route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: '合成迟到鉴权错误' }) }); });
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption(primary.project_id);
  await page.getByRole('button', { name: '查看记录与证据', exact: true }).click();
  const drawer = page.locator('.detail-drawer');
  await drawer.getByText('下游影响 · 只读核对', { exact: true }).click();
  await drawer.getByRole('button', { name: '读取下游核对项', exact: true }).click();
  await started;
  await page.getByLabel('当前项目', { exact: true }).selectOption(empty.project_id);
  await expect(drawer).toHaveCount(0);
  release();
  await expect.poll(() => page.evaluate(() => (window as typeof window & { impactSettled?: boolean }).impactSettled)).toBe(true);
  await expect(page.getByRole('navigation', { name: '研究工作区', exact: true })).toBeVisible();
  await expect(page.getByText('合成迟到鉴权错误', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('region', { name: '下游影响结果' })).toHaveCount(0);
  await openWorkspaceView(page, '研究问答');
  await expect(page.getByRole('heading', { name: '研究问答', exact: true })).toBeVisible();
  expect(count).toBe(1);
});
