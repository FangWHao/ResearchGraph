import { createHash } from 'node:crypto';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import type { EvidenceData, L1Run, RunManifestPage } from '../../src/types';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const projectName = '验收运行清单项目';
interface RunReceipt { project_id: string; revision: number; run_id: string; run: L1Run; manifests: RunManifestPage }
async function setup(page: Page, request: APIRequestContext, call = 'manifest-zero') {
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: projectName });
  const project = await page.getByLabel('当前项目', { exact: true }).inputValue();
  const search = await (await request.get(`/api/search?project=${project}&q=${encodeURIComponent('合成运行清单定位锚点')}`, { headers })).json();
  expect(search.results).toHaveLength(1);
  const runId = 'l1:' + createHash('sha256').update(JSON.stringify([search.results[0].session_pk, call])).digest('hex');
  const response = await request.get(`/api/run-evidence?project=${project}&run_id=${runId}`, { headers });
  expect(response.status()).toBe(200);
  const receipt: RunReceipt = await response.json();
  return { project, receipt };
}
async function openEvent(page: Page, id: number | null) {
  expect(typeof id === 'number' && Number.isSafeInteger(id) && id > 0).toBe(true);
  const dialog = page.getByRole('dialog', { name: '原文证据', exact: true });
  if (await dialog.isVisible()) await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await page.getByLabel('搜索会话原文').fill('');
  await page.getByRole('button', { name: '开始搜索', exact: true }).click();
  await page.getByLabel('全库事件编号', { exact: true }).fill(String(id));
  await page.getByRole('button', { name: '按编号打开原文', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: `原文 #${id}`, exact: true })).toBeVisible();
  await expect(dialog.locator('.run-manifests')).toBeVisible();
  return dialog;
}

test('真实候选清单保留原生冲突、五类版本和原文，不执行参数或确认记录', async ({ page, request }) => {
  const { project, receipt } = await setup(page, request);
  expect(receipt.run).toMatchObject({ state: 'exited', exit_code: 0 });
  expect(receipt.manifests.total).toBe(21);
  const report = receipt.manifests.items[0];
  expect(report).toMatchObject({ claim_state: 'candidate', basis: 'direct_record', actual_io_completeness: 'unknown', reported_exit_conflicts_with_native: true });
  expect(report.io).toMatchObject({ total: 102, next_offset: 100, partial: true });
  const before = await (await request.get(`/api/graph?project=${project}`, { headers })).json();
  const writes: string[] = [];
  page.on('request', r => { if (r.method() !== 'GET') writes.push(r.url()); });
  const dialog = await openEvent(page, receipt.run.request_event_id);
  await expect(dialog.locator('.l1-card > .l1-heading > strong')).toHaveText('已结束 · 退出码 0');
  const first = dialog.locator('.run-manifest').first();
  await expect(first.getByRole('alert')).toContainText('原生退出码 0，清单报告 2');
  await expect(dialog.locator('.run-manifests > h5')).toContainText('当前 20 份 / 总计 21');
  await expect(first.locator('.manifest-io')).toHaveCount(100);
  await expect(first.locator('[data-manifest-role="inputs"]')).toContainText('98 个版本');
  for (const role of ['scripts', 'patches', 'environment', 'outputs']) await expect(first.locator(`[data-manifest-role="${role}"]`)).toContainText('1 个版本');
  await expect(first.locator('.manifest-io').filter({ hasText: 'synthetic-manifest-version:missing' })).toContainText('版本未知或当前不可见');
  await expect(first).toContainText('实际 I/O 完整性未知');
  await first.locator('.manifest-parameters > summary').click();
  await expect(first.getByLabel('清单报告参数，仅供阅读')).toContainText('<script>window.manifestInjected=true</script>');
  await expect(first).toContainText('9007199254740993123456789');
  expect(await page.evaluate(() => (window as typeof window & { manifestInjected?: boolean }).manifestInjected)).toBeUndefined();
  expect(await first.locator('script, img').count()).toBe(0);
  await first.locator('.manifest-snapshot > summary').click();
  await expect(first.locator('.manifest-snapshot')).toContainText('synthetic-manifest-shadow-commit');
  await first.scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-run-manifest-desktop.png' });
  await dialog.locator('.l1-card > .l1-heading').evaluate(node => node.scrollIntoView({ block: 'start' }));
  await page.screenshot({ path: '../.cache/frontend-run-manifest-native-conflict-desktop.png' });
  await first.getByRole('button', { name: `清单原文 #${report.evidence_event_id}`, exact: true }).click();
  await expect(dialog.getByRole('heading', { name: `原文 #${report.evidence_event_id}`, exact: true })).toBeVisible();
  await expect(dialog.locator('.raw-event.focused')).toContainText(report.request_id);
  await expect(dialog.locator('.l1-card > .l1-heading > strong')).toHaveText('已结束 · 退出码 0');
  expect(writes).toEqual([]);
  const after = await (await request.get(`/api/graph?project=${project}`, { headers })).json();
  expect(after.revision).toBe(before.revision); expect(after.claims).toEqual(before.claims);
});

test('真实清单和独立IO续页固定修订累计，不把分页读全当实际完整', async ({ page, request }) => {
  const { receipt } = await setup(page, request);
  const urls: URL[] = [];
  page.on('request', r => { if (new URL(r.url()).pathname === '/api/run-evidence') urls.push(new URL(r.url())); });
  const dialog = await openEvent(page, receipt.run.request_event_id);
  const first = dialog.locator('.run-manifest').first();
  await first.getByRole('button', { name: '读取后续版本关联', exact: true }).click();
  await expect(first.locator('.manifest-io')).toHaveCount(102);
  expect(urls).toHaveLength(2);
  expect(urls[0].searchParams.has('expected_revision')).toBe(false);
  expect(urls[1].searchParams.get('expected_revision')).toBe(String(receipt.revision));
  expect(urls[1].searchParams.get('manifest_id')).toBe(receipt.manifests.items[0].request_id);
  expect(urls[1].searchParams.get('io_offset')).toBe('100');
  await expect(first.locator('.manifest-io').filter({ hasText: 'synthetic-manifest-version:script' })).toContainText('工具报告文本');
  await expect(first.locator('.manifest-io').filter({ hasText: 'synthetic-manifest-version:script' })).toContainText('不能等同当时文件的物理字节');
  await expect(first.getByRole('button', { name: '读取后续版本关联', exact: true })).toHaveCount(0);
  await dialog.getByRole('button', { name: '读取后续运行清单', exact: true }).click();
  await expect(dialog.locator('.run-manifest')).toHaveCount(21);
  expect(urls[2].searchParams.get('offset')).toBe('20');
  expect(urls[2].searchParams.get('expected_revision')).toBe(String(receipt.revision));
  await expect(dialog.getByRole('button', { name: '读取后续运行清单', exact: true })).toHaveCount(0);
  await expect(first).toContainText('实际 I/O 完整性未知');
  await first.locator('.manifest-io-list').evaluate(node => { node.scrollTop = node.scrollHeight; });
  await first.locator('.manifest-io-section').scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-run-manifest-io-desktop.png' });
});

test('真实未知和仅请求运行不被报告0提升，小屏可阅读参数与版本', async ({ page, request }) => {
  for (const [call, label] of [['manifest-unknown', '运行状态未知'], ['manifest-requested', '已请求']]) {
    const { receipt } = await setup(page, request, call);
    expect(receipt.run.exit_code).toBeNull();
    expect(receipt.manifests.items[0].reported.exit_code).toBe(0);
    const dialog = await openEvent(page, receipt.run.request_event_id);
    await expect(dialog.locator('.l1-card > .l1-heading > strong')).toHaveText(label);
    const first = dialog.locator('.run-manifest').first();
    await expect(first).toContainText('0（原生观测未证实结束）');
    await expect(first.locator('[data-manifest-role="inputs"]')).toContainText('报告未知');
    await expect(first.locator('[data-manifest-role="scripts"]')).toContainText('明确报告空列表');
  }
  const { receipt } = await setup(page, request);
  const dialog = await openEvent(page, receipt.run.request_event_id);
  await page.setViewportSize({ width: 390, height: 844 });
  const first = dialog.locator('.run-manifest').first();
  await first.scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-run-manifest-mobile.png' });
  await first.locator('.manifest-parameters > summary').click();
  await first.locator('.manifest-parameters').scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-run-manifest-mobile-parameters.png' });
  const list = first.getByLabel('运行清单版本关联，可纵向滚动');
  await list.scrollIntoViewIfNeeded(); await list.focus(); await page.keyboard.press('End');
  await expect.poll(() => list.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect(await first.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
});

test('真实409保留旧页，主动刷新后仍须再点续页，401沿统一鉴权入口', async ({ page, request }) => {
  const { project, receipt } = await setup(page, request);
  const dialog = await openEvent(page, receipt.run.request_event_id);
  await dialog.getByRole('button', { name: '读取后续版本关联', exact: true }).click();
  await expect(dialog.locator('.run-manifest').first().locator('.manifest-io')).toHaveCount(102);
  const graph = await (await request.get(`/api/graph?project=${project}`, { headers })).json();
  expect((await request.post('/api/records/question', { headers, data: { project_id: project, text: '合成清单分页版本变化', scope: null, actor: 'human:合成验收', expected_revision: graph.revision, request_id: crypto.randomUUID() } })).status()).toBe(200);
  const conflict = page.waitForResponse(r => new URL(r.url()).pathname === '/api/run-evidence');
  await dialog.getByRole('button', { name: '读取后续运行清单', exact: true }).click();
  expect((await conflict).status()).toBe(409);
  await expect(dialog.locator('.run-manifests > [role="alert"]')).toContainText('不混合新旧分页');
  await expect(dialog.locator('.run-manifest')).toHaveCount(20);
  await expect(dialog.getByRole('button', { name: '读取后续运行清单', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: '刷新运行清单', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '读取后续运行清单', exact: true })).toBeEnabled();
  await expect(dialog.locator('.run-manifest')).toHaveCount(20);
  await expect(dialog.locator('.run-manifest').first().locator('.manifest-io')).toHaveCount(100);
  await dialog.getByRole('button', { name: '读取后续运行清单', exact: true }).click();
  await expect(dialog.locator('.run-manifest')).toHaveCount(21);
  await openEvent(page, receipt.run.request_event_id);
  await page.route('**/api/run-evidence?**', async route => {
    const response = await route.fetch({ headers: { ...route.request().headers(), authorization: 'Bearer synthetic-invalid-token' } });
    expect(response.status()).toBe(401); await route.fulfill({ response });
  });
  await dialog.getByRole('button', { name: '读取后续版本关联', exact: true }).click();
  await expect(page.getByLabel('访问令牌', { exact: true })).toBeVisible();
});

test('兼容缺字段并保留异常绑定/版本诊断，不把错误完整性当确认', async ({ page, request }) => {
  const { receipt } = await setup(page, request);
  let legacy = true; let reads = 0;
  page.on('request', r => { if (new URL(r.url()).pathname === '/api/run-evidence') reads++; });
  await page.route(`**/api/evidence/${receipt.run.request_event_id}?**`, async route => {
    const response = await route.fetch(); const evidence: EvidenceData = await response.json();
    const native = evidence.l1!.runs[0];
    if (legacy) delete native.manifests;
    else {
      const report = native.manifests!.items[0];
      report.binding_state = 'native_request_ambiguous';
      report.reported_exit_conflicts_with_native = false;
      report.io.partial = false;
      report.io.items[0].version!.project_id = 'synthetic-other-project';
      Object.assign(report.io.items[1].version!, { digest: { invalid: true }, recorded_at: [] });
      report.declared_snapshot!.async_race = true;
    }
    await route.fulfill({ response, json: evidence });
  });
  const dialog = await openEvent(page, receipt.run.request_event_id);
  await expect(dialog.locator('.run-manifests')).toContainText('接口未提供清单字段');
  await expect(dialog.getByRole('button', { name: '读取后续运行清单', exact: true })).toHaveCount(0);
  expect(reads).toBe(0);
  legacy = false; await page.getByLabel('前后上下文').selectOption('1');
  const first = dialog.locator('.run-manifest').first();
  await expect(first).toContainText('原生请求关联存在歧义');
  await expect(first).toContainText('退出码冲突标记与返回的原生/报告数值不一致');
  await expect(first).toContainText('完整性标记与分页记录矛盾');
  await expect(first.locator('.manifest-io').first()).toContainText('版本身份不能核定');
  await expect(first.locator('.manifest-io').first()).toContainText('版本未知或当前不可见');
  await expect(first.locator('.manifest-io').nth(1)).toContainText('版本元数据字段格式异常');
  await expect(dialog.locator('.l1-card > .l1-heading > strong')).toHaveText('已结束 · 退出码 0');
  await first.locator('.manifest-snapshot > summary').click();
  await expect(first).toContainText('快照存在异步竞争标记');
});

test('真实续页延迟回包不进入新原文或其他项目，也不重复请求', async ({ page, request }) => {
  const { receipt } = await setup(page, request);
  let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; });
  let waiting = 0;
  await page.route('**/api/run-evidence?**', async route => {
    const response = await route.fetch();
    if (new URL(route.request().url()).searchParams.get('io_offset') === '100') {
      waiting++; await held;
    }
    await route.fulfill({ response }).catch(() => {});
  });
  const dialog = await openEvent(page, receipt.run.request_event_id);
  const button = dialog.getByRole('button', { name: '读取后续版本关联', exact: true });
  await button.click();
  await expect.poll(() => waiting).toBe(1);
  await expect(button).toBeDisabled();
  const search = await (await request.get(`/api/search?project=${receipt.project_id}&q=${encodeURIComponent('合成运行清单定位锚点')}`, { headers })).json();
  const runId = 'l1:' + createHash('sha256').update(JSON.stringify([search.results[0].session_pk, 'manifest-unknown'])).digest('hex');
  const other: RunReceipt = await (await request.get(`/api/run-evidence?project=${receipt.project_id}&run_id=${runId}`, { headers })).json();
  await openEvent(page, other.run.request_event_id);
  release();
  await expect(dialog.locator('.l1-card > .l1-heading > strong')).toHaveText('运行状态未知');
  await expect(dialog.locator('.run-manifest')).toHaveCount(1);
  await expect(dialog.locator('.manifest-io')).toHaveCount(0);
  await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '合成空项目' });
  await expect(page.locator('.run-manifests')).toHaveCount(0);
  expect(waiting).toBe(1);
});
