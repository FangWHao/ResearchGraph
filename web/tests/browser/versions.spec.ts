import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import type { HealthData, Project, VersionsPage } from '../../src/types';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const primary = '合成研究 · 界面信号验证';
const currentId = 'synthetic-browser-artifact:current';
async function open(page: Page) {
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  await page.getByRole('button', { name: '文件版本', exact: true }).click();
  const panel = page.getByRole('region', { name: '文件版本记录 当前项目', exact: true });
  await expect(panel.locator('.file-version-list > li')).toHaveCount(25);
  return panel;
}
async function primaryProject(request: APIRequestContext) {
  const projects: Project[] = (await (await request.get('/api/projects', { headers })).json()).projects;
  return projects.find(item => item.name === primary)!;
}

test('实际HTTP版本来源、缓存读取窗口与分页不改图或读取正文', async ({ page, request }) => {
  const project = await primaryProject(request);
  const before: VersionsPage = await (await request.get(`/api/versions?project=${project.project_id}&limit=25&offset=0`, { headers })).json();
  const graphBefore = await (await request.get(`/api/graph?project=${project.project_id}`, { headers })).json();
  expect(before).toMatchObject({ total: 33, limit: 25, offset: 0, partial: true });
  const current = before.versions!.find(version => version.version_id === currentId)!;
  expect(current).toMatchObject({ source: 'current_file', algo: 'sha256', size: 6000001, content_sha256: null, observations_total: 4, observations_partial: true });
  expect(current.observations).toHaveLength(3);
  expect(current.observations!.every(item => item.snapshot_id === null && item.cache_reused === true && item.hash_started_at === '2026-10-10T09:00:00Z' && item.hash_finished_at === '2026-10-10T09:01:00Z')).toBe(true);
  const writes: string[] = []; const files: string[] = [];
  page.on('request', event => {
    const url = new URL(event.url());
    if (url.pathname.startsWith('/api/') && event.method() !== 'GET') writes.push(url.pathname);
    if (url.pathname.startsWith('/api/') && /content|file\/|objects/.test(url.pathname)) files.push(url.pathname);
  });
  const panel = await open(page);
  await expect(page.getByRole('region', { name: '文件版本范围与依据' })).toContainText('版本记录不能证明运行实际输入输出');
  const row = panel.locator(`[data-file-version-id="${currentId}"]`);
  await expect(row).toContainText('当前文件哈希观察');
  await expect(row).toContainText('不等同发现快照当时的内容');
  await row.getByText('最近观察 · 4 条记录', { exact: true }).click();
  await expect(row).toContainText('仅返回最近 3 条');
  await expect(row).toContainText('不是本次入库时间的一次新读取');
  const latest = row.locator('[data-observation-id="synthetic-observation:current-3"]');
  await expect(latest.locator('time').nth(0)).toHaveAttribute('datetime', '2026-10-10T09:00:00Z');
  await expect(latest.locator('time').nth(1)).toHaveAttribute('datetime', '2026-10-10T09:01:00Z');
  await expect(latest.locator('time').nth(2)).toHaveAttribute('datetime', '2026-10-10T11:00:00Z');
  await expect(latest).toContainText('无快照关联');
  await expect(latest.locator('.version-metadata > div').filter({ hasText: '正文已复制保存' })).toHaveText('正文已复制保存否');
  await latest.getByText('身份与观察诊断 · 仅元数据', { exact: true }).click();
  await expect(latest).toContainText('synthetic-observation:current-0');
  const archived = panel.locator('[data-file-version-id="synthetic-browser-artifact:snapshot"]');
  await expect(archived).toContainText('影子快照字节');
  await archived.getByText('最近观察 · 1 条记录', { exact: true }).click();
  await expect(archived.locator('.version-metadata > div').filter({ hasText: '正文已复制保存' })).toHaveText('正文已复制保存是');
  await expect(panel.locator('[data-file-version-id="synthetic-browser-artifact:link"]')).toContainText('不是链接所指文件的内容');
  await expect(panel).not.toContainText('合成保存字节，不显示正文');
  await expect(panel.getByRole('button', { name: '下一页文件版本', exact: true })).toBeEnabled();
  await panel.getByRole('button', { name: '下一页文件版本', exact: true }).click();
  await expect(panel.locator('.file-version-list > li')).toHaveCount(8);
  await expect(panel.locator('.reported')).toHaveCount(2);
  await expect(panel.locator('.reported').first()).toContainText('不能等同当时文件的物理字节');
  await expect(panel.locator('.reported').first()).toContainText('待复核');
  await expect(panel.getByRole('button', { name: '下一页文件版本', exact: true })).toBeDisabled();
  const graphAfter = await (await request.get(`/api/graph?project=${project.project_id}`, { headers })).json();
  expect(graphAfter.revision).toBe(graphBefore.revision);
  expect(graphAfter.claims.map((claim: { claim_id: number; effective_state: string }) => [claim.claim_id, claim.effective_state])).toEqual(graphBefore.claims.map((claim: { claim_id: number; effective_state: string }) => [claim.claim_id, claim.effective_state]));
  expect(writes).toEqual([]); expect(files).toEqual([]);
});

test('特殊符号路径按字面精确筛选，项目切换与空结果不补造历史，小屏元数据可读', async ({ page }) => {
  const panel = await open(page);
  const row = panel.locator(`[data-file-version-id="${currentId}"]`);
  const path = await row.locator('.version-path').innerText();
  const response = page.waitForResponse(value => new URL(value.url()).pathname === '/api/versions' && new URL(value.url()).searchParams.get('path') === path);
  await row.getByRole('button', { name: '只看此路径版本', exact: true }).click();
  expect((await (await response).json()).total).toBe(1);
  await expect(panel.locator('.file-version-list > li')).toHaveCount(1);
  await expect(row.locator('.version-path')).toHaveText(path);
  await expect(row.locator('img')).toHaveCount(0);
  await expect(page.getByLabel('精确绝对路径', { exact: false })).toHaveValue(path);
  await expect(panel.getByRole('button', { name: '下一页文件版本', exact: true })).toBeDisabled();
  await page.screenshot({ path: '../.cache/frontend-artifacts-versions-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: '../.cache/frontend-artifacts-versions-mobile.png' });
  await row.getByText('最近观察 · 4 条记录', { exact: true }).click();
  const latest = row.locator('[data-observation-id="synthetic-observation:current-3"]');
  await latest.scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-artifacts-cache-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await row.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByLabel('精确绝对路径', { exact: false }).fill(path.slice(0, -4));
  await page.getByRole('button', { name: '筛选版本', exact: true }).click();
  await expect(panel.getByRole('heading', { name: '当前页没有文件版本', exact: true })).toBeVisible();
  await expect(panel).toContainText('不证明文件从未存在');
  const requests: string[] = [];
  page.on('request', event => { if (new URL(event.url()).pathname === '/api/versions') requests.push(event.url()); });
  await page.getByLabel('精确绝对路径', { exact: false }).fill('relative.txt');
  await page.getByRole('button', { name: '筛选版本', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('精确的绝对路径');
  expect(requests).toEqual([]);
  await page.getByRole('button', { name: '查看全部版本', exact: true }).click();
  await expect(panel.locator('.file-version-list > li')).toHaveCount(25);
  await panel.getByRole('button', { name: '下一页文件版本', exact: true }).click();
  await expect(panel.locator('.file-version-list > li')).toHaveCount(8);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收批量项目' });
  await expect(panel.locator('.file-version-list > li')).toHaveCount(1);
  await expect(panel).toContainText('偏移 0');
  await expect(panel).toContainText('工具报告文本');
  await expect(page.getByLabel('精确绝对路径', { exact: false })).toHaveValue('');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '合成空项目' });
  await expect(panel.getByRole('heading', { name: '当前页没有文件版本', exact: true })).toBeVisible();
});

test('项目健康数量、任务与快照发现分开统计，旧字段缺失不补零', async ({ page, request }) => {
  const project = await primaryProject(request);
  const health: HealthData = await (await request.get(`/api/health?project=${project.project_id}`, { headers })).json();
  expect(health.artifacts).toEqual({ versions: 33, archived: 2, current_hashed: 1, cache_reused: 3, jobs: { queued: 1, running: 1, paused: 1, failed: 1, done: 6 }, discovery: { pending: 1, unknown: 1, partial: 1, done: 1 } });
  await open(page);
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  const panel = page.getByRole('region', { name: '文件版本与后台摘要 当前项目', exact: true });
  for (const [group, values] of Object.entries({ versions: { versions: 33, archived: 2, current_hashed: 1, cache_reused: 3 }, jobs: health.artifacts!.jobs!, discovery: health.artifacts!.discovery! })) {
    for (const [key, value] of Object.entries(values)) await expect(panel.locator(`[data-artifact-metric="${group}.${key}"] dd`)).toHaveText(value.toString());
  }
  await expect(panel).toContainText('不证明进程仍活着');
  await expect(panel).toContainText('不能相加推算完整文件数量');
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-artifacts-health-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-artifacts-health-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  await panel.getByRole('button', { name: '查看文件版本', exact: true }).click();
  await expect(page.getByRole('heading', { name: '文件版本', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '合成空项目' });
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  await expect(panel.locator('[data-artifact-metric="versions.versions"] dd')).toHaveText('0');
  await expect(panel.locator('[data-artifact-metric="jobs.running"] dd')).toHaveText('0');
  await expect(panel.locator('[data-artifact-metric="discovery.pending"] dd')).toHaveText('1');
  await page.route('**/api/health?**', async route => {
    const response = await route.fetch(); const data: HealthData = await response.json(); delete data.artifacts;
    await route.fulfill({ response, json: data });
  });
  await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  await expect(panel.locator('.artifact-health-metrics dd')).toHaveCount(13);
  await expect(panel.locator('.artifact-health-metrics dd').first()).toHaveText('统计缺失');
  expect(await panel.locator('.artifact-health-metrics dd').allTextContents()).toEqual(Array(13).fill('统计缺失'));
});

test('版本失败可重试，未知字段与错误关联保留缺口，不显示未知诊断正文', async ({ page }) => {
  let mode: 'failure' | 'missing' | 'conflict' | 'foreign' = 'failure';
  await page.route('**/api/versions?**', async route => {
    if (mode === 'failure') { await route.fulfill({ status: 503, json: { error: '合成版本服务暂时不可用' } }); return; }
    const response = await route.fetch(); const data: VersionsPage = await response.json();
    if (mode === 'missing') { delete data.total; delete data.partial; delete data.versions; }
    else if (mode === 'foreign') data.versions![0].project_id = 'synthetic-foreign-project';
    else {
      data.versions = data.versions!.filter(item => item.version_id === currentId);
      const current = data.versions[0];
      delete current.observations_partial;
      const observation = current.observations![0];
      observation.snapshot_id = 17; delete observation.hash_started_at; delete observation.hash_finished_at;
      observation.signature = JSON.parse('[2049,98213,6000001,1791619260123456789,1791619260987654321]');
      observation.details = { complete: true, content_copied: false, body: '<img src=x onerror="window.versionUnsafe=true">不应显示的文件正文' };
    }
    await route.fulfill({ response, json: data });
  });
  await page.goto('/#token=synthetic-browser-token');
  await page.getByRole('button', { name: '文件版本', exact: true }).click();
  await expect(page.getByRole('heading', { name: '文件版本暂不可用', exact: true })).toBeVisible();
  mode = 'missing'; await page.getByRole('button', { name: '重新读取文件版本', exact: true }).click();
  const panel = page.getByRole('region', { name: '文件版本记录 当前项目', exact: true });
  await expect(panel).toContainText('文件版本列表缺失，不能视为没有版本');
  await expect(panel).toContainText('总数未知');
  await expect(panel).toContainText('完整性标记');
  await expect(panel.getByRole('button', { name: '下一页文件版本', exact: true })).toBeDisabled();
  mode = 'conflict'; await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  const row = panel.locator(`[data-file-version-id="${currentId}"]`);
  await row.getByText('最近观察 · 4 条记录', { exact: true }).click();
  await expect(row).toContainText('观察完整性标记');
  await expect(row).toContainText('当前文件观察错误关联快照');
  await expect(row).toContainText('时间未知');
  await row.locator('.version-diagnostics summary').first().click();
  await expect(row).toContainText('签名包含超出浏览器整数精度的字段，无法在此精确展示');
  await expect(row).not.toContainText('1791619260123456800');
  await expect(row).not.toContainText('不应显示的文件正文');
  await expect(row.locator('img')).toHaveCount(0);
  mode = 'foreign'; await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  await expect(page.getByRole('heading', { name: '文件版本暂不可用', exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('归属缺失或与当前项目不一致');
});

test('实际401交统一鉴权，重新鉴权后只读恢复', async ({ page }) => {
  let denied = true;
  await page.route('**/api/versions?**', route => denied ? route.fulfill({ status: 401, json: { error: '合成版本令牌已失效' } }) : route.continue());
  await page.goto('/#token=synthetic-browser-token');
  await page.getByRole('button', { name: '文件版本', exact: true }).click();
  await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toBeVisible();
  denied = false;
  await page.getByLabel('访问令牌', { exact: true }).fill('synthetic-browser-token');
  await page.getByRole('button', { name: /进入本地工作区/ }).click();
  await expect(page.getByRole('region', { name: '文件版本记录 当前项目', exact: true }).locator('.file-version-list > li')).toHaveCount(25);
});

for (const stale of ['project', 'authorization'] as const) {
  test(`无法中止的旧${stale === 'project' ? '项目结果' : '鉴权401'}不能覆盖新的只读页面`, async ({ page, request }) => {
    const project = await primaryProject(request);
    let release = () => {}; const gate = new Promise<void>(resolve => { release = resolve; });
    let ready = () => {}; const started = new Promise<void>(resolve => { ready = resolve; });
    let deferred = false;
    await page.addInitScript(() => {
      const actual = window.fetch.bind(window);
      const received: string[] = []; (window as unknown as { versionReceived: string[] }).versionReceived = received;
      window.fetch = async (input, options) => {
        if (!String(input).startsWith('/api/versions?')) return actual(input, options);
        const response = await actual(input, { ...options, signal: undefined }); const parse = response.json.bind(response);
        response.json = async () => { const data = await parse(); received.push(`${response.status}:${data.total ?? 'unknown'}`); return data; };
        return response;
      };
    });
    await page.route('**/api/versions?**', async route => {
      if (!deferred && new URL(route.request().url()).searchParams.get('project') === project.project_id) {
        deferred = true; const response = await route.fetch(); ready(); await gate;
        if (stale === 'project') await route.fulfill({ response });
        else await route.fulfill({ status: 401, json: { error: '不应影响新鉴权的旧错误' } });
      } else await route.continue();
    });
    await page.goto('/#token=synthetic-browser-token');
    await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
    await page.getByRole('button', { name: '文件版本', exact: true }).click(); await started;
    if (stale === 'project') await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收批量项目' });
    else await page.evaluate(() => { window.location.hash = 'token=synthetic-browser-token'; });
    const panel = page.getByRole('region', { name: '文件版本记录 当前项目', exact: true });
    await expect(panel.locator('.file-version-list > li')).toHaveCount(stale === 'project' ? 1 : 25);
    release();
    await expect.poll(() => page.evaluate(() => (window as unknown as { versionReceived: string[] }).versionReceived)).toEqual(stale === 'project' ? ['200:1', '200:33'] : ['200:33', '401:unknown']);
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(panel.locator('.file-version-list > li')).toHaveCount(stale === 'project' ? 1 : 25);
    await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toHaveCount(0);
    await expect(page.getByText('不应影响新鉴权的旧错误', { exact: false })).toHaveCount(0);
  });
}
