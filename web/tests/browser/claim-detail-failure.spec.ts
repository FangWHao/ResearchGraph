import { createHash } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import type { Claim, GraphData, Project, ResearchReading } from '../../src/types';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const name = '合成研究 · 界面信号验证';
const proof = '../.cache/frontend-claim-detail-failure-api';
mkdirSync(proof, { recursive: true });
function capture(key: string, value: unknown) { writeFileSync(`${proof}/${key}.json`, `${JSON.stringify(value, null, 2)}\n`); }
async function fixture(request: APIRequestContext) {
  const response = await request.get('/api/projects', { headers }); expect(response.status()).toBe(200);
  const projects: Project[] = (await response.json()).projects;
  const project = projects.find(item => item.name === name)!; expect(project).toBeTruthy();
  const graphResponse = await request.get(`/api/graph?project=${project.project_id}`, { headers }); expect(graphResponse.status()).toBe(200);
  const graph: GraphData = await graphResponse.json();
  const question = graph.claims.find(item => item.claim_type === 'entity_version' && item.payload.kind === 'question')!;
  const approach = graph.claims.find(item => item.claim_type === 'entity_version' && item.payload.label === '界面分层分析')!;
  expect(question).toBeTruthy(); expect(approach).toBeTruthy(); capture('fixture', { project, question, approach });
  return { project, graph, question, approach, emptyProject: projects.find(item => item.name === '合成空项目')! };
}
async function open(page: Page) {
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: name });
  await expect(page.getByRole('button', { name: '查看记录与证据', exact: true })).toBeVisible();
}
function drawer(page: Page, claim: Claim) { return page.getByRole('dialog', { name: `记录 ${claim.claim_id} 详情`, exact: true }); }
async function detail(request: APIRequestContext, claim: Claim) {
  const response = await request.get(`/api/claims/${claim.claim_id}`, { headers }); expect(response.status()).toBe(200); return response.json();
}
async function readOnly(request: APIRequestContext, project: Project, graph: GraphData) {
  const response = await request.get(`/api/graph?project=${project.project_id}`, { headers }); expect(response.status()).toBe(200);
  const after = await response.json(); expect(after).toEqual(graph); capture('read-only-after', after);
}
async function inViewport(page: Page, element: Locator) {
  const box = await element.boundingBox(); expect(box).not.toBeNull();
  const header = await element.locator('xpath=ancestor::aside/header').boundingBox(); expect(header).not.toBeNull();
  const viewport = page.viewportSize()!;
  expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
  expect(box!.y).toBeGreaterThanOrEqual(header!.y + header!.height); expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
}
async function history(page: Page, claim: Claim): Promise<ResearchReading> {
  await page.getByRole('button', { name: '研究图', exact: true }).click();
  await expect(page.locator('[data-research-summary]')).toContainText('双时间内全部 L2 登记记录');
  await expect(page.locator('[data-view-ready]')).toHaveAttribute('data-view-ready', 'true');
  const reading = JSON.parse((await page.locator('[data-research-summary]').getAttribute('data-reading'))!);
  await page.locator(`.react-flow__node[data-id^="${claim.entity_id}::"] .research-node > strong`).click();
  return reading;
}
async function ignoreTransportCancellation(page: Page) {
  // 仅验收 transport：真实请求仍执行，但故意忽略 signal，证明组件清理也会忽略迟到结果。
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = (input, init) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof Request ? input.url : input.toString(), location.href);
      if (!/^\/api\/claims\/\d+$/.test(url.pathname)) return original(input, init);
      return original(input, { ...init, signal: undefined }).then(response => {
        const json = response.json.bind(response);
        response.json = async () => {
          const value = await json();
          // 下个任务才记完成，api() 的 await/json 与组件 then/catch 已有机会处理。
          setTimeout(() => {
            const state = window as typeof window & { claimDetailSettled?: { url: string; status: number }[] };
            (state.claimDetailSettled ??= []).push({ url: url.href, status: response.status });
          }, 0);
          return value;
        };
        return response;
      });
    };
  });
}
async function settled(page: Page, claim: Claim, status: number, historical = false) {
  await page.waitForFunction(({ id, status, historical }) => {
    const state = window as typeof window & { claimDetailSettled?: { url: string; status: number }[] };
    return state.claimDetailSettled?.some(item => {
      const url = new URL(item.url);
      return url.pathname === `/api/claims/${id}` && item.status === status && (url.search !== '') === historical;
    });
  }, { id: claim.claim_id, status, historical });
}

test('普通详情断网明确失败，390可主动只读重读并恢复真实记录与当前修订', async ({ page, request }) => {
  const { project, graph, question } = await fixture(request); const original = await detail(request, question);
  capture('ordinary-original', original);
  let reads = 0; const writes: string[] = [];
  page.on('request', value => { if (value.method() !== 'GET' && new URL(value.url()).pathname.startsWith('/api/')) writes.push(value.url()); });
  const path = `**/api/claims/${question.claim_id}`;
  await page.route(path, async route => { reads++; if (reads === 1) await route.abort('failed'); else await route.continue(); });
  try {
    await open(page); await page.getByRole('button', { name: '查看记录与证据', exact: true }).click();
    const panel = drawer(page, question);
    await expect(panel.getByRole('heading', { name: '记录详情暂不可用', exact: true })).toBeVisible();
    await expect(panel.getByRole('alert')).toContainText('Failed to fetch'); await expect(panel.locator('.loading')).toHaveCount(0);
    await expect(panel.getByRole('button', { name: '修改', exact: true })).toHaveCount(0); expect(reads).toBe(1);
    await page.screenshot({ path: '../.cache/frontend-claim-detail-failure-desktop.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    const retry = panel.getByRole('button', { name: '重新读取详情', exact: true }); await inViewport(page, retry);
    await page.screenshot({ path: '../.cache/frontend-claim-detail-failure-mobile.png' });
    const replied = page.waitForResponse(value => new URL(value.url()).pathname === `/api/claims/${question.claim_id}`);
    await retry.click(); const response = await replied; expect(response.status()).toBe(200); capture('ordinary-reread', await response.json());
    await expect(panel.getByRole('heading', { name: question.payload.label!, exact: true })).toBeVisible();
    await expect(panel.locator('.badge').first()).toHaveText('已确认'); await expect(panel.getByRole('alert')).toHaveCount(0); expect(reads).toBe(2);
    await panel.getByRole('button', { name: '修改', exact: true }).click();
    const edit = page.getByRole('dialog', { name: '保留原文，追加你的判断', exact: true }); await expect(edit).toContainText(`版本 ${original.revision}`);
    await edit.getByRole('button', { name: '关闭修改', exact: true }).click(); expect(writes).toEqual([]);
    expect(await detail(request, question)).toEqual(original); await readOnly(request, project, graph);
  } finally { await page.unroute(path); }
});

test('普通详情拒绝另一记录回包，主动重读后保持原记录而不自动审核', async ({ page, request }) => {
  const { project, graph, question, approach } = await fixture(request); let reads = 0;
  const path = `**/api/claims/${question.claim_id}`;
  await page.route(path, async route => {
    reads++; if (reads > 1) { await route.continue(); return; }
    const other = await route.fetch({ url: new URL(`/api/claims/${approach.claim_id}`, route.request().url()).href });
    expect(other.status()).toBe(200); await route.fulfill({ response: other });
  });
  try {
    await open(page); await page.getByRole('button', { name: '查看记录与证据', exact: true }).click(); const panel = drawer(page, question);
    await expect(panel.getByRole('alert')).toContainText('记录或修订不匹配'); await expect(panel.getByRole('heading', { name: approach.payload.label!, exact: true })).toHaveCount(0);
    expect(reads).toBe(1); await panel.getByRole('button', { name: '重新读取详情', exact: true }).click();
    await expect(panel.getByRole('heading', { name: question.payload.label!, exact: true })).toBeVisible(); expect(reads).toBe(2);
    await readOnly(request, project, graph);
  } finally { await page.unroute(path); }
});

test('历史详情网络与条件错误均可重读，沿原双截止修订保留只读和原文字节', async ({ page, request }) => {
  const { project, graph, question } = await fixture(request); let reads = 0; const urls: string[] = [];
  const path = `**/api/claims/${question.claim_id}?*`;
  await page.route(path, async route => {
    reads++; urls.push(route.request().url());
    if (reads === 1) { await route.abort('failed'); return; }
    const response = await route.fetch(); expect(response.status()).toBe(200); const value = await response.json();
    capture(`history-actual-${reads}`, value);
    await route.fulfill({ response, json: reads === 2 ? { ...value, project_id: 'incorrect-project' } : value });
  });
  try {
    await open(page); const reading = await history(page, question); capture('history-reading', reading); const panel = drawer(page, question);
    await expect(panel.getByRole('heading', { name: '历史详情暂不可用', exact: true })).toBeVisible(); expect(reads).toBe(1);
    await page.screenshot({ path: '../.cache/frontend-claim-detail-failure-history-desktop.png' });
    await panel.getByRole('button', { name: '重新读取详情', exact: true }).click();
    await expect(panel.getByRole('alert')).toContainText('项目、双时间、修订'); expect(reads).toBe(2);
    await panel.getByRole('button', { name: '重新读取详情', exact: true }).click();
    await expect(panel.getByRole('heading', { name: question.payload.label!, exact: true })).toBeVisible(); expect(reads).toBe(3);
    expect(new Set(urls).size).toBe(1); const params = new URL(urls[0]).searchParams;
    expect(params.get('project')).toBe(reading.project_id); expect(params.get('expected_revision')).toBe(String(reading.revision));
    expect(params.get('occurred_until')).toBe(reading.occurred_until); expect(params.get('known_until')).toBe(reading.known_until);
    for (const action of ['确认', '驳回', '修改']) await expect(panel.getByRole('button', { name: action, exact: true })).toHaveCount(0);
    const span = question.evidence[0]; const raw = page.waitForResponse(value => new URL(value.url()).pathname === `/api/evidence/${span.event_id}`);
    await panel.locator('.evidence-link').first().click(); const response = await raw; expect(response.status()).toBe(200); const value = await response.json(); capture('history-evidence', value);
    expect(value).toMatchObject({ history_context: true, derived_context_loaded: false, project_id: reading.project_id, revision: reading.revision });
    await expect(page.getByTestId('evidence-quote')).toHaveText(value.event.quote);
    expect(createHash('sha256').update(value.event.quote).digest('hex')).toBe(span.quote_sha256); await readOnly(request, project, graph);
  } finally { await page.unroute(path); }
});

test('关闭后迟到真实200不进入另一记录的详情', async ({ page, request }) => {
  await ignoreTransportCancellation(page); const { project, graph, question, approach } = await fixture(request);
  let release = () => {}; const gate = new Promise<void>(resolve => { release = resolve; });
  let ready = () => {}; const prepared = new Promise<void>(resolve => { ready = resolve; }); const path = `**/api/claims/${question.claim_id}`;
  await page.route(path, async route => { const response = await route.fetch(); expect(response.status()).toBe(200); capture('late-200', await response.json()); ready(); await gate; await route.fulfill({ response }); });
  try {
    await open(page); await page.getByRole('button', { name: '查看记录与证据', exact: true }).click(); await prepared;
    await drawer(page, question).getByRole('button', { name: '关闭详情', exact: true }).click();
    await page.locator('.approach-card').getByRole('button', { name: approach.payload.label!, exact: true }).click();
    const next = drawer(page, approach); await expect(next.getByRole('heading', { name: approach.payload.label!, exact: true })).toBeVisible();
    const returned = page.waitForResponse(value => new URL(value.url()).pathname === `/api/claims/${question.claim_id}`); release(); expect((await returned).status()).toBe(200); await settled(page, question, 200);
    await expect(drawer(page, question)).toHaveCount(0); await expect(next.getByRole('heading', { name: approach.payload.label!, exact: true })).toBeVisible();
    await expect(next.getByRole('heading', { name: question.payload.label!, exact: true })).toHaveCount(0); await readOnly(request, project, graph);
  } finally { release(); await page.unroute(path); }
});

test('关闭后迟到真实401不撤销新项目鉴权或重新打开旧详情', async ({ page, request }) => {
  await ignoreTransportCancellation(page); const { question, emptyProject } = await fixture(request);
  let release = () => {}; const gate = new Promise<void>(resolve => { release = resolve; });
  let ready = () => {}; const prepared = new Promise<void>(resolve => { ready = resolve; }); const path = `**/api/claims/${question.claim_id}`;
  await page.route(path, async route => {
    const response = await route.fetch({ headers: { ...route.request().headers(), Authorization: 'Bearer deliberately-invalid-synthetic-token' } });
    expect(response.status()).toBe(401); capture('late-401', await response.json()); ready(); await gate; await route.fulfill({ response });
  });
  try {
    await open(page); await page.getByRole('button', { name: '查看记录与证据', exact: true }).click(); await prepared;
    await drawer(page, question).getByRole('button', { name: '关闭详情', exact: true }).click();
    await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '合成空项目' }); await expect(page.getByRole('heading', { name: '还没有可查看的研究问题', exact: true })).toBeVisible();
    const returned = page.waitForResponse(value => new URL(value.url()).pathname === `/api/claims/${question.claim_id}`); release(); expect((await returned).status()).toBe(401); await settled(page, question, 401);
    await expect(page.getByRole('button', { name: '进入本地工作区', exact: true })).toHaveCount(0); await expect(page.locator('.detail-drawer')).toHaveCount(0);
    await expect(page.getByLabel('当前项目', { exact: true })).toHaveValue(emptyProject.project_id);
    await expect(page.locator('.breadcrumb')).toContainText('合成空项目');
  } finally { release(); await page.unroute(path); }
});

test('历史条件变化取消旧详情，迟到200不覆盖随后当前详情', async ({ page, request }) => {
  await ignoreTransportCancellation(page); const { project, graph, question } = await fixture(request);
  let release = () => {}; const gate = new Promise<void>(resolve => { release = resolve; });
  let ready = () => {}; const prepared = new Promise<void>(resolve => { ready = resolve; }); const path = `**/api/claims/${question.claim_id}?*`;
  await page.route(path, async route => { const response = await route.fetch(); expect(response.status()).toBe(200); capture('late-history-200', await response.json()); ready(); await gate; await route.fulfill({ response }); });
  try {
    await open(page); await history(page, question); await prepared;
    await page.getByLabel('研究图发生时间截止', { exact: true }).fill('2026-10-09T11:00:00Z'); await expect(page.locator('.detail-drawer')).toHaveCount(0);
    await page.getByRole('button', { name: '研究问题', exact: true }).click(); await page.getByRole('button', { name: '查看记录与证据', exact: true }).click();
    const next = drawer(page, question); await expect(next.getByRole('button', { name: '修改', exact: true })).toBeVisible();
    const returned = page.waitForResponse(value => new URL(value.url()).pathname === `/api/claims/${question.claim_id}` && new URL(value.url()).search !== ''); release(); expect((await returned).status()).toBe(200); await settled(page, question, 200, true);
    await expect(next.getByRole('button', { name: '修改', exact: true })).toBeVisible(); await expect(next).not.toContainText('按图的双时间与修订只读查看'); await readOnly(request, project, graph);
  } finally { release(); await page.unroute(path); }
});
