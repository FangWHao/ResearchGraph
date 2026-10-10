import { createHash } from 'node:crypto';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import type { L1Node, L1Page, L1Edge } from '../../src/fileRunGraph';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const primary = '验收L1文件运行图项目';
async function open(page: Page, name = primary) {
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: name });
  await page.getByRole('button', { name: '文件运行图', exact: true }).click();
  const summary = page.getByRole('region', { name: '文件运行图完整性', exact: true });
  await expect(summary).toContainText('已读完登记图');
  await expect(page.getByRole('button', { name: '重新读取文件运行图', exact: true })).toBeVisible();
  return page.getByLabel('文件运行图项目', { exact: true }).inputValue();
}
async function nodes(request: APIRequestContext, project: string) {
  const response = await request.get(`/api/l1-graph?project=${project}&collection=nodes&limit=100`, { headers }); expect(response.status()).toBe(200);
  return (await response.json()) as Omit<L1Page, 'items'> & { items: L1Node[] };
}
async function choose(page: Page, node: L1Node) {
  await page.locator(`.file-run-node-choice[data-node-id="${node.node_id}"]`).click();
  return page.getByRole('region', { name: '文件运行节点详情', exact: true });
}
async function reread(page: Page) {
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/l1-graph' && new URL(r.url()).searchParams.get('collection') === 'unresolved');
  await page.getByRole('button', { name: '重新读取文件运行图', exact: true }).click(); expect((await response).status()).toBe(200);
  await expect(page.getByRole('region', { name: '文件运行图完整性' })).toContainText('已读完登记图');
}
async function visibleCanvasNodes(page: Page) {
  return page.getByTestId('file-run-canvas').evaluate(canvas => {
    const bounds = canvas.getBoundingClientRect();
    return [...canvas.querySelectorAll('[data-l1-node-id]')].filter(node => {
      const box = node.getBoundingClientRect();
      return box.width > 0 && box.height > 0 && Math.min(box.right, bounds.right, window.innerWidth) > Math.max(box.left, bounds.left, 0) && Math.min(box.bottom, bounds.bottom, window.innerHeight) > Math.max(box.top, bounds.top, 0);
    }).length;
  });
}
async function expectVisibleCanvas(page: Page) {
  try { await expect.poll(() => visibleCanvasNodes(page)).toBeGreaterThan(0); }
  catch (error) {
    const geometry = await page.getByTestId('file-run-canvas').evaluate(canvas => ({
      canvas: canvas.getBoundingClientRect().toJSON(), transform: canvas.querySelector('.react-flow__viewport')?.getAttribute('style'),
      nodes: [...canvas.querySelectorAll('[data-l1-node-id]')].map(node => ({ id: node.getAttribute('data-l1-node-id'), box: node.getBoundingClientRect().toJSON() })),
    }));
    console.log('手机画布几何', JSON.stringify(geometry)); throw error;
  }
}
async function holdSourceScroll(page: Page) {
  await page.evaluate(() => {
    const request = window.requestAnimationFrame.bind(window), cancel = window.cancelAnimationFrame.bind(window); const held = new Map<number, FrameRequestCallback>();
    window.requestAnimationFrame = callback => {
      if (!String(callback).includes('.file-run-source')) return request(callback);
      const id = request(() => {}); held.set(id, callback); return id;
    };
    window.cancelAnimationFrame = id => { held.delete(id); cancel(id); };
    (window as typeof window & { l1ScrollTest: { pending: () => number; restore: () => void } }).l1ScrollTest = {
      pending: () => held.size,
      restore: () => { window.requestAnimationFrame = request; window.cancelAnimationFrame = cancel; for (const callback of held.values()) callback(performance.now()); held.clear(); },
    };
  });
}
async function pendingSourceScroll(page: Page) { return page.evaluate(() => (window as typeof window & { l1ScrollTest: { pending: () => number } }).l1ScrollTest.pending()); }

test('真实完整五集合分页固定双时间/修订，报告IO连清单，冲突/未知/所有尝试版本可读', async ({ page, request }) => {
  const pages: L1Page[] = []; const queries: URLSearchParams[] = [];
  page.on('response', async response => { if (new URL(response.url()).pathname === '/api/l1-graph' && response.status() === 200) { pages.push(await response.json()); queries.push(new URL(response.url()).searchParams); } });
  const project = await open(page); await expect.poll(() => pages.length).toBe(6);
  const first = pages[0]; expect(pages.every(p => p.revision === first.revision && p.occurred_until === first.occurred_until && p.known_until === first.known_until && JSON.stringify(p.counts) === JSON.stringify(first.counts))).toBe(true);
  for (const query of queries.slice(1)) { expect(query.get('expected_revision')).toBe(String(first.revision)); expect(query.get('occurred_until')).toBe(first.occurred_until); expect(query.get('known_until')).toBe(first.known_until); }
  expect(pages.filter(p => p.collection === 'edges').map(p => p.offset)).toEqual([0, 100]);
  const allNodes = pages.find(p => p.collection === 'nodes')!.items as unknown as L1Node[];
  const edges = pages.filter(p => p.collection === 'edges').flatMap(p => p.items) as unknown as L1Edge[];
  expect(allNodes).toHaveLength(8); expect(edges).toHaveLength(113);
  expect(edges.filter(e => ['consumes', 'produces'].includes(e.relation)).every(e => e.source.kind === 'run_manifest' || e.target.kind === 'run_manifest' || e.source.kind === 'edit_record' || e.target.kind === 'edit_record')).toBe(true);
  await expect(page.locator('[data-l1-node-id]')).toHaveCount(8);
  const native = allNodes.find(n => n.kind === 'native_run')!; expect(native.record.state).toBe('exited'); expect(native.record.exit_code).toBe(3);
  await expect(await choose(page, native)).toContainText('已结束 · 退出码 3');
  const report = allNodes.find(n => n.kind === 'run_manifest' && n.record.reported_exit_conflicts_with_native)!;
  const detail = await choose(page, report); await expect(detail).toContainText('候选报告 · 退出码 0'); await expect(detail).toContainText('与原生退出观察冲突');
  await expect(detail.locator('[data-l1-role="inputs"]')).toContainText('明确报告 106 项'); await expect(detail.locator('[data-l1-role="scripts"]')).toContainText('明确报告空列表'); await expect(detail.locator('[data-l1-role="environment"]')).toContainText('未报告（未知）');
  await detail.getByText('报告参数（只读文字）', { exact: true }).click(); await expect(detail.locator('pre').first()).toContainText('$(never_execute)');
  const attempt = allNodes.find(n => n.kind === 'attempt')!; const attemptDetail = await choose(page, attempt);
  await expect(attemptDetail.locator('.file-run-version')).toHaveCount(2); await expect(attemptDetail).toContainText('合成尝试内容 A'); await expect(attemptDetail).toContainText('合成尝试内容 B');
  await expect(page.getByRole('region', { name: '文件运行图未解析诊断' })).toContainText('synthetic-run-not-observed');
  expect(await page.evaluate(() => (window as typeof window & { never_execute?: unknown; manifestInjected?: unknown }).never_execute)).toBeUndefined();
  const before = await (await request.get(`/api/graph?project=${project}`, { headers })).json(); await reread(page); const after = await (await request.get(`/api/graph?project=${project}`, { headers })).json(); expect(after).toEqual(before);
  await page.getByTestId('file-run-canvas').scrollIntoViewIfNeeded(); await expectVisibleCanvas(page);
  await page.getByTestId('file-run-canvas').screenshot({ path: '../.cache/frontend-l1-graph-canvas-desktop.png' });
  await choose(page, report); await page.locator('.file-run-detail').evaluate(node => node.scrollIntoView({ block: 'start' })); await page.screenshot({ path: '../.cache/frontend-l1-graph-detail-desktop.png' });
});

test('真实双截止阻止未来原生退出和编辑版本混入，完整/未知范围保持申报归属', async ({ page, request }) => {
  const project = await open(page);
  await page.getByLabel('文件运行图发生截止', { exact: true }).fill('2026-10-09T09:01:00Z'); await page.getByLabel('文件运行图获知截止', { exact: true }).fill('2026-10-09T09:01:00Z'); await reread(page);
  await page.locator('.file-run-node-choice').filter({ hasText: '原生运行 · synthetic' }).click(); const detail = page.getByRole('region', { name: '文件运行节点详情' });
  await expect(detail).toContainText('已请求 · 无结束证明'); await expect(detail.getByTestId('l1-observation')).toHaveCount(1); await expect(detail).not.toContainText('已结束 · 退出码 3');
  const eventIds: number[] = []; await page.locator('.file-run-picker summary').filter({ hasText: '全部原件引用' }).click();
  const buttons = page.locator('.file-run-picker').filter({ hasText: '全部原件引用' }).getByRole('button'); for (const button of await buttons.all()) eventIds.push(Number((await button.innerText()).match(/E(\d+)/)![1]));
  const observations = await (await request.get(`/api/l1-graph?project=${project}&collection=observations&limit=100`, { headers })).json();
  const futureExit = observations.items.find((item: { observation_kind: string; state: string }) => item.observation_kind === 'execution' && item.state === 'exited');
  expect(futureExit).toBeDefined(); expect(eventIds).not.toContain(futureExit.event_id);
  await page.getByLabel('文件运行图范围', { exact: true }).selectOption('exact'); await page.getByLabel('文件运行图完整范围', { exact: true }).fill('data=synthetic_v1\nstep=文件运行图'); await reread(page);
  await expect(page.getByRole('region', { name: '文件运行图完整性' })).toContainText('data=synthetic_v1');
  await page.getByLabel('文件运行图范围', { exact: true }).selectOption('unknown'); await reread(page); await expect(page.locator('.file-run-node-choice').filter({ hasText: '候选运行清单' })).toHaveCount(1);
  await page.getByLabel('文件运行图发生截止', { exact: true }).fill('2026-10-09T09:01'); let calls = 0; page.on('request', r => { if (new URL(r.url()).pathname === '/api/l1-graph') calls++; });
  await page.getByRole('button', { name: '重新读取文件运行图', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('带时区'); expect(calls).toBe(0);
});

test('真实历史原文窗口续读保持字节/hash与双截止，不访问旧证据派生接口', async ({ page, request }) => {
  const project = await open(page); const current = await nodes(request, project); const native = current.items.find(n => n.kind === 'native_run')!;
  const oldEndpoint: string[] = []; page.on('request', r => { if (/^\/api\/evidence\//.test(new URL(r.url()).pathname)) oldEndpoint.push(r.url()); });
  const detail = await choose(page, native); await detail.getByRole('button', { name: `打开历史原文 E${native.record.request_event_id}`, exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '文件运行图历史原文' }); await expect(dialog.getByTestId('l1-history-text')).toHaveCount(1);
  await dialog.getByRole('button', { name: '读取后续原文字节', exact: true }).click(); await expect(dialog.getByTestId('l1-history-text')).toHaveCount(2);
  const texts = await dialog.getByTestId('l1-history-text').allTextContents();
  const first = await (await request.get(`/api/l1-evidence?project=${project}&event_id=${native.record.request_event_id}&occurred_until=${encodeURIComponent(current.occurred_until)}&known_until=${encodeURIComponent(current.known_until)}&expected_revision=${current.revision}&max_bytes=4000`, { headers })).json();
  expect(createHash('sha256').update(texts[0]).digest('hex')).toBe(first.window_sha256); expect(Buffer.byteLength(texts[0])).toBe(first.window_end); expect(first).not.toHaveProperty('artifact_versions');
  expect(texts.join('')).toContain('<script>literal</script>'); await expect(dialog.locator('script, img')).toHaveCount(0); expect(oldEndpoint).toEqual([]);
});

test('真实物理版本保留捕获/发现/缓存的区别，缺失快照不补造已保存字节', async ({ page, request }) => {
  const project = await open(page, '合成研究 · 界面信号验证');
  await page.getByLabel('文件运行图发生截止', { exact: true }).fill('2026-10-10T12:00:00Z'); await page.getByLabel('文件运行图获知截止', { exact: true }).fill('2026-10-10T12:00:00Z'); await reread(page);
  const response = await request.get(`/api/l1-graph?project=${project}&collection=nodes&limit=100&occurred_until=2026-10-10T12:00:00Z&known_until=2026-10-10T12:00:00Z`, { headers }); expect(response.status()).toBe(200);
  const current = (await response.json()).items as L1Node[];
  const file = current.find(n => n.kind === 'artifact_version' && n.record.version_id === 'synthetic-browser-artifact:current')!;
  const detail = await choose(page, file); await expect(detail.getByTestId('l1-observation')).toHaveCount(4);
  const cached = detail.getByTestId('l1-observation').filter({ hasText: '缓存复用原窗口' }); await expect(cached).toHaveCount(3);
  for (const item of await cached.all()) { await expect(item).toContainText('2026-10-10T09:00:00Z → 2026-10-10T09:01:00Z'); await expect(item).toContainText('没有已保存捕获快照'); await expect(item).toContainText('发现快照未知'); }
  await page.locator('.file-run-detail').evaluate(node => node.scrollIntoView({ block: 'start' })); await page.screenshot({ path: '../.cache/frontend-l1-graph-observations-desktop.png' });
  expect(await detail.locator('script, img').count()).toBe(0);
  await page.getByLabel('文件运行图项目', { exact: true }).selectOption({ label: '验收运行清单项目' }); await expect(page.getByRole('region', { name: '文件运行图完整性' })).toContainText('已读完登记图');
  const manifestProject = await page.getByLabel('文件运行图项目', { exact: true }).inputValue(); const physical = (await nodes(request, manifestProject)).items;
  const archived = physical.find(n => n.kind === 'artifact_version' && n.record.source === 'shadow_snapshot')!; await expect(await choose(page, archived)).toContainText('已保存捕获快照');
  const discovered = physical.find(n => n.kind === 'artifact_version' && n.record.source === 'current_file')!; await expect(await choose(page, discovered)).toContainText('不证明旧字节'); await expect(detail).toContainText('没有已保存捕获快照');
});

test('真实409续读保留旧图和窗口，主动刷新不混页；401恢复原鉴权和草稿', async ({ page, request }) => {
  const project = await open(page); const current = await nodes(request, project); const native = current.items.find(n => n.kind === 'native_run')!;
  const detail = await choose(page, native); await detail.getByRole('button', { name: `打开历史原文 E${native.record.request_event_id}`, exact: true }).click(); const dialog = page.getByRole('dialog', { name: '文件运行图历史原文' }); await expect(dialog.getByTestId('l1-history-text')).toHaveCount(1);
  const saved = await request.post('/api/records/question', { headers, data: { project_id: project, text: '合成并发修订变更', scope: null, actor: 'human:合成图验收', request_id: crypto.randomUUID(), expected_revision: current.revision } }); expect(saved.status()).toBe(200);
  const conflict = page.waitForResponse(r => new URL(r.url()).pathname === '/api/l1-evidence'); await dialog.getByRole('button', { name: '读取后续原文字节', exact: true }).click(); expect((await conflict).status()).toBe(409); await expect(dialog.getByTestId('l1-history-text')).toHaveCount(1); await expect(dialog.getByRole('alert')).toContainText('已变化');
  const reads: string[] = []; page.on('request', r => { if (new URL(r.url()).pathname === '/api/l1-graph') reads.push(r.url()); }); expect(reads).toEqual([]); await reread(page); await expect(dialog).toHaveCount(0);
  await page.getByLabel('文件运行图发生截止', { exact: true }).fill('2026-10-09T09:01:00Z');
  await page.route('**/api/l1-graph?*', async route => { const response = await route.fetch({ headers: { ...route.request().headers(), Authorization: 'Bearer invalid-synthetic-token' } }); expect(response.status()).toBe(401); await route.fulfill({ response }); });
  await page.getByRole('button', { name: '重新读取文件运行图', exact: true }).click(); await expect(page.getByRole('heading', { name: '打开你的研究决定史' })).toBeVisible();
  await page.unroute('**/api/l1-graph?*'); await page.getByLabel('访问令牌', { exact: true }).fill('synthetic-browser-token'); await page.getByRole('button', { name: '进入本地工作区', exact: false }).click(); await expect(page.getByLabel('文件运行图发生截止', { exact: true })).toHaveValue('2026-10-09T09:01:00Z');
});

test('真实翻页中版本冲突不混入旧完整图，响应损坏显示缺口，不静默少画', async ({ page, request }) => {
  const project = await open(page); const before = await page.getByRole('region', { name: '文件运行图完整性' }).textContent() ?? ''; let changed = false;
  await page.route('**/api/l1-graph?*', async route => {
    const url = new URL(route.request().url()); if (url.searchParams.get('collection') === 'edges' && url.searchParams.get('offset') === '100' && !changed) {
      changed = true; const current = await nodes(request, project);
      expect((await request.post('/api/records/question', { headers, data: { project_id: project, text: '合成读取中变更', scope: null, actor: 'human:合成验收', expected_revision: current.revision, request_id: crypto.randomUUID() } })).status()).toBe(200);
    }
    const response = await route.fetch(); await route.fulfill({ response });
  });
  await page.getByRole('button', { name: '重新读取文件运行图', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('已变化'); await expect(page.getByRole('region', { name: '文件运行图完整性' })).toHaveText(before);
  await page.unroute('**/api/l1-graph?*'); await reread(page);
  await page.route('**/api/l1-graph?*', async route => { const response = await route.fetch(); const body = await response.json(); if (body.collection === 'edges' && body.offset === 100) body.counts.evidence += 1; await route.fulfill({ response, json: body }); });
  await page.getByRole('button', { name: '重新读取文件运行图', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('分页响应不一致'); await expect(page.locator('[data-l1-node-id]')).toHaveCount(8);
});

test('迟到真实图页/原文不进入修改后的条件或其他项目，390可查完整详情', async ({ page, request }) => {
  await open(page); let ready = false; let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/l1-graph?*', async route => {
    if (new URL(route.request().url()).searchParams.get('collection') !== 'nodes') return route.continue();
    const response = await route.fetch(); ready = true; await held; try { await route.fulfill({ response }); } catch { /* 新条件停止旧页面等待。 */ }
  });
  await page.getByRole('button', { name: '重新读取文件运行图', exact: true }).click(); await expect.poll(() => ready).toBe(true);
  await page.getByLabel('文件运行图获知截止', { exact: true }).fill('2025-01-01T00:00:00Z'); release(); await page.unroute('**/api/l1-graph?*'); await expect(page.getByRole('button', { name: '重新读取文件运行图', exact: true })).toBeVisible();
  await expect(page.getByText('阅读条件已改变', { exact: false })).toBeVisible(); await reread(page); await expect(page.locator('[data-l1-node-id]')).toHaveCount(0);
  await page.getByLabel('文件运行图获知截止', { exact: true }).fill(''); await reread(page);
  const project = await page.getByLabel('文件运行图项目', { exact: true }).inputValue(); const current = await nodes(request, project); const native = current.items.find(n => n.kind === 'native_run')!;
  ready = false; const next = new Promise<void>(resolve => { release = resolve; });
  await holdSourceScroll(page);
  await page.route('**/api/l1-evidence?*', async route => { const response = await route.fetch(); ready = true; await next; try { await route.fulfill({ response }); } catch { /* 项目切换停止旧页面等待。 */ } });
  await (await choose(page, native)).getByRole('button', { name: `打开历史原文 E${native.record.request_event_id}`, exact: true }).click(); await expect.poll(() => ready).toBe(true); await expect.poll(() => pendingSourceScroll(page)).toBe(1);
  await page.getByLabel('文件运行图项目', { exact: true }).selectOption({ label: '合成空项目' }); release(); await expect(page.getByRole('dialog', { name: '文件运行图历史原文' })).toHaveCount(0);
  const otherProject = await page.getByLabel('文件运行图项目', { exact: true }).inputValue(); const other = await nodes(request, otherProject);
  await expect.poll(() => pendingSourceScroll(page)).toBe(0); await page.evaluate(() => (window as typeof window & { l1ScrollTest: { restore: () => void } }).l1ScrollTest.restore());
  await expect(page.getByRole('region', { name: '文件运行图完整性' })).toContainText(`节点 ${other.total}/${other.total}`); await expect(page.locator(`[data-l1-node-id="${native.node_id}"]`)).toHaveCount(0);
  await page.unroute('**/api/l1-evidence?*'); await page.getByLabel('文件运行图项目', { exact: true }).selectOption({ label: primary }); await expect(page.getByRole('region', { name: '文件运行图完整性' })).toContainText('节点 8/8');
  await page.setViewportSize({ width: 390, height: 844 }); await page.locator('.file-run-summary').evaluate(node => node.scrollIntoView({ block: 'start' })); await expectVisibleCanvas(page); await page.screenshot({ path: '../.cache/frontend-l1-graph-summary-mobile.png' });
  const report = current.items.find(n => n.kind === 'run_manifest' && n.record.reported_exit_conflicts_with_native)!; await choose(page, report); await page.locator('.file-run-detail').evaluate(node => node.scrollIntoView({ block: 'start' })); await page.screenshot({ path: '../.cache/frontend-l1-graph-detail-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
