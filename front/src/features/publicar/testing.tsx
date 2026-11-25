import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { loadDb, applyScenario } from '@/api/mocks/state';
import type { MockDb } from '@/api/mocks/db';
import { BillingDialogsProvider } from '@/dialogs/BillingDialogs';
import { SessionProvider } from '@/shell/session';
import { ToastProvider } from '@/ui/Toast';

/**
 * Test helper: renders a screen the way the app does (query cache, toasts,
 * session and billing dialogs) against the mocked API of a scenario. The test
 * file starts the MSW server (`server` from '@/api/mocks/server').
 */
export function renderScreen(ui: ReactElement, { scenario = 'estudio-norte' }: { scenario?: string } = {}): RenderResult & { client: QueryClient } {
  // Seeds the scenario and signs in the user it starts with (Lucía, or the role of the scenario).
  applyScenario(scenario);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 15_000 }, mutations: { retry: false } } });
  const result = render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <SessionProvider>
          <BillingDialogsProvider>{ui}</BillingDialogsProvider>
        </SessionProvider>
      </ToastProvider>
    </QueryClientProvider>,
  );
  return Object.assign(result, { client });
}

/**
 * Lets the requests of the test that just ended finish. One still in flight
 * would land in the next test's database (the mocked API answers in a few
 * milliseconds under test). Call it from `afterEach`, after `cleanup()`.
 */
export function idle(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 60));
}

/** The mock database as it is now. */
export const db = (): MockDb => loadDb();

/** Ids of the default scenario: the clips of «Vlog Mendoza» (project 5), all rendered, with SEO. */
export const CLIPS = {
  first: '00000008-0000-4000-8000-0000000001f5',
  second: '00000008-0000-4000-8000-0000000001f6',
  third: '00000008-0000-4000-8000-0000000001f7',
} as const;
