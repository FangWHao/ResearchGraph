import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser',
  timeout: 30000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  outputDir: '../.cache/browser-results',
  use: { baseURL: 'http://127.0.0.1:8791', headless: true, viewport: { width: 1440, height: 1000 }, screenshot: 'only-on-failure', trace: 'off' },
  webServer: {
    command: '.venv/bin/python -m tests.frontend_server --port 8791 --web-dir web/dist',
    cwd: '..',
    url: 'http://127.0.0.1:8791/',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
