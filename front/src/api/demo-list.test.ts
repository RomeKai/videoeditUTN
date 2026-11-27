import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ALL_ENDPOINTS } from './contracts';
import { domainOf, parseRealList } from './routing';
import { unknownRealNames } from './routingTable';

/**
 * The demo's default `DEMO_API_REAL` (docker-compose.demo.yml) names some domains whole and others
 * endpoint by endpoint. A domain named endpoint by endpoint has to list every endpoint the backend
 * already has: one left out stays mocked, and the screen mixes a mock answer with real data (the
 * third rehearsal found «Seguir con» in Planes answered by the mock, `billing.cancelPending`).
 */
// Vitest runs from front/ (the jsdom environment gives import.meta.url an http scheme).
const compose = readFileSync(resolve(process.cwd(), '../docker-compose.demo.yml'), 'utf8');
const raw = /NEXT_PUBLIC_API_REAL:\s*\$\{DEMO_API_REAL:-([^}]*)\}/.exec(compose)?.[1];

/** Endpoints of a domain the demo names one by one that stay mocked on purpose, with the reason. */
const KEPT_MOCKED: Record<string, string> = {};

describe('DEMO_API_REAL de la demo', () => {
  it('se lee del compose y no nombra nada que no esté en el contrato', () => {
    expect(raw).toBeTruthy();
    expect(unknownRealNames(raw ?? '')).toEqual([]);
  });

  it('en un dominio nombrado endpoint por endpoint, están todos los que el backend ya tiene', () => {
    const list = parseRealList(raw);
    const named = new Set([...list.endpoints].map((id) => id.split('.')[0]));
    const missing = ALL_ENDPOINTS.filter((endpoint) => {
      const domain = domainOf(endpoint).toLowerCase();
      if (list.domains.has(domain) || !named.has(domain)) return false;
      if (endpoint.backend.status === 'proposed') return false;
      return !list.endpoints.has(endpoint.id.toLowerCase()) && !(endpoint.id in KEPT_MOCKED);
    }).map((endpoint) => endpoint.id);
    expect(missing).toEqual([]);
  });
});
