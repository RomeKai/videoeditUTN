import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { server } from '@/api/mocks/server';
import { applyScenario } from '@/api/mocks/state';
import { BillingDialogsProvider } from '@/dialogs/BillingDialogs';
import { ThemeProvider } from '@/lib/theme';
import { SessionProvider } from '@/shell/session';
import { ToastProvider } from '@/ui/Toast';

/** Starts the mocked API (same handlers as the browser) for a test file. */
export function startMockApi(): void {
  beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
  afterEach(() => server.resetHandlers());
  afterAll(() => server.close());
}

/**
 * Renders a screen the way the app does: seeded mock scenario (which also signs
 * in the scenario's user), query cache, session, toasts and billing dialogs.
 */
export function renderScreen(
  ui: ReactElement,
  { scenario = 'estudio-norte', prepare }: { scenario?: string; /** Changes the seeded database before the screen asks for anything. */ prepare?: () => void } = {},
): RenderResult {
  applyScenario(scenario);
  prepare?.();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 15_000 }, mutations: { retry: false } } });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <SessionProvider>
            <BillingDialogsProvider>{ui}</BillingDialogsProvider>
          </SessionProvider>
        </ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}
