import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWriteStream, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import type { EvidenceData, ResearchReading } from '../../src/types';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const origin = 'http://127.0.0.1:9804';
const headers = { Authorization: 'Bearer synthetic-injected-source-token' };
type Name = 'codex_candidate' | 'claude_candidate' | 'claude_confirmed';
interface Identity {
  project_id: string; claim_id: number; event_id: number; span_id: number;
  byte_start: number; byte_end: number; quote_sha256: string; raw_sha256: string;
  total_bytes: number; occurred_at: string; recorded_at: string;
}
interface Index { cases: Record<Name, Identity>; synthetic_only: boolean }
let server: ChildProcess | undefined;
let index: Index;
let captured = 0;
function capture(name: string, value: unknown) {
  const directory = `${root}/.cache/frontend-injected-source-api`;
  mkdirSync(directory, { recursive: true });
  writeFileSync(`${directory}/${String(++captured).padStart(3, '0')}-${name}.json`, JSON.stringify(value, null, 2) + '\n');
}
test.beforeAll(async () => {
  let existing: Response | undefined;
  try { existing = await fetch(`${origin}/api/projects`, { headers }); } catch { /* 无监听。 */ }
  if (existing) throw new Error('9804 已有服务，本轮保留未知进程且不复用未核对的库。');
  mkdirSync(`${root}/.cache`, { recursive: true });
  const log = createWriteStream(`${root}/.cache/frontend-injected-source-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.injected_sources_browser', '--port', '9804', '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => {
    if (server?.exitCode !== null) throw new Error('合成服务提前退出，查看原始服务日志。');
    try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; }
  }).toBe(200);
  index = JSON.parse(readFileSync(`${root}/.cache/frontend-injected-source-index.json`, 'utf8'));
  expect(index.synthetic_only).toBe(true);
  expect(new Set(Object.values(index.cases).map(item => item.project_id)).size).toBe(3);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) {
    const closed = new Promise<number | null>(resolve => server!.once('exit', code => resolve(code)));
    server.kill('SIGINT'); expect(await closed).toBe(0);
    expect(JSON.parse(readFileSync(`${root}/.cache/frontend-injected-source-audit.json`, 'utf8'))).toMatchObject({ 合成库逐表导出未改变: true });
  }
});
async function json(request: APIRequestContext, path: string, tag: string) {
  const response = await request.get(`${origin}/api${path}`, { headers });
  expect(response.status()).toBe(200); const body = await response.json(); capture(tag, body); return body;
}
async function current(request: APIRequestContext, item: Identity) {
  const params = new URLSearchParams({ context: '0', start: String(item.byte_start), end: String(item.byte_end) });
  const body = await json(request, `/evidence/${item.event_id}?${params}`, `current-${item.event_id}`) as EvidenceData;
  expect(body.event).toMatchObject({ event_id: item.event_id, exclude_reason: null, input_exclude_reason: 'injected_by_rg', quote: 'budget', quote_sha256: item.quote_sha256 });
  expect(createHash('sha256').update(body.event.quote).digest('hex')).toBe(item.quote_sha256);
  const full = await json(request, `/evidence/${item.event_id}?context=0&start=0&end=${item.total_bytes}`, `full-window-${item.event_id}`) as EvidenceData;
  expect(full.event.window_truncated).toBe(false); expect(full.event.quote_sha256).toBe(item.raw_sha256);
  expect(createHash('sha256').update(full.event.quote).digest('hex')).toBe(item.raw_sha256);
  expect(Buffer.byteLength(full.event.quote)).toBe(item.total_bytes);
  return body;
}
async function init(page: Page, item: Identity) {
  await page.goto(`${origin}/#token=synthetic-injected-source-token`);
  await page.getByLabel('当前项目', { exact: true }).selectOption(item.project_id);
  await expect(page.getByRole('heading', { name: 'budget 合成假说', exact: true })).toBeVisible();
}
async function openCurrent(page: Page, item: Identity) {
  await page.getByRole('button', { name: '查看记录与证据', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: `记录 ${item.claim_id} 详情`, exact: true });
  await drawer.locator('.evidence-link').click();
  const dialog = page.getByRole('dialog', { name: '原文证据', exact: true });
  await expect(dialog.getByRole('heading', { name: `原文 #${item.event_id}`, exact: true })).toBeVisible();
  await expect(page.getByTestId('evidence-quote')).toHaveText('budget');
  return { drawer, dialog };
}
async function diagnostic(page: Page) {
  const note = page.locator('.raw-event.focused [data-input-exclude-reason]');
  await expect(note).toHaveAttribute('data-input-exclude-reason', 'injected_by_rg');
  await note.evaluate(element => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
  await expect.poll(() => note.evaluate(element => {
    const box = element.getBoundingClientRect();
    const headerBottom = element.closest('[role="dialog"]')!.querySelector(':scope > header')!.getBoundingClientRect().bottom;
    return box.width > 0 && box.height > 0 && box.left >= 0 && box.right <= innerWidth
      && box.top >= headerBottom && box.bottom <= innerHeight;
  })).toBe(true);
  const geometry = await page.evaluate(() => ({
    viewport: innerWidth, document: document.documentElement.scrollWidth,
    overflow: Array.from(document.querySelectorAll<HTMLElement>('body *')).flatMap(element => {
      const box = element.getBoundingClientRect();
      return box.right > innerWidth || box.left < 0 ? [{ tag: element.tagName, class: element.className,
        width: box.width, left: box.left, right: box.right, text: element.textContent?.slice(0, 120) }] : [];
    }),
  }));
  capture('viewport-geometry', { ...geometry, diagnostic: await note.evaluate(element => {
    const box = element.getBoundingClientRect();
    const headerBottom = element.closest('[role="dialog"]')!.querySelector(':scope > header')!.getBoundingClientRect().bottom;
    return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, headerBottom, viewportHeight: innerHeight };
  }) });
  expect(geometry.document <= geometry.viewport).toBe(true);
  return note;
}

test('两工具旧漏标候选仍可按原文budget定位，资格诊断不改候选审核或保存字节', async ({ page, request }) => {
  for (const name of ['codex_candidate', 'claude_candidate'] as const) {
    const item = index.cases[name]; const before = await json(request, `/claims/${item.claim_id}`, `claim-before-${name}`);
    expect(before.claim).toMatchObject({ claim_state: 'candidate', effective_state: 'candidate', confirmation_source: null });
    await current(request, item); await init(page, item); const { drawer } = await openCurrent(page, item);
    await expect(drawer.locator('.badge')).toHaveText('待复核');
    await diagnostic(page);
    expect(await page.locator('.raw-event.focused pre').textContent()).toContain('budget');
    expect(await page.locator('.raw-event.focused pre script').count()).toBe(0);
    if (name === 'codex_candidate') await page.screenshot({ path: '../.cache/frontend-injected-source-candidate-desktop.png' });
    expect(await json(request, `/claims/${item.claim_id}`, `claim-after-${name}`)).toEqual(before);
  }
});

test('人工已确认记录保留审核与原始漏标，独立资格提示不能撤回人工记录', async ({ page, request }) => {
  const item = index.cases.claude_confirmed;
  const before = await json(request, `/claims/${item.claim_id}`, 'human-before');
  expect(before.claim).toMatchObject({ claim_state: 'candidate', effective_state: 'confirmed', confirmation_source: 'human' });
  expect(before.claim.review_history).toHaveLength(1);
  await current(request, item); await init(page, item); const { drawer } = await openCurrent(page, item);
  await expect(drawer.locator('.badge')).toHaveText('已确认');
  await diagnostic(page); await page.screenshot({ path: '../.cache/frontend-injected-source-confirmed-desktop.png' });
  expect(await json(request, `/claims/${item.claim_id}`, 'human-after')).toEqual(before);
});

test('历史原文沿固定项目双截止与修订报告来源资格，引用摘要仍一致且不加载当前派生', async ({ page, request }) => {
  const item = index.cases.claude_confirmed; await init(page, item);
  await page.getByRole('button', { name: '研究图', exact: true }).click();
  await expect(page.locator('[data-research-summary]')).toContainText('双时间内全部 L2 登记记录');
  const reading: ResearchReading = JSON.parse((await page.locator('[data-research-summary]').getAttribute('data-reading'))!);
  const params = new URLSearchParams({ project: item.project_id, occurred_until: reading.occurred_until, known_until: reading.known_until, expected_revision: String(reading.revision), start: String(item.byte_start), end: String(item.byte_end), context: '0' });
  const body = await json(request, `/evidence/${item.event_id}?${params}`, 'historical-window');
  expect(body).toMatchObject({ history_context: true, derived_context_loaded: false, project_id: item.project_id, revision: reading.revision });
  expect(body.event).toMatchObject({ exclude_reason: null, input_exclude_reason: 'injected_by_rg', quote: 'budget', quote_sha256: item.quote_sha256 });
  expect(body).not.toHaveProperty('l1'); expect(body).not.toHaveProperty('session_parent');
  await page.locator('.react-flow__node strong').click();
  const drawer = page.getByRole('dialog', { name: `记录 ${item.claim_id} 详情`, exact: true });
  await expect(drawer.locator('.badge')).toHaveText('已确认');
  await expect(drawer.locator('.drawer-actions button')).toHaveCount(0);
  await drawer.locator('.evidence-link').click();
  await expect(page.getByTestId('evidence-quote')).toHaveText('budget');
  await diagnostic(page); await page.screenshot({ path: '../.cache/frontend-injected-source-history-desktop.png' });
  params.set('known_until', '2025-01-01T00:00:00Z');
  const hidden = await request.get(`${origin}/api/evidence/${item.event_id}?${params}`, { headers });
  expect(hidden.status()).toBe(404); capture('historical-not-yet-known', { status: hidden.status(), body: await hidden.json() });
});

test('390直接切换独立项目，当前及历史来源提示在固定标题下可见，原文仍可选择', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/#token=synthetic-injected-source-token`);
  for (const name of ['codex_candidate', 'claude_candidate', 'claude_confirmed'] as const) {
    const item = index.cases[name]; await page.getByLabel('当前项目', { exact: true }).selectOption(item.project_id);
    await expect(page.getByRole('heading', { name: 'budget 合成假说', exact: true })).toBeVisible();
    const { drawer } = await openCurrent(page, item); await current(request, item);
    await expect(drawer.locator('.badge')).toHaveText(name === 'claude_confirmed' ? '已确认' : '待复核');
    await diagnostic(page);
    expect(await page.locator('.raw-event.focused pre').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    if (name !== 'claude_candidate') await page.screenshot({ path: `../.cache/frontend-injected-source-${name}-mobile.png` });
    await page.getByRole('button', { name: '关闭原文', exact: true }).click();
    await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  }
  await page.getByRole('button', { name: '研究图', exact: true }).click();
  await expect(page.locator('[data-research-summary]')).toContainText('双时间内全部 L2 登记记录');
  await page.locator('.react-flow__node strong').click();
  await page.getByRole('dialog', { name: `记录 ${index.cases.claude_confirmed.claim_id} 详情`, exact: true }).locator('.evidence-link').click();
  await expect(page.getByTestId('evidence-quote')).toHaveText('budget'); await diagnostic(page);
  await expect(page.locator('.evidence-panel .l1-card')).toHaveCount(0);
});
