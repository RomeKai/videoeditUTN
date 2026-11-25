import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { USER_IDS, WORKSPACE_IDS } from '@/api/mocks/seed';
import { applyScenario, loadDb, mockTokens } from '@/api/mocks/state';
import { setActiveWorkspaceId, setTokens } from '@/api/session';
import { BillingDialogsProvider } from '@/dialogs';
import { SessionProvider } from '@/shell/session';
import { ToastProvider } from '@/ui';

/** Seeds a mock scenario and signs a user in, as the dev panel does. */
export function signIn(scenario: string, user: keyof typeof USER_IDS = 'lucia'): void {
  applyScenario(scenario);
  setTokens(mockTokens(USER_IDS[user]));
  setActiveWorkspaceId(WORKSPACE_IDS.estudio);
}

/** Id of a seeded project, by the start of its title. */
export function projectId(title: string): string {
  const project = loadDb().projects.find((item) => item.title.startsWith(title));
  if (!project) throw new Error(`No hay proyecto «${title}» en el escenario`);
  return project.id;
}

/** Renders a screen with what the app gives every screen: query cache, toasts, session and billing dialogs. */
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
