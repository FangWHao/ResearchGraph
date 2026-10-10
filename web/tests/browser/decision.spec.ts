import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import type { Claim, DecisionRequest, DecisionResult, DecisionTargetsPage, GraphData, Project, ResolveDecisionRequest, ResolveDecisionResult } from '../../src/types';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const primary = '验收新增决定项目', secondary = '验收新增决定项目二';
const scope = { data: 'synthetic_decide_v1', step: '手工决定验收' };
const scopeText = 'data=synthetic_decide_v1\nstep=手工决定验收';
const unique = '合成待确认决定方案', duplicate = '合成同名决定方案';
const createURL = (url: string) => url.endsWith('/api/records/decide');

async function project(request: APIRequestContext, name = primary): Promise<Project> {
  return (await (await request.get('/api/projects', { headers })).json()).projects.find((p: Project) => p.name === name);
}
async function graph(request: APIRequestContext, id: string): Promise<GraphData> { return (await request.get(`/api/graph?project=${id}`, { headers })).json(); }
async function claim(request: APIRequestContext, id: number): Promise<Claim> { return (await (await request.get(`/api/claims/${id}`, { headers })).json()).claim; }
async function pending(request: APIRequestContext, selector: string, selectedScope: Record<string, string> | null = scope): Promise<DecisionResult> {
  const p = await project(request);
  const response = await request.post('/api/records/decide', { headers, data: { project_id: p.project_id, selector, action: 'accept', why: `合成待确定对象：${selector}`, scope: selectedScope, actor: 'human:合成记录者', request_id: crypto.randomUUID(), expected_revision: (await graph(request, p.project_id)).revision } });
  expect(response.status()).toBe(200); const result = await response.json(); expect(result.target_id).toBeNull(); return result;
}
async function open(page: Page, name = primary) {
  await page.goto('/?view=timeline#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: name });
  await page.getByRole('button', { name: '记录人工决定', exact: true }).click();
  return page.getByRole('dialog', { name: '记录人工决定', exact: true });
}
async function fill(dialog: Locator, selector: string, why: string, range = scopeText) {
  await dialog.getByLabel('决定对象原话', { exact: true }).fill(selector);
  await dialog.getByLabel('人工决定动作', { exact: true }).selectOption('accept');
  await dialog.getByLabel('人工决定理由', { exact: true }).fill(why);
  await dialog.getByLabel('人工决定范围', { exact: true }).fill(range);
}
async function resolveDialog(page: Page, id: number) {
  await page.goto('/?view=timeline#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  const item = page.locator('.pending-decisions article').filter({ hasText: `查看原记录 #${id}` });
  await item.getByRole('button', { name: '确定对象', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '确定决定对象', exact: true });
  await expect(dialog.getByRole('radio').first()).toBeVisible(); return dialog;
}

test('真实人工决定确认事件而不确认目标候选，原话、图与引用完整', async ({ page, request }) => {
  const dialog = await open(page); const why = '  合成决定理由\n保留“引号”和\\路径。  ';
  await fill(dialog, unique, why);
  await page.screenshot({ path: '../.cache/frontend-manual-decide-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: '../.cache/frontend-manual-decide-create-mobile.png' });
  expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const responsePromise = page.waitForResponse(r => createURL(r.url()));
  await dialog.getByRole('button', { name: '保存人工决定', exact: true }).click();
  const response = await responsePromise; expect(response.status()).toBe(200);
  const body: DecisionRequest = response.request().postDataJSON(), saved: DecisionResult = await response.json();
  expect(body).toMatchObject({ selector: unique, why, scope, actor: 'human:本机用户' });
  expect(saved).toMatchObject({ effective_state: 'confirmed', replayed: false });
  const event = await claim(request, saved.claim_id);
  expect(event).toMatchObject({ basis: 'manual', effective_state: 'confirmed', confirmation_source: 'human', payload: { target: saved.target_id, action: 'accepted', reason: why, referent_unique: true }, scope });
  const data = await graph(request, body.project_id);
  expect(data.claims.find(c => c.entity_id === saved.target_id && c.claim_type === 'entity_version')?.effective_state).toBe('candidate');
  const detail = page.getByRole('dialog', { name: `记录 ${saved.claim_id} 详情`, exact: true });
  await expect(detail).toContainText('人工确认');
  await expect(page.locator('.timeline-list')).toContainText(why.trim());
  let recoveredWhy = false; let selectorPosition = '';
  for (const span of event.evidence) {
    const raw = (await (await request.get(`/api/evidence/${span.event_id}?context=0&start=${span.byte_start}&end=${span.byte_end}`, { headers })).json()).event;
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw.quote));
    expect(Array.from(new Uint8Array(digest), v => v.toString(16).padStart(2, '0')).join('')).toBe(span.quote_sha256);
    if (raw.quote === JSON.stringify(why).slice(1, -1)) recoveredWhy = true;
    if (raw.quote === unique) selectorPosition = `${span.byte_start}–${span.byte_end}`;
  }
  expect(recoveredWhy).toBe(true);
  await detail.locator('.evidence-link').filter({ hasText: selectorPosition }).click();
  await expect(page.getByTestId('evidence-quote')).toHaveText(unique);
});

test('同名候选禁普通确认、修改和整组确认，显式选择后追加原命令替代版', async ({ page, request }) => {
  const dialog = await open(page); await fill(dialog, duplicate, '合成同名对象必须主动选择。');
  const responsePromise = page.waitForResponse(r => createURL(r.url()));
  await dialog.getByRole('button', { name: '保存人工决定', exact: true }).click();
  const saved: DecisionResult = await (await responsePromise).json(); expect(saved.target_id).toBeNull();
  const original = await claim(request, saved.claim_id);
  expect(original.pending_decision).toMatchObject({ selector: duplicate, requires_resolution: true, action: 'accepted', resolved_claim_id: null });
  expect(original.payload.target).toBeNull();
  const detail = page.getByRole('dialog', { name: `记录 ${saved.claim_id} 详情`, exact: true });
  await expect(detail.getByRole('button', { name: '确认', exact: true })).toBeDisabled();
  await expect(detail.getByRole('button', { name: '修改', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  await page.getByRole('button', { name: /复核队列/ }).click();
  const item = page.locator('.queue-item').filter({ hasText: `#${saved.claim_id}` });
  await expect(page.locator('.queue-item').first()).toBeVisible();
  for (let offset = 50; !await item.count() && offset <= 250; offset += 50) {
    const nextPromise = page.waitForResponse(r => new URL(r.url()).pathname === '/api/claims' && new URL(r.url()).searchParams.get('offset') === String(offset));
    await page.getByRole('button', { name: '下一页', exact: true }).click();
    const next = await (await nextPromise).json();
    await expect(page.locator('.queue-item').first()).toContainText(`#${next.claims[0].claim_id}`);
  }
  await item.click();
  await expect(page.getByRole('button', { name: /确认 C/ })).toBeDisabled();
  await expect(item.locator('xpath=..').getByRole('button', { name: '确认整个片段的待复核记录' })).toBeDisabled();
  const p = await project(request), revision = (await graph(request, p.project_id)).revision;
  const denied = await request.post('/api/review', { headers, data: { claim_ids: [saved.claim_id], action: 'confirm', actor: 'human:合成验证', expected_revision: revision } }); expect(denied.status()).toBe(400);
  await page.locator('.review-actions').getByRole('button', { name: '确定对象', exact: true }).click();
  const resolve = page.getByRole('dialog', { name: '确定决定对象', exact: true });
  await resolve.getByLabel('查找决定对象', { exact: true }).fill(duplicate);
  await expect(resolve.getByRole('radio')).toHaveCount(2);
  await expect(resolve.getByRole('radio', { checked: true })).toHaveCount(0);
  await expect(resolve.getByRole('button', { name: '确认选择的对象', exact: true })).toBeDisabled();
  await resolve.getByRole('radio').last().check();
  const targetId = await resolve.locator('.decision-target').last().getAttribute('data-target-id');
  const resultPromise = page.waitForResponse(r => r.url().endsWith(`/api/records/decisions/${saved.claim_id}/resolve`));
  await resolve.getByRole('button', { name: '确认选择的对象', exact: true }).click();
  const response = await resultPromise; expect(response.status()).toBe(200); const result: ResolveDecisionResult = await response.json();
  expect(Object.keys(response.request().postDataJSON()).sort()).toEqual(['actor', 'expected_revision', 'request_id', 'target_id']);
  expect(result.request_id).not.toBe(saved.request_id);
  const replacement = await claim(request, result.claim_id), previous = await claim(request, saved.claim_id);
  expect(replacement).toMatchObject({ replaces_claim: saved.claim_id, effective_state: 'confirmed', occurred_at: original.occurred_at, scope: original.scope, payload: { action: original.payload.action, reason: original.payload.reason, target: targetId, referent_unique: true } });
  expect(replacement.evidence.map(e => e.event_id)).toContain(saved.event_id);
  expect(previous.payload).toEqual(original.payload); expect(previous.replacement_ids).toContain(result.claim_id);
  expect(previous.pending_decision?.resolved_claim_id).toBe(result.claim_id);
  expect((await graph(request, p.project_id)).claims.find(c => c.entity_id === targetId && c.claim_type === 'entity_version')?.effective_state).toBe('candidate');
});

test('全项目对象分页与字面查询不自动选中，空范围选定对象后仍未知，小屏可用', async ({ page, request }) => {
  const saved = await pending(request, '合成空范围对象待定', null);
  const dialog = await resolveDialog(page, saved.claim_id);
  await expect(dialog.getByRole('radio')).toHaveCount(200);
  await expect(dialog.getByRole('radio', { checked: true })).toHaveCount(0);
  await dialog.getByRole('button', { name: '下一页对象', exact: true }).click();
  await expect(dialog.getByRole('radio')).toHaveCount(9);
  await expect(dialog.getByRole('radio', { checked: true })).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: '下一页对象', exact: true })).toBeDisabled();
  await dialog.getByLabel('查找决定对象', { exact: true }).fill('合成分页对象 205');
  await expect(dialog.getByRole('radio')).toHaveCount(1);
  await dialog.getByRole('radio').check();
  await page.setViewportSize({ width: 390, height: 844 });
  await dialog.getByRole('button', { name: '确认选择的对象', exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-manual-decide-resolve-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  const responsePromise = page.waitForResponse(r => r.url().endsWith(`/api/records/decisions/${saved.claim_id}/resolve`));
  await dialog.getByRole('button', { name: '确认选择的对象', exact: true }).click();
  const result: ResolveDecisionResult = await (await responsePromise).json();
  const resolved = await claim(request, result.claim_id); expect(resolved.scope).toBeNull();
  await expect(page.getByRole('dialog', { name: `记录 ${result.claim_id} 详情`, exact: true })).toContainText('范围未知');
  await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  await page.locator('.timeline-view .filter-bar select').first().selectOption(resolved.payload.target!);
  await expect(page.locator('.timeline-list')).toContainText('范围未知，不判断当前采用');
  expect(await page.locator('.timeline-view .filter-bar select').first().locator('option').evaluateAll(options => options.every(option => (option as HTMLOptionElement).value !== 'null'))).toBe(true);
});

test('实际400保留输入，已提交丢回包重试幂等且防双击', async ({ page, request }) => {
  const bodies: DecisionRequest[] = [], receipts: DecisionResult[] = [];
  let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/records/decide', async route => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 1) { const response = await route.fetch({ postData: JSON.stringify({ ...bodies[0], action: 'invalid' }) }); expect(response.status()).toBe(400); await route.fulfill({ response }); }
    else { const response = await route.fetch(); if (bodies.length === 2) { receipts.push(await response.json()); await held; await route.abort('failed'); } else await route.fulfill({ response }); }
  });
  const dialog = await open(page); const why = '合成真实400后同意图重试'; await fill(dialog, unique, why);
  await dialog.getByRole('button', { name: '保存人工决定', exact: true }).click();
  await expect(dialog.getByRole('alert')).toBeVisible(); await expect(dialog.getByLabel('人工决定理由')).toHaveValue(why);
  await dialog.getByRole('button', { name: '保存人工决定', exact: true }).click();
  await expect.poll(() => receipts.length).toBe(1); await dialog.locator('form').dispatchEvent('submit'); expect(bodies).toHaveLength(2);
  release(); await expect(dialog.getByRole('alert')).toBeVisible();
  const responsePromise = page.waitForResponse(r => createURL(r.url()));
  await dialog.getByRole('button', { name: '保存人工决定', exact: true }).click();
  const replay: DecisionResult = await (await responsePromise).json(); expect(replay).toMatchObject({ replayed: true, claim_id: receipts[0].claim_id });
  expect(new Set(bodies.map(body => body.request_id)).size).toBe(1);
  expect((await graph(request, bodies[0].project_id)).claims.filter(c => c.payload.reason === why)).toHaveLength(1);
});

test('真实409后人工刷新重试复用编号，401鉴权保留草稿且不自动写入', async ({ page, request }) => {
  const bodies: DecisionRequest[] = []; let deny = false;
  await page.route('**/api/records/decide', async route => { bodies.push(route.request().postDataJSON()); if (deny) { deny = false; const response = await route.fetch({ headers: { ...route.request().headers(), authorization: 'Bearer synthetic-invalid-token' } }); expect(response.status()).toBe(401); await route.fulfill({ response }); } else await route.continue(); });
  let dialog = await open(page); await fill(dialog, unique, '合成409与401保留决定意图');
  await pending(request, '合成另一窗口制造版本变化');
  const rejectedPromise = page.waitForResponse(r => createURL(r.url()));
  await dialog.getByRole('button', { name: '保存人工决定', exact: true }).click(); expect((await rejectedPromise).status()).toBe(409);
  await expect(dialog.getByRole('button', { name: '保存人工决定', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: '读取最新版本', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '保存人工决定', exact: true })).toBeEnabled(); expect(bodies).toHaveLength(1);
  deny = true; await dialog.getByRole('button', { name: '保存人工决定', exact: true }).click();
  await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toBeVisible();
  await page.getByLabel('访问令牌').fill('synthetic-browser-token'); await page.getByRole('button', { name: '进入本地工作区', exact: false }).click();
  dialog = page.getByRole('dialog', { name: '记录人工决定', exact: true });
  await expect(dialog.getByLabel('人工决定理由')).toHaveValue('合成409与401保留决定意图');
  await expect(dialog.getByRole('button', { name: '保存人工决定', exact: true })).toBeEnabled(); expect(bodies).toHaveLength(2);
  const responsePromise = page.waitForResponse(r => createURL(r.url()));
  await dialog.getByRole('button', { name: '保存人工决定', exact: true }).click(); expect((await responsePromise).status()).toBe(200);
  expect(new Set(bodies.map(body => body.request_id)).size).toBe(1);
  expect(bodies[2].expected_revision).toBeGreaterThan(bodies[0].expected_revision);
});

test('对象复核409和响应丢失保持同选择意图，不重复追加替代记录', async ({ page, request }) => {
  const saved = await pending(request, '合成复核重试对象'); const dialog = await resolveDialog(page, saved.claim_id);
  await dialog.getByLabel('查找决定对象').fill(unique); await expect(dialog.getByRole('radio')).toHaveCount(1); await dialog.getByRole('radio').check();
  const bodies: ResolveDecisionRequest[] = [], receipts: ResolveDecisionResult[] = [];
  await page.route(`**/api/records/decisions/${saved.claim_id}/resolve`, async route => { bodies.push(route.request().postDataJSON()); const response = await route.fetch(); if (bodies.length === 2) { receipts.push(await response.json()); await route.abort('failed'); } else await route.fulfill({ response }); });
  await pending(request, '合成复核另一窗口');
  const rejectedPromise = page.waitForResponse(r => r.url().endsWith(`/api/records/decisions/${saved.claim_id}/resolve`));
  await dialog.getByRole('button', { name: '确认选择的对象', exact: true }).click(); expect((await rejectedPromise).status()).toBe(409);
  await dialog.getByRole('button', { name: '读取最新版本', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '确认选择的对象', exact: true })).toBeEnabled(); expect(bodies).toHaveLength(1);
  await dialog.getByRole('button', { name: '确认选择的对象', exact: true }).click(); await expect(dialog.getByRole('alert')).toBeVisible(); expect(receipts).toHaveLength(1);
  const replayPromise = page.waitForResponse(r => r.url().endsWith(`/api/records/decisions/${saved.claim_id}/resolve`));
  await dialog.getByRole('button', { name: '确认选择的对象', exact: true }).click();
  const replay: ResolveDecisionResult = await (await replayPromise).json(); expect(replay).toMatchObject({ replayed: true, claim_id: receipts[0].claim_id });
  expect(new Set(bodies.map(body => body.request_id)).size).toBe(1);
  expect((await claim(request, saved.claim_id)).replacement_ids).toEqual([replay.claim_id]);
});

test('旧创建成功延迟到达不能清掉新决定草稿或归属到另一项目', async ({ page, request }) => {
  const bodies: DecisionRequest[] = [], old: DecisionResult[] = [];
  let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/records/decide', async route => { bodies.push(route.request().postDataJSON()); const response = await route.fetch(); if (bodies.length === 1) { old.push(await response.json()); await held; } await route.fulfill({ response }); });
  let dialog = await open(page); await fill(dialog, unique, '合成旧请求延迟');
  const oldResponse = page.waitForResponse(r => createURL(r.url())); await dialog.getByRole('button', { name: '保存人工决定', exact: true }).click();
  await expect.poll(() => old.length).toBe(1); await page.keyboard.press('Escape');
  await page.getByLabel('当前项目').selectOption({ label: secondary }); await page.getByRole('button', { name: '记录人工决定', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '记录人工决定', exact: true }); await fill(dialog, '合成第二项目方案', '合成其他项目草稿');
  await page.keyboard.press('Escape'); await page.getByLabel('当前项目').selectOption({ label: primary });
  await page.getByRole('button', { name: '记录人工决定', exact: true }).click(); dialog = page.getByRole('dialog', { name: '记录人工决定', exact: true });
  await dialog.getByLabel('人工决定理由').fill('合成新意图必须保留'); release(); expect((await oldResponse).status()).toBe(200);
  await expect(dialog.getByLabel('人工决定理由')).toHaveValue('合成新意图必须保留');
  await expect(page.getByRole('dialog', { name: `记录 ${old[0].claim_id} 详情`, exact: true })).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: '保存人工决定', exact: true })).toBeEnabled();
  const responsePromise = page.waitForResponse(r => createURL(r.url())); await dialog.getByRole('button', { name: '保存人工决定', exact: true }).click(); expect((await responsePromise).status()).toBe(200);
  expect(bodies[1].request_id).not.toBe(bodies[0].request_id);
  await page.getByRole('button', { name: '关闭详情', exact: true }).click(); await page.getByLabel('当前项目').selectOption({ label: secondary });
  await page.getByRole('button', { name: '记录人工决定', exact: true }).click(); dialog = page.getByRole('dialog', { name: '记录人工决定', exact: true });
  await expect(dialog.getByLabel('人工决定理由')).toHaveValue('合成其他项目草稿');
  const p = await project(request, secondary); expect((await graph(request, p.project_id)).claims.some(c => c.payload.reason === '合成新意图必须保留')).toBe(false);
  const otherResponse = page.waitForResponse(r => createURL(r.url()));
  await dialog.getByRole('button', { name: '保存人工决定', exact: true }).click();
  const other = await otherResponse; expect(other.status()).toBe(200); const otherSaved: DecisionResult = await other.json();
  expect(bodies[2].project_id).toBe(p.project_id); expect(bodies[2].project_id).not.toBe(bodies[0].project_id);
  expect((await claim(request, otherSaved.claim_id)).payload.reason).toBe('合成其他项目草稿');
  expect((await graph(request, bodies[0].project_id)).claims.some(c => c.claim_id === otherSaved.claim_id)).toBe(false);
});

test('旧确定对象回包延迟时不能打开另一个记录或清掉它的新选择', async ({ page, request }) => {
  const first = await pending(request, '合成旧确定对象请求'), second = await pending(request, '合成新确定对象请求');
  let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; }); const receipts: ResolveDecisionResult[] = [];
  await page.route(`**/api/records/decisions/${first.claim_id}/resolve`, async route => { const response = await route.fetch(); receipts.push(await response.json()); await held; await route.fulfill({ response }); });
  let dialog = await resolveDialog(page, first.claim_id); await dialog.getByLabel('查找决定对象').fill(unique); await expect(dialog.getByRole('radio')).toHaveCount(1); await dialog.getByRole('radio').check();
  const oldResponse = page.waitForResponse(r => r.url().endsWith(`/api/records/decisions/${first.claim_id}/resolve`));
  await dialog.getByRole('button', { name: '确认选择的对象', exact: true }).click(); await expect.poll(() => receipts.length).toBe(1); await page.keyboard.press('Escape');
  await page.locator('.pending-decisions article').filter({ hasText: `查看原记录 #${second.claim_id}` }).getByRole('button', { name: '确定对象', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '确定决定对象', exact: true }); await dialog.getByLabel('查找决定对象').fill('合成分页对象 204');
  await expect(dialog.getByRole('radio')).toHaveCount(1); await dialog.getByRole('radio').check(); const selectedId = await dialog.locator('.decision-target').getAttribute('data-target-id');
  release(); expect((await oldResponse).status()).toBe(200); await expect(dialog.getByRole('radio', { checked: true })).toHaveCount(1);
  await expect(dialog.getByLabel('查找决定对象')).toHaveValue('合成分页对象 204');
  await expect(page.getByRole('dialog', { name: `记录 ${receipts[0].claim_id} 详情`, exact: true })).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: '确认选择的对象', exact: true })).toBeEnabled();
  const responsePromise = page.waitForResponse(r => r.url().endsWith(`/api/records/decisions/${second.claim_id}/resolve`));
  await dialog.getByRole('button', { name: '确认选择的对象', exact: true }).click(); const result: ResolveDecisionResult = await (await responsePromise).json();
  expect((await claim(request, result.claim_id)).payload.target).toBe(selectedId); expect(result.original_claim_id).toBe(second.claim_id);
});

test('目标查询是字面量且标签截断不冒充完整，驳回候选不能再确定对象', async ({ page, request }) => {
  const saved = await pending(request, '合成截断名称复核');
  await page.route('**/api/decision-targets?**', async route => {
    const response = await route.fetch(); const data: DecisionTargetsPage = await response.json();
    if (new URL(route.request().url()).searchParams.get('q') === unique) data.targets = data.targets.map(t => ({ ...t, label: `${t.label}（合成长名称预览）`, label_truncated: true, label_total_bytes: 12000 }));
    await route.fulfill({ response, json: data });
  });
  const dialog = await resolveDialog(page, saved.claim_id); await dialog.getByLabel('查找决定对象').fill(unique);
  await expect(dialog.getByRole('radio')).toHaveCount(1); await expect(dialog).toContainText('当前仅为名称预览');
  await page.screenshot({ path: '../.cache/frontend-manual-decide-resolve-desktop.png' });
  await dialog.getByLabel('查找决定对象').fill('* OR "合成"'); await expect(dialog.getByRole('radio')).toHaveCount(0);
  const p = await project(request); const rev = (await graph(request, p.project_id)).revision;
  expect((await request.post('/api/review', { headers, data: { claim_ids: [saved.claim_id], action: 'dismiss', actor: 'human:合成复核', expected_revision: rev } })).status()).toBe(200);
  await dialog.getByLabel('查找决定对象').fill(unique);
  await dialog.getByRole('button', { name: '读取最新版本', exact: true }).click(); await expect(dialog).toContainText('不能继续提交');
  await expect(dialog.getByRole('button', { name: '确认选择的对象', exact: true })).toHaveCount(0);
});

test('对象复核实际401恢复后保留选择与编号，人工重试归属原记录', async ({ page, request }) => {
  const saved = await pending(request, '合成对象复核鉴权恢复'); let dialog = await resolveDialog(page, saved.claim_id);
  await dialog.getByLabel('查找决定对象').fill(unique); await expect(dialog.getByRole('radio')).toHaveCount(1); await dialog.getByRole('radio').check();
  const selected = await dialog.locator('.decision-target').getAttribute('data-target-id'); const bodies: ResolveDecisionRequest[] = [];
  await page.route(`**/api/records/decisions/${saved.claim_id}/resolve`, async route => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 1) { const response = await route.fetch({ headers: { ...route.request().headers(), authorization: 'Bearer synthetic-invalid-token' } }); expect(response.status()).toBe(401); await route.fulfill({ response }); }
    else await route.continue();
  });
  await dialog.getByRole('button', { name: '确认选择的对象', exact: true }).click();
  await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toBeVisible();
  await page.getByLabel('访问令牌').fill('synthetic-browser-token'); await page.getByRole('button', { name: '进入本地工作区', exact: false }).click();
  dialog = page.getByRole('dialog', { name: '确定决定对象', exact: true });
  await expect(dialog.getByRole('radio', { checked: true })).toHaveCount(1); expect(bodies).toHaveLength(1);
  const responsePromise = page.waitForResponse(r => r.url().endsWith(`/api/records/decisions/${saved.claim_id}/resolve`));
  await dialog.getByRole('button', { name: '确认选择的对象', exact: true }).click(); const response = await responsePromise; expect(response.status()).toBe(200);
  const result: ResolveDecisionResult = await response.json(); expect(result.original_claim_id).toBe(saved.claim_id); expect((await claim(request, result.claim_id)).payload.target).toBe(selected);
  expect(bodies[1].request_id).toBe(bodies[0].request_id);
});

test('旧请求实际401迟到不能登出另一项目或污染新的人工草稿', async ({ page }) => {
  let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; }); let received = false;
  await page.route('**/api/records/decide', async route => { const response = await route.fetch({ headers: { ...route.request().headers(), authorization: 'Bearer synthetic-invalid-token' } }); expect(response.status()).toBe(401); received = true; await held; await route.fulfill({ response }); });
  let dialog = await open(page); await fill(dialog, unique, '合成旧鉴权失败请求');
  const responsePromise = page.waitForResponse(r => createURL(r.url())); await dialog.getByRole('button', { name: '保存人工决定', exact: true }).click();
  await expect.poll(() => received).toBe(true); await page.keyboard.press('Escape'); await page.getByLabel('当前项目').selectOption({ label: secondary });
  await page.getByRole('button', { name: '记录人工决定', exact: true }).click(); dialog = page.getByRole('dialog', { name: '记录人工决定', exact: true });
  await fill(dialog, '合成第二项目方案', '合成另一项目的新草稿'); release(); expect((await responsePromise).status()).toBe(401);
  await expect(dialog.getByLabel('人工决定理由')).toHaveValue('合成另一项目的新草稿');
  await expect(dialog.getByRole('button', { name: '保存人工决定', exact: true })).toBeEnabled();
  await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toHaveCount(0);
  await expect(dialog.getByRole('alert')).toHaveCount(0);
});
