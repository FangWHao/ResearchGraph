import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWriteStream, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import type { Claim, NoteRequest, NoteResult } from '../../src/types';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const origin = 'http://127.0.0.1:9807', token = 'synthetic-note-token';
const headers = { Authorization: `Bearer ${token}` };
interface Index { project_id: string; second_project: string; target: NoteResult; other_target: NoteResult; candidates: Record<'confirm' | 'edit', NoteResult & { text: string }> }
let server: ChildProcess | undefined, directory: string, index: Index, captured = 0;
function capture(tag: string, value: unknown) {
  const dir = `${root}/.cache/frontend-notes-api`; mkdirSync(dir, { recursive: true });
  writeFileSync(`${dir}/${String(++captured).padStart(3, '0')}-${tag}.json`, JSON.stringify(value, null, 2) + '\n');
}
test.beforeAll(async () => {
  let occupied = false; try { await fetch(origin); occupied = true; } catch { /* 无监听。 */ }
  if (occupied) throw new Error('9807已占用；未停止未知服务。');
  directory = mkdtempSync(join(tmpdir(), 'researchgraph-notes-'));
  mkdirSync(`${root}/.cache`, { recursive: true });
  const prepareLog = createWriteStream(`${root}/.cache/frontend-notes-prepare.log`);
  const prepare = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.note_browser', '--fixture-dir', directory, '--prepare'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  prepare.stdout!.pipe(prepareLog); prepare.stderr!.pipe(prepareLog);
  expect(await new Promise<number | null>(resolve => prepare.once('exit', resolve))).toBe(0);
  index = JSON.parse(readFileSync(`${root}/.cache/frontend-notes-index.json`, 'utf8'));
  expect(index.project_id).not.toBe(index.second_project);
  const log = createWriteStream(`${root}/.cache/frontend-notes-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.note_browser', '--fixture-dir', directory, '--port', '9807', '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => { if (server?.exitCode !== null) throw new Error('合成服务提前退出，见日志。'); try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; } }).toBe(200);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) {
    const closed = new Promise<number | null>(resolve => server!.once('exit', resolve)); server.kill('SIGINT'); expect(await closed).toBe(0);
    expect(JSON.parse(readFileSync(`${root}/.cache/frontend-notes-audit.json`, 'utf8'))).toMatchObject({ 原始事件与候选内容未改写: true });
  }
  if (directory) rmSync(directory, { recursive: true, force: true });
});
async function json(request: APIRequestContext, path: string, tag: string) {
  const response = await request.get(`${origin}${path}`, { headers }); expect(response.status()).toBe(200);
  const data = await response.json(); capture(tag, data); return data;
}
async function audit(request: APIRequestContext) { return json(request, '/synthetic-notes/audit', 'audit'); }
async function detail(request: APIRequestContext, id: number, tag: string): Promise<Claim> { return (await json(request, `/api/claims/${id}`, tag)).claim; }
async function enter(page: Page, project = index.project_id) {
  await page.goto(`${origin}/#token=${token}`); await page.getByLabel('当前项目', { exact: true }).selectOption(project);
  await page.getByRole('button', { name: '研究笔记', exact: true }).click();
  await expect(page.locator('.notes-view .pagination')).toBeVisible();
}
async function form(page: Page) {
  await page.getByRole('button', { name: '新增研究笔记', exact: true }).click();
  return page.getByRole('dialog', { name: '新增研究笔记', exact: true });
}
async function save(page: Page, dialog: Locator) {
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/records/note');
  await dialog.getByRole('button', { name: '保存研究笔记', exact: true }).click();
  const result = await response; expect(result.status()).toBe(200); const receipt: NoteResult = await result.json();
  capture('manual-request', result.request().postDataJSON()); capture('manual-receipt', receipt); return receipt;
}
async function geometry(node: Locator) {
  await node.evaluate(element => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
  const bounds = await node.evaluate(element => {
    const b = element.getBoundingClientRect(), dialog = element.closest('[role="dialog"]');
    const header = dialog?.querySelector(':scope > header')?.getBoundingClientRect();
    return { left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width, height: b.height,
      headerBottom: header?.bottom ?? 0, widthLimit: innerWidth, heightLimit: innerHeight, document: document.documentElement.scrollWidth };
  });
  capture('geometry', bounds); expect(bounds.width).toBeGreaterThan(0); expect(bounds.height).toBeGreaterThan(0);
  expect(bounds.left).toBeGreaterThanOrEqual(0); expect(bounds.right).toBeLessThanOrEqual(bounds.widthLimit);
  expect(bounds.top).toBeGreaterThanOrEqual(bounds.headerBottom); expect(bounds.bottom).toBeLessThanOrEqual(bounds.heightLimit);
  expect(bounds.document).toBeLessThanOrEqual(bounds.widthLimit);
}

test('人工笔记原文及关联对象独立保存，真实分页与原文检索可回到同窗口，HTML不执行', async ({ page, request }) => {
  const before = await audit(request); const first = await json(request, `/api/claims?project=${index.project_id}&kind=note&limit=50`, 'first-note-page');
  expect(first.total).toBe(53); expect(first.claims).toHaveLength(50); expect(first.next_offset).toBe(50);
  expect(first.claims.every((claim: Claim) => claim.payload.kind === 'note')).toBe(true);
  await enter(page); const dialog = await form(page);
  const text = '  检索笔记字面\n<script>window.noteInjected=true</script>\n尾部空白  ';
  await dialog.getByLabel('笔记正文', { exact: true }).fill(text);
  await dialog.getByLabel('关联对象 ID（可留空）', { exact: true }).fill(index.target.entity_id);
  await page.screenshot({ path: '../.cache/frontend-notes-form-desktop.png' });
  const saved = await save(page, dialog), original = await detail(request, saved.claim_id, 'manual-detail');
  expect(original).toMatchObject({ actor: 'human:本机用户', basis: 'manual', claim_state: 'confirmed', effective_state: 'confirmed', scope: null,
    payload: { kind: 'note', content: text, label: text, about: index.target.entity_id } });
  const drawer = page.getByRole('dialog', { name: `记录 ${saved.claim_id} 详情`, exact: true });
  await expect(drawer.locator('.claim-content')).toHaveText(text); await expect(drawer.locator('.note-association')).toContainText(index.target.entity_id);
  await drawer.locator('.evidence-link').click(); const quote = await page.getByTestId('evidence-quote').textContent();
  expect(JSON.parse('"' + quote + '"')).toBe(text);
  expect(createHash('sha256').update(quote!).digest('hex')).toBe(original.evidence[0].quote_sha256);
  const span = original.evidence[0]; const raw = await json(request, `/api/evidence/${span.event_id}?start=${span.byte_start}&end=${span.byte_end}&context=0`, 'manual-raw');
  expect(raw.event).toMatchObject({ event_id: saved.event_id, quote, quote_sha256: span.quote_sha256 });
  expect(await page.evaluate(() => (window as unknown as { noteInjected?: boolean }).noteInjected)).toBeUndefined();
  const contextResponse = page.waitForResponse(r => new URL(r.url()).pathname === `/api/evidence/${span.event_id}` && new URL(r.url()).searchParams.get('context') === '0');
  await page.locator('.context-selector select').selectOption('0');
  const context = await contextResponse; expect(context.status()).toBe(200); const isolated = await context.json(); capture('isolated-original', isolated);
  expect(isolated.event).toMatchObject({ event_id: saved.event_id, quote, quote_sha256: span.quote_sha256 });
  await expect(page.getByTestId('evidence-quote')).toHaveText(quote!);
  await geometry(page.getByTestId('evidence-quote'));
  await page.screenshot({ path: '../.cache/frontend-notes-evidence-desktop.png' });
  await page.getByRole('button', { name: '关闭原文', exact: true }).click(); await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  await page.locator('.notes-view').getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page.locator(`[data-note-id="${saved.claim_id}"]`)).toContainText(text);
  const second = await json(request, `/api/claims?project=${index.project_id}&kind=note&limit=50&offset=50`, 'second-note-page');
  expect(second.total).toBe(54); expect(second.next_offset).toBeNull(); expect(second.claims.map((c: Claim) => c.claim_id)).toContain(saved.claim_id);
  await page.getByLabel('搜索会话原文', { exact: true }).fill('检索笔记字面'); await page.getByRole('button', { name: '开始搜索', exact: true }).click();
  await expect(page.locator('.search-result')).toContainText('检索笔记字面');
  const after = await audit(request); expect(after.initial_history_sha256).toBe(before.initial_history_sha256); expect(after.model_attempts).toBe(0);
});

test('写入后回包丢失保持同意图重试，无重复原件或笔记，忙时不能双击提交', async ({ page, request }) => {
  let release = () => {}; const held = new Promise<void>(resolve => { release = resolve; });
  const bodies: NoteRequest[] = [], receipts: NoteResult[] = [];
  await page.route('**/api/records/note', async route => {
    bodies.push(route.request().postDataJSON()); const response = await route.fetch(); expect(response.status()).toBe(200);
    receipts.push(await response.json());
    if (bodies.length === 1) { await held; await route.abort('failed'); } else await route.fulfill({ response });
  });
  try {
    await enter(page); const dialog = await form(page), text = '丢失回执后找回唯一笔记';
    await dialog.getByLabel('笔记正文', { exact: true }).fill(text); await dialog.getByRole('button', { name: '保存研究笔记', exact: true }).click();
    await expect(dialog.getByRole('button', { name: '正在保存…', exact: true })).toBeDisabled(); await dialog.locator('form').dispatchEvent('submit');
    await expect.poll(() => receipts.length).toBe(1); expect(bodies).toHaveLength(1); const wrote = await audit(request);
    release(); await expect(dialog.getByRole('alert')).toBeVisible(); await expect(dialog.getByLabel('笔记正文', { exact: true })).toHaveValue(text);
    const retry = await save(page, dialog); expect(retry.replayed).toBe(true); expect(retry.claim_id).toBe(receipts[0].claim_id);
    expect(bodies).toHaveLength(2); expect(bodies[1].request_id).toBe(bodies[0].request_id);
    const retried = await audit(request); expect(retried).toEqual(wrote);
    const changed = await request.post(`${origin}/api/records/note`, { headers, data: { ...bodies[0], text: '相同UUID改写' } }); expect(changed.status()).toBe(409);
    const original = await detail(request, retry.claim_id, 'retry-original'); expect(original.payload.content).toBe(text);
    expect((await audit(request)).initial_history_sha256).toBe(wrote.initial_history_sha256);
  } finally { release(); await page.unroute('**/api/records/note'); }
});

test('Agent笔记保持候选，人工确认和修改追加记录且about不可更换，研究图不显示科学状态轴', async ({ page, request }) => {
  await enter(page);
  const first = index.candidates.confirm, second = index.candidates.edit;
  const before = await detail(request, first.claim_id, 'candidate-before');
  expect(before).toMatchObject({ actor: 'model:mcp', basis: 'model_inference', claim_state: 'candidate', effective_state: 'candidate', scope: null, payload: { about: index.target.entity_id } });
  await page.locator(`[data-note-id="${first.claim_id}"]`).getByRole('button', { name: /查看记录/ }).click();
  let drawer = page.getByRole('dialog', { name: `记录 ${first.claim_id} 详情`, exact: true }); await expect(drawer.locator('.badge')).toHaveText('待复核');
  await drawer.getByRole('button', { name: '确认', exact: true }).click(); await expect(drawer.locator('.badge')).toHaveText('已确认');
  const confirmed = await detail(request, first.claim_id, 'candidate-confirmed'); expect(confirmed.payload).toEqual(before.payload); expect(confirmed.evidence).toEqual(before.evidence);
  expect(confirmed).toMatchObject({ claim_state: 'candidate', effective_state: 'confirmed', confirmation_source: 'human' });
  await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  await page.locator(`[data-note-id="${second.claim_id}"]`).getByRole('button', { name: /查看记录/ }).click();
  drawer = page.getByRole('dialog', { name: `记录 ${second.claim_id} 详情`, exact: true }); await drawer.getByRole('button', { name: '修改', exact: true }).click();
  const edit = page.getByRole('dialog', { name: '保留原文，追加你的判断', exact: true });
  await expect(edit.getByLabel('研究范围（可留空）', { exact: true })).toHaveValue(''); await expect(edit.locator('.note-readonly-about')).toContainText(index.target.entity_id);
  await expect(edit.getByLabel('关联对象 ID（可留空）', { exact: true })).toHaveCount(0);
  await edit.getByLabel('内容', { exact: true }).fill('人工补充后的笔记全文');
  const changed = page.waitForResponse(r => new URL(r.url()).pathname === `/api/claims/${second.claim_id}/edit`);
  await edit.getByRole('button', { name: '修改并确认', exact: true }).click(); const response = await changed; expect(response.status()).toBe(200);
  capture('edit-body', response.request().postDataJSON()); const replacement = await response.json(); capture('edit-receipt', replacement);
  const after = await detail(request, replacement.claim_id, 'edited-note');
  expect(after).toMatchObject({ basis: 'manual', effective_state: 'confirmed', scope: null, replaces_claim: second.claim_id, payload: { about: index.target.entity_id, content: '人工补充后的笔记全文' } });
  const old = await detail(request, second.claim_id, 'edited-old'); expect(old.payload.content).toBe(second.text); expect(old.replacement_ids).toContain(after.claim_id); expect(after.evidence).toEqual(old.evidence);
  await page.screenshot({ path: '../.cache/frontend-notes-edit-desktop.png' });
  await page.getByRole('button', { name: '关闭详情', exact: true }).click(); await page.getByRole('button', { name: '研究图', exact: true }).click();
  const node = page.locator(`.react-flow__node[data-id^="${confirmed.entity_id}::"]`); await expect(node).toBeAttached();
  await expect(node.locator('.node-dimensions')).toHaveCount(0); await expect(node.locator('.note-association')).toContainText(index.target.entity_id);
});

test('390笔记表单和详情实际可见，错误关联不写入且草稿按项目隔离', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await enter(page);
  let dialog = await form(page); const text = '第一项目保留的公开笔记草稿';
  await dialog.getByLabel('笔记正文', { exact: true }).fill(text); await dialog.getByLabel('关联对象 ID（可留空）', { exact: true }).fill(index.other_target.entity_id);
  const rejected = page.waitForResponse(r => new URL(r.url()).pathname === '/api/records/note'); await dialog.getByRole('button', { name: '保存研究笔记', exact: true }).click(); expect((await rejected).status()).toBe(400);
  await expect(dialog.getByLabel('笔记正文', { exact: true })).toHaveValue(text); await dialog.getByRole('button', { name: '关闭新增笔记', exact: true }).click();
  await page.getByLabel('当前项目', { exact: true }).selectOption(index.second_project); dialog = await form(page); await expect(dialog.getByLabel('笔记正文', { exact: true })).toHaveValue('');
  const secondText = '第二项目390笔记\n<script>字面，不执行</script>';
  await dialog.getByLabel('笔记正文', { exact: true }).fill(secondText); await dialog.getByLabel('关联对象 ID（可留空）', { exact: true }).fill(index.other_target.entity_id); await dialog.getByLabel('笔记范围（可留空）', { exact: true }).fill('data=笔记自有范围\nstep=人工补充');
  await geometry(dialog.getByLabel('笔记正文', { exact: true })); await page.screenshot({ path: '../.cache/frontend-notes-form-mobile.png' });
  const saved = await save(page, dialog), claim = await detail(request, saved.claim_id, 'mobile-note');
  expect(claim).toMatchObject({ scope: { data: '笔记自有范围', step: '人工补充' }, payload: { content: secondText, about: index.other_target.entity_id } });
  const drawer = page.getByRole('dialog', { name: `记录 ${saved.claim_id} 详情`, exact: true }); await geometry(drawer.locator('.claim-content')); await expect(drawer.locator('.note-association')).toContainText(index.other_target.entity_id);
  await page.screenshot({ path: '../.cache/frontend-notes-detail-mobile.png' });
  await page.getByRole('button', { name: '关闭详情', exact: true }).click(); await page.getByLabel('当前项目', { exact: true }).selectOption(index.project_id); dialog = await form(page);
  await expect(dialog.getByLabel('笔记正文', { exact: true })).toHaveValue(text);
  expect((await json(request, `/api/claims?project=${index.second_project}&kind=note`, 'mobile-second-project')).claims.every((c: Claim) => c.payload.content !== text)).toBe(true);
});

test('迟到真实保存成功不能关闭新笔记或清掉新意图草稿，旧记录仍归原项目', async ({ page, request }) => {
  let release = () => {}, first = true; const held = new Promise<void>(resolve => { release = resolve; }); const receipts: NoteResult[] = [];
  await page.route('**/api/records/note', async route => { const response = await route.fetch(); expect(response.status()).toBe(200); receipts.push(await response.json()); if (first) { first = false; await held; } await route.fulfill({ response }); });
  try {
    await enter(page); let dialog = await form(page); await dialog.getByLabel('笔记正文', { exact: true }).fill('旧在途笔记');
    await dialog.getByRole('button', { name: '保存研究笔记', exact: true }).click(); await expect.poll(() => receipts.length).toBe(1);
    await page.keyboard.press('Escape'); await expect(dialog).toHaveCount(0); dialog = await form(page); await dialog.getByLabel('笔记正文', { exact: true }).fill('新的笔记意图草稿');
    const arrived = page.waitForResponse(r => new URL(r.url()).pathname === '/api/records/note'); release(); expect((await arrived).status()).toBe(200);
    await expect(dialog.getByLabel('笔记正文', { exact: true })).toHaveValue('新的笔记意图草稿'); await expect(page.getByRole('dialog', { name: `记录 ${receipts[0].claim_id} 详情`, exact: true })).toHaveCount(0);
    const saved = await save(page, dialog); expect(saved.request_id).not.toBe(receipts[0].request_id);
    const old = await detail(request, receipts[0].claim_id, 'late-old-note'); expect(old.payload.content).toBe('旧在途笔记');
    const latest = await detail(request, saved.claim_id, 'late-new-note'); expect(latest.payload.content).toBe('新的笔记意图草稿');
    expect((await json(request, `/api/claims?project=${index.second_project}&kind=note`, 'late-other-project')).claims.some((c: Claim) => [old.claim_id, latest.claim_id].includes(c.claim_id))).toBe(false);
  } finally { release(); await page.unroute('**/api/records/note'); }
});
