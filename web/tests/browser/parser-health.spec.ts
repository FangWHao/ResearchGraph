import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import type { ParserHealthData } from '../../src/parserHealth';
import type { EvidenceData, Project } from '../../src/types';

const origin = 'http://127.0.0.1:9796';
const headers = { Authorization: 'Bearer synthetic-parser-health-token' };
const root = fileURLToPath(new URL('../../../', import.meta.url));
const primary = '验收解析器账本项目';
const secondary = '验收解析器账本项目二';
let server: ChildProcess | undefined;

test.beforeAll(async () => {
  let existing: Response | undefined;
  try { existing = await fetch(`${origin}/api/projects`, { headers }); } catch { /* 无监听。 */ }
  if (existing) {
    if (!existing.ok) throw new Error('9796 被其他服务占用，未终止已有进程。');
    const names = (await existing.json()).projects.map((item: Project) => item.name).sort();
    expect(names).toEqual([primary, secondary].sort());
    console.info('复用已核对鉴权和项目的解析器合成服务，不终止其进程。');
    return;
  }
  const log = createWriteStream(`${root}/.cache/frontend-parser-health-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.parser_health_browser', '--port', '9796', '--web-dir', `${root}/web/dist`],
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
async function read(request: APIRequestContext, project: string, offset = 0, basis?: ParserHealthData) {
  const params = new URLSearchParams({ project, limit: '20', offset: String(offset) });
  if (basis) { params.set('snapshot', String(basis.snapshot_id)); params.set('expected_scope_key', basis.scope_key); }
  const response = await request.get(`${origin}/api/parser-health?${params}`, { headers });
  expect(response.status()).toBe(200); return await response.json() as ParserHealthData;
}
async function originalWithProof(request: APIRequestContext, event: number) {
  const response = await request.get(`${origin}/api/evidence/${event}?context=0`, { headers });
  expect(response.status()).toBe(200);
  const raw: EvidenceData = await response.json();
  expect(raw.event.quote_sha256).toBeNull();
  const original = raw.event.before + raw.event.quote + raw.event.after;
  expect(Buffer.byteLength(original)).toBe(raw.event.total_bytes);
  const referenced = await request.get(`${origin}/api/evidence/${event}?context=0&start=0&end=${raw.event.total_bytes}`, { headers });
  expect(referenced.status()).toBe(200);
  const window: EvidenceData = await referenced.json();
  expect(window.event.quote).toBe(original);
  expect(createHash('sha256').update(window.event.quote).digest('hex')).toBe(window.event.quote_sha256);
  return original;
}
async function open(page: Page) {
  await page.goto(`${origin}/#token=synthetic-parser-health-token`);
  await expect(page.getByLabel('当前项目', { exact: true })).toContainText(primary);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  const panel = page.getByRole('region', { name: '解析器类型与工具版本账本', exact: true });
  return panel;
}
async function show(page: Page, locator: Locator) {
  await locator.evaluate(node => node.scrollIntoView({ block: 'start' }));
  if (page.viewportSize()!.width === 390) await page.evaluate(() => window.scrollBy(0, -104));
}
async function pageTo(page: Page, panel: Locator, direction: '下一页' | '上一页') {
  const response = page.waitForResponse(item => new URL(item.url()).pathname === '/api/parser-health');
  await panel.getByRole('button', { name: `${direction}解析类型`, exact: true }).click();
  const received = await response; expect(received.status()).toBe(200);
  return await received.json() as ParserHealthData;
}

test('真实两工具版本与物理多块账本完整分页，旧记录不补观测，原文与摘要保留', async ({ page, request }) => {
  const first = (await projects(request)).find(item => item.name === primary)!;
  const initial = await read(request, first.project_id);
  expect(initial.records_unobserved).toBe(1);
  for (const key of ['bad_json_records', 'invalid_record_records', 'parser_error_records', 'records_unknown_version', 'records_invalid_version'] as const)
    expect(initial[key]).toBeGreaterThan(0);
  expect(initial.type_groups_total).toBeGreaterThan(20);
  const all = [...initial.types];
  let offset = initial.next_offset;
  while (offset !== null) { const next = await read(request, first.project_id, offset, initial); all.push(...next.types); offset = next.next_offset; }
  expect(all).toHaveLength(initial.type_groups_total);
  expect(new Set(all.filter(item => item.tool_version !== null).map(item => item.tool))).toEqual(new Set(['claude', 'codex']));
  expect(new Set(all.map(item => item.version_basis))).toEqual(new Set(['direct_record', 'file_context', 'unknown', 'invalid']));
  const multiple = all.find(item => item.type_name === 'future_multi')!;
  expect(multiple).toMatchObject({ occurrences: 2, records: 1, source_files: 1, recognized: false });
  expect(multiple.last_event_id).toBeGreaterThan(multiple.first_event_id);
  const graphBefore = await (await request.get(`${origin}/api/graph?project=${first.project_id}`, { headers })).json();
  const reads: string[] = []; const writes: string[] = [];
  page.on('request', item => {
    const url = new URL(item.url());
    if (url.pathname === '/api/parser-health') reads.push(item.url());
    if (url.pathname.startsWith('/api/') && item.method() !== 'GET') writes.push(item.url());
  });
  const panel = await open(page);
  await expect(panel.locator('[data-parser-metric="records_unobserved"] dd')).toHaveText('1');
  await expect(panel.locator('.parser-health-types > li')).toHaveCount(initial.types.length);
  await show(page, panel.locator(':scope > header'));
  await page.screenshot({ path: '../.cache/frontend-parser-health-desktop.png' });
  let displayed = initial;
  const names: string[] = [];
  while (true) {
    await expect(panel.locator('.parser-health-page')).toContainText(`偏移 ${displayed.offset}`);
    expect(await panel.locator('.parser-health-types > li').evaluateAll(items => items.map(item => item.getAttribute('data-parser-type-name'))))
      .toEqual(displayed.types.map(item => item.type_name));
    names.push(...displayed.types.map(item => item.type_name));
    if (displayed.next_offset === null) break;
    displayed = await pageTo(page, panel, '下一页');
  }
  expect(names).toEqual(all.map(item => item.type_name));
  const targetOffset = Math.floor(all.indexOf(multiple) / 20) * 20;
  while (displayed.offset > targetOffset) displayed = await pageTo(page, panel, '上一页');
  const card = panel.locator('[data-parser-type-name="future_multi"]');
  await expect(card).toHaveAttribute('data-parser-recognized', 'false');
  await expect(card.locator('dl > div').filter({ hasText: '类型出现次数' }).locator('dd')).toHaveText('2');
  await expect(card.locator('dl > div').filter({ hasText: '相关物理记录' }).locator('dd')).toHaveText('1');
  await show(page, card);
  await page.screenshot({ path: '../.cache/frontend-parser-health-type-desktop.png' });
  await card.getByRole('button', { name: `首次原文 #${multiple.first_event_id}`, exact: true }).click();
  const original = await originalWithProof(request, multiple.first_event_id);
  await expect(page.locator('.raw-event.focused pre')).toHaveText(original);
  expect(await page.locator('.raw-event.focused pre').textContent()).toBe(original);
  expect(original).toContain('<script>window.parserInjected=true</script>');
  expect(await page.evaluate(() => 'parserInjected' in window)).toBe(false);
  await page.screenshot({ path: '../.cache/frontend-parser-health-evidence-desktop.png' });
  await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await card.getByRole('button', { name: `最近原文 #${multiple.last_event_id}`, exact: true }).click();
  const latest = await originalWithProof(request, multiple.last_event_id);
  await expect(page.locator('.raw-event.focused pre')).toHaveText(latest);
  expect(reads.filter(value => Number(new URL(value).searchParams.get('offset')) > 0).every(value => {
    const url = new URL(value); return url.searchParams.get('snapshot') === String(initial.snapshot_id)
      && url.searchParams.get('expected_scope_key') === initial.scope_key;
  })).toBe(true);
  expect(await (await request.get(`${origin}/api/graph?project=${first.project_id}`, { headers })).json()).toEqual(graphBefore);
  expect(writes).toEqual([]);
});

test('真实400与409拒绝错误续页，界面保留旧页且须主动首读，401沿原鉴权恢复', async ({ page, request }) => {
  const first = (await projects(request)).find(item => item.name === primary)!;
  expect((await request.get(`${origin}/api/parser-health?project=${first.project_id}&offset=20`, { headers })).status()).toBe(400);
  let wrongScope = true; let denied = false;
  await page.route('**/api/parser-health?**', async route => {
    const url = new URL(route.request().url());
    if (wrongScope && Number(url.searchParams.get('offset')) > 0) {
      url.searchParams.set('expected_scope_key', '0'.repeat(64));
      const response = await route.fetch({ url: url.toString() }); expect(response.status()).toBe(409);
      await route.fulfill({ response });
    } else if (denied) {
      const response = await route.fetch({ headers: { ...route.request().headers(), Authorization: 'Bearer synthetic-wrong-token' } });
      expect(response.status()).toBe(401); await route.fulfill({ response });
    } else await route.continue();
  });
  const panel = await open(page);
  const initial = await read(request, first.project_id);
  await expect(panel.locator('.parser-health-types > li')).toHaveCount(initial.types.length);
  const names = await panel.locator('.parser-health-types > li').evaluateAll(items => items.map(item => item.getAttribute('data-parser-type-name')));
  await panel.getByRole('button', { name: '下一页解析类型', exact: true }).click();
  await expect(panel.getByRole('alert')).toContainText('归属已变化');
  expect(await panel.locator('.parser-health-types > li').evaluateAll(items => items.map(item => item.getAttribute('data-parser-type-name')))).toEqual(names);
  await expect(panel.getByRole('button', { name: '下一页解析类型', exact: true })).toBeDisabled();
  wrongScope = false;
  await panel.getByRole('button', { name: '重新读取解析器账本', exact: true }).click();
  await expect(panel.getByRole('alert')).toHaveCount(0);
  await expect(panel.locator('.parser-health-page')).toContainText('偏移 0');
  await pageTo(page, panel, '下一页');
  await expect(panel.locator('.parser-health-page')).toContainText('偏移 20');
  denied = true;
  await panel.getByRole('button', { name: '重新读取解析器账本', exact: true }).click();
  await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toBeVisible();
  denied = false;
  await page.getByLabel('访问令牌', { exact: true }).fill('synthetic-parser-health-token');
  await page.getByRole('button', { name: /进入本地工作区/ }).click();
  await expect(panel.locator('.parser-health-page')).toContainText('偏移 0');
  await expect(panel.locator('.parser-health-types > li')).toHaveCount(initial.types.length);
});

test('旧接口统计缺失与不可用响应不补零，错误分页格式不替换真实旧页', async ({ page }) => {
  let mode: 'legacy' | 'missing' | 'normal' | 'malformed' = 'legacy';
  await page.route('**/api/parser-health?**', async route => {
    if (mode === 'legacy') await route.fulfill({ json: { available: false, reason: 'legacy_schema' } });
    else if (mode === 'missing') await route.fulfill({ status: 404, json: { error: '合成旧接口不存在' } });
    else if (mode === 'malformed') { const response = await route.fetch(); const data = await response.json(); await route.fulfill({ json: { ...data, scope_key: 'wrong-scope' } }); }
    else await route.continue();
  });
  const panel = await open(page);
  await expect(panel).toContainText('统计缺失');
  await expect(panel.locator('[data-parser-metric]')).toHaveCount(0);
  mode = 'missing'; await panel.getByRole('button', { name: '重新读取解析器账本', exact: true }).click();
  await expect(panel.getByRole('alert')).toContainText('尚未提供');
  await expect(panel.locator('[data-parser-metric]')).toHaveCount(0);
  mode = 'normal'; await panel.getByRole('button', { name: '重新读取解析器账本', exact: true }).click();
  await expect(panel.locator('.parser-health-types > li')).toHaveCount(20);
  const names = await panel.locator('.parser-health-types > li').evaluateAll(items => items.map(item => item.getAttribute('data-parser-type-name')));
  mode = 'malformed'; await panel.getByRole('button', { name: '下一页解析类型', exact: true }).click();
  await expect(panel.getByRole('alert')).toContainText('读取条件不一致');
  expect(await panel.locator('.parser-health-types > li').evaluateAll(items => items.map(item => item.getAttribute('data-parser-type-name')))).toEqual(names);
});

test('390直接切项目后迟到真实账本及旧401不覆盖新项目，类型与统计在屏内可读', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const all = await projects(request);
  const first = all.find(item => item.name === primary)!;
  const second = all.find(item => item.name === secondary)!;
  const current = await read(request, second.project_id);
  await page.addInitScript(() => {
    const actual = window.fetch.bind(window); const received: { status: number; late: string | null; url: string }[] = [];
    (window as unknown as { parserStatuses: typeof received }).parserStatuses = received;
    window.fetch = async (input, options) => {
      if (!String(input).startsWith('/api/parser-health?')) return actual(input, options);
      const response = await actual(input, { ...options, signal: undefined }); const parse = response.json.bind(response);
      response.json = async () => { const data = await parse(); received.push({ status: response.status,
        late: response.headers.get('x-synthetic-late'), url: String(input) }); return data; }; return response;
    };
  });
  for (const status of [200, 401]) {
    await page.goto('about:blank');
    let release = () => {}; const held = new Promise<void>(resolve => { release = resolve; });
    let ready = () => {}; const started = new Promise<void>(resolve => { ready = resolve; });
    let deferred = false;
    await page.route('**/api/parser-health?**', async route => {
      if (!deferred && new URL(route.request().url()).searchParams.get('project') === first.project_id) {
        deferred = true;
        const response = await route.fetch(status === 401 ? { headers: { ...route.request().headers(), Authorization: 'Bearer synthetic-wrong-token' } } : undefined);
        expect(response.status()).toBe(status); ready(); await held;
        await route.fulfill({ response, headers: { ...response.headers(), 'x-synthetic-late': String(status) } });
      } else await route.continue();
    });
    const panel = await open(page); await started;
    await page.getByLabel('当前项目', { exact: true }).selectOption({ label: secondary });
    await expect(panel.locator('[data-parser-metric="records_total"] dd')).toHaveText(String(current.records_total));
    await expect(panel.locator('.parser-health-types > li')).toHaveCount(current.types.length);
    release();
    await expect.poll(() => page.evaluate(({ status, first, second }) => {
      const values = (window as unknown as { parserStatuses: { status: number; late: string | null; url: string }[] }).parserStatuses;
      return values.some(item => item.status === 200 && new URL(item.url, location.origin).searchParams.get('project') === second)
        && values.some(item => item.status === status && item.late === String(status)
          && new URL(item.url, location.origin).searchParams.get('project') === first);
    }, { status, first: first.project_id, second: second.project_id })).toBe(true);
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(panel.locator('[data-parser-metric="records_total"] dd')).toHaveText(String(current.records_total));
    await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toHaveCount(0);
    await expect(panel.getByRole('alert')).toHaveCount(0);
    expect(await panel.locator('.parser-health-types > li').evaluateAll(items => items.map(item => item.getAttribute('data-parser-type-name')))).toEqual(current.types.map(item => item.type_name));
    expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await show(page, panel.locator(':scope > header'));
    await page.screenshot({ path: '../.cache/frontend-parser-health-mobile.png' });
    const card = panel.locator('.parser-health-types > li').first();
    await show(page, card);
    await expect(card.getByRole('button', { name: /首次原文/ })).toBeVisible();
    const bounds = (await card.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(72);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
    expect(bounds.y).toBeGreaterThanOrEqual(88);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(844);
    await page.screenshot({ path: '../.cache/frontend-parser-health-type-mobile.png' });
    await page.unroute('**/api/parser-health?**');
  }
});
