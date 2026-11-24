import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { apiCall, toFormData } from './client';
import { API_URL, WORKSPACE_HEADER } from './config';
import { defineEndpoint } from './endpoint';
import { ApiError, ContractError, NetworkError, SessionExpiredError } from './errors';
import { getTokens, onSessionExpired, setActiveWorkspaceId, setTokens } from './session';

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
