import { test, expect } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createWriteStream, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const origin = 'http://127.0.0.1:9792'; const headers = { Authorization: 'Bearer synthetic-browser-token' };
const root = fileURLToPath(new URL('../../../', import.meta.url));
let server: ChildProcess;
test.beforeAll(async () => {
  let occupied = false; try { await fetch(origin); occupied = true; } catch { /* 未监听。 */ }
  if (occupied) throw new Error('独立合成服务端口 9792 已被占用，未重启或终止现有服务。');
  mkdirSync(`${root}/.cache`, { recursive: true });
  const log = createWriteStream(`${root}/.cache/frontend-project-clear-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.project_clear_browser', '--port', '9792', '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => {
    if (server.exitCode !== null) throw new Error(`合成服务提前退出 ${server.exitCode}，见服务日志。`);
    try { return (await fetch(`${origin}/api/project-clear/status`, { headers })).status; } catch { return 0; }
  }).toBe(200);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) { const closed = new Promise<void>(resolve => server.once('exit', () => resolve())); server.kill('SIGINT'); await closed; }
});
async function projects(request: APIRequestContext) { return (await (await request.get(`${origin}/api/projects`, { headers })).json()).projects as { project_id: string; name: string }[]; }
async function open(page: Page, request: APIRequestContext, name: string) {
  const id = (await projects(request)).find(p => p.name === name)!.project_id;
  await page.goto(`${origin}/?view=clear#token=synthetic-browser-token`);
  await page.getByLabel('清除所属项目', { exact: true }).selectOption(id);
  await expect(page.getByRole('heading', { name: '项目清除', exact: true })).toBeVisible();
  return id;
}
async function preview(page: Page) {
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-clear/preview');
  await page.getByRole('button', { name: '预览清除影响', exact: true }).click();
  const r = await response; expect(r.status()).toBe(200);
  await expect(page.getByRole('region', { name: '清除影响预览' })).toBeVisible(); return r.json();
}
async function confirm(page: Page, name: string) { await page.getByLabel('确认清除项目名称', { exact: true }).fill(name); }
async function execute(page: Page) {
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-clear/execute');
  await page.getByRole('button', { name: '确认并清除整个项目', exact: true }).click(); return response;
}
async function completed(page: Page, project: string) {
  const receipt = page.getByRole('region', { name: '清除完成回执' });
  await expect(receipt).toBeVisible(); await expect(receipt).toContainText(project);
  expect(await page.evaluate(() => sessionStorage.getItem('rg_clear_receipt'))).toBeNull();
}
async function geometry(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); }

test('真实清除预览、明确确认、硬刷新回执、幂等和其他项目保留', async ({ page, request }) => {
  const name = '验收清除成功项目'; const project = await open(page, request, name);
  const retained = (await projects(request)).find(p => p.name === '保留对照项目')!.project_id;
  const before = await (await request.get(`${origin}/api/graph?project=${retained}`, { headers })).json();
  const summary = await preview(page); expect(summary.shared_objects_retained).toBeGreaterThan(0); expect(summary.objects).toBeGreaterThan(0);
  await expect(page.getByRole('button', { name: '确认并清除整个项目', exact: true })).toBeDisabled(); await confirm(page, `${name} `);
  await expect(page.getByRole('button', { name: '确认并清除整个项目', exact: true })).toBeDisabled(); await confirm(page, name);
  await page.screenshot({ path: '../.cache/frontend-project-clear-preview-desktop.png', fullPage: true });
  await page.evaluate(() => { (window as typeof window & { clearPageMemory?: string }).clearPageMemory = '先前读取的合成页面缓存'; });
  const response = await execute(page); expect(response.status()).toBe(200); const body = response.request().postDataJSON();
  await completed(page, project); expect(await page.evaluate(() => (window as typeof window & { clearPageMemory?: string }).clearPageMemory)).toBeUndefined();
  const result = await (await request.get(`${origin}/api/project-clear/status?request_id=${body.request_id}`, { headers })).json(); expect(result.state).toBe('complete');
  expect((await projects(request)).some(p => p.project_id === project)).toBe(false);
  const after = await (await request.get(`${origin}/api/graph?project=${retained}`, { headers })).json(); expect(after.claims).toEqual(before.claims);
  const replay = await request.post(`${origin}/api/project-clear/execute`, { headers, data: body }); expect(replay.status()).toBe(200); expect(await replay.json()).toEqual(result);
  await page.screenshot({ path: '../.cache/frontend-project-clear-complete-desktop.png', fullPage: true });
});

test('真实陈旧清单409不执行、不自动重试，重新预览须主动点击且请求身份正确', async ({ page, request }) => {
  const name = '验收清除过期项目'; const project = await open(page, request, name); const old = await preview(page); await confirm(page, name);
  const change = await request.post(`${origin}/api/records/question`, { headers, data: { project_id: project, text: '清除预览之后新增的合成问题', scope: null, actor: 'human:合成验收人', expected_revision: old.revision, request_id: crypto.randomUUID() } }); expect(change.status()).toBe(200);
  let sent = 0; page.on('request', r => { if (new URL(r.url()).pathname === '/api/project-clear/execute') sent++; });
  const conflict = await execute(page); expect(conflict.status()).toBe(409); const first = conflict.request().postDataJSON();
  await expect(page.getByRole('button', { name: '确认并清除整个项目', exact: true })).toBeDisabled(); expect(sent).toBe(1);
  const next = await preview(page); expect(next.preview_sha256).not.toBe(old.preview_sha256); expect(sent).toBe(1);
  const success = await execute(page); expect(success.status()).toBe(200); expect(success.request().postDataJSON().request_id).not.toBe(first.request_id); await completed(page, project);
});

test('真实占用409保留项目，归属阻碍禁执行并显示完整诊断', async ({ page, request }) => {
  const blocked = await open(page, request, '验收清除阻碍项目'); const plan = await preview(page); expect(plan.blockers.length).toBeGreaterThan(0);
  await expect(page.getByRole('region', { name: '清除影响预览' }).getByRole('alert')).toContainText(plan.blockers[0]);
  await expect(page.getByRole('button', { name: '确认并清除整个项目', exact: true })).toHaveCount(0);
  const name = '验收清除忙碌项目'; await page.getByLabel('清除所属项目', { exact: true }).selectOption({ label: name }); await preview(page); await confirm(page, name);
  const response = await execute(page); expect(response.status()).toBe(409); await expect(page.getByRole('alert').filter({ hasText: '数据目录' })).toBeVisible();
  expect((await projects(request)).some(p => p.project_id === blocked)).toBe(true); await preview(page);
  const success = await execute(page); expect(success.status()).toBe(200); await completed(page, success.request().postDataJSON().project_id);
});

test('网络未送达重试保持同UUID，双击不重复请求，完成不重复删写', async ({ page, request }) => {
  const name = '验收清除重试项目'; const project = await open(page, request, name); await preview(page); await confirm(page, name);
  const bodies: { request_id: string }[] = []; let first = true;
  await page.route('**/api/project-clear/execute', async route => { bodies.push(route.request().postDataJSON()); if (first) { first = false; return route.abort('failed'); } return route.continue(); });
  await page.getByRole('button', { name: '确认并清除整个项目', exact: true }).click();
  await expect(page.locator('.clear-error')).toBeVisible(); await expect(page.getByRole('button', { name: '确认并清除整个项目', exact: true })).toBeEnabled();
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-clear/execute');
  await page.getByRole('button', { name: '确认并清除整个项目', exact: true }).evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
  expect((await response).status()).toBe(200); await completed(page, project); expect(bodies).toHaveLength(2); expect(bodies[1].request_id).toBe(bodies[0].request_id);
});

test('真实401回到统一登录，恢复后不自动提交，重新核对项目预览', async ({ page, request }) => {
  const name = '验收清除鉴权项目'; const project = await open(page, request, name); await preview(page); await confirm(page, name);
  await page.route('**/api/project-clear/execute', async route => { const response = await route.fetch({ headers: { ...route.request().headers(), Authorization: 'Bearer invalid-synthetic-token' } }); expect(response.status()).toBe(401); await route.fulfill({ response }); });
  await execute(page); await expect(page.getByRole('heading', { name: '打开你的研究决定史' })).toBeVisible();
  await page.unroute('**/api/project-clear/execute'); let sent = 0; page.on('request', r => { if (new URL(r.url()).pathname === '/api/project-clear/execute') sent++; });
  await page.getByLabel('访问令牌', { exact: true }).fill('synthetic-browser-token'); await page.getByRole('button', { name: '进入本地工作区', exact: false }).click();
  await expect(page.getByLabel('清除所属项目', { exact: true })).toHaveValue(project); expect(sent).toBe(0); await preview(page); await confirm(page, name);
  const response = await execute(page); expect(response.status()).toBe(200); await completed(page, project);
});

test('迟到真实预览不串项目，迟到清除回执按原项目刷新且不清除新项目', async ({ page, request }) => {
  const name = '验收清除迟到项目'; const project = await open(page, request, name);
  let ready = false; let release!: () => void; let hold = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/project-clear/preview', async route => { const response = await route.fetch(); ready = true; await hold; try { await route.fulfill({ response }); } catch { /* 已取消旧预览等待。 */ } });
  await page.getByRole('button', { name: '预览清除影响', exact: true }).click(); await expect.poll(() => ready).toBe(true);
  await page.getByLabel('清除所属项目', { exact: true }).selectOption({ label: '保留对照项目' }); release(); await page.unroute('**/api/project-clear/preview');
  await expect(page.getByRole('region', { name: '清除影响预览' })).toHaveCount(0);
  await page.getByLabel('清除所属项目', { exact: true }).selectOption(project); await preview(page); await confirm(page, name);
  ready = false; hold = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/project-clear/execute', async route => { const response = await route.fetch(); expect(response.status()).toBe(200); ready = true; await hold; await route.fulfill({ response }); });
  await page.getByRole('button', { name: '确认并清除整个项目', exact: true }).click(); await expect.poll(() => ready).toBe(true);
  await page.getByLabel('清除所属项目', { exact: true }).selectOption({ label: '保留对照项目' }); release(); await completed(page, project);
  expect((await projects(request)).some(p => p.name === '保留对照项目')).toBe(true); expect((await projects(request)).some(p => p.project_id === project)).toBe(false);
});

test('真实提交后中断导致普通读取409，重新打开无项目列表仍可恢复，390屏硬刷新完成', async ({ page, request }) => {
  const name = '验收清除恢复项目'; const project = await open(page, request, name); await page.setViewportSize({ width: 390, height: 844 }); await preview(page); await confirm(page, name);
  await geometry(page); await page.screenshot({ path: '../.cache/frontend-project-clear-preview-mobile.png', fullPage: true });
  const response = await execute(page); expect(response.status()).toBe(500); const body = response.request().postDataJSON();
  await expect(page.getByRole('region', { name: '等待恢复的清除' })).toBeVisible();
  expect((await request.get(`${origin}/api/projects`, { headers })).status()).toBe(409);
  const pending = await (await request.get(`${origin}/api/project-clear/status`, { headers })).json(); expect(pending).toMatchObject({ state: 'pending', project_id: project, request_id: body.request_id });
  await page.reload(); await expect(page.getByRole('region', { name: '等待恢复的清除' })).toContainText(body.request_id);
  await page.goto(`${origin}/#token=synthetic-browser-token`);
  await page.getByRole('button', { name: '打开清除恢复', exact: true }).click();
  await expect(page.getByRole('region', { name: '等待恢复的清除' })).toContainText(body.request_id);
  await expect(page.locator('#project-picker option')).toHaveCount(1); await geometry(page);
  await page.screenshot({ path: '../.cache/frontend-project-clear-recovery-mobile.png', fullPage: true });
  const resume = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-clear/resume');
  await page.getByRole('button', { name: '恢复此项目清除', exact: true }).click(); const resumed = await resume; expect(resumed.status()).toBe(200); expect(resumed.request().postDataJSON()).toEqual({ request_id: body.request_id });
  await completed(page, project); await geometry(page); expect((await projects(request)).some(p => p.name === '保留对照项目')).toBe(true);
  expect(await page.evaluate(() => (window as typeof window & { clearInjected?: boolean }).clearInjected)).toBeUndefined();
});
