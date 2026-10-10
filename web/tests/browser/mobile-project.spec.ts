import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import type { GraphData, Project, VersionsPage } from '../../src/types';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const primary = '合成研究 · 界面信号验证';
const empty = '合成空项目';
const draftPrimary = '验收新增人工记录项目';
const draftSecondary = '验收新增人工记录项目二';

test.use({ viewport: { width: 390, height: 844 } });

async function projects(request: APIRequestContext) {
  const response = await request.get('/api/projects', { headers });
  expect(response.status()).toBe(200);
  return (await response.json()).projects as Project[];
}

async function visiblePicker(page: Page) {
  const picker = page.getByLabel('当前项目', { exact: true });
  await expect(picker).toBeVisible();
  await expect(picker).toBeEnabled();
  expect(await picker.evaluate(node => {
    const rect = node.getBoundingClientRect();
    return rect.left >= 0 && rect.right <= innerWidth && rect.top >= 0
      && rect.bottom <= innerHeight && rect.height >= 44
      && node.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
  })).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  return picker;
}

async function selectProject(page: Page, project: Project) {
  const picker = await visiblePicker(page);
  const response = page.waitForResponse(value => {
    const url = new URL(value.url());
    return url.pathname === '/api/graph' && url.searchParams.get('project') === project.project_id;
  });
  await picker.selectOption({ label: project.name });
  expect((await response).status()).toBe(200);
  await expect(picker).toHaveValue(project.project_id);
  await expect(page.locator('.page-heading .eyebrow')).toContainText(project.name);
}

test('390宽直接切换真实项目，空项目不残留问题和证据，返回后原记录未改变', async ({ page, request }) => {
  const all = await projects(request);
  const first = all.find(item => item.name === primary)!;
  const second = all.find(item => item.name === empty)!;
  const before: GraphData = await (await request.get(`/api/graph?project=${first.project_id}`, { headers })).json();
  const writes: string[] = [];
  page.on('request', item => {
    if (new URL(item.url()).pathname.startsWith('/api/') && item.method() !== 'GET') writes.push(item.url());
  });
  await page.goto('/#token=synthetic-browser-token');
  await expect(page.locator('.question-heading h2')).toContainText('哪些局部界面信号');
  await visiblePicker(page);
  await page.screenshot({ path: '../.cache/frontend-mobile-project-primary.png' });
  await page.getByRole('button', { name: '查看记录与证据', exact: true }).click();
  const detail = page.getByRole('dialog', { name: /记录 .* 详情/ });
  await expect(detail).toBeVisible();
  await detail.locator('.evidence-link').first().click();
  await expect(page.getByTestId('evidence-quote')).toBeVisible();
  await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  await selectProject(page, second);
  await expect(page.getByRole('heading', { name: '还没有可查看的研究问题', exact: true })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('.question-heading')).toHaveCount(0);
  await page.screenshot({ path: '../.cache/frontend-mobile-project-empty.png' });
  await selectProject(page, first);
  await expect(page.locator('.question-heading h2')).toContainText('哪些局部界面信号');
  const after: GraphData = await (await request.get(`/api/graph?project=${first.project_id}`, { headers })).json();
  expect(after).toEqual(before);
  expect(writes).toEqual([]);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(page.getByLabel('当前项目', { exact: true })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: '../.cache/frontend-mobile-project-desktop.png' });
});

test('390宽往返项目保留各自页面内草稿，不自动写入或把另一项目内容带入', async ({ page, request }) => {
  const all = await projects(request);
  const first = all.find(item => item.name === draftPrimary)!;
  const second = all.find(item => item.name === draftSecondary)!;
  const before = await Promise.all([first, second].map(async item =>
    (await (await request.get(`/api/graph?project=${item.project_id}`, { headers })).json()) as GraphData));
  const writes: string[] = [];
  page.on('request', item => {
    if (new URL(item.url()).pathname.startsWith('/api/') && item.method() !== 'GET') writes.push(item.url());
  });
  await page.goto('/#token=synthetic-browser-token');
  const drafts = ['手机项目一尚未提交的合成问题', '手机项目二独立的合成问题'];
  for (const [index, project] of [first, second].entries()) {
    await selectProject(page, project);
    await page.getByRole('button', { name: '新增研究问题', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '新增研究问题', exact: true });
    await expect(dialog.getByLabel('研究问题正文', { exact: true })).toHaveValue('');
    await dialog.getByLabel('研究问题正文', { exact: true }).fill(drafts[index]);
    await dialog.getByRole('button', { name: '关闭新增问题', exact: true }).click();
  }
  for (const [index, project] of [first, second].entries()) {
    await selectProject(page, project);
    await page.getByRole('button', { name: '新增研究问题', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '新增研究问题', exact: true });
    await expect(dialog.getByLabel('研究问题正文', { exact: true })).toHaveValue(drafts[index]);
    await expect(dialog).toContainText(project.name);
    await dialog.getByRole('button', { name: '关闭新增问题', exact: true }).click();
    expect(await (await request.get(`/api/graph?project=${project.project_id}`, { headers })).json()).toEqual(before[index]);
  }
  expect(writes).toEqual([]);
});

test('390宽切项目后迟到真实版本响应不覆盖当前项目，原鉴权与只读状态保留', async ({ page, request }) => {
  const all = await projects(request);
  const first = all.find(item => item.name === primary)!;
  const second = all.find(item => item.name === '验收批量项目')!;
  const original: VersionsPage = await (await request.get(`/api/versions?project=${first.project_id}&limit=25`, { headers })).json();
  const current: VersionsPage = await (await request.get(`/api/versions?project=${second.project_id}&limit=25`, { headers })).json();
  await page.addInitScript(() => {
    const actual = window.fetch.bind(window);
    const totals: number[] = [];
    (window as unknown as { mobileVersionTotals: number[] }).mobileVersionTotals = totals;
    window.fetch = async (input, options) => {
      if (!String(input).startsWith('/api/versions?')) return actual(input, options);
      const response = await actual(input, { ...options, signal: undefined });
      const parse = response.json.bind(response);
      response.json = async () => { const data = await parse(); totals.push(data.total); return data; };
      return response;
    };
  });
  let release = () => {};
  const held = new Promise<void>(resolve => { release = resolve; });
  let ready = () => {};
  const started = new Promise<void>(resolve => { ready = resolve; });
  let delayed = false;
  await page.route('**/api/versions?**', async route => {
    if (!delayed && new URL(route.request().url()).searchParams.get('project') === first.project_id) {
      delayed = true;
      const response = await route.fetch();
      ready();
      await held;
      await route.fulfill({ response });
    } else await route.continue();
  });
  await page.goto('/#token=synthetic-browser-token');
  await expect(page.locator('.question-heading')).toBeVisible();
  await page.getByRole('button', { name: '文件版本', exact: true }).click();
  await started;
  await selectProject(page, second);
  const panel = page.getByRole('region', { name: '文件版本记录 当前项目', exact: true });
  const rows = panel.locator('.file-version-list > li');
  await expect(rows).toHaveCount(current.versions!.length);
  const ids = current.versions!.map(item => item.version_id);
  expect(await rows.evaluateAll(items => items.map(item => item.getAttribute('data-file-version-id')))).toEqual(ids);
  release();
  await expect.poll(() => page.evaluate(() => (window as unknown as { mobileVersionTotals: number[] }).mobileVersionTotals))
    .toEqual([current.total, original.total]);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  expect(await rows.evaluateAll(items => items.map(item => item.getAttribute('data-file-version-id')))).toEqual(ids);
  await expect(page.getByLabel('当前项目', { exact: true })).toHaveValue(second.project_id);
  await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toHaveCount(0);
  await visiblePicker(page);
  await rows.first().evaluate(node => node.scrollIntoView({ block: 'start' }));
  await page.evaluate(() => window.scrollBy(0, -104));
  await page.screenshot({ path: '../.cache/frontend-mobile-project-late-versions.png' });
});
