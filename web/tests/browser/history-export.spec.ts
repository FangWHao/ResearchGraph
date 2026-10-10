import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Download, Page } from '@playwright/test';
import type { ExportRequest } from '../../src/historyExport';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const primary = '验收问答项目'; const secondary = '验收问答项目二';
const python = fileURLToPath(new URL('../../../.venv/bin/python', import.meta.url));
interface Archive { names: string[]; verified: boolean; files: Record<string, any> }
async function unpack(download: Download, name: string): Promise<Archive> {
  const path = `../.cache/frontend-export-${name}.zip`; await download.saveAs(path);
  const code = "import sys,json,zipfile,hashlib\nwith zipfile.ZipFile(sys.argv[1]) as z:\n raw={n:z.read(n) for n in z.namelist()}\n files={n:json.loads(v) for n,v in raw.items() if n.endswith('.json')}\n checks=files['manifest.json']['files']\n verified=all(len(raw[n])==c['bytes'] and hashlib.sha256(raw[n]).hexdigest()==c['sha256'] for n,c in checks.items())\n print(json.dumps({'names':list(raw),'verified':verified,'files':files},ensure_ascii=False))";
  return JSON.parse(execFileSync(python, ['-c', code, path], { encoding: 'utf-8' }));
}
async function open(page: Page, name = primary) {
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: name });
  await page.getByRole('button', { name: '历史导出', exact: true }).click();
  await expect(page.getByRole('region', { name: '历史导出阅读条件', exact: true })).toContainText(name);
  await expect(page.getByRole('button', { name: '下载历史 ZIP', exact: true })).toBeEnabled();
  return page.getByLabel('当前项目', { exact: true }).inputValue();
}
async function download(page: Page, name: string) {
  const received = page.waitForResponse(r => new URL(r.url()).pathname === '/api/exports');
  const file = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载历史 ZIP', exact: true }).click();
  const response = await received; expect(response.status()).toBe(200); expect(response.headers()['content-type']).toBe('application/zip');
  const result = await file; const archive = await unpack(result, name);
  expect(archive.verified).toBe(true);
  return { archive, body: response.request().postDataJSON() as ExportRequest, download: result };
}
async function appendQuestion(request: APIRequestContext, project: string, text: string, scope: Record<string, string> | null = null) {
  const graph = await (await request.get(`/api/graph?project=${project}`, { headers })).json();
  const result = await request.post('/api/records/question', { headers, data: { project_id: project, text, scope, actor: 'human:合成导出验收', expected_revision: graph.revision, request_id: crypto.randomUUID() } });
  expect(result.status()).toBe(200); return result.json();
}

test('真实默认ZIP保留全条件图与独立状态，仅引用不含正文，只读且回收临时URL', async ({ page, request }) => {
  await page.addInitScript(() => {
    const state = { created: [] as string[], revoked: [] as string[] }; (window as any).exportURLs = state;
    const create = URL.createObjectURL.bind(URL); const revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = blob => { const value = create(blob); state.created.push(value); return value; };
    URL.revokeObjectURL = value => { state.revoked.push(value); revoke(value); };
  });
  const project = await open(page); const before = await (await request.get(`/api/graph?project=${project}`, { headers })).json();
  await expect(page.getByRole('checkbox', { name: '加入遮盖后的证据片段', exact: true })).not.toBeChecked();
  await page.screenshot({ path: '../.cache/frontend-history-export-desktop.png', fullPage: true });
  const calls: string[] = []; page.on('request', r => { if (/\/api\/qa\/(answer|preview|allow-remote)$/.test(new URL(r.url()).pathname)) calls.push(r.url()); });
  const { archive, body, download: file } = await download(page, 'default');
  expect(body).toEqual({ project_id: project, include_evidence: false, expected_revision: before.revision });
  expect(file.suggestedFilename()).toContain(`-r${before.revision}.zip`);
  const manifest = archive.files['manifest.json'];
  expect(manifest).toMatchObject({ include_evidence: false, complete_raw_sessions: false, binary_data: false, is_backup: false, is_reproduction_bundle: false, conditions: { project_id: project, revision: before.revision, scope_filter: false } });
  expect(archive.names).toHaveLength(11); expect(archive.files['graph.json'].projection).toBe(false);
  expect(archive.files['claims.json'].map((claim: any) => claim.claim_id).sort((a: number, b: number) => a - b)).toEqual(before.claims.map((claim: any) => claim.claim_id).sort((a: number, b: number) => a - b));
  expect(archive.files['claims.json'].some((claim: any) => claim.effective_state === 'candidate')).toBe(true);
  expect(archive.files['evidence.json'].length).toBeGreaterThan(0);
  expect(archive.files['evidence.json'].every((span: any) => !Object.hasOwn(span, 'text') && span.verification === 'not_read')).toBe(true);
  await expect.poll(() => page.evaluate(() => (window as any).exportURLs.revoked.length)).toBe(1);
  expect(await page.evaluate(() => (window as any).exportURLs.created)).toEqual(await page.evaluate(() => (window as any).exportURLs.revoked));
  const after = await (await request.get(`/api/graph?project=${project}`, { headers })).json();
  expect(after.revision).toBe(before.revision); expect(after.claims).toEqual(before.claims); expect(calls).toEqual([]);
});

test('真实显式片段和临床编号规则等长遮盖，原库与引用hash保留，小屏可下载', async ({ page, request }) => {
  const project = await open(page);
  const saved = await appendQuestion(request, project, '合成导出研究问题 ZY654321 contact-export@example.invalid。');
  await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  await expect(page.getByRole('button', { name: '下载历史 ZIP', exact: true })).toBeEnabled();
  await page.getByRole('checkbox', { name: '加入遮盖后的证据片段', exact: true }).check();
  await page.locator('.export-privacy summary').click();
  await page.getByLabel('导出自定义遮盖规则', { exact: true }).fill('ZY\\d+');
  await page.screenshot({ path: '../.cache/frontend-history-export-privacy-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.export-privacy').scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-history-export-mobile.png', fullPage: true });
  await page.locator('.export-form footer').evaluate(node => node.scrollIntoView({ block: 'end' }));
  await page.screenshot({ path: '../.cache/frontend-history-export-mobile-privacy.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const { archive, body } = await download(page, 'evidence');
  expect(body).toMatchObject({ include_evidence: true, redact_patterns: ['ZY\\d+'] });
  expect(archive.files['manifest.json'].privacy.custom_patterns_count).toBe(1);
  const all = JSON.stringify(archive.files); expect(all).not.toContain('ZY654321'); expect(all).not.toContain('contact-export@example.invalid'); expect(all).not.toContain('qa-fixture@example.invalid');
  const span = archive.files['evidence.json'].find((value: any) => value.event_id === saved.event_id);
  expect(span).toMatchObject({ verification: 'original_sha256_verified', byte_mapping: 'identity_relative_to_original_event', exported_text_is_original: false });
  const raw = await (await request.get(`/api/evidence/${span.event_id}?start=${span.byte_start}&end=${span.byte_end}&context=0`, { headers })).json();
  expect(raw.event.quote_sha256).toBe(span.quote_sha256);
  expect(raw.event.quote).toContain('ZY654321');
  expect(Buffer.byteLength(span.text)).toBe(Buffer.byteLength(raw.event.quote));
  expect(createHash('sha256').update(span.text).digest('hex')).toBe(span.exported_text_sha256);
  expect(await page.evaluate(() => (window as any).qaInjected)).toBeUndefined();
  await page.getByLabel('切换导出项目', { exact: true }).selectOption({ label: secondary });
  await expect(page.locator('.export-intro > p').first()).toContainText(secondary);
  await expect(page.getByRole('checkbox', { name: '加入遮盖后的证据片段', exact: true })).not.toBeChecked();
  const second = await download(page, 'mobile-other-project');
  expect(second.body.project_id).not.toBe(project);
});

test('真实双时间与完整/未知范围选择写进ZIP条件，不借网页默认范围', async ({ page, request }) => {
  const project = await open(page); const saved = await appendQuestion(request, project, '合成未知范围导出问题');
  await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  await expect(page.getByRole('button', { name: '下载历史 ZIP', exact: true })).toBeEnabled();
  await page.getByLabel('导出阅读范围', { exact: true }).selectOption('exact');
  await page.getByLabel('导出完整范围', { exact: true }).fill('dataset_version=qa_fixture_v1\nanalysis_step=问答验收');
  await page.getByLabel('导出发生截止', { exact: true }).fill('2026-01-02T00:00:00Z');
  const exact = await download(page, 'exact');
  expect(exact.archive.files['manifest.json'].conditions).toMatchObject({ scope_filter: true, scope: { dataset_version: 'qa_fixture_v1', analysis_step: '问答验收' }, occurred_until: '2026-01-02T00:00:00+00:00' });
  expect(exact.archive.files['claims.json'].length).toBeGreaterThan(0);
  expect(exact.archive.files['claims.json'].every((claim: any) => claim.scope.dataset_version === 'qa_fixture_v1' && claim.scope.analysis_step === '问答验收')).toBe(true);
  expect(exact.archive.files['state-events.json'].every((claim: any) => claim.occurred_at == null || Date.parse(claim.occurred_at) <= Date.parse('2026-01-02T00:00:00Z'))).toBe(true);
  await page.getByLabel('导出获知截止', { exact: true }).fill('2025-12-31T23:59:59+00:00');
  const old = await download(page, 'old');
  expect(old.archive.files['claims.json']).toEqual([]); expect(old.archive.files['sources.json']).toEqual([]);
  await page.getByLabel('导出获知截止', { exact: true }).fill(''); await page.getByLabel('导出发生截止', { exact: true }).fill('');
  await page.getByLabel('导出阅读范围', { exact: true }).selectOption('unknown');
  const unknown = await download(page, 'unknown');
  expect(unknown.body.scope).toBeNull(); expect(unknown.archive.files['manifest.json'].conditions).toMatchObject({ scope: null, scope_filter: true });
  expect(unknown.archive.files['claims.json'].some((claim: any) => claim.claim_id === saved.claim_id)).toBe(true);
  expect(unknown.archive.files['claims.json'].every((claim: any) => claim.scope === null)).toBe(true);
});

test('错误条件不发送，真实400保留规则且可手动修正，错误文件不能下载', async ({ page }) => {
  await open(page); const bodies: ExportRequest[] = []; const files: Download[] = [];
  page.on('request', r => { if (new URL(r.url()).pathname === '/api/exports') bodies.push(r.postDataJSON()); }); page.on('download', file => { files.push(file); });
  await page.getByLabel('导出发生截止', { exact: true }).fill('2026-10-10T12:00');
  await page.getByRole('button', { name: '下载历史 ZIP', exact: true }).click();
  await expect(page.locator('.export-form [role="alert"]')).toContainText('带时区'); expect(bodies).toHaveLength(0);
  await page.getByLabel('导出发生截止', { exact: true }).fill(''); await page.locator('.export-privacy summary').click();
  await page.getByLabel('导出自定义遮盖规则', { exact: true }).fill('[');
  const bad = page.waitForResponse(r => new URL(r.url()).pathname === '/api/exports');
  await page.getByRole('button', { name: '下载历史 ZIP', exact: true }).click(); expect((await bad).status()).toBe(400);
  await expect(page.locator('.export-form [role="alert"]')).toContainText('遮盖正则无效');
  await expect(page.getByLabel('导出自定义遮盖规则', { exact: true })).toHaveValue('['); expect(files).toHaveLength(0);
  await page.getByLabel('导出自定义遮盖规则', { exact: true }).fill('ZY\\d+');
  await download(page, 'corrected'); expect(files).toHaveLength(1);
  await page.route('**/api/exports', route => route.fulfill({ status: 200, contentType: 'text/html', body: '<script>window.badExport = true</script>' }));
  await page.getByRole('button', { name: '下载历史 ZIP', exact: true }).click();
  await expect(page.locator('.export-form [role="alert"]')).toContainText('文件类型异常'); expect(files).toHaveLength(1);
  expect(await page.evaluate(() => (window as any).badExport)).toBeUndefined();
});

test('真实409保留条件，读取最新版本不自动下载，用户再次点击才生成', async ({ page, request }) => {
  const project = await open(page); const bodies: ExportRequest[] = []; const files: Download[] = [];
  page.on('request', r => { if (new URL(r.url()).pathname === '/api/exports') bodies.push(r.postDataJSON()); }); page.on('download', value => { files.push(value); });
  await page.getByLabel('导出获知截止', { exact: true }).fill('2025-12-31T23:59:59Z');
  await appendQuestion(request, project, '合成另一窗口使导出修订变化');
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/exports');
  await page.getByRole('button', { name: '下载历史 ZIP', exact: true }).click(); expect((await response).status()).toBe(409);
  await expect(page.getByRole('button', { name: '下载历史 ZIP', exact: true })).toBeDisabled();
  await expect(page.getByLabel('导出获知截止', { exact: true })).toHaveValue('2025-12-31T23:59:59Z');
  await page.getByRole('button', { name: '读取最新版本', exact: true }).click();
  await expect(page.getByRole('button', { name: '下载历史 ZIP', exact: true })).toBeEnabled();
  expect(bodies).toHaveLength(1); expect(files).toHaveLength(0);
  const result = await download(page, 'revision'); expect(result.archive.files['claims.json']).toEqual([]);
  expect(bodies[1].expected_revision).toBeGreaterThan(bodies[0].expected_revision); expect(bodies[1].known_until).toBe(bodies[0].known_until);
});

test('真实401沿统一登录，阅读条件和隐私选择保留且恢复后需再次点击', async ({ page }) => {
  let calls = 0; const files: Download[] = []; page.on('download', value => { files.push(value); });
  await page.route('**/api/exports', async route => {
    calls++; const response = calls === 1 ? await route.fetch({ headers: { ...route.request().headers(), authorization: 'Bearer synthetic-invalid-token' } }) : await route.fetch();
    if (calls === 1) expect(response.status()).toBe(401); await route.fulfill({ response });
  });
  await open(page); await page.getByLabel('导出获知截止', { exact: true }).fill('2025-12-31T23:59:59Z'); await page.getByRole('checkbox', { name: '加入遮盖后的证据片段', exact: true }).check();
  await page.getByRole('button', { name: '下载历史 ZIP', exact: true }).click();
  await expect(page.getByLabel('访问令牌', { exact: true })).toBeVisible();
  await page.getByLabel('访问令牌', { exact: true }).fill('synthetic-browser-token'); await page.getByRole('button', { name: '进入本地工作区', exact: false }).click();
  await expect(page.getByRole('button', { name: '下载历史 ZIP', exact: true })).toBeEnabled();
  await expect(page.getByLabel('导出获知截止', { exact: true })).toHaveValue('2025-12-31T23:59:59Z'); await expect(page.getByRole('checkbox', { name: '加入遮盖后的证据片段', exact: true })).toBeChecked();
  expect(calls).toBe(1); expect(files).toHaveLength(0); await download(page, 'reauth'); expect(calls).toBe(2);
});

for (const mode of ['阅读条件', '项目'] as const) test(`真实ZIP回包延迟时${mode}改变不下载旧意图，忙时防重复且取消仅停止等待`, async ({ page }) => {
  const project = await open(page); let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; }); let waiting = 0; let completed = 0; const files: Download[] = [];
  page.on('download', value => { files.push(value); });
  await page.route('**/api/exports', async route => {
    const response = await route.fetch();
    if (!waiting) { waiting++; await held; }
    await route.fulfill({ response }).catch(() => {}); completed++;
  });
  await page.getByRole('button', { name: '下载历史 ZIP', exact: true }).click(); await expect.poll(() => waiting).toBe(1);
  await expect(page.getByRole('button', { name: '正在生成…', exact: true })).toBeDisabled(); await page.locator('.export-form').dispatchEvent('submit');
  if (mode === '项目') {
    await page.getByRole('button', { name: '取消等待', exact: true }).click(); await expect(page.locator('.export-status')).toContainText('服务端生成可能仍完成');
    await page.getByLabel('当前项目', { exact: true }).selectOption({ label: secondary });
    await expect(page.getByRole('region', { name: '历史导出阅读条件', exact: true })).toContainText(secondary);
  } else await page.getByLabel('导出获知截止', { exact: true }).fill('2025-12-31T23:59:59Z');
  release(); await expect.poll(() => completed).toBe(1); expect(files).toHaveLength(0); expect(waiting).toBe(1);
  await expect(page.getByRole('button', { name: '下载历史 ZIP', exact: true })).toBeEnabled();
  const result = await download(page, `late-${mode === '项目' ? 'project' : 'conditions'}`);
  if (mode === '项目') expect(result.body.project_id).not.toBe(project);
  else { expect(result.body.project_id).toBe(project); expect(result.archive.files['claims.json']).toEqual([]); }
});

test('真实401迟到不能登出新项目或抹掉该项目的阅读条件', async ({ page }) => {
  await open(page); let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; }); let waiting = 0; let completed = false;
  await page.route('**/api/exports', async route => {
    const response = await route.fetch({ headers: { ...route.request().headers(), authorization: 'Bearer synthetic-invalid-token' } }); expect(response.status()).toBe(401);
    waiting++; await held; await route.fulfill({ response }).catch(() => {}); completed = true;
  });
  await page.getByRole('button', { name: '下载历史 ZIP', exact: true }).click(); await expect.poll(() => waiting).toBe(1);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: secondary }); await page.getByLabel('导出发生截止', { exact: true }).fill('2026-01-01T00:00:00Z');
  release(); await expect.poll(() => completed).toBe(true);
  await expect(page.getByLabel('访问令牌', { exact: true })).toHaveCount(0); await expect(page.getByLabel('导出发生截止', { exact: true })).toHaveValue('2026-01-01T00:00:00Z');
  await expect(page.locator('.export-form [role="alert"]')).toHaveCount(0);
});
