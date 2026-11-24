import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { setActiveWorkspaceId, setTokens } from '@/api/session';
import type { MockDb } from '@/api/mocks/db';
import { USER_IDS, WORKSPACE_IDS } from '@/api/mocks/seed';
import { applyScenario, loadDb, mockTokens, saveDb } from '@/api/mocks/state';
import { BillingDialogsProvider } from '@/dialogs/BillingDialogs';
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

/** Renders a screen with what the root layout gives it: query cache, toasts, session and the billing dialogs. */
export function renderScreen(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <SessionProvider>
          <BillingDialogsProvider>{ui}</BillingDialogsProvider>
        </SessionProvider>
      </ToastProvider>
    </QueryClientProvider>,
  );
}
