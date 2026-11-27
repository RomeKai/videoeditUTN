import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { API_URL } from '@/api/config';
import real from '@/api/contracts/drf/responses.json';
import type { NotificationsResponse } from '@/api/contracts/notifications';
import { server } from '@/api/mocks/server';
import { tick } from '@/api/mocks/sim';
import { forceRouting } from '@/api/routing';
import { getActiveWorkspaceId } from '@/api/session';
import { db, editDb, renderScreen, signIn, startMockApi } from '@/testing';
import { NotificationsPanel } from './NotificationsPanel';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router, usePathname: () => '/proyectos', useSearchParams: () => new URLSearchParams(), useParams: () => ({}) }));

startMockApi();

beforeEach(() => signIn('estudio-norte'));
afterEach(() => {
  forceRouting(null);
  router.push.mockReset();
});

type Body = NotificationsResponse;
const LIST = real.notifications_list as Body;

/** The backend of the bell: answers the list it has, and records what the panel sends. */
function backend(initial: Body = LIST) {
  let body: Body = structuredClone(initial);
  const calls: string[] = [];
  const workspaces: (string | null)[] = [];
  const count = () => ({ ...body, unread_count: body.results.filter((item) => item.read_at === null).length });
  server.use(
    http.get(`${API_URL}/api/v1/notifications/`, ({ request }) => {
      calls.push('list');
      workspaces.push(request.headers.get('X-Workspace-Id'));
      return HttpResponse.json(count());
    }),
    http.post(`${API_URL}/api/v1/notifications/read-all/`, () => {
      calls.push('read-all');
      body = { ...body, results: body.results.map((item) => ({ ...item, read_at: item.read_at ?? '2026-11-04T16:30:00Z' })) };
      return new HttpResponse(null, { status: 204 });
    }),
    http.post(`${API_URL}/api/v1/notifications/:id/read/`, ({ params }) => {
      calls.push(`read ${String(params.id)}`);
      const found = body.results.find((item) => item.id === params.id);
      if (!found) return HttpResponse.json(real.errors.notification_404.body, { status: 404 });
      const read = { ...found, read_at: '2026-11-04T16:30:00Z' };
      body = { ...body, results: body.results.map((item) => (item.id === read.id ? read : item)) };
      return HttpResponse.json(read);
    }),
  );
  return { calls, workspaces };
}

const rows = () => screen.getAllByRole('button').filter((button) => button.className.includes('noti'));

describe('J2 · la campana con las respuestas del backend', () => {
  beforeEach(() => forceRouting({ mode: 'hybrid', real: 'notifications' }));

  it('muestra las seis, cuántas faltan leer y cuál ya está leída', async () => {
    const api = backend();
    renderScreen(<NotificationsPanel />);

    expect(await screen.findByText('Podcast Ep. 42 está listo para revisar')).toBeInTheDocument();
    expect(screen.getByText('5 sin leer')).toBeInTheDocument();
    expect(rows().map((row) => within(row).getAllByText(/./)[0]?.textContent)).toEqual([
      'Te quedan 300 tokens',
      'Podcast Ep. 42 está listo para revisar',
      'No pudimos cobrar tu compra de 5.000 tokens',
      'Se acreditaron 1.500 tokens',
      'Charla TEDx no se pudo procesar',
      'Entrevista con Ana: terminó el render',
    ]);
    expect(screen.getByText('Te devolvimos 12,50 tokens.')).toBeInTheDocument();
    expect(rows().map((row) => within(row).queryByText(/sin leer/) !== null)).toEqual([true, true, true, true, true, false]);
    expect(api.workspaces).toEqual([getActiveWorkspaceId()]);
  });

  it('al tocar una la marca leída en el backend y lleva a donde corresponde', async () => {
    const api = backend();
    const onNavigate = vi.fn();
    renderScreen(<NotificationsPanel onNavigate={onNavigate} />);
    const [low, ready, rejected, , failed, finished] = LIST.results;
    if (!low || !ready || !rejected || !failed || !finished) throw new Error('faltan notificaciones en el fixture');

    await userEvent.click(await screen.findByText(ready.title));
    expect(router.push).toHaveBeenLastCalledWith(`/proyectos/${ready.project_id}/revision`);
    expect(onNavigate).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByText('4 sin leer')).toBeInTheDocument());

    // Saldo bajo: a Comprar tokens.
    expect(low.type).toBe('low_balance');
    await userEvent.click(screen.getByText(low.title));
    expect(router.push).toHaveBeenLastCalledWith('/uso/comprar');

    await userEvent.click(screen.getByText(rejected.title));
    expect(router.push).toHaveBeenLastCalledWith('/uso/movimientos');
    await userEvent.click(screen.getByText(failed.title));
    expect(router.push).toHaveBeenLastCalledWith(`/proyectos/${failed.project_id}`);
    // One already read is not marked again.
    await userEvent.click(screen.getByText(finished.title));
    expect(router.push).toHaveBeenLastCalledWith(`/proyectos/${finished.project_id}/clips`);

    await waitFor(() => expect(screen.getByText('1 sin leer')).toBeInTheDocument());
    expect(api.calls.filter((call) => call.startsWith('read'))).toEqual([`read ${ready.id}`, `read ${low.id}`, `read ${rejected.id}`, `read ${failed.id}`]);
  });

  it('«Marcar todo como leído» lo pide una vez y vuelve a leer la lista', async () => {
    const api = backend();
    renderScreen(<NotificationsPanel />);

    await userEvent.click(await screen.findByRole('button', { name: 'Marcar todo como leído' }));

    await waitFor(() => expect(screen.queryByText(/sin leer/)).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Marcar todo como leído' })).toBeDisabled();
    expect(api.calls).toEqual(['list', 'read-all', 'list']);
  });

  it('sin notificaciones lo dice', async () => {
    backend({ unread_count: 0, results: [] });
    renderScreen(<NotificationsPanel />);

    expect(await screen.findByText('No tenés notificaciones.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Marcar todo como leído' })).toBeDisabled();
  });

  it('si el backend falla, se puede reintentar', async () => {
    server.use(http.get(`${API_URL}/api/v1/notifications/`, () => HttpResponse.json({ detail: 'x' }, { status: 500 })));
    renderScreen(<NotificationsPanel />);

    const retry = await screen.findByRole('button', { name: /Reintentar/ });
    backend();
    await userEvent.click(retry);

    expect(await screen.findByText('Podcast Ep. 42 está listo para revisar')).toBeInTheDocument();
  });
});

describe('J2 · los mocks se portan como el backend', () => {
  beforeEach(() => forceRouting({ mode: 'mock' }));

  it('un pago aprobado o rechazado le avisa a quien lo inició, y lleva a los movimientos', async () => {
    const me = db().sessionUserId;
    const workspace = getActiveWorkspaceId();
    if (!me || !workspace) throw new Error('falta la sesión');
    const purchase = (id: string, reject: boolean) => ({
      id,
      workspace,
      status: 'pending' as const,
      tokens: 1500,
      amount_usd: 15,
      plan_id: null,
      checkout_url: null,
      failure_reason: null,
      created_at: new Date().toISOString(),
      balance_after: null,
      _settleAt: 0,
      _reject: reject,
      _by: me,
    });
    editDb((current) => {
      current.notifications = [];
      current.purchases.push(purchase('33333333-3333-4333-8333-333333333333', false), purchase('44444444-4444-4444-8444-444444444444', true));
      tick(current, Date.now());
    });
    renderScreen(<NotificationsPanel />);

    expect(await screen.findByText('Se acreditaron 1.500 tokens')).toBeInTheDocument();
    expect(screen.getByText('No pudimos cobrar tu compra de 1.500 tokens')).toBeInTheDocument();
    expect(screen.getByText('2 sin leer')).toBeInTheDocument();
    expect(db().notifications.map((item) => item.user)).toEqual([me, me]);
    await userEvent.click(screen.getByText('Se acreditaron 1.500 tokens'));
    expect(router.push).toHaveBeenLastCalledWith('/uso/movimientos');
  });
});
