import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createWriteStream, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { foldGroup, graphNodeId, projectGraph } from '../../src/model';
import { appendResearchPage, parseResearchPage, researchQuery } from '../../src/researchGraph';
import type { ResearchPage } from '../../src/researchGraph';
import type { Claim, PersonalViewRecord, ResearchReading } from '../../src/types';

const origin = 'http://127.0.0.1:9802', token = 'synthetic-personal-view-token';
const headers = { Authorization: `Bearer ${token}` }, root = fileURLToPath(new URL('../../../', import.meta.url));
let server: ChildProcess | undefined, directory: string;
interface Fixture { project: string; view_secondary: string; chain: number[]; total: number }
async function stop() { if (server && server.exitCode === null) { const closed = new Promise<void>(resolve => server!.once('exit', () => resolve())); server.kill('SIGINT'); await closed; } }
async function start() {
  const log = createWriteStream(`${root}/.cache/frontend-personal-view-state-server.log`, { flags: 'a' });
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.view_state_browser', '--fixture-dir', directory, '--port', '9802', '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => { if (server?.exitCode !== null) throw new Error('9802合成服务提前退出，见服务日志。'); try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; } }).toBe(200);
}
test.beforeAll(async () => {
  let occupied = false; try { await fetch(origin); occupied = true; } catch { /* 无监听。 */ }
  if (occupied) throw new Error('9802已占用，未终止其它进程。');
  directory = mkdtempSync(join(tmpdir(), 'researchgraph-personal-view-'));
  const log = createWriteStream(`${root}/.cache/frontend-personal-view-state-prepare.log`);
  const prepare = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.view_state_browser', '--fixture-dir', directory, '--prepare'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  prepare.stdout!.pipe(log); prepare.stderr!.pipe(log);
  const code = await new Promise<number | null>(resolve => prepare.once('exit', resolve)); expect(code).toBe(0); await start();
});
test.afterAll(async () => { await stop(); if (directory) rmSync(directory, { recursive: true, force: true }); });
async function cases(request: APIRequestContext): Promise<Fixture> { const response = await request.get(`${origin}/synthetic-personal-view/cases`, { headers }); expect(response.status()).toBe(200); return response.json(); }
async function head(request: APIRequestContext, project: string, user: string): Promise<PersonalViewRecord> { const response = await request.get(`${origin}/api/view-state?${new URLSearchParams({ project, user })}`, { headers }); expect(response.status()).toBe(200); return response.json(); }
async function audit(request: APIRequestContext) { return (await (await request.get(`${origin}/synthetic-personal-view/audit`, { headers })).json()) as { research_sha256: string; revision: number; views: number; model_attempts: number }; }
async function complete(page: Page) {
  await expect(page.locator('[data-research-summary]')).toContainText('双时间内全部 L2 登记记录');
  await expect(page.getByRole('button', { name: '重新读取研究图', exact: true })).toBeEnabled();
}
async function ready(page: Page) { await complete(page); await expect(page.getByRole('button', { name: '保存个人视图', exact: true })).toBeEnabled(); }
async function open(page: Page, user: string, project = '验收语义图项目') {
  await page.goto(`${origin}/#token=${token}`); await page.getByLabel('当前项目', { exact: true }).selectOption({ label: project });
  await page.getByRole('button', { name: '研究图', exact: true }).click(); await complete(page);
  await page.getByLabel('个人视图标识', { exact: true }).fill(user); await ready(page);
}
async function save(page: Page): Promise<PersonalViewRecord> {
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/view-state' && r.request().method() === 'POST');
  await page.getByRole('button', { name: '保存个人视图', exact: true }).click(); const result = await response; expect(result.status()).toBe(200);
  await expect(page.locator('[data-personal-info]')).toContainText('个人视图已保存'); const saved = await result.json(); expect(saved.view).toEqual(result.request().postDataJSON().view); return saved;
}
async function restore(page: Page) { await page.getByRole('button', { name: '读取已保存视图', exact: true }).click(); await expect(page.locator('.graph-notice')).toContainText('恢复个人布局'); await ready(page); }
async function reading(page: Page): Promise<ResearchReading> { return JSON.parse((await page.locator('[data-research-summary]').getAttribute('data-reading'))!); }
async function records(request: APIRequestContext, value: ResearchReading) {
  let offset = 0, first: ResearchPage | undefined, claims: Claim[] = [];
  do {
    const params = new URLSearchParams(Object.entries({ ...researchQuery(value), collection: 'claims', limit: 100, offset }).map(([key, value]) => [key, String(value)]));
    const response = await request.get(`${origin}/api/semantic-graph?${params}`, { headers }); expect(response.status()).toBe(200);
    const page = parseResearchPage(await response.json(), value.project_id, offset, first); first ??= page; claims = appendResearchPage(claims, page);
    if (page.next_offset === null) break; offset = page.next_offset;
  } while (true);
  return claims;
}
async function select(page: Page, members: Claim[]) {
  const picker = page.locator('.graph-node-picker'); if (!await picker.evaluate(node => (node as HTMLDetailsElement).open)) await picker.locator('summary').click();
  for (const claim of members) await picker.locator(`input[data-node-id=${JSON.stringify(graphNodeId(claim))}]`).check();
}
async function layout(page: Page) {
  return page.evaluate(() => ({
    positions: Object.fromEntries([...document.querySelectorAll('.react-flow__node')].map(node => { const p = /translate\(([-\d.e]+)px,\s*([-\d.e]+)px\)/.exec((node as HTMLElement).style.transform)!; return [node.getAttribute('data-id'), { x: Number(p[1]), y: Number(p[2]) }]; })) as Record<string, { x: number; y: number }>,
    viewport: (() => { const p = /translate\(([-\d.e]+)px,\s*([-\d.e]+)px\)\s*scale\(([-\d.e]+)\)/.exec((document.querySelector('.react-flow__viewport') as HTMLElement).style.transform)!; return { x: Number(p[1]), y: Number(p[2]), zoom: Number(p[3]) }; })(),
    edges: [...document.querySelectorAll('.react-flow__edge')].map(node => node.getAttribute('data-id')).sort(),
  }));
}
function closePositions(actual: Record<string, { x: number; y: number }>, expected: Record<string, { x: number; y: number }>) {
  expect(Object.keys(actual).sort()).toEqual(Object.keys(expected).sort());
  for (const [id, position] of Object.entries(expected)) { expect(actual[id].x).toBeCloseTo(position.x, 2); expect(actual[id].y).toBeCloseTo(position.y, 2); }
}
function closeViewport(actual: { x: number; y: number; zoom: number }, expected: { x: number; y: number; zoom: number }) {
  expect(actual.x).toBeCloseTo(expected.x, 2); expect(actual.y).toBeCloseTo(expected.y, 2); expect(actual.zoom).toBeCloseTo(expected.zoom, 5);
}
async function reopen(page: Page) {
  await page.reload(); await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收语义图项目' });
  await page.getByRole('button', { name: '研究图', exact: true }).click(); await ready(page);
}
async function canvasVisible(page: Page) {
  const canvas = page.getByTestId('research-canvas'); await canvas.evaluate(node => node.scrollIntoView({ block: 'center' }));
  await expect.poll(() => canvas.evaluate(node => { const c = node.getBoundingClientRect(); return [...node.querySelectorAll('.react-flow__node')].some(element => { const b = element.getBoundingClientRect(); return b.width > 0 && b.height > 0 && Math.min(b.right, c.right, innerWidth) > Math.max(b.left, c.left, 0) && Math.min(b.bottom, c.bottom, innerHeight) > Math.max(b.top, c.top, 0); }); })).toBe(true);
}
async function viewportSettled(page: Page) {
  await expect.poll(() => page.evaluate(async () => {
    const node = document.querySelector('.react-flow__viewport') as HTMLElement;
    const first = node.style.transform; await new Promise(resolve => requestAnimationFrame(resolve));
    const second = node.style.transform; await new Promise(resolve => requestAnimationFrame(resolve));
    return first === second && second === node.style.transform;
  })).toBe(true);
}
async function drag(page: Page, id: string) {
  await canvasVisible(page); const node = page.locator(`.react-flow__node[data-id=${JSON.stringify(id)}]`); const box = (await node.locator('.node-top').boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 + 38, box.y + box.height / 2 + 20, { steps: 5 }); await page.mouse.up();
}

test('实际拖动和版本选择保存完整个人视图，服务与页面重开后恢复视角且研究数据不变', async ({ page, request }) => {
  const fixture = await cases(request), before = await audit(request); await open(page, '视图重启验收');
  const claims = await records(request, await reading(page)), graph = projectGraph(claims); const multi = [...graph.versions].find(([, versions]) => versions.length > 1)!;
  await page.locator(`.react-flow__node[data-id=${JSON.stringify(multi[0])}] select`).selectOption(String(multi[1][1].claim_id));
  const first = graphNodeId(claims.find(c => c.claim_id === fixture.chain[0])!), initial = await layout(page); await drag(page, first);
  expect((await layout(page)).positions[first]).not.toEqual(initial.positions[first]);
  await page.locator('.react-flow__controls-zoomin').click(); await page.locator('.react-flow__controls-zoomin').click();
  const moved = await layout(page), saved = await save(page); closePositions(saved.view!.positions, moved.positions); closeViewport(saved.view!.viewport, moved.viewport); expect(saved.view!.selected_versions[multi[0]]).toBe(multi[1][1].claim_id);
  const after = await audit(request); expect(after.research_sha256).toBe(before.research_sha256); expect(after.revision).toBe(before.revision); expect(after.views).toBe(before.views + 1); expect(after.model_attempts).toBe(0);
  await stop(); await start(); expect((await head(request, fixture.project, '视图重启验收')).view).toEqual(saved.view);
  await reopen(page); await restore(page); await expect.poll(() => layout(page)).toEqual(moved);
  await expect(page.locator(`.react-flow__node[data-id=${JSON.stringify(multi[0])}] select`)).toHaveValue(String(multi[1][1].claim_id));
  expect(await reading(page)).toEqual(saved.view!.reading); await canvasVisible(page); await page.screenshot({ path: '../.cache/frontend-personal-view-state-restored-desktop.png' });
  expect(await page.evaluate(() => Object.keys(localStorage).filter(key => /view.state|personal.view/i.test(key)))).toEqual([]);
});

test('过程组拖动只移动入口原位置，恢复重新验证边界证据且390可展开回原节点', async ({ page, request }) => {
  const fixture = await cases(request); await page.setViewportSize({ width: 390, height: 844 }); await open(page, '视图过程验收');
  const claims = await records(request, await reading(page)), members = fixture.chain.slice(1, 3).map(id => claims.find(c => c.claim_id === id)!); const graph = projectGraph(claims), proof = foldGroup(members.map(graphNodeId), graph.edges, claims);
  const original = await layout(page); await select(page, members); await page.getByLabel('过程组名称', { exact: true }).fill('已保留撤回和阴性记录');
  await page.getByRole('button', { name: '折叠所选（2）', exact: true }).click(); await expect(page.locator('.process-node')).toHaveCount(1);
  await page.getByRole('button', { name: '聚焦过程组', exact: true }).click(); await drag(page, 'view-process-group');
  const grouped = await layout(page), saved = await save(page); closePositions({ entry: saved.view!.positions[proof.entry] }, { entry: grouped.positions['view-process-group'] });
  expect(Object.keys(saved.view!.positions).sort()).toEqual(Object.keys(original.positions).sort()); expect(saved.view!.positions).not.toHaveProperty('view-process-group');
  for (const [id, position] of Object.entries(original.positions)) if (id !== proof.entry) closePositions({ node: saved.view!.positions[id] }, { node: position });
  await canvasVisible(page); await page.screenshot({ path: '../.cache/frontend-personal-view-state-fold-mobile.png' });
  await reopen(page); await restore(page); await expect(page.locator('.process-node')).toHaveCount(1); closeViewport((await layout(page)).viewport, saved.view!.viewport);
  expect(await page.locator('[data-original-edge]').evaluateAll(nodes => nodes.map(node => JSON.parse(node.getAttribute('data-original-edge')!)))).toEqual([...proof.internalEdges, ...proof.boundaryEdges]);
  expect(await page.locator('.graph-retained article').evaluateAll(nodes => nodes.map(node => Number(node.getAttribute('data-claim-id'))))).toEqual(proof.claimIds);
  await page.getByRole('button', { name: '聚焦过程组', exact: true }).click(); await canvasVisible(page); await viewportSettled(page);
  await expect.poll(() => page.locator('.process-node').evaluate(node => {
    const b = node.getBoundingClientRect(), c = document.querySelector('[data-testid="research-canvas"]')!.getBoundingClientRect(), header = document.querySelector('.project-picker')!.getBoundingClientRect();
    return b.x >= c.x && b.right <= c.right && b.y >= Math.max(c.y, header.bottom) && b.bottom <= Math.min(c.bottom, innerHeight);
  })).toBe(true);
  expect((await head(request, fixture.project, '视图过程验收')).view).toEqual(saved.view);
  await page.screenshot({ path: '../.cache/frontend-personal-view-state-fold-focused-mobile.png' });
  await page.getByRole('button', { name: '展开过程组', exact: true }).click(); await expect(page.locator('.react-flow__node')).toHaveCount(Object.keys(saved.view!.positions).length); closePositions((await layout(page)).positions, saved.view!.positions); await expect.poll(async () => (await layout(page)).edges).toEqual(original.edges);
  await page.getByRole('button', { name: '适配画布', exact: true }).click(); await canvasVisible(page); await viewportSettled(page); await page.screenshot({ path: '../.cache/frontend-personal-view-state-expanded-mobile.png' });
  const panel = page.getByRole('region', { name: '个人研究图视图' }); await panel.evaluate(node => node.scrollIntoView({ block: 'center' }));
  for (const label of ['个人视图标识']) { const b = (await page.getByLabel(label, { exact: true }).boundingBox())!; expect(b.x).toBeGreaterThanOrEqual(0); expect(b.x + b.width).toBeLessThanOrEqual(390); }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.screenshot({ path: '../.cache/frontend-personal-view-state-controls-mobile.png' });
});

test('两个姓名与项目独立，真实CAS冲突不覆盖其它页面，主动读取后才可再保存', async ({ page, request }) => {
  const fixture = await cases(request); await open(page, '视图甲'); const first = await save(page);
  await page.getByLabel('个人视图标识', { exact: true }).fill('视图乙'); await ready(page); expect((await head(request, fixture.project, '视图乙')).view).toBeNull(); const second = await save(page);
  expect(second.view_id).not.toBe(first.view_id); expect((await head(request, fixture.project, '视图甲')).view_id).toBe(first.view_id);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收个人视图项目二' }); await ready(page); expect((await head(request, fixture.view_secondary, '视图乙')).view).toBeNull(); const other = await save(page); expect(other.view!.reading.project_id).toBe(fixture.view_secondary);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收语义图项目' }); await ready(page);
  const outside = await request.post(`${origin}/api/view-state`, { headers, data: { project_id: fixture.project, user: '视图乙', expected_view_id: second.view_id, view: { ...second.view!, viewport: { x: 10, y: 20, zoom: 0.4 } } } }); expect(outside.status()).toBe(200); const latest = await outside.json();
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/view-state' && r.request().method() === 'POST'); await page.getByRole('button', { name: '保存个人视图', exact: true }).click(); expect((await response).status()).toBe(409);
  await expect(page.locator('[data-personal-error]')).toContainText('主动读取'); expect((await head(request, fixture.project, '视图乙')).view_id).toBe(latest.view_id);
  await page.screenshot({ path: '../.cache/frontend-personal-view-state-cas-desktop.png' }); await restore(page); await expect.poll(async () => (await layout(page)).viewport).toEqual(latest.view.viewport);
  const replaced = await save(page); expect(replaced.view_id).not.toBe(latest.view_id); expect((await head(request, fixture.view_secondary, '视图乙')).view_id).toBe(other.view_id);
});

test('图变化真实409及旧保存记录仍保留，不能自动恢复或用新修订覆盖保存条件', async ({ page, request }) => {
  const fixture = await cases(request); await open(page, '视图旧修订'); const saved = await save(page), original = await layout(page);
  const advance = await request.post(`${origin}/synthetic-personal-view/advance`, { headers, data: {} }); expect(advance.status()).toBe(200);
  const pending = page.waitForResponse(r => new URL(r.url()).pathname === '/api/view-state' && r.request().method() === 'POST'); await page.getByRole('button', { name: '保存个人视图', exact: true }).click(); expect((await pending).status()).toBe(409);
  expect((await head(request, fixture.project, '视图旧修订')).view_id).toBe(saved.view_id);
  await page.getByRole('button', { name: '读取已保存视图', exact: true }).click(); await expect(page.locator('[data-restore-error]')).toContainText('修订已变化'); expect(await layout(page)).toEqual(original);
  const still = await head(request, fixture.project, '视图旧修订'); expect(still.view).toEqual(saved.view); expect(still.current_revision).toBeGreaterThan(saved.view!.reading.revision);
  await page.screenshot({ path: '../.cache/frontend-personal-view-state-revision-desktop.png' });
  await page.getByRole('button', { name: '重新读取研究图', exact: true }).click(); await ready(page); const current = await save(page); expect(current.view!.reading.revision).toBe(still.current_revision);
});

test('受控transport晚200和401在条件、姓名、项目、页面与epoch变化后不误报成功或登出', async ({ page }) => {
  await page.addInitScript(() => { const actual = window.fetch.bind(window); (window as unknown as { personalLate: string[] }).personalLate = []; window.fetch = async (input, options) => { if (!/\/api\/(view-state|semantic-graph)/.test(String(input))) return actual(input, options); const response = await actual(input, { ...options, signal: undefined }); const parse = response.json.bind(response); response.json = async () => { const data = await parse(), marker = response.headers.get('x-synthetic-late'); if (marker) (window as unknown as { personalLate: string[] }).personalLate.push(marker); return data; }; return response; }; });
  const variants = [{ method: 'POST', status: 200, action: '条件' }, { method: 'GET', status: 401, action: '姓名' }, { method: 'GET', status: 200, action: '项目' }, { method: 'GET', status: 401, action: '页面' }, { method: 'GET', status: 200, action: 'epoch' }];
  for (const [index, item] of variants.entries()) {
    await open(page, `晚回包${item.action}`); const saved = await save(page);
    let release = () => {}, readyHeld = () => {}, first = true; const held = new Promise<void>(resolve => { release = resolve; }), started = new Promise<void>(resolve => { readyHeld = resolve; });
    await page.route('**/api/view-state**', async route => { if (!first || route.request().method() !== item.method) { await route.continue(); return; } first = false; const response = await route.fetch(item.status === 401 ? { headers: { ...route.request().headers(), Authorization: 'Bearer synthetic-wrong-token' } } : {}); expect(response.status()).toBe(item.status); readyHeld(); await held; await route.fulfill({ response, headers: { ...response.headers(), 'x-synthetic-late': `late-${index}` } }); });
    await page.getByRole('button', { name: item.method === 'POST' ? '保存个人视图' : '读取已保存视图', exact: true }).click(); await started;
    if (item.action === '条件') await page.getByLabel('研究图发生时间截止', { exact: true }).fill('2026-01-01T00:00:00Z');
    if (item.action === '姓名') await page.getByLabel('个人视图标识', { exact: true }).fill('晚回包新姓名');
    if (item.action === '项目') await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收个人视图项目二' });
    if (item.action === '页面') await page.getByRole('button', { name: '研究问题', exact: true }).click();
    if (item.action === 'epoch') await page.getByRole('button', { name: '刷新数据', exact: true }).click();
    release(); await expect.poll(() => page.evaluate(marker => (window as unknown as { personalLate: string[] }).personalLate.includes(marker), `late-${index}`)).toBe(true);
    await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toHaveCount(0);
    if (item.action === '条件') { await expect(page.locator('[data-view-id]')).toHaveAttribute('data-view-id', String(saved.view_id)); await expect(page.locator('[data-personal-info]')).not.toContainText('个人视图已保存'); }
    if (item.action === '姓名') await expect(page.getByLabel('个人视图标识', { exact: true })).toHaveValue('晚回包新姓名');
    if (item.action === '项目') { await ready(page); expect((await reading(page)).project_id).not.toBe(saved.project_id); }
    if (item.action === '页面') await expect(page.getByRole('region', { name: '个人研究图视图' })).toHaveCount(0);
    if (item.action === 'epoch') { await ready(page); await expect(page.locator('.graph-notice').filter({ hasText: '恢复个人布局' })).toHaveCount(0); }
    await page.unroute('**/api/view-state**');
  }
  await open(page, '恢复途中版本修改'); await save(page);
  const selector = page.locator('.node-version select').first(), previous = await selector.inputValue();
  const option = (await selector.locator('option').evaluateAll(nodes => nodes.map(node => (node as HTMLOptionElement).value))).find(value => value && value !== previous)!;
  let release = () => {}, began = () => {}; const held = new Promise<void>(resolve => { release = resolve; }), started = new Promise<void>(resolve => { began = resolve; });
  await page.route('**/api/semantic-graph?*', async route => { if (new URL(route.request().url()).searchParams.get('offset') !== '100') { await route.continue(); return; } const response = await route.fetch(); expect(response.status()).toBe(200); began(); await held; await route.fulfill({ response, headers: { ...response.headers(), 'x-synthetic-late': 'restore-page' } }); });
  await page.getByRole('button', { name: '读取已保存视图', exact: true }).click(); await started;
  await selector.selectOption(option); release(); await expect.poll(() => page.evaluate(() => (window as unknown as { personalLate: string[] }).personalLate.includes('restore-page'))).toBe(true);
  await expect(selector).toHaveValue(option); await expect(page.locator('.graph-notice').filter({ hasText: '恢复个人布局' })).toHaveCount(0); await page.unroute('**/api/semantic-graph?*');
});
