import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createWriteStream, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { FileVersionRecord } from '../../src/types';

const origin = 'http://127.0.0.1:9793'; const headers = { Authorization: 'Bearer synthetic-browser-token' };
const root = fileURLToPath(new URL('../../../', import.meta.url)); let server: ChildProcess;
test.beforeAll(async () => {
  let occupied = false; try { await fetch(origin); occupied = true; } catch { /* 无监听。 */ }
  if (occupied) throw new Error('9793 已占用，未终止已有服务。');
  mkdirSync(`${root}/.cache`, { recursive: true }); const log = createWriteStream(`${root}/.cache/frontend-version-diff-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.version_diff_browser', '--port', '9793', '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => { if (server.exitCode !== null) throw new Error('合成服务提前退出，见服务日志。'); try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; } }).toBe(200);
});
test.afterAll(async () => { if (server && server.exitCode === null) { const closed = new Promise<void>(resolve => server.once('exit', () => resolve())); server.kill('SIGINT'); await closed; } });
async function project(request: APIRequestContext, name = '验收文件差异项目') {
  const values = (await (await request.get(`${origin}/api/projects`, { headers })).json()).projects;
  return values.find((p: { name: string }) => p.name === name).project_id as string;
}
async function open(page: Page, request: APIRequestContext) {
  const id = await project(request); await page.goto(`${origin}/#token=synthetic-browser-token`);
  await page.getByLabel('当前项目', { exact: true }).selectOption(id); await page.getByRole('button', { name: '文件版本', exact: true }).click();
  await expect(page.locator('.file-version-list > li')).toHaveCount(25);
  const pageData = await (await request.get(`${origin}/api/versions?project=${id}&limit=100&offset=0`, { headers })).json();
  const versions = pageData.versions as FileVersionRecord[];
  expect(versions.every(v => v.observed_at?.endsWith('+00:00'))).toBe(true);
  return { id, versions, pair: (name: string) => versions.filter(v => v.path?.endsWith('/' + name)).sort((a, b) => a.observed_at! < b.observed_at! ? -1 : a.observed_at! > b.observed_at! ? 1 : 0) };
}
async function select(page: Page, pair: FileVersionRecord[]) {
  await page.getByLabel('比较前完整版本标识', { exact: true }).fill(pair[0].version_id!); await page.getByLabel('比较后完整版本标识', { exact: true }).fill(pair.at(-1)!.version_id!);
}
async function compare(page: Page) {
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/version-diff');
  await page.getByRole('button', { name: '比较所选版本', exact: true }).click(); return response;
}
async function result(page: Page) { const node = page.getByRole('region', { name: '文件差异结果' }); await expect(node).toBeVisible(); return node; }
async function geometry(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); }

test('真实保存字节在工作区删除后可比较，跨分页选择与完整引用保留，HTML不执行', async ({ page, request }) => {
  const { id, versions, pair } = await open(page, request); const [before, after] = pair('compare.txt');
  console.info('独立合成分页根', JSON.stringify({ repeat: test.info().repeatEachIndex, server_pid: server.pid, project_id: id, root_id: after.root_id, path: after.path, after_rank: versions.findIndex(v => v.version_id === after.version_id), before_rank: versions.findIndex(v => v.version_id === before.version_id) }));
  expect(versions.filter(v => v.observed_at === after.observed_at)).toHaveLength(9);
  expect(versions.findIndex(v => v.version_id === after.version_id)).toBeLessThan(25);
  expect(versions.findIndex(v => v.version_id === before.version_id)).toBeGreaterThanOrEqual(25);
  expect(await page.locator('.file-version-list > li').evaluateAll(nodes => nodes.map(n => n.getAttribute('data-file-version-id')))).toEqual(versions.slice(0, 25).map(v => v.version_id));
  await expect(page.getByLabel('比较前完整版本标识', { exact: true })).toHaveValue(''); await expect(page.getByLabel('比较后完整版本标识', { exact: true })).toHaveValue('');
  await page.locator(`[data-file-version-id="${after.version_id}"]`).getByRole('button', { name: '选为比较后版本', exact: true }).click();
  const secondPage = page.waitForResponse(r => new URL(r.url()).pathname === '/api/versions' && new URL(r.url()).searchParams.get('offset') === '25');
  await page.getByRole('button', { name: '下一页文件版本', exact: true }).click(); expect((await secondPage).status()).toBe(200);
  await expect(page.locator('.file-version-list > li')).toHaveCount(22);
  expect(await page.locator('.file-version-list > li').evaluateAll(nodes => nodes.map(n => n.getAttribute('data-file-version-id')))).toEqual(versions.slice(25).map(v => v.version_id));
  await expect(page.locator(`[data-file-version-id="${before.version_id}"]`)).toBeVisible();
  await page.locator(`[data-file-version-id="${before.version_id}"]`).getByRole('button', { name: '选为比较前版本', exact: true }).click();
  await expect(page.getByLabel('比较后完整版本标识', { exact: true })).toHaveValue(after.version_id!);
  const firstPage = page.waitForResponse(r => new URL(r.url()).pathname === '/api/versions' && new URL(r.url()).searchParams.get('offset') === '0');
  await page.getByRole('button', { name: '上一页文件版本', exact: true }).click(); expect((await firstPage).status()).toBe(200); await expect(page.locator('.file-version-list > li')).toHaveCount(25);
  await expect(page.getByLabel('比较前完整版本标识', { exact: true })).toHaveValue(before.version_id!);
  await expect(page.getByLabel('比较后完整版本标识', { exact: true })).toHaveValue(after.version_id!);
  await expect(page.locator(`[data-file-version-id="${after.version_id}"]`).getByRole('button', { name: '选为比较后版本', exact: true })).toHaveAttribute('aria-pressed', 'true');
  const stateBefore = await (await request.get(`${origin}/api/graph?project=${id}`, { headers })).json(); const writes: string[] = [];
  page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/') && r.method() !== 'GET') writes.push(r.url()); });
  const response = await compare(page); expect(response.status()).toBe(200); const packet = await response.json(); const panel = await result(page);
  expect(packet).toMatchObject({ byte_identity: 'different', before: { content_verified: true, claim_state: 'candidate' }, after: { content_verified: true, claim_state: 'candidate' }, diff: { available: true, complete: true } });
  await expect(page.getByTestId('version-diff-text')).toHaveText(packet.diff.text);
  expect(await page.getByTestId('version-diff-text').textContent()).toBe(packet.diff.text);
  expect(packet.diff.before_newlines).toEqual({ lf: 0, crlf: 3, final_newline: true }); expect(packet.diff.after_newlines).toEqual({ lf: 2, crlf: 0, final_newline: false }); expect(packet.diff.before_lines).toBe(3);
  await panel.getByRole('article', { name: '比较前比较来源' }).getByText('比较所用来源观察', { exact: true }).click();
  await expect(panel).toContainText(packet.before.saved_observation.observation_id); await expect(panel).toContainText(packet.before.saved_observation.snapshot.shadow_commit);
  expect(await page.evaluate(() => (window as typeof window & { versionDiffInjected?: boolean }).versionDiffInjected)).toBeUndefined(); await expect(panel.locator('script,img')).toHaveCount(0);
  const stateAfter = await (await request.get(`${origin}/api/graph?project=${id}`, { headers })).json(); expect(stateAfter).toEqual(stateBefore); expect(writes).toEqual([]);
  await panel.evaluate(n => n.scrollIntoView({ block: 'start', behavior: 'instant' })); await page.screenshot({ path: '../.cache/frontend-version-diff-result-desktop.png' });
  await panel.getByRole('article', { name: '比较前比较来源' }).locator('details').evaluate(n => n.scrollIntoView({ block: 'start', behavior: 'instant' })); await page.screenshot({ path: '../.cache/frontend-version-diff-observation-desktop.png' });
  await page.getByTestId('version-diff-text').evaluate(n => n.scrollIntoView({ block: 'center', behavior: 'instant' })); await page.screenshot({ path: '../.cache/frontend-version-diff-text-desktop.png' });
});

test('真实模式、链接目标、相同版本与空文件分别显示，不提升候选或推断运行', async ({ page, request }) => {
  const { pair } = await open(page, request);
  await select(page, pair('mode.txt')); let response = await compare(page); let packet = await response.json(); expect(packet.mode_changed).toBe(true); await expect(await result(page)).toContainText('模式集合不同');
  await select(page, pair('link')); response = await compare(page); packet = await response.json(); expect(packet.before.representation).toBe('symlink_target_bytes'); await expect(await result(page)).toContainText('不会跟随链接');
  await select(page, [pair('compare.txt')[0]]); response = await compare(page); packet = await response.json(); expect(packet.byte_identity).toBe('same'); expect(packet.diff.text).toBe(''); await expect(await result(page)).toContainText('完整文本差异为空');
  await select(page, pair('empty.txt')); response = await compare(page); packet = await response.json(); expect(packet.diff.before_lines).toBe(0); expect(packet.diff.after_lines).toBe(1); await expect(page.getByTestId('version-diff-text')).toHaveText(packet.diff.text);
});

test('真实二进制、非法UTF8、字节/行/差异超限及未保存正文保留明确缺口', async ({ page, request }) => {
  const { pair } = await open(page, request);
  for (const [name, reason] of [['binary.bin', 'binary'], ['invalid.txt', 'invalid_utf8'], ['bytes.txt', 'input_byte_limit'], ['lines.txt', 'line_limit'], ['output.txt', 'diff_byte_limit'], ['current.bin', 'unsupported_source']]) {
    await select(page, pair(name)); const response = await compare(page); expect(response.status()).toBe(200); const packet = await response.json(); expect(packet.diff).toMatchObject({ available: false, complete: false, text: null, reason });
    await expect(page.getByTestId('version-diff-unavailable')).toBeVisible(); await expect(page.getByTestId('version-diff-text')).toHaveCount(0);
    if (name === 'current.bin') { expect(packet.byte_identity).toBe('unverified'); expect(packet.mode_changed).toBeNull(); await expect(await result(page)).toContainText('文件模式变化未知'); }
  }
});

test('真实不同路径400、截止不可见404；无时区不发请求，反向比较只能主动选择', async ({ page, request }) => {
  const { pair } = await open(page, request); await select(page, [pair('compare.txt')[0], pair('mode.txt')[1]]);
  let response = await compare(page); expect(response.status()).toBe(400); await expect(page.getByRole('region', { name: '已保存版本差异' }).getByRole('alert')).toContainText('同登记根目录与路径');
  await select(page, pair('compare.txt')); await page.getByLabel('差异获知截止', { exact: true }).fill('2025-01-01T00:00:00Z'); response = await compare(page); expect(response.status()).toBe(404); await expect(page.getByRole('region', { name: '文件差异结果' })).toHaveCount(0);
  await page.getByLabel('差异获知截止', { exact: true }).fill(''); await page.getByLabel('差异发生截止', { exact: true }).fill(pair('compare.txt')[0].observed_at!); response = await compare(page); expect(response.status()).toBe(404);
  await select(page, [pair('compare.txt')[0]]); response = await compare(page); expect(response.status()).toBe(200); const historical = await response.json(); expect(historical.before.observations_total).toBe(1);
  expect(Date.parse(historical.before.saved_observation.snapshot.taken_at)).toBeLessThanOrEqual(Date.parse(historical.occurred_until));
  await page.getByLabel('差异发生截止', { exact: true }).fill(''); await select(page, pair('compare.txt'));
  let count = 0; page.on('request', r => { if (new URL(r.url()).pathname === '/api/version-diff') count++; });
  await page.getByLabel('差异获知截止', { exact: true }).fill('2026-10-10T12:00:00'); await page.getByRole('button', { name: '比较所选版本', exact: true }).click(); await expect(page.getByRole('alert').filter({ hasText: '带时区' })).toBeVisible(); expect(count).toBe(0);
  await page.getByLabel('差异获知截止', { exact: true }).fill(''); await select(page, pair('compare.txt').reverse()); response = await compare(page); const packet = await response.json(); expect(packet.before.version_id).toBe(pair('compare.txt')[1].version_id); expect(packet.diff.text).toContain('+旧数据');
});

test('真实revision冲突保留两侧，读取最新列表不自动重试，401仍走统一登录', async ({ page, request }) => {
  const { id, pair } = await open(page, request); await select(page, pair('compare.txt'));
  const graph = await (await request.get(`${origin}/api/graph?project=${id}`, { headers })).json();
  const change = await request.post(`${origin}/api/records/question`, { headers, data: { project_id: id, text: '差异修订冲突合成记录', actor: 'human:合成验收人', scope: null, expected_revision: graph.revision, request_id: crypto.randomUUID() } }); expect(change.status()).toBe(200);
  let response = await compare(page); expect(response.status()).toBe(409); await expect(page.getByRole('button', { name: '比较所选版本', exact: true })).toBeDisabled(); await expect(page.getByLabel('比较前完整版本标识', { exact: true })).toHaveValue(pair('compare.txt')[0].version_id!);
  let count = 0; page.on('request', r => { if (new URL(r.url()).pathname === '/api/version-diff') count++; });
  await page.getByRole('button', { name: '读取最新版本列表', exact: true }).click(); await expect(page.getByRole('button', { name: '比较所选版本', exact: true })).toBeEnabled(); expect(count).toBe(0);
  response = await compare(page); expect(response.status()).toBe(200); await result(page);
  await page.route('**/api/version-diff?*', async route => { const r = await route.fetch({ headers: { ...route.request().headers(), Authorization: 'Bearer invalid-synthetic-token' } }); expect(r.status()).toBe(401); await route.fulfill({ response: r }); });
  await compare(page); await expect(page.getByRole('heading', { name: '打开你的研究决定史' })).toBeVisible();
});

test('迟到真实差异不进入新条件、另一版本方向或项目；390屏正文和来源可读', async ({ page, request }) => {
  const { pair } = await open(page, request); await select(page, pair('compare.txt'));
  let ready = false; let release!: () => void; let hold = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/version-diff?*', async route => { const response = await route.fetch(); expect(response.status()).toBe(200); ready = true; await hold; try { await route.fulfill({ response }); } catch { /* 新意图已取消等待。 */ } });
  await page.getByRole('button', { name: '比较所选版本', exact: true }).click(); await expect.poll(() => ready).toBe(true);
  await page.getByLabel('差异发生截止', { exact: true }).fill('2025-01-01T00:00:00Z'); release(); await page.unroute('**/api/version-diff?*'); await expect(page.getByRole('region', { name: '文件差异结果' })).toHaveCount(0);
  await page.getByLabel('差异发生截止', { exact: true }).fill(''); ready = false; hold = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/version-diff?*', async route => { const response = await route.fetch(); ready = true; await hold; try { await route.fulfill({ response }); } catch { /* 两侧方向已改变。 */ } });
  await page.getByRole('button', { name: '比较所选版本', exact: true }).click(); await expect.poll(() => ready).toBe(true); await select(page, pair('compare.txt').reverse()); release(); await page.unroute('**/api/version-diff?*'); await expect(page.getByRole('region', { name: '文件差异结果' })).toHaveCount(0);
  await select(page, pair('compare.txt')); ready = false; hold = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/version-diff?*', async route => { const response = await route.fetch(); ready = true; await hold; try { await route.fulfill({ response }); } catch { /* 项目改变已取消等待。 */ } });
  await page.getByRole('button', { name: '比较所选版本', exact: true }).click(); await expect.poll(() => ready).toBe(true);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收文件差异项目二' }); release(); await page.unroute('**/api/version-diff?*'); await expect(page.getByRole('region', { name: '文件差异结果' })).toHaveCount(0); await expect(page.getByLabel('比较前完整版本标识', { exact: true })).toHaveValue('');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收文件差异项目' }); await expect(page.locator('.file-version-list > li')).toHaveCount(25); await select(page, pair('compare.txt')); await compare(page);
  await page.setViewportSize({ width: 390, height: 844 }); await geometry(page); const panel = await result(page); await panel.getByRole('article', { name: '比较前比较来源' }).getByText('比较所用来源观察', { exact: true }).click();
  await page.getByRole('region', { name: '已保存版本差异' }).evaluate(n => n.scrollIntoView({ block: 'start', behavior: 'instant' })); await page.screenshot({ path: '../.cache/frontend-version-diff-selection-mobile.png' });
  await panel.getByRole('article', { name: '比较前比较来源' }).evaluate(n => n.scrollIntoView({ block: 'start', behavior: 'instant' })); await page.screenshot({ path: '../.cache/frontend-version-diff-result-mobile.png' });
  await panel.getByRole('article', { name: '比较前比较来源' }).locator('details').evaluate(n => n.scrollIntoView({ block: 'start', behavior: 'instant' })); await page.screenshot({ path: '../.cache/frontend-version-diff-observation-mobile.png' }); await geometry(page);
  await page.getByTestId('version-diff-text').evaluate(n => n.scrollIntoView({ block: 'center', behavior: 'instant' })); await page.screenshot({ path: '../.cache/frontend-version-diff-text-mobile.png' }); await expect(page.getByTestId('version-diff-text')).toContainText('<script>'); expect(await page.getByTestId('version-diff-text').evaluate(n => n.scrollWidth <= n.clientWidth)).toBe(true);
  expect(await page.evaluate(() => (window as typeof window & { versionDiffInjected?: boolean }).versionDiffInjected)).toBeUndefined();
});
