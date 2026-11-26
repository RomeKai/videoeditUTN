import fs from 'node:fs';
import { defineConfig, devices } from '@playwright/test';
import { HYBRID_API_URL, HYBRID_REAL, REAL_API_URL } from './e2e/helpers';

// Local sandboxes ship one preinstalled Chromium; CI installs the pinned one.
const LOCAL_CHROMIUM = '/opt/pw-browsers/chromium';
const executablePath =
  process.env.PW_CHROMIUM_PATH ?? (!process.env.CI && fs.existsSync(LOCAL_CHROMIUM) ? LOCAL_CHROMIUM : undefined);

const PORT = Number(process.env.E2E_PORT ?? 3100);
/** The build without mocks is served on the next port. */
const REAL_PORT = PORT + 1;
/** The hybrid build (only made when `E2E_HYBRID_API_URL` names a real backend) is served on the one after. */
const HYBRID_PORT = Number(process.env.E2E_HYBRID_PORT ?? PORT + 2);

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
    { name: 'chromium', testIgnore: /\.(real|hybrid)\.spec\.ts$/, use: { ...chromium, baseURL: `http://127.0.0.1:${PORT}` } },
    // …and `*.real.spec.ts` against the build that talks to the backend, with the API faked in the browser.
    { name: 'sin-mocks', testMatch: /\.real\.spec\.ts$/, use: { ...chromium, baseURL: `http://127.0.0.1:${REAL_PORT}` } },
    // …and `*.hybrid.spec.ts` against the hybrid build and a real Django backend. Skipped without `E2E_HYBRID_API_URL`.
    { name: 'hibrido', testMatch: /\.hybrid\.spec\.ts$/, use: { ...chromium, baseURL: `http://127.0.0.1:${HYBRID_PORT}` } },
  ],
  // Two production builds (three with the hybrid one), each in its own dist folder, one after the other.
  webServer: [
    {
      command: `npx next build && npx next start --port ${PORT}`,
      // The older variable on purpose (it must keep working); the empty mode keeps a local .env from deciding.
      // A production build with mocks has to be asked for (`API_MOCKS_IN_BUILD`, see next.config.ts).
      env: { NEXT_PUBLIC_API_MODE: '', NEXT_PUBLIC_API_MOCKS: 'true', API_MOCKS_IN_BUILD: 'true', NEXT_DIST_DIR: '.next-e2e' },
      url: `http://127.0.0.1:${PORT}/login`,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
    },
    {
      // Built as real and started with the variables of another mode: the mode is fixed by the build, so they must change nothing.
      command: `npx next build && NEXT_PUBLIC_API_MODE=hybrid NEXT_PUBLIC_API_MOCKS=true NEXT_PUBLIC_API_REAL=billing npx next start --port ${REAL_PORT}`,
      env: { NEXT_PUBLIC_API_MODE: '', NEXT_PUBLIC_API_MOCKS: 'false', NEXT_PUBLIC_API_URL: REAL_API_URL, NEXT_DIST_DIR: '.next-e2e-real' },
      url: `http://127.0.0.1:${REAL_PORT}/login`,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
    },
    ...(HYBRID_API_URL
      ? [
          {
            command: `npx next build && npx next start --port ${HYBRID_PORT}`,
            env: { NEXT_PUBLIC_API_MODE: 'hybrid', NEXT_PUBLIC_API_URL: HYBRID_API_URL, NEXT_PUBLIC_API_REAL: HYBRID_REAL, API_MOCKS_IN_BUILD: 'true', NEXT_DIST_DIR: '.next-e2e-hybrid' },
            url: `http://127.0.0.1:${HYBRID_PORT}/login`,
            reuseExistingServer: !process.env.CI,
            timeout: 300_000,
          },
        ]
      : []),
  ],
});
