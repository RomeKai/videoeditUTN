import { MutationObserver } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { apiCall, REFRESH_HANDOVER_WAIT_MS, REFRESH_LOCK_NAME, REFRESH_LOCK_WAIT_MS, toFormData } from './client';
import { errorMessage } from '@/lib/error-messages';
import { API_URL, REQUEST_TIMEOUT_MS, SLOW_REQUEST_TIMEOUT_MS, UPLOAD_STALL_TIMEOUT_MS, WORKSPACE_HEADER } from './config';
import { exportTransactions } from './contracts/billing';
import { createBrandKit, duplicateBrandKit, updateBrandKit } from './contracts/brandkits';
import real from './contracts/drf/responses.json';
import { createProject, createProjectFromPrompt, downloadAllClips } from './contracts/projects';
import { defineEndpoint } from './endpoint';
import { ApiError, ContractError, NetworkError, RequestTimeoutError, SessionExpiredError, UploadStalledError, isOutcomeUnknown } from './errors';
import { createQueryClient, shouldRetry } from './query';
import { AUTH_STORAGE_KEY, clearSession, getActiveWorkspaceId, getTokens, isSessionExpired, notifySessionExpired, onMaintenance, onSessionExpired, setActiveWorkspaceId, setTokens, subscribeSession } from './session';

const Thing = z.object({ id: z.number(), name: z.string() });

const getThing = defineEndpoint({
  id: 'test.thing',
  method: 'GET',
  path: '/api/v1/things/:id/',
  summary: 'Trae una cosa de prueba.',
  query: z.object({ expand: z.boolean().optional() }),
  response: Thing,
  errors: [],
  backend: { status: 'proposed', note: 'Solo para el test.' },
  screens: ['test'],
});

const createThing = defineEndpoint({
  id: 'test.createThing',
  method: 'POST',
  path: '/api/v1/things/',
  summary: 'Crea una cosa de prueba.',
  request: z.object({ name: z.string().min(1) }),
  response: Thing,
  successStatus: 201,
  errors: [],
  backend: { status: 'proposed', note: 'Solo para el test.' },
  screens: ['test'],
});

const publicThing = defineEndpoint({
  id: 'test.public',
  method: 'GET',
  path: '/api/v1/public/',
  summary: 'Endpoint público de prueba.',
  response: z.object({ ok: z.boolean() }),
  auth: false,
  workspaceScoped: false,
  errors: [],
  backend: { status: 'proposed', note: 'Solo para el test.' },
  screens: ['test'],
});

const optionalThing = defineEndpoint({
  id: 'test.optional',
  method: 'POST',
  path: '/api/v1/optional/',
  summary: 'Endpoint público que manda el token si hay sesión.',
  request: z.object({}),
  response: z.object({ user: z.string().nullable() }),
  auth: 'optional',
  workspaceScoped: false,
  errors: [],
  backend: { status: 'proposed', note: 'Solo para el test.' },
  screens: ['test'],
});

const uploadThing = defineEndpoint({
  id: 'test.upload',
  method: 'POST',
  path: '/api/v1/uploads/',
  summary: 'Subida de prueba.',
  request: z.object({ title: z.string() }),
  bodyType: 'multipart',
  response: z.object({ ok: z.boolean() }),
  errors: [],
  backend: { status: 'proposed', note: 'Solo para el test.' },
  screens: ['test'],
});

/** A JWT-shaped token that expires `seconds` from now. */
function jwt(seconds: number, name: string): string {
  const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + seconds, name })).replace(/=+$/, '');
  return `h.${payload}.s`;
}

const deleteThing = defineEndpoint({
  id: 'test.deleteThing',
  method: 'DELETE',
  path: '/api/v1/things/:id/',
  summary: 'Borra una cosa de prueba.',
  response: z.void(),
  successStatus: 204,
  errors: [],
  backend: { status: 'proposed', note: 'Solo para el test.' },
  screens: ['test'],
});

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('apiCall', () => {
  it('manda el token, el workspace activo y la query, y valida la respuesta', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    setActiveWorkspaceId('ws-1');
    let seen: Request | null = null;
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, ({ request, params }) => {
        seen = request;
        return HttpResponse.json({ id: Number(params.id), name: 'Vlog' });
      }),
    );
    const thing = await apiCall(getThing, { params: { id: '7' }, query: { expand: true } });
    expect(thing).toEqual({ id: 7, name: 'Vlog' });
    const request = seen as unknown as Request;
    expect(request.headers.get('Authorization')).toBe('Bearer acceso-1');
    expect(request.headers.get(WORKSPACE_HEADER)).toBe('ws-1');
    expect(new URL(request.url).searchParams.get('expand')).toBe('true');
  });

  it('una llamada puede decir su propio workspace (para limpiar lo de la pantalla que se deja) y su clave de idempotencia', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    setActiveWorkspaceId('ws-nuevo');
    const seen: Array<{ workspace: string | null; key: string | null }> = [];
    server.use(
      http.post(`${API_URL}/api/v1/things/`, ({ request }) => {
        seen.push({ workspace: request.headers.get(WORKSPACE_HEADER), key: request.headers.get('Idempotency-Key') });
        return HttpResponse.json({ id: 1, name: 'x' }, { status: 201 });
      }),
    );
    await apiCall(createThing, { body: { name: 'x' } });
    await apiCall(createThing, { body: { name: 'x' }, workspaceId: 'ws-anterior', idempotencyKey: 'envio-1' });
    expect(seen).toEqual([
      { workspace: 'ws-nuevo', key: null },
      { workspace: 'ws-anterior', key: 'envio-1' },
    ]);
  });

  it('no manda credenciales a un endpoint público', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    setActiveWorkspaceId('ws-1');
    let headers: Headers | null = null;
    server.use(
      http.get(`${API_URL}/api/v1/public/`, ({ request }) => {
        headers = request.headers;
        return HttpResponse.json({ ok: true });
      }),
    );
    await apiCall(publicThing);
    expect((headers as unknown as Headers).get('Authorization')).toBeNull();
    expect((headers as unknown as Headers).get(WORKSPACE_HEADER)).toBeNull();
  });

  it('manda el cuerpo como JSON y acepta un 204 sin cuerpo', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    let body: unknown;
    server.use(
      http.post(`${API_URL}/api/v1/things/`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ id: 1, name: 'Nuevo' }, { status: 201 });
      }),
      http.delete(`${API_URL}/api/v1/things/:id/`, () => new HttpResponse(null, { status: 204 })),
    );
    await expect(apiCall(createThing, { body: { name: 'Nuevo' } })).resolves.toEqual({ id: 1, name: 'Nuevo' });
    expect(body).toEqual({ name: 'Nuevo' });
    await expect(apiCall(deleteThing, { params: { id: '1' } })).resolves.toBeUndefined();
  });

  it('ante un 401 renueva el token una sola vez y repite el pedido', async () => {
    setTokens({ access: 'vencido', refresh: 'refresh-1' });
    let refreshes = 0;
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, ({ request, params }) =>
        request.headers.get('Authorization') === 'Bearer nuevo'
          ? HttpResponse.json({ id: Number(params.id), name: 'Ok' })
          : HttpResponse.json({ detail: 'Token vencido', code: 'token_not_valid' }, { status: 401 }),
      ),
      http.post(`${API_URL}/api/token/refresh/`, async ({ request }) => {
        refreshes += 1;
        expect(await request.json()).toEqual({ refresh: 'refresh-1' });
        return HttpResponse.json({ access: 'nuevo', refresh: 'refresh-2' });
      }),
    );
    // Two requests fail at once: they share one refresh.
    const [a, b] = await Promise.all([apiCall(getThing, { params: { id: '1' } }), apiCall(getThing, { params: { id: '2' } })]);
    expect([a.id, b.id]).toEqual([1, 2]);
    expect(refreshes).toBe(1);
    // The backend rotates refresh tokens: the new pair replaces the stored one.
    expect(getTokens()).toEqual({ access: 'nuevo', refresh: 'refresh-2' });
  });

  it('si no se puede renovar, avisa que la sesión venció', async () => {
    setTokens({ access: 'vencido', refresh: 'refresh-vencido' });
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, () => HttpResponse.json({ detail: 'Token vencido' }, { status: 401 })),
      http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ detail: 'Token is invalid or expired' }, { status: 401 })),
    );
    await expect(apiCall(getThing, { params: { id: '1' } })).rejects.toBeInstanceOf(SessionExpiredError);
    expect(expired).toHaveBeenCalledTimes(1);
    off();
  });

  it('si no se puede renovar porque la sesión venció, lo avisa como vencida', async () => {
    setTokens({ access: 'vencido', refresh: 'refresh-vencido' });
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, () => HttpResponse.json({ detail: 'Token is invalid or expired', code: 'token_not_valid' }, { status: 401 })),
      http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ detail: 'Token is invalid or expired', code: 'token_not_valid' }, { status: 401 })),
    );
    const error = await apiCall(getThing, { params: { id: '1' } }).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(SessionExpiredError);
    expect((error as SessionExpiredError).reason).toBe('expired');
    expect(expired).toHaveBeenCalledWith('expired');
    off();
  });

  it('un 401 `session_revoked` avisa que la sesión se cerró en todos los dispositivos, no que venció', async () => {
    // What another device gets after «cerrar sesión en todos los dispositivos»: the access token names
    // the reason. Even if the refresh then only says it is not valid, the reason already given stays.
    setTokens({ access: 'revocado', refresh: 'refresh-revocado' });
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, () =>
        HttpResponse.json({ detail: 'La sesión se cerró en todos los dispositivos. Iniciá sesión de nuevo.', code: 'session_revoked' }, { status: 401 }),
      ),
      http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ detail: 'Token is blacklisted', code: 'token_not_valid' }, { status: 401 })),
    );
    const error = await apiCall(getThing, { params: { id: '1' } }).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(SessionExpiredError);
    expect((error as SessionExpiredError).reason).toBe('revoked');
    expect(expired).toHaveBeenCalledTimes(1);
    expect(expired).toHaveBeenCalledWith('revoked');
    // Another request of the same dead session that did not learn why does not change what was said.
    notifySessionExpired();
    expect(expired).toHaveBeenLastCalledWith('revoked');
    // A new session starts clean.
    setTokens({ access: 'nuevo', refresh: 'refresh-nuevo' });
    notifySessionExpired();
    expect(expired).toHaveBeenLastCalledWith('expired');
    off();
  });

  it('también cuando el que dice `session_revoked` es el refresh (el token de acceso ya había vencido)', async () => {
    setTokens({ access: 'vencido', refresh: 'refresh-revocado' });
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, () => HttpResponse.json({ detail: 'Token is invalid or expired', code: 'token_not_valid' }, { status: 401 })),
      http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ detail: 'La sesión se cerró en todos los dispositivos.', code: 'session_revoked' }, { status: 401 })),
    );
    await expect(apiCall(getThing, { params: { id: '1' } })).rejects.toBeInstanceOf(SessionExpiredError);
    expect(expired).toHaveBeenCalledWith('revoked');
    off();
  });

  it('con la respuesta real del refresh de una sesión cerrada en todos los dispositivos dice eso, también al subir un archivo', async () => {
    // back/apps/users/tests/test_auth_contract_fixture.py builds this body: the refresh that revoke_sessions blacklisted.
    const revoked = real.errors.refresh_401_session_revoked;
    expect(revoked.body.code).toBe('session_revoked');
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    let uploads = 0;
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, () => HttpResponse.json({ detail: 'Token is invalid or expired', code: 'token_not_valid' }, { status: 401 })),
      http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json(revoked.body, { status: revoked.status })),
      http.post(`${API_URL}/api/v1/uploads/`, () => {
        uploads += 1;
        return HttpResponse.json({ ok: true });
      }),
    );

    setTokens({ access: 'vencido', refresh: 'refresh-revocado' });
    const error = await apiCall(getThing, { params: { id: '1' } }).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(SessionExpiredError);
    expect((error as SessionExpiredError).reason).toBe('revoked');
    expect(expired).toHaveBeenLastCalledWith('revoked');

    // An upload renews the token before it starts: that refresh is the one that answers, and nothing is sent.
    setTokens({ access: 'vencido', refresh: 'refresh-revocado' });
    const upload = await apiCall(uploadThing, { body: { title: 'x' } }).catch((reason: unknown) => reason);
    expect((upload as SessionExpiredError).reason).toBe('revoked');
    expect(expired).toHaveBeenLastCalledWith('revoked');
    expect(uploads).toBe(0);
    off();
  });

  it('una respuesta que no cumple el esquema es un ContractError', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    server.use(http.get(`${API_URL}/api/v1/things/:id/`, () => HttpResponse.json({ id: 'no-es-numero' })));
    const error = await apiCall(getThing, { params: { id: '1' } }).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ContractError);
    const contractError = error as ContractError;
    expect(contractError.endpointId).toBe('test.thing');
    expect(contractError.direction).toBe('response');
    expect(contractError.issues.map((issue) => issue.path.join('.'))).toEqual(['id', 'name']);
  });

  it('un cuerpo que no cumple el esquema no llega a salir', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    const error = await apiCall(createThing, { body: { name: '' } }).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ContractError);
    expect((error as ContractError).direction).toBe('request');
  });

  it('convierte los errores del backend en ApiError', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    server.use(
      http.post(`${API_URL}/api/v1/things/`, () => HttpResponse.json({ error: 'Saldo insuficiente', required: '1966.50' }, { status: 402 })),
    );
    const insufficient = (await apiCall(createThing, { body: { name: 'x' } }).catch((reason: unknown) => reason)) as ApiError;
    expect(insufficient).toBeInstanceOf(ApiError);
    expect(insufficient.isInsufficientFunds).toBe(true);
    expect(insufficient.requiredTokens).toBe(1966.5);
    expect(insufficient.message).toBe('Saldo insuficiente');

    server.use(
      http.post(`${API_URL}/api/v1/things/`, () =>
        HttpResponse.json({ name: ['Este campo no puede estar en blanco.'], source_file: [{ message: 'Muy largo', code: 'too_long' }] }, { status: 400 }),
      ),
    );
    const validation = (await apiCall(createThing, { body: { name: 'x' } }).catch((reason: unknown) => reason)) as ApiError;
    expect(validation.status).toBe(400);
    expect(validation.fieldErrors).toEqual({ name: ['Este campo no puede estar en blanco.'], source_file: ['Muy largo'] });
    expect(validation.fieldCodes).toEqual({ source_file: 'too_long' });

    server.use(
      http.post(`${API_URL}/api/v1/things/`, () => HttpResponse.json({ error: "Your plan 'Gratis' does not support 4K rendering." }, { status: 403 })),
    );
    const plan = (await apiCall(createThing, { body: { name: 'x' } }).catch((reason: unknown) => reason)) as ApiError;
    expect(plan.isPlanLimit).toBe(true);
  });

  it('sin respuesta de la red es un NetworkError', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    server.use(http.get(`${API_URL}/api/v1/things/:id/`, () => HttpResponse.error()));
    await expect(apiCall(getThing, { params: { id: '1' } })).rejects.toBeInstanceOf(NetworkError);
  });
});

describe('tiempo de espera', () => {
  /** An answer that never comes, until the test ends. */
  const hang = () => new Promise<never>(() => undefined);
  const slowThing = defineEndpoint({ ...getThing, id: 'test.slowThing', path: '/api/v1/slow/:id/', timeoutMs: 25 });
  const patientThing = defineEndpoint({ ...getThing, id: 'test.patientThing', path: '/api/v1/patient/:id/', timeoutMs: false });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('un pedido que queda colgado termina como error de red a los 30 s, y se reintenta como cualquier otro', async () => {
    expect(REQUEST_TIMEOUT_MS).toBe(30_000);
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    server.use(http.get(`${API_URL}/api/v1/things/:id/`, hang));
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    let settled: unknown = 'pendiente';
    void apiCall(getThing, { params: { id: '1' } }).catch((reason: unknown) => (settled = reason));
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS - 100);
    expect(settled).toBe('pendiente');
    await vi.advanceTimersByTimeAsync(200);
    vi.useRealTimers();
    await vi.waitFor(() => expect(settled).toBeInstanceOf(NetworkError));
    const error = settled as NetworkError;
    expect(error.endpointId).toBe('test.thing');
    expect(error.cause).toBeInstanceOf(RequestTimeoutError);
    // Same treatment as "no answer from the network": one retry for a query, the same message on screen.
    expect(shouldRetry(0, error)).toBe(true);
    expect(errorMessage(error)).toBe(errorMessage(new NetworkError('test.thing')));
  });

  it('un endpoint puede pedir otro tiempo, o ninguno; y una llamada puede cambiarlo', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => (release = resolve));
    server.use(
      http.get(`${API_URL}/api/v1/slow/:id/`, hang),
      http.get(`${API_URL}/api/v1/things/:id/`, hang),
      http.get(`${API_URL}/api/v1/patient/:id/`, async () => {
        await held;
        return HttpResponse.json({ id: 1, name: 'tarde pero llega' });
      }),
    );
    const slow = (await apiCall(slowThing, { params: { id: '1' } }).catch((reason: unknown) => reason)) as NetworkError;
    expect(slow).toBeInstanceOf(NetworkError);
    expect((slow.cause as RequestTimeoutError).timeoutMs).toBe(25);

    const perCall = (await apiCall(getThing, { params: { id: '1' }, timeoutMs: 20 }).catch((reason: unknown) => reason)) as NetworkError;
    expect((perCall.cause as RequestTimeoutError).timeoutMs).toBe(20);

    const patient = apiCall(patientThing, { params: { id: '1' } });
    await new Promise((resolve) => setTimeout(resolve, 80));
    release();
    await expect(patient).resolves.toEqual({ id: 1, name: 'tarde pero llega' });
  });

  it('una respuesta que empieza y se queda a mitad del cuerpo también vence', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    server.use(
      http.get(
        `${API_URL}/api/v1/slow/:id/`,
        () =>
          new HttpResponse(
            new ReadableStream({
              start(controller) {
                controller.enqueue(new TextEncoder().encode('{"id": 1, '));
              },
            }),
            { headers: { 'Content-Type': 'application/json' } },
          ),
      ),
    );
    const error = (await apiCall(slowThing, { params: { id: '1' } }).catch((reason: unknown) => reason)) as NetworkError;
    expect(error).toBeInstanceOf(NetworkError);
    expect(error.cause).toBeInstanceOf(RequestTimeoutError);
  });

  it('cancelar desde la pantalla sigue siendo una cancelación, no un error de red', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    server.use(http.get(`${API_URL}/api/v1/things/:id/`, hang));
    const controller = new AbortController();
    const call = apiCall(getThing, { params: { id: '1' }, signal: controller.signal }).catch((reason: unknown) => reason);
    setTimeout(() => controller.abort(), 10);
    const error = (await call) as DOMException;
    expect(error).not.toBeInstanceOf(NetworkError);
    expect(error.name).toBe('AbortError');
  });

  it('si la renovación del token queda colgada, el pedido no espera para siempre', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, () => HttpResponse.json({ detail: 'vencido' }, { status: 401 })),
      http.post(`${API_URL}/api/token/refresh/`, hang),
    );
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    let settled: unknown = 'pendiente';
    void apiCall(getThing, { params: { id: '1' } }).catch((reason: unknown) => (settled = reason));
    await vi.waitFor(() => expect(vi.getTimerCount()).toBeGreaterThan(0));
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 100);
    vi.useRealTimers();
    await vi.waitFor(() => expect(settled).toBeInstanceOf(NetworkError));
    // A passing problem: the session is still there.
    expect(isSessionExpired()).toBe(false);
  });

  it('en una subida el reloj arranca recién cuando el archivo terminó de salir', async () => {
    setTokens({ access: jwt(3600, 'vigente'), refresh: 'refresh-1' });
    server.use(http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ access: jwt(3600, 'nuevo'), refresh: 'refresh-2' })));
    /** A browser that uploads as slowly as the test says. */
    const sent: FakeXhr[] = [];
    class FakeXhr {
      upload: { onprogress?: (event: unknown) => void; onload?: () => void; onloadstart?: () => void } = {};
      status = 0;
      responseText = '';
      responseType = '';
      onload?: () => void;
      onerror?: () => void;
      onabort?: () => void;
      aborted = false;
      open() {}
      setRequestHeader() {}
      getResponseHeader() {
        return 'application/json';
      }
      send() {
        sent.push(this);
      }
      abort() {
        this.aborted = true;
        this.onabort?.();
      }
    }
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    const progress = vi.fn();
    let settled: unknown = 'pendiente';
    void apiCall(uploadThing, { body: { title: 'x' }, onUploadProgress: progress, timeoutMs: 1000 }).catch((reason: unknown) => (settled = reason));
    await vi.waitFor(() => expect(sent).toHaveLength(1));
    const xhr = sent[0] as FakeXhr;

    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    // Ten times the timeout uploading: nothing cuts it.
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 10, total: 100 });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(progress).toHaveBeenCalledWith(10, 100);
    expect(settled).toBe('pendiente');
    expect(xhr.aborted).toBe(false);

    // The file is out, and the server never answers.
    xhr.upload.onload?.();
    await vi.advanceTimersByTimeAsync(900);
    expect(settled).toBe('pendiente');
    await vi.advanceTimersByTimeAsync(200);
    vi.useRealTimers();
    await vi.waitFor(() => expect(settled).toBeInstanceOf(NetworkError));
    expect(xhr.aborted).toBe(true);
    expect((settled as NetworkError).cause).toBeInstanceOf(RequestTimeoutError);
  });
});

describe('un pedido que cambia algo y se queda sin respuesta', () => {
  const hang = () => new Promise<never>(() => undefined);

  it('crear un proyecto espera lo que el servidor puede tardar después de la subida, no los 30 s', () => {
    // ffprobe (hasta 60 s), mover el video al storage, calcular el precio y reservar.
    expect(createProject.timeoutMs).toBe(SLOW_REQUEST_TIMEOUT_MS);
    expect(createProjectFromPrompt.timeoutMs).toBe(SLOW_REQUEST_TIMEOUT_MS);
    expect(SLOW_REQUEST_TIMEOUT_MS).toBeGreaterThanOrEqual(180_000);
    // The other requests that wait on work done in the request itself.
    for (const endpoint of [createBrandKit, updateBrandKit, duplicateBrandKit, downloadAllClips, exportTransactions]) {
      expect(endpoint.timeoutMs, endpoint.id).toBeGreaterThan(REQUEST_TIMEOUT_MS);
    }
  });

  it('un POST que vence no es «no pudimos conectar»: puede que se haya hecho, y se distingue de un error de red común', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    server.use(http.post(`${API_URL}/api/v1/things/`, hang), http.get(`${API_URL}/api/v1/things/:id/`, hang));
    const write = (await apiCall(createThing, { body: { name: 'x' }, timeoutMs: 20 }).catch((reason: unknown) => reason)) as NetworkError;
    expect(write).toBeInstanceOf(NetworkError);
    expect(write.timedOut).toBe(true);
    expect(write.outcomeUnknown).toBe(true);
    expect(isOutcomeUnknown(write)).toBe(true);
    expect(errorMessage(write)).toBe('No recibimos la respuesta. Puede que se haya hecho igual: revisá antes de probar de nuevo.');

    // A read that times out changed nothing.
    const read = (await apiCall(getThing, { params: { id: '1' }, timeoutMs: 20 }).catch((reason: unknown) => reason)) as NetworkError;
    expect(read.timedOut).toBe(true);
    expect(read.outcomeUnknown).toBe(false);
    expect(errorMessage(read)).toBe('No pudimos conectar');

    // A write that never got to the server did not happen.
    server.use(http.post(`${API_URL}/api/v1/things/`, () => HttpResponse.error()));
    const refused = (await apiCall(createThing, { body: { name: 'x' } }).catch((reason: unknown) => reason)) as NetworkError;
    expect(refused.outcomeUnknown).toBe(false);
    expect(errorMessage(refused)).toBe('No pudimos conectar');
  });

  it('cuando una acción queda sin respuesta, la app vuelve a leer lo que pudo haber cambiado', async () => {
    const client = createQueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const run = (error: unknown) =>
      new MutationObserver(client, { mutationFn: () => Promise.reject(error) }).mutate().catch(() => undefined);
    await run(new NetworkError('projects.create', new TypeError('Failed to fetch'), 'POST'));
    expect(invalidate).not.toHaveBeenCalled();
    await run(new NetworkError('projects.create', new RequestTimeoutError(180_000), 'POST'));
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it('una subida que deja de avanzar se corta al minuto, como error de red común (el archivo no llegó entero)', async () => {
    setTokens({ access: jwt(3600, 'vigente'), refresh: 'refresh-1' });
    server.use(http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ access: jwt(3600, 'nuevo'), refresh: 'refresh-2' })));
    const sent: StalledXhr[] = [];
    class StalledXhr {
      upload: { onprogress?: (event: unknown) => void; onload?: () => void; onloadstart?: () => void } = {};
      status = 0;
      responseText = '';
      responseType = '';
      onload?: () => void;
      onerror?: () => void;
      onabort?: () => void;
      aborted = false;
      open() {}
      setRequestHeader() {}
      getResponseHeader() {
        return 'application/json';
      }
      send() {
        sent.push(this);
        this.upload.onloadstart?.();
      }
      abort() {
        this.aborted = true;
        this.onabort?.();
      }
    }
    vi.stubGlobal('XMLHttpRequest', StalledXhr);
    try {
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
      let settled: unknown = 'pendiente';
      void apiCall(uploadThing, { body: { title: 'x' }, onUploadProgress: () => undefined }).catch((reason: unknown) => (settled = reason));
      await vi.waitFor(() => expect(sent).toHaveLength(1));
      const xhr = sent[0] as StalledXhr;
      // Slow but moving: five minutes of it are fine.
      for (let minute = 0; minute < 6; minute += 1) {
        await vi.advanceTimersByTimeAsync(UPLOAD_STALL_TIMEOUT_MS - 1000);
        xhr.upload.onprogress?.({ lengthComputable: true, loaded: minute + 1, total: 100 });
      }
      expect(settled).toBe('pendiente');
      // Then nothing at all for a minute.
      await vi.advanceTimersByTimeAsync(UPLOAD_STALL_TIMEOUT_MS + 10);
      vi.useRealTimers();
      await vi.waitFor(() => expect(settled).toBeInstanceOf(NetworkError));
      expect(xhr.aborted).toBe(true);
      const error = settled as NetworkError;
      expect(error.cause).toBeInstanceOf(UploadStalledError);
      expect(error.outcomeUnknown).toBe(false);
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });
});

describe('sesión', () => {
  it('un endpoint opcional manda el token y lo renueva ante un 401', async () => {
    setTokens({ access: 'vencido', refresh: 'refresh-1' });
    server.use(
      http.post(`${API_URL}/api/v1/optional/`, ({ request }) => {
        const header = request.headers.get('Authorization');
        if (header === 'Bearer vencido') return HttpResponse.json({ detail: 'Token vencido' }, { status: 401 });
        return HttpResponse.json({ user: header ? 'lucia' : null });
      }),
      http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ access: 'nuevo', refresh: 'refresh-2' })),
    );
    await expect(apiCall(optionalThing, { body: {} })).resolves.toEqual({ user: 'lucia' });
    // Without a session it goes out with no token at all.
    setTokens(null);
    await expect(apiCall(optionalThing, { body: {} })).resolves.toEqual({ user: null });
  });

  it('un endpoint opcional con la sesión muerta sigue como público, sin abrir «sesión vencida»', async () => {
    setTokens({ access: 'vencido', refresh: 'refresh-vencido' });
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    server.use(
      http.post(`${API_URL}/api/v1/optional/`, ({ request }) =>
        request.headers.get('Authorization') ? HttpResponse.json({ detail: 'Token vencido' }, { status: 401 }) : HttpResponse.json({ user: null }),
      ),
      http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ detail: 'Token is invalid or expired' }, { status: 401 })),
    );
    await expect(apiCall(optionalThing, { body: {} })).resolves.toEqual({ user: null });
    expect(expired).not.toHaveBeenCalled();
    off();
  });

  it('un 500 o un 429 al renovar no es una sesión vencida', async () => {
    setTokens({ access: 'vencido', refresh: 'refresh-1' });
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    for (const status of [500, 429]) {
      server.use(
        http.get(`${API_URL}/api/v1/things/:id/`, () => HttpResponse.json({ detail: 'Token vencido' }, { status: 401 })),
        http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ detail: 'x' }, { status })),
      );
      const error = await apiCall(getThing, { params: { id: '1' } }).catch((reason: unknown) => reason);
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(status);
    }
    expect(expired).not.toHaveBeenCalled();
    expect(isSessionExpired()).toBe(false);
    expect(getTokens()).toEqual({ access: 'vencido', refresh: 'refresh-1' });
    off();
  });

  it('con la sesión vencida no sale nada más hasta que haya tokens nuevos', async () => {
    setTokens({ access: 'vencido', refresh: 'refresh-vencido' });
    let calls = 0;
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, ({ request }) => {
        calls += 1;
        return request.headers.get('Authorization') === 'Bearer nuevo'
          ? HttpResponse.json({ id: 1, name: 'Ok' })
          : HttpResponse.json({ detail: 'Token vencido' }, { status: 401 });
      }),
      http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ detail: 'Token is invalid or expired' }, { status: 401 })),
    );
    await expect(apiCall(getThing, { params: { id: '1' } })).rejects.toBeInstanceOf(SessionExpiredError);
    expect(isSessionExpired()).toBe(true);
    const before = calls;
    await expect(apiCall(getThing, { params: { id: '1' } })).rejects.toBeInstanceOf(SessionExpiredError);
    expect(calls).toBe(before);
    // Signing in again (dialog D9) ends the expired state.
    setTokens({ access: 'nuevo', refresh: 'refresh-2' });
    await expect(apiCall(getThing, { params: { id: '1' } })).resolves.toEqual({ id: 1, name: 'Ok' });
  });

  it('si otra pestaña ya renovó, usa su token sin gastar otro refresh', async () => {
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    let refreshes = 0;
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, ({ request }) => {
        if (request.headers.get('Authorization') === 'Bearer viejo') {
          // While this request was in flight, another tab stored a new pair.
          setTokens({ access: 'de-otra-pestaña', refresh: 'refresh-2' });
          return HttpResponse.json({ detail: 'Token vencido' }, { status: 401 });
        }
        return HttpResponse.json({ id: 1, name: 'Ok' });
      }),
      http.post(`${API_URL}/api/token/refresh/`, () => {
        refreshes += 1;
        return HttpResponse.json({ detail: 'rotado' }, { status: 401 });
      }),
    );
    await expect(apiCall(getThing, { params: { id: '1' } })).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(refreshes).toBe(0);
  });

  it('una subida siempre sale con un token recién renovado', async () => {
    const almost = jwt(900, 'con-tiempo');
    const fresh = jwt(1500, 'nuevo');
    setTokens({ access: almost, refresh: 'refresh-1' });
    let used: string | null = null;
    server.use(
      http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ access: fresh, refresh: 'refresh-2' })),
      http.post(`${API_URL}/api/v1/uploads/`, ({ request }) => {
        used = request.headers.get('Authorization');
        return HttpResponse.json({ ok: true });
      }),
    );
    await apiCall(uploadThing, { body: { title: 'x' } });
    expect(used).toBe(`Bearer ${fresh}`);
  });

  it('si la renovación previa a una subida falla por algo pasajero, la subida sale igual', async () => {
    setTokens({ access: 'actual', refresh: 'refresh-1' });
    let used: string | null = null;
    server.use(
      http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ detail: 'caído' }, { status: 500 })),
      http.post(`${API_URL}/api/v1/uploads/`, ({ request }) => {
        used = request.headers.get('Authorization');
        return HttpResponse.json({ ok: true });
      }),
    );
    await apiCall(uploadThing, { body: { title: 'x' } });
    expect(used).toBe('Bearer actual');
    expect(isSessionExpired()).toBe(false);
  });

  it('si otra pestaña vuelve a iniciar sesión, esta deja de estar vencida', async () => {
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, ({ request }) =>
        request.headers.get('Authorization') === 'Bearer de-otra-pestaña'
          ? HttpResponse.json({ id: 1, name: 'Ok' })
          : HttpResponse.json({ detail: 'Token vencido' }, { status: 401 }),
      ),
      http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ detail: 'Token is invalid or expired' }, { status: 401 })),
    );
    const unsubscribe = subscribeSession(() => undefined);
    await expect(apiCall(getThing, { params: { id: '1' } })).rejects.toBeInstanceOf(SessionExpiredError);
    expect(isSessionExpired()).toBe(true);
    // What the other tab does: it writes the new pair, and this one hears the `storage` event.
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ access: 'de-otra-pestaña', refresh: 'refresh-9' }));
    window.dispatchEvent(new StorageEvent('storage', { key: AUTH_STORAGE_KEY }));
    expect(isSessionExpired()).toBe(false);
    await expect(apiCall(getThing, { params: { id: '1' } })).resolves.toEqual({ id: 1, name: 'Ok' });
    unsubscribe();
  });

  it('cerrar sesión borra también el workspace activo', () => {
    setTokens({ access: 'a', refresh: 'r' });
    setActiveWorkspaceId('ws-1');
    clearSession();
    expect(getTokens()).toBeNull();
    expect(getActiveWorkspaceId()).toBeNull();
  });

  it('un 503 avisa mantenimiento y la respuesta siguiente lo levanta', async () => {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    const seen: boolean[] = [];
    const off = onMaintenance((active) => seen.push(active));
    server.use(http.get(`${API_URL}/api/v1/things/:id/`, () => HttpResponse.json({ detail: 'Service Unavailable' }, { status: 503 })));
    await apiCall(getThing, { params: { id: '1' } }).catch(() => undefined);
    server.use(http.get(`${API_URL}/api/v1/things/:id/`, () => HttpResponse.json({ id: 1, name: 'Ok' })));
    await apiCall(getThing, { params: { id: '1' } });
    expect(seen).toEqual([true, false]);
    off();
  });
});

describe('503 y 502 que no son mantenimiento', () => {
  /** What the maintenance listener heard while the endpoint answered `status` with `body`. */
  async function heard(status: number, body: unknown): Promise<{ seen: boolean[]; error: unknown }> {
    setTokens({ access: 'acceso-1', refresh: 'refresh-1' });
    const seen: boolean[] = [];
    const off = onMaintenance((active) => seen.push(active));
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, () =>
        typeof body === 'string' ? new HttpResponse(body, { status, headers: { 'Content-Type': 'text/html' } }) : HttpResponse.json(body as Record<string, unknown>, { status }),
      ),
    );
    const error = await apiCall(getThing, { params: { id: '1' } }).catch((caught: unknown) => caught);
    off();
    return { seen, error };
  }

  it('el 503 del middleware de mantenimiento (`{detail}`) pasa a mantenimiento', async () => {
    // back/apps/core/maintenance.py · MAINTENANCE_DETAIL
    const { seen } = await heard(503, { detail: 'Estamos haciendo mantenimiento. Volvé en un rato.' });
    expect(seen).toEqual([true]);
  });

  it('un 503 que no es JSON (un proxy con el backend caído) pasa a mantenimiento', async () => {
    const { seen } = await heard(503, '<html><body>503 Service Unavailable</body></html>');
    expect(seen).toEqual([true]);
  });

  it('un 503 con `code` es un error de ese pedido: no pasa a mantenimiento y llega como ApiError', async () => {
    const { seen, error } = await heard(503, { error: 'No pudimos leer las redes conectadas. Probá de nuevo en unos minutos.', code: 'provider_not_configured' });
    expect(seen).toEqual([false]);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).code).toBe('provider_not_configured');
    expect((error as ApiError).message).toBe('No pudimos leer las redes conectadas. Probá de nuevo en unos minutos.');
  });

  it('un 503 con `error` y sin `code` tampoco es mantenimiento', async () => {
    const { seen } = await heard(503, { error: 'No pudimos volver a procesar el proyecto. Probá de nuevo en unos minutos.' });
    expect(seen).toEqual([false]);
  });

  it('un 502 con `code` (el proveedor de redes no respondió) es un error normal', async () => {
    const { seen, error } = await heard(502, { error: 'No pudimos leer las redes conectadas. Probá de nuevo en unos minutos.', code: 'provider_unavailable' });
    expect(seen).toEqual([false]);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(502);
    expect((error as ApiError).code).toBe('provider_unavailable');
  });
});

describe('toFormData', () => {
  it('arma el formulario como lo espera DRF', () => {
    const file = new File(['x'], 'clase.mp4', { type: 'video/mp4' });
    const form = toFormData({ title: 'Clase', use_seo: true, auto_render_bypass: false, max_clips: 8, source_file: file, brand_kit: undefined, video_url: null });
    expect(form.get('title')).toBe('Clase');
    expect(form.get('use_seo')).toBe('true');
    expect(form.get('auto_render_bypass')).toBe('false');
    expect(form.get('max_clips')).toBe('8');
    expect((form.get('source_file') as File).name).toBe('clase.mp4');
    expect(form.has('brand_kit')).toBe(false);
    expect(form.has('video_url')).toBe(false);
  });
});

/** Another tab of the same browser stores a pair: it writes localStorage, without going through this tab's `setTokens`. */
function otherTabStores(pair: { access: string; refresh: string } | null): void {
  if (pair) window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(pair));
  else window.localStorage.removeItem(AUTH_STORAGE_KEY);
}

/** Web Locks as the browser gives them: one holder per name, the rest wait in order. Shared by every "tab" of the test. */
function fakeLocks() {
  const queues = new Map<string, Promise<unknown>>();
  const names: string[] = [];
  const signals: Array<AbortSignal | undefined> = [];
  /** Holders and waiters per name: with any, the lock is not available. */
  const busy = new Map<string, number>();
  const request = <T,>(name: string, options: { signal?: AbortSignal; ifAvailable?: boolean }, callback: (lock: object | null) => Promise<T>): Promise<T> => {
    // As the browser: `ifAvailable` never waits; with the lock taken the callback runs at once, without it.
    if (options.ifAvailable && (busy.get(name) ?? 0) > 0) return Promise.resolve().then(() => callback(null));
    // The renewal asks twice when it has to wait (first with `ifAvailable`): the name is noted once it queues.
    names.push(name);
    signals.push(options.signal);
    busy.set(name, (busy.get(name) ?? 0) + 1);
    const run = () => callback({ name }).finally(() => busy.set(name, (busy.get(name) ?? 1) - 1));
    if (options.signal) {
      // As the browser: a request still waiting when its signal aborts is rejected.
      let granted = false;
      const previous = queues.get(name) ?? Promise.resolve();
      return new Promise<T>((resolve, reject) => {
        options.signal?.addEventListener('abort', () => {
          if (!granted) reject(new DOMException('Aborted', 'AbortError'));
        });
        const mine = previous.then(() => {
          if (options.signal?.aborted) {
            busy.set(name, (busy.get(name) ?? 1) - 1);
            return undefined;
          }
          granted = true;
          return run().then(resolve, reject);
        });
        queues.set(name, mine);
      });
    }
    const previous = queues.get(name) ?? Promise.resolve();
    const mine = previous.then(run, run);
    queues.set(name, mine.catch(() => undefined));
    return mine;
  };
  return { locks: { request }, names, signals };
}

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('renovar el token con varias pestañas', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    clearSession();
  });

  const thingNeedsToken = (valid: string) =>
    http.get(`${API_URL}/api/v1/things/:id/`, ({ request }) =>
      request.headers.get('Authorization') === `Bearer ${valid}` ? HttpResponse.json({ id: 1, name: 'Ok' }) : HttpResponse.json({ detail: 'Token vencido' }, { status: 401 }),
    );

  it('la renovación toma siempre el mismo lock del navegador', async () => {
    const { locks, names } = fakeLocks();
    vi.stubGlobal('navigator', { ...navigator, locks });
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    server.use(thingNeedsToken('nuevo'), http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ access: 'nuevo', refresh: 'refresh-2' })));
    await expect(apiCall(getThing, { params: { id: '1' } })).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(names).toEqual([REFRESH_LOCK_NAME]);
    expect(REFRESH_LOCK_NAME).toBe('oc.auth.refresh');
    expect(getTokens()).toEqual({ access: 'nuevo', refresh: 'refresh-2' });
  });

  it('si otra pestaña renovó mientras esperaba el lock, usa su par y no manda el refresh ya rotado', async () => {
    const { locks } = fakeLocks();
    vi.stubGlobal('navigator', { ...navigator, locks });
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    let refreshes = 0;
    server.use(
      thingNeedsToken('de-otra-pestaña'),
      // The backend blacklists a rotated refresh token: sending it again is what used to end the session.
      http.post(`${API_URL}/api/token/refresh/`, () => {
        refreshes += 1;
        return HttpResponse.json({ detail: 'Token is blacklisted', code: 'token_not_valid' }, { status: 401 });
      }),
    );
    // The other tab holds the lock while it renews.
    const otherTab = deferred();
    const held = locks.request(REFRESH_LOCK_NAME, {}, () => otherTab.promise);
    const call = apiCall(getThing, { params: { id: '1' } });
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(refreshes).toBe(0);
    otherTabStores({ access: 'de-otra-pestaña', refresh: 'refresh-2' });
    otherTab.resolve();
    await held;
    await expect(call).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(refreshes).toBe(0);
    expect(expired).not.toHaveBeenCalled();
    expect(isSessionExpired()).toBe(false);
    expect(getTokens()).toEqual({ access: 'de-otra-pestaña', refresh: 'refresh-2' });
    off();
  });

  it('si el par de la otra pestaña llega al storage después que el lock, lo espera y no manda el refresh ya rotado', async () => {
    const { locks } = fakeLocks();
    vi.stubGlobal('navigator', { ...navigator, locks });
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    const used: unknown[] = [];
    server.use(
      thingNeedsToken('de-otra-pestaña'),
      http.post(`${API_URL}/api/token/refresh/`, async ({ request }) => {
        used.push(((await request.json()) as { refresh: string }).refresh);
        return HttpResponse.json({ detail: 'Token is blacklisted', code: 'token_not_valid' }, { status: 401 });
      }),
    );
    const otherTab = deferred();
    const held = locks.request(REFRESH_LOCK_NAME, {}, () => otherTab.promise);
    const call = apiCall(getThing, { params: { id: '1' } });
    await new Promise((resolve) => setTimeout(resolve, 30));
    // The other tab stored its pair and let the lock go, but each tab reads its own copy of localStorage:
    // here the lock arrives first and the pair a moment later.
    otherTab.resolve();
    await held;
    setTimeout(() => otherTabStores({ access: 'de-otra-pestaña', refresh: 'refresh-2' }), 40);
    await expect(call).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(used).toEqual([]);
    expect(expired).not.toHaveBeenCalled();
    expect(getTokens()).toEqual({ access: 'de-otra-pestaña', refresh: 'refresh-2' });
    off();
  });

  it('si la otra pestaña soltó el lock sin renovar, pasada la espera renueva esta', async () => {
    const { locks } = fakeLocks();
    vi.stubGlobal('navigator', { ...navigator, locks });
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    const used: unknown[] = [];
    server.use(
      thingNeedsToken('nuevo'),
      http.post(`${API_URL}/api/token/refresh/`, async ({ request }) => {
        used.push(((await request.json()) as { refresh: string }).refresh);
        return HttpResponse.json({ access: 'nuevo', refresh: 'refresh-2' });
      }),
    );
    // The other tab's renewal failed (its network, a 503): it lets the lock go and stores nothing.
    const otherTab = deferred();
    const held = locks.request(REFRESH_LOCK_NAME, {}, () => otherTab.promise);
    const call = apiCall(getThing, { params: { id: '1' } });
    await new Promise((resolve) => setTimeout(resolve, 30));
    otherTab.resolve();
    await held;
    const released = Date.now();
    await expect(call).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(Date.now() - released).toBeGreaterThanOrEqual(REFRESH_HANDOVER_WAIT_MS - 20);
    expect(used).toEqual(['refresh-1']);
    expect(getTokens()).toEqual({ access: 'nuevo', refresh: 'refresh-2' });
  });

  // A regression guard, not a test of the fix: it passes without the handover wait too. It keeps that
  // wait from ever being paid by a tab that had nobody to wait for.
  it('guarda de regresión: con el lock libre no espera a nadie, renueva en el momento', async () => {
    const { locks } = fakeLocks();
    vi.stubGlobal('navigator', { ...navigator, locks });
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    server.use(thingNeedsToken('nuevo'), http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ access: 'nuevo', refresh: 'refresh-2' })));
    const started = Date.now();
    await expect(apiCall(getThing, { params: { id: '1' } })).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(Date.now() - started).toBeLessThan(REFRESH_HANDOVER_WAIT_MS);
  });

  it('dos pestañas que renuevan a la vez mandan un solo refresh', async () => {
    const { locks } = fakeLocks();
    vi.stubGlobal('navigator', { ...navigator, locks });
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    const used: unknown[] = [];
    server.use(
      thingNeedsToken('nuevo'),
      http.post(`${API_URL}/api/token/refresh/`, async ({ request }) => {
        const body = (await request.json()) as { refresh: string };
        used.push(body.refresh);
        if (used.length > 1) return HttpResponse.json({ detail: 'Token is blacklisted' }, { status: 401 });
        return HttpResponse.json({ access: 'nuevo', refresh: 'refresh-2' });
      }),
    );
    // A second copy of the client module is the other tab: its own memory, the same localStorage and the same locks.
    vi.resetModules();
    const otherTab = await import('./client');
    const [mine, theirs] = await Promise.all([apiCall(getThing, { params: { id: '1' } }), otherTab.apiCall(getThing, { params: { id: '1' } })]);
    expect(mine).toEqual({ id: 1, name: 'Ok' });
    expect(theirs).toEqual({ id: 1, name: 'Ok' });
    expect(used).toEqual(['refresh-1']);
    expect(isSessionExpired()).toBe(false);
  });

  it('sin Web Locks: si el refresh ya lo rotó otra pestaña, espera su par en vez de dar la sesión por vencida', async () => {
    vi.stubGlobal('navigator', { ...navigator, locks: undefined });
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    server.use(
      thingNeedsToken('de-otra-pestaña'),
      http.post(`${API_URL}/api/token/refresh/`, () => {
        // The other tab's answer is still on its way when this one is refused.
        setTimeout(() => otherTabStores({ access: 'de-otra-pestaña', refresh: 'refresh-2' }), 120);
        return HttpResponse.json({ detail: 'Token is blacklisted', code: 'token_not_valid' }, { status: 401 });
      }),
    );
    await expect(apiCall(getThing, { params: { id: '1' } })).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(expired).not.toHaveBeenCalled();
    off();
  });

  it('sin Web Locks y sin otra pestaña, un refresh rechazado sigue siendo una sesión vencida', async () => {
    vi.stubGlobal('navigator', { ...navigator, locks: undefined });
    setTokens({ access: 'viejo', refresh: 'refresh-vencido' });
    server.use(thingNeedsToken('ninguno'), http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ detail: 'Token is invalid or expired' }, { status: 401 })));
    await expect(apiCall(getThing, { params: { id: '1' } })).rejects.toBeInstanceOf(SessionExpiredError);
    expect(isSessionExpired()).toBe(true);
  });

  it('una pestaña suspendida con el lock no cuelga a las demás: pasado el plazo, renueva sin él', async () => {
    const { locks, signals } = fakeLocks();
    vi.stubGlobal('navigator', { ...navigator, locks });
    // The wait for the lock has a deadline; here it is due almost at once.
    const timeout = vi.spyOn(AbortSignal, 'timeout').mockImplementation(() => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), 40);
      return controller.signal;
    });
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    server.use(thingNeedsToken('nuevo'), http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ access: 'nuevo', refresh: 'refresh-2' })));
    // The other tab took the lock and never lets go.
    void locks.request(REFRESH_LOCK_NAME, {}, () => new Promise<void>(() => undefined));
    await expect(apiCall(getThing, { params: { id: '1' } })).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(timeout).toHaveBeenCalledWith(REFRESH_LOCK_WAIT_MS);
    expect(REFRESH_LOCK_WAIT_MS).toBeGreaterThan(REQUEST_TIMEOUT_MS);
    expect(signals.at(-1)).toBeInstanceOf(AbortSignal);
    expect(getTokens()).toEqual({ access: 'nuevo', refresh: 'refresh-2' });
    timeout.mockRestore();
  });

  it('con el lock: si un cambio de contraseña en otra pestaña llegó antes al servidor, espera su par en vez de dar la sesión por vencida', async () => {
    const { locks } = fakeLocks();
    vi.stubGlobal('navigator', { ...navigator, locks });
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    server.use(
      thingNeedsToken('del-cambio'),
      http.post(`${API_URL}/api/token/refresh/`, () => {
        // POST /me/password/ takes no lock: its new pair is still on its way to the other tab.
        setTimeout(() => otherTabStores({ access: 'del-cambio', refresh: 'refresh-del-cambio' }), 120);
        return HttpResponse.json({ detail: 'Token is blacklisted', code: 'token_not_valid' }, { status: 401 });
      }),
    );
    await expect(apiCall(getThing, { params: { id: '1' } })).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(expired).not.toHaveBeenCalled();
    off();
  });

  it('un evento de storage que llega tarde no descarta una renovación válida', async () => {
    // Tab A rotated refresh-1 into refresh-2. This tab read refresh-2 from storage before A's
    // `storage` event arrived, and renews with it; the event lands while the request is in flight.
    const unsubscribe = subscribeSession(() => undefined);
    otherTabStores({ access: 'de-a', refresh: 'refresh-2' });
    const refreshArrived = deferred();
    const release = deferred();
    server.use(
      thingNeedsToken('acceso-3'),
      http.post(`${API_URL}/api/token/refresh/`, async ({ request }) => {
        expect(await request.json()).toEqual({ refresh: 'refresh-2' });
        refreshArrived.resolve();
        await release.promise;
        return HttpResponse.json({ access: 'acceso-3', refresh: 'refresh-3' });
      }),
    );
    const call = apiCall(getThing, { params: { id: '1' } });
    await refreshArrived.promise;
    window.dispatchEvent(new StorageEvent('storage', { key: AUTH_STORAGE_KEY, oldValue: JSON.stringify({ access: 'viejo', refresh: 'refresh-1' }), newValue: JSON.stringify({ access: 'de-a', refresh: 'refresh-2' }) }));
    release.resolve();
    await expect(call).resolves.toEqual({ id: 1, name: 'Ok' });
    // refresh-2 is blacklisted now: keeping it would end the session at the next renewal.
    expect(getTokens()).toEqual({ access: 'acceso-3', refresh: 'refresh-3' });
    unsubscribe();
  });

  it('si el navegador no deja pedir el lock, renueva igual', async () => {
    vi.stubGlobal('navigator', { ...navigator, locks: { request: () => Promise.reject(new DOMException('no', 'SecurityError')) } });
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    server.use(thingNeedsToken('nuevo'), http.post(`${API_URL}/api/token/refresh/`, () => HttpResponse.json({ access: 'nuevo', refresh: 'refresh-2' })));
    await expect(apiCall(getThing, { params: { id: '1' } })).resolves.toEqual({ id: 1, name: 'Ok' });
  });
});

describe('un par nuevo anula la renovación en vuelo', () => {
  afterEach(() => clearSession());

  it('cambiar la contraseña mientras se renueva: el par del cambio queda, el de la renovación se descarta', async () => {
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    const refreshArrived = deferred();
    const release = deferred();
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, ({ request }) =>
        request.headers.get('Authorization') === 'Bearer del-cambio' ? HttpResponse.json({ id: 1, name: 'Ok' }) : HttpResponse.json({ detail: 'Token vencido' }, { status: 401 }),
      ),
      http.post(`${API_URL}/api/token/refresh/`, async () => {
        refreshArrived.resolve();
        await release.promise;
        // Rotated before the password changed: the server revoked this pair with the change.
        return HttpResponse.json({ access: 'revocado', refresh: 'refresh-revocado' });
      }),
    );
    const call = apiCall(getThing, { params: { id: '1' } });
    await refreshArrived.promise;
    // POST /me/password/ answered: useChangePassword stores the new pair.
    setTokens({ access: 'del-cambio', refresh: 'refresh-del-cambio' });
    release.resolve();
    // The request that was waiting for the renewal goes on with the pair that is valid.
    await expect(call).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(getTokens()).toEqual({ access: 'del-cambio', refresh: 'refresh-del-cambio' });
    expect(expired).not.toHaveBeenCalled();
    off();
  });

  it('también si la renovación tardía falla: no vence la sesión que el cambio dejó', async () => {
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    const refreshArrived = deferred();
    const release = deferred();
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, ({ request }) =>
        request.headers.get('Authorization') === 'Bearer del-cambio' ? HttpResponse.json({ id: 1, name: 'Ok' }) : HttpResponse.json({ detail: 'Token vencido' }, { status: 401 }),
      ),
      http.post(`${API_URL}/api/token/refresh/`, async () => {
        refreshArrived.resolve();
        await release.promise;
        return HttpResponse.json({ detail: 'Token is blacklisted' }, { status: 401 });
      }),
    );
    const call = apiCall(getThing, { params: { id: '1' } });
    await refreshArrived.promise;
    setTokens({ access: 'del-cambio', refresh: 'refresh-del-cambio' });
    release.resolve();
    await expect(call).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(isSessionExpired()).toBe(false);
    expect(getTokens()).toEqual({ access: 'del-cambio', refresh: 'refresh-del-cambio' });
  });

  it('un par guardado en esta pestaña con el mismo refresh también gana (solo la generación lo distingue)', async () => {
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    const refreshArrived = deferred();
    const release = deferred();
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, ({ request }) =>
        request.headers.get('Authorization') === 'Bearer guardado-después' ? HttpResponse.json({ id: 1, name: 'Ok' }) : HttpResponse.json({ detail: 'Token vencido' }, { status: 401 }),
      ),
      http.post(`${API_URL}/api/token/refresh/`, async () => {
        refreshArrived.resolve();
        await release.promise;
        return HttpResponse.json({ access: 'tarde', refresh: 'refresh-tarde' });
      }),
    );
    const call = apiCall(getThing, { params: { id: '1' } });
    await refreshArrived.promise;
    // Same refresh string, another access token: comparing the stored pair cannot tell it was replaced.
    setTokens({ access: 'guardado-después', refresh: 'refresh-1' });
    release.resolve();
    await expect(call).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(getTokens()).toEqual({ access: 'guardado-después', refresh: 'refresh-1' });
  });

  it('cerrar sesión mientras se renueva: la renovación no la revive', async () => {
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    const refreshArrived = deferred();
    const release = deferred();
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, () => HttpResponse.json({ detail: 'Token vencido' }, { status: 401 })),
      http.post(`${API_URL}/api/token/refresh/`, async () => {
        refreshArrived.resolve();
        await release.promise;
        return HttpResponse.json({ access: 'tarde', refresh: 'refresh-tarde' });
      }),
    );
    const call = apiCall(getThing, { params: { id: '1' } }).catch((reason: unknown) => reason);
    await refreshArrived.promise;
    clearSession();
    release.resolve();
    expect(await call).toBeInstanceOf(SessionExpiredError);
    expect(getTokens()).toBeNull();
  });

  it('un par que guardó otra pestaña mientras tanto también gana', async () => {
    setTokens({ access: 'viejo', refresh: 'refresh-1' });
    const refreshArrived = deferred();
    const release = deferred();
    server.use(
      http.get(`${API_URL}/api/v1/things/:id/`, ({ request }) =>
        request.headers.get('Authorization') === 'Bearer de-otra-pestaña' ? HttpResponse.json({ id: 1, name: 'Ok' }) : HttpResponse.json({ detail: 'Token vencido' }, { status: 401 }),
      ),
      http.post(`${API_URL}/api/token/refresh/`, async () => {
        refreshArrived.resolve();
        await release.promise;
        return HttpResponse.json({ access: 'revocado', refresh: 'refresh-revocado' });
      }),
    );
    const call = apiCall(getThing, { params: { id: '1' } });
    await refreshArrived.promise;
    otherTabStores({ access: 'de-otra-pestaña', refresh: 'refresh-de-otra' });
    release.resolve();
    await expect(call).resolves.toEqual({ id: 1, name: 'Ok' });
    expect(getTokens()).toEqual({ access: 'de-otra-pestaña', refresh: 'refresh-de-otra' });
  });
});
