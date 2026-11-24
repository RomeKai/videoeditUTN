import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { contractStats, renderContract, renderRoutes } from './docs';

const root = fileURLToPath(new URL('..', import.meta.url));
writeFileSync(`${root}src/api/CONTRACT.md`, renderContract());
writeFileSync(`${root}ROUTES.md`, renderRoutes());
const stats = contractStats();
console.log(`CONTRACT.md: ${stats.total} endpoints (${stats.exists} existen, ${stats.partial} en parte, ${stats.proposed} propuestos). ROUTES.md actualizado.`);
