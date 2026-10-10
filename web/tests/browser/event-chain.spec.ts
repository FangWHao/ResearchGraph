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

const origin = 'http://127.0.0.1:9799';
const token = 'synthetic-event-chain-token';
const headers = { Authorization: `Bearer ${token}` };
const root = fileURLToPath(new URL('../../../', import.meta.url));
const primary = '验收事件父链项目';
let server: ChildProcess | undefined; let preparer: ChildProcess | undefined; let directory: string | undefined;
let log: ReturnType<typeof createWriteStream> | undefined;
type Case = { event_ids: number[]; session_pk: number; file_instance_id: number; aliases: (number | null)[] };
type Fixture = { project: string; cases: Record<string, Case> };

test.beforeAll(async () => {
  let existing: Response | undefined;
  try { existing = await fetch(`${origin}/api/projects`, { headers }); } catch { /* 无监听。 */ }
  if (existing) {
    if (!existing.ok) throw new Error('9799 被其它服务占用，未终止已有进程。');
    expect((await existing.json()).projects.map((item: Project) => item.name).sort())
      .toEqual([primary, '验收事件父链项目二'].sort());
    console.info('复用已核对令牌和项目的合成服务，不终止其进程。'); return;
  }
  directory = mkdtempSync(join(tmpdir(), 'researchgraph-event-chain-browser-'));
  log = createWriteStream(`${root}/.cache/frontend-event-chain-server.log`);
  const args = ['-m', 'tests.event_chain_browser', '--fixture-dir', directory];
  preparer = spawn(`${root}/.venv/bin/python`, [...args, '--prepare'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  preparer.stdout!.pipe(log, { end: false }); preparer.stderr!.pipe(log, { end: false });
  await new Promise<void>((resolve, reject) => {
    preparer!.once('error', reject); preparer!.once('exit', code => code === 0 ? resolve() : reject(new Error('合成库准备失败，见服务日志。')));
  });
  server = spawn(`${root}/.venv/bin/python`, [...args, '--port', '9799', '--web-dir', `${root}/web/dist`],
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
  const response = await request.get(`${origin}/synthetic-chain-fixture/cases`, { headers });
  expect(response.status()).toBe(200); return await response.json();
}
async function evidence(request: APIRequestContext, id: number): Promise<EvidenceData> {
  const response = await request.get(`${origin}/api/evidence/${id}?context=0`, { headers });
  expect(response.status()).toBe(200); return await response.json();
}
async function control(request: APIRequestContext, action: string) {
  const response = await request.post(`${origin}/synthetic-chain-fixture/${action}`, { headers, data: {} });
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
  return dialog.getByRole('region', { name: 'Claude 事件父链依据', exact: true });
}
async function show(locator: Locator) { await locator.evaluate(node => node.scrollIntoView({ block: 'start' })); }
async function rawProof(request: APIRequestContext, id: number) {
  const data = await evidence(request, id); expect(data.event.quote_sha256).toBeNull();
  const response = await request.get(`${origin}/api/evidence/${id}?start=0&end=${data.event.total_bytes}&context=0`, { headers });
  expect(response.status()).toBe(200); const full: EvidenceData = await response.json();
  expect(full.event.quote_sha256).toBe(createHash('sha256').update(full.event.quote).digest('hex'));
  expect(Buffer.byteLength(full.event.quote)).toBe(full.event.total_bytes); return full;
}

test('真实fork来源变体不随正文别名丢失，多内容块共享记录声明，导航不借文件字节且HTML不执行', async ({ page, request }) => {
  const { cases } = await fixture(request);
  const first = await evidence(request, cases.fork_a.event_ids[1]);
  const second = await evidence(request, cases.fork_b.event_ids[1]);
  expect(first.event_chain!.native_uuid).toBe(second.event_chain!.native_uuid);
  expect(first.event_chain!.parent_uuid).not.toBe(second.event_chain!.parent_uuid);
  expect(cases.fork_b.aliases[1]).toBe(cases.fork_a.event_ids[1]);
  for (const name of ['fork_a', 'fork_b']) {
    const chain = (await evidence(request, cases[name].event_ids[1])).event_chain!;
    expect(chain).toMatchObject({ state: 'linked', resolution_scope: 'source_file', is_sidechain: null,
      sidechain_state: 'missing', parent_references: [{ event_id: cases[name].event_ids[0], file_instance_id: cases[name].file_instance_id }] });
  }
  const ids = cases.multiblock.event_ids; expect(ids).toHaveLength(3);
  const block = await evidence(request, ids[2]); const firstBlock = await evidence(request, ids[1]);
  expect(block.event_chain).toEqual({ ...firstBlock.event_chain, event_id: ids[2] });
  expect(block.event_chain).toMatchObject({ record_event_id: ids[1], is_sidechain: true, sidechain_state: 'declared' });
  expect(block.event.source_byte_start).toBeGreaterThan(0);
  const raw = await rawProof(request, ids[2]); expect(raw.event.quote).toContain('<script>window.chainInjected=true</script>');
  const requests: string[] = [];
  page.on('request', item => { if (new URL(item.url()).pathname.startsWith('/api/evidence/')) requests.push(item.url()); });
  await open(page); const panel = await openEvent(page, ids[2]);
  await expect(panel.locator('[data-chain-state="linked"]')).toHaveCount(1);
  await expect(panel.locator('[data-chain-sidechain="declared"]')).toContainText('true');
  await show(panel); await page.screenshot({ path: '../.cache/frontend-event-chain-linked-desktop.png' });
  await panel.getByRole('button', { name: `打开本记录原文 #${ids[1]}`, exact: true }).click();
  await expect(page.getByRole('heading', { name: `原文 #${ids[1]}`, exact: true })).toBeVisible();
  await expect(panel.locator('[data-chain-state="linked"]')).toHaveCount(1);
  await panel.getByRole('button', { name: `打开父记录原文 #${ids[0]}`, exact: true }).click();
  await expect(page.getByRole('heading', { name: `原文 #${ids[0]}`, exact: true })).toBeVisible();
  await expect(panel.locator('[data-chain-state="null_parent"]')).toHaveCount(1);
  await expect(panel.locator('[data-chain-sidechain="declared"]')).toContainText('false');
  for (const value of requests) {
    expect(new URL(value).searchParams.has('start')).toBe(false); expect(new URL(value).searchParams.has('end')).toBe(false);
  }
  expect(await page.evaluate(() => 'chainInjected' in window)).toBe(false);
  expect((await rawProof(request, ids[2])).event.quote_sha256).toBe(raw.event.quote_sha256);
});

test('真实旧库补记每轮256条，积压不误判唯一父或空父，父迟到只更新关联不改原件', async ({ page, request }) => {
  const { cases } = await fixture(request); const oldIds = cases.backlog.event_ids;
  expect(oldIds).toHaveLength(260);
  const before = await rawProof(request, oldIds[0]);
  expect(before.event_chain).toMatchObject({ state: 'unobserved', source_metadata_complete: false, scope_metadata_incomplete: true });
  expect((await evidence(request, cases.copies.event_ids[0])).event_chain!.state).toBe('metadata_incomplete');
  await control(request, 'backfill');
  const partial = (await evidence(request, oldIds[0])).event_chain!;
  expect(partial).toMatchObject({ state: 'metadata_incomplete', source_metadata_complete: false,
    parent_state: 'null', parent_references: [], scope_metadata_incomplete: true });
  expect((await evidence(request, oldIds[255])).event_chain!.record_event_id).toBe(oldIds[255]);
  expect((await evidence(request, oldIds[256])).event_chain!.record_event_id).toBeNull();
  await open(page); const gap = await openEvent(page, oldIds[0]);
  await expect(gap.locator('[data-chain-state="metadata_incomplete"]')).toHaveCount(1);
  await expect(gap.locator('[data-chain-incomplete="true"]')).toHaveCount(1);
  await expect(gap.locator('[data-chain-reference]')).toHaveCount(0);
  await show(gap); await page.screenshot({ path: '../.cache/frontend-event-chain-backlog-desktop.png' });
  await control(request, 'backfill');
  expect((await evidence(request, oldIds[259])).event_chain).toMatchObject({ state: 'null_parent', source_metadata_complete: true, scope_metadata_incomplete: false });
  expect((await rawProof(request, oldIds[0])).event.quote_sha256).toBe(before.event.quote_sha256);
  const initial = await evidence(request, cases.late.event_ids[0]); expect(initial.event_chain!.state).toBe('missing_parent');
  const panel = await openEvent(page, cases.late.event_ids[0]);
  await expect(panel.locator('[data-chain-state="missing_parent"]')).toHaveCount(1);
  const added: Case = await control(request, 'append-parent');
  const current = await evidence(request, cases.late.event_ids[0]);
  expect(current.event_chain).toMatchObject({ state: 'linked', resolution_scope: 'project_uuid',
    parent_references: [{ event_id: added.event_ids[0], file_instance_id: added.file_instance_id }],
    record_event_id: initial.event_chain!.record_event_id, parent_uuid: initial.event_chain!.parent_uuid,
    recorded_at: initial.event_chain!.recorded_at });
  await page.getByLabel('前后上下文').selectOption('1');
  await expect(panel.locator('[data-chain-state="linked"]')).toHaveCount(1);
  await panel.getByRole('button', { name: `打开父记录原文 #${added.event_ids[0]}`, exact: true }).click();
  await expect(page.getByRole('heading', { name: `原文 #${added.event_ids[0]}`, exact: true })).toBeVisible();
});

test('真实父副本不代选，循环和祖先上限保留，未知旁支与跨项目隐藏引用，坏身份不生成导航', async ({ page, request }) => {
  const { cases } = await fixture(request);
  for (const [name, state] of Object.entries({ conflicting: 'conflicting_record', ambiguous: 'ambiguous_parent',
    cycle: 'cycle', outside: 'outside_project', missing_field: 'parent_not_declared', null_parent: 'null_parent',
    invalid_parent: 'invalid_parent', uuid_unavailable: 'uuid_unavailable' })) {
    const data = (await evidence(request, cases[name].event_ids[0])).event_chain!;
    expect(data.state).toBe(state); expect(data.parent_references).toEqual([]); expect(data.parent_references_total).toBe(0);
  }
  expect((await evidence(request, cases.invalid_sidechain.event_ids[0])).event_chain)
    .toMatchObject({ is_sidechain: null, sidechain_state: 'invalid' });
  expect((await evidence(request, cases.depth.event_ids[0])).event_chain)
    .toMatchObject({ state: 'linked', ancestry_state: 'depth_limit', ancestry_steps: 64, ancestry_limit: 64 });
  const copies = (await evidence(request, cases.copies.event_ids[0])).event_chain!;
  expect(copies).toMatchObject({ state: 'linked', parent_references_total: 21, parent_references_partial: true,
    parent_source_contexts: 21, ancestry_state: 'multiple_source_contexts' });
  expect(copies.parent_references).toHaveLength(20);
  await open(page); const panel = await openEvent(page, cases.copies.event_ids[0]);
  await expect(panel.locator('[data-chain-reference]')).toHaveCount(20);
  await expect(panel.locator('[data-chain-partial="true"]')).toHaveCount(1);
  await expect(panel.locator('[data-chain-ancestry="multiple_source_contexts"]')).toHaveCount(1);
  await show(panel); await page.screenshot({ path: '../.cache/frontend-event-chain-copies-desktop.png' });
  const chosen = copies.parent_references[1];
  await panel.getByRole('button', { name: `打开父记录原文 #${chosen.event_id}`, exact: true }).click();
  await expect(page.getByRole('heading', { name: `原文 #${chosen.event_id}`, exact: true })).toBeVisible();
  await expect(panel.locator('[data-chain-state="null_parent"]')).toHaveCount(1);
  await expect(panel.locator('.event-chain-metadata')).toContainText(`#${chosen.file_instance_id}`);
  for (const name of ['missing_field', 'invalid_sidechain', 'outside', 'cycle']) {
    const current = await openEvent(page, cases[name].event_ids[0]);
    await expect(current.getByRole('button', { name: /^打开父记录原文/ })).toHaveCount(0);
    await expect(current.locator('[data-chain-sidechain]')).not.toContainText('false');
  }
  await page.route(`**/api/evidence/${cases.multiblock.event_ids[2]}?**`, async route => {
    const response = await route.fetch(); const data: EvidenceData = await response.json();
    data.event_chain!.event_id = cases.multiblock.event_ids[1]; await route.fulfill({ response, json: data });
  });
  const bad = await openEvent(page, cases.multiblock.event_ids[2]);
  await expect(bad.getByRole('alert')).toContainText('关系暂不可判断');
  await expect(bad.getByRole('button')).toHaveCount(0);
});

test('390导航取消后迟到真实200和401不覆盖新来源父链，未知旁支与引用卡实际在屏内', async ({ page, request }) => {
  const { cases } = await fixture(request); const id = cases.multiblock.event_ids[2];
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    const actual = window.fetch.bind(window); const received: { status: number; late: string | null }[] = [];
    (window as unknown as { chainResponses: typeof received }).chainResponses = received;
    window.fetch = async (input, options) => {
      if (!String(input).startsWith('/api/evidence/')) return actual(input, options);
      const response = await actual(input, { ...options, signal: undefined }); const parse = response.json.bind(response);
      response.json = async () => { const data = await parse(); received.push({ status: response.status,
        late: response.headers.get('x-synthetic-late') }); return data; }; return response;
    };
  });
  await open(page); const linked = await openEvent(page, id);
  await expect(linked.locator('[data-chain-state="linked"]')).toHaveCount(1);
  await show(linked); await page.screenshot({ path: '../.cache/frontend-event-chain-linked-mobile.png' });
  const reference = linked.locator('[data-chain-reference]').first(); await show(reference);
  const bounds = (await reference.boundingBox())!;
  const header = (await page.getByRole('dialog', { name: '原文证据' }).locator(':scope > header').boundingBox())!;
  expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  expect(bounds.y).toBeGreaterThanOrEqual(header.y + header.height); expect(bounds.y + bounds.height).toBeLessThanOrEqual(844);
  await page.screenshot({ path: '../.cache/frontend-event-chain-reference-mobile.png' });
  const proof = await rawProof(request, id);
  for (const status of [200, 401]) {
    let release = () => {}; const held = new Promise<void>(resolve => { release = resolve; });
    let ready = () => {}; const started = new Promise<void>(resolve => { ready = resolve; }); let deferred = false;
    await page.route(`**/api/evidence/${id}?**`, async route => {
      if (!deferred) {
        deferred = true; const response = await route.fetch(status === 401 ? { headers: {
          ...route.request().headers(), Authorization: 'Bearer synthetic-wrong-token' } } : {});
        expect(response.status()).toBe(status); ready(); await held;
        await route.fulfill({ response, headers: { ...response.headers(), 'x-synthetic-late': String(status) } });
      } else await route.continue();
    });
    await openEvent(page, id); await started; await page.keyboard.press('Escape');
    const current = await openEvent(page, cases.outside.event_ids[0]);
    await expect(current.locator('[data-chain-state="outside_project"]')).toHaveCount(1); release();
    await expect.poll(() => page.evaluate(status =>
      (window as unknown as { chainResponses: { status: number; late: string | null }[] }).chainResponses
        .some(item => item.status === status && item.late === String(status)), status)).toBe(true);
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(current.locator('[data-chain-state="outside_project"]')).toHaveCount(1);
    await expect(current.getByRole('button', { name: /^打开父记录原文/ })).toHaveCount(0);
    await expect(current.locator('[data-chain-sidechain="missing"]')).toContainText('未知');
    await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: `原文 #${cases.outside.event_ids[0]}`, exact: true })).toBeVisible();
    await page.unroute(`**/api/evidence/${id}?**`);
  }
  const panel = page.getByRole('region', { name: 'Claude 事件父链依据', exact: true });
  await show(panel); await page.screenshot({ path: '../.cache/frontend-event-chain-outside-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.getByRole('dialog', { name: '原文证据' }).evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect((await rawProof(request, id)).event.quote_sha256).toBe(proof.event.quote_sha256);
});
