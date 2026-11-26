import { expect, type Page } from '@playwright/test';
import { ALL_ENDPOINTS } from '../src/api/contracts';
import { matchEndpoint, routeOf } from '../src/api/routing';

/** The API of the build with mocks (default of `src/api/config.ts`). */
const MOCKED_API_URL = 'http://localhost:8000';

/** Where the build without mocks thinks the backend is. Nothing listens there: the tests answer for it with `page.route`. */
export const REAL_API_URL = 'http://localhost:8000';

/**
 * The hybrid smoke only runs against a real backend: `E2E_HYBRID_API_URL` says where it is (for example
 * `http://127.0.0.1:8000`). Without it the hybrid build is not made and the spec is skipped.
 */
export const HYBRID_API_URL = (process.env.E2E_HYBRID_API_URL ?? '').replace(/\/+$/, '');
/** `NEXT_PUBLIC_API_REAL` of the hybrid build. Empty: what the contract marks as existing. */
export const HYBRID_REAL = process.env.E2E_HYBRID_REAL ?? '';

/** Header of the answers a mock gives (`MOCK_HEADER` in src/api/mocks/http.ts), as Playwright reads it. */
const MOCK_HEADER = 'x-oc-mock';

export interface WatchOptions {
  /** The build under test is the hybrid one: the API is at `apiUrl` and `real` is its `NEXT_PUBLIC_API_REAL`. */
  hybrid?: { apiUrl: string; real: string };
}

export interface Problems {
  consoleErrors: string[];
  failedRequests: string[];
}

/**
 * Collects what the smoke test must not see: console errors, uncaught
 * exceptions, requests that fail, API answers with an error status and API
 * answers that came from the wrong side: in mock mode, any that the mock worker
 * did not give; in hybrid mode, a mocked endpoint answered by the network, a
 * real one answered by a mock, or a path that is not in the contract.
 */
export function watch(page: Page, options: WatchOptions = {}): Problems {
  const problems: Problems = { consoleErrors: [], failedRequests: [] };
  const apiUrl = options.hybrid?.apiUrl ?? MOCKED_API_URL;
  page.on('console', (message) => {
    if (message.type() === 'error') problems.consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => problems.consoleErrors.push(`pageerror: ${error.message}`));
  page.on('requestfailed', (request) => {
    const failure = request.failure()?.errorText ?? '';
    // Navigating away cancels in-flight prefetches: not a failure of the app.
    if (failure.includes('ERR_ABORTED')) return;
    problems.failedRequests.push(`${request.method()} ${request.url()} · ${failure}`);
  });
  page.on('response', (response) => {
    const request = response.request();
    const label = `${request.method()} ${response.url()}`;
    // (Hybrid: the preflight of a request the worker lets through is the browser's own, not an API answer.)
    const preflight = Boolean(options.hybrid) && request.method() === 'OPTIONS';
    if (response.url().startsWith(`${apiUrl}/api/`) && !preflight) {
      const mocked = response.headers()[MOCK_HEADER] !== undefined;
      // Mock and hybrid: every API request goes through the MSW worker, which answers it or lets it through.
      if (!response.fromServiceWorker()) problems.failedRequests.push(`${label} · no pasó por MSW`);
      // Mock mode: a mock answers everything. What the worker let through also "comes from the service
      // worker", so only the mark of the mocks tells an answer of theirs from one that leaked to the network.
      else if (!options.hybrid && !mocked) problems.failedRequests.push(`${label} · MSW lo dejó pasar a la red`);
      else if (options.hybrid) {
        const endpoint = matchEndpoint(ALL_ENDPOINTS, request.method(), new URL(response.url()).pathname);
        if (!endpoint) problems.failedRequests.push(`${label} · no está en el contrato`);
        else {
          const expected = routeOf(endpoint, { mode: 'hybrid', real: options.hybrid.real, overrides: {} });
          if (expected === 'mock' && !mocked) problems.failedRequests.push(`${label} · ${endpoint.id} es simulado y lo respondió la red`);
          if (expected === 'real' && mocked) problems.failedRequests.push(`${label} · ${endpoint.id} es real y lo respondió un mock`);
        }
      }
    }
    if (response.status() >= 400) problems.failedRequests.push(`${label} · ${response.status()}`);
  });
  return problems;
}

export function expectClean(problems: Problems, where: string): void {
  expect(problems.failedRequests, `Pedidos fallidos en ${where}`).toEqual([]);
  expect(problems.consoleErrors, `Errores de consola en ${where}`).toEqual([]);
}

/** Picks the mock scenario before the app boots (same keys the dev panel writes). */
export async function useScenario(page: Page, scenario: string): Promise<void> {
  await page.addInitScript((id) => {
    if (window.localStorage.getItem('oc.mock.scenario') !== id) {
      window.localStorage.clear();
      window.localStorage.setItem('oc.mock.scenario', id);
    }
  }, scenario);
}

/** The mocked API is ready and nothing is still loading. */
export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState('networkidle');
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
}
