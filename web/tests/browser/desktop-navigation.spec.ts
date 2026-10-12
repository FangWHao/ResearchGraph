import { mkdirSync, writeFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { openWorkspaceView } from './navigation';

const proof = '../.cache/desktop-simple-20261013';
const headers = { Authorization: 'Bearer synthetic-browser-token' };
mkdirSync(proof, { recursive: true });
const daily = ['研究问题', '研究图', '研究问答'];
const tools = ['研究笔记', '复核队列', '决定时间线', '文件版本', '文件运行图', '历史导出', '采集健康', '项目清除'];

test('桌面默认只有三个日常入口，候选提示可见，原文搜索和研究图问答仍可直接使用', async ({ page, request }) => {
  const projects = await (await request.get('/api/projects', { headers })).json();
  const project = projects.projects.find((item: { name: string }) => item.name === '合成研究 · 界面信号验证');
  const original = await (await request.get(`/api/graph?project=${project.project_id}`, { headers })).json();
  const candidates = original.claims.filter((item: { effective_state: string }) => item.effective_state === 'candidate').length;
  expect(candidates).toBeGreaterThan(0);
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption(project.project_id);
  const navigation = page.getByRole('navigation', { name: '研究工作区', exact: true });
  await expect(navigation.locator('.workspace-primary button')).toHaveText(daily);
  await expect(navigation.getByRole('button', { name: '更多工具', exact: true })).toHaveAttribute('aria-expanded', 'false');
  await expect(navigation.locator('.workspace-tools')).toBeHidden();
  await expect(navigation.locator('.nav-more-count')).toHaveText(`${candidates} 待复核`);
  await expect(navigation.locator('.nav-more-count')).toBeInViewport();
  await expect(page.getByLabel('搜索会话原文', { exact: true })).toBeVisible();
  await page.screenshot({ path: `${proof}/frontend-navigation-default-desktop.png` });
  for (const name of ['研究图', '研究问答', '研究问题']) {
    await navigation.getByRole('button', { name, exact: true }).click();
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
    await expect(navigation.getByRole('button', { name, exact: true })).toHaveAttribute('aria-current', 'page');
    await expect(navigation.locator('.workspace-tools')).toBeHidden();
    if (name === '研究图') await expect(page.locator('[data-research-summary]')).toContainText('双时间内全部 L2 登记记录');
    if (name === '研究问答') await expect(page.getByLabel('研究问答问题', { exact: true })).toBeVisible();
  }
  await page.getByLabel('搜索会话原文', { exact: true }).fill('界面分层分析');
  const searched = page.waitForResponse(value => new URL(value.url()).pathname === '/api/search');
  await page.getByRole('button', { name: '开始搜索', exact: true }).click(); expect((await searched).status()).toBe(200);
  await expect(page.getByRole('heading', { name: '原文检索', exact: true })).toBeVisible();
  await expect(page.locator('.search-result').first()).toContainText('界面分层分析');
  expect(await (await request.get(`/api/graph?project=${project.project_id}`, { headers })).json()).toEqual(original);
  writeFileSync(`${proof}/frontend-navigation-default-api.json`, JSON.stringify({ project: project.project_id, candidates, graph_unchanged: true }, null, 2));
});

test('更多工具保留全部低频页面和复核入口，展开可滚动且当前页面仍明确', async ({ page }) => {
  await page.goto('/#token=synthetic-browser-token');
  const navigation = page.getByRole('navigation', { name: '研究工作区', exact: true });
  await expect(navigation.getByRole('button', { name: '更多工具', exact: true })).toHaveAttribute('aria-expanded', 'false');
  for (const name of tools) {
    await openWorkspaceView(page, name);
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
    await expect(navigation.getByRole('button', { name, exact: true })).toHaveAttribute('aria-current', 'page');
    await expect(navigation.getByRole('button', { name: '更多工具', exact: true })).toHaveAttribute('aria-expanded', 'true');
  }
  await openWorkspaceView(page, '复核队列'); await expect(page.locator('.queue-item').first()).toBeVisible();
  await navigation.getByRole('button', { name: '更多工具', exact: true }).click();
  await expect(navigation.locator('.workspace-tools')).toBeHidden();
  await expect(navigation.locator('.current-tool')).toHaveText('复核队列');
  await expect(navigation.locator('.nav-more-count')).toBeVisible();
  await navigation.getByRole('button', { name: '更多工具', exact: true }).click();
  await page.getByLabel('复核者姓名', { exact: true }).fill('公开桌面复核者');
  await expect(page.getByLabel('复核者姓名', { exact: true })).toHaveValue('公开桌面复核者');
  await navigation.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${proof}/frontend-navigation-tools-desktop.png` });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth); expect(overflow).toBe(false);
});

test('低频视图深链自动展开并高亮，鉴权恢复后仍可到达相同页面', async ({ page }) => {
  await page.goto('/?view=versions#token=synthetic-browser-token');
  const navigation = page.getByRole('navigation', { name: '研究工作区', exact: true });
  await expect(page.getByRole('heading', { name: '文件版本', exact: true })).toBeVisible();
  await expect(navigation.getByRole('button', { name: '更多工具', exact: true })).toHaveAttribute('aria-expanded', 'true');
  const active = navigation.getByRole('button', { name: '文件版本', exact: true });
  await expect(active).toHaveAttribute('aria-current', 'page'); await expect(active).toBeInViewport();
  await page.screenshot({ path: `${proof}/frontend-navigation-deep-link-desktop.png` });
  await page.reload();
  await expect(page.getByRole('heading', { name: '文件版本', exact: true })).toBeVisible();
  await expect(active).toHaveAttribute('aria-current', 'page');
  await page.evaluate(() => sessionStorage.removeItem('rg_token')); await page.reload();
  await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toBeVisible();
  await page.getByLabel('访问令牌', { exact: true }).fill('synthetic-browser-token');
  await page.getByRole('button', { name: '进入本地工作区', exact: false }).click();
  await expect(page.getByRole('heading', { name: '文件版本', exact: true })).toBeVisible();
  await expect(active).toHaveAttribute('aria-current', 'page');
  await expect(navigation.getByRole('button', { name: '更多工具', exact: true })).toHaveAttribute('aria-expanded', 'true');
});
