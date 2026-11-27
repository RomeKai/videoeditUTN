import { execFileSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

// Vitest runs from front/.
const root = process.cwd();
const served = join(root, 'public/mockServiceWorker.js');
const pristine = join(root, 'node_modules/msw/lib/mockServiceWorker.js');
const patch = join(root, 'scripts/patch-msw-worker.mjs');

/** The fetch listener up to the point where the worker takes the request. */
function beforeRespondWith(source: string): string {
  const start = source.indexOf("addEventListener('fetch'");
  const end = source.indexOf('event.respondWith(', start);
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return source.slice(start, end);
}

function expectBypass(source: string) {
  const head = beforeRespondWith(source);
  expect(head).toContain("event.request.headers.has('range')");
  expect(head).toContain("event.request.destination === 'video'");
  expect(head).toContain("event.request.destination === 'audio'");
  expect(head).toContain("pathname.startsWith('/media/')");
}

describe('service worker de los mocks', () => {
  it('no toma los pedidos de media: si falla, corré `npm run msw:init`', () => {
    // Without the bypass a <video> seek loses its Range header and the server sends the whole file.
    expectBypass(readFileSync(served, 'utf8'));
  });

  it('el parche se aplica al worker que genera `msw init` y no se repite', () => {
    const dir = mkdtempSync(join(tmpdir(), 'msw-worker-'));
    try {
      const copy = join(dir, 'mockServiceWorker.js');
      copyFileSync(pristine, copy);
      expect(readFileSync(copy, 'utf8')).not.toContain('onecreator:media-bypass');
      execFileSync(process.execPath, [patch, copy]);
      const once = readFileSync(copy, 'utf8');
      expectBypass(once);
      execFileSync(process.execPath, [patch, copy]);
      expect(readFileSync(copy, 'utf8')).toBe(once);
      // The worker that is served is exactly the generated one plus the patch.
      expect(readFileSync(served, 'utf8')).toBe(once);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
