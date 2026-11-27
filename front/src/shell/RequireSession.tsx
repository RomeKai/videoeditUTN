'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import type { Action } from '@/lib/permissions';
import { GlobalLoading } from './GlobalLoading';
import { needsWelcome, usePermissions, useSession } from './session';
import { Button } from '@/ui/Button';
import { useToast } from '@/ui';
import { NoAccessMessage, SystemScreen } from './SystemPage';
import { CreateWorkspaceDialog } from './WorkspaceSwitcher';

/**
 * `?espacio=<workspace id>`: the workspace a link belongs to. The emails of the notifications carry
 * it (back/apps/notifications/emails.py · WORKSPACE_PARAM): whoever opens one may have another
 * workspace active, where the project of the link does not exist and "Comprar tokens" would buy
 * for the wrong one.
 */
export const WORKSPACE_PARAM = 'espacio';

/** What the notice says when the link names a workspace the person is not in (any more). */
export const NOT_IN_WORKSPACE = 'Ese link es de un espacio de trabajo al que no tenés acceso. Seguís en el tuyo.';

/**
 * A path inside the app (`/uso/comprar?espacio=<id>&compra=ok`) split into the workspace it names
 * and the same path without the parameter. `workspaceId` is null when it names none.
 */
export function splitWorkspaceLink(target: string): { workspaceId: string | null; rest: string } {
  const [beforeHash = '', ...hash] = target.split('#');
  const [path = '', ...search] = beforeHash.split('?');
  const query = new URLSearchParams(search.join('?'));
  const workspaceId = query.get(WORKSPACE_PARAM);
  if (workspaceId === null) return { workspaceId: null, rest: target };
  query.delete(WORKSPACE_PARAM);
  const left = query.toString();
  return { workspaceId: workspaceId || null, rest: `${path}${left ? `?${left}` : ''}${hash.length ? `#${hash.join('#')}` : ''}` };
}

/**
 * Route protection for the app (everything under the `(app)` group):
 * - without a session → /login?siguiente=<where the user was going, with its query>
 * - while `/me` loads → the app's outline
 * - a link of another workspace of the user (`?espacio=`) → that workspace becomes the active one
 *   before the page is shown, and the parameter leaves the URL
 * - a brand-new account landing on Proyectos → /bienvenida (map B1)
 * - an account with no workspace → the empty state, where it creates one
 */
export function RequireSession({ children }: { children: ReactNode }) {
  // `useSearchParams` needs a boundary to keep the pages prerendered. Nothing is lost: before the
  // browser knows the session, this only ever renders the app's outline.
  return (
    <Suspense fallback={<GlobalLoading />}>
      <Gate>{children}</Gate>
    </Suspense>
  );
}

function Gate({ children }: { children: ReactNode }) {
  const { status, me, workspace, switchWorkspace, refetch } = useSession();
  const router = useRouter();
  const toast = useToast();
  const pathname = usePathname() ?? '/proyectos';
  // From the router, not from `window.location`: while a client navigation renders (the login
  // sending the person to the link they asked for) the browser's URL is still the previous page's.
  const search = useSearchParams()?.toString() ?? '';

  useEffect(() => {
    if (status === 'anonymous') {
      const target = `${pathname}${search ? `?${search}` : ''}`;
      const next = target && target !== '/proyectos' ? `?siguiente=${encodeURIComponent(target)}` : '';
      router.replace(`/login${next}`);
    }
  }, [pathname, router, search, status]);

  const here = `${pathname}${search ? `?${search}` : ''}`;
  const link = status === 'authenticated' && me ? splitWorkspaceLink(here) : null;
  const linked = link?.workspaceId ?? null;
  // The workspace of the link, when it is one of the user's.
  const wanted = linked && me?.workspaces.some((item) => item.id === linked) ? linked : null;
  const rest = link?.rest ?? here;
  // The link already took the person there: switching by hand afterwards is not undone.
  const [arrived, setArrived] = useState<string | null>(null);
  const switching = useRef<string | null>(null);
  const moving = wanted !== null && arrived !== wanted && workspace?.id !== wanted;

  useEffect(() => {
    if (linked === null || arrived === linked) return;
    const clean = () => router.replace(`${rest}${window.location.hash}`);
    if (wanted === null) {
      // Not one of theirs (removed since the email, or someone else's link): say so instead of
      // showing the page of another workspace as if it were the link's.
      setArrived(linked);
      toast.error(NOT_IN_WORKSPACE);
      clean();
      return;
    }
    if (workspace?.id === wanted) {
      setArrived(wanted);
      clean();
      return;
    }
    if (switching.current === wanted) return;
    switching.current = wanted;
    // `to: null`: the person is already on the page of the link.
    void switchWorkspace(wanted, { to: null }).catch(() => undefined);
  }, [arrived, linked, rest, router, switchWorkspace, toast, wanted, workspace?.id]);

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
  if (status !== 'authenticated' || !me || moving) return <GlobalLoading />;
  if (me.workspaces.length === 0) return <NoWorkspace />;
  return <>{children}</>;
}

/** Someone removed from every workspace: they create their own (`POST /workspaces/`) and land in it. */
function NoWorkspace() {
  const [creating, setCreating] = useState(false);
  return (
    <>
      <SystemScreen
        title="No tenés ningún workspace"
        description="Creá el tuyo para empezar, o pedile a alguien que te invite al suyo."
        actions={
          <Button variant="primary" aria-haspopup="dialog" onClick={() => setCreating(true)}>
            Crear un espacio de trabajo
          </Button>
        }
      />
      <CreateWorkspaceDialog open={creating} onClose={() => setCreating(false)} />
    </>
  );
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
