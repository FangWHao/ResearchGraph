import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { adoption, evidenceState, foldGroup, graphNodeId, isSemanticEdge, projectGraph } from '../../src/model';
import { appendResearchPage, parseResearchPage, researchQuery } from '../../src/researchGraph';
import type { ResearchPage } from '../../src/researchGraph';
import type { Claim, ResearchReading } from '../../src/types';

const origin = 'http://127.0.0.1:9801', token = 'synthetic-research-graph-token';
const headers = { Authorization: `Bearer ${token}` };
const root = fileURLToPath(new URL('../../../', import.meta.url));
const primary = '验收语义图项目', secondary = '验收语义图缺口项目';
let server: ChildProcess | undefined;
interface Fixture { project: string; secondary: string; chain: number[]; boundary: number; historical: number; replacement: number; reextract: number; history_event: number; total: number; reading: { occurred_until: string; known_until: string }; execution: { event_id: number; run_id: string } }
test.beforeAll(async () => {
  let existing: Response | undefined;
  try { existing = await fetch(`${origin}/api/projects`, { headers }); } catch { /* 无监听。 */ }
  if (existing) {
    if (!existing.ok) throw new Error('9801被其它服务占用，未终止已有进程。');
    expect((await existing.json()).projects.map((item: { name: string }) => item.name).sort()).toEqual([primary, secondary].sort());
    return;
  }
  const log = createWriteStream(`${root}/.cache/frontend-full-research-graph-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.research_graph_browser', '--port', '9801', '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => {
    if (server?.exitCode !== null) throw new Error('合成服务提前退出，见服务日志。');
    try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; }
  }).toBe(200);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) { const closed = new Promise<void>(resolve => server!.once('exit', () => resolve())); server.kill('SIGINT'); await closed; }
});
async function fixture(request: APIRequestContext): Promise<Fixture> {
  const response = await request.get(`${origin}/synthetic-full-graph/cases`, { headers }); expect(response.status()).toBe(200); return response.json();
}
async function records(request: APIRequestContext, project: string, draft: Record<string, string> = {}) {
  let offset = 0, first: ResearchPage | undefined, claims: Claim[] = [];
  do {
    const params = new URLSearchParams(Object.entries({ ...(first ? researchQuery(first) : { project, ...draft }), collection: 'claims', limit: 100, offset }).map(([key, value]) => [key, String(value)]));
    const response = await request.get(`${origin}/api/semantic-graph?${params}`, { headers }); expect(response.status()).toBe(200);
    const page = parseResearchPage(await response.json(), project, offset, first); first ??= page;
    claims = appendResearchPage(claims, page); if (page.next_offset === null) break; offset = page.next_offset;
  } while (true);
  return { ...first!, claims };
}
async function open(page: Page) {
  await page.goto(`${origin}/#token=${token}`);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  await page.getByRole('button', { name: '研究图', exact: true }).click();
  await complete(page);
}
async function complete(page: Page) {
  await expect(page.locator('[data-research-summary]')).toContainText('双时间内全部 L2 登记记录');
  await expect(page.getByRole('button', { name: '重新读取研究图', exact: true })).toBeEnabled();
}
async function reading(page: Page): Promise<ResearchReading> {
  return JSON.parse((await page.locator('[data-research-summary]').getAttribute('data-reading'))!);
}
async function history(page: Page, value: Fixture) {
  await page.getByLabel('研究图发生时间截止', { exact: true }).fill(value.reading.occurred_until);
  await page.getByLabel('研究图已知时间截止', { exact: true }).fill(value.reading.known_until);
  await page.getByRole('button', { name: '重新读取研究图', exact: true }).click(); await complete(page);
}
async function select(page: Page, members: Claim[]) {
  const picker = page.locator('.graph-node-picker');
  if (!await picker.evaluate(node => (node as HTMLDetailsElement).open)) await picker.locator('summary').click();
  for (const claim of members) await picker.locator(`input[data-node-id=${JSON.stringify(graphNodeId(claim))}]`).check();
}
async function snapshot(page: Page) {
  return page.evaluate(() => ({ nodes: [...document.querySelectorAll('.react-flow__node')].map(node => ({ id: node.getAttribute('data-id'), position: (node as HTMLElement).style.transform })).sort((a, b) => a.id!.localeCompare(b.id!)), edges: [...document.querySelectorAll('.react-flow__edge')].map(node => node.getAttribute('data-id')).sort() }));
}
async function canvasVisible(page: Page) {
  const canvas = page.getByTestId('research-canvas'); await canvas.evaluate(node => node.scrollIntoView({ block: 'center' }));
  await expect.poll(() => canvas.evaluate(node => {
    const area = node.getBoundingClientRect();
    return [...node.querySelectorAll('.react-flow__node')].filter(element => {
      const box = element.getBoundingClientRect(); return box.width > 0 && box.height > 0 && Math.min(box.right, area.right, innerWidth) > Math.max(box.left, area.left, 0) && Math.min(box.bottom, area.bottom, innerHeight) > Math.max(box.top, area.top, 0);
    }).length;
  })).toBeGreaterThan(0);
}
async function memberDrawer(page: Page, claim: Claim) {
  const node = page.locator(`.react-flow__node[data-id=${JSON.stringify(graphNodeId(claim))}]`);
  const selector = node.locator('select'); if (await selector.count()) await selector.selectOption(String(claim.claim_id));
  await node.locator('strong').click(); const drawer = page.getByRole('dialog', { name: `记录 ${claim.claim_id} 详情`, exact: true });
  await expect(drawer.locator('.drawer-evidence')).toBeVisible(); return drawer;
}

test('真实完整记录超过2000且保留35引用，折叠恢复全部ID端口证据状态与原位置', async ({ page, request }) => {
  const cases = await fixture(request); const pages: { page: ResearchPage; query: URLSearchParams }[] = [];
  page.on('response', async response => { if (new URL(response.url()).pathname === '/api/semantic-graph' && response.status() === 200) pages.push({ page: await response.json(), query: new URL(response.url()).searchParams }); });
  await open(page); await expect.poll(() => pages.length).toBe(Math.ceil(cases.total / 100));
  const first = pages[0].page, claims = pages.flatMap(item => item.page.items);
  expect(claims).toHaveLength(cases.total); expect(claims.length).toBeGreaterThan(2000); expect(new Set(claims.map(c => c.claim_id)).size).toBe(cases.total);
  expect(claims.find(c => c.claim_id === cases.boundary)!.evidence).toHaveLength(35);
  for (const item of pages.slice(1)) {
    expect(item.query.get('expected_revision')).toBe(String(first.revision)); expect(item.query.get('occurred_until')).toBe(first.occurred_until); expect(item.query.get('known_until')).toBe(first.known_until);
    expect(item.page.total).toBe(first.total); expect(item.page.project_id).toBe(first.project_id);
  }
  const graph = projectGraph(claims); expect(graph.diagnostics).toEqual([]);
  await expect(page.locator('.react-flow__node')).toHaveCount(graph.entities.length);
  const members = cases.chain.slice(1, 3).map(id => claims.find(c => c.claim_id === id)!);
  const folded = foldGroup(members.map(graphNodeId), graph.edges, claims);
  const states = members.map(c => [adoption(claims, c.entity_id!, c.scope), evidenceState(claims, c.entity_id!, c.scope)]);
  const before = await snapshot(page); await select(page, members);
  await page.getByRole('button', { name: '折叠所选（2）', exact: true }).click(); await expect(page.locator('.process-node')).toHaveCount(1);
  expect(await page.locator('.graph-retained article').evaluateAll(nodes => nodes.map(node => Number(node.getAttribute('data-claim-id'))))).toEqual(folded.claimIds);
  expect(await page.locator('[data-original-edge]').evaluateAll(nodes => nodes.map(node => JSON.parse(node.getAttribute('data-original-edge')!)))).toEqual([...folded.internalEdges, ...folded.boundaryEdges]);
  expect(folded.evidenceIds).toEqual([...new Set(claims.filter(c => folded.claimIds.includes(c.claim_id)).flatMap(c => c.evidence.map(s => s.span_id)))].sort((a, b) => a - b));
  await expect(page.locator('.process-node .node-dimensions')).toHaveCount(0);
  const boundary = page.locator('.graph-original-edge').getByRole('button', { name: new RegExp(`^#${cases.boundary} `) }); await boundary.click();
  await expect(page.locator('.drawer-evidence .evidence-link')).toHaveCount(35); await expect(page.locator('.drawer-actions button')).toHaveCount(0);
  await page.locator('.drawer-evidence .evidence-link').first().click(); const span = claims.find(c => c.claim_id === cases.boundary)!.evidence[0];
  const raw = await (await request.get(`${origin}/api/evidence/${span.event_id}?${new URLSearchParams(Object.entries({ ...researchQuery(await reading(page)), start: span.byte_start, end: span.byte_end }).map(([k, v]) => [k, String(v)]))}`, { headers })).json();
  await expect(page.getByTestId('evidence-quote')).toHaveText(raw.event.quote); expect(createHash('sha256').update(raw.event.quote).digest('hex')).toBe(span.quote_sha256);
  expect(raw.history_context).toBe(true); expect(raw.derived_context_loaded).toBe(false); expect(raw).not.toHaveProperty('l1');
  await page.getByRole('button', { name: '关闭原文', exact: true }).click(); await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  await page.locator('.graph-node-picker summary').click(); await page.getByRole('button', { name: '聚焦过程组', exact: true }).click(); await canvasVisible(page);
  await page.screenshot({ path: '../.cache/frontend-full-research-graph-fold-desktop.png' });
  await page.getByRole('button', { name: '展开过程组', exact: true }).click(); await expect.poll(() => snapshot(page)).toEqual(before);
  expect(members.map(c => [adoption(claims, c.entity_id!, c.scope), evidenceState(claims, c.entity_id!, c.scope)])).toEqual(states);
  const compare = claims.find(c => c.claim_type === 'join_ports' && c.payload.semantics === 'compare_then_select')!;
  expect(graph.edges.filter(e => e.claimId === compare.claim_id && e.role === 'compared_input').every(e => !isSemanticEdge(e))).toBe(true);
  const native = await (await request.get(`${origin}/api/evidence/${cases.execution.event_id}?context=0`, { headers })).json(); expect(native.l1.runs[0].state).toBe('requested'); expect(native.l1.runs[0].exit_code).toBeNull();
});

test('双截止历史详情保留当时候选和更正前内容，原文不混未来上下文或今天派生', async ({ page, request }) => {
  const cases = await fixture(request); await open(page); await history(page, cases);
  const data = await records(request, cases.project, cases.reading); const original = data.claims.find(c => c.claim_id === cases.historical)!;
  expect(original.effective_state).toBe('candidate'); expect(original.replaced).toBe(false); expect(original.replacement_ids).toEqual([]); expect(data.claims.some(c => c.claim_id === cases.replacement)).toBe(false);
  const now = await (await request.get(`${origin}/api/claims/${cases.historical}`, { headers })).json(); expect(now.claim.effective_state).toBe('confirmed'); expect(now.claim.replacement_ids).toContain(cases.replacement);
  const drawer = await memberDrawer(page, original); await expect(drawer.locator('.badge')).toHaveText('待复核'); await expect(drawer.locator('.drawer-actions button')).toHaveCount(0);
  await page.screenshot({ path: '../.cache/frontend-full-research-graph-history-desktop.png' });
  await drawer.locator('.evidence-link').first().click(); const span = original.evidence[0];
  const historic = await (await request.get(`${origin}/api/evidence/${span.event_id}?${new URLSearchParams(Object.entries({ ...researchQuery(await reading(page)), start: span.byte_start, end: span.byte_end, context: 2 }).map(([k, v]) => [k, String(v)]))}`, { headers })).json();
  expect(historic.after).toEqual([]); expect(historic).not.toHaveProperty('artifact_versions'); expect(historic).not.toHaveProperty('session_parent'); expect(historic).not.toHaveProperty('event_chain');
  await expect(page.getByTestId('evidence-quote')).toHaveText(historic.event.quote); expect(createHash('sha256').update(historic.event.quote).digest('hex')).toBe(span.quote_sha256);
  await expect(page.getByRole('dialog', { name: '原文证据' }).locator('.raw-event')).toHaveCount(1); expect(await page.evaluate(() => 'graphInjected' in window)).toBe(false);
  await page.screenshot({ path: '../.cache/frontend-full-research-graph-evidence-desktop.png' });
  await page.getByRole('button', { name: '关闭原文', exact: true }).click(); await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  const advance = await request.post(`${origin}/synthetic-full-graph/advance`, { headers, data: {} }); expect(advance.status()).toBe(200);
  await page.getByRole('button', { name: '重新读取研究图', exact: true }).click(); await complete(page);
  expect((await reading(page)).known_until).toBe(data.known_until); await memberDrawer(page, original); await expect(page.locator('.detail-drawer .badge')).toHaveText('待复核');
});

test('真实第二页409保留旧完整图且禁止折叠，主动重读新修订；错页不混入旧图', async ({ page, request }) => {
  await open(page); const before = await reading(page), snapshotBefore = await snapshot(page);
  let intercepted = false, status = 0;
  await page.route('**/api/semantic-graph?*', async route => {
    if (new URL(route.request().url()).searchParams.get('offset') !== '100' || intercepted) { await route.continue(); return; }
    intercepted = true; expect((await request.post(`${origin}/synthetic-full-graph/advance`, { headers, data: {} })).status()).toBe(200);
    const response = await route.fetch(); status = response.status(); await route.fulfill({ response });
  });
  await page.getByRole('button', { name: '重新读取研究图', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('修订冲突'); expect(status).toBe(409);
  expect(await reading(page)).toEqual(before); expect(await snapshot(page)).toEqual(snapshotBefore);
  await page.getByRole('button', { name: '折叠所选（0）', exact: true }).click(); await expect(page.locator('.graph-notice')).toContainText('完整研究图');
  await page.unroute('**/api/semantic-graph?*'); await page.getByRole('button', { name: '重新读取研究图', exact: true }).click(); await complete(page);
  expect((await reading(page)).revision).toBeGreaterThan(before.revision); const refreshed = await reading(page);
  await page.route('**/api/semantic-graph?*', async route => {
    const response = await route.fetch(); const body = await response.json();
    await route.fulfill({ response, json: new URL(route.request().url()).searchParams.get('offset') === '100' ? { ...body, project_id: 'synthetic-wrong-project' } : body });
  });
  await page.getByRole('button', { name: '重新读取研究图', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('分页不一致'); expect(await reading(page)).toEqual(refreshed); expect(await snapshot(page)).toEqual(snapshotBefore);
});

test('受控transport迟到真实图页200与401不进入新条件或其它项目，历史导航取消保持身份', async ({ page, request }) => {
  const cases = await fixture(request);
  await page.addInitScript(() => {
    const actual = window.fetch.bind(window); const received: string[] = []; (window as unknown as { graphResponses: string[] }).graphResponses = received;
    window.fetch = async (input, options) => {
      if (!/\/api\/(semantic-graph|claims\/|evidence\/)/.test(String(input))) return actual(input, options);
      const response = await actual(input, { ...options, signal: undefined }); const parse = response.json.bind(response);
      response.json = async () => { const data = await parse(); const late = response.headers.get('x-synthetic-late'); if (late) received.push(late); return data; }; return response;
    };
  });
  await open(page); const originalReading = await reading(page);
  for (const status of [200, 401]) {
    let release = () => {}, ready = () => {}; const held = new Promise<void>(resolve => { release = resolve; }), started = new Promise<void>(resolve => { ready = resolve; }); let first = true;
    await page.route('**/api/semantic-graph?*', async route => {
      if (!first) { await route.continue(); return; } first = false;
      const response = await route.fetch(status === 401 ? { headers: { ...route.request().headers(), Authorization: 'Bearer synthetic-wrong-token' } } : {}); expect(response.status()).toBe(status); ready(); await held;
      await route.fulfill({ response, headers: { ...response.headers(), 'x-synthetic-late': String(status) } });
    });
    await page.getByRole('button', { name: '重新读取研究图', exact: true }).click(); await started;
    if (status === 200) await page.getByLabel('研究图发生时间截止', { exact: true }).fill(cases.reading.occurred_until);
    else { await page.getByLabel('当前项目', { exact: true }).selectOption({ label: secondary }); await complete(page); }
    release(); await expect.poll(() => page.evaluate(value => (window as unknown as { graphResponses: string[] }).graphResponses.includes(String(value)), status)).toBe(true);
    expect((await reading(page)).project_id).toBe(status === 200 ? cases.project : cases.secondary);
    if (status === 200) expect(await reading(page)).toEqual(originalReading);
    await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toHaveCount(0);
    await page.unroute('**/api/semantic-graph?*');
  }
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary }); await complete(page); await history(page, cases);
  const data = await records(request, cases.project, cases.reading), original = data.claims.find(c => c.claim_id === cases.historical)!;
  let release = () => {}, ready = () => {}; const held = new Promise<void>(resolve => { release = resolve; }), started = new Promise<void>(resolve => { ready = resolve; });
  await page.route(`**/api/claims/${original.claim_id}?*`, async route => { const response = await route.fetch(); expect(response.status()).toBe(200); ready(); await held; await route.fulfill({ response, headers: { ...response.headers(), 'x-synthetic-late': 'detail' } }); });
  const node = page.locator(`.react-flow__node[data-id=${JSON.stringify(graphNodeId(original))}]`); await node.locator('strong').click(); await started;
  await page.getByRole('button', { name: '关闭详情', exact: true }).click(); await page.getByLabel('研究图已知时间截止', { exact: true }).fill('2026-01-01T00:00:00Z'); release();
  await expect.poll(() => page.evaluate(() => (window as unknown as { graphResponses: string[] }).graphResponses.includes('detail'))).toBe(true); await expect(page.locator('.detail-drawer')).toHaveCount(0);
});

test('390手机直接换项目并读取完整图，画布实际可见，折叠历史与只读原文在屏内', async ({ page, request }) => {
  const cases = await fixture(request); await page.setViewportSize({ width: 390, height: 844 }); await open(page);
  const selector = page.getByLabel('当前项目', { exact: true }); await expect(selector).toBeInViewport();
  await selector.selectOption({ label: secondary }); await complete(page); expect((await reading(page)).project_id).toBe(cases.secondary);
  await selector.selectOption({ label: primary }); await complete(page); expect((await reading(page)).project_id).toBe(cases.project);
  for (const field of ['研究图发生时间截止', '研究图已知时间截止']) { const bounds = (await page.getByLabel(field, { exact: true }).boundingBox())!; expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(390); }
  await canvasVisible(page); await page.screenshot({ path: '../.cache/frontend-full-research-graph-canvas-mobile.png' });
  const data = await records(request, cases.project), members = cases.chain.slice(1, 3).map(id => data.claims.find(c => c.claim_id === id)!);
  await select(page, members); await page.getByRole('button', { name: '折叠所选（2）', exact: true }).click(); await expect(page.locator('.process-node')).toHaveCount(1);
  const record = page.locator('.graph-retained article').filter({ hasText: '决定 · 撤回' }).first(); await record.evaluate(node => node.scrollIntoView({ block: 'center' }));
  const box = (await record.boundingBox())!; expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: '../.cache/frontend-full-research-graph-history-mobile.png' }); await record.getByRole('button').click();
  await expect(page.locator('.drawer-actions button')).toHaveCount(0); await page.locator('.drawer-evidence .evidence-link').first().click();
  const quote = page.getByTestId('evidence-quote'); await expect(quote).not.toBeEmpty(); await quote.evaluate(node => node.scrollIntoView({ block: 'center' }));
  const quoteBox = (await quote.boundingBox())!; const header = (await page.getByRole('dialog', { name: '原文证据' }).locator(':scope > header').boundingBox())!;
  expect(quoteBox.x).toBeGreaterThanOrEqual(0); expect(quoteBox.x + quoteBox.width).toBeLessThanOrEqual(390); expect(quoteBox.y).toBeGreaterThanOrEqual(header.y + header.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.screenshot({ path: '../.cache/frontend-full-research-graph-evidence-mobile.png' });
});
