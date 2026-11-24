import fs from 'node:fs';
import { defineConfig, devices } from '@playwright/test';

// Local sandboxes ship one preinstalled Chromium; CI installs the pinned one.
const LOCAL_CHROMIUM = '/opt/pw-browsers/chromium';
const executablePath =
  process.env.PW_CHROMIUM_PATH ?? (!process.env.CI && fs.existsSync(LOCAL_CHROMIUM) ? LOCAL_CHROMIUM : undefined);

const PORT = Number(process.env.E2E_PORT ?? 3100);

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  timeout: 60_000,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'off',
    launchOptions: executablePath ? { executablePath } : {},
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // The smoke suite runs against a production build made with mocks on, in its own dist folder.
    command: `npx next build && npx next start --port ${PORT}`,
    env: { NEXT_PUBLIC_API_MOCKS: 'true', NEXT_DIST_DIR: '.next-e2e' },
    url: `http://127.0.0.1:${PORT}/login`,
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
  },
});
