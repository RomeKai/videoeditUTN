import { useQuery } from '@tanstack/react-query';
import { screen, waitFor } from '@testing-library/react';
import { useEffect } from 'react';
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
import { MAINTENANCE_RECHECK_MS, MaintenanceGate } from './MaintenanceGate';
import { RequireSession } from './RequireSession';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/ajustes/redes',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

startMockApi();

const ACCOUNTS = `${API_URL}/api/v1/social-accounts/`;
const STATUS = `${API_URL}/api/v1/status/`;
const ME = `${API_URL}/api/v1/me/`;

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

  it('un 503 suelto con el estado en orden no saca la app: se pregunta el estado enseguida y la pantalla sigue montada', async () => {
    let mounts = 0;
    function Inside() {
      useEffect(() => {
        mounts += 1;
      }, []);
      return <p>adentro</p>;
    }
    const view = renderScenario(
      <MaintenanceGate>
        <Inside />
      </MaintenanceGate>,
    );
    await waitFor(() => expect(view.queryClient.getQueryData(keys.system)).toEqual({ maintenance: false, maintenance_window: null }));
    // Only this endpoint answers the 503 of a proxy; the status endpoint says all is well.
    let asked = 0;
    server.use(
      http.get(ACCOUNTS, () => new HttpResponse('<html>503</html>', { status: 503, headers: { 'Content-Type': 'text/html' } })),
      http.get(STATUS, () => {
        asked += 1;
        return undefined;
      }),
    );
    let shown = false;
    const observer = new MutationObserver(() => {
      if (screen.queryByText('Estamos en mantenimiento')) shown = true;
    });
    observer.observe(document.body, { childList: true, subtree: true });
    await apiCall(listSocialAccounts).catch(() => undefined);
    // The status was asked at once, not at the next poll…
    await waitFor(() => expect(asked).toBe(1));
    await new Promise((resolve) => setTimeout(resolve, 100));
    observer.disconnect();
    // …and it says all is well: the page never came up and the screen was never taken away.
    expect(shown).toBe(false);
    expect(screen.getByText('adentro')).toBeInTheDocument();
    expect(mounts).toBe(1);
    expect(asked).toBe(1);
  });

  it('una consulta de adentro que recibe siempre el 503 de un proxy no entra en un bucle de pedidos', async () => {
    // The storm: the gate took the app away, the status brought it back, the screen mounted again and asked
    // again, got the 503 again… This screen asks the way the app's do: it reads again when it mounts with an error.
    let requests = 0;
    let mounts = 0;
    server.use(
      http.get(ACCOUNTS, () => {
        requests += 1;
        return new HttpResponse('<html>503</html>', { status: 503, headers: { 'Content-Type': 'text/html' } });
      }),
    );
    function Inside() {
      useEffect(() => {
        mounts += 1;
      }, []);
      const accounts = useQuery({ queryKey: ['prueba', 'redes'], queryFn: () => apiCall(listSocialAccounts) });
      return <p>{accounts.isError ? 'no se pudo leer' : 'leyendo'}</p>;
    }
    renderScenario(
      <MaintenanceGate>
        <Inside />
      </MaintenanceGate>,
    );
    expect(await screen.findByText('no se pudo leer')).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(screen.getByText('no se pudo leer')).toBeInTheDocument();
    expect(screen.queryByText('Estamos en mantenimiento')).not.toBeInTheDocument();
    expect(requests).toBe(1);
    expect(mounts).toBe(1);
  });

  it('al terminar el mantenimiento cada cosa se pide una vez: lo que la pantalla ya está pidiendo al volver no se corta para pedirlo de nuevo', async () => {
    const down = () => HttpResponse.json({ detail: 'Estamos haciendo mantenimiento. Volvé en un rato.' }, { status: 503 });
    let state: 'up' | 'down' = 'up';
    let requests = 0;
    server.use(
      http.get(ACCOUNTS, () => {
        requests += 1;
        return state === 'up' ? undefined : down();
      }),
      http.get(STATUS, () => (state === 'up' ? undefined : down())),
    );
    // A screen with data from before, already old when it comes back: it reads again as it mounts.
    const key = ['prueba', 'redes'];
    function Inside() {
      const accounts = useQuery({ queryKey: key, queryFn: () => apiCall(listSocialAccounts), staleTime: 0 });
      return <p>{accounts.data ? `redes: ${accounts.data.length}` : 'leyendo'}</p>;
    }
    const view = renderScenario(
      <MaintenanceGate>
        <Inside />
      </MaintenanceGate>,
    );
    expect(await screen.findByText(/^redes: \d/)).toBeInTheDocument();
    expect(requests).toBe(1);

    state = 'down';
    await view.queryClient.refetchQueries({ queryKey: key });
    expect(await screen.findByRole('heading', { name: 'Estamos en mantenimiento' })).toBeInTheDocument();
    expect(requests).toBe(2);

    state = 'up';
    document.dispatchEvent(new Event('visibilitychange'));
    expect(await screen.findByText(/^redes: \d/)).toBeInTheDocument();
    await waitFor(() => expect(view.queryClient.isFetching()).toBe(0));
    await new Promise((resolve) => setTimeout(resolve, 100));
    // One more, not two: the one the screen made as it came back.
    expect(requests).toBe(3);
  });

  it('mientras dura, vuelve a preguntar el estado cada 10 s; una pestaña oculta no pregunta hasta que vuelve al frente', async () => {
    await renderGate();
    const down = () => HttpResponse.json({ detail: 'Estamos haciendo mantenimiento. Volvé en un rato.' }, { status: 503 });
    let asked = 0;
    let over = false;
    server.use(
      http.get(ACCOUNTS, down),
      http.get(STATUS, () => {
        asked += 1;
        return over ? HttpResponse.json({ maintenance: false, maintenance_window: null }) : down();
      }),
    );
    const visibility = (state: 'visible' | 'hidden') => {
      Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
    };
    // (`waitFor` polls with setInterval, which is faked here: this one waits with real timeouts.)
    const until = async (check: () => boolean) => {
      for (let tries = 0; tries < 150 && !check(); tries += 1) await new Promise((resolve) => setTimeout(resolve, 20));
      expect(check()).toBe(true);
    };
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    try {
      await apiCall(listSocialAccounts).catch(() => undefined);
      expect(await screen.findByRole('heading', { name: 'Estamos en mantenimiento' })).toBeInTheDocument();
      await until(() => asked === 1);
      // Ten seconds later, still down: asked again.
      vi.advanceTimersByTime(MAINTENANCE_RECHECK_MS);
      await until(() => asked === 2);
      // Hidden: the clock goes on and nothing is asked.
      visibility('hidden');
      vi.advanceTimersByTime(MAINTENANCE_RECHECK_MS * 3);
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(asked).toBe(2);
      // Back to the front, with the maintenance over: asked at once, and the app is back.
      over = true;
      visibility('visible');
      expect(await screen.findByText('adentro')).toBeInTheDocument();
      expect(asked).toBe(3);
    } finally {
      vi.useRealTimers();
      Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    }
  });

  it('cuando termina, la app vuelve sola: la cuenta que no se pudo cargar durante el mantenimiento se pide de nuevo', async () => {
    // A tab opened (or reloaded) with the platform down: the profile never loaded.
    const down = () => HttpResponse.json({ detail: 'Estamos haciendo mantenimiento. Volvé en un rato.' }, { status: 503 });
    let over = false;
    let profile = 0;
    server.use(
      http.get(ME, () => {
        profile += 1;
        return over ? undefined : down();
      }),
      http.get(STATUS, () => (over ? undefined : down())),
    );
    renderScenario(
      <MaintenanceGate>
        <RequireSession>
          <p>adentro</p>
        </RequireSession>
      </MaintenanceGate>,
    );
    expect(await screen.findByRole('heading', { name: 'Estamos en mantenimiento' })).toBeInTheDocument();
    await waitFor(() => expect(profile).toBe(1));

    over = true;
    // The tab asks the status again (here, as when it comes back to the front).
    document.dispatchEvent(new Event('visibilitychange'));
    expect(await screen.findByText('adentro')).toBeInTheDocument();
    expect(screen.queryByText('Algo falló de nuestro lado')).not.toBeInTheDocument();
    expect(profile).toBe(2);
  });

  it('un 503 suelto que el estado desmiente no hace pedir todo de nuevo: el pedido que falla no se repite sin fin', async () => {
    // Only the profile answers the 503 of a proxy, every time; the status endpoint says all is well.
    let profile = 0;
    server.use(
      http.get(ME, () => {
        profile += 1;
        return new HttpResponse('<html>503</html>', { status: 503, headers: { 'Content-Type': 'text/html' } });
      }),
    );
    renderScenario(
      <MaintenanceGate>
        <RequireSession>
          <p>adentro</p>
        </RequireSession>
      </MaintenanceGate>,
    );
    // The app is back with the error of that request, to retry by hand.
    expect(await screen.findByRole('button', { name: 'Reintentar' }, { timeout: 2000 })).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(screen.getByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
    expect(profile).toBe(1);
  });
});
