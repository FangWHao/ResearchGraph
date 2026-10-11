import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createWriteStream, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import type { WorktreePage } from '../../src/worktrees';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const origin = 'http://127.0.0.1:9808', headers = { Authorization: 'Bearer synthetic-worktrees-token' };
interface Index { project_id: string; second_project: string; main_root: string; new_tree: string; late_tree: string; initial_total: number; after_discovery_total: number }
let directory: string, index: Index, server: ChildProcess | undefined, captured = 0;
function capture(tag: string, value: unknown) { const dir = `${root}/.cache/frontend-worktrees-api`; mkdirSync(dir, { recursive: true }); writeFileSync(`${dir}/${String(++captured).padStart(3, '0')}-${tag}.json`, JSON.stringify(value, null, 2) + '\n'); }
test.beforeAll(async () => {
  let occupied = false; try { await fetch(origin); occupied = true; } catch { /* 无监听。 */ }
  if (occupied) throw new Error('9808已占用，未停止未知服务。');
  directory = mkdtempSync(join(tmpdir(), 'researchgraph-worktrees-'));
  mkdirSync(`${root}/.cache`, { recursive: true });
  const prepareLog = createWriteStream(`${root}/.cache/frontend-worktrees-prepare.log`);
  const prepare = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.worktree_browser', '--fixture-dir', directory, '--prepare'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] }); prepare.stdout!.pipe(prepareLog); prepare.stderr!.pipe(prepareLog);
  expect(await new Promise<number | null>(resolve => prepare.once('exit', resolve))).toBe(0);
  index = JSON.parse(readFileSync(`${root}/.cache/frontend-worktrees-index.json`, 'utf8'));
  const log = createWriteStream(`${root}/.cache/frontend-worktrees-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.worktree_browser', '--fixture-dir', directory, '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] }); server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => { if (server?.exitCode !== null) throw new Error('合成服务提前退出，见日志'); try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; } }).toBe(200);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) { const exit = new Promise<number | null>(resolve => server!.once('exit', resolve)); server.kill('SIGINT'); expect(await exit).toBe(0); expect(JSON.parse(readFileSync(`${root}/.cache/frontend-worktrees-audit.json`, 'utf8'))).toMatchObject({ 科研及Git原内容未改写: true }); }
  if (directory) rmSync(directory, { recursive: true, force: true });
});
async function get(request: APIRequestContext, project = index.project_id, offset = 0, revision?: number): Promise<WorktreePage> {
  const response = await request.get(`${origin}/api/worktrees?project=${project}&limit=50&offset=${offset}${revision == null ? '' : `&expected_revision=${revision}`}`, { headers }); expect(response.status()).toBe(200); const data = await response.json(); capture('worktree-page', data); return data;
}
async function control(request: APIRequestContext, command: string) { const r = await request.post(`${origin}/synthetic-worktrees/${command}`, { headers, data: {} }); expect(r.status()).toBe(200); const data = await r.json(); capture(command, data); return data; }
async function audit(request: APIRequestContext) { const r = await request.get(`${origin}/synthetic-worktrees/audit`, { headers }); expect(r.status()).toBe(200); const data = await r.json(); capture('audit', data); return data; }
async function enter(page: Page, project = index.project_id) { await page.goto(`${origin}/#token=synthetic-worktrees-token`); await page.getByLabel('当前项目', { exact: true }).selectOption(project); await page.getByRole('button', { name: '采集健康', exact: true }).click(); await expect(panel(page).locator('[data-worktree-policy]')).toBeVisible(); }
function panel(page: Page) { return page.getByRole('region', { name: '工作树自动跟踪', exact: true }); }
async function refresh(page: Page): Promise<WorktreePage> { const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/worktrees'); await panel(page).getByRole('button', { name: '重新读取工作树', exact: true }).click(); const r = await response; expect(r.status()).toBe(200); await expect(panel(page).getByRole('button', { name: /^(开启|停止)自动跟踪$/ })).toBeEnabled(); return r.json(); }
async function toggle(page: Page) { const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/worktrees/follow'); await panel(page).getByRole('button', { name: /^(开启|停止)自动跟踪$/ }).click(); const r = await response; expect(r.status()).toBe(200); const data = await r.json(); capture('follow-request', r.request().postDataJSON()); capture('follow-receipt', data); await expect(panel(page).locator(`[data-worktree-policy="${data.follow_enabled}"]`)).toBeVisible(); await expect(panel(page).getByRole('button', { name: /^(开启|停止)自动跟踪$/ })).toBeEnabled(); return data; }
async function visible(node: Locator) {
  await node.evaluate(element => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
  const bounds = await node.evaluate(element => { const b = element.getBoundingClientRect(), picker = document.querySelector('.project-picker'); const headerBottom = picker && getComputedStyle(picker).position === 'fixed' ? picker.getBoundingClientRect().bottom : 0; return { left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width, height: b.height, headerBottom, w: innerWidth, h: innerHeight, document: document.documentElement.scrollWidth }; }); capture('geometry', bounds);
  expect(bounds.width).toBeGreaterThan(0); expect(bounds.height).toBeGreaterThan(0); expect(bounds.left).toBeGreaterThanOrEqual(0); expect(bounds.right).toBeLessThanOrEqual(bounds.w); expect(bounds.top).toBeGreaterThanOrEqual(bounds.headerBottom); expect(bounds.bottom).toBeLessThanOrEqual(bounds.h); expect(bounds.document).toBeLessThanOrEqual(bounds.w);
}
async function ignoreTransportAbort(page: Page) {
  await page.addInitScript(() => { const original = window.fetch.bind(window); window.fetch = (input, init) => { const path = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href).pathname; if (path.startsWith('/api/worktrees')) { const options = { ...init }; delete options.signal; return original(input, options); } return original(input, init); }; });
}

test('默认关闭只保存开启策略，下一轮真实cycle登记新树并保留16项发现页和50项根分页', async ({ page, request }) => {
  const before = await audit(request), initial = await get(request); expect(initial.follow_enabled).toBe(false); expect(initial.total).toBe(53); expect(initial.roots.every(r => r.discovery.status === 'not_observed')).toBe(true);
  await enter(page); await toggle(page); const saved = await get(request); expect(saved.total).toBe(initial.total); expect(saved.follow_enabled).toBe(true);
  const cycle = await control(request, 'cycle'); expect(cycle.cycle.worktrees_created).toBe(1);
  const first = await refresh(page); expect(first.total).toBe(54); expect(first.roots).toHaveLength(50); expect(first.next_offset).toBe(50);
  const secondResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/worktrees' && new URL(r.url()).searchParams.get('offset') === '50');
  await panel(page).getByRole('button', { name: '下一页工作树', exact: true }).click(); const secondHttp = await secondResponse; expect(secondHttp.status()).toBe(200); const second: WorktreePage = await secondHttp.json(); capture('second-root-page', second);
  expect(new URL(secondHttp.request().url()).searchParams.get('expected_revision')).toBe(String(first.revision)); expect(second.revision).toBe(first.revision); expect(second.total).toBe(54); expect(second.roots).toHaveLength(4); expect(second.next_offset).toBeNull();
  const combined = [...first.roots, ...second.roots]; expect(new Set(combined.map(r => r.root_id)).size).toBe(54); expect(combined.some(r => r.path === index.new_tree)).toBe(true);
  const main = combined.find(r => r.root_id === index.main_root)!; expect(main.discovery).toMatchObject({ status: 'partial', details: { created: 1, page_count: 16, list_total: 54, list_complete: false } });
  if (first.roots.some((r: { root_id: string }) => r.root_id === index.main_root)) await panel(page).getByRole('button', { name: '上一页工作树', exact: true }).click();
  const card = panel(page).locator(`[data-worktree-root="${index.main_root}"]`); await expect(card.locator('[data-discovery-status="partial"]')).toBeVisible(); await visible(card); await page.screenshot({ path: '../.cache/frontend-worktrees-partial-desktop.png' });
  const duplicate = await control(request, 'cycle'); expect(duplicate.cycle.worktrees_created ?? 0).toBe(0); expect((await get(request)).total).toBe(54);
  const after = await audit(request); expect(after.research_sha256).toBe(before.research_sha256); expect(after.git_index_config_body).toEqual(before.git_index_config_body); expect(after.protected_counts.model_attempts).toBe(0);
});

test('停止保留历史且未来新树不登记，共同目录跨项目歧义不泄露其它项目身份', async ({ page, request }) => {
  await enter(page); if ((await get(request)).follow_enabled) await toggle(page); const stopped = await get(request); const oldRoots = stopped.total;
  await control(request, 'new'); await control(request, 'cycle'); const after = await get(request); expect(after.follow_enabled).toBe(false); expect(after.total).toBe(oldRoots); expect(after.roots.some(r => r.path === index.late_tree)).toBe(false);
  await control(request, 'share'); await refresh(page); await toggle(page); await control(request, 'cycle'); const data = await refresh(page);
  let observed = data.roots.find(r => r.discovery.status === 'ambiguous');
  if (!observed) {
    const next = await get(request, index.project_id, 50, data.revision);
    observed = next.roots.find(r => r.discovery.status === 'ambiguous');
    await panel(page).getByRole('button', { name: '下一页工作树', exact: true }).click();
  }
  expect(observed).toBeDefined(); expect(observed!.project_id).toBe(index.project_id);
  const card = panel(page).locator(`[data-worktree-root="${observed!.root_id}"]`); await expect(card.locator('[data-discovery-status="ambiguous"]')).toBeVisible(); await visible(card); await page.screenshot({ path: '../.cache/frontend-worktrees-ambiguous-desktop.png' });
  expect(await panel(page).textContent()).not.toContain(index.second_project); expect((await get(request)).total).toBe(oldRoots);
});

test('真实修订409不混分页或恢复旧开关，保留旧成功页并主动重新读取再提交', async ({ page, request }) => {
  await enter(page); const before = await get(request), ids = await panel(page).locator('[data-worktree-root]').evaluateAll(elements => elements.map(e => e.getAttribute('data-worktree-root')));
  let changed = await request.post(`${origin}/api/worktrees/follow`, { headers, data: { project_id: index.second_project, follow_enabled: true, expected_revision: before.revision } }); expect(changed.status()).toBe(200);
  const conflict = page.waitForResponse(r => new URL(r.url()).pathname === '/api/worktrees' && new URL(r.url()).searchParams.get('offset') === '50'); await panel(page).getByRole('button', { name: '下一页工作树', exact: true }).click(); expect((await conflict).status()).toBe(409); await expect(panel(page).getByRole('alert')).toBeVisible(); expect(await panel(page).locator('[data-worktree-root]').evaluateAll(elements => elements.map(e => e.getAttribute('data-worktree-root')))).toEqual(ids);
  await expect(panel(page).getByRole('button', { name: /^(开启|停止)自动跟踪$/ })).toBeDisabled(); const fresh = await refresh(page);
  changed = await request.post(`${origin}/api/worktrees/follow`, { headers, data: { project_id: index.second_project, follow_enabled: false, expected_revision: fresh.revision } }); expect(changed.status()).toBe(200);
  const post = page.waitForResponse(r => new URL(r.url()).pathname === '/api/worktrees/follow'); await panel(page).getByRole('button', { name: /^(开启|停止)自动跟踪$/ }).click(); expect((await post).status()).toBe(409); await expect(panel(page).getByRole('alert')).toBeVisible(); expect((await get(request)).follow_enabled).toBe(before.follow_enabled);
  await refresh(page); const success = await toggle(page); expect(success.follow_enabled).toBe(!before.follow_enabled);
});

test('真实迟到读取200不会把另一项目根与策略放进当前项目', async ({ page }) => {
  await ignoreTransportAbort(page); await enter(page); let release = () => {}; const held = new Promise<void>(resolve => { release = resolve; }); let arrived = false;
  await page.route('**/api/worktrees?*', async route => { const response = await route.fetch(); const project = new URL(route.request().url()).searchParams.get('project'); if (project === index.project_id) { arrived = true; await held; } await route.fulfill({ response }); });
  try {
    await panel(page).getByRole('button', { name: '重新读取工作树', exact: true }).click(); await expect.poll(() => arrived).toBe(true);
    await page.getByLabel('当前项目', { exact: true }).selectOption(index.second_project); await expect(panel(page).locator('[data-worktree-policy]')).toBeVisible();
    const ids = await panel(page).locator('[data-worktree-root]').evaluateAll(elements => elements.map(e => e.getAttribute('data-worktree-root'))); const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/worktrees' && new URL(r.url()).searchParams.get('project') === index.project_id); release(); expect((await response).status()).toBe(200);
    expect(await panel(page).locator('[data-worktree-root]').evaluateAll(elements => elements.map(e => e.getAttribute('data-worktree-root')))).toEqual(ids); await expect(page.getByLabel('当前项目', { exact: true })).toHaveValue(index.second_project);
  } finally { release(); await page.unroute('**/api/worktrees?*'); }
});

test('真实旧保存200在刷新后不能覆盖新的停止或开启意图', async ({ page, request }) => {
  await ignoreTransportAbort(page); await enter(page); const before = await get(request); let release = () => {}, first = true, arrived = false; const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/worktrees/follow', async route => { const response = await route.fetch(); expect(response.status()).toBe(200); if (first) { first = false; arrived = true; await held; } await route.fulfill({ response }); });
  try {
    await panel(page).getByRole('button', { name: /^(开启|停止)自动跟踪$/ }).click(); await expect.poll(() => arrived).toBe(true);
    await refresh(page); const saved = await toggle(page); expect(saved.follow_enabled).toBe(before.follow_enabled);
    const late = page.waitForResponse(r => new URL(r.url()).pathname === '/api/worktrees/follow'); release(); expect((await late).status()).toBe(200);
    await expect(panel(page).locator(`[data-worktree-policy="${saved.follow_enabled}"]`)).toBeVisible(); expect((await get(request)).follow_enabled).toBe(saved.follow_enabled); await expect(panel(page).getByRole('alert')).toHaveCount(0);
  } finally { release(); await page.unroute('**/api/worktrees/follow'); }
});

test('真实旧保存401在项目切换后不撤销当前授权或给新项目报错', async ({ page }) => {
  await ignoreTransportAbort(page); await enter(page); let release = () => {}, arrived = false; const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/worktrees/follow', async route => { const response = await route.fetch({ headers: { ...route.request().headers(), Authorization: 'Bearer synthetic-invalid' } }); expect(response.status()).toBe(401); arrived = true; await held; await route.fulfill({ response }); });
  try {
    await panel(page).getByRole('button', { name: /^(开启|停止)自动跟踪$/ }).click(); await expect.poll(() => arrived).toBe(true); await page.getByLabel('当前项目', { exact: true }).selectOption(index.second_project); await expect(panel(page).locator('[data-worktree-policy]')).toBeVisible();
    const late = page.waitForResponse(r => new URL(r.url()).pathname === '/api/worktrees/follow'); release(); expect((await late).status()).toBe(401);
    await expect(page.getByLabel('当前项目', { exact: true })).toHaveValue(index.second_project); await expect(panel(page).getByRole('alert')).toHaveCount(0); await refresh(page); await expect(page.getByRole('heading', { name: '连接本机研究库' })).toHaveCount(0);
  } finally { release(); await page.unroute('**/api/worktrees/follow'); }
});

test('390工作树策略、路径与歧义卡在屏内可见，手机直接切换项目', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await enter(page); await visible(panel(page).locator('.worktree-policy')); await page.screenshot({ path: '../.cache/frontend-worktrees-policy-mobile.png' });
  let card = panel(page).locator('[data-worktree-root]').filter({ has: page.locator('[data-discovery-status="not_observed"]') }).first(); await visible(card);
  const first = await get(request), next = await get(request, index.project_id, 50, first.revision);
  const ambiguous = [...first.roots, ...next.roots].find(r => r.discovery.status === 'ambiguous'); expect(ambiguous).toBeDefined();
  if (!first.roots.some(r => r.root_id === ambiguous!.root_id)) await panel(page).getByRole('button', { name: '下一页工作树', exact: true }).click();
  card = panel(page).locator(`[data-worktree-root="${ambiguous!.root_id}"]`);
  await visible(card.locator('h4')); await visible(card.locator('.worktree-counts')); await visible(card.locator('[data-discovery-status="ambiguous"]'));
  await page.screenshot({ path: '../.cache/frontend-worktrees-root-mobile.png' });
  await page.getByLabel('当前项目', { exact: true }).selectOption(index.second_project); await expect(panel(page).locator('[data-worktree-policy]')).toBeVisible(); card = panel(page).locator('[data-worktree-root]').first(); await visible(card); await page.screenshot({ path: '../.cache/frontend-worktrees-other-mobile.png' });
  await expect(page.getByLabel('当前项目', { exact: true })).toHaveValue(index.second_project);
});
