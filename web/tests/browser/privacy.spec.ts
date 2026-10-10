import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { parseQaContext } from '../../src/qa';
import type { QaPacket, QaPreview } from '../../src/qa';
import type { PrivacyPolicy } from '../../src/privacy';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const primary = '验收遮盖项目'; const secondary = '验收遮盖项目二';
const rules = 'HOSP-\\d+\nCASE-\\d+\nSAMPLE-[A-Z0-9]+';
async function policy(request: APIRequestContext, project: string) { return (await (await request.get(`/api/privacy?project=${project}`, { headers })).json()) as PrivacyPolicy; }
async function configure(request: APIRequestContext, project: string, patterns: string[]) {
  const current = await policy(request, project);
  const response = await request.post('/api/privacy', { headers, data: { project_id: project, patterns, actor: 'human:合成遮盖验收', expected_revision: current.revision } });
  expect(response.status()).toBe(200); return response.json() as Promise<PrivacyPolicy>;
}
async function open(page: Page, request: APIRequestContext, name = primary) {
  const projects = (await (await request.get('/api/projects', { headers })).json()).projects;
  const project = projects.find((p: { name: string }) => p.name === name).project_id;
  await configure(request, project, []);
  const options = await (await request.get(`/api/qa/options?project=${project}`, { headers })).json();
  if (options.remote_allowed) expect((await request.post('/api/qa/disable-remote', { headers, data: { project_id: project, expected_revision: options.revision } })).status()).toBe(200);
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: name });
  await page.getByRole('button', { name: '研究问答', exact: true }).click();
  const panel = page.getByRole('region', { name: '项目编号遮盖配置', exact: true });
  await panel.locator('summary').click(); await expect(panel.getByRole('button', { name: '保存项目规则', exact: true })).toBeEnabled();
  await expect(page.getByText('服务端模型：synthetic-qa-model', { exact: false })).toBeVisible();
  return { project, panel };
}
async function save(page: Page, text = rules) {
  const panel = page.getByRole('region', { name: '项目编号遮盖配置', exact: true });
  await panel.getByLabel('项目遮盖正则规则', { exact: true }).fill(text);
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/privacy' && r.request().method() === 'POST');
  await panel.getByRole('button', { name: '保存项目规则', exact: true }).click();
  const received = await response; expect(received.status()).toBe(200);
  await expect(panel.getByText('项目规则已保存。', { exact: false })).toBeVisible();
  await expect(panel.getByRole('button', { name: '保存项目规则', exact: true })).toBeEnabled();
  return received.json() as Promise<PrivacyPolicy>;
}
async function preview(page: Page) {
  await page.getByLabel('研究问答问题', { exact: true }).fill('遮盖问答');
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/qa/preview');
  await page.getByRole('button', { name: '查找答案', exact: true }).click();
  const received = await response; expect(received.status()).toBe(200);
  await expect(page.getByRole('region', { name: '首次外发授权' })).toBeVisible();
  return parseQaContext((await received.json()).context_text) as unknown as QaPreview;
}

test('真实项目规则保存使旧预览失效，发送副本遮盖临床编号，L0与断言状态不变', async ({ page, request }) => {
  const { project, panel } = await open(page, request);
  const oldPreview = await preview(page);
  expect(JSON.stringify(oldPreview.input)).toContain('HOSP-246810');
  await page.getByRole('region', { name: '首次外发授权' }).getByRole('checkbox').check();
  const before = await (await request.get(`/api/graph?project=${project}`, { headers })).json();
  const source = page.getByTestId('qa-source').filter({ hasText: 'HOSP-246810' }).first();
  await source.getByRole('button', { name: '打开此处原文', exact: false }).click();
  const rawBefore = await page.getByTestId('evidence-quote').textContent();
  await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  const saved = await save(page);
  expect(saved.patterns).toEqual(rules.split('\n')); expect(saved.revision).toBeGreaterThan(before.revision);
  await expect(page.getByRole('region', { name: '首次外发授权' })).toHaveCount(0);
  await expect(panel.locator('.privacy-saved')).toContainText('HOSP-\\d+');
  const next = await preview(page); expect(next.preview_sha).not.toBe(oldPreview.preview_sha);
  for (const identifier of ['HOSP-246810', 'CASE-135790', 'SAMPLE-ABC123']) expect(JSON.stringify(next.input)).not.toContain(identifier);
  expect(next.input.sources.some(s => s.text_redacted)).toBe(true);
  await expect(page.getByRole('region', { name: '首次外发授权' }).getByRole('checkbox')).not.toBeChecked();
  await source.getByRole('button', { name: '打开此处原文', exact: false }).click();
  await expect(page.getByTestId('evidence-quote')).toHaveText(rawBefore!);
  const after = await (await request.get(`/api/graph?project=${project}`, { headers })).json(); expect(after.claims).toEqual(before.claims);
  expect(await page.evaluate(() => (window as typeof window & { privacyInjected?: boolean }).privacyInjected)).toBeUndefined();
});

test('真实外发许可不因保存关闭，旧解释保留并标规则变化，新回答和ZIP使用新规则', async ({ page, request }) => {
  const { project, panel } = await open(page, request); await preview(page);
  await page.getByRole('region', { name: '首次外发授权' }).getByRole('checkbox').check();
  let answer = page.waitForResponse(r => new URL(r.url()).pathname === '/api/qa/answer');
  await page.getByRole('button', { name: '开启项目外发并回答', exact: true }).click(); expect((await answer).status()).toBe(200);
  await expect(page.getByRole('region', { name: '模型解释', exact: true })).toBeVisible();
  const saved = await save(page);
  expect((await (await request.get(`/api/qa/options?project=${project}`, { headers })).json()).remote_allowed).toBe(true);
  await expect(page.getByText('下方结果使用此前的项目遮盖规则', { exact: false })).toBeVisible();
  answer = page.waitForResponse(r => new URL(r.url()).pathname === '/api/qa/answer');
  await page.getByRole('button', { name: '查找答案', exact: true }).click();
  const packet = parseQaContext((await (await answer).json()).context_text) as unknown as QaPacket;
  expect(packet.status).toBe('answered'); expect(JSON.stringify(packet.statements)).not.toContain('HOSP-246810');
  await expect(page.getByText('下方结果使用此前的项目遮盖规则', { exact: false })).toHaveCount(0);
  await page.getByRole('button', { name: '历史导出', exact: true }).click();
  await expect(panel.getByLabel('项目遮盖正则规则', { exact: true })).toHaveValue(rules);
  await page.getByRole('checkbox', { name: '加入遮盖后的证据片段', exact: true }).check();
  const download = page.waitForEvent('download'); const exported = page.waitForResponse(r => new URL(r.url()).pathname === '/api/exports');
  await page.getByRole('button', { name: '下载历史 ZIP', exact: true }).click();
  const response = await exported; expect(response.status()).toBe(200); expect(response.request().postDataJSON()).toMatchObject({ expected_revision: saved.revision, project_id: project });
  const path = '../.cache/frontend-privacy-rules.zip'; await (await download).saveAs(path);
  const python = fileURLToPath(new URL('../../../.venv/bin/python', import.meta.url));
  const contents = execFileSync(python, ['-c', 'import sys,zipfile\nwith zipfile.ZipFile(sys.argv[1]) as z:\n print("\\n".join(z.read(n).decode() for n in z.namelist()))', path], { encoding: 'utf-8' });
  for (const identifier of ['HOSP-246810', 'CASE-135790', 'SAMPLE-ABC123']) expect(contents).not.toContain(identifier);
  expect(contents).toContain(saved.policy_id);
  await page.screenshot({ path: '../.cache/frontend-privacy-export-desktop.png', fullPage: true });
});

test('真实400与409保留草稿，读取新版本不会自动保存；清空仅删除额外项目规则', async ({ page, request }) => {
  const { project, panel } = await open(page, request);
  await panel.getByLabel('项目遮盖正则规则', { exact: true }).fill('[');
  let response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/privacy' && r.request().method() === 'POST');
  await panel.getByRole('button', { name: '保存项目规则', exact: true }).click(); expect((await response).status()).toBe(400);
  await expect(panel.getByRole('alert')).toContainText('正则无效'); await expect(panel.getByLabel('项目遮盖正则规则', { exact: true })).toHaveValue('[');
  await panel.getByLabel('项目遮盖正则规则', { exact: true }).fill(rules);
  await configure(request, project, ['OTHER-\\d+']);
  response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/privacy' && r.request().method() === 'POST');
  await panel.getByRole('button', { name: '保存项目规则', exact: true }).click(); expect((await response).status()).toBe(409);
  await expect(panel.getByRole('button', { name: '保存项目规则', exact: true })).toBeDisabled();
  const posts: unknown[] = []; page.on('request', r => { if (new URL(r.url()).pathname === '/api/privacy' && r.method() === 'POST') posts.push(r.postDataJSON()); });
  await panel.getByRole('button', { name: '读取最新规则', exact: true }).click();
  await expect(panel.locator('.privacy-saved')).toContainText('OTHER-\\d+'); await expect(panel.getByRole('button', { name: '保存项目规则', exact: true })).toBeEnabled();
  await expect(panel.getByLabel('项目遮盖正则规则', { exact: true })).toHaveValue(rules); expect(posts).toEqual([]);
  await save(page); const cleared = await save(page, ''); expect(cleared.patterns).toEqual([]); expect(cleared.builtin_masking).toBe(true); expect(cleared.raw_unchanged).toBe(true);
});

test('真实401恢复统一登录，规则草稿不丢且不自动重发', async ({ page, request }) => {
  const { panel } = await open(page, request); await panel.getByLabel('项目遮盖正则规则', { exact: true }).fill(rules);
  await page.route('**/api/privacy', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    const response = await route.fetch({ headers: { ...route.request().headers(), Authorization: 'Bearer invalid-synthetic-token' } }); expect(response.status()).toBe(401); await route.fulfill({ response });
  });
  await panel.getByRole('button', { name: '保存项目规则', exact: true }).click(); await expect(page.getByRole('heading', { name: '打开你的研究决定史' })).toBeVisible();
  await page.unroute('**/api/privacy'); await page.getByLabel('访问令牌', { exact: true }).fill('synthetic-browser-token'); await page.getByRole('button', { name: '进入本地工作区', exact: false }).click();
  await expect(panel.getByLabel('项目遮盖正则规则', { exact: true })).toHaveValue(rules); await expect(panel.locator('.privacy-saved')).toContainText('尚无额外项目规则');
});

test('迟到真实保存回执不清新草稿，项目切换后不把旧规则写入新项目', async ({ page, request }) => {
  const { project, panel } = await open(page, request);
  let ready = false; let release!: () => void; const hold = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/privacy', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    const response = await route.fetch(); expect(response.status()).toBe(200); ready = true; await hold;
    try { await route.fulfill({ response }); } catch { /* 页面停止等待后仍检查持久结果。 */ }
  });
  await panel.getByLabel('项目遮盖正则规则', { exact: true }).fill(rules); await panel.getByRole('button', { name: '保存项目规则', exact: true }).click(); await expect.poll(() => ready).toBe(true);
  await panel.getByLabel('项目遮盖正则规则', { exact: true }).fill('NEW-DRAFT-\\d+'); release();
  await expect(panel.getByRole('button', { name: '保存项目规则', exact: true })).toBeEnabled(); await expect(panel.getByLabel('项目遮盖正则规则', { exact: true })).toHaveValue('NEW-DRAFT-\\d+');
  await page.unroute('**/api/privacy');
  const projects = (await (await request.get('/api/projects', { headers })).json()).projects; const second = projects.find((p: { name: string }) => p.name === secondary).project_id;
  await configure(request, second, []);
  ready = false; const nextHold = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/privacy', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    const response = await route.fetch(); expect(response.status()).toBe(200); ready = true; await nextHold; try { await route.fulfill({ response }); } catch { /* 项目切换会停止页面等待。 */ }
  });
  // 次项目配置推进全局版本；先刷新主项目，再发送并延迟旧响应。
  await panel.getByRole('button', { name: '读取最新规则', exact: true }).click(); await expect(panel.getByRole('button', { name: '保存项目规则', exact: true })).toBeEnabled();
  await panel.getByRole('button', { name: '保存项目规则', exact: true }).click(); await expect.poll(() => ready).toBe(true);
  await panel.getByLabel('遮盖规则所属项目', { exact: true }).selectOption({ label: secondary });
  await expect(panel.getByRole('button', { name: '保存项目规则', exact: true })).toBeEnabled(); await panel.getByLabel('项目遮盖正则规则', { exact: true }).fill('SECOND-DRAFT-\\d+');
  release(); await expect(panel.getByLabel('项目遮盖正则规则', { exact: true })).toHaveValue('SECOND-DRAFT-\\d+');
  expect((await policy(request, second)).patterns).toEqual([]); expect((await policy(request, project)).patterns).toEqual(['NEW-DRAFT-\\d+']);
  await panel.getByLabel('遮盖规则所属项目', { exact: true }).selectOption({ label: primary }); await expect(panel.getByLabel('项目遮盖正则规则', { exact: true })).toHaveValue('NEW-DRAFT-\\d+');
});

test('390小屏可切项目保存并读取真实遮盖预览，规则和原文文字不执行', async ({ page, request }) => {
  const { panel } = await open(page, request); await page.setViewportSize({ width: 390, height: 844 });
  await save(page); await panel.locator('summary').evaluate(node => node.scrollIntoView({ block: 'start' }));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '../.cache/frontend-privacy-mobile.png' });
  await panel.locator('footer').evaluate(node => node.scrollIntoView({ block: 'end' })); await page.screenshot({ path: '../.cache/frontend-privacy-mobile-save.png' });
  await preview(page); const consent = page.getByRole('region', { name: '首次外发授权' });
  await expect(consent.getByTestId('qa-preview')).not.toContainText('HOSP-246810');
  await consent.evaluate(node => node.scrollIntoView({ block: 'start' })); await page.screenshot({ path: '../.cache/frontend-privacy-preview-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1440, height: 1000 }); await panel.locator('summary').evaluate(node => node.scrollIntoView({ block: 'start' }));
  await page.screenshot({ path: '../.cache/frontend-privacy-desktop.png' });
  expect(await page.evaluate(() => (window as typeof window & { privacyInjected?: boolean }).privacyInjected)).toBeUndefined();
});

test('规则保存后迟到的真实预览不能恢复旧样例或勾选，需人工重新查询新预览', async ({ page, request }) => {
  await open(page, request);
  let ready = false; let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; });
  let original: QaPreview | undefined;
  await page.route('**/api/qa/preview', async route => {
    const response = await route.fetch(); expect(response.status()).toBe(200);
    original = parseQaContext((await response.json()).context_text) as unknown as QaPreview; ready = true; await held;
    try { await route.fulfill({ response }); } catch { /* 保存规则后只停止页面等待。 */ }
  });
  await page.getByLabel('研究问答问题', { exact: true }).fill('遮盖问答'); await page.getByRole('button', { name: '查找答案', exact: true }).click(); await expect.poll(() => ready).toBe(true);
  await save(page); release(); await page.unroute('**/api/qa/preview');
  await expect(page.getByRole('region', { name: '首次外发授权' })).toHaveCount(0);
  await expect(page.getByTestId('qa-source')).toHaveCount(1);
  const fresh = await preview(page); expect(fresh.preview_sha).not.toBe(original!.preview_sha); expect(JSON.stringify(fresh.input)).not.toContain('HOSP-246810');
  await expect(page.getByRole('region', { name: '首次外发授权' }).getByRole('checkbox')).not.toBeChecked();
});

test('读取期间更换人工身份会重新读取配置，旧响应不阻塞新身份保存', async ({ page, request }) => {
  await open(page, request);
  const panel = page.getByRole('region', { name: '项目编号遮盖配置', exact: true });
  let ready = false; let first = true; let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/privacy?*', async route => {
    if (!first) return route.continue(); first = false;
    const response = await route.fetch(); expect(response.status()).toBe(200); ready = true; await held;
    try { await route.fulfill({ response }); } catch { /* 旧读取已中止，新身份拥有独立读取。 */ }
  });
  await panel.getByRole('button', { name: '读取最新规则', exact: true }).click(); await expect.poll(() => ready).toBe(true);
  await page.getByLabel('复核者姓名', { exact: true }).fill('新遮盖复核者');
  await expect(panel.getByRole('button', { name: '保存项目规则', exact: true })).toBeEnabled(); release();
  await panel.getByLabel('项目遮盖正则规则', { exact: true }).fill(rules);
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/privacy' && r.request().method() === 'POST');
  await panel.getByRole('button', { name: '保存项目规则', exact: true }).click();
  const received = await response; expect(received.status()).toBe(200); expect(received.request().postDataJSON().actor).toBe('human:新遮盖复核者');
});
