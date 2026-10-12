import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createWriteStream, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page, Route } from '@playwright/test';
import type { AutoAssignmentPage } from '../../src/fragmentAutoPolicy';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const origin = 'http://127.0.0.1:9798', headers = { Authorization: 'Bearer synthetic-fragment-auto-policy-token' };
interface Index { project_id: string; second_project: string; primary: string; shared: string; future_root: string; transcript: string; initial_total: number; old_event_id: number; source: { session_pk: number; file_instance_id: number } }
let directory: string, index: Index, server: ChildProcess | undefined, captured = 0;
function capture(tag: string, value: unknown) { const path = `${root}/.cache/frontend-fragment-auto-policy-api`; mkdirSync(path, { recursive: true }); writeFileSync(`${path}/${String(++captured).padStart(3, '0')}-${tag}.json`, JSON.stringify(value, null, 2) + '\n'); }
test.beforeAll(async () => {
  let occupied = false; try { await fetch(origin); occupied = true; } catch { /* 无监听。 */ }
  if (occupied) throw new Error('9798已占用，未停止未知服务。');
  directory = mkdtempSync(join(tmpdir(), 'researchgraph-fragment-auto-policy-'));
  mkdirSync(`${root}/.cache`, { recursive: true });
  const preparation = createWriteStream(`${root}/.cache/frontend-fragment-auto-policy-prepare.log`);
  const prepare = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.fragment_auto_policy_browser', '--fixture-dir', directory, '--prepare'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  prepare.stdout!.pipe(preparation); prepare.stderr!.pipe(preparation);
  expect(await new Promise<number | null>(resolve => prepare.once('exit', resolve))).toBe(0);
  index = JSON.parse(readFileSync(`${root}/.cache/frontend-fragment-auto-policy-index.json`, 'utf8'));
  const log = createWriteStream(`${root}/.cache/frontend-fragment-auto-policy-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.fragment_auto_policy_browser', '--fixture-dir', directory, '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => { if (server?.exitCode !== null) throw new Error('独立合成服务提前退出，见日志'); try { return (await fetch(`${origin}/api/project-assignment?project=${index.project_id}`, { headers })).status; } catch { return 0; } }).toBe(200);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) { const ended = new Promise<number | null>(resolve => server!.once('exit', resolve)); server.kill('SIGINT'); expect(await ended).toBe(0); expect(JSON.parse(readFileSync(`${root}/.cache/frontend-fragment-auto-policy-audit.json`, 'utf8'))['旧原件与科研记录未改写']).toBe(true); }
  if (directory) rmSync(directory, { recursive: true, force: true });
});
function panel(page: Page) { return page.getByRole('region', { name: '未来记录自动归属', exact: true }); }
async function get(request: APIRequestContext, project = index.project_id, offset = 0, fixed?: AutoAssignmentPage): Promise<AutoAssignmentPage> {
  const url = `${origin}/api/project-assignment?project=${project}&limit=100&offset=${offset}${fixed ? `&expected_revision=${fixed.revision}&expected_scope_key=${fixed.scope_key}` : ''}`;
  const response = await request.get(url, { headers }); expect(response.status()).toBe(200); const value = await response.json(); capture('rules', value); return value;
}
async function control(request: APIRequestContext, command: string, body = {}) { const response = await request.post(`${origin}/synthetic-fragment-auto-policy/${command}`, { headers, data: body }); expect(response.status()).toBe(200); const value = await response.json(); capture(command, value); return value; }
async function audit(request: APIRequestContext) { const response = await request.get(`${origin}/synthetic-fragment-auto-policy/audit`, { headers }); expect(response.status()).toBe(200); const value = await response.json(); capture('audit', value); return value; }
async function enter(page: Page, project = index.project_id) {
  await page.goto(`${origin}/#token=synthetic-fragment-auto-policy-token`);
  await page.getByLabel('当前项目', { exact: true }).selectOption(project);
  await page.getByLabel('复核者姓名').fill('公开甲');
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  await expect(panel(page).locator('[data-auto-assignment-enabled]')).toBeVisible();
  await expect(panel(page).getByRole('button', { name: /^(开启|停止)未来自动归属$/ })).toBeEnabled();
}
async function refresh(page: Page) { await panel(page).getByRole('button', { name: '重新读取归属规则', exact: true }).click(); await expect(panel(page).getByRole('button', { name: /^(开启|停止)未来自动归属$/ })).toBeEnabled(); }
async function toggle(page: Page) {
  const receipt = page.waitForResponse(response => new URL(response.url()).pathname === '/api/project-assignment/configure');
  await panel(page).getByRole('button', { name: /^(开启|停止)未来自动归属$/ }).click();
  const response = await receipt; expect(response.status()).toBe(200); const value = await response.json(); capture('configure-body', response.request().postDataJSON()); capture('configure-receipt', value);
  await expect(panel(page).locator(`[data-auto-assignment-enabled="${value.enabled}"]`)).toBeVisible();
  await expect(panel(page).getByRole('button', { name: /^(开启|停止)未来自动归属$/ })).toBeEnabled(); return value as AutoAssignmentPage;
}
async function transportIgnoresAbort(page: Page) {
  await page.addInitScript(() => { const original = window.fetch.bind(window); window.fetch = (input, init) => { const path = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href).pathname; if (path.startsWith('/api/project-assignment')) { const options = { ...init }; delete options.signal; return original(input, options); } return original(input, init); }; });
}
function hold() { let release = () => {}; const waiting = new Promise<void>(resolve => { release = resolve; }); let finished = () => {}; const done = new Promise<void>(resolve => { finished = resolve; }); return { release, waiting, finished, done }; }
async function visible(locator: Locator) {
  await locator.evaluate(element => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
  const bounds = await locator.evaluate(element => { const b = element.getBoundingClientRect(), picker = document.querySelector('.project-picker'), p = picker?.getBoundingClientRect(); const sticky = picker && ['fixed', 'sticky'].includes(getComputedStyle(picker).position); return { x: b.x, right: b.right, y: b.y, bottom: b.bottom, width: b.width, height: b.height, header: sticky && p && p.top >= 0 ? p.bottom : 0, w: innerWidth, h: innerHeight, document: document.documentElement.scrollWidth }; }); capture('geometry', bounds);
  expect(bounds.width).toBeGreaterThan(0); expect(bounds.height).toBeGreaterThan(0); expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.right).toBeLessThanOrEqual(bounds.w); expect(bounds.y).toBeGreaterThanOrEqual(bounds.header); expect(bounds.bottom).toBeLessThanOrEqual(bounds.h); expect(bounds.document).toBeLessThanOrEqual(bounds.w);
}

test('真实目录规则只分未来记录，103根固定分页、后来目录、人工绑定与停止均保留原史', async ({ page, request }) => {
  const before = await audit(request), initial = await get(request); expect(initial.enabled).toBe(false); expect(initial.total).toBe(index.initial_total);
  await enter(page); const enabled = await toggle(page); expect(enabled.enabled).toBe(true); expect(enabled.policy?.after_event_id).toBeGreaterThanOrEqual(index.old_event_id);
  const first = await get(request), next = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-assignment' && new URL(r.url()).searchParams.get('offset') === '100');
  await panel(page).getByRole('button', { name: '下一页归属规则', exact: true }).click(); const response = await next; expect(response.status()).toBe(200); const second = await response.json(); capture('second-page', second);
  expect(new URL(response.request().url()).searchParams.get('expected_revision')).toBe(String(first.revision)); expect(new URL(response.request().url()).searchParams.get('expected_scope_key')).toBe(first.scope_key); expect(second.roots).toHaveLength(3); expect(second.next_offset).toBeNull(); expect(new Set([...first.roots, ...second.roots].map(r => r.root_id)).size).toBe(103);
  const registered = await control(request, 'append', { case: 'registered' }); const records = registered.page.records;
  expect(records.find((r: { first_event_id: number }) => r.first_event_id === index.old_event_id).assignment.project_id).toBeNull();
  expect(records.at(-1).assignment).toMatchObject({ project_id: index.project_id, basis: 'path_rule', reason: 'registered_path_rule' });
  await control(request, 'register'); const future = await control(request, 'append', { case: 'future' }); expect(future.page.records.at(-1).assignment).toMatchObject({ project_id: index.project_id, basis: 'path_rule' });
  const manual = await control(request, 'append', { case: 'manual' }); expect(manual.page.records.at(-1).assignment.project_id).toBe(index.second_project); expect(manual.page.records.at(-1).assignment.basis).not.toBe('path_rule');
  const other = await get(request, index.second_project);
  const otherPolicy = await request.post(`${origin}/api/project-assignment/configure`, { headers, data: { project_id: index.second_project, enabled: true, actor: 'human:公开乙', expected_revision: other.revision, expected_scope_key: other.scope_key } }); expect(otherPolicy.status()).toBe(200);
  await refresh(page); const stopped = await toggle(page); expect(stopped.enabled).toBe(false);
  const afterStop = await control(request, 'append', { case: 'stopped' }); expect(afterStop.page.records.at(-1).assignment).toMatchObject({ project_id: null, basis: 'rule_unassigned', reason: 'policy_disabled' });
  const after = await audit(request); expect(after.scientific_sha256).toBe(before.scientific_sha256); expect(after.original_raw_sha256).toBe(before.original_raw_sha256); expect(after.model_calls).toBe(0);
});

test('真实409保留旧规则页，禁止自动重发或混合新页，重新读后才可主动保存', async ({ page, request }) => {
  await enter(page); const old = await get(request); const other = await get(request, index.second_project);
  const changed = await request.post(`${origin}/api/project-assignment/configure`, { headers, data: { project_id: index.second_project, enabled: !other.enabled, actor: 'human:公开另一页面', expected_revision: other.revision, expected_scope_key: other.scope_key } }); expect(changed.status()).toBe(200);
  const roots = await panel(page).locator('[data-auto-assignment-root]').evaluateAll(elements => elements.map(e => e.getAttribute('data-auto-assignment-root')));
  const failed = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-assignment' && new URL(r.url()).searchParams.get('offset') === '100');
  await panel(page).getByRole('button', { name: '下一页归属规则', exact: true }).click(); expect((await failed).status()).toBe(409); await expect(panel(page).getByRole('alert')).toBeVisible(); expect(await panel(page).locator('[data-auto-assignment-root]').evaluateAll(elements => elements.map(e => e.getAttribute('data-auto-assignment-root')))).toEqual(roots);
  await expect(panel(page).getByRole('button', { name: /^(开启|停止)未来自动归属$/ })).toBeDisabled();
  await refresh(page); const stale = await get(request); const concurrent = await get(request, index.second_project); await request.post(`${origin}/api/project-assignment/configure`, { headers, data: { project_id: index.second_project, enabled: !concurrent.enabled, actor: 'human:公开另一页面', expected_revision: concurrent.revision, expected_scope_key: concurrent.scope_key } });
  let posted = 0; page.on('request', r => { if (new URL(r.url()).pathname === '/api/project-assignment/configure') posted++; });
  const rejected = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-assignment/configure'); await panel(page).getByRole('button', { name: /^(开启|停止)未来自动归属$/ }).click(); expect((await rejected).status()).toBe(409); await expect(panel(page).getByRole('alert')).toBeVisible(); expect(posted).toBe(1); expect((await get(request)).enabled).toBe(stale.enabled); expect(stale.revision).toBeGreaterThan(old.revision);
  await refresh(page); const success = await toggle(page); expect(success.enabled).toBe(!stale.enabled); expect(posted).toBe(2);
});

test('真实晚200读取不进入另项目，返回首页仍重新核对完整规则身份', async ({ page }) => {
  await transportIgnoresAbort(page); await enter(page); const held = hold(); let arrived = false;
  const handler = async (route: Route) => { const response = await route.fetch(); if (!arrived && new URL(route.request().url()).searchParams.get('project') === index.project_id) { arrived = true; await held.waiting; try { await route.fulfill({ response }); } finally { held.finished(); } } else await route.fulfill({ response }); };
  await page.route('**/api/project-assignment?*', handler);
  try {
    await panel(page).getByRole('button', { name: '重新读取归属规则', exact: true }).click(); await expect.poll(() => arrived).toBe(true);
    await page.getByLabel('当前项目', { exact: true }).selectOption(index.second_project); await expect(panel(page).locator('[data-auto-assignment-root]')).toHaveCount(2);
    const ids = await panel(page).locator('[data-auto-assignment-root]').evaluateAll(elements => elements.map(e => e.getAttribute('data-auto-assignment-root'))); held.release(); await held.done;
    expect(await panel(page).locator('[data-auto-assignment-root]').evaluateAll(elements => elements.map(e => e.getAttribute('data-auto-assignment-root')))).toEqual(ids);
    await expect(page.getByLabel('当前项目', { exact: true })).toHaveValue(index.second_project);
    await page.getByLabel('当前项目', { exact: true }).selectOption(index.project_id); await expect(panel(page).locator('[data-auto-assignment-root]')).toHaveCount(100);
  } finally { held.release(); if (arrived) await held.done; await page.unroute('**/api/project-assignment?*', handler); }
});

test('真实晚200保存不能覆盖主动刷新后的新开关意图', async ({ page, request }) => {
  await transportIgnoresAbort(page); await enter(page); const before = await get(request), held = hold(); let arrived = false;
  const handler = async (route: Route) => { const response = await route.fetch(); expect(response.status()).toBe(200); if (!arrived) { arrived = true; await held.waiting; try { await route.fulfill({ response }); } finally { held.finished(); } } else await route.fulfill({ response }); };
  await page.route('**/api/project-assignment/configure', handler);
  try {
    await panel(page).getByRole('button', { name: /^(开启|停止)未来自动归属$/ }).click(); await expect.poll(() => arrived).toBe(true);
    await refresh(page); const newer = await toggle(page); expect(newer.enabled).toBe(before.enabled);
    held.release(); await held.done; await expect(panel(page).locator(`[data-auto-assignment-enabled="${newer.enabled}"]`)).toBeVisible(); expect((await get(request)).policy?.action_id).toBe(newer.policy?.action_id); await expect(panel(page).getByRole('alert')).toHaveCount(0);
  } finally { held.release(); if (arrived) await held.done; await page.unroute('**/api/project-assignment/configure', handler); }
});

test('真实晚401保存不注销切换项目和操作者后的本机身份', async ({ page }) => {
  await transportIgnoresAbort(page); await enter(page); const held = hold(); let arrived = false;
  const handler = async (route: Route) => { const response = await route.fetch({ headers: { ...route.request().headers(), Authorization: 'Bearer synthetic-invalid' } }); expect(response.status()).toBe(401); arrived = true; await held.waiting; try { await route.fulfill({ response }); } finally { held.finished(); } };
  await page.route('**/api/project-assignment/configure', handler);
  try {
    await panel(page).getByRole('button', { name: /^(开启|停止)未来自动归属$/ }).click(); await expect.poll(() => arrived).toBe(true);
    await page.getByLabel('当前项目', { exact: true }).selectOption(index.second_project); await page.getByLabel('复核者姓名').fill('公开乙'); await expect(panel(page).getByRole('button', { name: /^(开启|停止)未来自动归属$/ })).toBeEnabled();
    held.release(); await held.done; await expect(page.getByLabel('访问令牌', { exact: true })).toHaveCount(0); await expect(page.getByLabel('当前项目', { exact: true })).toHaveValue(index.second_project); await expect(panel(page).getByRole('alert')).toHaveCount(0); await refresh(page);
  } finally { held.release(); if (arrived) await held.done; await page.unroute('**/api/project-assignment/configure', handler); }
});

test('390真实规则诊断和片段原因在可视区域内，未知与重叠留收件箱，HTML只作原文', async ({ page, request }) => {
  await enter(page); if (!(await get(request)).enabled) await toggle(page);
  const overlap = await control(request, 'append', { case: 'overlap' }); expect(overlap.page.records.at(-1).assignment).toMatchObject({ project_id: null, basis: 'rule_unassigned', reason: 'ambiguous_path' });
  const unknown = await control(request, 'append', { case: 'unknown' }); expect(unknown.page.records.at(-1).assignment).toMatchObject({ project_id: null, basis: 'rule_unassigned', reason: 'unknown_record' });
  await refresh(page); await visible(panel(page).locator('.fragment-auto-switch')); await page.screenshot({ path: '../.cache/frontend-fragment-auto-policy-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 }); await visible(panel(page).locator('.fragment-auto-switch')); await page.screenshot({ path: '../.cache/frontend-fragment-auto-policy-mobile.png' });
  const rules = await get(request), second = rules.next_offset == null ? null : await get(request, index.project_id, rules.next_offset, rules);
  const conflicted = [...rules.roots, ...(second?.roots ?? [])].find(r => r.reason === 'overlapping_projects')!; expect(conflicted).toBeDefined();
  if (!rules.roots.some(r => r.root_id === conflicted.root_id)) await panel(page).getByRole('button', { name: '下一页归属规则', exact: true }).click();
  const card = panel(page).locator(`[data-auto-assignment-root="${conflicted.root_id}"]`); await visible(card); await page.screenshot({ path: '../.cache/frontend-fragment-auto-policy-overlap-mobile.png' });
  const opened = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-fragments');
  await page.locator('.health-view').getByRole('row').filter({ hasText: index.transcript }).getByRole('button', { name: '查看片段与归属', exact: true }).click(); expect((await opened).status()).toBe(200);
  const drawer = page.getByRole('dialog', { name: '物理片段与项目归属', exact: true }), record = drawer.locator(`[data-fragment-record="${unknown.page.records.at(-1).first_event_id}"]`);
  await expect(record.locator('[data-fragment-assignment-reason]')).toBeVisible(); await visible(record); await page.screenshot({ path: '../.cache/frontend-fragment-auto-policy-fragment-mobile.png' });
  expect(await page.evaluate(() => (window as unknown as { syntheticExecuted?: boolean }).syntheticExecuted)).toBeUndefined();
});
