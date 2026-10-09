import { expect, test } from '@playwright/test';
import type { HealthData, Project } from '../../src/types';

const headers = { Authorization: 'Bearer synthetic-browser-token' };
const primary = '合成研究 · 界面信号验证';

test('真实HTTP健康统计保留全库口径、项目范围与重叠快照标记', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const projects: { projects: Project[] } = await (await request.get('/api/projects', { headers })).json();
  const project = projects.projects.find(item => item.name === primary)!;
  const response = await request.get(`/api/health?project=${project.project_id}`, { headers });
  expect(response.status()).toBe(200);
  const health: HealthData = await response.json();
  expect(health.ingest).toEqual({ registered_sources: 2, known_source_paths: 5, spool_receipts: 3, spool_unfinished: 2, spool_failed: 1 });
  expect(health.snapshots).toEqual({ total: 4, skipped: 1, async_race: 2, partial: 2, metadata_unknown: 1 });
  expect(health.hook_failures).toBeNull();
  expect(health.extraction.stages).toEqual([
    expect.objectContaining({ stage: 'pass1', attempts: 1, sent: 0, mean_utilization: null, utilization_samples: 0, validation_samples: 0 }),
    expect.objectContaining({ stage: 'pass2', attempts: 1, sent: 1, mean_utilization: 0, utilization_samples: 1, validation_samples: 1, validation_rejections: 1, citation_failures: 1 }),
  ]);

  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  const ingest = page.getByRole('region', { name: /采集登记与回执/ });
  const snapshots = page.getByRole('region', { name: /工作区快照/ });
  for (const [key, value] of Object.entries(health.ingest!)) {
    await expect(ingest.locator(`[data-health-metric="${key}"] dd`)).toHaveText(value.toString());
  }
  for (const [key, value] of Object.entries(health.snapshots!)) {
    await expect(snapshots.locator(`[data-health-metric="${key}"] dd`)).toHaveText(value.toString());
  }
  await expect(ingest).toContainText('不证明采集进程仍在运行');
  await expect(snapshots).toContainText('不能从记录数相减得到成功数');
  await page.getByText('如何理解快照标记', { exact: true }).click();
  await expect(snapshots).toContainText('不证明发生了竞态错误');
  await expect(page.locator('.quality-grid')).toContainText('未知');
  const stages = page.getByRole('region', { name: /模型阶段观测 当前项目/ });
  const unknownRow = stages.getByRole('row').filter({ hasText: 'pass1' });
  const zeroRow = stages.getByRole('row').filter({ hasText: 'pass2' });
  await expect(unknownRow.getByRole('cell').nth(0)).toHaveText('1 / 0');
  await expect(unknownRow.getByRole('cell').nth(1)).toContainText('未知');
  await expect(zeroRow.getByRole('cell').nth(0)).toHaveText('1 / 1');
  await expect(zeroRow.getByRole('cell').nth(1)).toContainText('0%');
  for (const index of [2, 3, 4]) await expect(zeroRow.getByRole('cell').nth(index)).toHaveText('1');
  await expect(stages).toContainText('两个计数不能相加');
  await expect(page.getByRole('heading', { name: /来源与游标/ })).toBeVisible();
  expect(new URL(page.url()).hash).toBe('');
  await page.screenshot({ path: '../.cache/frontend-health-followup-desktop.png', fullPage: true });

  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '合成空项目' });
  await expect(snapshots.locator('[data-health-metric="total"] dd')).toHaveText('1');
  await expect(snapshots.locator('[data-health-metric="skipped"] dd')).toHaveText('1');
  for (const key of ['async_race', 'partial', 'metadata_unknown']) await expect(snapshots.locator(`[data-health-metric="${key}"] dd`)).toHaveText('0');
  await expect(ingest.locator('[data-health-metric="spool_receipts"] dd')).toHaveText('3');
  await expect(page.getByRole('region', { name: /模型阶段观测 当前项目/ })).toContainText('没有模型尝试记录');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收批量项目' });
  for (const key of ['total', 'skipped', 'async_race', 'partial', 'metadata_unknown']) await expect(snapshots.locator(`[data-health-metric="${key}"] dd`)).toHaveText('0');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: primary });
  await expect(snapshots.locator('[data-health-metric="total"] dd')).toHaveText('4');
  await page.setViewportSize({ width: 390, height: 844 });
  const table = page.getByRole('region', { name: '模型阶段观测表，可横向滚动', exact: true });
  expect(await table.evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
  await table.focus();
  await table.press('ArrowRight');
  await expect.poll(() => table.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
  await table.evaluate(node => { node.scrollLeft = 0; });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: '../.cache/frontend-health-followup-mobile.png', fullPage: true });
  await page.screenshot({ path: '../.cache/frontend-health-followup-mobile-top.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test('旧接口没有采集与快照对象时显示缺失，其他真实统计仍可查看', async ({ page }) => {
  await page.route('**/api/health?**', async route => {
    const response = await route.fetch();
    const health: HealthData = await response.json();
    delete health.ingest; delete health.snapshots;
    await route.fulfill({ response, json: health });
  });
  await page.goto('/#token=synthetic-browser-token');
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  const missing = page.locator('.health-metrics dd');
  await expect(missing).toHaveCount(10);
  expect(await missing.allTextContents()).toEqual(Array(10).fill('统计缺失'));
  await expect(page.getByRole('heading', { name: /来源与游标/ })).toBeVisible();
  await expect(page.locator('.health-view')).toContainText('缺失项无法判断，不能视为零');
});

test('健康HTTP失败停止加载，局部重试恢复后清除过时错误', async ({ page }) => {
  let fail = true;
  await page.route('**/api/health?**', async route => {
    if (fail) await route.fulfill({ status: 503, contentType: 'application/json', json: { error: '合成服务暂时不可用' } });
    else await route.continue();
  });
  await page.goto('/#token=synthetic-browser-token');
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  await expect(page.getByRole('heading', { name: '健康统计暂不可用' })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('合成服务暂时不可用');
  await expect(page.getByText('本次未取得统计，无法判断采集是否正常。', { exact: false })).toBeVisible();
  await expect(page.getByRole('status')).toHaveCount(0);
  fail = false;
  await page.getByRole('button', { name: '重新读取健康统计' }).click();
  await expect(page.getByRole('heading', { name: /工作区快照/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: '健康统计暂不可用' })).toHaveCount(0);
  await expect(page.getByText('合成服务暂时不可用', { exact: false })).toHaveCount(0);
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('健康读取的401交给统一鉴权入口', async ({ page }) => {
  await page.route('**/api/health?**', route => route.fulfill({ status: 401, json: { error: '合成令牌已失效' } }));
  await page.goto('/#token=synthetic-browser-token');
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  await expect(page.getByRole('heading', { name: '打开你的研究决定史' })).toBeVisible();
  await expect(page.getByRole('heading', { name: /工作区快照/ })).toHaveCount(0);
});
