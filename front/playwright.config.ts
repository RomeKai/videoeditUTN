import fs from 'node:fs';
import { defineConfig, devices } from '@playwright/test';
import { REAL_API_URL } from './e2e/helpers';

// Local sandboxes ship one preinstalled Chromium; CI installs the pinned one.
const LOCAL_CHROMIUM = '/opt/pw-browsers/chromium';
const executablePath =
  process.env.PW_CHROMIUM_PATH ?? (!process.env.CI && fs.existsSync(LOCAL_CHROMIUM) ? LOCAL_CHROMIUM : undefined);

const PORT = Number(process.env.E2E_PORT ?? 3100);
/** The build without mocks is served on the next port. */
const REAL_PORT = PORT + 1;

const chromium = { ...devices['Desktop Chrome'], launchOptions: executablePath ? { executablePath } : {} };

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  timeout: 60_000,
  use: { trace: 'off' },
  projects: [
    // Every `*.spec.ts` runs against the build with mocks…
    { name: 'chromium', testIgnore: /\.real\.spec\.ts$/, use: { ...chromium, baseURL: `http://127.0.0.1:${PORT}` } },
    // …and `*.real.spec.ts` against the build that talks to the backend, with the API faked in the browser.
    { name: 'sin-mocks', testMatch: /\.real\.spec\.ts$/, use: { ...chromium, baseURL: `http://127.0.0.1:${REAL_PORT}` } },
  ],
  // Two production builds, each in its own dist folder, one after the other.
  webServer: [
    {
      command: `npx next build && npx next start --port ${PORT}`,
      env: { NEXT_PUBLIC_API_MOCKS: 'true', NEXT_DIST_DIR: '.next-e2e' },
      url: `http://127.0.0.1:${PORT}/login`,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
    },
    {
      command: `npx next build && npx next start --port ${REAL_PORT}`,
      env: { NEXT_PUBLIC_API_MOCKS: 'false', NEXT_PUBLIC_API_URL: REAL_API_URL, NEXT_DIST_DIR: '.next-e2e-real' },
      url: `http://127.0.0.1:${REAL_PORT}/login`,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
    },
  ],
});
