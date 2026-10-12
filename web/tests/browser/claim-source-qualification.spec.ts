import { openWorkspaceView } from './navigation';
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWriteStream, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const origin = 'http://127.0.0.1:9805';
const headers = { Authorization: 'Bearer synthetic-claim-source-token' };
type Name = 'excluded' | 'pending_newer' | 'pending_older' | 'unavailable';
interface Identity {
  project_id: string; base_claim_id: number; entity_id: string; model_claim_id: number;
  manual_claim_id: number | null; scope: Record<string, string>; event_id: number;
  byte_start: number; byte_end: number; quote_sha256: string;
}
let server: ChildProcess | undefined;
let cases: Record<Name, Identity>;
let captured = 0;
function capture(tag: string, data: unknown) {
  const directory = `${root}/.cache/frontend-claim-source-api`;
  mkdirSync(directory, { recursive: true });
  writeFileSync(`${directory}/${String(++captured).padStart(3, '0')}-${tag}.json`, JSON.stringify(data, null, 2) + '\n');
}
test.beforeAll(async () => {
  let response: Response | undefined;
  try { response = await fetch(`${origin}/api/projects`, { headers }); } catch { /* 无监听。 */ }
  if (response) throw new Error('9805 已有服务，未复用未知合成库或停止未知进程。');
  mkdirSync(`${root}/.cache`, { recursive: true });
  const log = createWriteStream(`${root}/.cache/frontend-claim-source-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.claim_source_qualification_browser', '--port', '9805', '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => {
    if (server?.exitCode !== null) throw new Error('合成服务提前退出，请查看原始日志。');
    try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; }
  }).toBe(200);
  const index = JSON.parse(readFileSync(`${root}/.cache/frontend-claim-source-index.json`, 'utf8'));
  expect(index.synthetic_only).toBe(true); cases = index.cases;
  expect(new Set(Object.values(cases).map(item => item.project_id)).size).toBe(4);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) {
    const closed = new Promise<number | null>(resolve => server!.once('exit', code => resolve(code)));
    server.kill('SIGINT'); expect(await closed).toBe(0);
    expect(JSON.parse(readFileSync(`${root}/.cache/frontend-claim-source-audit.json`, 'utf8'))).toMatchObject({ 合成库逐表导出未改变: true });
  }
});
async function json(request: APIRequestContext, path: string, tag: string) {
  const response = await request.get(`${origin}/api${path}`, { headers });
  expect(response.status()).toBe(200); const data = await response.json(); capture(tag, data); return data;
}
async function proof(request: APIRequestContext, name: Name) {
  const item = cases[name]; const status = name.startsWith('pending') ? 'pending' : name;
  const claim = await json(request, `/claims/${item.model_claim_id}`, `${name}-claim-before`);
  expect(claim.claim).toMatchObject({ effective_state: 'confirmed', claim_state: 'candidate', input_eligible: false,
    confirmation_source: 'human', source_qualification: { status, input_eligible: false } });
  expect(claim.claim.review_history).toHaveLength(1);
  const graph = await json(request, `/semantic-graph?project=${item.project_id}&collection=nodes&limit=100`, `${name}-authoritative-state`);
  const target = graph.items.find((node: { entity_id: string }) => node.entity_id === item.entity_id);
  expect(target.states.adoption.state).toBe(name === 'pending_older' ? 'accepted' : 'unknown');
  expect(target.states.adoption.source_issues).toContainEqual({ claim_id: item.model_claim_id, status });
  return { item, status, claim };
}
async function enter(page: Page, item: Identity) {
  await page.goto(`${origin}/#token=synthetic-claim-source-token`);
  await page.getByLabel('当前项目', { exact: true }).selectOption(item.project_id);
  await page.getByRole('button', { name: '研究问题', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'budget 合成假说', exact: true })).toBeVisible();
}
async function graph(page: Page, item: Identity, state: string) {
  await page.getByRole('button', { name: '研究图', exact: true }).click();
  await expect.poll(async () => {
    const reading = await page.locator('[data-research-summary]').getAttribute('data-reading');
    return reading ? JSON.parse(reading).project_id : null;
  }).toBe(item.project_id);
  const node = page.locator(`.react-flow__node[data-id^="${item.entity_id}::"]`);
  await expect(node.locator('.node-dimensions')).toContainText(`采用 ${state}`);
  await expect(node.locator('.badge')).toHaveText('已确认');
  const issues = JSON.parse((await node.locator('[data-source-issues]').getAttribute('data-source-issues'))!);
  expect(issues).toContainEqual({ claim_id: item.model_claim_id, status: cases.pending_older === item || cases.pending_newer === item ? 'pending' : cases.excluded === item ? 'excluded' : 'unavailable' });
  await node.scrollIntoViewIfNeeded();
  await expect.poll(() => node.evaluate(element => {
    const box = element.getBoundingClientRect(); const canvas = element.closest('.graph-canvas')!.getBoundingClientRect();
    return Math.min(box.right, canvas.right, innerWidth) > Math.max(box.left, canvas.left, 0)
      && Math.min(box.bottom, canvas.bottom, innerHeight) > Math.max(box.top, canvas.top, 0);
  })).toBe(true);
  return node;
}
async function history(page: Page, item: Identity, node: Locator, status: string) {
  await node.locator('strong').first().click();
  await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  const retained = page.locator(`.graph-retained [data-claim-id="${item.model_claim_id}"]`);
  await expect(retained.locator('.badge')).toHaveText('已确认');
  await expect(retained.locator('[data-source-qualification]')).toHaveAttribute('data-source-qualification', status);
  await retained.getByRole('button').first().click();
  const drawer = page.getByRole('dialog', { name: `记录 ${item.model_claim_id} 详情`, exact: true });
  await expect(drawer.locator('.badge')).toHaveText('已确认');
  await expect(drawer.locator('.drawer-actions button')).toHaveCount(0);
  const note = drawer.locator(`[data-source-claim-id="${item.model_claim_id}"]`);
  await expect(note).toHaveAttribute('data-source-qualification', status);
  await note.evaluate(element => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
  const geometry = await note.evaluate(element => {
    const box = element.getBoundingClientRect();
    const header = element.closest('[role="dialog"]')!.querySelector(':scope > header')!.getBoundingClientRect();
    return { width: box.width, height: box.height, left: box.left, right: box.right, top: box.top, bottom: box.bottom,
      headerBottom: header.bottom, viewport: innerWidth, viewportHeight: innerHeight, document: document.documentElement.scrollWidth };
  });
  capture(`visible-diagnostic-${item.model_claim_id}`, geometry);
  expect(geometry.width).toBeGreaterThan(0); expect(geometry.height).toBeGreaterThan(0);
  expect(geometry.left).toBeGreaterThanOrEqual(0); expect(geometry.right).toBeLessThanOrEqual(geometry.viewport);
  expect(geometry.top).toBeGreaterThanOrEqual(geometry.headerBottom); expect(geometry.bottom).toBeLessThanOrEqual(geometry.viewportHeight);
  expect(geometry.document).toBeLessThanOrEqual(geometry.viewport);
  return drawer;
}

test('已确认注入模型记录保留审核和原文，当前采用未知，当前与历史提示独立', async ({ page, request }) => {
  const { item, status, claim } = await proof(request, 'excluded'); await enter(page, item);
  await openWorkspaceView(page, '决定时间线');
  await expect(page.locator(`[data-source-claim-id="${item.model_claim_id}"]`)).toHaveAttribute('data-source-qualification', 'excluded');
  const node = await graph(page, item, '未知'); const drawer = await history(page, item, node, status);
  await page.screenshot({ path: '../.cache/frontend-claim-source-excluded-desktop.png' });
  await drawer.locator('.evidence-link').click();
  await expect(page.getByTestId('evidence-quote')).toHaveText('budget');
  const quote = await page.getByTestId('evidence-quote').textContent();
  expect(createHash('sha256').update(quote!).digest('hex')).toBe(item.quote_sha256);
  const raw = await json(request, `/evidence/${item.event_id}?start=${item.byte_start}&end=${item.byte_end}&context=0`, 'excluded-raw');
  expect(raw.event).toMatchObject({ exclude_reason: null, input_exclude_reason: 'injected_by_rg', quote_sha256: item.quote_sha256 });
  expect(await json(request, `/claims/${item.model_claim_id}`, 'excluded-claim-after')).toEqual(claim);
});

test('较新未核对确认阻止回退旧采用，较旧未核对记录不遮挡之后的合格采用', async ({ page, request }) => {
  for (const name of ['pending_newer', 'pending_older'] as const) {
    const { item, status, claim } = await proof(request, name); await enter(page, item);
    const node = await graph(page, item, name === 'pending_older' ? '采用' : '未知');
    await history(page, item, node, status);
    if (name === 'pending_newer') await page.screenshot({ path: '../.cache/frontend-claim-source-pending-desktop.png' });
    expect(await json(request, `/claims/${item.model_claim_id}`, `${name}-claim-after`)).toEqual(claim);
  }
});

test('保存来源对象不可用时保留已确认历史，不能显示旧采用或自动驳回', async ({ page, request }) => {
  const { item, status, claim } = await proof(request, 'unavailable'); await enter(page, item);
  const node = await graph(page, item, '未知'); await history(page, item, node, status);
  expect(claim.claim.source_qualification.references).toEqual(expect.arrayContaining([expect.objectContaining({ status: 'unavailable', reason: 'source_unavailable' })]));
  await page.screenshot({ path: '../.cache/frontend-claim-source-unavailable-desktop.png' });
  expect(await json(request, `/claims/${item.model_claim_id}`, 'unavailable-claim-after')).toEqual(claim);
});

test('390直接切换四项目，来源诊断与确认状态同屏可见且不跨项目沿用', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/#token=synthetic-claim-source-token`);
  for (const name of ['excluded', 'pending_newer', 'pending_older', 'unavailable'] as const) {
    const { item, status } = await proof(request, name);
    await page.getByLabel('当前项目', { exact: true }).selectOption(item.project_id);
    const node = await graph(page, item, name === 'pending_older' ? '采用' : '未知');
    const drawer = await history(page, item, node, status);
    expect(await drawer.locator('[data-source-claim-id]').getAttribute('data-source-claim-id')).toBe(String(item.model_claim_id));
    if (name === 'excluded' || name === 'pending_newer') await page.screenshot({ path: `../.cache/frontend-claim-source-${name}-mobile.png` });
    await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  }
});
