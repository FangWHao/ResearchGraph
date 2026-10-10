import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { HealthData, Project } from '../../src/types';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const primary = '合成研究 · 界面信号验证';
async function open(page: Page) {
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  const panel = page.getByRole('region', { name: '持续提取队列 当前项目', exact: true });
  await expect(panel.locator('[data-queue-metric="total"] dd')).toHaveText('53');
  return panel;
}

test('真实队列七状态、固定范围计数和共享分页保留缺口与候选，界面只读', async ({ page, request }) => {
  const projects: Project[] = (await (await request.get('/api/projects', { headers })).json()).projects;
  const p = projects.find(item => item.name === primary)!;
  const before: HealthData = await (await request.get(`/api/health?project=${p.project_id}&limit=50&offset=0`, { headers })).json();
  const graphBefore = await (await request.get(`/api/graph?project=${p.project_id}`, { headers })).json();
  expect(before.extraction_queue).toMatchObject({ scope: 'project', total: 53, limit: 50, offset: 0, next_offset: 50, counts: { queued: 47, running: 1, done: 1, partial: 1, paused: 1, blocked: 1, cancelled: 1 } });
  expect(before.extraction_queue?.tasks).toHaveLength(50);
  const writes: string[] = []; page.on('request', r => { if (r.method() !== 'GET' && new URL(r.url()).pathname.startsWith('/api/')) writes.push(r.url()); });
  const panel = await open(page);
  for (const [key, value] of Object.entries(before.extraction_queue!.counts!)) await expect(panel.locator(`[data-queue-metric="${key}"] dd`)).toHaveText(value.toString());
  await expect(panel.locator('.extraction-tasks > li')).toHaveCount(50);
  await expect(panel).toContainText('不证明进程仍活着');
  await expect(panel).toContainText('不代表会话全部原文已覆盖');
  await expect(panel).toContainText('不代表候选事实已确认');
  const paused = before.extraction_queue!.tasks!.find(t => t.state === 'paused')!;
  await expect(panel.locator(`[data-queue-id="${paused.queue_id}"]`)).toContainText('每日模型额度不足');
  await expect(panel.locator(`[data-queue-id="${paused.queue_id}"] time`).last()).toHaveAttribute('datetime', paused.next_attempt_at!);
  const partial = before.extraction_queue!.tasks!.find(t => t.state === 'partial')!;
  await expect(panel.locator(`[data-queue-id="${partial.queue_id}"]`)).toContainText('RuntimeError');
  await expect(panel.locator(`[data-queue-id="${partial.queue_id}"]`)).toContainText('部分结果仍有缺口');
  await panel.scrollIntoViewIfNeeded(); await page.screenshot({ path: '../.cache/frontend-extraction-queue-desktop.png' });
  const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === '/api/health' && new URL(r.url()).searchParams.get('offset') === '50');
  await panel.getByRole('button', { name: '下一页提取任务', exact: true }).click();
  const after: HealthData = await (await responsePromise).json();
  await expect(panel.locator('.extraction-tasks > li')).toHaveCount(3);
  expect(after.extraction_queue?.counts).toEqual(before.extraction_queue?.counts);
  expect(after.extraction.coverage.pending_event_stages).toBe(before.extraction.coverage.pending_event_stages);
  expect(after.extraction.coverage.offset).toBe(50);
  await expect(panel.getByRole('button', { name: '下一页提取任务', exact: true })).toBeDisabled();
  await expect(panel.locator('[data-queue-metric="total"] dd')).toHaveText('53');
  await panel.getByRole('button', { name: '上一页提取任务', exact: true }).click();
  await expect(panel.locator('.extraction-tasks > li')).toHaveCount(50);
  const graphAfter = await (await request.get(`/api/graph?project=${p.project_id}`, { headers })).json();
  expect(graphAfter.revision).toBe(graphBefore.revision);
  expect(graphAfter.claims.map((c: { claim_id: number; effective_state: string }) => [c.claim_id, c.effective_state])).toEqual(graphBefore.claims.map((c: { claim_id: number; effective_state: string }) => [c.claim_id, c.effective_state]));
  expect(writes).toEqual([]);
});

test('项目切换重置任务分页，全库统计独立，小屏可读与键盘滚动', async ({ page, request }) => {
  const panel = await open(page);
  await panel.getByRole('button', { name: '下一页提取任务', exact: true }).click(); await expect(panel.locator('.extraction-tasks > li')).toHaveCount(3);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收批量项目' });
  await expect(panel.locator('[data-queue-metric="total"] dd')).toHaveText('1');
  await expect(panel.locator('[data-queue-metric="done"] dd')).toHaveText('1');
  await expect(panel.locator('.extraction-tasks > li')).toHaveCount(1);
  await expect(panel).toContainText('共享分页 · 偏移 0');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '合成空项目' });
  await expect(panel.locator('[data-queue-metric="total"] dd')).toHaveText('0');
  await expect(panel.locator('.extraction-tasks > li')).toHaveCount(0);
  await expect(panel).toContainText('当前页没有队列任务');
  const global: HealthData = await (await request.get('/api/health?limit=50&offset=0', { headers })).json();
  expect(global.extraction_queue).toMatchObject({ scope: 'all_projects', total: 54, counts: { queued: 47, done: 2 } });
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  await expect(panel.locator('[data-queue-metric="total"] dd')).toHaveText('53');
  await page.setViewportSize({ width: 390, height: 844 });
  await panel.scrollIntoViewIfNeeded(); await page.screenshot({ path: '../.cache/frontend-extraction-queue-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await panel.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  const tasks = panel.getByRole('list', { name: '提取任务列表，可纵向滚动', exact: true });
  await tasks.focus(); await tasks.press('ArrowDown'); await expect.poll(() => tasks.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  const partial = panel.locator('.extraction-tasks > li').filter({ hasText: '部分结果仍有缺口' });
  await partial.scrollIntoViewIfNeeded(); await page.screenshot({ path: '../.cache/frontend-extraction-queue-mobile-task.png' });
});

test('旧健康响应缺队列或任务字段保持未知，实际零和全库范围可区分', async ({ page }) => {
  let mode: 'missing' | 'partial' | 'global' = 'missing';
  await page.route('**/api/health?**', async route => {
    const response = await route.fetch(); const health: HealthData = await response.json();
    if (mode === 'missing') delete health.extraction_queue;
    else if (mode === 'partial') health.extraction_queue = { counts: { queued: 0 }, tasks: [{ queue_id: 999, state: 'new_unrecognized_state' }] };
    else { const global = await route.fetch({ url: new URL('/api/health?limit=50&offset=0', route.request().url()).href }); health.extraction_queue = (await global.json()).extraction_queue; }
    await route.fulfill({ response, json: health });
  });
  await page.goto('/#token=synthetic-browser-token'); await page.getByRole('button', { name: '采集健康', exact: true }).click();
  let panel = page.getByRole('region', { name: '持续提取队列 范围未知', exact: true });
  await expect(panel.locator('.health-queue-metrics dd')).toHaveCount(8);
  expect(await panel.locator('.health-queue-metrics dd').allTextContents()).toEqual(Array(8).fill('未知'));
  await expect(panel).toContainText('队列任务列表未知');
  await expect(page.getByRole('heading', { name: /来源与游标/ })).toBeVisible();
  mode = 'partial'; await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  await expect(panel.locator('[data-queue-metric="queued"] dd')).toHaveText('0');
  await expect(panel.locator('[data-queue-metric="done"] dd')).toHaveText('未知');
  await expect(panel.locator('.extraction-tasks')).toContainText('状态未知');
  await expect(panel.locator('.extraction-tasks')).toContainText('时间未知');
  await expect(panel.locator('.extraction-tasks')).toContainText('错误记录：未知');
  await expect(panel).toContainText('队列分页信息未知');
  await expect(panel.getByRole('button', { name: '下一页提取任务', exact: true })).toBeDisabled();
  mode = 'global'; await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  panel = page.getByRole('region', { name: '持续提取队列 全库 · 所有项目', exact: true });
  await expect(panel.locator('[data-queue-metric="total"] dd')).toHaveText('54');
});
