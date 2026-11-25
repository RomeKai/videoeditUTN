import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { setActiveWorkspaceId, setTokens } from '@/api/session';
import type { MockDb } from '@/api/mocks/db';
import { USER_IDS, WORKSPACE_IDS } from '@/api/mocks/seed';
import { applyScenario, loadDb, mockTokens, saveDb } from '@/api/mocks/state';
import { BillingDialogsProvider } from '@/dialogs/BillingDialogs';
import { LeaveGuardProvider } from '@/shell/leaveGuard';
import { SessionProvider } from '@/shell/session';
import { ToastProvider } from '@/ui/Toast';

/** Seeds a mock scenario and signs a user in, like the dev panel does. */
export function signIn(scenario: string, user: keyof typeof USER_IDS = 'lucia'): void {
  applyScenario(scenario);
  setTokens(mockTokens(USER_IDS[user]));
  setActiveWorkspaceId(WORKSPACE_IDS.estudio);
}

/** Changes the mocked database directly (another person approved, the render started…). */
export function editDb(change: (db: MockDb) => void): void {
  const db = loadDb();
  change(db);
  saveDb(db);
}

/** A cache that outlives a screen, like the app's: going from one screen to another keeps what was fetched. */
export function appQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 15_000 }, mutations: { retry: false } } });
}

/** Renders a screen with what the root layout gives it: query cache, toasts, session and the billing dialogs. */
export function renderScreen(ui: ReactElement, client?: QueryClient) {
  const queryClient = client ?? new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <SessionProvider>
          <LeaveGuardProvider>
            <BillingDialogsProvider>{ui}</BillingDialogsProvider>
          </LeaveGuardProvider>
        </SessionProvider>
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}
