import { QueryClient, QueryClientProvider, keepPreviousData } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { hydrateRoot } from 'react-dom/client';
import { renderToString } from 'react-dom/server';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { API_URL, WORKSPACE_HEADER } from '@/api/config';
import { useScopedQuery, useWallet } from '@/api/hooks';
import { AUTH_STORAGE_KEY, clearSession, getActiveWorkspaceId, isSessionExpired, setActiveWorkspaceId, setTokens } from '@/api/session';
import { UnsavedChangesDialog } from '@/dialogs';
import { LeaveGuardProvider, useLeaveGuard } from './leaveGuard';
import { SessionProvider, useSession, type SessionStatus, type SessionValue } from './session';

/** The app's router and where it is, for the tests that switch workspace from another screen. */
const nav = vi.hoisted(() => {
  const state = { pathname: '/proyectos', pushed: [] as string[] };
  const router = { push: (href: string) => void state.pushed.push(href), replace: () => undefined, back: () => undefined, refresh: () => undefined, prefetch: () => undefined };
  return { state, router };
});
vi.mock('next/navigation', () => ({ useRouter: () => nav.router, usePathname: () => nav.state.pathname }));

const WS = 'a3c1e2f4-5b6d-4e7f-8a9b-0c1d2e3f4a5b';
const me = {
  id: 'b3c1e2f4-5b6d-4e7f-8a9b-0c1d2e3f4a5b',
  email: 'lucia@example.com',
  name: 'Lucía',
  is_email_verified: true,
  tour_completed: true,
  terms_accepted_at: '2026-01-01T00:00:00Z',
  active_workspace_id: WS,
  workspaces: [{ id: WS, name: 'Estudio', role: 'owner', plan: null, available_balance: '100.00' }],
};
const wallet = { id: WS, workspace: WS, available_balance: '100.0000000000', reserved_balance: '0.0000000000', updated_at: '2026-01-01T00:00:00Z', spent_this_month: '0.00', low_balance_threshold: '10.00', reservations: [] };

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  // Unmount first: a mounted tree would refetch against handlers that are gone.
  cleanup();
  nav.state.pathname = '/proyectos';
  nav.state.pushed = [];
  server.resetHandlers();
  clearSession();
});
afterAll(() => server.close());

const seen: SessionStatus[] = [];
let switchTo: SessionValue['switchWorkspace'] = async () => false;
function Probe() {
  const { status, workspace, switchWorkspace } = useSession();
  switchTo = switchWorkspace;
  seen.push(status);
  const balance = useWallet();
  return (
    <p>
      {status}:{workspace?.name ?? '-'}:{balance.data ? balance.data.available_balance : 'sin saldo'}
    </p>
  );
}

function app() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <SessionProvider>
        <Probe />
      </SessionProvider>
    </QueryClientProvider>
  );
}

describe('sesión al hidratar', () => {
  it('con tokens guardados nunca pasa por «sin sesión»: es desconocida hasta leer el navegador', async () => {
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ access: 'a', refresh: 'r' }));
    const walletHeaders: Array<string | null> = [];
    server.use(
      http.get(`${API_URL}/api/v1/me/`, () => HttpResponse.json(me)),
      http.get(`${API_URL}/api/v1/wallet/`, ({ request }) => {
        walletHeaders.push(request.headers.get(WORKSPACE_HEADER));
        return HttpResponse.json(wallet);
      }),
    );
    seen.length = 0;
    // What the server renders: no storage there.
    const html = renderToString(app());
    expect(html).toContain('loading');
    const container = document.createElement('div');
    container.innerHTML = html;
    document.body.appendChild(container);
    let root: ReturnType<typeof hydrateRoot> | undefined;
    await act(async () => {
      root = hydrateRoot(container, app());
    });
    await vi.waitFor(() => expect(container.textContent).toBe('authenticated:Estudio:100'));
    expect(seen).not.toContain('anonymous');
    // Workspace-scoped queries wait for the workspace: every request carries the header.
    expect(walletHeaders).toEqual([WS]);
    await act(async () => root?.unmount());
    container.remove();
  });

  it('sin tokens es anónima, y al cerrar sesión deja de mostrar al usuario', async () => {
    server.use(http.get(`${API_URL}/api/v1/me/`, () => HttpResponse.json(me)), http.get(`${API_URL}/api/v1/wallet/`, () => HttpResponse.json(wallet)));
    render(app());
    expect(await screen.findByText('anonymous:-:sin saldo')).toBeInTheDocument();
    act(() => setTokens({ access: 'a', refresh: 'r' }));
    expect(await screen.findByText('authenticated:Estudio:100')).toBeInTheDocument();
    act(() => clearSession());
    expect(await screen.findByText('anonymous:-:sin saldo')).toBeInTheDocument();
  });

  it('cambiar de workspace mueve la UI aunque `/me` vuelva igual, y también a uno que `/me` todavía no traía', async () => {
    const OTHER = 'c3c1e2f4-5b6d-4e7f-8a9b-0c1d2e3f4a5b';
    const NEW = 'd3c1e2f4-5b6d-4e7f-8a9b-0c1d2e3f4a5b';
    const two = { ...me, workspaces: [...me.workspaces, { id: OTHER, name: 'Cliente', role: 'editor', plan: null, available_balance: '7.00' }] };
    let current: typeof two = two;
    const walletHeaders: Array<string | null> = [];
    server.use(
      http.get(`${API_URL}/api/v1/me/`, () => HttpResponse.json(current)),
      // Today's backend ignores `active_workspace_id`: the answer is the same `me`.
      http.patch(`${API_URL}/api/v1/me/`, () => HttpResponse.json(current)),
      http.get(`${API_URL}/api/v1/wallet/`, ({ request }) => {
        const id = request.headers.get(WORKSPACE_HEADER);
        walletHeaders.push(id);
        return HttpResponse.json({ ...wallet, available_balance: id === WS ? '100.0000000000' : '7.0000000000' });
      }),
    );
    setTokens({ access: 'a', refresh: 'r' });
    render(app());
    expect(await screen.findByText('authenticated:Estudio:100')).toBeInTheDocument();
    await act(() => switchTo(OTHER));
    expect(await screen.findByText('authenticated:Cliente:7')).toBeInTheDocument();
    expect(walletHeaders.at(-1)).toBe(OTHER);

    // An invitation was just accepted: the workspace only shows up in the `/me` that follows.
    current = { ...two, workspaces: [...two.workspaces, { id: NEW, name: 'Invitado', role: 'viewer', plan: null, available_balance: '7.00' }] };
    await act(() => switchTo(NEW));
    expect(await screen.findByText('authenticated:Invitado:7')).toBeInTheDocument();
    expect(walletHeaders.at(-1)).toBe(NEW);
  });

  it('dos cambios seguidos: la respuesta tardía del primero no devuelve al workspace anterior', async () => {
    const OTHER = 'c3c1e2f4-5b6d-4e7f-8a9b-0c1d2e3f4a5b';
    const THIRD = 'e3c1e2f4-5b6d-4e7f-8a9b-0c1d2e3f4a5b';
    const three = {
      ...me,
      workspaces: [
        ...me.workspaces,
        { id: OTHER, name: 'Cliente', role: 'editor', plan: null, available_balance: '7.00' },
        { id: THIRD, name: 'Personal', role: 'owner', plan: null, available_balance: '7.00' },
      ],
    };
    /** The first PATCH hangs until the test lets it answer. */
    let answerFirst: () => void = () => undefined;
    const firstHeld = new Promise<void>((resolve) => (answerFirst = resolve));
    const walletHeaders: Array<string | null> = [];
    server.use(
      http.get(`${API_URL}/api/v1/me/`, () => HttpResponse.json(three)),
      http.patch(`${API_URL}/api/v1/me/`, async ({ request }) => {
        const body = (await request.json()) as { active_workspace_id: string };
        if (body.active_workspace_id === OTHER) await firstHeld;
        return HttpResponse.json({ ...three, active_workspace_id: body.active_workspace_id });
      }),
      http.get(`${API_URL}/api/v1/wallet/`, ({ request }) => {
        const id = request.headers.get(WORKSPACE_HEADER);
        walletHeaders.push(id);
        return HttpResponse.json({ ...wallet, available_balance: id === WS ? '100.0000000000' : '7.0000000000' });
      }),
    );
    setTokens({ access: 'a', refresh: 'r' });
    render(app());
    expect(await screen.findByText('authenticated:Estudio:100')).toBeInTheDocument();

    let first: Promise<boolean> = Promise.resolve(true);
    act(() => void (first = switchTo(OTHER)));
    let second = false;
    await act(async () => {
      second = await switchTo(THIRD);
    });
    expect(second).toBe(true);
    expect(await screen.findByText('authenticated:Personal:7')).toBeInTheDocument();

    // The slow answer of the first switch arrives now.
    const requestsBefore = walletHeaders.length;
    let firstWasCurrent = true;
    await act(async () => {
      answerFirst();
      firstWasCurrent = await first;
    });
    expect(firstWasCurrent).toBe(false);
    await act(() => new Promise((resolve) => setTimeout(resolve, 30)));
    expect(screen.getByText('authenticated:Personal:7')).toBeInTheDocument();
    expect(getActiveWorkspaceId()).toBe(THIRD);
    // And it does not reload the screen either: the newer switch already did.
    expect(walletHeaders.length).toBe(requestsBefore);
    expect(walletHeaders.at(-1)).toBe(THIRD);
  });

  describe('cambiar de workspace desde otra pantalla', () => {
    const OTHER = 'c3c1e2f4-5b6d-4e7f-8a9b-0c1d2e3f4a5b';
    const KIT = '00000009-0000-4000-8000-000000000001';
    const two = { ...me, workspaces: [...me.workspaces, { id: OTHER, name: 'Cliente', role: 'editor', plan: null, available_balance: '7.00' }] };
    /** Every request, with the workspace it was made for. */
    let requests: string[] = [];
    let patchStatus = 200;
    const mockApi = () => {
      requests = [];
      patchStatus = 200;
      const seen = (request: Request) => void requests.push(`${request.method} ${new URL(request.url).pathname} @${request.headers.get(WORKSPACE_HEADER) === WS ? 'Estudio' : 'Cliente'}`);
      server.use(
        http.get(`${API_URL}/api/v1/me/`, ({ request }) => (seen(request), HttpResponse.json(two))),
        http.patch(`${API_URL}/api/v1/me/`, () => (patchStatus === 200 ? HttpResponse.json(two) : HttpResponse.json({ detail: 'caído' }, { status: patchStatus }))),
        http.get(`${API_URL}/api/v1/wallet/`, ({ request }) => {
          seen(request);
          return HttpResponse.json({ ...wallet, available_balance: request.headers.get(WORKSPACE_HEADER) === WS ? '100.0000000000' : '7.0000000000' });
        }),
        // The kit belongs to Estudio: in any other workspace it does not exist.
        http.get(`${API_URL}/api/v1/brand-kits/:id/`, ({ request }) => {
          seen(request);
          return request.headers.get(WORKSPACE_HEADER) === WS ? HttpResponse.json({ id: KIT, name: 'Estudio Norte' }) : HttpResponse.json({ detail: 'No encontrado.' }, { status: 404 });
        }),
      );
    };
    /** A detail screen: it reads a record of the active workspace, and may have unsaved changes. */
    function KitScreen({ dirty = false }: { dirty?: boolean }) {
      const kit = useScopedQuery({ queryKey: ['brand-kits', KIT], queryFn: () => fetch(`${API_URL}/api/v1/brand-kits/${KIT}/`, { headers: { [WORKSPACE_HEADER]: getActiveWorkspaceId() ?? '' } }).then((response) => (response.ok ? response.json() : Promise.reject(new Error('404')))), retry: false });
      const guard = useLeaveGuard(dirty);
      return (
        <>
          <p>{kit.isError ? 'Esta página no existe' : kit.data ? 'kit cargado' : 'cargando el kit'}</p>
          <UnsavedChangesDialog {...guard.dialog} />
        </>
      );
    }
    const detailApp = (client: QueryClient, dirty = false) => (
      <QueryClientProvider client={client}>
        <LeaveGuardProvider>
          <SessionProvider>
            <Probe />
            {nav.state.pathname === '/proyectos' ? <p>Proyectos</p> : <KitScreen dirty={dirty} />}
          </SessionProvider>
        </LeaveGuardProvider>
      </QueryClientProvider>
    );
    const open = async (dirty = false) => {
      mockApi();
      nav.state.pathname = `/brand-kits/${KIT}`;
      setTokens({ access: 'a', refresh: 'r' });
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const view = render(detailApp(client, dirty));
      expect(await screen.findByText('kit cargado')).toBeInTheDocument();
      expect(await screen.findByText('authenticated:Estudio:100')).toBeInTheDocument();
      requests = [];
      /** The router finished the navigation the switch started. */
      const arrive = () => {
        nav.state.pathname = nav.state.pushed.at(-1) ?? nav.state.pathname;
        view.rerender(detailApp(client, dirty));
      };
      return { client, arrive };
    };

    it('primero sale de la pantalla y recién al llegar cambia el workspace: la que se deja nunca pide su dato en el otro', async () => {
      const { client, arrive } = await open();
      let switched: boolean | 'pendiente' = 'pendiente';
      act(() => void switchTo(OTHER).then((value) => (switched = value)));
      await waitFor(() => expect(nav.state.pushed).toEqual(['/proyectos']));
      // Still on the kit, still in its workspace: nothing reloads, nothing says "no existe".
      await act(() => new Promise((resolve) => setTimeout(resolve, 40)));
      expect(getActiveWorkspaceId()).toBe(WS);
      expect(screen.getByText('kit cargado')).toBeInTheDocument();
      expect(requests).toEqual([]);

      act(arrive);
      expect(getActiveWorkspaceId()).toBe(OTHER);
      expect(await screen.findByText('authenticated:Cliente:7')).toBeInTheDocument();
      await waitFor(() => expect(switched).toBe(true));
      expect(screen.queryByText('Esta página no existe')).not.toBeInTheDocument();
      // The kit was never asked for in the workspace that does not have it, and the balance was read once.
      expect(requests.filter((line) => line.includes('brand-kits'))).toEqual([]);
      expect(requests.filter((line) => line.includes('wallet'))).toEqual(['GET /api/v1/wallet/ @Cliente']);
      // What belongs to no workspace is read again; what was the previous workspace's is gone from the cache.
      expect(requests.filter((line) => line.includes('/me/'))).toHaveLength(1);
      expect(client.getQueryCache().findAll({ predicate: (query) => query.queryKey.at(-1) === WS })).toEqual([]);
    });

    it('con cambios sin guardar pregunta antes: «Seguir editando» no cambia nada y «Descartar» cambia', async () => {
      const { arrive } = await open(true);
      let switched: boolean | 'pendiente' = 'pendiente';
      act(() => void switchTo(OTHER).then((value) => (switched = value)));
      const dialog = await screen.findByRole('alertdialog', { name: 'Tenés cambios sin guardar' });
      expect(nav.state.pushed).toEqual([]);
      expect(getActiveWorkspaceId()).toBe(WS);
      fireEvent.click(within(dialog).getByRole('button', { name: 'Seguir editando' }));
      await waitFor(() => expect(switched).toBe(false));
      expect(nav.state.pushed).toEqual([]);
      expect(getActiveWorkspaceId()).toBe(WS);
      expect(requests).toEqual([]);

      switched = 'pendiente';
      act(() => void switchTo(OTHER).then((value) => (switched = value)));
      fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Descartar' }));
      await waitFor(() => expect(nav.state.pushed).toEqual(['/proyectos']));
      act(arrive);
      await waitFor(() => expect(switched).toBe(true));
      expect(getActiveWorkspaceId()).toBe(OTHER);
    });

    it('si guardar el workspace en el perfil falla, el cambio queda igual: cada pedido dice su workspace', async () => {
      const { arrive } = await open();
      patchStatus = 500;
      let switched: boolean | 'pendiente' = 'pendiente';
      act(() => void switchTo(OTHER).then((value) => (switched = value)));
      await waitFor(() => expect(nav.state.pushed).toEqual(['/proyectos']));
      act(arrive);
      await waitFor(() => expect(switched).toBe(true));
      expect(await screen.findByText('authenticated:Cliente:7')).toBeInTheDocument();
    });

    it('`to: null` cambia sin navegar (lo hace quien llama)', async () => {
      await open();
      await act(async () => void (await switchTo(OTHER, { to: null })));
      expect(nav.state.pushed).toEqual([]);
      expect(getActiveWorkspaceId()).toBe(OTHER);
    });
  });

  it('si la sesión venció antes de que `/me` respondiera, queda cargando (con «Sesión vencida» encima), no en error', async () => {
    server.use(
      http.get(`${API_URL}/api/v1/me/`, () => HttpResponse.json({ detail: 'Token is invalid or expired' }, { status: 401 })),
      http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ detail: 'Token is invalid or expired' }, { status: 401 })),
    );
    setTokens({ access: 'a', refresh: 'r' });
    seen.length = 0;
    render(app());
    await vi.waitFor(() => expect(isSessionExpired()).toBe(true));
    await act(async () => undefined);
    expect(screen.getByText('loading:-:sin saldo')).toBeInTheDocument();
    expect(seen).not.toContain('error');
  });

  it('una lista con keepPreviousData no muestra las filas del workspace anterior después de cambiar', async () => {
    setActiveWorkspaceId('ws-a');
    let resolveB: (value: string[]) => void = () => undefined;
    function List() {
      const query = useScopedQuery({
        queryKey: ['projects', 'list', { page: 1 }],
        queryFn: ({ queryKey }) => (queryKey.at(-1) === 'ws-a' ? Promise.resolve(['Proyecto de A']) : new Promise<string[]>((resolve) => (resolveB = resolve))),
        placeholderData: keepPreviousData,
      });
      return <p>{query.data?.join(',') ?? 'cargando'}</p>;
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <List />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Proyecto de A')).toBeInTheDocument();
    act(() => setActiveWorkspaceId('ws-b'));
    expect(screen.getByText('cargando')).toBeInTheDocument();
    await act(async () => resolveB(['Proyecto de B']));
    expect(await screen.findByText('Proyecto de B')).toBeInTheDocument();
  });
});
