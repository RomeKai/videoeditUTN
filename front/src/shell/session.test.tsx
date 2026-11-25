import { QueryClient, QueryClientProvider, keepPreviousData } from '@tanstack/react-query';
import { act, cleanup, render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { hydrateRoot } from 'react-dom/client';
import { renderToString } from 'react-dom/server';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { API_URL, WORKSPACE_HEADER } from '@/api/config';
import { useScopedQuery, useWallet } from '@/api/hooks';
import { AUTH_STORAGE_KEY, clearSession, isSessionExpired, setActiveWorkspaceId, setTokens } from '@/api/session';
import { SessionProvider, useSession, type SessionStatus } from './session';

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
  server.resetHandlers();
  clearSession();
});
afterAll(() => server.close());

const seen: SessionStatus[] = [];
let switchTo: (id: string) => Promise<void> = async () => undefined;
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
