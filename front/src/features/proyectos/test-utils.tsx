import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
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
