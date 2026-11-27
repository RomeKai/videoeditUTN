import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { API_URL } from '../config';
import { clearSession, getTokens, setTokens } from '../session';
import { useLogout } from './auth';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  clearSession();
});
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>{children}</QueryClientProvider>;
}

/** The backend of logout and refresh: an expired access token answers 401; the refresh rotates. */
function backend() {
  const logouts: unknown[] = [];
  server.use(
    http.post(`${API_URL}/api/v1/auth/logout/`, async ({ request }) => {
      if (request.headers.get('Authorization') === 'Bearer vencido') {
        return HttpResponse.json({ detail: 'Given token not valid for any token type', code: 'token_not_valid' }, { status: 401 });
      }
      logouts.push(await request.json());
      return new HttpResponse(null, { status: 204 });
    }),
    http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ access: 'acceso-nuevo', refresh: 'refresh-nuevo' })),
  );
  return logouts;
}

describe('useLogout', () => {
  it('con el acceso vencido, cierra también el refresh que la renovación trajo en el camino', async () => {
    const logouts = backend();
    setTokens({ access: 'vencido', refresh: 'refresh-viejo' });
    const { result } = renderHook(() => useLogout(), { wrapper });

    await act(() => result.current.mutateAsync({}));

    // The first one was rotated by the renewal (the backend answers 204: already closed); the second is the live one.
    expect(logouts).toEqual([{ refresh: 'refresh-viejo', all_devices: false }, { refresh: 'refresh-nuevo' }]);
    expect(getTokens()).toBeNull();
  });

  it('sin renovación es un solo pedido, y con todos los dispositivos también', async () => {
    const logouts = backend();
    setTokens({ access: 'vigente', refresh: 'refresh-1' });
    const { result } = renderHook(() => useLogout(), { wrapper });
    await act(() => result.current.mutateAsync({}));

    setTokens({ access: 'vencido', refresh: 'refresh-2' });
    await act(() => result.current.mutateAsync({ allDevices: true }));

    expect(logouts).toEqual([
      { refresh: 'refresh-1', all_devices: false },
      { refresh: 'refresh-2', all_devices: true },
    ]);
    expect(getTokens()).toBeNull();
  });
});
