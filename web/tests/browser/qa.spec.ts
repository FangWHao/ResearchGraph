import { createHash } from 'node:crypto';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { parseQaContext } from '../../src/qa';
import type { QaPacket, QaPreview } from '../../src/qa';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const primary = '验收问答项目'; const secondary = '验收问答项目二';
async function open(page: Page, name = primary) {
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: name });
  await page.getByRole('button', { name: '研究问答', exact: true }).click();
  await expect(page.getByText('服务端模型：synthetic-qa-model', { exact: false })).toBeVisible();
  return page.getByLabel('当前项目', { exact: true }).inputValue();
}
async function permission(request: APIRequestContext, project: string, allowed: boolean) {
  const options = await (await request.get(`/api/qa/options?project=${project}`, { headers })).json();
  if (options.remote_allowed === allowed) return;
  if (!allowed) {
    expect((await request.post('/api/qa/disable-remote', { headers, data: { project_id: project, expected_revision: options.revision } })).status()).toBe(200); return;
  }
  const body = { project_id: project, question: '问答', expected_revision: options.revision };
  const preview = parseQaContext((await (await request.post('/api/qa/preview', { headers, data: body })).json()).context_text) as unknown as QaPreview;
  expect((await request.post('/api/qa/allow-remote', { headers, data: { ...body, preview_sha: preview.preview_sha, allow: true } })).status()).toBe(200);
}
async function answer(page: Page, question: string) {
  await page.getByLabel('研究问答问题', { exact: true }).fill(question);
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/qa/answer');
  await page.getByRole('button', { name: '查找答案', exact: true }).click();
  const received = await response; expect(received.status()).toBe(200);
  return parseQaContext((await received.json()).context_text) as unknown as QaPacket;
}

test('真实本地检索保留候选与独立状态，原文窗口位置/hash准确且不调用模型', async ({ page, request }) => {
  const project = await open(page);
  const before = await (await request.get(`/api/graph?project=${project}`, { headers })).json();
  const modelRequests: string[] = []; page.on('request', r => { if (/\/api\/qa\/(answer|preview|allow-remote)$/.test(new URL(r.url()).pathname)) modelRequests.push(r.url()); });
  await page.getByLabel('研究问答问题', { exact: true }).fill('问答方案甲');
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/qa/retrieve');
  await page.getByRole('button', { name: '仅本地检索', exact: true }).click();
  const received = await response; expect(received.request().postDataJSON()).not.toHaveProperty('scope');
  const packet = parseQaContext((await received.json()).context_text) as unknown as QaPacket;
  await expect(page.getByTestId('qa-source')).toHaveCount(packet.sources.length);
  expect(packet.sources.flatMap(s => s.records).some(r => r.effective_state === 'candidate')).toBe(true);
  for (const details of await page.locator('.qa-source details').all()) await details.locator('summary').click();
  await expect(page.locator('.qa-record .badge.candidate').first()).toHaveText('待复核');
  await expect(page.locator('.qa-state-row').filter({ hasText: '采用状态：拒绝' }).first()).toBeVisible();
  const source = packet.sources[0];
  await page.locator(`#qa-source-${source.citation_id}`).getByRole('button', { name: '打开此处原文', exact: false }).click();
  const quote = page.getByTestId('evidence-quote'); await expect(quote).toHaveText(source.text);
  expect(createHash('sha256').update((await quote.textContent())!).digest('hex')).toBe(source.window_sha256);
  const raw = await (await request.get(`/api/evidence/${source.event_id}?start=${source.window_start}&end=${source.window_end}`, { headers })).json();
  expect(raw.event.quote_sha256).toBe(source.window_sha256);
  expect(modelRequests).toEqual([]);
  const after = await (await request.get(`/api/graph?project=${project}`, { headers })).json();
  expect(after.revision).toBe(before.revision); expect(after.claims).toEqual(before.claims);
});

test('真实首次预览遮盖与项目授权生成，字面脚本安全、引文定位、可撤回且记录未确认', async ({ page, request }) => {
  const project = await open(page); await permission(request, project, false);
  await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  await expect(page.getByText('项目未允许外发', { exact: false })).toBeVisible();
  const before = await (await request.get(`/api/graph?project=${project}`, { headers })).json();
  await page.getByLabel('研究问答问题', { exact: true }).fill('隐私问答');
  await page.getByRole('button', { name: '查找答案', exact: true }).click();
  const consent = page.getByRole('region', { name: '首次外发授权' });
  await expect(consent).toBeVisible(); await expect(consent.getByTestId('qa-preview')).not.toContainText('qa-fixture@example.invalid');
  await expect(page.getByTestId('qa-source-text').filter({ hasText: 'qa-fixture@example.invalid' }).first()).toBeVisible();
  expect(await page.evaluate(() => (window as typeof window & { qaInjected?: boolean }).qaInjected)).toBeUndefined();
  await expect(consent.getByRole('button', { name: '开启项目外发并回答', exact: true })).toBeDisabled();
  await page.screenshot({ path: '../.cache/frontend-qa-preview-desktop.png', fullPage: true });
  await consent.getByRole('checkbox').check();
  const generated = page.waitForResponse(r => new URL(r.url()).pathname === '/api/qa/answer');
  await consent.getByRole('button', { name: '开启项目外发并回答', exact: true }).click();
  const packet = parseQaContext((await (await generated).json()).context_text) as unknown as QaPacket;
  expect(packet.status).toBe('answered'); expect(packet.model_interpretation).toBe(true);
  await expect(page.getByRole('region', { name: '模型解释', exact: true })).toBeVisible();
  const citation = packet.statements![0].citations[0];
  await page.getByRole('button', { name: `定位来源 ${citation.id}`, exact: true }).first().click();
  await expect(page.locator(`#qa-source-${citation.id}`)).toBeFocused();
  await page.screenshot({ path: '../.cache/frontend-qa-answer-desktop.png', fullPage: true });
  const source = packet.sources.find(item => item.citation_id === citation.id)!;
  const rawResponse = page.waitForResponse(r => new URL(r.url()).pathname === `/api/evidence/${source.event_id}`);
  await page.locator(`#qa-source-${citation.id}`).getByRole('button', { name: '打开此处原文', exact: false }).click();
  const rawUrl = new URL((await rawResponse).url()); expect(rawUrl.searchParams.get('start')).toBe(String(source.window_start)); expect(rawUrl.searchParams.get('end')).toBe(String(source.window_end));
  await expect(page.getByTestId('evidence-quote')).toHaveText(source.text); await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  const after = await (await request.get(`/api/graph?project=${project}`, { headers })).json();
  expect(after.claims.map((r: { claim_id: number; effective_state: string }) => [r.claim_id, r.effective_state])).toEqual(before.claims.map((r: { claim_id: number; effective_state: string }) => [r.claim_id, r.effective_state]));
  await page.getByRole('button', { name: '撤回项目外发许可', exact: true }).click();
  await expect(page.getByText('项目外发许可已撤回', { exact: false })).toBeVisible();
  expect((await (await request.get(`/api/qa/options?project=${project}`, { headers })).json()).remote_allowed).toBe(false);
});

test('真实完整范围与双截止筛选，历史未知和零来源不生成；非法时区不发送', async ({ page }) => {
  await open(page); await page.getByText('范围与历史时间（可选）', { exact: true }).click();
  await page.getByLabel('研究问答问题', { exact: true }).fill('问答');
  await page.getByLabel('问答完整范围', { exact: true }).fill('dataset_version=qa_fixture_v1\nanalysis_step=问答验收');
  await page.getByLabel('问答发生截止', { exact: true }).fill('2026-01-02T23:59:59+00:00');
  await page.getByLabel('问答获知截止', { exact: true }).fill('2026-10-10T23:59:59+08:00');
  let response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/qa/retrieve');
  await page.getByRole('button', { name: '仅本地检索', exact: true }).click();
  let packet = parseQaContext((await (await response).json()).context_text) as unknown as QaPacket;
  expect(packet.scope_filter).toBe(true); expect(packet.scope).toEqual({ dataset_version: 'qa_fixture_v1', analysis_step: '问答验收' });
  expect(packet.sources.flatMap(s => s.records).some(r => r.occurred_at?.startsWith('2026-01-03'))).toBe(false);
  await page.getByLabel('问答获知截止', { exact: true }).fill('2025-01-01T00:00:00Z');
  response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/qa/retrieve');
  await page.getByRole('button', { name: '查找答案', exact: true }).click();
  packet = parseQaContext((await (await response).json()).context_text) as unknown as QaPacket;
  expect(packet.sources).toEqual([]); await expect(page.getByText('没有找到有效来源', { exact: false })).toBeVisible();
  await page.getByLabel('问答发生截止', { exact: true }).fill('2026-01-02T23:59');
  let calls = 0; page.on('request', r => { if (new URL(r.url()).pathname === '/api/qa/retrieve') calls++; });
  await page.getByRole('button', { name: '查找答案', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('带时区'); expect(calls).toBe(0);
});

test('真实409版本冲突保留问题，刷新须主动重新查询并重看预览，400拒绝额外字段', async ({ page, request }) => {
  const project = await open(page); await permission(request, project, false); await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  await page.getByLabel('研究问答问题', { exact: true }).fill('问答'); await page.getByRole('button', { name: '查找答案', exact: true }).click();
  const consent = page.getByRole('region', { name: '首次外发授权' }); await expect(consent).toBeVisible();
  const options = await (await request.get(`/api/qa/options?project=${project}`, { headers })).json();
  expect((await request.post('/api/records/question', { headers, data: { project_id: project, text: '合成并发版本变更', scope: null, actor: 'human:合成验收', expected_revision: options.revision, request_id: crypto.randomUUID() } })).status()).toBe(200);
  await consent.getByRole('checkbox').check(); const failed = page.waitForResponse(r => new URL(r.url()).pathname === '/api/qa/allow-remote');
  await consent.getByRole('button', { name: '开启项目外发并回答', exact: true }).click(); expect((await failed).status()).toBe(409);
  await expect(page.getByRole('alert')).toContainText('不会自动重发');
  let calls = 0; page.on('request', r => { if (['/api/qa/retrieve', '/api/qa/allow-remote', '/api/qa/answer'].includes(new URL(r.url()).pathname)) calls++; });
  await page.getByRole('button', { name: '刷新问答状态', exact: true }).click(); await expect(consent).toHaveCount(0);
  await expect(page.getByLabel('研究问答问题', { exact: true })).toHaveValue('问答'); expect(calls).toBe(0);
  await page.getByRole('button', { name: '查找答案', exact: true }).click(); await expect(consent).toBeVisible(); await expect(consent.getByRole('checkbox')).not.toBeChecked();
  expect((await request.post('/api/qa/retrieve', { headers, data: { project_id: project, question: '问答', model: '不能由浏览器配置' } })).status()).toBe(400);
});

test('真实模型坏引用、计数失败与截断作废保留来源，不把不可用提升为答案', async ({ page, request }) => {
  const project = await open(page); await permission(request, project, true); await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  for (const text of ['问答 模拟问答失败', '问答 模拟计数不可用', '问答 模拟截断']) {
    const packet = await answer(page, text); expect(packet.status).toBe('unavailable'); expect(packet.statements).toEqual([]); expect(packet.sources.length).toBeGreaterThan(0);
    await expect(page.getByRole('region', { name: '模型解释', exact: true })).toContainText('模型暂时无法回答');
    await expect(page.getByTestId('qa-source')).toHaveCount(packet.sources.length);
  }
});

test('真实401恢复既有登录且保留问答草稿，不自动重发', async ({ page }) => {
  await open(page); const text = '问答 登录恢复仍保留'; await page.getByLabel('研究问答问题', { exact: true }).fill(text);
  await page.route('**/api/qa/retrieve', async route => { const response = await route.fetch({ headers: { ...route.request().headers(), Authorization: 'Bearer invalid-synthetic-token' } }); expect(response.status()).toBe(401); await route.fulfill({ response }); });
  await page.getByRole('button', { name: '仅本地检索', exact: true }).click(); await expect(page.getByRole('heading', { name: '打开你的研究决定史' })).toBeVisible();
  await page.unroute('**/api/qa/retrieve'); await page.getByLabel('访问令牌', { exact: true }).fill('synthetic-browser-token'); await page.getByRole('button', { name: '进入本地工作区', exact: false }).click();
  await expect(page.getByLabel('研究问答问题', { exact: true })).toHaveValue(text); await expect(page.getByTestId('qa-source')).toHaveCount(0);
});

test('迟到真实回答不能覆盖修改或其他项目的新草稿，停止等待不宣称远端停止', async ({ page, request }) => {
  const project = await open(page); await permission(request, project, true); await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  let ready = false; let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/qa/answer', async route => { const response = await route.fetch(); ready = true; await held; try { await route.fulfill({ response }); } catch { /* Page cancellation may dispose the route. */ } });
  await page.getByLabel('研究问答问题', { exact: true }).fill('问答 旧意图'); await page.getByRole('button', { name: '查找答案', exact: true }).click(); await expect.poll(() => ready).toBe(true);
  await page.getByLabel('研究问答问题', { exact: true }).fill('问答 新意图');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: secondary }); await page.getByLabel('研究问答问题', { exact: true }).fill('问答 次项目新草稿');
  release(); await expect(page.getByLabel('研究问答问题', { exact: true })).toHaveValue('问答 次项目新草稿'); await expect(page.getByRole('region', { name: '模型解释' })).toHaveCount(0);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary }); await expect(page.getByLabel('研究问答问题', { exact: true })).toHaveValue('问答 新意图'); await expect(page.getByText('下方结果来自此前输入', { exact: false })).toBeVisible();
  await expect(page.getByText('已发送的模型请求可能仍在服务端处理', { exact: false })).toBeVisible();
});

test('390小屏实际预览与回答可读，未配置模拟只作本地检索且不清来源', async ({ page, request }) => {
  const project = await open(page); await permission(request, project, false); await page.getByRole('button', { name: '刷新数据', exact: true }).click(); await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel('研究问答问题', { exact: true }).fill('问答'); await page.getByRole('button', { name: '查找答案', exact: true }).click();
  const consent = page.getByRole('region', { name: '首次外发授权' }); await expect(consent).toBeVisible(); await consent.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.screenshot({ path: '../.cache/frontend-qa-preview-mobile.png', fullPage: true });
  await consent.getByRole('checkbox').check(); await consent.getByRole('button', { name: '开启项目外发并回答', exact: true }).click();
  const explanation = page.getByRole('region', { name: '模型解释', exact: true }); await expect(explanation).toBeVisible(); await explanation.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.screenshot({ path: '../.cache/frontend-qa-answer-mobile.png', fullPage: true });
  await page.route('**/api/qa/options?**', async route => { const response = await route.fetch(); await route.fulfill({ json: { ...await response.json(), configured: false, model: null, remote: null } }); });
  await page.getByRole('button', { name: '刷新数据', exact: true }).click(); await expect(page.getByText('尚未配置模型，仍可本地检索。', { exact: false })).toBeVisible();
  let generations = 0; page.on('request', r => { if (new URL(r.url()).pathname === '/api/qa/answer') generations++; });
  await page.getByRole('button', { name: '查找答案', exact: true }).click(); await expect(page.getByText('尚未配置模型，已显示本地检索来源。', { exact: true })).toBeVisible(); expect(generations).toBe(0); await expect(page.getByTestId('qa-source').first()).toBeVisible();
});

test('模拟损坏回答的记录格式不能清掉真实本地来源或使页面崩溃', async ({ page, request }) => {
  const project = await open(page); await permission(request, project, true); await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  await page.route('**/api/qa/answer', async route => {
    const response = await route.fetch(); const json = await response.json(); const packet = parseQaContext(json.context_text);
    (packet.sources as { records: unknown[] }[])[0].records = [null];
    await route.fulfill({ json: { context_text: `<rg-context v="1" id="ctx_0123456789abcdef0123">\n${JSON.stringify(packet)}\n</rg-context>` } });
  });
  await page.getByLabel('研究问答问题', { exact: true }).fill('问答'); await page.getByRole('button', { name: '查找答案', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('关联记录格式无效'); await expect(page.getByTestId('qa-source').first()).toBeVisible();
  await expect(page.getByRole('region', { name: '模型解释' })).toHaveCount(0); await expect(page.getByLabel('研究问答问题', { exact: true })).toHaveValue('问答');
});

test('同项目迟到真实回答不能覆盖问题和范围已变的新意图', async ({ page, request }) => {
  const project = await open(page); await permission(request, project, true); await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  let ready = false; let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/qa/answer', async route => { const response = await route.fetch(); ready = true; await held; await route.fulfill({ response }); });
  await page.getByLabel('研究问答问题', { exact: true }).fill('问答 旧问题'); await page.getByRole('button', { name: '查找答案', exact: true }).click(); await expect.poll(() => ready).toBe(true);
  await page.getByLabel('研究问答问题', { exact: true }).fill('问答 新问题'); await page.getByText('范围与历史时间（可选）', { exact: true }).click(); await page.getByLabel('问答完整范围', { exact: true }).fill('dataset_version=qa_fixture_v2\nanalysis_step=问答验收');
  release(); await expect(page.getByRole('button', { name: '查找答案', exact: true })).toBeEnabled();
  await expect(page.getByLabel('研究问答问题', { exact: true })).toHaveValue('问答 新问题'); await expect(page.getByLabel('问答完整范围', { exact: true })).toHaveValue('dataset_version=qa_fixture_v2\nanalysis_step=问答验收');
  await expect(page.getByRole('region', { name: '模型解释' })).toHaveCount(0); await expect(page.getByText('下方结果来自此前输入', { exact: false })).toBeVisible();
});
