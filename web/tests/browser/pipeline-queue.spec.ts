import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { HealthData, Project } from '../../src/types';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const primary = '合成研究 · 界面信号验证';
async function open(page: Page) {
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  const panel = page.getByRole('region', { name: '自动关联与概览 当前项目', exact: true });
  await expect(panel.locator('[data-pipeline-metric="total"] dd')).toHaveText('54');
  return panel;
}
function healthResponse(page: Page, offset: string, pipeline: string) {
  return page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname === '/api/health' && url.searchParams.get('offset') === offset && url.searchParams.get('pipeline_offset') === pipeline;
  });
}

test('真实HTTP阶段队列、等待原因与独立分页保持范围计数，查看只读', async ({ page, request }) => {
  const projects: Project[] = (await (await request.get('/api/projects', { headers })).json()).projects;
  const project = projects.find(item => item.name === primary)!;
  const before: HealthData = await (await request.get(`/api/health?project=${project.project_id}&offset=0&pipeline_offset=0&limit=50`, { headers })).json();
  const graphBefore = await (await request.get(`/api/graph?project=${project.project_id}`, { headers })).json();
  expect(before.pipeline_queue).toMatchObject({ scope: 'project', total: 54, limit: 50, offset: 0, next_offset: 50, counts: { queued: 45, running: 1, done: 2, partial: 1, paused: 3, blocked: 1, cancelled: 1 } });
  expect(before.pipeline_queue?.tasks).toHaveLength(50);
  expect(before.extraction_queue?.total).toBe(53);
  const writes: string[] = [];
  page.on('request', event => { if (event.method() !== 'GET' && new URL(event.url()).pathname.startsWith('/api/')) writes.push(event.url()); });
  const panel = await open(page);
  const extraction = page.getByRole('region', { name: '持续提取队列 当前项目', exact: true });
  for (const [key, value] of Object.entries(before.pipeline_queue!.counts!)) await expect(panel.locator(`[data-pipeline-metric="${key}"] dd`)).toHaveText(value.toString());
  await expect(panel.locator('.pipeline-tasks > li')).toHaveCount(50);
  await expect(panel).toContainText('不证明进程仍活着');
  await expect(panel).toContainText('阶段任务完成不代表研究事实已确认');
  await expect(panel).toContainText('概览不作为证据');
  const completed = before.pipeline_queue!.tasks!.find(task => task.state === 'done' && task.stage === 'overview')!;
  await expect(panel.locator(`[data-pipeline-id="${completed.queue_id}"]`)).toContainText(`会话 #${completed.session_pk}`);
  const link = before.pipeline_queue!.tasks!.find(task => task.stage === 'link')!;
  await expect(panel.locator(`[data-pipeline-id="${link.queue_id}"]`)).toContainText('整个项目');
  for (const reason of ['daily_budget', 'CountingUnavailable', 'RuntimeError', 'stage_busy']) {
    const task = before.pipeline_queue!.tasks!.find(item => item.defer_reason === reason)!;
    const row = panel.locator(`[data-pipeline-id="${task.queue_id}"]`);
    await expect(row.locator('time').last()).toHaveAttribute('datetime', task.next_attempt_at!);
    if (reason === 'daily_budget') await expect(row).toContainText('等待下一个 UTC 日');
    else { await expect(row).not.toContainText('每日模型额度不足'); await expect(row).toContainText('按下次尝试时间重试'); }
  }
  const response = healthResponse(page, '0', '50');
  await panel.getByRole('button', { name: '下一页关联与概览', exact: true }).click();
  const after: HealthData = await (await response).json();
  await expect(panel.locator('.pipeline-tasks > li')).toHaveCount(4);
  await expect(extraction.locator('.extraction-tasks > li')).toHaveCount(50);
  expect(after.pipeline_queue?.counts).toEqual(before.pipeline_queue?.counts);
  expect(after.extraction_queue?.offset).toBe(0);
  expect(after.extraction.coverage.offset).toBe(0);
  expect(after.extraction.coverage.pending_event_stages).toBe(before.extraction.coverage.pending_event_stages);
  await expect(panel.getByRole('button', { name: '下一页关联与概览', exact: true })).toBeDisabled();
  const extractionResponse = healthResponse(page, '50', '50');
  await extraction.getByRole('button', { name: '下一页提取任务', exact: true }).click();
  const both: HealthData = await (await extractionResponse).json();
  await expect(extraction.locator('.extraction-tasks > li')).toHaveCount(3);
  await expect(panel.locator('.pipeline-tasks > li')).toHaveCount(4);
  expect(both.pipeline_queue?.offset).toBe(50);
  expect(both.extraction.coverage.offset).toBe(50);
  const returnResponse = healthResponse(page, '50', '0');
  await panel.getByRole('button', { name: '上一页关联与概览', exact: true }).click();
  await returnResponse;
  await expect(panel.locator('.pipeline-tasks > li')).toHaveCount(50);
  await expect(extraction.locator('.extraction-tasks > li')).toHaveCount(3);
  const graphAfter = await (await request.get(`/api/graph?project=${project.project_id}`, { headers })).json();
  expect(graphAfter.revision).toBe(graphBefore.revision);
  expect(graphAfter.claims.map((claim: { claim_id: number; effective_state: string }) => [claim.claim_id, claim.effective_state])).toEqual(graphBefore.claims.map((claim: { claim_id: number; effective_state: string }) => [claim.claim_id, claim.effective_state]));
  expect(writes).toEqual([]);
});

test('项目切换分别重置两个分页，零值和全库分开，小屏与键盘可读', async ({ page, request }) => {
  const panel = await open(page);
  const extraction = page.getByRole('region', { name: '持续提取队列 当前项目', exact: true });
  await panel.getByRole('button', { name: '下一页关联与概览', exact: true }).click();
  await expect(panel.locator('.pipeline-tasks > li')).toHaveCount(4);
  await extraction.getByRole('button', { name: '下一页提取任务', exact: true }).click();
  await expect(extraction.locator('.extraction-tasks > li')).toHaveCount(3);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收批量项目' });
  await expect(panel.locator('[data-pipeline-metric="total"] dd')).toHaveText('2');
  await expect(panel.locator('.pipeline-tasks > li')).toHaveCount(2);
  await expect(extraction.locator('[data-queue-metric="total"] dd')).toHaveText('1');
  await expect(panel).toContainText('独立分页 · 偏移 0');
  await expect(extraction).toContainText('共享分页 · 偏移 0');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '合成空项目' });
  await expect(panel.locator('[data-pipeline-metric="total"] dd')).toHaveText('0');
  await expect(panel.locator('.pipeline-tasks > li')).toHaveCount(0);
  await expect(panel).toContainText('当前页没有关联与概览任务');
  const global: HealthData = await (await request.get('/api/health?offset=0&pipeline_offset=0&limit=50', { headers })).json();
  expect(global.pipeline_queue).toMatchObject({ scope: 'all_projects', total: 56, counts: { queued: 46, done: 3 } });
  expect(global.extraction_queue?.total).toBe(54);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  await expect(panel.locator('[data-pipeline-metric="total"] dd')).toHaveText('54');
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-pipeline-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-pipeline-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  const tasks = panel.getByRole('list', { name: '关联与概览任务列表，可纵向滚动', exact: true });
  await tasks.focus(); await tasks.press('ArrowDown');
  await expect.poll(() => tasks.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  const counting = panel.locator('.pipeline-tasks > li').filter({ hasText: '（CountingUnavailable）' });
  await counting.scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-pipeline-mobile-task.png' });
  expect(await counting.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
});

test('缺失队列与未知字段保持未知，错误目标与结果文本不会成为证据或HTML', async ({ page }) => {
  let mode: 'missing' | 'partial' | 'global' = 'missing';
  await page.route('**/api/health?**', async route => {
    const response = await route.fetch(); const health: HealthData = await response.json();
    if (mode === 'missing') delete health.pipeline_queue;
    else if (mode === 'partial') health.pipeline_queue = { counts: { paused: 0 }, tasks: [{ queue_id: 999, state: 'new_unrecognized_state', target_key: 'project', session_pk: 12, result: '<img src=x onerror="window.pipelineUnsafe=true">' }] };
    else {
      const global = await route.fetch({ url: new URL('/api/health?offset=0&pipeline_offset=0&limit=50', route.request().url()).href });
      health.pipeline_queue = (await global.json()).pipeline_queue;
    }
    await route.fulfill({ response, json: health });
  });
  await page.goto('/#token=synthetic-browser-token');
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  let panel = page.getByRole('region', { name: '自动关联与概览 范围未知', exact: true });
  await expect(panel.locator('.health-queue-metrics dd')).toHaveCount(8);
  expect(await panel.locator('.health-queue-metrics dd').allTextContents()).toEqual(Array(8).fill('未知'));
  await expect(panel).toContainText('关联与概览任务列表未知');
  await expect(page.getByRole('heading', { name: /来源与游标/ })).toBeVisible();
  mode = 'partial'; await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  await expect(panel.locator('[data-pipeline-metric="paused"] dd')).toHaveText('0');
  await expect(panel.locator('[data-pipeline-metric="done"] dd')).toHaveText('未知');
  const task = panel.locator('[data-pipeline-id="999"]');
  for (const value of ['阶段未知', '状态未知', '目标字段不一致', '时间未知', '等待原因：未知', '错误记录：未知']) await expect(task).toContainText(value);
  await task.getByText('阶段结果记录 · 仅诊断', { exact: true }).click();
  await expect(task.locator('pre')).toHaveText('<img src=x onerror="window.pipelineUnsafe=true">');
  await expect(task.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { pipelineUnsafe?: boolean }).pipelineUnsafe)).toBeUndefined();
  await expect(panel).toContainText('关联与概览分页信息未知');
  await expect(panel.getByRole('button', { name: '下一页关联与概览', exact: true })).toBeDisabled();
  mode = 'global'; await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  panel = page.getByRole('region', { name: '自动关联与概览 全库 · 所有项目', exact: true });
  await expect(panel.locator('[data-pipeline-metric="total"] dd')).toHaveText('56');
});

test('迟到的旧项目健康响应即使无法中止也不能覆盖当前项目', async ({ page, request }) => {
  const projects: Project[] = (await (await request.get('/api/projects', { headers })).json()).projects;
  const project = projects.find(item => item.name === primary)!;
  let release = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  let markStarted = () => {};
  const started = new Promise<void>(resolve => { markStarted = resolve; });
  let markReturned = () => {};
  const returned = new Promise<void>(resolve => { markReturned = resolve; });
  await page.addInitScript(() => {
    const actual = window.fetch.bind(window);
    const received: number[] = [];
    (window as unknown as { healthReceived: number[] }).healthReceived = received;
    window.fetch = async (input, options) => {
      if (!String(input).startsWith('/api/health?')) return actual(input, options);
      const response = await actual(input, { ...options, signal: undefined });
      const parse = response.json.bind(response);
      response.json = async () => {
        const data: HealthData = await parse();
        if (data.pipeline_queue?.total != null) received.push(data.pipeline_queue.total);
        return data;
      };
      return response;
    };
  });
  await page.route('**/api/health?**', async route => {
    if (new URL(route.request().url()).searchParams.get('project') === project.project_id) {
      const response = await route.fetch(); markStarted();
      await gate; await route.fulfill({ response }); markReturned();
    } else await route.continue();
  });
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  await started;
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收批量项目' });
  const panel = page.getByRole('region', { name: '自动关联与概览 当前项目', exact: true });
  await expect(panel.locator('[data-pipeline-metric="total"] dd')).toHaveText('2');
  release(); await returned;
  await expect.poll(() => page.evaluate(() => (window as unknown as { healthReceived: number[] }).healthReceived)).toEqual([2, 54]);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(panel.locator('[data-pipeline-metric="total"] dd')).toHaveText('2');
  await expect(panel.locator('.pipeline-tasks > li')).toHaveCount(2);
  await expect(page.getByRole('region', { name: '持续提取队列 当前项目', exact: true }).locator('[data-queue-metric="total"] dd')).toHaveText('1');
});
