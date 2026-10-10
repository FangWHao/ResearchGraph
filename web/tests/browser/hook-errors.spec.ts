import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import type { HookErrorPage } from '../../src/hookErrors';
import type { Project } from '../../src/types';

const origin = 'http://127.0.0.1:9797';
const headers = { Authorization: 'Bearer synthetic-hook-errors-token' };
const root = fileURLToPath(new URL('../../../', import.meta.url));
const primary = '验收钩子报告项目';
const secondary = '验收钩子报告项目二';
let server: ChildProcess | undefined;

test.beforeAll(async () => {
  let existing: Response | undefined;
  try { existing = await fetch(`${origin}/api/projects`, { headers }); } catch { /* 无监听。 */ }
  if (existing) {
    if (!existing.ok) throw new Error('9797 被其他服务占用，未终止已有进程。');
    expect((await existing.json()).projects.map((item: Project) => item.name).sort()).toEqual([primary, secondary].sort());
    console.info('复用已核对令牌和项目的合成服务，不终止其进程。');
    return;
  }
  const log = createWriteStream(`${root}/.cache/frontend-hook-errors-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.hook_errors_browser', '--port', '9797', '--web-dir', `${root}/web/dist`],
    { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => {
    if (server?.exitCode !== null) throw new Error('合成服务提前退出，见服务日志。');
    try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; }
  }).toBe(200);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) {
    const closed = new Promise<void>(resolve => server!.once('exit', () => resolve()));
    server.kill('SIGINT'); await closed;
  }
});
async function projects(request: APIRequestContext) {
  return (await (await request.get(`${origin}/api/projects`, { headers })).json()).projects as Project[];
}
async function read(request: APIRequestContext, project: string, offset = 0, snapshot?: number) {
  const params = new URLSearchParams({ project, limit: '20', offset: String(offset) });
  if (snapshot != null) params.set('snapshot', String(snapshot));
  const response = await request.get(`${origin}/api/hook-errors?${params}`, { headers });
  expect(response.status()).toBe(200); return await response.json() as HookErrorPage;
}
async function open(page: Page) {
  await page.goto(`${origin}/#token=synthetic-hook-errors-token`);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  return page.getByRole('region', { name: '钩子失败报告', exact: true });
}
async function show(page: Page, locator: Locator) {
  await locator.evaluate(node => node.scrollIntoView({ block: 'start' }));
  await page.evaluate(() => window.scrollBy(0, -16));
  if (page.viewportSize()!.width === 390) await page.evaluate(() => window.scrollBy(0, -88));
}
async function displayedReports(panel: Locator) {
  return await panel.locator('[data-hook-report]').evaluateAll(items => items.map(item => Number(item.getAttribute('data-hook-report'))));
}

test('真实轮换历史和未知报告保留，分页与全库范围一致且不显示错误正文', async ({ page, request }) => {
  const allProjects = await projects(request); const first = allProjects.find(item => item.name === primary)!;
  const second = allProjects.find(item => item.name === secondary)!;
  const initial = await read(request, first.project_id);
  expect(initial).toMatchObject({ available: true, source_instances: 2, complete_failure_history: false,
    observation: { status: 'partial_line' }, counts: { records_total: 29, reported_failures: 28,
      unknown_records: 1, unknown_exception_classes: 1 } });
  expect(await read(request, second.project_id)).toEqual(initial);
  const remaining = await read(request, first.project_id, 20, initial.snapshot_id);
  const reports = [...initial.reports, ...remaining.reports];
  expect(reports).toHaveLength(initial.counts!.records_total);
  expect(new Set(reports.map(item => item.source_id)).size).toBe(2);
  const duplicates = reports.filter(item => item.line_sha256 === reports.find(item => item.report_id === 1)!.line_sha256);
  expect(duplicates).toHaveLength(2); expect(new Set(duplicates.map(item => item.source_id)).size).toBe(2);
  expect(reports.some(item => item.exception_class === 'JSONDecodeError')).toBe(true);
  const masked = reports.find(item => item.exception_class_sha256 !== null)!;
  expect(masked.exception_class).toBeNull();
  expect(masked.exception_class_sha256).toBe(createHash('sha256').update('SyntheticPrivateError').digest('hex'));
  expect(JSON.stringify(initial)).not.toContain('合成私密错误正文');
  expect(JSON.stringify(initial)).not.toContain('SyntheticPrivateError');
  expect((await (await request.get(`${origin}/api/health?project=${first.project_id}`, { headers })).json()).hook_failures).toBe(28);
  const graph = await (await request.get(`${origin}/api/graph?project=${first.project_id}`, { headers })).json();
  const writes: string[] = []; const reads: string[] = [];
  page.on('request', item => {
    if (new URL(item.url()).pathname === '/api/hook-errors') reads.push(item.url());
    if (new URL(item.url()).pathname.startsWith('/api/') && item.method() !== 'GET') writes.push(item.url());
  });
  const panel = await open(page);
  await expect(panel.locator('[data-hook-metric="reported_failures"] dd')).toHaveText('28');
  await expect(panel.locator('[data-hook-status="partial_line"]')).toBeVisible();
  expect(await displayedReports(panel)).toEqual(initial.reports.map(item => item.report_id));
  await show(page, panel.locator(':scope > header'));
  await page.screenshot({ path: '../.cache/frontend-hook-errors-desktop.png' });
  const unknown = panel.locator(`[data-hook-report="${masked.report_id}"]`);
  await expect(unknown).toContainText(masked.exception_class_sha256!);
  await show(page, unknown);
  await page.screenshot({ path: '../.cache/frontend-hook-errors-report-desktop.png' });
  await panel.getByRole('button', { name: '下一页钩子报告', exact: true }).click();
  await expect(panel.locator('.hook-error-page')).toContainText('偏移 20');
  expect(await displayedReports(panel)).toEqual(remaining.reports.map(item => item.report_id));
  await expect(panel.getByRole('button', { name: '下一页钩子报告', exact: true })).toBeDisabled();
  expect(reads.filter(value => Number(new URL(value).searchParams.get('offset')) > 0)
    .every(value => new URL(value).searchParams.get('snapshot') === String(initial.snapshot_id))).toBe(true);
  expect(await panel.textContent()).not.toContain('合成私密错误正文');
  expect(await panel.textContent()).not.toContain('SyntheticPrivateError');
  expect(await page.evaluate(() => 'hookInjected' in window)).toBe(false);
  expect(writes).toEqual([]);
  expect(await (await request.get(`${origin}/api/graph?project=${first.project_id}`, { headers })).json()).toEqual(graph);
});

test('真实追加不会进入旧快照续页，主动首读取得新状态，缺截止与未来快照被拒绝', async ({ page, request }) => {
  const first = (await projects(request)).find(item => item.name === primary)!;
  const panel = await open(page); const initial = await read(request, first.project_id);
  await expect(panel.locator('.hook-error-page')).toContainText(`读取快照 #${initial.snapshot_id}`);
  expect((await request.get(`${origin}/api/hook-errors?project=${first.project_id}&offset=20`, { headers })).status()).toBe(400);
  expect((await request.get(`${origin}/api/hook-errors?project=${first.project_id}&snapshot=999999`, { headers })).status()).toBe(400);
  const appended = await request.post(`${origin}/synthetic-hook-fixture/append`, { headers, data: {} });
  expect(appended.status()).toBe(200); expect((await appended.json()).hook_error_records).toBe(2);
  const current = await read(request, first.project_id);
  expect(current.snapshot_id).toBeGreaterThan(initial.snapshot_id);
  expect(current.counts!.records_total).toBe(initial.counts!.records_total + 2);
  expect(current.observation!.status).toBe('synced');
  await panel.getByRole('button', { name: '下一页钩子报告', exact: true }).click();
  await expect(panel.locator('.hook-error-page')).toContainText('偏移 20');
  const old = await read(request, first.project_id, 20, initial.snapshot_id);
  expect(await displayedReports(panel)).toEqual(old.reports.map(item => item.report_id));
  await expect(panel.locator('[data-hook-metric="records_total"] dd')).toHaveText(String(initial.counts!.records_total));
  await expect(panel.locator('[data-hook-status="partial_line"]')).toHaveCount(1);
  await panel.getByRole('button', { name: '重新读取钩子报告', exact: true }).click();
  await expect(panel.locator('.hook-error-page')).toContainText(`读取快照 #${current.snapshot_id}`);
  await expect(panel.locator('[data-hook-metric="records_total"] dd')).toHaveText(String(current.counts!.records_total));
  await expect(panel.locator('[data-hook-status="synced"]')).toHaveCount(1);
  expect(await displayedReports(panel)).toEqual(current.reports.map(item => item.report_id));
});

test('旧接口和未观测统计保持未知，格式错误保留旧页，真实401通过既有登录恢复', async ({ page }) => {
  let mode: 'legacy' | 'unobserved' | 'missing' | 'normal' | 'malformed' | 'denied' = 'legacy';
  await page.route('**/api/hook-errors?**', async route => {
    if (mode === 'legacy') await route.fulfill({ json: { available: false, reason: 'legacy_schema', scope: 'data_directory' } });
    else if (mode === 'unobserved') await route.fulfill({ json: { available: false, reason: 'not_observed',
      scope: 'data_directory', snapshot_id: 0, source_instances: 0, observation: null, counts: null,
      reports: [], offset: 0, limit: 20, next_offset: null, complete_failure_history: false } });
    else if (mode === 'missing') await route.fulfill({ status: 404, json: { error: '合成旧接口未提供' } });
    else if (mode === 'malformed') { const response = await route.fetch(); const data = await response.json();
      await route.fulfill({ json: { ...data, snapshot_id: data.snapshot_id + 1 } }); }
    else if (mode === 'denied') { const response = await route.fetch({ headers: { ...route.request().headers(), Authorization: 'Bearer synthetic-wrong-token' } });
      expect(response.status()).toBe(401); await route.fulfill({ response }); }
    else await route.continue();
  });
  const panel = await open(page);
  await expect(panel).toContainText('旧数据库'); await expect(panel.locator('[data-hook-metric]')).toHaveCount(0);
  mode = 'unobserved'; await panel.getByRole('button', { name: '重新读取钩子报告', exact: true }).click();
  await expect(panel.locator('[data-hook-metric] dd')).toHaveText(['未知', '未知', '未知', '未知']);
  await expect(panel.locator('[data-hook-report]')).toHaveCount(0);
  mode = 'missing'; await panel.getByRole('button', { name: '重新读取钩子报告', exact: true }).click();
  await expect(panel.getByRole('alert')).toContainText('采集情况和失败次数未知');
  await expect(panel.locator('[data-hook-metric] dd')).toHaveText(['未知', '未知', '未知', '未知']);
  mode = 'normal'; await panel.getByRole('button', { name: '重新读取钩子报告', exact: true }).click();
  await expect(panel.locator('[data-hook-report]')).toHaveCount(20); const ids = await displayedReports(panel);
  mode = 'malformed'; await panel.getByRole('button', { name: '下一页钩子报告', exact: true }).click();
  await expect(panel.getByRole('alert')).toContainText('读取快照不一致');
  expect(await displayedReports(panel)).toEqual(ids);
  await expect(panel.getByRole('button', { name: '下一页钩子报告', exact: true })).toBeDisabled();
  mode = 'denied'; await panel.getByRole('button', { name: '重新读取钩子报告', exact: true }).click();
  await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toBeVisible();
  mode = 'normal'; await page.getByLabel('访问令牌', { exact: true }).fill('synthetic-hook-errors-token');
  await page.getByRole('button', { name: /进入本地工作区/ }).click();
  await expect(panel.locator('[data-hook-report]')).toHaveCount(20);
  await expect(panel.locator('.hook-error-page')).toContainText('偏移 0');
});

test('390直接切项目后迟到旧快照及401不替换全库新观测，报告卡实际在屏内', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const all = await projects(request); const first = all.find(item => item.name === primary)!;
  const second = all.find(item => item.name === secondary)!;
  const current = await read(request, second.project_id); const old = await read(request, first.project_id, 0, 1);
  expect(current.snapshot_id).toBeGreaterThan(old.snapshot_id);
  expect(current.counts!.records_total).toBeGreaterThan(old.counts!.records_total);
  await page.addInitScript(() => {
    const actual = window.fetch.bind(window); const received: { status: number; late: string | null; url: string }[] = [];
    (window as unknown as { hookResponses: typeof received }).hookResponses = received;
    window.fetch = async (input, options) => {
      if (!String(input).startsWith('/api/hook-errors?')) return actual(input, options);
      const response = await actual(input, { ...options, signal: undefined }); const parse = response.json.bind(response);
      response.json = async () => { const data = await parse(); received.push({ status: response.status,
        late: response.headers.get('x-synthetic-late'), url: String(input) }); return data; }; return response;
    };
  });
  for (const status of [200, 401]) {
    await page.goto('about:blank');
    let release = () => {}; const held = new Promise<void>(resolve => { release = resolve; });
    let ready = () => {}; const started = new Promise<void>(resolve => { ready = resolve; }); let deferred = false;
    await page.route('**/api/hook-errors?**', async route => {
      if (!deferred && new URL(route.request().url()).searchParams.get('project') === first.project_id) {
        deferred = true; const url = new URL(route.request().url()); url.searchParams.set('snapshot', '1');
        const response = await route.fetch(status === 401 ? { headers: { ...route.request().headers(), Authorization: 'Bearer synthetic-wrong-token' } }
          : { url: url.toString() });
        expect(response.status()).toBe(status); ready(); await held;
        await route.fulfill({ response, headers: { ...response.headers(), 'x-synthetic-late': String(status) } });
      } else await route.continue();
    });
    const panel = await open(page); await started;
    await page.getByLabel('当前项目', { exact: true }).selectOption({ label: secondary });
    await expect(panel.locator('.hook-error-page')).toContainText(`读取快照 #${current.snapshot_id}`);
    release();
    await expect.poll(() => page.evaluate(({ status, project }) =>
      (window as unknown as { hookResponses: { status: number; late: string | null; url: string }[] }).hookResponses
        .some(item => item.status === status && item.late === String(status)
          && new URL(item.url, location.origin).searchParams.get('project') === project),
    { status, project: first.project_id })).toBe(true);
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(panel.locator('.hook-error-page')).toContainText(`读取快照 #${current.snapshot_id}`);
    await expect(panel.locator('[data-hook-metric="records_total"] dd')).toHaveText(String(current.counts!.records_total));
    expect(await displayedReports(panel)).toEqual(current.reports.map(item => item.report_id));
    await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toHaveCount(0);
    await expect(panel.getByRole('alert')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await show(page, panel.locator(':scope > header'));
    await page.screenshot({ path: '../.cache/frontend-hook-errors-mobile.png' });
    const card = panel.locator('[data-hook-report]').first(); await show(page, card);
    const bounds = (await card.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(72); expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
    expect(bounds.y).toBeGreaterThanOrEqual(88); expect(bounds.y + bounds.height).toBeLessThanOrEqual(844);
    await page.screenshot({ path: '../.cache/frontend-hook-errors-report-mobile.png' });
    await page.unroute('**/api/hook-errors?**');
  }
});
