import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createWriteStream, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import type { FragmentPage, FragmentReceipt } from '../../src/projectFragments';
const root = fileURLToPath(new URL('../../../', import.meta.url));
const origin = 'http://127.0.0.1:9809', headers = { Authorization: 'Bearer synthetic-project-fragments-token' };
interface Source { session_pk: number; file_instance_id: number; path: string; parser: string; events: { event_id: number; byte_start: number; byte_end: number; object_sha256: string }[] }
interface Index { first_project: string; second_project: string; sources: Source[]; candidate: number; confirmed: number }
let directory: string, index: Index, server: ChildProcess | undefined, sequence = 0;
function capture(tag: string, value: unknown) { const dir = `${root}/.cache/frontend-project-fragments-api`; mkdirSync(dir, { recursive: true }); writeFileSync(`${dir}/${String(++sequence).padStart(3, '0')}-${tag}.json`, JSON.stringify(value, null, 2) + '\n'); }
test.beforeAll(async () => {
  let occupied = false; try { await fetch(origin); occupied = true; } catch { /* 不停止未知进程。 */ }
  if (occupied) throw new Error('9809已有监听服务。');
  directory = mkdtempSync(join(tmpdir(), 'researchgraph-fragments-')); mkdirSync(`${root}/.cache`, { recursive: true });
  const prepareLog = createWriteStream(`${root}/.cache/frontend-project-fragments-prepare.log`);
  const prepare = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.project_fragments_browser', '--fixture-dir', directory, '--prepare'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] }); prepare.stdout!.pipe(prepareLog); prepare.stderr!.pipe(prepareLog);
  expect(await new Promise<number | null>(resolve => prepare.once('exit', resolve))).toBe(0);
  index = JSON.parse(readFileSync(`${root}/.cache/frontend-project-fragments-index.json`, 'utf8'));
  const log = createWriteStream(`${root}/.cache/frontend-project-fragments-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.project_fragments_browser', '--fixture-dir', directory, '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] }); server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => { if (server?.exitCode !== null) throw new Error('合成服务提前退出'); try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; } }).toBe(200);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) { const stopped = new Promise<number | null>(resolve => server!.once('exit', resolve)); server.kill('SIGINT'); expect(await stopped).toBe(0); expect(JSON.parse(readFileSync(`${root}/.cache/frontend-project-fragments-audit.json`, 'utf8'))).toMatchObject({ 原件候选审核未改写: true }); }
  if (directory) rmSync(directory, { recursive: true, force: true });
});
const source = (tool = 'codex') => index.sources.find(s => s.parser === tool)!;
const panel = (page: Page) => page.getByRole('dialog', { name: '物理片段与项目归属', exact: true });
async function get(request: APIRequestContext, s = source(), fixed?: FragmentPage): Promise<FragmentPage> {
  const query = new URLSearchParams({ session: String(s.session_pk), source: String(s.file_instance_id), limit: '20' });
  if (fixed) { query.set('after_event', String(fixed.next_after_event)); query.set('snapshot', String(fixed.snapshot)); query.set('observation_snapshot', String(fixed.observation_snapshot)); query.set('expected_scope_key', fixed.scope_key!); }
  const r = await request.get(`${origin}/api/project-fragments?${query}`, { headers }); expect(r.status()).toBe(200); const data = await r.json(); capture('page', data); return data;
}
async function audit(request: APIRequestContext) { const r = await request.get(`${origin}/synthetic-fragments/audit`, { headers }); expect(r.status()).toBe(200); const data = await r.json(); capture('audit', data); return data; }
async function enter(page: Page, s = source(), project = index.first_project) {
  await page.goto(`${origin}/#token=synthetic-project-fragments-token`);
  await page.getByLabel('当前项目', { exact: true }).selectOption(project); await page.getByRole('button', { name: '采集健康', exact: true }).click();
  const row = page.locator('[aria-labelledby="sources-heading"] tbody tr').filter({ hasText: s.path });
  await expect(row).toHaveCount(1); await row.getByRole('button', { name: '查看片段与归属', exact: true }).click();
  await expect(panel(page).locator('[data-fragment-record]').first()).toBeVisible();
}
async function refresh(page: Page): Promise<FragmentPage> { const r = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-fragments' && new URL(r.url()).searchParams.get('after_event') === '0'); await panel(page).getByRole('button', { name: '重新读取片段第一页', exact: true }).click(); const response = await r; expect(response.status()).toBe(200); await expect(panel(page).getByRole('alert')).toHaveCount(0); return response.json(); }
async function choose(page: Page, events: number[], target: string) {
  await panel(page).getByRole('button', { name: `选择记录 #${events[0]}`, exact: true }).click();
  if (events.length > 1) await panel(page).getByRole('button', { name: `选择记录 #${events.at(-1)}`, exact: true }).click();
  await panel(page).getByLabel('片段目标项目').selectOption(target);
  await panel(page).getByRole('checkbox').check();
}
async function post(request: APIRequestContext, s: Source, current: FragmentPage, event: number, target: string | null, requestId = crypto.randomUUID()) {
  const body = { request_id: requestId, session: s.session_pk, source: s.file_instance_id, first_event: event, last_event: event, project_id: target, expected_revision: current.revision, expected_scope_key: current.scope_key, snapshot: current.snapshot, observation_snapshot: current.observation_snapshot, actor: 'human:公开外部操作' };
  const r = await request.post(`${origin}/api/project-fragments/assign`, { headers, data: body }); capture('outside-body', body); capture('outside-receipt', await r.json()); expect(r.status()).toBe(200);
}
async function visible(node: Locator, page: Page) {
  await node.evaluate(el => el.scrollIntoView({ block: 'center', behavior: 'instant' }));
  const b = await node.evaluate(el => { const r = el.getBoundingClientRect(), header = el.closest('.detail-drawer')?.querySelector(':scope > header')?.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height, headerBottom: header?.bottom ?? 0, w: innerWidth, h: innerHeight, document: document.documentElement.scrollWidth }; }); capture('geometry', b);
  expect(b.width).toBeGreaterThan(0); expect(b.height).toBeGreaterThan(0); expect(b.left).toBeGreaterThanOrEqual(0); expect(b.right).toBeLessThanOrEqual(b.w); expect(b.top).toBeGreaterThanOrEqual(b.headerBottom); expect(b.bottom).toBeLessThanOrEqual(b.h); expect(b.document).toBeLessThanOrEqual(b.w);
  expect(await page.evaluate(() => (window as unknown as { fragmentInjected?: boolean }).fragmentInjected)).toBeUndefined();
}
async function ignoreAbort(page: Page) { await page.addInitScript(() => { const real = window.fetch.bind(window); window.fetch = (input, init) => { const path = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href).pathname; if (path.startsWith('/api/project-fragments')) { const options = { ...init }; delete options.signal; return real(input, options); } return real(input, init); }; }); }

test('完整物理记录分页保留字节和原件摘要，目录切换只产生候选且查询不写库', async ({ page, request }) => {
  const before = await audit(request), first = await get(request), second = await get(request, source(), first);
  expect(first.records_total).toBe(24); expect(first.records).toHaveLength(20); expect(second.records).toHaveLength(4); expect(second.next_after_event).toBeNull();
  expect(second.snapshot).toBe(first.snapshot); expect(second.observation_snapshot).toBe(first.observation_snapshot); expect(second.scope_key).toBe(first.scope_key);
  expect([...first.records!, ...second.records!].map(r => ({ event_id: r.first_event_id, byte_start: r.byte_start, byte_end: r.byte_end, object_sha256: r.object_sha256 }))).toEqual(source().events);
  expect(first.records![10].project_hint.projects).toEqual([index.second_project]);
  expect(first.records!.every(r => r.assignment?.project_id === index.first_project)).toBe(true);
  await enter(page); expect(await panel(page).getByLabel('片段目标项目').inputValue()).toBe(''); await expect(panel(page).getByRole('button', { name: '保存明确归属', exact: true })).toBeDisabled();
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-fragments' && new URL(r.url()).searchParams.get('after_event') === String(first.next_after_event)); await panel(page).getByRole('button', { name: '下一页片段', exact: true }).click(); const next = await response; expect(next.status()).toBe(200);
  const params = new URL(next.request().url()).searchParams; expect(params.get('snapshot')).toBe(String(first.snapshot)); expect(params.get('observation_snapshot')).toBe(String(first.observation_snapshot)); expect(params.get('expected_scope_key')).toBe(first.scope_key);
  await expect(panel(page).locator('[data-fragment-record]')).toHaveCount(4); await visible(panel(page).locator('[data-fragment-record]').first(), page); await page.screenshot({ path: '../.cache/frontend-project-fragments-page-desktop.png' });
  const after = await audit(request); expect(after).toEqual(before);
});

test('主动确认连续范围才归属另一项目并刷新首屏，既有候选审核和原件不变', async ({ page, request }) => {
  const before = await audit(request); await enter(page); const s = source(), chosen = s.events.slice(0, 2).map(e => e.event_id);
  await choose(page, chosen, index.second_project); await expect(panel(page).locator('[data-fragment-selected-range]')).toContainText('2 条完整物理记录');
  const r = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-fragments/assign'); await panel(page).getByRole('button', { name: '保存明确归属', exact: true }).click(); const response = await r; expect(response.status()).toBe(200); capture('assign-body', response.request().postDataJSON()); const receipt: FragmentReceipt = await response.json(); capture('assign-receipt', receipt); expect(receipt.records_assigned).toBe(2); expect(receipt.project_id).toBe(index.second_project);
  await expect(panel(page).locator(`[data-fragment-record="${chosen[0]}"] [data-fragment-assignment]`)).toContainText('验收物理片段项目乙'); await expect(panel(page).getByRole('checkbox')).not.toBeChecked();
  const refreshed = await get(request); expect(refreshed.records![2].assignment?.project_id).toBe(index.first_project); await visible(panel(page).locator('[data-fragment-record]').first(), page); await page.screenshot({ path: '../.cache/frontend-project-fragments-assigned-desktop.png' });
  const after = await audit(request); for (const key of ['research_sha256', 'counts', 'objects', 'input_files', 'claim_states']) expect(after[key]).toEqual(before[key]); expect(after.revision).toBeGreaterThan(before.revision);
});

test('网络丢失成功回执后人工重读并重试同UUID，真实回放不新增分区', async ({ page, request }) => {
  await enter(page); const event = source().events[3].event_id; await choose(page, [event], 'inbox'); let sent: Record<string, unknown> | null = null;
  await page.route('**/api/project-fragments/assign', async route => { sent = route.request().postDataJSON(); const response = await route.fetch(); expect(response.status()).toBe(200); capture('lost-receipt', await response.json()); await route.abort('failed'); });
  await panel(page).getByRole('button', { name: '保存明确归属', exact: true }).click(); await expect(panel(page).getByRole('alert')).toBeVisible(); await page.unroute('**/api/project-fragments/assign');
  await refresh(page); await choose(page, [event], 'inbox'); const retried = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-fragments/assign'); await panel(page).getByRole('button', { name: '保存明确归属', exact: true }).click(); const response = await retried; expect(response.status()).toBe(200); const data = await response.json(); expect(data.replayed).toBe(true); expect(response.request().postDataJSON().request_id).toBe(sent!.request_id); capture('retry-body', response.request().postDataJSON()); capture('retry-receipt', data);
  expect((await get(request)).records!.find(r => r.first_event_id === event)?.assignment?.project_id).toBeNull();
});

test('真实归属409保留旧页且禁止改选绕过，主动刷新后才允许新意图', async ({ page, request }) => {
  await enter(page); const old = await get(request), ids = await panel(page).locator('[data-fragment-record]').evaluateAll(elements => elements.map(e => e.getAttribute('data-fragment-record')));
  await choose(page, [source().events[4].event_id], index.second_project); await post(request, source(), old, source().events[22].event_id, index.second_project);
  const obsolete = await request.get(`${origin}/api/project-fragments?session=${source().session_pk}&source=${source().file_instance_id}&after_event=${old.next_after_event}&snapshot=${old.snapshot}&observation_snapshot=${old.observation_snapshot}&expected_scope_key=${old.scope_key}`, { headers }); expect(obsolete.status()).toBe(409); capture('stale-page', await obsolete.json());
  const r = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-fragments/assign'); await panel(page).getByRole('button', { name: '保存明确归属', exact: true }).click(); expect((await r).status()).toBe(409); await expect(panel(page).getByRole('alert')).toBeVisible();
  expect(await panel(page).locator('[data-fragment-record]').evaluateAll(elements => elements.map(e => e.getAttribute('data-fragment-record')))).toEqual(ids); await panel(page).getByLabel('片段目标项目').selectOption('inbox'); await expect(panel(page).getByRole('button', { name: '保存明确归属', exact: true })).toBeDisabled();
  await refresh(page); await choose(page, [source().events[4].event_id], 'inbox'); await expect(panel(page).getByRole('button', { name: '保存明确归属', exact: true })).toBeEnabled();
});

test('迟到真实归属成功不清掉另一项目的新范围或确认意图', async ({ page }) => {
  await ignoreAbort(page); await enter(page); const oldEvent = source().events[5].event_id, newEvent = source().events[6].event_id;
  await choose(page, [oldEvent], index.second_project); let release = () => {}; let seen = false; const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/project-fragments/assign', async route => { const response = await route.fetch(); expect(response.status()).toBe(200); capture('late-write-body', route.request().postDataJSON()); capture('late-write-result', await response.json()); seen = true; await held; await route.fulfill({ response }); });
  try {
    await panel(page).getByRole('button', { name: '保存明确归属', exact: true }).click(); await expect.poll(() => seen).toBe(true);
    await page.getByLabel('当前项目', { exact: true }).selectOption(index.second_project); await expect(panel(page)).toHaveCount(0);
    const row = page.locator('[aria-labelledby="sources-heading"] tbody tr').filter({ hasText: source().path }); await row.getByRole('button', { name: '查看片段与归属', exact: true }).click(); await expect(panel(page).locator('[data-fragment-record]').first()).toBeVisible();
    await choose(page, [newEvent], 'inbox'); const before = await panel(page).locator('[data-fragment-selected-range]').textContent();
    const r = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-fragments/assign'); release(); expect((await r).status()).toBe(200);
    expect(await panel(page).locator('[data-fragment-selected-range]').textContent()).toBe(before); await expect(panel(page).getByRole('checkbox')).toBeChecked(); await expect(page.getByLabel('当前项目', { exact: true })).toHaveValue(index.second_project); await expect(panel(page).getByRole('button', { name: '保存明确归属', exact: true })).toBeEnabled();
  } finally { release(); await page.unroute('**/api/project-fragments/assign'); }
});

test('迟到真实200不会进入切换后的项目，晚401不能撤销新页面鉴权', async ({ page }) => {
  await ignoreAbort(page); await enter(page); let release = () => {}; let seen = false; const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/project-fragments?*', async route => { const response = await route.fetch(); seen = true; await held; await route.fulfill({ response }); });
  try {
    await panel(page).getByRole('button', { name: '重新读取片段第一页', exact: true }).click(); await expect.poll(() => seen).toBe(true); await page.getByLabel('当前项目', { exact: true }).selectOption(index.second_project); await expect(panel(page)).toHaveCount(0);
    const r = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-fragments'); release(); expect((await r).status()).toBe(200); await expect(panel(page)).toHaveCount(0);
  } finally { release(); await page.unroute('**/api/project-fragments?*'); }
  await enter(page); let open = () => {}; let blocked = false; const paused = new Promise<void>(resolve => { open = resolve; });
  await page.route('**/api/project-fragments?*', async route => { const response = await route.fetch({ headers: { ...route.request().headers(), authorization: 'Bearer synthetic-invalid' } }); expect(response.status()).toBe(401); blocked = true; await paused; await route.fulfill({ response }); });
  try {
    await panel(page).getByRole('button', { name: '重新读取片段第一页', exact: true }).click(); await expect.poll(() => blocked).toBe(true); await panel(page).getByRole('button', { name: '关闭片段面板', exact: true }).click(); const r = page.waitForResponse(r => new URL(r.url()).pathname === '/api/project-fragments'); open(); expect((await r).status()).toBe(401); await expect(page.getByLabel('当前项目', { exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: '采集健康', exact: true })).toBeVisible();
  } finally { open(); await page.unroute('**/api/project-fragments?*'); }
});

test('390宽多块整记录与候选目录可见，原文只传event编号且字节摘要相同', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await enter(page, source('claude')); const current = await get(request, source('claude')); expect(current.records).toHaveLength(5); expect(current.records!.every(r => r.event_count === 2)).toBe(true);
  const first = current.records![0], card = panel(page).locator(`[data-fragment-record="${first.first_event_id}"]`); await visible(card, page); await page.screenshot({ path: '../.cache/frontend-project-fragments-multiblock-mobile.png' });
  const response = page.waitForResponse(r => new URL(r.url()).pathname === `/api/evidence/${first.first_event_id}`); await card.getByRole('button', { name: '打开记录原文', exact: true }).click(); const r = await response; expect(r.status()).toBe(200); expect(new URL(r.request().url()).searchParams.has('start')).toBe(false); expect(new URL(r.request().url()).searchParams.has('end')).toBe(false); const raw = await r.json(); capture('raw', raw);
  const original = readFileSync(source('claude').path).subarray(first.byte_start, first.byte_end); expect(createHash('sha256').update(original).digest('hex')).toBe(first.object_sha256); expect(raw.event.event_id).toBe(first.first_event_id); expect(raw.event.source_byte_start).toBe(first.byte_start); expect(raw.event.source_byte_end).toBe(first.byte_end);
  await expect(page.getByRole('dialog', { name: '原文证据' })).toBeVisible(); await page.screenshot({ path: '../.cache/frontend-project-fragments-evidence-mobile.png' });
  await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await choose(page, current.records!.slice(0, 2).map(r => r.first_event_id), index.second_project);
  const confirmation = panel(page).getByRole('region', { name: '明确指定归属' }); await visible(confirmation, page); await expect(confirmation.locator('[data-fragment-selected-range]')).toContainText('4 个解析事件'); await page.screenshot({ path: '../.cache/frontend-project-fragments-confirm-mobile.png' });
});
