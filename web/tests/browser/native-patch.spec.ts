import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createWriteStream, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import type { EvidenceData, L1Edit } from '../../src/types';

const origin = 'http://127.0.0.1:9795';
const headers = { Authorization: 'Bearer synthetic-native-patch-token' };
const projectName = '验收原生补丁项目';
const root = fileURLToPath(new URL('../../../', import.meta.url));
const cases = { add: [2, 3], delete: [5, 6], update: [8, 9], failed: [11, 12], conflict: [14, 15], waiting: [null, 17], empty: [19, 20] } as const;
let server: ChildProcess | undefined;
test.beforeAll(async () => {
  let existing: Response | undefined;
  try { existing = await fetch(`${origin}/api/projects`, { headers }); } catch { /* 无监听。 */ }
  if (existing) {
    if (!existing.ok) throw new Error('9795 已被其它服务占用，未终止已有进程。');
    const data = await existing.json();
    if (data.projects.length !== 1 || data.projects[0].name !== projectName) throw new Error('9795 不是本轮独立合成服务，未复用。');
    console.info('复用经鉴权与项目核对的现有原生补丁合成服务；不终止其进程。');
    return;
  }
  mkdirSync(`${root}/.cache`, { recursive: true });
  const log = createWriteStream(`${root}/.cache/frontend-native-patch-server.log`);
  server = spawn(`${root}/.venv/bin/python`, ['-m', 'tests.native_patch_browser', '--port', '9795', '--web-dir', `${root}/web/dist`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => {
    if (server?.exitCode !== null) throw new Error('合成服务提前退出，见服务日志。');
    try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; }
  }).toBe(200);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) {
    const closed = new Promise<void>(resolve => server!.once('exit', () => resolve()));
    server.kill('SIGINT'); await closed;
  }
});
async function read(request: APIRequestContext, id: number) {
  const response = await request.get(`${origin}/api/evidence/${id}?context=0`, { headers }); expect(response.status()).toBe(200);
  return await response.json() as EvidenceData;
}
function rawText(data: EvidenceData) { return data.event.before + data.event.quote + data.event.after; }
async function open(page: Page, id: number) {
  const dialog = page.getByRole('dialog', { name: '原文证据', exact: true });
  if (await dialog.isVisible()) await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await page.getByLabel('搜索会话原文').fill(''); await page.getByRole('button', { name: '开始搜索', exact: true }).click();
  await page.getByLabel('全库事件编号', { exact: true }).fill(String(id)); await page.getByRole('button', { name: '按编号打开原文', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: `原文 #${id}`, exact: true })).toBeVisible();
  await expect(dialog.getByRole('heading', { name: '本原文派生状态', exact: true })).toBeVisible(); return dialog;
}
async function init(page: Page, request: APIRequestContext) {
  const response = await request.get(`${origin}/api/projects`, { headers }); expect(response.status()).toBe(200);
  const projects = (await response.json()).projects; expect(projects).toHaveLength(1); expect(projects[0].name).toBe(projectName);
  await page.goto(`${origin}/#token=synthetic-native-patch-token`);
  await page.getByLabel('当前项目', { exact: true }).selectOption(projects[0].project_id);
  return projects[0].project_id as string;
}
function card(page: Page, edit: L1Edit) { return page.locator(`.l1-card[data-edit-id=${JSON.stringify(edit.edit_id)}]`); }
async function original(page: Page, request: APIRequestContext, id: number) {
  const data = await read(request, id); expect(data.event.window_truncated).toBe(false);
  await expect(page.locator('.raw-event.focused pre')).toHaveText(rawText(data));
  expect(await page.locator('.raw-event.focused pre').textContent()).toBe(rawText(data));
  const quote = await (await request.get(`${origin}/api/evidence/${id}?context=0&start=0&end=${data.event.total_bytes}`, { headers })).json();
  expect(quote.event.quote).toBe(rawText(data));
  expect(createHash('sha256').update(quote.event.quote).digest('hex')).toBe(quote.event.quote_sha256);
  expect(Buffer.byteLength(quote.event.quote)).toBe(data.event.total_bytes);
  return JSON.parse(rawText(data));
}
async function geometry(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); }

test('真实原生新增后与删除前全文保持候选，双原文可定位，工具文本不成为物理差异', async ({ page, request }) => {
  const project = await init(page, request); const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  const initial = await (await request.get(`${origin}/api/graph?project=${project}`, { headers })).json(); expect(initial.claims).toEqual([]);
  const sessions: number[] = [];
  for (const name of ['add', 'delete'] as const) {
    const [begin, end] = cases[name]; const data = await read(request, end); const edit = data.l1!.edits[0];
    expect(data.l1!.edits).toHaveLength(1); expect(data.l1!.runs).toEqual([]);
    expect(edit).toMatchObject({ request_event_id: begin, result_event_id: end, gap: 'native_patch_input_missing', diff: { available: true, format: name === 'add' ? 'reported_after' : 'reported_before', complete_versions: false } });
    expect(name === 'add' ? edit.before_version : edit.after_version).toBeNull();
    const version = data.artifact_versions[0]; expect(data.artifact_versions).toHaveLength(1);
    expect(version).toMatchObject({ version_id: name === 'add' ? edit.after_version : edit.before_version, phase: name === 'add' ? 'after' : 'before', source: 'agent_edit', representation: 'tool_reported_utf8', basis: 'direct_record', claim_state: 'candidate', algo: 'sha256:tool-utf8' });
    const dialog = await open(page, end); const endRaw = await original(page, request, end);
    expect(endRaw.payload.type).toBe('patch_apply_end'); expect(endRaw.payload.success).toBe(true);
    const reported = Object.values(endRaw.payload.changes)[0] as { content: string };
    expect(edit.diff.text).toBe(reported.content); expect(createHash('sha256').update(reported.content).digest('hex')).toBe(version.digest);
    const label = name === 'add' ? '工具报告的候选编辑后全文' : '工具报告的候选编辑前全文';
    expect(await card(page, edit).getByLabel(label, { exact: true }).textContent()).toBe(reported.content);
    expect(await card(page, edit).getByLabel(label).locator('script').count()).toBe(0);
    await expect(dialog.getByRole('region', { name: '相关文件版本', exact: true })).toContainText('待复核');
    await card(page, edit).evaluate(n => n.scrollIntoView({ block: 'start', behavior: 'instant' }));
    await page.screenshot({ path: `../.cache/frontend-native-patch-${name}-desktop.png` });
    await card(page, edit).getByRole('button', { name: `编辑请求原文 #${begin}`, exact: true }).click();
    await expect(dialog.getByRole('heading', { name: `原文 #${begin}`, exact: true })).toBeVisible(); const beginRaw = await original(page, request, begin);
    expect(beginRaw.payload.type).toBe('patch_apply_begin'); expect(beginRaw.payload.call_id).toBe(endRaw.payload.call_id);
    const call = await read(request, begin); expect(call.event.session_pk).toBe(data.event.session_pk); expect(call.l1!.edits[0]).toEqual(edit); sessions.push(data.event.session_pk);
    await card(page, edit).getByRole('button', { name: `编辑结果原文 #${end}`, exact: true }).click(); await expect(dialog.getByRole('heading', { name: `原文 #${end}`, exact: true })).toBeVisible();
    const diff = await request.get(`${origin}/api/version-diff?project=${project}&before_version_id=${version.version_id}&after_version_id=${version.version_id}`, { headers }); expect(diff.status()).toBe(200);
    expect(await diff.json()).toMatchObject({ byte_identity: 'unverified', mode_changed: null, diff: { available: false, reason: 'unsupported_source', text: null } });
  }
  expect(new Set(sessions).size).toBe(2); expect(errors).toEqual([]);
  expect(await (await request.get(`${origin}/api/graph?project=${project}`, { headers })).json()).toEqual(initial);
});

test('失败新增不生成候选后全文，失败删除保留前报告；矛盾和缺开始仍有原文与缺口', async ({ page, request }) => {
  await init(page, request);
  const data = await read(request, cases.failed[1]); expect(data.l1!.edits).toHaveLength(2);
  const added = data.l1!.edits.find(e => e.operation === 'add')!, deleted = data.l1!.edits.find(e => e.operation === 'delete')!;
  expect(added).toMatchObject({ before_version: null, after_version: null, gap: 'native_patch_failed', diff: { format: 'patch_only', complete_versions: false } });
  expect(deleted.before_version).toBeTruthy(); expect(deleted).toMatchObject({ after_version: null, gap: 'native_patch_failed', diff: { format: 'reported_before', text: '失败删除仍有原文', complete_versions: false } });
  expect(data.artifact_versions).toHaveLength(1); expect(data.artifact_versions[0]).toMatchObject({ version_id: deleted.before_version, phase: 'before', claim_state: 'candidate' });
  await open(page, cases.failed[1]); await original(page, request, cases.failed[1]);
  await expect(card(page, added)).toContainText('native_patch_failed'); await expect(card(page, added).getByLabel('工具报告的候选编辑后全文')).toHaveCount(0);
  expect(await card(page, deleted).getByLabel('工具报告的候选编辑前全文').textContent()).toBe(deleted.diff.text);
  await card(page, added).getByRole('button', { name: `编辑请求原文 #${cases.failed[0]}`, exact: true }).click();
  await original(page, request, cases.failed[0]);
  await card(page, added).getByRole('button', { name: `编辑结果原文 #${cases.failed[1]}`, exact: true }).click();
  await original(page, request, cases.failed[1]);
  const conflict = await read(request, cases.conflict[1]); expect(conflict.artifact_versions).toEqual([]);
  expect(conflict.l1!.edits[0]).toMatchObject({ before_version: null, after_version: null, gap: 'native_patch_metadata_conflict', diff: { format: 'patch_only' } });
  const dialog = await open(page, cases.conflict[1]); await original(page, request, cases.conflict[1]);
  await expect(card(page, conflict.l1!.edits[0])).toContainText('native_patch_metadata_conflict');
  await card(page, conflict.l1!.edits[0]).getByRole('button', { name: `编辑请求原文 #${cases.conflict[0]}`, exact: true }).click(); await original(page, request, cases.conflict[0]);
  const waiting = await read(request, cases.waiting[1]); expect(waiting.l1).toMatchObject({ edits: [], runs: [], derivation: { state: 'waiting', error: 'call_not_recorded' } }); expect(waiting.artifact_versions).toEqual([]);
  await open(page, cases.waiting[1]); await original(page, request, cases.waiting[1]); await expect(dialog.getByRole('region', { name: '本原文派生状态' })).toContainText('等待关联');
});

test('原生更新移动只展示有源结构化补丁，空删除前报告不补造after或确认', async ({ page, request }) => {
  await init(page, request); const data = await read(request, cases.update[1]); const edit = data.l1!.edits[0];
  expect(edit).toMatchObject({ before_version: null, after_version: null, operation: 'update', diff: { format: 'patch_only', complete_versions: false } }); expect(data.artifact_versions).toEqual([]);
  const reported = JSON.parse(edit.diff.text!); expect(reported['旧路径.py']).toMatchObject({ type: 'update', move_path: '新路径.py', unified_diff: '@@ -1 +1 @@\n-old\n+new\n' });
  await open(page, cases.update[1]); await original(page, request, cases.update[1]);
  expect(await card(page, edit).getByLabel('工具报告的补丁正文').textContent()).toBe(edit.diff.text); await expect(card(page, edit)).toContainText('不能代表完整文件内容');
  expect(edit.patch_sha256).toMatch(/^[a-f0-9]{64}$/); await card(page, edit).locator('summary').click(); await expect(card(page, edit)).toContainText(edit.patch_sha256!);
  const empty = await read(request, cases.empty[1]); const emptyEdit = empty.l1!.edits[0];
  expect(emptyEdit).toMatchObject({ after_version: null, diff: { format: 'reported_before', text: '', complete_versions: false } }); expect(emptyEdit.before_version).toBeTruthy();
  expect(empty.artifact_versions[0]).toMatchObject({ phase: 'before', claim_state: 'candidate', digest: createHash('sha256').update('').digest('hex') });
  await open(page, cases.empty[1]); const raw = await original(page, request, cases.empty[1]); expect(raw.payload.changes['空全文.py'].content).toBe(''); await expect(card(page, emptyEdit).getByLabel('工具报告的候选编辑前全文')).toContainText('编辑后版本未知');
});

test('390屏单侧候选全文、结构化补丁与请求结果导航可读，HTML保持文本', async ({ page, request }) => {
  await init(page, request); await page.setViewportSize({ width: 390, height: 844 });
  for (const name of ['delete', 'add', 'update'] as const) {
    const [begin, end] = cases[name]; const data = await read(request, end); const edit = data.l1!.edits[0]; const dialog = await open(page, end);
    await card(page, edit).evaluate(n => n.scrollIntoView({ block: 'start', behavior: 'instant' })); await geometry(page);
    expect(await card(page, edit).locator('.l1-diff').evaluate(n => n.scrollWidth <= n.clientWidth)).toBe(true);
    await page.screenshot({ path: `../.cache/frontend-native-patch-${name}-mobile.png` });
    await card(page, edit).getByRole('button', { name: `编辑请求原文 #${begin}`, exact: true }).click(); await expect(dialog.getByRole('heading', { name: `原文 #${begin}`, exact: true })).toBeVisible(); await original(page, request, begin);
    await card(page, edit).getByRole('button', { name: `编辑结果原文 #${end}`, exact: true }).click(); await expect(dialog.getByRole('heading', { name: `原文 #${end}`, exact: true })).toBeVisible(); await original(page, request, end);
    expect(await dialog.locator('pre script').count()).toBe(0);
  }
});
