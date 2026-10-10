import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import type { Claim, ResearchReading } from '../../src/types';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
type Scope = Record<string, string> | null;
export interface NodeIdentity { entity_id: string; scope: Scope; node_id?: string }
interface SourceIdentity {
  claim_id: number; span_id: number; event_id: number; byte_start: number; byte_end: number;
  quote: string; quote_sha256: string; recorded_at: string;
}
interface CaseIdentity {
  project_id: string; nodes: Record<string, NodeIdentity>; claims: Record<string, SourceIdentity>;
  source_paths: string[]; known_before_late: string;
}
interface SemanticCase {
  id: string; title: string; project_name: string; scope: Scope;
  nodes: Record<string, { kind: string; label: string; entity?: string; scope?: Scope }>;
  relations: { key: string; source: string; target: string; relation: string }[];
  joins: { key: string; target: string; semantics: string; inputs: { port: string; ref: string }[]; selected: string | null }[];
  events: { key: string; target: string; type: string; value: string; day: number }[];
  expected: {
    members: string[]; entry: string; exit: string; node_count: number;
    internal_edges: string[]; boundary_edges: string[]; retained_claims: string[];
    states: { node: string; adoption?: string; evidence_state?: string }[];
    outside_retained?: string[]; refuse_members?: string[];
    join?: { key: string; semantics: string; inputs: { port: string; ref: string; semantic: boolean; required: boolean }[]; selected: string | null; independence: boolean };
    native?: { initial_state?: string; initial_effective_state?: string; corrected_state?: string; new_state?: string };
  };
}
export interface EdgeIdentity {
  id: string; claimId: number; source: string; target: string;
  sourceEntity: string; targetEntity: string; relation: string;
  semantic: boolean; targetPort?: string; joinSemantics?: string;
  role?: 'required_input' | 'selected_input' | 'compared_input' | 'synthesis_input';
}
export function nodeId(identity: NodeIdentity): string {
  return identity.node_id ?? `${identity.entity_id}::${identity.scope ? JSON.stringify(Object.entries(identity.scope).sort(([a], [b]) => a.localeCompare(b))) : 'unknown'}`;
}
export async function openCase(page: Page, name: string, count: number) {
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: name });
  await page.getByRole('button', { name: '研究图', exact: true }).click();
  await expect(page.locator('[data-research-summary]')).toContainText('双时间内全部 L2 登记记录');
  await expect(page.locator('.react-flow__node')).toHaveCount(count);
  await expect(page.locator('[data-view-ready]')).toHaveAttribute('data-view-ready', 'true');
  return JSON.parse((await page.locator('[data-research-summary]').getAttribute('data-reading'))!) as ResearchReading;
}
export async function records(request: APIRequestContext, reading: ResearchReading): Promise<Claim[]> {
  const all: Claim[] = [];
  let offset = 0;
  while (true) {
    const query = new URLSearchParams({
      project: reading.project_id, occurred_until: reading.occurred_until,
      known_until: reading.known_until, expected_revision: String(reading.revision),
      collection: 'claims', limit: '100', offset: String(offset),
    });
    const response = await request.get(`/api/semantic-graph?${query}`, { headers });
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.project_id).toBe(reading.project_id); expect(body.revision).toBe(reading.revision);
    expect(body.occurred_until).toBe(reading.occurred_until); expect(body.known_until).toBe(reading.known_until);
    expect(body.offset).toBe(offset); expect(body.layer).toBe('L2');
    expect(body.projection).toBe(false); expect(body.semantic_graph_complete).toBe(true);
    expect(body.items.length).toBeGreaterThan(0);
    all.push(...body.items);
    if (body.next_offset === null) { expect(all.length).toBe(body.total); break; }
    expect(body.next_offset).toBe(offset + body.items.length); offset = body.next_offset;
  }
  expect(new Set(all.map(claim => claim.claim_id)).size).toBe(all.length);
  return all;
}
export async function selectMembers(page: Page, members: NodeIdentity[]) {
  const picker = page.locator('.graph-node-picker');
  if (!await picker.evaluate(element => (element as HTMLDetailsElement).open)) await picker.locator('summary').click();
  while (await picker.locator('input:checked').count()) await picker.locator('input:checked').first().uncheck();
  for (const member of members) await picker.locator(`input[data-node-id=${JSON.stringify(nodeId(member))}]`).check();
  await expect(page.getByRole('button', { name: `折叠所选（${members.length}）`, exact: true })).toBeEnabled();
}
export async function layoutSnapshot(page: Page) {
  return page.evaluate(() => ({
    nodes: [...document.querySelectorAll('.react-flow__node')].map(element => ({
      id: element.getAttribute('data-id'), position: (element as HTMLElement).style.transform,
    })).sort((a, b) => a.id!.localeCompare(b.id!)),
    edges: [...document.querySelectorAll('.react-flow__edge')].map(element => element.getAttribute('data-id')).sort(),
  }));
}
export async function assertCanvasVisible(page: Page, selector = '.react-flow__node') {
  const canvas = page.getByTestId('research-canvas');
  await canvas.evaluate(element => element.scrollIntoView({ block: 'center' }));
  await expect.poll(() => canvas.evaluate((element, selected) => {
    const area = element.getBoundingClientRect();
    const headerBottom = document.querySelector('.project-picker')?.getBoundingClientRect().bottom ?? 0;
    return [...element.querySelectorAll(selected)].some(node => {
      const box = node.getBoundingClientRect();
      return box.width > 0 && box.height > 0
        && Math.min(box.right, area.right, innerWidth) > Math.max(box.left, area.left, 0)
        && Math.min(box.bottom, area.bottom, innerHeight) > Math.max(box.top, area.top, headerBottom, 0);
    });
  }, selector)).toBe(true);
}
export async function assertSource(page: Page, request: APIRequestContext, claim: Claim, source: SourceIdentity, reading: ResearchReading) {
  await page.locator(`.graph-retained article[data-claim-id="${claim.claim_id}"]`).getByRole('button').click();
  const drawer = page.getByRole('dialog', { name: `记录 ${claim.claim_id} 详情`, exact: true });
  await expect(drawer.locator('.drawer-evidence .evidence-link')).toHaveCount(claim.evidence.length);
  await expect(drawer.locator('.drawer-actions button')).toHaveCount(0);
  await drawer.locator('.drawer-evidence .evidence-link').first().click();
  expect(claim.evidence[0]).toMatchObject({ span_id: source.span_id, event_id: source.event_id, byte_start: source.byte_start, byte_end: source.byte_end, quote_sha256: source.quote_sha256 });
  const span = source;
  const query = new URLSearchParams({
    project: reading.project_id, occurred_until: reading.occurred_until,
    known_until: reading.known_until, expected_revision: String(reading.revision),
    start: String(span.byte_start), end: String(span.byte_end), context: '0',
  });
  const response = await request.get(`/api/evidence/${span.event_id}?${query}`, { headers });
  expect(response.status()).toBe(200);
  const raw = await response.json();
  expect(raw.event.quote).toBe(source.quote);
  await expect(page.getByTestId('evidence-quote')).toHaveText(source.quote);
  expect(createHash('sha256').update(raw.event.quote).digest('hex')).toBe(source.quote_sha256);
  expect(raw.history_context).toBe(true); expect(raw.derived_context_loaded).toBe(false);
  expect(raw).not.toHaveProperty('l1'); expect(raw).not.toHaveProperty('artifact_versions');
  await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await page.getByRole('button', { name: '关闭详情', exact: true }).click();
}

const root = fileURLToPath(new URL('../../../', import.meta.url));
const fixture = JSON.parse(readFileSync(`${root}/fixtures/golden/semantic_cases.json`, 'utf8')) as { version: number; synthetic_only: boolean; cases: SemanticCase[] };
let identities: Record<string, CaseIdentity>;
test.beforeAll(() => {
  expect(fixture.version).toBe(1); expect(fixture.synthetic_only).toBe(true);
  expect(fixture.cases.map(item => item.id)).toEqual(['A01', 'A02', 'A03', 'A04-required', 'A04-synthesis', 'A05', 'A06', 'A07', 'A08', 'A09', 'A10']);
  identities = JSON.parse(readFileSync(`${root}/.cache/semantic-cases-index.json`, 'utf8'));
  expect(Object.keys(identities).sort()).toEqual(fixture.cases.map(item => item.id).sort());
});

function expectedEdges(value: SemanticCase, identity: CaseIdentity): Map<string, EdgeIdentity> {
  const edges = new Map<string, EdgeIdentity>();
  for (const relation of value.relations) {
    const source = identity.nodes[relation.source], target = identity.nodes[relation.target];
    const claim = identity.claims[relation.key];
    edges.set(relation.key, { id: `claim-${claim.claim_id}`, claimId: claim.claim_id, source: nodeId(source), target: nodeId(target), sourceEntity: source.entity_id, targetEntity: target.entity_id, relation: relation.relation, semantic: relation.relation !== 'same_topic' });
  }
  for (const join of value.joins) for (const [position, input] of join.inputs.entries()) {
    const source = identity.nodes[input.ref], target = identity.nodes[join.target], claim = identity.claims[join.key];
    const role = join.semantics === 'all_required' ? 'required_input' : join.semantics === 'evidence_synthesis' ? 'synthesis_input' : input.ref === join.selected ? 'selected_input' : 'compared_input';
    edges.set(`${join.key}:${input.port}`, { id: `claim-${claim.claim_id}-${position}-${input.port}`, claimId: claim.claim_id,
      source: nodeId(source), target: nodeId(target), sourceEntity: source.entity_id, targetEntity: target.entity_id,
      relation: `input:${input.port}`, semantic: role !== 'compared_input', targetPort: input.port, joinSemantics: join.semantics, role });
  }
  return edges;
}
async function assertStates(page: Page, value: SemanticCase, identity: CaseIdentity) {
  const adoptionNames: Record<string, string> = { accepted: '采用', deferred: '暂缓', rejected: '拒绝', withdrawn: '撤回' };
  const evidenceNames: Record<string, string> = { supported: '受支持', refuted: '被否定', contested: '有争议', insufficient: '不足', needs_review: '需复核' };
  for (const state of value.expected.states) {
    const node = page.locator(`.react-flow__node[data-id=${JSON.stringify(nodeId(identity.nodes[state.node]))}]`);
    if (state.adoption) await expect(node.locator('.node-dimensions')).toContainText(`采用 ${adoptionNames[state.adoption]}`);
    if (state.evidence_state) await expect(node.locator('.node-dimensions')).toContainText(`证据 ${evidenceNames[state.evidence_state]}`);
  }
}
async function verifyQuote(request: APIRequestContext, source: SourceIdentity, reading: ResearchReading) {
  const params = new URLSearchParams({ project: reading.project_id, occurred_until: reading.occurred_until, known_until: reading.known_until, expected_revision: String(reading.revision), context: '0', start: String(source.byte_start), end: String(source.byte_end) });
  const response = await request.get(`/api/evidence/${source.event_id}?${params}`, { headers });
  expect(response.status()).toBe(200);
  const raw = await response.json();
  expect(raw.event.event_id).toBe(source.event_id); expect(raw.event.quote).toBe(source.quote);
  expect(raw.event.quote_sha256).toBe(source.quote_sha256);
  expect(createHash('sha256').update(raw.event.quote).digest('hex')).toBe(source.quote_sha256);
  expect(raw.history_context).toBe(true); expect(raw.derived_context_loaded).toBe(false);
}
async function settleView(page: Page) {
  let previous = '', unchanged = 0;
  await expect.poll(async () => {
    const current = await page.evaluate(() => new Promise<string>(resolve => requestAnimationFrame(() => resolve((document.querySelector('.react-flow__viewport') as HTMLElement).style.transform))));
    unchanged = current === previous ? unchanged + 1 : 0; previous = current; return unchanged;
  }).toBeGreaterThanOrEqual(2);
}
async function focusExternalState(page: Page, identity: NodeIdentity) {
  await page.getByRole('button', { name: '适配画布', exact: true }).click();
  await settleView(page);
  const selector = `.react-flow__node[data-id=${JSON.stringify(nodeId(identity))}]`;
  await assertCanvasVisible(page, `${selector} .node-dimensions`);
  const node = page.locator(selector), canvas = page.getByTestId('research-canvas');
  const first = (await node.boundingBox())!;
  await page.mouse.move(first.x + first.width / 2, first.y + first.height / 2);
  // 通过真实画布滚轮与中键平移聚焦；不写组件状态或视角对象。
  const desiredWidth = page.viewportSize()!.width === 390 ? 240 : 320;
  await page.mouse.wheel(0, -Math.log2(desiredWidth / first.width) / 0.002);
  await settleView(page);
  const box = (await node.boundingBox())!, area = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down({ button: 'middle' });
  await page.mouse.move(area.x + area.width / 2, area.y + area.height / 2, { steps: 8 });
  await page.mouse.up({ button: 'middle' });
  await settleView(page);
  await expect.poll(() => node.locator('.node-dimensions').evaluate(element => {
    const box = element.getBoundingClientRect();
    const area = document.querySelector('[data-testid="research-canvas"]')!.getBoundingClientRect();
    const header = document.querySelector('.project-picker')?.getBoundingClientRect().bottom ?? 0;
    return box.width > 100 && box.height > 0 && box.left >= Math.max(area.left, 0)
      && box.right <= Math.min(area.right, innerWidth)
      && box.top >= Math.max(area.top, header, 0) && box.bottom <= Math.min(area.bottom, innerHeight);
  })).toBe(true);
}

for (const value of fixture.cases) test(`${value.id} 静态语义预期：${value.title}，折叠及展开保持历史、端口、证据与位置`, async ({ page, request }) => {
  const identity = identities[value.id];
  const mobile = ['A04-synthesis', 'A05', 'A10'].includes(value.id);
  if (mobile) await page.setViewportSize({ width: 390, height: 844 });
  const reading = await openCase(page, value.project_name, value.expected.node_count);
  expect(reading.project_id).toBe(identity.project_id);
  const originalClaims = await records(request, reading);
  expect(originalClaims.map(claim => claim.claim_id).sort((a, b) => a - b)).toEqual(Object.values(identity.claims).map(claim => claim.claim_id).sort((a, b) => a - b));
  const edges = expectedEdges(value, identity);
  const original = await layoutSnapshot(page);
  expect(original.nodes.map(node => node.id).sort()).toEqual(Object.values(identity.nodes).map(nodeId).sort());
  expect(original.edges).toEqual([...edges.values()].map(edge => edge.id).sort());
  await assertStates(page, value, identity);
  for (const key of [...value.expected.retained_claims, ...(value.expected.outside_retained ?? [])]) {
    const source = identity.claims[key];
    const claim = originalClaims.find(item => item.claim_id === source.claim_id)!;
    expect(claim).toBeTruthy(); expect(claim.evidence).toHaveLength(1);
    expect(claim.evidence[0]).toMatchObject({ span_id: source.span_id, event_id: source.event_id, byte_start: source.byte_start, byte_end: source.byte_end, quote_sha256: source.quote_sha256 });
    await verifyQuote(request, source, reading);
  }
  if (value.id === 'A10') {
    const initial = originalClaims.find(claim => claim.claim_id === identity.claims.initial.claim_id)!;
    const correction = originalClaims.find(claim => claim.claim_id === identity.claims.correction.claim_id)!;
    const reextracted = originalClaims.find(claim => claim.claim_id === identity.claims.reextracted.claim_id)!;
    expect(value.expected.native?.initial_effective_state).toBe('dismissed');
    expect(initial.claim_state).toBe(value.expected.native!.initial_state); expect(initial.effective_state).toBe(value.expected.native!.initial_effective_state); expect(initial.replacement_ids).toEqual([correction.claim_id]); expect(initial.replaced).toBe(true);
    expect(correction.effective_state).toBe(value.expected.native!.corrected_state); expect(correction.payload.action).toBe('deferred'); expect(correction.actor).toMatch(/^human:/); expect(correction.replaces_claim).toBe(initial.claim_id);
    expect(reextracted.claim_state).toBe(value.expected.native!.new_state); expect(reextracted.effective_state).toBe(value.expected.native!.new_state); expect(reextracted.payload.action).toBe('accepted');
    expect(initial.actor).toMatch(/^model:/); expect(reextracted.actor).toMatch(/^model:/);
    expect(identity.claims.initial.event_id).toBe(identity.claims.reextracted.event_id);
    expect(identity.claims.initial.quote).toBe(identity.claims.reextracted.quote);
    expect(identity.claims.initial.byte_start).toBe(identity.claims.reextracted.byte_start);
    expect(identity.claims.initial.byte_end).toBe(identity.claims.reextracted.byte_end);
  }
  if (value.id === 'A08') {
    for (const key of value.expected.members) await expect(page.locator(`.react-flow__node[data-id=${JSON.stringify(nodeId(identity.nodes[key]))}] .node-dimensions`)).toContainText('运行 未知');
  }
  if (value.expected.refuse_members) {
    await selectMembers(page, value.expected.refuse_members.map(key => identity.nodes[key]));
    await page.getByRole('button', { name: `折叠所选（${value.expected.refuse_members.length}）`, exact: true }).click();
    await expect(page.locator('.process-node')).toHaveCount(0);
    await expect(page.locator('.graph-notice')).toContainText('单入口单出口');
    expect(await layoutSnapshot(page)).toEqual(original);
  }
  await selectMembers(page, value.expected.members.map(key => identity.nodes[key]));
  await page.getByLabel('过程组名称', { exact: true }).fill(`${value.id} 合成过程`);
  await page.getByRole('button', { name: `折叠所选（${value.expected.members.length}）`, exact: true }).click();
  await expect(page.locator('.process-node')).toHaveCount(1);
  await expect(page.locator('.react-flow__node')).toHaveCount(value.expected.node_count - value.expected.members.length + 1);
  await expect(page.locator('.process-node .node-dimensions')).toHaveCount(0);
  const retained = page.locator('.graph-retained article');
  expect((await retained.evaluateAll(elements => elements.map(element => Number(element.getAttribute('data-claim-id'))))).sort((a, b) => a - b)).toEqual(value.expected.retained_claims.map(key => identity.claims[key].claim_id).sort((a, b) => a - b));
  const expectedOriginals = [...value.expected.internal_edges, ...value.expected.boundary_edges].map(key => edges.get(key)!);
  expect(expectedOriginals.every(edge => edge !== undefined)).toBe(true);
  const originals = await page.locator('[data-original-edge]').evaluateAll(elements => elements.map(element => JSON.parse(element.getAttribute('data-original-edge')!)));
  expect(originals.map(edge => edge.id).sort()).toEqual(expectedOriginals.map(edge => edge.id).sort());
  for (const edge of expectedOriginals) {
    const actual = originals.find(item => item.id === edge.id);
    expect(actual).toMatchObject({ ...edge });
    expect(actual.evidenceIds).toEqual([Object.values(identity.claims).find(claim => claim.claim_id === edge.claimId)!.span_id]);
  }
  const folded = await layoutSnapshot(page);
  expect(folded.edges).toEqual([...edges.entries()].filter(([key]) => !value.expected.internal_edges.includes(key)).map(([, edge]) => edge.id).sort());
  expect(folded.nodes.find(node => node.id === 'view-process-group')!.position).toBe(original.nodes.find(node => node.id === nodeId(identity.nodes[value.expected.entry]))!.position);
  const memberIds = value.expected.members.map(key => nodeId(identity.nodes[key]));
  const handles = await page.locator('.process-node .react-flow__handle').evaluateAll(elements => elements.map(element => element.getAttribute('data-handleid')).sort());
  expect(handles).toEqual(value.expected.boundary_edges.map(key => edges.get(key)!).map(edge => `${memberIds.includes(edge.target) ? 'in' : 'out'}-${edge.id}`).sort());
  const expectedEvidence = [...new Set(value.expected.retained_claims.map(key => identity.claims[key].span_id))];
  await expect(page.locator('.process-node')).toContainText(`${expectedEvidence.length} 条原证据`);
  for (const state of value.expected.states.filter(state => !value.expected.members.includes(state.node))) await assertStates(page, { ...value, expected: { ...value.expected, states: [state] } }, identity);
  if (['A03', 'A05'].includes(value.id)) {
    const external = value.expected.states.find(state => !value.expected.members.includes(state.node))!;
    await focusExternalState(page, identity.nodes[external.node]);
    await assertStates(page, { ...value, expected: { ...value.expected, states: [external] } }, identity);
    expect((await layoutSnapshot(page)).edges).toEqual(folded.edges);
    const subject = value.id === 'A03' ? 'negative-desktop' : 'unselected-mobile';
    await page.screenshot({ path: `../.cache/frontend-semantic-cases-${value.id}-${subject}.png` });
  }
  if (value.expected.join) {
    for (const input of value.expected.join.inputs) {
      const edge = originals.find(item => item.id === edges.get(`${value.expected.join!.key}:${input.port}`)!.id);
      expect(edge.semantic).toBe(input.semantic);
      expect(edge.role === 'required_input').toBe(input.required);
      expect(edge.joinSemantics).toBe(value.expected.join.semantics);
    }
  }
  const sourceKey = value.id === 'A10' ? 'initial' : value.events.find(event => value.expected.retained_claims.includes(event.key))?.key ?? value.expected.internal_edges.find(key => !key.includes(':')) ?? value.expected.boundary_edges[0];
  const source = identity.claims[sourceKey];
  if (value.id === 'A10') await expect(page.locator(`.graph-retained article[data-claim-id="${source.claim_id}"]`)).toContainText('已被替代');
  await assertSource(page, request, originalClaims.find(claim => claim.claim_id === source.claim_id)!, source, reading);
  if (['A01', 'A04-required', 'A04-synthesis', 'A05', 'A10'].includes(value.id)) {
    await page.getByRole('button', { name: '聚焦过程组', exact: true }).click();
    await assertCanvasVisible(page, '.process-node'); await settleView(page);
    if (mobile) expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../.cache/frontend-semantic-cases-${value.id}-${mobile ? 'mobile' : 'desktop'}.png` });
  }
  await page.getByRole('button', { name: '展开过程组', exact: true }).click();
  await expect.poll(() => layoutSnapshot(page)).toEqual(original);
  await assertStates(page, value, identity);
  expect(await records(request, reading)).toEqual(originalClaims);
  if (value.expected.outside_retained?.length) {
    await page.getByRole('button', { name: '适配画布', exact: true }).click();
    await assertCanvasVisible(page); await settleView(page);
  }
  for (const key of value.expected.outside_retained ?? []) {
    const event = value.events.find(item => item.key === key)!;
    await page.locator(`.react-flow__node[data-id=${JSON.stringify(nodeId(identity.nodes[event.target]))}] strong`).click();
    await page.getByRole('button', { name: '关闭详情', exact: true }).click();
    const source = identity.claims[key];
    await assertSource(page, request, originalClaims.find(claim => claim.claim_id === source.claim_id)!, source, reading);
  }
  if (value.id === 'A09') {
    await page.getByLabel('研究图已知时间截止', { exact: true }).fill(identity.known_before_late);
    await page.getByRole('button', { name: '重新读取研究图', exact: true }).click();
    await expect(page.locator('[data-view-ready]')).toHaveAttribute('data-view-ready', 'true');
    const beforeLate = JSON.parse((await page.locator('[data-research-summary]').getAttribute('data-reading'))!) as ResearchReading;
    expect((await records(request, beforeLate)).some(claim => claim.claim_id === identity.claims.late.claim_id)).toBe(false);
    await assertStates(page, value, identity);
    await page.getByLabel('研究图已知时间截止', { exact: true }).fill(reading.known_until);
    await page.getByLabel('研究图发生时间截止', { exact: true }).fill('2026-01-02T23:59:59+00:00');
    await page.getByRole('button', { name: '重新读取研究图', exact: true }).click();
    await expect(page.locator('[data-view-ready]')).toHaveAttribute('data-view-ready', 'true');
    const older = JSON.parse((await page.locator('[data-research-summary]').getAttribute('data-reading'))!) as ResearchReading;
    const olderClaims = await records(request, older);
    expect(olderClaims.some(claim => claim.claim_id === identity.claims.late.claim_id)).toBe(true);
    expect(olderClaims.some(claim => claim.claim_id === identity.claims.current.claim_id)).toBe(false);
    await expect(page.locator(`.react-flow__node[data-id=${JSON.stringify(nodeId(identity.nodes.B))}] .node-dimensions`)).toContainText('采用 拒绝');
  }
});
