import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import type { EvidenceData, Project } from '../../src/types';

const origin = 'http://127.0.0.1:9798';
const token = 'synthetic-session-parent-token';
const headers = { Authorization: `Bearer ${token}` };
const root = fileURLToPath(new URL('../../../', import.meta.url));
const primary = '验收父线程项目';
let server: ChildProcess | undefined;
type Case = { event_id: number; session_pk: number; head_events: number[] };
type Fixture = { project: string; cases: Record<string, Case> };

test.beforeAll(async () => {
  let existing: Response | undefined;
  try { existing = await fetch(`${origin}/api/projects`, { headers }); } catch { /* 无监听。 */ }
  if (existing) {
    if (!existing.ok) throw new Error('9798 被其他服务占用，未终止已有进程。');
    expect((await existing.json()).projects.map((item: Project) => item.name).sort())
      .toEqual([primary, '验收父线程项目二'].sort());
    console.info('复用已核对令牌和项目的合成服务，不终止其进程。');
    return;
  }
  const log = createWriteStream(`${root}/.cache/frontend-session-parent-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.session_parent_browser', '--port', '9798', '--web-dir', `${root}/web/dist`],
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
async function fixture(request: APIRequestContext): Promise<Fixture> {
  const response = await request.get(`${origin}/synthetic-parent-fixture/cases`, { headers });
  expect(response.status()).toBe(200); return await response.json();
}
async function evidence(request: APIRequestContext, id: number): Promise<EvidenceData> {
  const response = await request.get(`${origin}/api/evidence/${id}?context=0`, { headers });
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
async function show(panel: Locator) {
  await panel.evaluate(node => node.scrollIntoView({ block: 'start' }));
}
async function rawProof(request: APIRequestContext, id: number) {
  const data = await evidence(request, id);
  expect(data.event.quote_sha256).toBeNull();
  const response = await request.get(`${origin}/api/evidence/${id}?start=0&end=${data.event.total_bytes}&context=0`, { headers });
  expect(response.status()).toBe(200); const full: EvidenceData = await response.json();
  expect(full.event.quote_sha256).toBe(createHash('sha256').update(full.event.quote).digest('hex'));
  expect(Buffer.byteLength(full.event.quote)).toBe(full.event.total_bytes);
  return full;
}

test('真实两处头声明与关联分别显示，导航只用事件ID且原文摘要不变，HTML不执行', async ({ page, request }) => {
  const { cases } = await fixture(request); const data = await evidence(request, cases.linked.event_id);
  expect(data.session_parent).toMatchObject({ state: 'linked', session_pk: cases.linked.session_pk,
    parent_session_pk: cases.parent.session_pk, parent_event_id: cases.parent.head_events[0] });
  expect(data.session_parent!.observations[0].basis).toBe('both');
  const raw = await rawProof(request, cases.linked.event_id);
  expect(raw.event.quote).toContain('<script>window.parentInjected=true</script>');
  const requests: string[] = [];
  page.on('request', item => { if (new URL(item.url()).pathname.startsWith('/api/evidence/')) requests.push(item.url()); });
  await open(page); const panel = await openEvent(page, cases.linked.event_id);
  await expect(panel.locator('[data-parent-state="linked"]')).toHaveCount(1);
  await expect(panel.locator('[data-parent-observation]')).toHaveCount(1);
  await show(panel); await page.screenshot({ path: '../.cache/frontend-session-parent-linked-desktop.png' });
  const head = data.session_parent!.observations[0].event_id;
  await panel.getByRole('button', { name: `打开声明原文 #${head}`, exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '原文证据', exact: true });
  await expect(dialog.getByRole('heading', { name: `原文 #${head}`, exact: true })).toBeVisible();
  await expect(dialog.locator('.raw-event.focused')).toContainText(data.session_parent!.parent_native_id!);
  expect((await rawProof(request, head)).event.quote).toContain('parent_thread_id');
  await expect(panel.locator('[data-parent-state="linked"]')).toHaveCount(1);
  await panel.getByRole('button', { name: `打开父线程头原文 #${cases.parent.head_events[0]}`, exact: true }).click();
  await expect(dialog.getByRole('heading', { name: `原文 #${cases.parent.head_events[0]}`, exact: true })).toBeVisible();
  await expect(panel.locator('[data-parent-state="no_parent_declared"]')).toHaveCount(1);
  await expect(dialog.locator('.raw-event.focused')).toContainText(data.session_parent!.parent_native_id!);
  await show(dialog.locator('.raw-event.focused'));
  await page.screenshot({ path: '../.cache/frontend-session-parent-head-desktop.png' });
  expect(await page.evaluate(() => 'parentInjected' in window)).toBe(false);
  expect(requests.length).toBeGreaterThanOrEqual(3);
  for (const value of requests) {
    const params = new URL(value).searchParams;
    expect(params.has('start')).toBe(false); expect(params.has('end')).toBe(false);
  }
  expect((await rawProof(request, cases.linked.event_id)).event.quote_sha256).toBe(raw.event.quote_sha256);
});

test('真实迟到父头只更新关联，全部历史冲突不会被最近20条一致声明抹去', async ({ page, request }) => {
  const { cases } = await fixture(request); const initial = await evidence(request, cases.late.event_id);
  expect(initial.session_parent!.state).toBe('missing_parent');
  await open(page); let panel = await openEvent(page, cases.late.event_id);
  await expect(panel.locator('[data-parent-state="missing_parent"]')).toHaveCount(1);
  const appended = await request.post(`${origin}/synthetic-parent-fixture/append-parent`, { headers, data: {} });
  expect(appended.status()).toBe(200); const parent: Case = await appended.json();
  const current = await evidence(request, cases.late.event_id);
  expect(current.session_parent).toMatchObject({ state: 'linked', parent_session_pk: parent.session_pk,
    parent_event_id: parent.head_events[0], observations: initial.session_parent!.observations });
  await page.getByLabel('前后上下文').selectOption('1');
  await expect(panel.locator('[data-parent-state="linked"]')).toHaveCount(1);
  await panel.getByRole('button', { name: `打开父线程头原文 #${parent.head_events[0]}`, exact: true }).click();
  await expect(page.getByRole('heading', { name: `原文 #${parent.head_events[0]}`, exact: true })).toBeVisible();
  const conflict = (await evidence(request, cases.conflicting.event_id)).session_parent!;
  expect(conflict).toMatchObject({ state: 'conflicting', observations_total: 21, observations_partial: true,
    parent_session_pk: null, parent_native_id: null, parent_event_id: null });
  expect(new Set(conflict.observations.map(row => row.parent_id)).size).toBe(1);
  expect(conflict.observations).toHaveLength(20);
  expect(conflict.observations.some(row => row.event_id === cases.conflicting.head_events[0])).toBe(false);
  const oldest = await rawProof(request, cases.conflicting.head_events[0]);
  expect(oldest.event.quote).not.toContain(conflict.observations[0].parent_id!);
  panel = await openEvent(page, cases.conflicting.event_id);
  await expect(panel.locator('[data-parent-state="conflicting"]')).toHaveCount(1);
  await expect(panel.locator('[data-parent-partial="true"]')).toHaveCount(1);
  await expect(panel.locator('[data-parent-observation]')).toHaveCount(20);
  await expect(panel.getByRole('button', { name: /^打开父线程头原文/ })).toHaveCount(0);
  await show(panel); await page.screenshot({ path: '../.cache/frontend-session-parent-conflict-desktop.png' });
});

test('真实未知与异常关系不猜根线程，跨项目不公开父引用，坏会话身份不提供导航', async ({ page, request }) => {
  const { cases } = await fixture(request);
  for (const [name, state] of Object.entries({ unobserved: 'unobserved', none: 'no_parent_declared',
    invalid: 'invalid', cycle: 'cycle', ambiguous: 'ambiguous_parent', outside: 'outside_project' })) {
    const data = (await evidence(request, cases[name].event_id)).session_parent!;
    expect(data.state).toBe(state);
    expect(data.parent_session_pk).toBeNull(); expect(data.parent_native_id).toBeNull(); expect(data.parent_event_id).toBeNull();
  }
  await open(page);
  for (const [name, state] of [['unobserved', 'unobserved'], ['none', 'no_parent_declared'], ['outside', 'outside_project']]) {
    const panel = await openEvent(page, cases[name].event_id);
    await expect(panel.locator(`[data-parent-state="${state}"]`)).toHaveCount(1);
    await expect(panel.getByRole('button', { name: /^打开父线程头原文/ })).toHaveCount(0);
    if (name === 'outside') {
      const outside = (await evidence(request, cases.outside.event_id)).session_parent!;
      await expect(panel).toContainText(outside.observations[0].parent_id!);
      expect(await panel.textContent()).not.toContain(`同项目父会话 #${cases.outside_parent.session_pk}`);
    }
  }
  await page.route(`**/api/evidence/${cases.linked.event_id}?**`, async route => {
    const response = await route.fetch(); const data: EvidenceData = await response.json();
    data.session_parent!.session_pk = cases.none.session_pk;
    await route.fulfill({ response, json: data });
  });
  const broken = await openEvent(page, cases.linked.event_id);
  await expect(broken.getByRole('alert')).toContainText('关系暂不可判断');
  await expect(broken.getByRole('button')).toHaveCount(0);
  await expect(broken.locator('[data-parent-state]')).toHaveCount(0);
});

test('390原文导航取消后迟到真实200及401不替换新父依据，卡片实际在屏内', async ({ page, request }) => {
  const { cases } = await fixture(request);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    const actual = window.fetch.bind(window); const received: { status: number; late: string | null }[] = [];
    (window as unknown as { parentResponses: typeof received }).parentResponses = received;
    window.fetch = async (input, options) => {
      if (!String(input).startsWith('/api/evidence/')) return actual(input, options);
      const response = await actual(input, { ...options, signal: undefined }); const parse = response.json.bind(response);
      response.json = async () => { const data = await parse(); received.push({ status: response.status,
        late: response.headers.get('x-synthetic-late') }); return data; }; return response;
    };
  });
  await open(page); const linked = await openEvent(page, cases.linked.event_id);
  await expect(linked.locator('[data-parent-state="linked"]')).toHaveCount(1);
  await show(linked); await page.screenshot({ path: '../.cache/frontend-session-parent-linked-mobile.png' });
  const card = linked.locator('[data-parent-observation]').first(); await show(card);
  const bounds = (await card.boundingBox())!;
  expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  expect(bounds.y).toBeGreaterThanOrEqual(0); expect(bounds.y + bounds.height).toBeLessThanOrEqual(844);
  const original = await rawProof(request, cases.linked.head_events[0]);
  expect(original.event.quote).toContain('thread_spawn');
  for (const status of [200, 401]) {
    let release = () => {}; const held = new Promise<void>(resolve => { release = resolve; });
    let ready = () => {}; const started = new Promise<void>(resolve => { ready = resolve; }); let deferred = false;
    await page.route(`**/api/evidence/${cases.linked.event_id}?**`, async route => {
      if (!deferred) {
        deferred = true; const response = await route.fetch(status === 401 ? { headers: {
          ...route.request().headers(), Authorization: 'Bearer synthetic-wrong-token' } } : {});
        expect(response.status()).toBe(status); ready(); await held;
        await route.fulfill({ response, headers: { ...response.headers(), 'x-synthetic-late': String(status) } });
      } else await route.continue();
    });
    await openEvent(page, cases.linked.event_id); await started;
    await page.keyboard.press('Escape');
    const current = await openEvent(page, cases.outside.event_id);
    await expect(current.locator('[data-parent-state="outside_project"]')).toHaveCount(1);
    release();
    await expect.poll(() => page.evaluate(status =>
      (window as unknown as { parentResponses: { status: number; late: string | null }[] }).parentResponses
        .some(item => item.status === status && item.late === String(status)), status)).toBe(true);
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(current.locator('[data-parent-state="outside_project"]')).toHaveCount(1);
    await expect(current.getByRole('button', { name: /^打开父线程头原文/ })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: `原文 #${cases.outside.event_id}`, exact: true })).toBeVisible();
    await page.unroute(`**/api/evidence/${cases.linked.event_id}?**`);
  }
  const panel = page.getByRole('region', { name: 'Codex 父线程依据', exact: true });
  await show(panel); await page.screenshot({ path: '../.cache/frontend-session-parent-outside-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.getByRole('dialog', { name: '原文证据' }).evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect((await rawProof(request, cases.linked.head_events[0])).event.quote_sha256).toBe(original.event.quote_sha256);
});
