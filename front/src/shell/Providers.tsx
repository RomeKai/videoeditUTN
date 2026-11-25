'use client';

import { QueryClientProvider } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import { useEffect, useState, type ReactNode } from 'react';
import { API_MOCKS } from '@/api/config';
import { createQueryClient } from '@/api/query';
import { BillingDialogsProvider } from '@/dialogs/BillingDialogs';
import { SessionExpiredDialog } from '@/dialogs/SessionExpiredDialog';
import { ThemeProvider } from '@/lib/theme';
import { ToastProvider } from '@/ui/Toast';
import { GlobalLoading } from './GlobalLoading';
import { LeaveGuardProvider } from './leaveGuard';
import { MaintenanceGate } from './MaintenanceGate';
import { OfflineBanner } from './OfflineBanner';
import { SessionProvider } from './session';

// Only loaded in mock mode, and only in the browser.
const MockPanel = dynamic(() => import('@/dev/MockPanel').then((module) => module.MockPanel), { ssr: false });

/** In mock mode nothing may fetch before the service worker is ready. */
function MockGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(!API_MOCKS);
  useEffect(() => {
    if (!API_MOCKS) {
      // A browser that once ran this origin with mocks keeps the MSW worker registered: it goes away here.
      void navigator.serviceWorker
        ?.getRegistrations()
        .then((registrations) => {
          for (const registration of registrations) {
            const script = (registration.active ?? registration.waiting ?? registration.installing)?.scriptURL ?? '';
            if (script.split('?')[0]?.endsWith('/mockServiceWorker.js')) void registration.unregister();
          }
        })
        .catch(() => undefined);
      return;
    }
    let cancelled = false;
    void import('@/api/mocks/browser')
      .then((module) => module.startMocks())
      .then(() => {
        if (!cancelled) setReady(true);
      })
      .catch((error: unknown) => {
        // Without the worker nothing may fetch: the app stays on its outline and says why.
        console.error('No se pudo iniciar la API simulada (MSW). Recargá la página.', error);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  if (!ready) return <GlobalLoading />;
  return (
    <>
      {children}
      {API_MOCKS ? <MockPanel /> : null}
    </>
  );
}

/** Everything the screens can count on: query cache, theme, toasts, session, the leave guard, shared dialogs and system banners. */
export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(createQueryClient);
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <MockGate>
            <SessionProvider>
              <LeaveGuardProvider>
                <BillingDialogsProvider>
                  <OfflineBanner />
                  <MaintenanceGate>{children}</MaintenanceGate>
                  <SessionExpiredDialog />
                </BillingDialogsProvider>
              </LeaveGuardProvider>
            </SessionProvider>
          </MockGate>
        </ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
