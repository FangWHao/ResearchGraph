import { createHash } from 'node:crypto';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { adoption, foldGroup, graphNodeId, isSemanticEdge, joinRecords, projectGraph } from '../../src/model';
import type { Claim, GraphData } from '../../src/types';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const projectName = '验收语义图项目';
async function open(page: Page, request: APIRequestContext, name = projectName) {
  const projects = await (await request.get('/api/projects', { headers })).json();
  const project = projects.projects.find((item: { name: string }) => item.name === name);
  expect(project).toBeTruthy();
  const response = await request.get(`/api/graph?project=${project.project_id}`, { headers });
  expect(response.status()).toBe(200);
  const data = await response.json() as GraphData;
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: name });
  await page.getByRole('button', { name: '研究图', exact: true }).click();
  await expect(page.locator('.react-flow__node')).toHaveCount(projectGraph(data.claims).entities.length);
  return { project, data };
}
async function select(page: Page, claims: Claim[]) {
  const picker = page.locator('.graph-node-picker');
  if (!await picker.evaluate(node => (node as HTMLDetailsElement).open)) await picker.locator('summary').click();
  while (await picker.locator('input:checked').count()) await picker.locator('input:checked').first().uncheck();
  for (const claim of claims) await page.locator(`.graph-node-picker input[data-node-id=${JSON.stringify(graphNodeId(claim))}]`).check();
  await expect(page.getByRole('button', { name: `折叠所选（${claims.length}）`, exact: true })).toBeEnabled();
}
async function snapshot(page: Page) {
  return page.evaluate(() => ({
    nodes: [...document.querySelectorAll('.react-flow__node')].map(node => ({ id: node.getAttribute('data-id'), position: (node as HTMLElement).style.transform })).sort((a, b) => a.id!.localeCompare(b.id!)),
    edges: [...document.querySelectorAll('.react-flow__edge')].map(edge => edge.getAttribute('data-id')).sort(),
  }));
}
function foldable(data: GraphData) {
  const graph = projectGraph(data.claims);
  // Discover a true two-node single-entry/single-exit process from the actual API, rather than inventing IDs.
  for (const edge of graph.edges.filter(isSemanticEdge)) {
    const members = [edge.source, edge.target];
    try {
      const folded = foldGroup(members, graph.edges, data.claims, !data.partial && graph.unresolvedClaimIds.length === 0);
      const retained = data.claims.filter(claim => folded.claimIds.includes(claim.claim_id));
      if (retained.some(claim => claim.payload.action === 'withdrawn') && retained.some(claim => claim.payload.state === 'refuted')) return { graph, folded, members: graph.entities.filter(claim => members.includes(graphNodeId(claim))) };
    } catch { /* Other seed branches deliberately fail the conservative folding rule. */ }
  }
  throw new Error('真实合成库缺少保留撤回及阴性证据的可折叠过程');
}

test('真实图折叠保留重要历史和每条原边证据，展开恢复全部原节点、关系与位置', async ({ page, request }) => {
  const { project, data } = await open(page, request);
  const { graph, folded, members } = foldable(data);
  const before = await snapshot(page);
  expect(before.nodes).toHaveLength(graph.entities.length);
  expect(before.edges).toEqual(graph.edges.map(edge => edge.id).sort());
  await select(page, members);
  await page.getByRole('button', { name: '折叠所选（2）', exact: true }).click();
  await expect(page.locator('.process-node')).toHaveCount(1);
  await expect(page.locator('.react-flow__node')).toHaveCount(graph.entities.length - 1);
  const retained = page.getByRole('region', { name: '保留的原始记录' });
  const expectedClaims = data.claims.filter(claim => folded.claimIds.includes(claim.claim_id));
  expect(await retained.locator('article').evaluateAll(nodes => nodes.map(node => Number(node.getAttribute('data-claim-id'))))).toEqual(expectedClaims.map(claim => claim.claim_id));
  const retainedEdges = await retained.locator('.graph-original-edge').evaluateAll(nodes => nodes.map(node => JSON.parse(node.getAttribute('data-original-edge')!)));
  expect(retainedEdges).toEqual([...folded.internalEdges, ...folded.boundaryEdges]);
  const foldedView = await snapshot(page);
  expect(foldedView.edges).toEqual(graph.edges.filter(edge => !(folded.members.includes(edge.source) && folded.members.includes(edge.target))).map(edge => edge.id).sort());
  expect(expectedClaims.some(claim => claim.payload.action === 'withdrawn')).toBe(true);
  expect(expectedClaims.some(claim => claim.payload.state === 'refuted')).toBe(true);
  expect(expectedClaims.some(claim => claim.effective_state === 'candidate')).toBe(true);
  expect(new Set(expectedClaims.map(claim => JSON.stringify(claim.scope))).size).toBeGreaterThan(1);
  const boundary = folded.boundaryEdges.find(edge => isSemanticEdge(edge))!;
  const boundaryClaim = data.claims.find(claim => claim.claim_id === boundary.claimId)!;
  await retained.locator('.graph-original-edge').getByRole('button', { name: new RegExp(`^#${boundary.claimId} `) }).click();
  await expect(page.getByRole('dialog', { name: /记录 .* 详情/ })).toBeVisible();
  await page.locator('.detail-drawer .evidence-link').first().click();
  const span = boundaryClaim.evidence[0];
  const rawResponse = await request.get(`/api/evidence/${span.event_id}?start=${span.byte_start}&end=${span.byte_end}&context=0`, { headers });
  expect(rawResponse.status()).toBe(200);
  const raw = await rawResponse.json();
  await expect(page.getByTestId('evidence-quote')).toHaveText(raw.event.quote);
  expect(createHash('sha256').update(raw.event.quote).digest('hex')).toBe(span.quote_sha256);
  await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  await page.locator('.graph-node-picker summary').click();
  await page.getByRole('button', { name: '聚焦过程组', exact: true }).click();
  await page.locator('.graph-canvas').evaluate(node => node.scrollIntoView({ block: 'start' }));
  await expect(page.locator('.process-node')).toBeInViewport();
  await expect.poll(async () => (await page.locator('.process-node').boundingBox())!.width).toBeGreaterThan(280);
  await page.screenshot({ path: '../.cache/frontend-semantic-graph-group-desktop.png' });
  await page.screenshot({ path: '../.cache/frontend-semantic-graph-desktop.png', fullPage: true });
  await page.getByRole('button', { name: '展开过程组', exact: true }).click();
  await expect(page.locator('.react-flow__node')).toHaveCount(before.nodes.length);
  await expect.poll(() => snapshot(page)).toEqual(before);
  const after = await (await request.get(`/api/graph?project=${project.project_id}`, { headers })).json();
  expect(after).toEqual(data);
});

test('真实比较未选端口不表示使用，多版本内容须主动选择，共同输入不能压为单入口', async ({ page, request }) => {
  const { project, data } = await open(page, request);
  const graph = projectGraph(data.claims);
  const compare = data.claims.find(claim => claim.claim_type === 'join_ports' && claim.payload.semantics === 'compare_then_select')!;
  expect(compare).toBeTruthy();
  const compared = graph.edges.filter(edge => edge.claimId === compare.claim_id && edge.role === 'compared_input');
  expect(compared.length).toBeGreaterThan(0);
  expect(compared.every(edge => !isSemanticEdge(edge))).toBe(true);
  await expect(page.locator('.react-flow__edge-text').filter({ hasText: '仅比较（未选）' })).toHaveCount(compared.length);
  await expect(page.locator('.react-flow__edge-text').filter({ hasText: '被选输入' })).toHaveCount(graph.edges.filter(edge => edge.claimId === compare.claim_id && edge.role === 'selected_input').length);
  const multiple = [...graph.versions.entries()].find(([, versions]) => versions.length > 1)!;
  expect(multiple).toBeTruthy();
  const selector = page.getByLabel(`显示内容版本 ${multiple[1][0].entity_id}`, { exact: true });
  await expect(selector).toHaveValue('');
  await selector.selectOption(String(multiple[1].at(-1)!.claim_id));
  await expect(selector).toHaveValue(String(multiple[1].at(-1)!.claim_id));
  await selector.locator('xpath=ancestor::div[contains(@class,"research-node")]').locator('strong').click();
  await expect(page.getByRole('dialog', { name: /记录 .* 详情/ })).toContainText(multiple[1].at(-1)!.payload.label!);
  await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  const required = data.claims.find(claim => claim.claim_type === 'join_ports' && claim.payload.semantics === 'all_required')!;
  const target = graph.entities.find(claim => claim.entity_id === required.payload.target)!;
  const input = graph.entities.find(claim => claim.entity_id === required.payload.inputs![0].ref)!;
  await select(page, [input, target]);
  await page.getByRole('button', { name: '折叠所选（2）', exact: true }).click();
  await expect(page.locator('.process-node')).toHaveCount(0);
  await expect(page.locator('.graph-notice')).toContainText('单入口单出口');
  expect(await (await request.get(`/api/graph?project=${project.project_id}`, { headers })).json()).toEqual(data);
});

test('隐藏候选、真实缺端点和模拟部分投影都不能折叠', async ({ page, request }) => {
  const { data } = await open(page, request);
  const { members } = foldable(data);
  await page.getByRole('checkbox', { name: '显示待复核记录', exact: true }).uncheck();
  await page.getByRole('button', { name: '折叠所选（0）', exact: true }).click();
  await expect(page.locator('.graph-notice')).toContainText('完整研究图');
  await open(page, request, '验收语义图缺口项目');
  await expect(page.locator('.notice').filter({ hasText: '同范围端点' })).toBeVisible();
  await page.getByRole('button', { name: '折叠所选（0）', exact: true }).click();
  await expect(page.locator('.graph-notice')).toContainText('完整研究图');
  await page.route('**/api/semantic-graph?*', async route => {
    if (new URL(route.request().url()).searchParams.get('offset') !== '0') {
      await route.fulfill({ status: 503, json: { error: '合成第二页读取失败' } }); return;
    }
    const response = await route.fetch(); const body = await response.json();
    await route.fulfill({ response, json: { ...body, total: body.total + 1, claims_total: body.total + 1, next_offset: body.items.length } });
  });
  await open(page, request);
  await select(page, members);
  await page.getByRole('button', { name: '折叠所选（2）', exact: true }).click();
  await expect(page.locator('.process-node')).toHaveCount(0);
  await expect(page.locator('.graph-notice')).toContainText('完整研究图');
});

test('390小屏通过节点列表真实折叠，重要历史和原文入口仍在屏内', async ({ page, request }) => {
  const { data } = await open(page, request);
  const { graph, members } = foldable(data);
  await page.setViewportSize({ width: 390, height: 844 });
  await select(page, members);
  await page.getByRole('button', { name: '折叠所选（2）', exact: true }).click();
  await expect(page.locator('.process-node')).toHaveCount(1);
  await expect(page.locator('.react-flow__node')).toHaveCount(graph.entities.length - 1);
  await page.locator('.graph-node-picker summary').click();
  await page.locator('.graph-retained').evaluate(node => node.scrollIntoView({ block: 'start' }));
  await page.screenshot({ path: '../.cache/frontend-semantic-graph-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const withdrawal = page.locator('.graph-retained article').filter({ hasText: '决定 · 撤回' }).first();
  await withdrawal.evaluate(node => node.scrollIntoView({ block: 'center' }));
  await page.screenshot({ path: '../.cache/frontend-semantic-graph-mobile-history.png' });
  await withdrawal.getByRole('button').click();
  await expect(page.getByRole('dialog', { name: /记录 .* 详情/ })).toContainText('撤回');
  await page.locator('.detail-drawer .evidence-link').first().click();
  await expect(page.getByRole('dialog', { name: '原文证据' })).toBeVisible();
  await expect(page.getByTestId('evidence-quote')).not.toBeEmpty();
});

test('真实人工更正让旧内容与旧关系退出有效投影，旧决定和原文继续留在过程组历史', async ({ page, request }) => {
  const { project, data } = await open(page, request);
  const initial = foldable(data);
  const version = initial.members.find(claim => initial.graph.versions.get(graphNodeId(claim))!.length === 1)!;
  const relation = data.claims.find(claim => claim.claim_id === initial.folded.internalEdges.find(isSemanticEdge)!.claimId)!;
  const join = data.claims.find(claim => claim.claim_type === 'join_ports' && claim.payload.semantics === 'compare_then_select')!;
  const decision = data.claims.find(claim => initial.folded.claimIds.includes(claim.claim_id) && claim.payload.action === 'withdrawn')!;
  let revision = data.revision;
  const replacements: { original: Claim; claimId: number }[] = [];
  for (const [original, payload] of [
    [version, { ...version.payload, label: '阴性实验记录 · 人工内容更正', content: '更正表述，保留原始阴性证据与范围。' }],
    [relation, { ...relation.payload }],
    [join, { ...join.payload }],
    [decision, { ...decision.payload, action: 'deferred', reason: '人工纠正当前动作，原撤回事件仍保留。' }],
  ] as [Claim, Claim['payload']][]) {
    const response = await request.post(`/api/claims/${original.claim_id}/edit`, { headers, data: { payload, scope: original.scope, actor: 'human:合成语义纠正验收', expected_revision: revision } });
    const receipt = await response.json();
    expect(response.status(), JSON.stringify(receipt)).toBe(200);
    revision = receipt.revision;
    replacements.push({ original, claimId: receipt.claim_id });
  }
  await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  const updated = await (await request.get(`/api/graph?project=${project.project_id}`, { headers })).json() as GraphData;
  expect(updated.revision).toBe(revision);
  const graph = projectGraph(updated.claims);
  for (const item of replacements) {
    expect(updated.claims.find(claim => claim.claim_id === item.original.claim_id)?.replacement_ids).toContain(item.claimId);
    expect(graph.edges.some(edge => edge.claimId === item.original.claim_id)).toBe(false);
  }
  expect(graph.versions.get(graphNodeId(version))?.map(claim => claim.claim_id)).toEqual([replacements[0].claimId]);
  expect(joinRecords(updated.claims, join.payload.target!, join.scope).map(claim => claim.claim_id)).toEqual([replacements[2].claimId]);
  expect(adoption(updated.claims, decision.payload.target!, decision.scope)).toBe('deferred');
  await expect.poll(async () => (await snapshot(page)).edges).toEqual(graph.edges.map(edge => edge.id).sort());
  const process = foldable(updated);
  await select(page, process.members);
  await page.getByRole('button', { name: '折叠所选（2）', exact: true }).click();
  await expect(page.locator('.process-node')).toHaveCount(1);
  const old = page.locator(`.graph-retained article[data-claim-id="${decision.claim_id}"]`);
  await expect(old).toContainText('已被替代');
  await old.getByRole('button').click();
  await page.locator('.detail-drawer .evidence-link').first().click();
  const span = decision.evidence[0];
  const raw = await (await request.get(`/api/evidence/${span.event_id}?start=${span.byte_start}&end=${span.byte_end}&context=0`, { headers })).json();
  await expect(page.getByTestId('evidence-quote')).toHaveText(raw.event.quote);
  expect(createHash('sha256').update(raw.event.quote).digest('hex')).toBe(span.quote_sha256);
});
