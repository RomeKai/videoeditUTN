import { useMemo, useSyncExternalStore } from 'react';

/**
 * For the tests of B2: a URL that changes. The screen keeps its filter, order, page and search in the
 * query string, and the `next/navigation` of vitest.setup.ts never moves. Use it as the mock of the file:
 *
 *   vi.mock('next/navigation', async () => (await import('./testing-nav')).navigationMock());
 *
 * and `nav.set('')` after each test.
 */
let search = '';
const listeners = new Set<() => void>();

export const nav = {
  /** The query string, without the `?`. */
  get: (): string => search,
  set: (value: string): void => {
    search = value;
    for (const listener of listeners) listener();
  },
  subscribe: (listener: () => void): (() => void) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export function navigationMock(pathname = '/proyectos') {
  const go = (url: string) => nav.set(url.split('?')[1] ?? '');
  const router = { push: go, replace: go, back: () => undefined, refresh: () => undefined, prefetch: () => undefined };
  return {
    useRouter: () => router,
    usePathname: () => pathname,
    useSearchParams: () => {
      const current = useSyncExternalStore(nav.subscribe, nav.get, nav.get);
      return useMemo(() => new URLSearchParams(current), [current]);
    },
    useParams: () => ({}),
    redirect: () => undefined,
    notFound: () => undefined,
  };
}
