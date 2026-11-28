// @vitest-environment node
import fs from 'node:fs';
import path from 'node:path';
import { ESLint } from 'eslint';
import { describe, expect, it } from 'vitest';

const root = path.resolve(__dirname, '..');

/**
 * A build can go to any folder (`NEXT_DIST_DIR`, see next.config.ts): the Playwright builds do, and so does
 * whoever builds next to a running app. None of them is source: the linter must not read what Next wrote there.
 */
describe('las carpetas de build', () => {
  it('ESLint ignora cualquier `.next*`, no solo las que conoce de nombre', async () => {
    const eslint = new ESLint({ cwd: root });
    for (const folder of ['.next', '.next-e2e', '.next-e2e-real', '.next-e2e-hybrid', '.next-ensayo', '.next-demo-2']) {
      expect(await eslint.isPathIgnored(path.join(root, folder, 'types/app/layout.ts')), folder).toBe(true);
      expect(await eslint.isPathIgnored(path.join(root, folder, 'server/chunks/123.js')), folder).toBe(true);
    }
    // What is source is still read.
    expect(await eslint.isPathIgnored(path.join(root, 'src/api/client.ts'))).toBe(false);
    expect(await eslint.isPathIgnored(path.join(root, 'next.config.ts'))).toBe(false);
  });

  it('el tsconfig trae anotadas las carpetas de los builds de siempre: esos no lo reescriben', () => {
    // Next adds `<distDir>/types/**/*.ts` to `include` when it is not there, word for word (a glob does not count).
    const tsconfig = JSON.parse(fs.readFileSync(path.join(root, 'tsconfig.json'), 'utf8')) as { include: string[] };
    const playwright = fs.readFileSync(path.join(root, 'playwright.config.ts'), 'utf8');
    const folders = ['.next', ...[...playwright.matchAll(/NEXT_DIST_DIR: '([^']+)'/g)].map((match) => match[1] as string)];
    expect(folders.length).toBeGreaterThan(2);
    for (const folder of folders) expect(tsconfig.include, folder).toContain(`${folder}/types/**/*.ts`);
  });
});
