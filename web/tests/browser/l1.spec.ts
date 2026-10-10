import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { EvidenceData, L1Evidence, L1Run } from '../../src/types';

const headers = { Authorization: 'Bearer synthetic-browser-token' };

async function openEvent(page: Page, id: number) {
  const dialog = page.getByRole('dialog', { name: '原文证据', exact: true });
  if (await dialog.isVisible()) await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await page.getByLabel('搜索会话原文').fill('');
  await page.getByRole('button', { name: '开始搜索', exact: true }).click();
  await page.getByLabel('全库事件编号', { exact: true }).fill(id.toString());
  await page.getByRole('button', { name: '按编号打开原文', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: `原文 #${id}`, exact: true })).toBeVisible();
  await expect(dialog.getByRole('heading', { name: '本原文派生状态', exact: true })).toBeVisible();
  return dialog;
}

test('真实工具运行观测与原文导航保留未知退出和双时间', async ({ page, request }) => {
  await page.goto('/#token=synthetic-browser-token');
  const scenarios = [
    { id: 11, state: 'requested', code: null, label: '已请求' },
    { id: 12, state: 'started', code: null, label: '已有启动记录' },
    { id: 14, state: 'exited', code: 0, label: '已结束 · 退出码 0' },
    { id: 16, state: 'exited', code: 2, label: '已结束 · 退出码 2' },
    { id: 18, state: 'unknown', code: null, label: '运行状态未知' },
  ];
  for (const scenario of scenarios) {
    const response = await request.get(`/api/evidence/${scenario.id}?context=0`, { headers });
    expect(response.status()).toBe(200);
    const evidence: EvidenceData = await response.json();
    expect(evidence.l1?.runs).toHaveLength(1);
    expect(evidence.l1!.runs[0]).toMatchObject({ state: scenario.state, exit_code: scenario.code, observations_partial: false });
    expect(evidence.l1!.runs[0].observations.every(item => item.occurred_at === null)).toBe(true);
    const run = evidence.l1!.runs[0];
    expect(run.command_truncated).toBe(false);
    expect(run.command_total_bytes).toBe(new TextEncoder().encode(run.command ?? '').length);
    for (const observation of run.observations) {
      expect(typeof observation.details).toBe('object');
      expect(observation.details).not.toBeNull();
      expect(Array.isArray(observation.details)).toBe(false);
      expect(observation.details).not.toHaveProperty('command');
      if (observation.state === 'requested') {
        expect(observation.details.command_sha256).toMatch(/^[a-f0-9]{64}$/);
        expect(observation.details.cwd).toBe('/synthetic/research');
        expect(observation.details.root_id).toBe(run.root_id);
      }
    }
    const dialog = await openEvent(page, scenario.id);
    const runs = dialog.getByRole('region', { name: '运行证据', exact: true });
    await expect(runs.locator('.l1-card > .l1-heading > strong')).toHaveText(scenario.label);
    await expect(runs).toContainText('运行输入、输出版本未记录');
    const occurred = runs.locator('.l1-times > div').filter({ hasText: '发生时间' });
    expect(await occurred.allTextContents()).toEqual(evidence.l1!.runs[0].observations.map(() => '发生时间时间未知'));
    if (scenario.id === 12) {
      await expect(runs).toContainText('执行器会话标识：88');
      await runs.getByRole('button', { name: '观测原文 #13', exact: true }).click();
      await expect(dialog.getByRole('heading', { name: '原文 #13', exact: true })).toBeVisible();
      await expect(dialog.locator('.raw-event.focused')).toContainText('session_id');
      await expect(dialog.getByRole('region', { name: '运行证据', exact: true }).locator('.l1-card > .l1-heading > strong')).toHaveText('已有启动记录');
    }
    if (scenario.id === 18) {
      await runs.getByRole('button', { name: '观测原文 #19', exact: true }).click();
      await expect(dialog.getByRole('heading', { name: '原文 #19', exact: true })).toBeVisible();
      await expect(dialog.locator('.raw-event.focused')).toContainText('合成stdout exit_code: 9');
      await expect(runs.locator('.l1-card > .l1-heading > strong')).toHaveText('运行状态未知');
      await runs.scrollIntoViewIfNeeded();
      expect(new URL(page.url()).hash).toBe('');
      await page.screenshot({ path: '../.cache/frontend-l1-run-desktop.png' });
    }
  }
});

test('真实完整编辑仍为候选文本，补丁不充当完整版本，小屏原文可导航', async ({ page, request }) => {
  await page.goto('/#token=synthetic-browser-token');
  const full: EvidenceData = await (await request.get('/api/evidence/21?context=0', { headers })).json();
  expect(full.l1!.edits[0].diff).toMatchObject({ available: true, format: 'reported_versions', complete_versions: true });
  expect(full.artifact_versions).toHaveLength(2);
  expect(full.artifact_versions.every(item => item.basis === 'direct_record' && item.claim_state === 'candidate' && item.representation === 'tool_reported_utf8')).toBe(true);
  const dialog = await openEvent(page, 20);
  let edits = dialog.getByRole('region', { name: '编辑证据', exact: true });
  await expect(edits.locator('.l1-card > .l1-heading > strong')).toHaveText('工具报告版本差异 · 待复核');
  await expect(edits.getByLabel('候选前后版本差异正文')).toContainText('-old\n+新');
  await expect(edits).toContainText('不证明当时文件原始字节完全一致');
  await edits.getByRole('button', { name: '编辑结果原文 #21', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: '原文 #21', exact: true })).toBeVisible();
  const versions = dialog.getByRole('region', { name: '相关文件版本', exact: true });
  await expect(versions.locator('li')).toHaveCount(2);
  await expect(versions).toContainText('编辑前'); await expect(versions).toContainText('编辑后');
  expect(await versions.locator('.l1-heading > span').allTextContents()).toEqual(['待复核', '待复核']);
  edits = dialog.getByRole('region', { name: '编辑证据', exact: true });
  await edits.scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-l1-edit-desktop.png' });
  await versions.scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-l1-versions-desktop.png' });

  const patch: EvidenceData = await (await request.get('/api/evidence/24?context=0', { headers })).json();
  expect(patch.l1!.edits[0]).toMatchObject({ before_version: null, after_version: null, gap: 'preimage_unknown', diff: { available: true, format: 'patch_only', complete_versions: false } });
  expect(patch.artifact_versions).toEqual([]);
  await openEvent(page, 23);
  await expect(edits.locator('.l1-card > .l1-heading > strong')).toHaveText('仅有补丁');
  await expect(edits.getByLabel('工具报告的候选编辑前全文', { exact: true })).toHaveCount(0);
  await expect(edits.getByLabel('工具报告的候选编辑后全文', { exact: true })).toHaveCount(0);
  await expect(edits.getByLabel('工具报告的补丁正文')).toContainText('*** Update File: 合成仅补丁.py');
  await page.setViewportSize({ width: 390, height: 844 });
  await edits.scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/frontend-l1-patch-mobile.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  await edits.getByRole('button', { name: '编辑结果原文 #24', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: '原文 #24', exact: true })).toBeVisible();
  await expect(edits.locator('.l1-card > .l1-heading > strong')).toHaveText('仅有补丁');
});

test('派生缺口、部分清单与超限正文不会变成完整证据', async ({ page }) => {
  let state: 'waiting' | 'failed' | 'done' = 'waiting';
  let legacy = false;
  await page.route('**/api/evidence/21?**', async route => {
    const response = await route.fetch();
    const evidence: EvidenceData = await response.json();
    evidence.l1!.derivation = { state, error: state === 'waiting' ? 'call_not_recorded' : state === 'failed' ? 'ambiguous_call_id' : 'rg_context_in_tool_payload', updated_at: '2026-10-09T00:00:00Z' };
    evidence.l1!.runs_partial = true;
    evidence.l1!.edits_partial = true;
    evidence.artifact_versions_partial = true;
    evidence.l1!.edits[0].diff = { available: false, reason: '差异超过展示上限', gap: 'preimage_unknown' };
    if (legacy) { delete evidence.l1; delete evidence.artifact_versions_partial; }
    await route.fulfill({ response, json: evidence });
  });
  await page.goto('/#token=synthetic-browser-token');
  await page.getByLabel('搜索会话原文').fill('合成完整编辑');
  await page.getByRole('button', { name: '开始搜索', exact: true }).click();
  await page.getByLabel('全库事件编号', { exact: true }).fill('1e2');
  await page.getByRole('button', { name: '按编号打开原文', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('有效的正整数事件编号');
  await expect(page.getByRole('dialog', { name: '原文证据', exact: true })).toHaveCount(0);
  const dialog = await openEvent(page, 21);
  const derivation = dialog.getByRole('region', { name: '本原文派生状态', exact: true });
  await expect(derivation).toContainText('等待关联');
  await expect(derivation).toContainText('尚未找到对应请求记录');
  for (const name of ['运行证据', '编辑证据', '相关文件版本']) {
    await expect(dialog.getByRole('region', { name, exact: true })).toContainText('只显示部分');
  }
  const edits = dialog.getByRole('region', { name: '编辑证据', exact: true });
  await expect(edits).toContainText('差异超过展示上限');
  await expect(edits.locator('.l1-diff')).toHaveCount(0);
  state = 'failed';
  await page.getByLabel('前后上下文').selectOption('1');
  await expect(derivation).toContainText('处理异常');
  await expect(derivation).toContainText('调用标识对应多个请求');
  state = 'done';
  await page.getByLabel('前后上下文').selectOption('0');
  await expect(derivation).toContainText('已处理');
  await expect(derivation).toContainText('注入内容已排除');
  legacy = true;
  await page.getByLabel('前后上下文').selectOption('1');
  await expect(dialog.getByRole('region', { name: '运行与编辑证据', exact: true })).toContainText('相关证据是否存在未知');
  await expect(dialog.getByRole('region', { name: '相关文件版本', exact: true })).toContainText('无法判断是否还有未显示的版本');
});

test('命令预览与缺失完整性标记保留缺口，仍可返回请求原文', async ({ page }) => {
  let legacy = false;
  const preview = '令'.repeat(2666) + 'ab';
  await page.route('**/api/evidence/15?**', async route => {
    const response = await route.fetch();
    const evidence: EvidenceData = await response.json();
    const run = evidence.l1!.runs[0];
    Object.assign(run, { command: preview, command_total_bytes: 11000, command_truncated: true, observations_partial: true });
    if (legacy) {
      const oldRun: Partial<L1Run> = run;
      const oldL1: Partial<L1Evidence> = evidence.l1!;
      delete oldRun.command_truncated;
      delete oldRun.observations_partial;
      delete oldL1.runs_partial;
      delete oldL1.edits_partial;
    }
    await route.fulfill({ response, json: evidence });
  });
  await page.goto('/#token=synthetic-browser-token');
  const dialog = await openEvent(page, 15);
  const runs = dialog.getByRole('region', { name: '运行证据', exact: true });
  await expect(runs.getByLabel('历史命令预览，仅供阅读')).toHaveText(preview);
  await expect(runs).toContainText('当前仅为命令预览，完整命令见请求原文');
  await expect(runs).toContainText('命令共 11000 UTF-8 字节');
  await expect(runs).toContainText('当前列表不完整');
  expect(new TextEncoder().encode(await runs.locator('.l1-command').textContent() ?? '').length).toBe(8000);
  const command = runs.getByLabel('历史命令预览，仅供阅读');
  await command.focus();
  await command.press('ArrowDown');
  await expect.poll(() => command.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  await command.evaluate(node => { node.scrollTop = 0; });
  await runs.locator('.l1-card').evaluate(node => node.scrollIntoView({ block: 'start' }));
  await page.screenshot({ path: '../.cache/frontend-l1-command-preview-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await runs.locator('.l1-card').evaluate(node => node.scrollIntoView({ block: 'start' }));
  await page.screenshot({ path: '../.cache/frontend-l1-command-preview-mobile.png' });
  expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  legacy = true;
  await page.getByLabel('前后上下文').selectOption('1');
  await expect(runs.getByLabel('历史命令内容，完整性未知')).toHaveText(preview);
  await expect(runs).toContainText('当前显示内容是否完整未知');
  await expect(runs).toContainText('无法判断是否还有未显示的运行');
  await expect(runs).toContainText('无法判断是否还有未显示的观测');
  await expect(dialog.getByRole('region', { name: '编辑证据', exact: true })).toContainText('无法判断是否还有未显示的编辑');
  await runs.getByRole('button', { name: '请求原文 #14', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: '原文 #14', exact: true })).toBeVisible();
  await expect(dialog.locator('.raw-event.focused')).toContainText('合成运行退出零');
});

test('事件编号入口保留缺失事件错误，401返回统一鉴权入口', async ({ page, request }) => {
  const response = await request.get('/api/evidence/999999?context=0', { headers });
  expect(response.status()).toBe(404);
  await page.goto('/?view=search#token=synthetic-browser-token');
  await page.getByLabel('全库事件编号', { exact: true }).fill('999999');
  await page.getByRole('button', { name: '按编号打开原文', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '原文证据', exact: true });
  await expect(dialog.getByRole('heading', { name: '原文暂不可用', exact: true })).toBeVisible();
  await expect(dialog).toContainText('原始事件不存在');
  await page.getByRole('button', { name: '关闭原文', exact: true }).click();
  await page.route('**/api/evidence/11?**', route => route.fulfill({ status: 401, json: { error: '合成事件令牌已失效' } }));
  await page.getByLabel('全库事件编号', { exact: true }).fill('11');
  await page.getByRole('button', { name: '按编号打开原文', exact: true }).click();
  await expect(page.getByRole('heading', { name: '打开你的研究决定史', exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('合成事件令牌已失效');
  await expect(dialog).toHaveCount(0);
});
