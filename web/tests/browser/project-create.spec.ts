import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createWriteStream, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { ProjectCreateResult } from '../../src/projectCreate';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const token = 'synthetic-project-create-token'; const headers = { Authorization: `Bearer ${token}` };
const temporary = mkdtempSync(join(tmpdir(), 'rg-browser-project-create-'));
const firstRoot = join(temporary, 'first-workspace'); const secondRoot = join(temporary, 'second-workspace'); const home = join(temporary, 'synthetic-home');
mkdirSync(firstRoot); mkdirSync(secondRoot); mkdirSync(join(home, '.claude', 'projects'), { recursive: true });
let server: ChildProcess | undefined; let origin = '';
test.describe.configure({ mode: 'serial' });
test.beforeAll(async () => {
  // 只在这个公开合成进程里改 home 的返回值，实际用户目录不参与验收。
  const script = `import sys\nfrom pathlib import Path\nfrom unittest.mock import patch\nfrom rg.store.database import Store\nfrom rg.api.server import LocalServer\nStore(Path(sys.argv[1])).close()\nwith patch('pathlib.Path.home', return_value=Path(sys.argv[3])):\n    server = LocalServer(Path(sys.argv[1]), Path(sys.argv[2]), port=0, token='${token}')\n    print(server.origin, flush=True)\n    try:\n        server.serve_forever()\n    except KeyboardInterrupt:\n        pass\n    finally:\n        server.server_close()\n`;
  const log = createWriteStream(join(root, '.cache/frontend-project-create-server.log'));
  server = spawn(join(root, '.venv/bin/python'), ['-u', '-c', script, join(temporary, 'store'), join(root, 'web/dist'), home], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout!.on('data', (chunk: Buffer) => { const match = chunk.toString().match(/http:\/\/127\.0\.0\.1:\d+/); if (match) origin = match[0]; });
  server.stdout!.pipe(log); server.stderr!.pipe(log);
  await expect.poll(async () => { if (server?.exitCode !== null) throw new Error('合成创建服务提前退出，见 .cache/frontend-project-create-server.log'); if (!origin) return 0; try { return (await fetch(`${origin}/api/projects`, { headers })).status; } catch { return 0; } }).toBe(200);
});
test.afterAll(async () => {
  if (server && server.exitCode === null) { const closed = new Promise<void>(resolve => server!.once('exit', () => resolve())); server.kill('SIGINT'); await closed; }
  rmSync(temporary, { recursive: true, force: true });
});

test('真实空库创建项目，明确采集且来源缺失不丢项目，重复同名同目录打开原项目', async ({ page, request }) => {
  expect(await (await request.get(`${origin}/api/projects`, { headers })).json()).toMatchObject({ projects: [] });
  await page.goto(`${origin}/#token=${token}`);
  const empty = page.getByRole('region', { name: '创建第一个项目', exact: true });
  await expect(empty).toBeVisible();
  await empty.getByRole('button', { name: '创建第一个项目', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '创建研究项目', exact: true });
  await expect(dialog.getByLabel('登记本机 Claude Code / Codex 会话来源')).not.toBeChecked();
  await dialog.getByLabel('项目名称', { exact: true }).fill(' 合成首次项目 ');
  await dialog.getByLabel('工作区绝对目录', { exact: true }).fill(firstRoot);
  await dialog.getByLabel('登记本机 Claude Code / Codex 会话来源').check();
  const creation = page.waitForResponse(response => response.url() === `${origin}/api/projects` && response.request().method() === 'POST');
  await dialog.getByRole('button', { name: '创建并打开项目', exact: true }).click();
  const response = await creation; expect(response.status()).toBe(200);
  expect(response.request().postDataJSON()).toEqual({ name: '合成首次项目', root: firstRoot, collect_local: true });
  const saved: ProjectCreateResult = await response.json();
  expect(saved.created).toBe(true); expect(saved.project).toMatchObject({ name: '合成首次项目', remote_model_allowed: 0, sessions: 0, claims: 0 });
  expect(saved.sources).toEqual([{ tool: 'claude', path: join(home, '.claude/projects'), state: 'registered' }, { tool: 'codex', path: join(home, '.codex/sessions'), state: 'missing' }]);
  await expect(dialog).toHaveCount(0); await expect(empty).toHaveCount(0);
  await expect(page.getByLabel('当前项目', { exact: true })).toHaveValue(saved.project.project_id);
  await expect(page.locator('.project-source-notice')).toContainText('项目已保存');
  await expect(page.locator('.project-source-notice')).toContainText('Codex · 目录不存在');
  await page.getByRole('button', { name: '创建项目', exact: true }).click();
  await dialog.getByLabel('项目名称', { exact: true }).fill(saved.project.name);
  await dialog.getByLabel('工作区绝对目录', { exact: true }).fill(firstRoot);
  const duplicate = page.waitForResponse(value => value.url() === `${origin}/api/projects` && value.request().method() === 'POST');
  await dialog.getByRole('button', { name: '创建并打开项目', exact: true }).click();
  const repeated = await duplicate; expect(repeated.status()).toBe(200);
  const replay: ProjectCreateResult = await repeated.json(); expect(replay.created).toBe(false); expect(replay.project.project_id).toBe(saved.project.project_id); expect(replay.sources).toEqual([]);
  expect((await (await request.get(`${origin}/api/projects`, { headers })).json()).projects).toHaveLength(1);
});

test('项目列表加载和503不冒充空库，创建400保留输入，修正目录后真实创建成功', async ({ page }) => {
  let failing = true; let release!: () => void; const hold = new Promise<void>(resolve => { release = resolve; });
  await page.route(`${origin}/api/projects`, async route => { if (route.request().method() === 'GET' && failing) { await hold; await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '合成项目列表读取失败' }) }); } else await route.continue(); });
  await page.goto(`${origin}/#token=${token}`);
  await expect(page.getByRole('heading', { name: '研究问题', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: '创建第一个项目', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '创建项目', exact: true })).toBeDisabled();
  release();
  const error = page.getByRole('region', { name: '项目列表暂不可用', exact: true });
  await expect(error).toContainText('尚无法判断是否已有项目');
  await expect(page.getByRole('region', { name: '创建第一个项目', exact: true })).toHaveCount(0);
  failing = false; await error.getByRole('button', { name: '重新读取项目列表', exact: true }).click();
  await page.getByRole('button', { name: '创建项目', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '创建研究项目', exact: true });
  await dialog.getByLabel('项目名称', { exact: true }).fill('合成第二项目');
  const missing = join(temporary, 'missing-workspace');
  await dialog.getByLabel('工作区绝对目录', { exact: true }).fill(missing);
  const rejected = page.waitForResponse(response => response.url() === `${origin}/api/projects` && response.request().method() === 'POST');
  await dialog.getByRole('button', { name: '创建并打开项目', exact: true }).click();
  expect((await rejected).status()).toBe(400);
  await expect(dialog.getByRole('alert')).toContainText('输入已保留');
  await expect(dialog.getByLabel('项目名称', { exact: true })).toHaveValue('合成第二项目');
  await expect(dialog.getByLabel('工作区绝对目录', { exact: true })).toHaveValue(missing);
  await dialog.getByLabel('工作区绝对目录', { exact: true }).fill(secondRoot);
  const accepted = page.waitForResponse(response => response.url() === `${origin}/api/projects` && response.request().method() === 'POST');
  await dialog.getByRole('button', { name: '创建并打开项目', exact: true }).click();
  const response = await accepted; expect(response.status()).toBe(200);
  const saved: ProjectCreateResult = await response.json(); expect(saved.sources).toEqual([]); expect(saved.created).toBe(true);
  await expect(dialog).toHaveCount(0);
  await expect(page.getByLabel('当前项目', { exact: true })).toHaveValue(saved.project.project_id);
});
