import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWriteStream, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import type { EvidenceData, Project } from '../../src/types';

const origin = 'http://127.0.0.1:9800';
const token = 'synthetic-parent-backfill-token';
const headers = { Authorization: `Bearer ${token}` };
const root = fileURLToPath(new URL('../../../', import.meta.url));
const primary = '验收父线程补记项目';
let server: ChildProcess | undefined; let preparer: ChildProcess | undefined; let directory: string | undefined;
let log: ReturnType<typeof createWriteStream> | undefined;
type Case = { event_id: number; session_pk: number; event_ids: number[]; heads: number[] };
type Fixture = { project: string; cases: Record<string, Case>; removed_sources: number };

test.beforeAll(async () => {
  let existing: Response | undefined;
  try { existing = await fetch(`${origin}/api/projects`, { headers }); } catch { /* 无监听。 */ }
  if (existing) {
    if (!existing.ok) throw new Error('9800 被其它服务占用，未终止已有进程。');
    expect((await existing.json()).projects.map((item: Project) => item.name).sort())
      .toEqual([primary, '验收父线程补记项目二'].sort());
    console.info('复用已核对令牌和项目的合成服务，不终止其进程。'); return;
  }
  directory = mkdtempSync(join(tmpdir(), 'researchgraph-parent-backfill-browser-'));
  log = createWriteStream(`${root}/.cache/frontend-parent-backfill-server.log`);
  const args = ['-m', 'tests.parent_backfill_browser', '--fixture-dir', directory];
  preparer = spawn(`${root}/.venv/bin/python`, [...args, '--prepare'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  preparer.stdout!.pipe(log, { end: false }); preparer.stderr!.pipe(log, { end: false });
  await new Promise<void>((resolve, reject) => {
    preparer!.once('error', reject);
    preparer!.once('exit', code => code === 0 ? resolve() : reject(new Error('旧库准备失败，见服务日志。')));
  });
  server = spawn(`${root}/.venv/bin/python`, [...args, '--port', '9800', '--web-dir', `${root}/web/dist`],
    { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log, { end: false }); server.stderr!.pipe(log, { end: false });
  await expect.poll(async () => {
    if (server?.exitCode !== null) throw new Error('合成服务提前退出，见服务日志。');
    try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; }
  }).toBe(200);
});
test.afterAll(async () => {
  for (const child of [preparer, server]) {
    if (child && child.exitCode === null) {
      const closed = new Promise<void>(resolve => child.once('exit', () => resolve()));
      child.kill('SIGINT'); await closed;
    }
  }
  log?.end();
  if (directory) rmSync(directory, { recursive: true, force: true });
});
async function fixture(request: APIRequestContext): Promise<Fixture> {
  const response = await request.get(`${origin}/synthetic-parent-backfill/cases`, { headers });
  expect(response.status()).toBe(200); return await response.json();
}
async function evidence(request: APIRequestContext, id: number): Promise<EvidenceData> {
  const response = await request.get(`${origin}/api/evidence/${id}?context=0`, { headers });
  expect(response.status()).toBe(200); return await response.json();
}
async function cycle(request: APIRequestContext): Promise<Record<string, number>> {
  const response = await request.post(`${origin}/synthetic-parent-backfill/cycle`, { headers, data: {} });
  expect(response.status()).toBe(200); return await response.json();
}
async function open(page: Page) {
  await page.goto(`${origin}/#token=${token}`);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  await page.getByRole('button', { name: '开始搜索', exact: true }).click();
}
async function openEvent(page: Page, id: number) {
  const dialog = page.getByRole('dialog', { name: '原文证据', exact: true });
  if (await dialog.isVisible()) await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await page.getByLabel('全库事件编号', { exact: true }).fill(String(id));
  await page.getByRole('button', { name: '按编号打开原文', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: `原文 #${id}`, exact: true })).toBeVisible();
  return dialog.getByRole('region', { name: 'Codex 父线程依据', exact: true });
}
async function show(panel: Locator) { await panel.evaluate(node => node.scrollIntoView({ block: 'start' })); }
async function rawProof(request: APIRequestContext, id: number) {
  const data = await evidence(request, id); expect(data.event.quote_sha256).toBeNull();
  const response = await request.get(`${origin}/api/evidence/${id}?start=0&end=${data.event.total_bytes}&context=0`, { headers });
  expect(response.status()).toBe(200); const full: EvidenceData = await response.json();
  expect(full.event.quote_sha256).toBe(createHash('sha256').update(full.event.quote).digest('hex'));
  expect(Buffer.byteLength(full.event.quote)).toBe(full.event.total_bytes); return full;
}
function noParentReferences(data: EvidenceData) {
  expect(data.session_parent!.parent_session_pk).toBeNull();
  expect(data.session_parent!.parent_native_id).toBeNull(); expect(data.session_parent!.parent_event_id).toBeNull();
}

test('真实旧库原日志消失，首批积压与全库身份缺口都拒绝唯一父，原L0不变', async ({ page, request }) => {
  const { cases, removed_sources } = await fixture(request); expect(removed_sources).toBe(7);
  expect(cases.stable.event_ids).toHaveLength(260); expect(cases.conflicting.event_ids).toHaveLength(260);
  expect(cases.duplicate.event_ids).toHaveLength(260);
  const before = await rawProof(request, cases.stable.heads[0]);
  expect(before.session_parent).toMatchObject({ state: 'metadata_incomplete', observations: [],
    source_metadata_complete: false, identity_metadata_complete: false }); noParentReferences(before);
  const result = await cycle(request);
  const expectedRecords = Object.values(cases).reduce((sum, item) => sum + Math.min(item.event_ids.length, 256), 0);
  expect(expectedRecords).toBe(776); expect(result.parent_backfilled).toBe(expectedRecords);
  console.info('真实首批已核对物理记录：', result.parent_backfilled);
  const partial = await rawProof(request, cases.stable.heads[0]);
  expect(partial.session_parent).toMatchObject({ state: 'metadata_incomplete', observations_total: 1,
    source_metadata_complete: false, identity_metadata_complete: false }); noParentReferences(partial);
  expect(partial.session_parent!.observations[0].event_id).toBe(cases.stable.heads[0]);
  expect(partial.event.quote_sha256).toBe(before.event.quote_sha256);
  expect(partial.event.recorded_at).toBe(before.event.recorded_at);
  const identity = await evidence(request, cases.identity.event_id);
  expect(identity.session_parent).toMatchObject({ state: 'metadata_incomplete', observations_total: 1,
    source_metadata_complete: true, identity_metadata_complete: false }); noParentReferences(identity);
  const parent = await evidence(request, cases['stable-parent'].event_id);
  expect(parent.session_parent).toMatchObject({ state: 'no_parent_declared',
    source_metadata_complete: true, identity_metadata_complete: false });
  await open(page); let panel = await openEvent(page, cases.stable.event_id);
  await expect(panel.locator('[data-parent-state="metadata_incomplete"]')).toHaveCount(1);
  await expect(panel.locator('[data-parent-source-coverage]')).toHaveText('未齐');
  await expect(panel.locator('[data-parent-observation]')).toHaveCount(1);
  await expect(panel.getByRole('button', { name: /^打开父线程头原文/ })).toHaveCount(0);
  await show(panel); await page.screenshot({ path: '../.cache/frontend-parent-backfill-gap-desktop.png' });
  panel = await openEvent(page, cases.identity.event_id);
  await expect(panel.locator('[data-parent-source-coverage]')).toContainText('已齐');
  await expect(panel.locator('[data-parent-identity-coverage]')).toHaveText('未齐');
  await expect(panel.getByRole('button', { name: /^打开父线程头原文/ })).toHaveCount(0);
  await show(panel); await page.screenshot({ path: '../.cache/frontend-parent-backfill-identity-desktop.png' });
});

test('真实第二批恢复关联与迟到冲突，另一项目的晚身份不能被任选，原文导航只有event_id', async ({ page, request }) => {
  const { cases } = await fixture(request); const initial = await rawProof(request, cases.stable.heads[0]);
  const result = await cycle(request);
  const remaining = Object.values(cases).reduce((sum, item) => sum + Math.max(item.event_ids.length - 256, 0), 0);
  expect(remaining).toBe(12); expect(result.parent_backfilled).toBe(remaining);
  console.info('真实第二批已核对物理记录：', result.parent_backfilled);
  const linked = await rawProof(request, cases.stable.heads[0]);
  expect(linked.session_parent).toMatchObject({ state: 'linked', parent_session_pk: cases['stable-parent'].session_pk,
    parent_event_id: cases['stable-parent'].heads[0], source_metadata_complete: true, identity_metadata_complete: true });
  expect(linked.session_parent!.observations).toEqual(initial.session_parent!.observations);
  expect(linked.event.quote_sha256).toBe(initial.event.quote_sha256); expect(linked.event.recorded_at).toBe(initial.event.recorded_at);
  const conflict = await evidence(request, cases.conflicting.event_id);
  expect(conflict.session_parent).toMatchObject({ state: 'conflicting', observations_total: 2,
    source_metadata_complete: true, identity_metadata_complete: true }); noParentReferences(conflict);
  expect(conflict.session_parent!.observations.map(row => row.event_id)).toEqual(cases.conflicting.heads);
  expect(new Set(conflict.session_parent!.observations.map(row => row.parent_id)).size).toBe(2);
  const ambiguous = await evidence(request, cases.identity.event_id);
  expect(ambiguous.session_parent).toMatchObject({ state: 'ambiguous_parent',
    source_metadata_complete: true, identity_metadata_complete: true }); noParentReferences(ambiguous);
  const requests: string[] = [];
  page.on('request', item => { if (new URL(item.url()).pathname.startsWith('/api/evidence/')) requests.push(item.url()); });
  await open(page); let panel = await openEvent(page, cases.stable.event_id);
  await expect(panel.locator('[data-parent-state="linked"]')).toHaveCount(1);
  await panel.getByRole('button', { name: `打开声明原文 #${cases.stable.heads[0]}`, exact: true }).click();
  await expect(page.getByRole('heading', { name: `原文 #${cases.stable.heads[0]}`, exact: true })).toBeVisible();
  await panel.getByRole('button', { name: `打开父线程头原文 #${cases['stable-parent'].heads[0]}`, exact: true }).click();
  await expect(page.getByRole('heading', { name: `原文 #${cases['stable-parent'].heads[0]}`, exact: true })).toBeVisible();
  for (const url of requests) {
    expect(new URL(url).searchParams.has('start')).toBe(false); expect(new URL(url).searchParams.has('end')).toBe(false);
  }
  expect((await rawProof(request, cases['stable-parent'].heads[0])).event.quote).toContain('session_meta');
  panel = await openEvent(page, cases.conflicting.event_id);
  await expect(panel.locator('[data-parent-state="conflicting"]')).toHaveCount(1);
  await expect(panel.locator('[data-parent-observation]')).toHaveCount(2);
  await expect(panel.getByRole('button', { name: /^打开父线程头原文/ })).toHaveCount(0);
  await show(panel); await page.screenshot({ path: '../.cache/frontend-parent-backfill-conflict-desktop.png' });
});

test('兼容旧响应覆盖缺字段、null或false，保留源声明但不公开接口报告的父引用', async ({ page, request }) => {
  const { cases } = await fixture(request);
  const actual = await evidence(request, cases.stable.event_id);
  expect(actual.session_parent!.state).toBe('linked');
  let mode = 'missing';
  await page.route(`**/api/evidence/${cases.stable.event_id}?**`, async route => {
    const response = await route.fetch(); const data: EvidenceData = await response.json();
    if (mode === 'missing') { delete data.session_parent!.source_metadata_complete; delete data.session_parent!.identity_metadata_complete; }
    else if (mode === 'null') { data.session_parent!.source_metadata_complete = null; data.session_parent!.identity_metadata_complete = null; }
    else if (mode === 'source') data.session_parent!.source_metadata_complete = false;
    else data.session_parent!.identity_metadata_complete = false;
    await route.fulfill({ response, json: data });
  });
  await open(page); const panel = await openEvent(page, cases.stable.event_id);
  for (const [index, value] of ['missing', 'null', 'source', 'identity'].entries()) {
    mode = value;
    if (index > 0) await page.getByLabel('前后上下文').selectOption(String(index % 2));
    const coverage = value === 'source' ? '[data-parent-source-coverage]' : '[data-parent-identity-coverage]';
    await expect(panel.locator(coverage)).toHaveText(index < 2 ? '未知（旧响应未提供）' : '未齐');
    await expect(panel.locator('[data-parent-incomplete]')).toHaveCount(1);
    await expect(panel.locator('[data-parent-observation]')).toHaveCount(1);
    await expect(panel.locator('.session-parent-linked')).toHaveCount(0);
    await expect(panel.getByRole('button', { name: /^打开父线程头原文/ })).toHaveCount(0);
    await expect(panel.getByRole('button', { name: /^打开声明原文/ })).toHaveCount(1);
  }
});

test('390真实完整关联与身份歧义可读，旧响应不借导航，观测卡实际位于屏内', async ({ page, request }) => {
  const { cases } = await fixture(request); await page.setViewportSize({ width: 390, height: 844 });
  await open(page); let panel = await openEvent(page, cases.stable.event_id);
  await expect(panel.locator('[data-parent-state="linked"]')).toHaveCount(1);
  await show(panel); await page.screenshot({ path: '../.cache/frontend-parent-backfill-linked-mobile.png' });
  const card = panel.locator('[data-parent-observation]').first();
  await card.evaluate(node => node.scrollIntoView({ block: 'center' }));
  const bounds = (await card.boundingBox())!;
  const header = (await page.getByRole('dialog', { name: '原文证据' }).locator(':scope > header').boundingBox())!;
  expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  expect(bounds.y).toBeGreaterThanOrEqual(header.y + header.height);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(844);
  for (const item of [card.locator('strong').first(), card.getByRole('button', { name: /^打开声明原文/ })]) {
    await expect(item).toBeVisible(); const box = (await item.boundingBox())!;
    expect(box.y).toBeGreaterThanOrEqual(header.y + header.height);
    expect(box.y + box.height).toBeLessThanOrEqual(844);
  }
  await page.screenshot({ path: '../.cache/frontend-parent-backfill-observation-mobile.png' });
  await page.route(`**/api/evidence/${cases.stable.event_id}?**`, async route => {
    const response = await route.fetch(); const data: EvidenceData = await response.json();
    delete data.session_parent!.source_metadata_complete; delete data.session_parent!.identity_metadata_complete;
    await route.fulfill({ response, json: data });
  });
  await page.getByLabel('前后上下文').selectOption('1');
  await expect(panel.locator('[data-parent-source-coverage]')).toContainText('未知');
  await expect(panel.locator('[data-parent-identity-coverage]')).toContainText('未知');
  await expect(panel.getByRole('button', { name: /^打开父线程头原文/ })).toHaveCount(0);
  await show(panel); await page.screenshot({ path: '../.cache/frontend-parent-backfill-legacy-mobile.png' });
  panel = await openEvent(page, cases.identity.event_id);
  await expect(panel.locator('[data-parent-state="ambiguous_parent"]')).toHaveCount(1);
  await expect(panel.locator('[data-parent-source-coverage]')).toContainText('已齐');
  await expect(panel.locator('[data-parent-identity-coverage]')).toContainText('已齐');
  await expect(panel.getByRole('button', { name: /^打开父线程头原文/ })).toHaveCount(0);
  expect(await panel.textContent()).not.toContain(`同项目父会话 #${cases.duplicate.session_pk}`);
  await show(panel); await page.screenshot({ path: '../.cache/frontend-parent-backfill-ambiguous-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.getByRole('dialog', { name: '原文证据' }).evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect((await rawProof(request, cases.stable.heads[0])).event.quote).toContain('parent_thread_id');
});
