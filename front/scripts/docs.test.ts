import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { contractStats, renderContract, renderRoutes } from './docs';

// Vitest runs from front/.
const root = process.cwd();

describe('documentos generados', () => {
  it('CONTRACT.md está al día con src/api/contracts (corré `npm run contract:doc`)', () => {
    expect(readFileSync(join(root, 'src/api/CONTRACT.md'), 'utf8')).toBe(renderContract());
  });

  it('ROUTES.md está al día con src/dev/screens.ts (corré `npm run contract:doc`)', () => {
    expect(readFileSync(join(root, 'ROUTES.md'), 'utf8')).toBe(renderRoutes());
  });

  it('cada endpoint dice si existe o es propuesto, con su fuente o su hueco', () => {
    const stats = contractStats();
    expect(stats.exists + stats.partial + stats.proposed).toBe(stats.total);
    expect(stats.exists).toBeGreaterThan(0);
  });
});
