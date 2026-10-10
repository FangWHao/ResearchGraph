import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createWriteStream, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { claimInstant } from '../../src/exactTime';
import { adoption, evidenceState, foldGroup, graphNodeId, projectGraph, scopeKey } from '../../src/model';
import type { Claim, GraphData } from '../../src/types';

const origin = 'http://127.0.0.1:9794';
const headers = { Authorization: 'Bearer synthetic-state-time-token' };
const root = fileURLToPath(new URL('../../../', import.meta.url));
let server: ChildProcess;
const cases = [
  ['精确微秒', 'accepted', 'refuted'], ['同刻时区冲突', 'conflict', 'conflict'],
  ['缺失时间', 'time_unknown', 'time_unknown'], ['没有时区', 'time_unknown', 'time_unknown'],
  ['非法日期', 'time_unknown', 'time_unknown'], ['未知范围', 'unknown_scope', 'needs_review'],
  ['紧凑日期', 'accepted', 'refuted'], ['周日期', 'accepted', 'refuted'],
  ['逗号小数', 'accepted', 'refuted'], ['秒级偏移', 'accepted', 'refuted'],
  ['UTC越界', 'time_unknown', 'time_unknown'],
] as const;
test.beforeAll(async () => {
  let occupied = false; try { await fetch(origin); occupied = true; } catch { /* 无监听。 */ }
  if (occupied) throw new Error('9794 已占用，未终止已有服务。');
  mkdirSync(`${root}/.cache`, { recursive: true });
  const log = createWriteStream(`${root}/.cache/frontend-state-time-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.state_time_browser', '--port', '9794', '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => {
    if (server.exitCode !== null) throw new Error('合成服务提前退出，见服务日志。');
    try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; }
  }).toBe(200);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) {
    const closed = new Promise<void>(resolve => server.once('exit', () => resolve()));
    server.kill('SIGINT'); await closed;
  }
});
async function read(request: APIRequestContext) {
  const response = await request.get(`${origin}/api/projects`, { headers }); expect(response.status()).toBe(200);
  const projects = (await response.json()).projects;
  const project = projects.find((p: { name: string }) => p.name === '验收精确状态项目'); expect(project).toBeTruthy();
  const graphResponse = await request.get(`${origin}/api/graph?project=${project.project_id}`, { headers }); expect(graphResponse.status()).toBe(200);
  return { project, data: await graphResponse.json() as GraphData };
}
async function open(page: Page, request: APIRequestContext) {
  const value = await read(request);
  await page.goto(`${origin}/#token=synthetic-state-time-token`);
  await page.getByLabel('当前项目', { exact: true }).selectOption(value.project.project_id);
  await page.getByRole('button', { name: '研究图', exact: true }).click();
  await expect(page.locator('.react-flow__node')).toHaveCount(projectGraph(value.data.claims).entities.length);
  return value;
}
function entity(data: GraphData, label: string): Claim {
  const value = data.claims.find(c => c.claim_type === 'entity_version' && c.payload.label === label);
  expect(value, label).toBeTruthy(); return value!;
}
function node(page: Page, claim: Claim) { return page.locator(`.react-flow__node[data-id=${JSON.stringify(graphNodeId(claim))}]`); }
async function geometry(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); }
async function question(page: Page, label: string) {
  await page.locator('.question-index').getByRole('button').filter({ hasText: `${label}问题` }).click();
  const card = page.locator('.approach-card').filter({ hasText: `${label}方案` }); await expect(card).toBeVisible(); return card;
}
async function snapshot(page: Page) {
  return page.evaluate(() => ({
    nodes: [...document.querySelectorAll('.react-flow__node')].map(n => ({ id: n.getAttribute('data-id'), position: (n as HTMLElement).style.transform })).sort((a, b) => a.id!.localeCompare(b.id!)),
    edges: [...document.querySelectorAll('.react-flow__edge')].map(n => n.getAttribute('data-id')).sort(),
  }));
}
async function collapse(page: Page, data: GraphData) {
  const graph = projectGraph(data.claims);
  const members = [entity(data, '精确微秒发现'), entity(data, '精确状态链中间')];
  const folded = foldGroup(members.map(graphNodeId), graph.edges, data.claims, !data.partial && !graph.unresolvedClaimIds.length);
  await page.locator('.graph-node-picker summary').click();
  for (const claim of members) await page.locator(`.graph-node-picker input[data-node-id=${JSON.stringify(graphNodeId(claim))}]`).check();
  await page.getByRole('button', { name: '折叠所选（2）', exact: true }).click();
  await expect(page.locator('.process-node')).toHaveCount(1);
  return { folded, graph, members };
}

test('真实 API 的规范微秒与权威状态一致，Python 特殊日期不由浏览器猜测', async ({ request }) => {
  const { project, data } = await read(request);
  const response = await request.get(`${origin}/api/semantic-graph?project=${project.project_id}&collection=nodes&limit=100&offset=0`, { headers });
  expect(response.status()).toBe(200); const packet = await response.json();
  expect(packet.revision).toBe(data.revision); expect(packet.next_offset).toBeNull();
  expect(data.claims.every(c => Object.hasOwn(c, 'occurred_at_utc') && Object.hasOwn(c, 'recorded_at_utc'))).toBe(true);
  for (const [label, action, findingEvidence] of cases) {
    for (const kind of ['发现', '方案'] as const) {
      const claim = entity(data, label + kind);
      const state = packet.items.find((n: { entity_id: string; scope: Claim['scope'] }) => n.entity_id === claim.entity_id && scopeKey(n.scope) === scopeKey(claim.scope)).states;
      const expectedEvidence = kind === '发现' ? findingEvidence : label === '未知范围' ? 'needs_review' : 'unassessed';
      expect(state.adoption.state, label + kind).toBe(action); expect(state.evidence_state.state, label + kind).toBe(expectedEvidence);
      expect(adoption(data.claims, claim.entity_id!, claim.scope), label + kind).toBe(state.adoption.state);
      expect(evidenceState(data.claims, claim.entity_id!, claim.scope), label + kind).toBe(state.evidence_state.state);
    }
  }
  const finding = entity(data, '精确微秒发现');
  const decisions = data.claims.filter(c => c.claim_type === 'decision_event' && c.payload.target === finding.entity_id);
  expect(decisions).toHaveLength(2);
  const accepted = decisions.find(c => c.payload.action === 'accepted')!, withdrawn = decisions.find(c => c.payload.action === 'withdrawn')!;
  expect(accepted.claim_id).toBeLessThan(withdrawn.claim_id);
  expect(claimInstant(accepted, 'recorded')! <= claimInstant(withdrawn, 'recorded')!).toBe(true);
  expect(claimInstant(accepted, 'occurred')! - claimInstant(withdrawn, 'occurred')!).toBe(1n);
  const special = ['紧凑日期', '周日期', '逗号小数'].map(label => data.claims.find(c => c.claim_type === 'evidence_event' && c.payload.target === entity(data, label + '发现').entity_id)!);
  expect(new Set(special.map(c => c.occurred_at)).size).toBe(3);
  expect(special.map(c => c.occurred_at_utc)).toEqual(Array(3).fill('2026-01-02T00:00:00.123456+00:00'));
  const offset = data.claims.find(c => c.claim_type === 'evidence_event' && c.payload.target === entity(data, '秒级偏移发现').entity_id)!;
  expect(offset.occurred_at).toBe('2026-01-02T00:00:00.123456+00:00:30'); expect(offset.occurred_at_utc).toBe('2026-01-01T23:59:30.123456+00:00');
  for (const label of ['缺失时间', '没有时区', '非法日期', 'UTC越界']) {
    const event = data.claims.find(c => c.claim_type === 'evidence_event' && c.payload.target === entity(data, label + '发现').entity_id)!;
    expect(event.occurred_at_utc, label).toBeNull(); expect(claimInstant(event, 'occurred'), label).toBeNull();
  }
  expect(await (await request.get(`${origin}/api/graph?project=${project.project_id}`, { headers })).json()).toEqual(data);
});

test('图与问题卡展示真实微秒状态，未知和冲突不进入当前采用', async ({ page, request }) => {
  const { data } = await open(page, request);
  const graphAction: Record<string, string> = { accepted: '采用', conflict: '有冲突', time_unknown: '时间未知', unknown_scope: '范围未知' };
  const graphEvidence: Record<string, string> = { refuted: '被否定', conflict: '记录冲突', time_unknown: '发生时间未知', needs_review: '需复核' };
  for (const [label, action, state] of cases) {
    const dimensions = node(page, entity(data, label + '发现')).locator('.node-dimensions');
    await expect(dimensions).toContainText(`采用 ${graphAction[action]}`); await expect(dimensions).toContainText(`证据 ${graphEvidence[state]}`);
  }
  await page.getByRole('button', { name: '研究问题', exact: true }).click();
  for (const [label, action] of cases) {
    const card = await question(page, label);
    const names: Record<string, string> = { accepted: '采用', conflict: '记录冲突', time_unknown: '发生时间未知', unknown_scope: '范围未知' };
    await expect(card.locator('.dimension-row')).toContainText(`采用 ${names[action]}`);
    await expect(card.locator('.dimension-row')).toContainText(`证据 ${label === '未知范围' ? '需复核' : '未评估'}`);
    expect(await card.evaluate(n => n.closest('.approach-group')!.querySelector('h3')!.textContent)).toContain(action === 'accepted' ? '当前采用' : '状态未明');
  }
  const card = await question(page, '精确微秒'); await card.evaluate(n => n.scrollIntoView({ block: 'center', behavior: 'instant' }));
  await page.screenshot({ path: '../.cache/frontend-state-time-question-desktop.png' }); await geometry(page);
});

test('同修订折叠保留状态记录和原证据，展开恢复完整关系与微秒结论', async ({ page, request }) => {
  const { project, data } = await open(page, request);
  const before = await snapshot(page); const { folded, members } = await collapse(page, data);
  const retained = page.getByRole('region', { name: '保留的原始记录' });
  expect(await retained.locator('article').evaluateAll(nodes => nodes.map(n => Number(n.getAttribute('data-claim-id'))))).toEqual(data.claims.filter(c => folded.claimIds.includes(c.claim_id)).map(c => c.claim_id));
  const negative = data.claims.find(c => c.claim_type === 'evidence_event' && c.payload.target === members[0].entity_id && c.payload.state === 'refuted' && !c.replacement_ids.length)!;
  expect(folded.claimIds).toContain(negative.claim_id);
  await retained.locator(`article[data-claim-id="${negative.claim_id}"]`).getByRole('button').click();
  await expect(page.getByRole('dialog', { name: /记录 .* 详情/ })).toBeVisible();
  await page.locator('.detail-drawer .evidence-link').first().click();
  const span = negative.evidence[0];
  const rawResponse = await request.get(`${origin}/api/evidence/${span.event_id}?start=${span.byte_start}&end=${span.byte_end}&context=0`, { headers });
  expect(rawResponse.status()).toBe(200); const raw = await rawResponse.json();
  await expect(page.getByTestId('evidence-quote')).toHaveText(raw.event.quote);
  expect(createHash('sha256').update(raw.event.quote).digest('hex')).toBe(span.quote_sha256);
  await page.screenshot({ path: '../.cache/frontend-state-time-evidence-desktop.png' });
  await page.getByRole('button', { name: '关闭原文', exact: true }).click(); await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  await page.locator('.graph-node-picker summary').click(); await page.getByRole('button', { name: '聚焦过程组', exact: true }).click();
  await page.locator('.graph-canvas').evaluate(n => n.scrollIntoView({ block: 'start', behavior: 'instant' }));
  await expect(page.locator('.process-node')).toBeInViewport(); await expect.poll(async () => (await page.locator('.process-node').boundingBox())!.width).toBeGreaterThan(280);
  // 过程组不声明一个聚合采用/证据状态；原成员状态由完整原记录独立计算。
  const foldedData = await (await request.get(`${origin}/api/graph?project=${project.project_id}`, { headers })).json() as GraphData;
  expect(foldedData).toEqual(data);
  expect(adoption(foldedData.claims, members[0].entity_id!, members[0].scope)).toBe('accepted');
  expect(evidenceState(foldedData.claims, members[0].entity_id!, members[0].scope)).toBe('refuted');
  await expect(page.locator('.process-node .group-retention')).toContainText('证据状态');
  await page.screenshot({ path: '../.cache/frontend-state-time-fold-desktop.png' });
  await page.getByRole('button', { name: '展开过程组', exact: true }).click(); await expect.poll(() => snapshot(page)).toEqual(before);
  await expect(node(page, members[0]).locator('.node-dimensions')).toContainText('采用 采用'); await expect(node(page, members[0]).locator('.node-dimensions')).toContainText('证据 被否定');
  expect(await (await request.get(`${origin}/api/graph?project=${project.project_id}`, { headers })).json()).toEqual(data);
});

test('390小屏未知状态和原证据可读，切到空项目不残留旧方案', async ({ page, request }) => {
  const { data } = await open(page, request);
  // 当前窄屏导航没有项目下拉；通过实际桌面入口切换后再验收窄屏内容。
  await page.getByRole('button', { name: '研究问题', exact: true }).click();
  await question(page, '同刻时区冲突');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '精确状态独立空项目' });
  await expect(page.locator('.approach-card')).toHaveCount(0); await expect(page.getByText('还没有可查看的研究问题', { exact: true })).toBeVisible();
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收精确状态项目' });
  await page.setViewportSize({ width: 390, height: 844 });
  const unknown = await question(page, '未知范围'); await expect(unknown.locator('.dimension-row')).toContainText('采用 范围未知'); await expect(unknown.locator('.dimension-row')).toContainText('证据 需复核');
  await unknown.evaluate(n => n.scrollIntoView({ block: 'start', behavior: 'instant' })); await geometry(page);
  await page.screenshot({ path: '../.cache/frontend-state-time-unknown-mobile.png' });
  const conflict = await question(page, '同刻时区冲突'); await expect(conflict.locator('.dimension-row')).toContainText('记录冲突');
  await conflict.evaluate(n => n.scrollIntoView({ block: 'start', behavior: 'instant' })); await geometry(page);
  await page.screenshot({ path: '../.cache/frontend-state-time-conflict-mobile.png' });
  await page.getByRole('button', { name: '研究图', exact: true }).click(); await expect(page.locator('.react-flow__node')).toHaveCount(projectGraph(data.claims).entities.length);
  await collapse(page, data); const retained = page.getByRole('region', { name: '保留的原始记录' });
  const unknownClaim = data.claims.find(c => c.claim_type === 'evidence_event' && c.payload.target === entity(data, '精确微秒发现').entity_id && c.occurred_at === null && c.effective_state === 'candidate')!;
  await retained.locator(`article[data-claim-id="${unknownClaim.claim_id}"]`).getByRole('button').click(); await page.locator('.detail-drawer .evidence-link').first().click();
  const span = unknownClaim.evidence[0]; const raw = await (await request.get(`${origin}/api/evidence/${span.event_id}?start=${span.byte_start}&end=${span.byte_end}&context=0`, { headers })).json();
  await expect(page.getByTestId('evidence-quote')).toHaveText(raw.event.quote); expect(createHash('sha256').update(raw.event.quote).digest('hex')).toBe(span.quote_sha256);
  await geometry(page); await page.screenshot({ path: '../.cache/frontend-state-time-evidence-mobile.png' });
});
