import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { configure, render, type RenderResult } from '@testing-library/react';
import type { ReactElement, ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, vi } from 'vitest';
import type { MockDb } from '@/api/mocks/db';
import { USER_IDS, WORKSPACE_IDS } from '@/api/mocks/seed';
import { server } from '@/api/mocks/server';
import { applyScenario, loadDb, mockTokens, saveDb } from '@/api/mocks/state';
import { setActiveWorkspaceId, setTokens } from '@/api/session';
import { BillingDialogsProvider } from '@/dialogs/BillingDialogs';
import { ThemeProvider } from '@/lib/theme';
import { LeaveGuardProvider } from '@/shell/leaveGuard';
import { SessionProvider } from '@/shell/session';
import { ToastProvider } from '@/ui/Toast';

/**
 * What the screen tests of every feature share: the app's providers around a
 * screen, the mocked API, and the mock database behind it. Only for tests
 * (nothing of the app imports it).
 */

/** Starts the mocked API (same handlers as the browser) for a test file. */
export function startMockApi(): void {
  beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
  afterEach(() => server.resetHandlers());
  afterAll(() => server.close());
}

/**
 * Screen tests click through whole flows against MSW. On a busy machine (CI, many
 * suites at once) the defaults of 5 s per test and 1 s per query are too tight.
 */
export function allowSlowScreens(): void {
  vi.setConfig({ testTimeout: 30_000 });
  configure({ asyncUtilTimeout: 5_000 });
}

/** Seeds a mock scenario, which signs in the user it starts with (Lucía, or the role of the scenario). */
export function startScenario(scenario: string): void {
  applyScenario(scenario);
}

/** Seeds a mock scenario and signs a given user in, like the dev panel does. */
export function signIn(scenario: string, user: keyof typeof USER_IDS = 'lucia'): void {
  applyScenario(scenario);
  setTokens(mockTokens(USER_IDS[user]));
  setActiveWorkspaceId(WORKSPACE_IDS.estudio);
}

/** The mock database as it is now. */
export const db = (): MockDb => loadDb();

/** Changes the mocked database directly (another person approved, the render started…). */
export function editDb(change: (db: MockDb) => void): void {
  const current = loadDb();
  change(current);
  saveDb(current);
}

/**
 * Lets the requests of the test that just ended finish. One still in flight
 * would land in the next test's database (the mocked API answers in a few
 * milliseconds under test). Call it from `afterEach`, after `cleanup()`.
 */
export function idle(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 60));
}

/** A cache that outlives a screen, like the app's: going from one screen to another keeps what was fetched. */
export function appQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 15_000 }, mutations: { retry: false } } });
}

/** What the root layout gives every screen: theme, query cache, toasts, session, the leave guard and the billing dialogs. */
export function AppProviders({ client, children }: { client: QueryClient; children: ReactNode }) {
  return (
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <ToastProvider>
          <SessionProvider>
            <LeaveGuardProvider>
              <BillingDialogsProvider>{children}</BillingDialogsProvider>
            </LeaveGuardProvider>
          </SessionProvider>
        </ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

/** Renders a screen inside the app's providers, against the mocked API. `client`: a cache to share between renders. */
export function renderScreen(ui: ReactElement, { client = appQueryClient() }: { client?: QueryClient } = {}): RenderResult & { queryClient: QueryClient } {
  const view = render(<AppProviders client={client}>{ui}</AppProviders>);
  return Object.assign(view, { queryClient: client });
}
