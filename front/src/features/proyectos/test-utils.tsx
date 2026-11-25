import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { configure, render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';
import { applyScenario } from '@/api/mocks/state';
import { BillingDialogsProvider } from '@/dialogs/BillingDialogs';
import { SessionProvider } from '@/shell/session';
import { ToastProvider } from '@/ui/Toast';

/** Seeds a mock scenario and signs in as its user, the way the dev panel does. */
export function startScenario(scenario: string): void {
  applyScenario(scenario);
}

/** Renders a screen with what the root layout provides: query cache, toasts, session and billing dialogs. */
export function renderScreen(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 15_000 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <SessionProvider>
          <BillingDialogsProvider>{ui}</BillingDialogsProvider>
        </SessionProvider>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

/**
 * Screen tests click through whole flows against MSW. On a busy machine (CI, many
 * suites at once) the defaults of 5 s per test and 1 s per query are too tight.
 */
export function allowSlowScreens(): void {
  vi.setConfig({ testTimeout: 30_000 });
  configure({ asyncUtilTimeout: 5_000 });
}
