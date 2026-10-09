import { expect, test } from '@playwright/test';

test('本机鉴权与链接令牌清除', async ({ page, request }) => {
  const denied = await request.get('/api/projects');
  expect(denied.status()).toBe(401);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '打开你的研究决定史' })).toBeVisible();
  await page.goto('/#token=synthetic-browser-token');
  await expect(page.getByRole('heading', { name: '研究问题', exact: true })).toBeVisible();
  expect(new URL(page.url()).hash).toBe('');
  await expect(page.getByRole('button', { name: /哪些局部界面信号值得继续验证/ })).toBeVisible();
});

test('真实来源、时间线、健康、图引用与响应式界面', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/#token=synthetic-browser-token');
  await expect(page.getByRole('heading', { name: '研究问题', exact: true })).toBeVisible();
  await expect(page.locator('.question-heading h2')).toContainText('哪些局部界面信号');
  await page.screenshot({ path: '../.cache/frontend-question-desktop.png', fullPage: true });
  await page.getByRole('button', { name: '决定时间线', exact: true }).click();
  await expect(page.locator('.timeline-list li')).toHaveCount(13);
  await expect(page.locator('.timeline-list')).toContainText('撤回');
  await page.getByRole('button', { name: '采集健康', exact: true }).click();
  await expect(page.getByRole('heading', { name: '来源与游标' })).toBeVisible();
  await expect(page.getByText('钩子失败').last()).toBeVisible();
  await expect(page.locator('.quality-grid')).toContainText('未知');
  await page.getByRole('button', { name: '研究图', exact: true }).click();
  await expect(page.locator('.react-flow__node')).toHaveCount(6);
  await expect(page.locator('.join-semantics')).toContainText('证据综合');
  await page.screenshot({ path: '../.cache/frontend-graph-desktop.png', fullPage: true });
  await page.locator('.react-flow__node').filter({ hasText: '界面分层分析' }).click();
  await expect(page.getByRole('dialog', { name: /记录 .* 详情/ })).toContainText('记录：模型候选');
  await page.locator('.detail-drawer .evidence-link').first().click();
  await expect(page.getByTestId('evidence-quote')).toHaveText('界面分层分析');
  await expect(page.getByRole('dialog', { name: '原文证据' })).toContainText('原文摘要一致');
  await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await page.getByRole('button', { name: '关闭详情', exact: true }).click();
  await page.getByRole('button', { name: '研究问题', exact: true }).click();
  await expect(page.locator('.question-heading h2')).toContainText('哪些局部界面信号');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: '../.cache/frontend-question-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test('同片段跨页一键确认与项目切换', async ({ page, request }) => {
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '验收批量项目' });
  await page.getByRole('button', { name: /复核队列/ }).click();
  await expect(page.locator('.queue-item')).toHaveCount(50);
  await expect(page.locator('.count-pill')).toHaveText('211 条');
  await page.getByRole('button', { name: '确认整个片段的待复核记录' }).click();
  await expect(page.getByRole('heading', { name: '当前没有待复核记录' })).toBeVisible();
  await page.getByLabel('审核状态', { exact: true }).selectOption('confirmed');
  await expect(page.locator('.queue-item')).toHaveCount(50);
  await expect(page.locator('.count-pill')).toHaveText('211 条');
  for (let offset = 50; offset <= 200; offset += 50) {
    await page.getByRole('button', { name: '下一页', exact: true }).click();
    await expect(page.locator('.queue-item')).toHaveCount(offset === 200 ? 11 : 50);
    await expect(page.locator('.queue-item').first()).toContainText(`批量候选 ${String(offset + 1).padStart(2, '0')}`);
  }
  await expect(page.locator('.queue-item')).toHaveCount(11);
  const headers = { Authorization: 'Bearer synthetic-browser-token' };
  const projects = await (await request.get('/api/projects', { headers })).json();
  const project = projects.projects.find((item: { name: string }) => item.name === '验收批量项目');
  const first = await (await request.get(`/api/claims?project=${project.project_id}&limit=200`, { headers })).json();
  expect(first.next_offset).toBe(200);
  const second = await (await request.get(`/api/claims?project=${project.project_id}&limit=200&offset=${first.next_offset}`, { headers })).json();
  expect(second.next_offset).toBeNull();
  const claims = [...first.claims, ...second.claims];
  expect(claims).toHaveLength(211);
  expect(claims.every((item: { claim_state: string; effective_state: string; confirmation_source: string }) => item.claim_state === 'candidate' && item.effective_state === 'confirmed' && item.confirmation_source === 'human')).toBe(true);
  await page.getByLabel('当前项目', { exact: true }).selectOption({ label: '合成空项目' });
  await expect(page.getByRole('heading', { name: '此筛选下没有记录' })).toBeVisible();
});

test('人工确认、批量原子复核、冲突刷新、修改与检索', async ({ page, request }) => {
  await page.goto('/?view=review#token=synthetic-browser-token');
  await expect(page.getByRole('heading', { name: '复核队列', exact: true })).toBeVisible();
  await expect(page.locator('.queue-item')).toHaveCount(3);
  await expect(page.getByTestId('evidence-quote')).toHaveText('相邻区域呈现不同模式');
  await page.getByRole('button', { name: /确认 C/ }).click();
  await expect(page.locator('.queue-item')).toHaveCount(2);
  await page.locator('.queue-item').filter({ hasText: '撤回候选' }).click();
  await expect(page.locator('.edit-diff').first()).toContainText('与人工已确认记录');
  const headers = { Authorization: 'Bearer synthetic-browser-token' };
  const current = await (await request.get('/api/claims?state=candidate', { headers })).json();
  const changed = await request.post('/api/review', { headers, data: { claim_ids: [current.claims[0].claim_id], action: 'confirm', actor: 'human:另一窗口', expected_revision: current.revision } });
  expect(changed.status()).toBe(200);
  await page.getByRole('button', { name: /驳回 X/ }).click();
  await expect(page.getByRole('alert')).toContainText('图版本已变化');
  await page.getByRole('button', { name: '刷新后重新复核' }).click();
  await expect(page.locator('.queue-item')).toHaveCount(1);
  await page.getByRole('button', { name: /修改 E/ }).click();
  await page.getByLabel('理由', { exact: true }).fill('人工核对后暂缓，等待更多合成证据。');
  await page.getByLabel('决定动作').selectOption('deferred');
  await page.getByRole('button', { name: '修改并确认' }).click();
  await expect(page.getByRole('heading', { name: '当前没有待复核记录' })).toBeVisible();
  await expect(page.locator('.detail-drawer')).toContainText('人工确认');
  await expect(page.locator('.detail-drawer .edit-diff')).toContainText('人工修改前后');
  await page.getByRole('button', { name: '关闭详情' }).click();
  await page.getByLabel('搜索会话原文').fill('界面分层分析');
  await page.getByRole('button', { name: '开始搜索' }).click();
  await expect(page.locator('.search-result')).toHaveCount(3);
  await page.locator('.search-result').first().click();
  await expect(page.getByRole('dialog', { name: '原文证据' })).toContainText('界面分层分析');
});
