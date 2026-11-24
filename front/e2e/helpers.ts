import { expect, type Page } from '@playwright/test';

export interface Problems {
  consoleErrors: string[];
  failedRequests: string[];
}

/**
 * Collects what the smoke test must not see: console errors, uncaught
 * exceptions, requests that fail and API answers with an error status.
 */
export function watch(page: Page): Problems {
  const problems: Problems = { consoleErrors: [], failedRequests: [] };
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
    if (response.status() >= 400) problems.failedRequests.push(`${response.request().method()} ${response.url()} · ${response.status()}`);
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
