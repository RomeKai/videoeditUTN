import { describe, expect, it } from 'vitest';
import { ALL_ENDPOINTS, CONTRACT } from './index';

describe('contrato', () => {
  it('no repite ids ni método + ruta', () => {
    const ids = ALL_ENDPOINTS.map((endpoint) => endpoint.id);
    expect(new Set(ids).size).toBe(ids.length);
    const routes = ALL_ENDPOINTS.map((endpoint) => `${endpoint.method} ${endpoint.path}`);
    expect(new Set(routes).size).toBe(routes.length);
  });

  it.each(ALL_ENDPOINTS.map((endpoint) => [endpoint.id, endpoint] as const))('%s está completo', (_id, endpoint) => {
    expect(endpoint.id).toMatch(/^[a-zA-Z]+\.[a-zA-Z]+$/);
    expect(endpoint.path).toMatch(/^\/api\/.*\/$/);
    expect(endpoint.summary.length).toBeGreaterThan(10);
    expect(endpoint.screens.length).toBeGreaterThan(0);
    if (endpoint.backend.status === 'exists' || endpoint.backend.status === 'partial') {
      // What exists cites the DRF view or serializer its field names were copied from.
      expect(endpoint.backend.source).toMatch(/back\//);
    } else {
      // What is proposed cites the gap of the map, or explains why it has none.
      expect(endpoint.backend.gap !== undefined || Boolean(endpoint.backend.note)).toBe(true);
    }
    if (endpoint.backend.status === 'partial') expect(endpoint.backend.note).toBeTruthy();
  });

  it('cubre los dominios que piden las pantallas', () => {
    expect(CONTRACT.map((group) => group.domain)).toEqual([
      'auth',
      'users',
      'workspaces',
      'billing',
      'projects',
      'clips',
      'brandKits',
      'social',
      'notifications',
      'system',
    ]);
  });
});
