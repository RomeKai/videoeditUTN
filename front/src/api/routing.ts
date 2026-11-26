import { API_MODE, API_REAL_LIST, type ApiMode } from './config';
import type { AnyEndpoint } from './endpoint';

/**
 * Hybrid mode: who answers each endpoint of the contract, the real backend or the MSW mocks.
 *
 * The decision is made from the contract alone (the endpoint's id and its `backend.status`), in this order:
 * 1. mode `mock` → mock, mode `real` → real;
 * 2. `auth` is always real: the backend rejects the mock tokens, so the session has to be a real one;
 * 3. the dev-only override of the endpoint's domain (the dev panel, kept in localStorage);
 * 4. `NEXT_PUBLIC_API_REAL`: the domains and endpoint ids it lists are real, the rest is mocked;
 * 5. without a list (or for its word `existing`): what the contract marks `exists` or `partial` is real.
 * So marking an endpoint `exists` in its contract is all it takes to send it to the backend.
 */
export type Route = 'real' | 'mock';

export type RouteOverrides = Readonly<Record<string, Route>>;

export interface RoutingConfig {
  mode: ApiMode;
  /** Raw value of `NEXT_PUBLIC_API_REAL`. */
  real: string;
  overrides: RouteOverrides;
}

/** The domain every hybrid setup sends to the backend. */
export const ALWAYS_REAL_DOMAIN = 'auth';

/** Word of the list that stands for "what the contract says exists". */
export const EXISTING = 'existing';

export const ROUTE_OVERRIDES_KEY = 'oc.api.routes';

/** Domain of an endpoint: the first half of its id (`billing.plans` → `billing`). Checked against the registry in routing.test.ts. */
export function domainOf(endpoint: Pick<AnyEndpoint, 'id'>): string {
  return endpoint.id.split('.')[0] ?? '';
}

export interface RealList {
  /** Lower-cased domains (`brandkits`). */
  domains: Set<string>;
  /** Lower-cased endpoint ids (`billing.plans`). */
  endpoints: Set<string>;
  /** The list asks for what the contract marks as existing (also true when the list is empty). */
  existing: boolean;
}

/** Reads `NEXT_PUBLIC_API_REAL`. Case and spaces do not matter (`brandKits` = `brandkits`). */
export function parseRealList(raw: string | undefined): RealList {
  const items = (raw ?? '')
    .split(',')
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
  const list: RealList = { domains: new Set(), endpoints: new Set(), existing: items.length === 0 };
  for (const item of items) {
    if (item === EXISTING) list.existing = true;
    else if (item.includes('.')) list.endpoints.add(item);
    else list.domains.add(item);
  }
  return list;
}

/** Whether the backend has the endpoint today, according to its contract. */
export function existsInBackend(endpoint: Pick<AnyEndpoint, 'backend'>): boolean {
  return endpoint.backend.status === 'exists' || endpoint.backend.status === 'partial';
}

/** What the env alone says (no dev override): steps 1, 2, 4 and 5. */
export function envRouteOf(endpoint: Pick<AnyEndpoint, 'id' | 'backend'>, mode: ApiMode, real: string): Route {
  if (mode !== 'hybrid') return mode === 'mock' ? 'mock' : 'real';
  const domain = domainOf(endpoint);
  if (domain === ALWAYS_REAL_DOMAIN) return 'real';
  const list = parseRealList(real);
  if (list.domains.has(domain.toLowerCase()) || list.endpoints.has(endpoint.id.toLowerCase())) return 'real';
  return list.existing && existsInBackend(endpoint) ? 'real' : 'mock';
}

let memoryOverrides: Record<string, Route> = {};

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** Dev-only overrides by domain. Anything unreadable counts as none. */
export function readRouteOverrides(): RouteOverrides {
  const store = storage();
  if (!store) return memoryOverrides;
  try {
    const parsed: unknown = JSON.parse(store.getItem(ROUTE_OVERRIDES_KEY) ?? '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const overrides: Record<string, Route> = {};
    for (const [domain, route] of Object.entries(parsed)) {
      if (route === 'real' || route === 'mock') overrides[domain] = route;
    }
    return overrides;
  } catch {
    return {};
  }
}

/** Sets the override of one domain, or removes it (`null`: back to what the env says). */
export function setRouteOverride(domain: string, route: Route | null): void {
  const next: Record<string, Route> = { ...readRouteOverrides() };
  if (route) next[domain] = route;
  else delete next[domain];
  writeRouteOverrides(next);
}

export function clearRouteOverrides(): void {
  writeRouteOverrides({});
}

function writeRouteOverrides(overrides: Record<string, Route>): void {
  memoryOverrides = overrides;
  const store = storage();
  if (!store) return;
  try {
    if (Object.keys(overrides).length === 0) store.removeItem(ROUTE_OVERRIDES_KEY);
    else store.setItem(ROUTE_OVERRIDES_KEY, JSON.stringify(overrides));
  } catch {
    // Private mode or quota: the override lasts for this page only.
  }
}

let forced: Partial<RoutingConfig> | null = null;

/** Tests only: fixes the mode, the list or the overrides instead of reading the env and localStorage. `null` undoes it. */
export function forceRouting(config: Partial<RoutingConfig> | null): void {
  forced = config;
}

export function routingConfig(): RoutingConfig {
  const mode = forced?.mode ?? API_MODE;
  return {
    mode,
    real: forced?.real ?? API_REAL_LIST,
    // Overrides only exist in hybrid: they must never turn a mock or a real build into something else.
    overrides: forced?.overrides ?? (mode === 'hybrid' ? readRouteOverrides() : {}),
  };
}

export function isHybrid(): boolean {
  return (forced?.mode ?? API_MODE) === 'hybrid';
}

/** Who answers the endpoint. */
export function routeOf(endpoint: Pick<AnyEndpoint, 'id' | 'backend'>, config: RoutingConfig = routingConfig()): Route {
  if (config.mode !== 'hybrid') return config.mode === 'mock' ? 'mock' : 'real';
  const domain = domainOf(endpoint);
  if (domain === ALWAYS_REAL_DOMAIN) return 'real';
  return config.overrides[domain] ?? envRouteOf(endpoint, config.mode, config.real);
}

/** The endpoint a request is for, by method and path (`/api/v1/projects/abc/` → `projects.detail`). */
export function matchEndpoint<E extends Pick<AnyEndpoint, 'method' | 'path'>>(endpoints: ReadonlyArray<E>, method: string, pathname: string): E | null {
  const verb = method.toUpperCase();
  let best: E | null = null;
  for (const endpoint of endpoints) {
    if (endpoint.method !== verb) continue;
    const pattern = new RegExp(`^${endpoint.path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/:[A-Za-z_]+/g, '[^/]+')}$`);
    if (!pattern.test(pathname)) continue;
    // A literal path wins over one with parameters (`/projects/probe-link/` over `/projects/:id/`).
    if (!best || endpoint.path.split(':').length < best.path.split(':').length) best = endpoint;
  }
  return best;
}
