import { afterEach, describe, expect, it, vi } from 'vitest';
import { resolveApiMode } from './config';
import { ALL_ENDPOINTS, CONTRACT } from './contracts';
import * as auth from './contracts/auth';
import * as billing from './contracts/billing';
import * as brandKits from './contracts/brandkits';
import * as projects from './contracts/projects';
import * as users from './contracts/users';
import {
  clearRouteOverrides,
  domainOf,
  envRouteOf,
  forceRouting,
  matchEndpoint,
  parseRealList,
  readRouteOverrides,
  ROUTE_OVERRIDES_KEY,
  routeOf,
  routingConfig,
  setRouteOverride,
  type RoutingConfig,
} from './routing';
import { AUTH_LOCKED_REASON, domainRouting, unknownRealNames } from './routingTable';

const hybrid = (real = '', overrides: RoutingConfig['overrides'] = {}): RoutingConfig => ({ mode: 'hybrid', real, overrides });

afterEach(() => {
  forceRouting(null);
  clearRouteOverrides();
  vi.restoreAllMocks();
});

describe('modo de la API', () => {
  it('NEXT_PUBLIC_API_MODE manda; sin él, NEXT_PUBLIC_API_MOCKS sigue funcionando', () => {
    expect(resolveApiMode('hybrid', 'true')).toBe('hybrid');
    expect(resolveApiMode(' Real ', 'true')).toBe('real');
    expect(resolveApiMode('mock', 'false')).toBe('mock');
    expect(resolveApiMode(undefined, 'true')).toBe('mock');
    expect(resolveApiMode(undefined, 'false')).toBe('real');
    expect(resolveApiMode(undefined, undefined)).toBe('real');
    // A value it does not know is not a mode: the old variable decides.
    expect(resolveApiMode('mixto', 'true')).toBe('mock');
  });
});

describe('ruteo híbrido', () => {
  it('el dominio de un endpoint es la primera mitad de su id, y coincide con el registro del contrato', () => {
    for (const group of CONTRACT) for (const endpoint of group.endpoints) expect(domainOf(endpoint)).toBe(group.domain);
  });

  it('en mock todo es mock y en real todo es real, diga lo que diga la lista', () => {
    for (const endpoint of ALL_ENDPOINTS) {
      expect(routeOf(endpoint, { mode: 'mock', real: 'projects', overrides: { projects: 'real' } })).toBe('mock');
      expect(routeOf(endpoint, { mode: 'real', real: '', overrides: { projects: 'mock' } })).toBe('real');
    }
  });

  it('sin lista, va al backend lo que el contrato marca exists o partial; lo propuesto queda simulado', () => {
    for (const endpoint of ALL_ENDPOINTS) {
      if (domainOf(endpoint) === 'auth') continue;
      expect(routeOf(endpoint, hybrid()), endpoint.id).toBe(endpoint.backend.status === 'proposed' ? 'mock' : 'real');
    }
    expect(routeOf(projects.listProjects, hybrid())).toBe('real'); // partial
    expect(routeOf(brandKits.listBrandKits, hybrid())).toBe('real'); // exists
    // An endpoint still proposed, taken from the registry: a fixed one turns real as soon as the
    // backend builds it (projects.getProjectActivity did).
    const stillProposed = ALL_ENDPOINTS.find((e) => e.backend.status === 'proposed' && domainOf(e) !== 'auth');
    expect(stillProposed, 'algún endpoint sigue propuesto').toBeDefined();
    expect(routeOf(stillProposed!, hybrid())).toBe('mock'); // proposed
  });

  it('marcar un endpoint como exists en el contrato alcanza para mandarlo al backend', () => {
    // Whatever the contract says about it today: only the status decides.
    const proposed = { ...billing.getWallet, backend: { status: 'proposed' as const, gap: 6 } };
    expect(routeOf(proposed, hybrid())).toBe('mock');
    expect(routeOf({ ...proposed, backend: { status: 'exists', source: 'back/apps/payments/views.py' } }, hybrid())).toBe('real');
  });

  it('auth siempre es real en híbrido: ni la lista ni un cambio a mano lo simulan', () => {
    for (const endpoint of auth.authEndpoints) {
      expect(routeOf(endpoint, hybrid('billing', { auth: 'mock' })), endpoint.id).toBe('real');
    }
  });

  it('con lista, los dominios nombrados van enteros al backend y el resto queda simulado', () => {
    const config = hybrid('projects, BrandKits');
    expect(routeOf(projects.listProjects, config)).toBe('real');
    expect(routeOf(projects.getProjectActivity, config)).toBe('real'); // proposed, but its domain was asked for
    expect(routeOf(brandKits.duplicateBrandKit, config)).toBe('real');
    expect(routeOf(billing.listPlans, config)).toBe('mock');
    expect(routeOf(users.getMe, config)).toBe('mock');
  });

  it('la lista acepta ids de endpoint sueltos y la palabra existing', () => {
    const config = hybrid('billing.plans');
    expect(routeOf(billing.listPlans, config)).toBe('real');
    expect(routeOf(billing.getWallet, config)).toBe('mock');
    // Only what was listed: what exists but was not named stays mocked.
    expect(routeOf(projects.listProjects, config)).toBe('mock');
    const plus = hybrid('existing,users.me');
    expect(routeOf(projects.listProjects, plus)).toBe('real');
    expect(routeOf(users.getMe, plus)).toBe('real');
    // A proposed endpoint that was not named (users.updateMe exists since feat/perfil-me, billing.wallet
    // since feat/billetera-movimientos-precios).
    expect(routeOf(billing.getSubscription, plus)).toBe('mock');
    expect(routeOf(billing.getWallet, plus)).toBe('real');
    expect(parseRealList(' Projects ,billing.Plans,, existing ')).toEqual({ domains: new Set(['projects']), endpoints: new Set(['billing.plans']), existing: true });
    expect(parseRealList(undefined).existing).toBe(true);
    expect(parseRealList('projects').existing).toBe(false);
  });

  it('el cambio a mano de un dominio gana sobre el env, en los dos sentidos', () => {
    expect(routeOf(projects.listProjects, hybrid('', { projects: 'mock' }))).toBe('mock');
    expect(routeOf(billing.getWallet, hybrid('projects', { billing: 'real' }))).toBe('real');
    expect(envRouteOf(billing.getWallet, 'hybrid', 'projects')).toBe('mock');
  });

  it('encuentra el endpoint de un pedido por método y ruta', () => {
    expect(matchEndpoint(ALL_ENDPOINTS, 'get', '/api/v1/projects/')?.id).toBe('projects.list');
    expect(matchEndpoint(ALL_ENDPOINTS, 'POST', '/api/v1/projects/')?.id).toBe('projects.create');
    expect(matchEndpoint(ALL_ENDPOINTS, 'GET', '/api/v1/projects/1b05848d-9fbc-4021-8452-6710f1ac902d/')?.id).toBe('projects.detail');
    expect(matchEndpoint(ALL_ENDPOINTS, 'POST', '/api/v1/projects/probe-link/')?.id).toBe('projects.probeLink');
    expect(matchEndpoint(ALL_ENDPOINTS, 'POST', '/api/token/')?.id).toBe('auth.login');
    expect(matchEndpoint(ALL_ENDPOINTS, 'GET', '/api/v1/no-existe/')).toBeNull();
  });
});

describe('nombres de NEXT_PUBLIC_API_REAL', () => {
  it('avisa de lo que no es un dominio ni un endpoint del contrato', () => {
    expect(unknownRealNames('')).toEqual([]);
    expect(unknownRealNames('projects, BrandKits, existing, billing.plans, auth')).toEqual([]);
    expect(unknownRealNames('proyects,brandkits,billing.planes,users.me')).toEqual(['proyects', 'billing.planes']);
  });
});

describe('cambios a mano (localStorage)', () => {
  it('se guardan por dominio, se leen en híbrido y se pueden quitar', () => {
    setRouteOverride('billing', 'real');
    setRouteOverride('projects', 'mock');
    expect(JSON.parse(window.localStorage.getItem(ROUTE_OVERRIDES_KEY) ?? '{}')).toEqual({ billing: 'real', projects: 'mock' });
    forceRouting({ mode: 'hybrid', real: 'projects' });
    expect(routeOf(billing.getWallet)).toBe('real');
    expect(routeOf(projects.listProjects)).toBe('mock');
    setRouteOverride('billing', null);
    expect(routeOf(billing.getWallet)).toBe('mock');
    clearRouteOverrides();
    expect(window.localStorage.getItem(ROUTE_OVERRIDES_KEY)).toBeNull();
    expect(routeOf(projects.listProjects)).toBe('real');
  });

  it('fuera de híbrido no se leen: nunca convierten un build mock o real en otra cosa', () => {
    setRouteOverride('billing', 'real');
    forceRouting({ mode: 'mock' });
    expect(routingConfig().overrides).toEqual({});
    expect(routeOf(billing.getWallet)).toBe('mock');
  });

  it('un valor roto o un localStorage que falla cuentan como sin cambios', () => {
    window.localStorage.setItem(ROUTE_OVERRIDES_KEY, '{no es json');
    expect(readRouteOverrides()).toEqual({});
    window.localStorage.setItem(ROUTE_OVERRIDES_KEY, JSON.stringify({ billing: 'real', projects: 'cualquiera', social: 3 }));
    expect(readRouteOverrides()).toEqual({ billing: 'real' });
    window.localStorage.setItem(ROUTE_OVERRIDES_KEY, '[1]');
    expect(readRouteOverrides()).toEqual({});
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    expect(readRouteOverrides()).toEqual({});
    expect(() => setRouteOverride('billing', 'real')).not.toThrow();
    forceRouting({ mode: 'hybrid', real: 'projects' });
    expect(routeOf(billing.getWallet)).toBe('mock');
  });
});

describe('tabla por dominio (panel de desarrollo)', () => {
  it('resume cada dominio del contrato: real, mock o mixto, con auth bloqueado', () => {
    const rows = domainRouting(hybrid());
    expect(rows.map((row) => row.domain)).toEqual(CONTRACT.map((group) => group.domain));
    const by = Object.fromEntries(rows.map((row) => [row.domain, row]));
    expect(by.auth).toMatchObject({ route: 'real', locked: AUTH_LOCKED_REASON, override: null });
    // The counts follow the contract: nothing here lists which endpoints exist.
    for (const group of CONTRACT) {
      if (group.domain === 'auth') continue;
      const real = group.endpoints.filter((endpoint) => endpoint.backend.status !== 'proposed').length;
      expect(by[group.domain], group.domain).toMatchObject({ real, total: group.endpoints.length, route: real === 0 ? 'mock' : real === group.endpoints.length ? 'real' : 'mixed', locked: null });
    }
    // A domain that is part built, part proposed reads as mixed, taken from the registry: a fixed
    // domain stops being mixed once the backend builds the rest of it (projects did).
    const mixed = CONTRACT.find((group) => group.domain !== 'auth' && group.endpoints.some((e) => e.backend.status === 'proposed') && group.endpoints.some((e) => e.backend.status !== 'proposed'));
    if (mixed) expect(by[mixed.domain], mixed.domain).toMatchObject({ route: 'mixed', envRoute: 'mixed' });
  });

  it('refleja la lista del env y el cambio a mano', () => {
    const rows = domainRouting(hybrid('projects', { billing: 'real', projects: 'mock' }));
    const by = Object.fromEntries(rows.map((row) => [row.domain, row]));
    expect(by.projects).toMatchObject({ route: 'mock', envRoute: 'real', override: 'mock', real: 0 });
    expect(by.billing).toMatchObject({ route: 'real', envRoute: 'mock', override: 'real' });
    expect(by.brandKits).toMatchObject({ route: 'mock', override: null });
  });
});
