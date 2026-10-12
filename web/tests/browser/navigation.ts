import type { Page } from '@playwright/test';

/** 只适配导航收起；每个原业务用例仍自己等待原页面、请求和状态。 */
export async function openWorkspaceView(page: Page, name: string | RegExp) {
  const navigation = page.getByRole('navigation', { name: '研究工作区', exact: true });
  const entry = navigation.getByRole('button', { name, exact: typeof name === 'string' });
  if (!await entry.isVisible()) await navigation.getByRole('button', { name: '更多工具', exact: true }).click();
  await entry.click();
}
