import { expect, test } from '@playwright/test';
import { settle, useScenario } from './helpers';

/**
 * The mocks' service worker must leave media alone. When it took a <video> request, its passthrough
 * rebuilt it without the `Range` header, and the server sent the whole file on every seek (the
 * bypass is added by scripts/patch-msw-worker.mjs, after `msw init`).
 */
test('un <video> pide por rango aunque el service worker de los mocks esté activo', async ({ page, context }) => {
  const ranges: Array<string | undefined> = [];
  // On the context: it also sees what a service worker fetches. No real video is needed, only what the browser asks for.
  await context.route('**/media/e2e/rango.mp4', async (route) => {
    ranges.push(route.request().headers()['range']);
    await route.fulfill({ status: 404, body: '' });
  });
  await useScenario(page, 'sin-sesion');
  await page.goto('/login');
  await settle(page);
  // The worker of the mocks controls the page and answers the API.
  await page.waitForFunction(() => navigator.serviceWorker.controller?.scriptURL.endsWith('/mockServiceWorker.js') ?? false);

  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        const video = document.createElement('video');
        video.preload = 'auto';
        video.addEventListener('error', () => resolve(), { once: true });
        video.src = '/media/e2e/rango.mp4';
        document.body.append(video);
      }),
  );
  expect(ranges.length).toBeGreaterThan(0);
  for (const range of ranges) expect(range).toMatch(/^bytes=\d+-/);
});
