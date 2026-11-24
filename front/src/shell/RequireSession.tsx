'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useEffect, type ReactNode } from 'react';
import type { Action } from '@/lib/permissions';
import { GlobalLoading } from './GlobalLoading';
import { needsWelcome, usePermissions, useSession } from './session';
import { Button } from '@/ui/Button';
import { NoAccessMessage, SystemScreen } from './SystemPage';

/**
 * Route protection for the app (everything under the `(app)` group):
 * - without a session → /login?siguiente=<where the user was going>
 * - while `/me` loads → the app's outline
 * - a brand-new account landing on Proyectos → /bienvenida (map B1)
 */
export function RequireSession({ children }: { children: ReactNode }) {
  const { status, me, refetch } = useSession();
  const router = useRouter();
  const pathname = usePathname() ?? '/proyectos';

  useEffect(() => {
    if (status === 'anonymous') {
      const next = pathname && pathname !== '/proyectos' ? `?siguiente=${encodeURIComponent(pathname)}` : '';
      router.replace(`/login${next}`);
    }
  }, [pathname, router, status]);

  useEffect(() => {
    if (status === 'authenticated' && me && pathname === '/proyectos' && needsWelcome(me)) router.replace('/bienvenida');
  }, [me, pathname, router, status]);

  if (status === 'error') {
    return (
      <SystemScreen
        title="Algo falló de nuestro lado"
        code="500"
        description="No pudimos cargar tu cuenta. Probá de nuevo en un momento."
        actions={
          <Button variant="primary" iconLeft="retry" onClick={refetch}>
            Reintentar
          </Button>
        }
      />
    );
  }
  if (status !== 'authenticated' || !me) return <GlobalLoading />;
  if (me.workspaces.length === 0) {
    return <SystemScreen title="No tenés ningún workspace" description="Pedile a alguien que te invite al suyo, o escribinos." />;
  }
  return <>{children}</>;
}

/** For public screens that make no sense with a session (login, register): sends the user to the app. */
export function RedirectIfAuthenticated({ children, to = '/proyectos' }: { children: ReactNode; to?: string }) {
  const { status } = useSession();
  const router = useRouter();
  useEffect(() => {
    if (status === 'authenticated') router.replace(to);
  }, [router, status, to]);
  return <>{children}</>;
}

/**
 * Sections that only some roles can open (Ajustes › Miembros and Redes are for
 * Dueño and Admin): the rest get "No tenés acceso a esto" (K2) instead of the page.
 * For single actions use `usePermissions().gate()` on the button, which keeps it visible.
 */
export function RequireRole({ action, children }: { action: Action; children: ReactNode }) {
  const { can } = usePermissions();
  if (!can(action)) return <NoAccessMessage inline />;
  return <>{children}</>;
}
