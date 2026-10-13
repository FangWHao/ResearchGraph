import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWriteStream, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import type { EvidenceData, L1Edit } from '../../src/types';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const origin = 'http://127.0.0.1:9803';
const headers = { Authorization: 'Bearer synthetic-multiedit-token' };
const beforeText = '旧 旧\n<script>字面</script>\n';
const afterText = '新 新\n<script>字面</script>\n';
type CaseName = 'valid' | 'mismatch' | 'legacy_mismatch' | 'legacy_unavailable';
interface CaseIdentity { request_event_id: number; result_event_id: number; edit_id: string; saved_after_version?: string }
interface Index { project_id: string; cases: Record<CaseName, CaseIdentity>; synthetic_only: boolean }
let server: ChildProcess | undefined;
let index: Index;
let captured = 0;
function capture(name: string, value: unknown) {
  const directory = `${root}/.cache/frontend-multiedit-api`;
  mkdirSync(directory, { recursive: true });
  writeFileSync(`${directory}/${String(++captured).padStart(3, '0')}-${name}.json`, JSON.stringify(value, null, 2) + '\n');
}

test.beforeAll(async () => {
  let existing: Response | undefined;
  try { existing = await fetch(`${origin}/api/projects`, { headers }); } catch { /* 尚无监听。 */ }
  if (existing) throw new Error('9803 已被占用；保留现有进程，本轮不复用未核对的合成库。');
  mkdirSync(`${root}/.cache`, { recursive: true });
  const log = createWriteStream(`${root}/.cache/frontend-multiedit-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.multiedit_browser', '--port', '9803', '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => {
    if (server?.exitCode !== null) throw new Error('合成服务提前退出，查看原始服务日志。');
    try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; }
  }).toBe(200);
  index = JSON.parse(readFileSync(`${root}/.cache/frontend-multiedit-index.json`, 'utf8'));
  expect(index.synthetic_only).toBe(true);
  expect(Object.keys(index.cases).sort()).toEqual(['legacy_mismatch', 'legacy_unavailable', 'mismatch', 'valid']);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) {
    const closed = new Promise<number | null>(resolve => server!.once('exit', code => resolve(code)));
    server.kill('SIGINT'); expect(await closed).toBe(0);
    expect(JSON.parse(readFileSync(`${root}/.cache/frontend-multiedit-audit.json`, 'utf8'))).toMatchObject({ 合成库逐表导出未改变: true });
  }
});
async function init(page: Page, request: APIRequestContext) {
  const response = await request.get(`${origin}/api/projects`, { headers }); expect(response.status()).toBe(200);
  const projects = (await response.json()).projects;
  expect(projects).toHaveLength(1); expect(projects[0].project_id).toBe(index.project_id);
  await page.goto(`${origin}/#token=synthetic-multiedit-token`);
  await page.getByLabel('当前项目', { exact: true }).selectOption(index.project_id);
}
async function evidence(request: APIRequestContext, event: number) {
  const response = await request.get(`${origin}/api/evidence/${event}?context=0`, { headers });
  expect(response.status()).toBe(200); const body = await response.json() as EvidenceData;
  capture(`evidence-${event}`, body); return body;
}
function editCard(page: Page, id: string) { return page.locator(`.l1-card[data-edit-id=${JSON.stringify(id)}]`); }
async function open(page: Page, event: number) {
  const dialog = page.getByRole('dialog', { name: '原文证据', exact: true });
  if (await dialog.isVisible()) await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await page.getByLabel('搜索会话原文').fill(''); await page.getByRole('button', { name: '开始搜索', exact: true }).click();
  await page.getByLabel('全库事件编号', { exact: true }).fill(String(event));
  await page.getByRole('button', { name: '按编号打开原文', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: `原文 #${event}`, exact: true })).toBeVisible();
  return dialog;
}
async function source(page: Page, request: APIRequestContext, event: number) {
  const data = await evidence(request, event), raw = data.event.before + data.event.quote + data.event.after;
  expect(data.event.window_truncated).toBe(false);
  await expect(page.locator('.raw-event.focused header strong')).toHaveText(`事件 #${event}`);
  expect(await page.locator('.raw-event.focused pre').textContent()).toBe(raw);
  const response = await request.get(`${origin}/api/evidence/${event}?context=0&start=0&end=${data.event.total_bytes}`, { headers });
  expect(response.status()).toBe(200); const full = await response.json();
  capture(`source-window-${event}`, full);
  expect(full.event.quote).toBe(raw); expect(Buffer.byteLength(raw)).toBe(data.event.total_bytes);
  expect(createHash('sha256').update(raw).digest('hex')).toBe(full.event.quote_sha256);
  return JSON.parse(raw);
}
async function graph(request: APIRequestContext) {
  const response = await request.get(`${origin}/api/graph?project=${index.project_id}`, { headers });
  expect(response.status()).toBe(200); const body = await response.json();
  capture('graph', body); return body;
}
async function checkEdit(request: APIRequestContext, name: CaseName) {
  const identity = index.cases[name], data = await evidence(request, identity.result_event_id);
  expect(data.l1!.edits).toHaveLength(1); const edit = data.l1!.edits[0];
  expect(edit).toMatchObject({ edit_id: identity.edit_id, operation: 'multiedit', project_id: index.project_id, request_event_id: identity.request_event_id, result_event_id: identity.result_event_id });
  expect(edit.request_validation?.basis).toBe('saved_request_and_reported_versions');
  expect(data.artifact_versions.every(version => version.claim_state === 'candidate'
    && version.source === 'agent_edit' && version.basis === 'direct_record'
    && version.representation === 'tool_reported_utf8')).toBe(true);
  expect(data.artifact_versions.find(version => version.version_id === edit.before_version))
    .toMatchObject({ phase: 'before', digest: createHash('sha256').update(beforeText).digest('hex') });
  if (identity.saved_after_version) {
    expect(data.artifact_versions.find(version => version.version_id === identity.saved_after_version))
      .toMatchObject({ phase: 'after', claim_state: 'candidate', digest: createHash('sha256').update(afterText).digest('hex') });
  }
  return { identity, data, edit };
}
async function visibleDiagnostic(page: Page, edit: L1Edit) {
  const diagnostic = editCard(page, edit.edit_id).locator('[data-request-validation]');
  await diagnostic.evaluate(element => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
  await expect.poll(() => diagnostic.evaluate(element => {
    const box = element.getBoundingClientRect();
    const dialog = element.closest('[role="dialog"]')!;
    const header = dialog.querySelector('header')?.getBoundingClientRect();
    return box.width > 0 && box.height > 0 && box.left >= 0 && box.right <= innerWidth
      && box.top >= (header?.bottom ?? 0) && box.bottom <= innerHeight;
  })).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

test('真实顺序替换与 replace_all 核验一致，前后候选和两份原文保留，HTML仅文本', async ({ page, request }) => {
  await init(page, request); const initial = await graph(request);
  const { identity, data, edit } = await checkEdit(request, 'valid');
  expect(edit).toMatchObject({ gap: null, request_validation: { status: 'matches_request' }, diff: { available: true, format: 'reported_versions', complete_versions: true } });
  expect(edit.before_version).toBeTruthy(); expect(edit.after_version).toBeTruthy();
  expect(edit.reported_after_version).toBe(edit.after_version);
  expect(data.artifact_versions).toHaveLength(2);
  expect(data.artifact_versions.find(version => version.phase === 'before')?.digest).toBe(createHash('sha256').update(beforeText).digest('hex'));
  expect(data.artifact_versions.find(version => version.phase === 'after')?.digest).toBe(createHash('sha256').update(afterText).digest('hex'));
  await open(page, identity.result_event_id); const result = await source(page, request, identity.result_event_id);
  expect(result.toolUseResult.originalFile).toBe(beforeText);
  const card = editCard(page, edit.edit_id);
  await expect(card.locator('[data-request-validation]')).toHaveAttribute('data-request-validation', 'matches_request');
  expect(await card.getByLabel('候选前后版本差异正文').textContent()).toBe(edit.diff.text);
  expect(await card.locator('pre script').count()).toBe(0);
  await visibleDiagnostic(page, edit); await page.screenshot({ path: '../.cache/frontend-multiedit-valid-desktop.png' });
  await card.getByRole('button', { name: `编辑请求原文 #${identity.request_event_id}`, exact: true }).click();
  const call = await source(page, request, identity.request_event_id);
  expect(call.message.content[0].input.edits).toEqual([{ old_string: '旧', new_string: '中', replace_all: true }, { old_string: '中', new_string: '新', replace_all: true }]);
  await card.getByRole('button', { name: `编辑结果原文 #${identity.result_event_id}`, exact: true }).click();
  await source(page, request, identity.result_event_id);
  const physical = await request.get(`${origin}/api/version-diff?project=${index.project_id}&before_version_id=${edit.before_version}&after_version_id=${edit.after_version}`, { headers });
  expect(physical.status()).toBe(200);
  const physicalBody = await physical.json(); capture('physical-diff-unavailable', physicalBody);
  expect(physicalBody).toMatchObject({ byte_identity: 'unverified', mode_changed: null, diff: { available: false, reason: 'unsupported_source' } });
  expect(await graph(request)).toEqual(initial);
});

test('请求不一致的新记录仅有前候选，旧after保留历史编号且不再作为完整后版本', async ({ page, request }) => {
  await init(page, request);
  for (const name of ['mismatch', 'legacy_mismatch'] as const) {
    const { identity, data, edit } = await checkEdit(request, name);
    expect(edit).toMatchObject({ after_version: null, gap: 'reported_edit_disagrees_with_request', request_validation: { status: 'mismatch' }, diff: { available: true, format: 'reported_before', text: beforeText, complete_versions: false } });
    expect(edit.before_version).toBeTruthy();
    expect(edit.reported_after_version).toBe(name === 'mismatch' ? null : identity.saved_after_version);
    expect(data.artifact_versions.some(version => version.version_id === edit.before_version)).toBe(true);
    await open(page, identity.result_event_id); await source(page, request, identity.result_event_id);
    const card = editCard(page, edit.edit_id);
    await expect(card.locator('[data-request-validation]')).toHaveAttribute('data-request-validation', 'mismatch');
    expect(await card.getByLabel('工具报告的候选编辑前全文').textContent()).toBe(beforeText);
    await expect(card.getByLabel('候选前后版本差异正文')).toHaveCount(0);
    await expect(card.getByLabel('工具报告的候选编辑后全文')).toHaveCount(0);
    if (name === 'legacy_mismatch') {
      const historical = card.locator('[data-reported-after-version]');
      await expect(historical).toHaveAttribute('data-reported-after-version', identity.saved_after_version!);
      await expect(historical).toHaveText(identity.saved_after_version!);
      await visibleDiagnostic(page, edit); await page.screenshot({ path: '../.cache/frontend-multiedit-legacy-desktop.png' });
    } else await expect(card.locator('[data-reported-after-version]')).toHaveCount(0);
  }
});

test('旧请求不可核验保持候选身份，历史双截止限定诊断与原文字节，查询不写研究记录', async ({ page, request }) => {
  await init(page, request); const initial = await graph(request);
  const { identity, edit } = await checkEdit(request, 'legacy_unavailable');
  expect(edit).toMatchObject({ after_version: null, reported_after_version: identity.saved_after_version, gap: 'reported_edit_request_unsupported', request_validation: { status: 'unavailable' }, diff: { format: 'reported_before', text: beforeText, complete_versions: false } });
  await open(page, identity.result_event_id); await source(page, request, identity.result_event_id);
  await expect(editCard(page, edit.edit_id).locator('[data-request-validation]')).toHaveAttribute('data-request-validation', 'unavailable');
  const query = new URLSearchParams({ project: index.project_id, collection: 'nodes', limit: '100' });
  const response = await request.get(`${origin}/api/l1-graph?${query}`, { headers }); expect(response.status()).toBe(200);
  const first = await response.json(); capture('l1-first-reading', first); expect(first.next_offset).toBeNull(); expect(first.items).toHaveLength(first.total);
  query.set('occurred_until', first.occurred_until); query.set('known_until', first.known_until); query.set('expected_revision', String(first.revision));
  const historical = await request.get(`${origin}/api/l1-graph?${query}`, { headers }); expect(historical.status()).toBe(200);
  const historicalBody = await historical.json(); capture('l1-fixed-reading', historicalBody);
  const nodes = historicalBody.items;
  const saved = nodes.find((node: { kind: string; record: { edit_id: string } }) => node.kind === 'edit_record' && node.record.edit_id === identity.edit_id);
  expect(saved.record).toMatchObject({ after_version: null, reported_after_version: identity.saved_after_version, request_validation: { status: 'unavailable' } });
  query.set('known_until', '2025-12-31T23:59:59+00:00');
  const unknownThen = await request.get(`${origin}/api/l1-graph?${query}`, { headers }); expect(unknownThen.status()).toBe(200);
  const unknownBody = await unknownThen.json(); capture('l1-before-known', unknownBody); expect(unknownBody.items).toEqual([]);
  const call = await evidence(request, identity.request_event_id);
  query.set('known_until', first.known_until); query.set('occurred_until', call.event.occurred_at!);
  const beforeResult = await request.get(`${origin}/api/l1-graph?${query}`, { headers }); expect(beforeResult.status()).toBe(200);
  const beforeResultBody = await beforeResult.json(); capture('l1-before-result', beforeResultBody);
  expect(beforeResultBody.items.some((node: { kind: string; record: { edit_id: string } }) => node.kind === 'edit_record' && node.record.edit_id === identity.edit_id)).toBe(false);
  const rawQuery = new URLSearchParams({ project: index.project_id, expected_revision: String(first.revision), occurred_until: first.occurred_until, known_until: first.known_until, context: '0' });
  const raw = await request.get(`${origin}/api/evidence/${identity.result_event_id}?${rawQuery}`, { headers }); expect(raw.status()).toBe(200);
  const body = await raw.json(); capture('historical-event', body); expect(body.history_context).toBe(true); expect(body.derived_context_loaded).toBe(false); expect(body).not.toHaveProperty('l1');
  const text = body.event.before + body.event.quote + body.event.after;
  rawQuery.set('start', '0'); rawQuery.set('end', String(body.event.total_bytes));
  const quoted = await request.get(`${origin}/api/evidence/${identity.result_event_id}?${rawQuery}`, { headers }); expect(quoted.status()).toBe(200);
  const quotedBody = await quoted.json(); capture('historical-source-window', quotedBody);
  const window = quotedBody.event; expect(window.quote).toBe(text); expect(createHash('sha256').update(text).digest('hex')).toBe(window.quote_sha256);
  rawQuery.set('occurred_until', call.event.occurred_at!);
  const hidden = await request.get(`${origin}/api/evidence/${identity.result_event_id}?${rawQuery}`, { headers });
  expect(hidden.status()).toBe(404); capture('historical-hidden-event', { status: hidden.status(), body: await hidden.json() });
  expect(await graph(request)).toEqual(initial);
});

test('390直接打开四类核验与历史编号，诊断在固定标题下可见，候选正文无横溢', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await init(page, request);
  for (const name of ['valid', 'mismatch', 'legacy_mismatch', 'legacy_unavailable'] as const) {
    const { identity, edit } = await checkEdit(request, name); await open(page, identity.result_event_id);
    await visibleDiagnostic(page, edit); const card = editCard(page, edit.edit_id);
    expect(await card.locator('.l1-diff').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect(await card.locator('pre script').count()).toBe(0);
    if (identity.saved_after_version) {
      const version = card.locator('[data-reported-after-version]'); await expect(version).toHaveText(identity.saved_after_version);
      await version.evaluate(element => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
      expect(await version.evaluate(element => {
        const box = element.getBoundingClientRect();
        const headerBottom = element.closest('[role="dialog"]')!.querySelector('header')!.getBoundingClientRect().bottom;
        return box.width > 0 && box.height > 0 && box.left >= 0 && box.right <= innerWidth
          && box.top >= headerBottom && box.bottom <= innerHeight;
      })).toBe(true);
      expect(await version.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    }
    if (name !== 'mismatch') await page.screenshot({ path: `../.cache/frontend-multiedit-${name}-mobile.png` });
    await card.getByRole('button', { name: `编辑请求原文 #${identity.request_event_id}`, exact: true }).click();
    await source(page, request, identity.request_event_id);
  }
});
