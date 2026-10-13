import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWriteStream, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const origin = 'http://127.0.0.1:9806';
const headers = { Authorization: 'Bearer synthetic-rule-qualification-token' };
type Name = 'pending_newer' | 'pending_older' | 'verified' | 'invalid' | 'unverifiable' | 'unavailable' | 'human_override';
interface Identity {
  project_id: string; entity_id: string; definition_id: number; claim_id: number;
  rule_review_id: number; manual_claim_id: number; scope: Record<string, string>;
  span: { event_id: number; byte_start: number; byte_end: number; quote: string }; quote_sha256: string;
  finding?: { entity_id: string; definition_id: number; claim_id: number; rule_review_id: number };
}
let server: ChildProcess | undefined;
let cases: Record<Name, Identity>;
let captured = 0;
const expected = {
  pending_newer: { qualification: 'pending', adoption: 'unknown', display: '未知' },
  pending_older: { qualification: 'pending', adoption: 'accepted', display: '采用' },
  verified: { qualification: 'verified', adoption: 'withdrawn', display: '撤回' },
  invalid: { qualification: 'invalid', adoption: 'accepted', display: '采用' },
  unverifiable: { qualification: 'unverifiable', adoption: 'unknown', display: '未知' },
  unavailable: { qualification: 'unavailable', adoption: 'unknown', display: '未知' },
  human_override: { qualification: 'not_required', adoption: 'withdrawn', display: '撤回' },
} as const;
function capture(tag: string, data: unknown) {
  const directory = `${root}/.cache/frontend-rule-qualification-api`;
  mkdirSync(directory, { recursive: true });
  writeFileSync(`${directory}/${String(++captured).padStart(3, '0')}-${tag}.json`, JSON.stringify(data, null, 2) + '\n');
}
test.beforeAll(async () => {
  let response: Response | undefined;
  try { response = await fetch(`${origin}/api/projects`, { headers }); } catch { /* 无监听。 */ }
  if (response) throw new Error('9806 已有服务，未复用未知合成库或停止未知进程。');
  mkdirSync(`${root}/.cache`, { recursive: true });
  const log = createWriteStream(`${root}/.cache/frontend-rule-qualification-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.rule_qualification_browser', '--port', '9806', '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => {
    if (server?.exitCode !== null) throw new Error('合成服务提前退出，请查看原始日志。');
    try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; }
  }).toBe(200);
  const index = JSON.parse(readFileSync(`${root}/.cache/frontend-rule-qualification-index.json`, 'utf8'));
  expect(index.synthetic_only).toBe(true); cases = index.cases;
  expect(new Set(Object.values(cases).map(item => item.project_id)).size).toBe(7);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) {
    const closed = new Promise<number | null>(resolve => server!.once('exit', code => resolve(code)));
    server.kill('SIGINT'); expect(await closed).toBe(0);
    expect(JSON.parse(readFileSync(`${root}/.cache/frontend-rule-qualification-audit.json`, 'utf8'))).toMatchObject({ 逐表导出相同: true });
  }
});
async function json(request: APIRequestContext, path: string, tag: string) {
  const response = await request.get(`${origin}/api${path}`, { headers });
  expect(response.status()).toBe(200); const data = await response.json(); capture(tag, data); return data;
}
async function proof(request: APIRequestContext, name: Name) {
  const item = cases[name], states = expected[name];
  const before = await json(request, `/claims/${item.claim_id}`, `${name}-claim-before`);
  expect(before.claim).toMatchObject({ claim_state: 'candidate', effective_state: 'confirmed',
    confirmation_source: name === 'human_override' ? 'human' : 'rule',
    rule_qualification: { status: states.qualification, input_eligible: name === 'verified' || name === 'human_override' },
    source_qualification: { status: 'not_excluded', input_eligible: true },
    input_eligible: name === 'verified' || name === 'human_override' });
  expect(before.claim.review_history.map((review: { action_id: number }) => review.action_id)).toContain(item.rule_review_id);
  expect(before.claim.review_history).toHaveLength(name === 'human_override' ? 2 : 1);
  const graph = await json(request, `/semantic-graph?project=${item.project_id}&collection=nodes&limit=100`, `${name}-authoritative-state`);
  const target = graph.items.find((node: { entity_id: string; scope: Record<string, string> }) => node.entity_id === item.entity_id && node.scope.dataset_version === 'data_v2');
  expect(target.states.adoption.state).toBe(states.adoption);
  expect(target.states.adoption.source_issues ?? []).toEqual([]);
  if (!['verified', 'human_override'].includes(name)) expect(target.states.adoption.rule_issues).toContainEqual({ claim_id: item.claim_id, status: states.qualification });
  return { item, before, graph };
}
async function enter(page: Page, item: Identity) {
  await page.goto(`${origin}/#token=synthetic-rule-qualification-token`);
  await page.getByLabel('当前项目', { exact: true }).selectOption(item.project_id);
  await page.getByRole('button', { name: '研究图', exact: true }).click();
}
async function graphNode(page: Page, item: Identity, state: string, scope = 'data_v2') {
  await page.getByRole('button', { name: '研究图', exact: true }).click();
  await expect.poll(async () => {
    const reading = await page.locator('[data-research-summary]').getAttribute('data-reading');
    return reading ? JSON.parse(reading).project_id : null;
  }).toBe(item.project_id);
  // ID 是静态合成身份与完整范围；不借状态计算函数生成期望。
  const id = `${item.entity_id}::[["dataset_version","${scope}"]]`;
  const target = page.locator(`.react-flow__node[data-id='${id}']`);
  await expect(target.locator('.node-dimensions')).toContainText(`采用 ${state}`);
  await expect(target.locator('.badge')).toHaveText('已确认');
  await expect(target.locator('[data-source-issues]')).toHaveCount(0);
  await target.scrollIntoViewIfNeeded();
  await expect.poll(() => target.evaluate(element => {
    const box = element.getBoundingClientRect(), canvas = element.closest('.graph-canvas')!.getBoundingClientRect();
    return Math.min(box.right, canvas.right, innerWidth) > Math.max(box.left, canvas.left, 0)
      && Math.min(box.bottom, canvas.bottom, innerHeight) > Math.max(box.top, canvas.top, 0);
  })).toBe(true);
  return target;
}
async function history(page: Page, item: Identity, node: Locator, status: string) {
  await node.locator('strong').first().click();
  await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  const retained = page.locator(`.graph-retained [data-claim-id="${item.claim_id}"]`);
  await expect(retained.locator('.badge')).toHaveText('已确认');
  await retained.getByRole('button').first().click();
  const drawer = page.getByRole('dialog', { name: `记录 ${item.claim_id} 详情`, exact: true });
  await expect(drawer.locator('.badge')).toHaveText('已确认');
  await expect(drawer.locator('.drawer-actions button')).toHaveCount(0);
  await expect(drawer.locator('[data-source-qualification]')).toHaveCount(0);
  const note = drawer.locator(`[data-rule-claim-id="${item.claim_id}"]`);
  await expect(note).toHaveAttribute('data-rule-qualification', status);
  await note.evaluate(element => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
  const geometry = await note.evaluate(element => {
    const box = element.getBoundingClientRect();
    const header = element.closest('[role="dialog"]')!.querySelector(':scope > header')!.getBoundingClientRect();
    return { width: box.width, height: box.height, left: box.left, right: box.right, top: box.top, bottom: box.bottom,
      headerBottom: header.bottom, viewport: innerWidth, viewportHeight: innerHeight, document: document.documentElement.scrollWidth };
  });
  capture(`visible-rule-${item.claim_id}`, geometry);
  expect(geometry.width).toBeGreaterThan(0); expect(geometry.height).toBeGreaterThan(0);
  expect(geometry.left).toBeGreaterThanOrEqual(0); expect(geometry.right).toBeLessThanOrEqual(geometry.viewport);
  expect(geometry.top).toBeGreaterThanOrEqual(geometry.headerBottom); expect(geometry.bottom).toBeLessThanOrEqual(geometry.viewportHeight);
  expect(geometry.document).toBeLessThanOrEqual(geometry.viewport);
  return drawer;
}

test('较新未核对规则保留确认与原文，阻止回退旧采用及旧证据', async ({ page, request }) => {
  const { item, before, graph } = await proof(request, 'pending_newer'); await enter(page, item);
  const finding = item.finding!;
  const findingState = graph.items.find((node: { entity_id: string }) => node.entity_id === finding.entity_id);
  expect(findingState.states.evidence_state.state).toBe('unassessed');
  expect(findingState.states.evidence_state.rule_issues).toContainEqual({ claim_id: finding.claim_id, status: 'pending' });
  const node = await graphNode(page, item, '未知');
  const findingNode = page.locator(`.react-flow__node[data-id^="${finding.entity_id}::"]`);
  await expect(findingNode.locator('.node-dimensions')).toContainText('证据 未评估');
  expect(JSON.parse((await node.locator('[data-rule-issues]').getAttribute('data-rule-issues'))!)).toEqual([{ claim_id: item.claim_id, status: 'pending' }]);
  const drawer = await history(page, item, node, 'pending');
  await page.screenshot({ path: '../.cache/frontend-rule-qualification-pending-desktop.png' });
  const reading = JSON.parse((await page.locator('[data-research-summary]').getAttribute('data-reading'))!);
  await drawer.locator('.evidence-link').click();
  await expect(page.getByTestId('evidence-quote')).toHaveText(item.span.quote);
  expect(createHash('sha256').update((await page.getByTestId('evidence-quote').textContent())!).digest('hex')).toBe(item.quote_sha256);
  const query = new URLSearchParams({ project: item.project_id, occurred_until: reading.occurred_until, known_until: reading.known_until,
    expected_revision: String(reading.revision), start: String(item.span.byte_start), end: String(item.span.byte_end), context: '0' });
  const raw = await json(request, `/evidence/${item.span.event_id}?${query}`, 'pending-historical-raw');
  expect(raw).toMatchObject({ history_context: true, derived_context_loaded: false });
  expect(raw.event).toMatchObject({ event_id: item.span.event_id, quote: item.span.quote, quote_sha256: item.quote_sha256 });
  await page.screenshot({ path: '../.cache/frontend-rule-qualification-evidence-desktop.png' });
  expect(await json(request, `/claims/${item.claim_id}`, 'pending-claim-after')).toEqual(before);
});

test('有效与不成立证明分别撤回和保留旧采用，不能核对与不可用保持未知且来源独立', async ({ page, request }) => {
  for (const name of ['verified', 'invalid', 'unverifiable', 'unavailable'] as const) {
    const { item, before } = await proof(request, name); await enter(page, item);
    const node = await graphNode(page, item, expected[name].display);
    if (name === 'verified') await expect(node.locator('[data-rule-issues]')).toHaveCount(0);
    else {
      await history(page, item, node, name);
      if (name === 'invalid' || name === 'unverifiable') await page.screenshot({ path: `../.cache/frontend-rule-qualification-${name}-desktop.png` });
    }
    expect(await json(request, `/claims/${item.claim_id}`, `${name}-claim-after`)).toEqual(before);
  }
});

test('较旧未核对记录不挡后来采用，人工覆盖规则及另一个完整范围隔离', async ({ page, request }) => {
  for (const name of ['pending_older', 'human_override', 'unverifiable'] as const) {
    const { item, before, graph } = await proof(request, name); await enter(page, item);
    const node = await graphNode(page, item, expected[name].display);
    if (name === 'human_override') {
      await expect(node.locator('[data-rule-issues]')).toHaveCount(0);
      expect(before.claim.review_history.at(-1)).toMatchObject({ actor: 'human:公开人工覆盖', action: 'confirm' });
    } else if (name === 'pending_older') await history(page, item, node, 'pending');
    else {
      const another = graph.items.find((record: { entity_id: string; scope: Record<string, string> }) => record.entity_id === item.entity_id && record.scope.dataset_version === 'data_v3');
      expect(another.states.adoption.state).toBe('accepted');
      expect(another.states.adoption.rule_issues ?? []).toEqual([]);
      const anotherNode = await graphNode(page, item, '采用', 'data_v3');
      await expect(anotherNode.locator('[data-rule-issues]')).toHaveCount(0);
    }
    expect(await json(request, `/claims/${item.claim_id}`, `${name}-claim-after-isolation`)).toEqual(before);
  }
});

test('390直接切项目，规则提示不冒充来源故障，确认历史与诊断卡实际可见', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/#token=synthetic-rule-qualification-token`);
  for (const name of ['pending_newer', 'invalid', 'unverifiable', 'unavailable'] as const) {
    const { item } = await proof(request, name);
    await page.getByLabel('当前项目', { exact: true }).selectOption(item.project_id);
    const node = await graphNode(page, item, expected[name].display);
    await history(page, item, node, expected[name].qualification);
    if (name === 'pending_newer' || name === 'unverifiable') await page.screenshot({ path: `../.cache/frontend-rule-qualification-${name}-mobile.png` });
    await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  }
});
