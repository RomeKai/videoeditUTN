import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';
import { apiCall } from '@/api/client';
import { API_URL } from '@/api/config';
import { listSocialAccounts } from '@/api/contracts/social';
import { ApiError } from '@/api/errors';
import { onMaintenance } from '@/api/session';
import { server } from '@/api/mocks/server';
import { keys } from '@/api/query';
import { renderScenario, startMockApi } from '@/testing';
import { MaintenanceGate } from './MaintenanceGate';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/ajustes/redes',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

startMockApi();

const ACCOUNTS = `${API_URL}/api/v1/social-accounts/`;
const STATUS = `${API_URL}/api/v1/status/`;

/** The app behind the gate, once the status endpoint answered that all is well. */
async function renderGate() {
  const view = renderScenario(
    <MaintenanceGate>
      <p>adentro</p>
    </MaintenanceGate>,
  );
  await waitFor(() => expect(view.queryClient.getQueryData(keys.system)).toEqual({ maintenance: false, maintenance_window: null }));
  expect(screen.getByText('adentro')).toBeInTheDocument();
  return view;
}

describe('K5 · cuándo la app pasa a mantenimiento', () => {
  /** Whether the maintenance page was up at any moment while `run` ran (it can come and go between two looks at the screen). */
  async function everClosed(run: () => Promise<unknown>): Promise<boolean> {
    let closed = false;
    const off = onMaintenance((active) => {
      if (active) closed = true;
    });
    const observer = new MutationObserver(() => {
      if (screen.queryByText('Estamos en mantenimiento')) closed = true;
    });
    observer.observe(document.body, { childList: true, subtree: true });
    await run();
    // Long enough for the gate to have switched if it were going to.
    await new Promise((resolve) => setTimeout(resolve, 50));
    observer.disconnect();
    off();
    return closed;
  }

  it('un 503 con `code` (el proveedor de redes no respondió) deja la app como estaba', async () => {
    await renderGate();
    server.use(http.get(ACCOUNTS, () => HttpResponse.json({ error: 'No pudimos leer las redes conectadas. Probá de nuevo en unos minutos.', code: 'provider_not_configured' }, { status: 503 })));
    let error: unknown;
    expect(await everClosed(async () => (error = await apiCall(listSocialAccounts).catch((caught: unknown) => caught)))).toBe(false);
    // The request fails where it was made, with the backend's code.
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe('provider_not_configured');
    expect(screen.getByText('adentro')).toBeInTheDocument();
  });

  it('un 502 con `code` tampoco', async () => {
    await renderGate();
    server.use(http.get(ACCOUNTS, () => HttpResponse.json({ error: 'No pudimos leer las redes conectadas. Probá de nuevo en unos minutos.', code: 'provider_unavailable' }, { status: 502 })));
    expect(await everClosed(() => apiCall(listSocialAccounts).catch(() => undefined))).toBe(false);
    expect(screen.getByText('adentro')).toBeInTheDocument();
  });

  it('el 503 del mantenimiento sí, y la app vuelve cuando el estado dice que terminó, sin esperar el minuto del sondeo', async () => {
    await renderGate();
    // The whole API is down, the status endpoint included.
    const down = () => HttpResponse.json({ detail: 'Estamos haciendo mantenimiento. Volvé en un rato.' }, { status: 503 });
    let asked = 0;
    server.use(
      http.get(ACCOUNTS, down),
      http.get(STATUS, () => {
        asked += 1;
        return down();
      }),
    );
    await apiCall(listSocialAccounts).catch(() => undefined);
    expect(await screen.findByRole('heading', { name: 'Estamos en mantenimiento' })).toBeInTheDocument();
    expect(screen.queryByText('adentro')).not.toBeInTheDocument();
    // The status was asked at once, not at the next poll.
    await waitFor(() => expect(asked).toBeGreaterThan(0));
    expect(screen.getByRole('heading', { name: 'Estamos en mantenimiento' })).toBeInTheDocument();
  });

  it('un 503 suelto con el estado en orden no deja la app en mantenimiento', async () => {
    await renderGate();
    // Only this endpoint answers the 503 of a proxy; the status endpoint (the mock's) says all is well.
    server.use(http.get(ACCOUNTS, () => new HttpResponse('<html>503</html>', { status: 503, headers: { 'Content-Type': 'text/html' } })));
    await apiCall(listSocialAccounts).catch(() => undefined);
    expect(await screen.findByRole('heading', { name: 'Estamos en mantenimiento' })).toBeInTheDocument();
    // Back without waiting for the 60 s poll: the gate asked the status when the 503 arrived.
    expect(await screen.findByText('adentro', undefined, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.queryByText('Estamos en mantenimiento')).not.toBeInTheDocument();
  });
});
