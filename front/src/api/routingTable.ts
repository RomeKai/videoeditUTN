import { CONTRACT } from './contracts';
import { ALWAYS_REAL_DOMAIN, envRouteOf, parseRealList, routeOf, routingConfig, type Route, type RoutingConfig } from './routing';

/** How one domain of the contract is routed: what the dev panel shows. */
export interface DomainRouting {
  domain: string;
  title: string;
  total: number;
  /** How many of its endpoints go to the real backend. */
  real: number;
  /** `mixed`: some endpoints are real and some mocked (the default by contract status). */
  route: Route | 'mixed';
  /** What the env says, before the dev override. */
  envRoute: Route | 'mixed';
  /** The dev override in force, if any. */
  override: Route | null;
  /** Set when the domain cannot be switched, with the reason (Spanish, shown in the panel). */
  locked: string | null;
}

const summarize = (real: number, total: number): Route | 'mixed' => (real === 0 ? 'mock' : real === total ? 'real' : 'mixed');

export const AUTH_LOCKED_REASON = 'Siempre real en modo híbrido: el backend no acepta los tokens simulados.';

/** Names of `NEXT_PUBLIC_API_REAL` that are neither a domain nor an endpoint id of the contract (a typo, most likely). */
export function unknownRealNames(raw: string): string[] {
  const list = parseRealList(raw);
  const domains = new Set(CONTRACT.map((group) => group.domain.toLowerCase()));
  const endpoints = new Set(CONTRACT.flatMap((group) => group.endpoints.map((endpoint) => endpoint.id.toLowerCase())));
  return [...[...list.domains].filter((name) => !domains.has(name)), ...[...list.endpoints].filter((name) => !endpoints.has(name))];
}

/** One row per domain of the contract registry, in its order. */
export function domainRouting(config: RoutingConfig = routingConfig()): DomainRouting[] {
  return CONTRACT.map(({ domain, title, endpoints }) => {
    const real = endpoints.filter((endpoint) => routeOf(endpoint, config) === 'real').length;
    const envReal = endpoints.filter((endpoint) => envRouteOf(endpoint, config.mode, config.real) === 'real').length;
    const locked = config.mode === 'hybrid' && domain === ALWAYS_REAL_DOMAIN;
    return {
      domain,
      title,
      total: endpoints.length,
      real,
      route: summarize(real, endpoints.length),
      envRoute: summarize(envReal, endpoints.length),
      override: locked ? null : (config.overrides[domain] ?? null),
      locked: locked ? AUTH_LOCKED_REASON : null,
    };
  });
}
