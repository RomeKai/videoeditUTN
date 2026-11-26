// @vitest-environment node
import { http, passthrough } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiCall } from '../client';
import { dropStaleBridgeWorkspace, isMeBridged } from '../bridge';
import { API_URL, backendDefaultWorkspaceId, hasBackendDefaultPrefix, WORKSPACE_HEADER } from '../config';
import * as auth from '../contracts/auth';
import * as billing from '../contracts/billing';
import * as brandKits from '../contracts/brandkits';
import * as clips from '../contracts/clips';
import * as notifications from '../contracts/notifications';
import * as projects from '../contracts/projects';
import * as social from '../contracts/social';
import * as users from '../contracts/users';
import { ApiError, NetworkError, SessionExpiredError } from '../errors';
import { clearRouteOverrides, forceRouting, isBackendDefaultWorkspace, isOfWorkspace, setRouteOverride } from '../routing';
import { getActiveWorkspaceId, getTokens, setActiveWorkspaceId, setTokens } from '../session';
import { adoptClip, adoptUserFromToken, adoptWorkspace, readJwt, rememberLoginEmail } from './hybrid';
import { MOCK_HEADER } from './http';
import { PLAN_IDS, USER_IDS, WORKSPACE_IDS } from './seed';
import { server } from './server';
import { applyScenario, dropForeignSession, getScenarioId, loadDb, mockTokens, saveDb } from './state';

const REAL_USER = '4544e2e0-d826-449c-b6ac-3cfe1c5e357a';
const REAL_WORKSPACE = '8ab1487e-a3e8-4f71-8b1e-b4386aede26f';
const REAL_PROJECT = '1b05848d-9fbc-4021-8452-6710f1ac902d';
const REAL_CLIP = '84f2c518-5036-4665-8964-9fab0a9f6f84';
const BACKEND_DEFAULT_WORKSPACE_ID = backendDefaultWorkspaceId(REAL_USER);

/** An explicit list, so the tests do not depend on which endpoints the contract marks as existing today. */
const HYBRID = { mode: 'hybrid', real: 'projects.list,brandKits.list' } as const;

const base64url = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
/** A token shaped like simplejwt's: header.payload.signature, with `user_id` in the payload. */
function jwt(claims: Record<string, unknown> = {}, type: 'access' | 'refresh' = 'access'): string {
  const payload = { token_type: type, exp: Math.floor(Date.now() / 1000) + 1500, iat: Math.floor(Date.now() / 1000), jti: 'abc', user_id: REAL_USER, ...claims };
  return `${base64url({ alg: 'HS256', typ: 'JWT' })}.${base64url(payload)}.firma-que-el-mock-no-puede-verificar`;
}

interface BackendCall {
  method: string;
  path: string;
  authorization: string | null;
  workspace: string | null;
}

/** Stands in for the real backend: what MSW lets through ends up here (it is the fetch MSW found when it started). */
const backendCalls: BackendCall[] = [];
let backend: (request: Request) => Response | Promise<Response> = () => new Response('{}', { status: 404 });
const backendFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const request = new Request(input, init);
  backendCalls.push({
    method: request.method,
    path: new URL(request.url).pathname,
    authorization: request.headers.get('Authorization'),
    workspace: request.headers.get(WORKSPACE_HEADER),
  });
  return backend(request);
});

const originalFetch = globalThis.fetch;
beforeAll(() => {
  globalThis.fetch = backendFetch as typeof fetch;
  server.listen({ onUnhandledRequest: 'error' });
});
afterAll(() => {
  server.close();
  globalThis.fetch = originalFetch;
});

function signInReal(claims: Record<string, unknown> = {}) {
  setTokens({ access: jwt(claims), refresh: jwt(claims, 'refresh') });
  setActiveWorkspaceId(null);
}

beforeEach(() => {
  forceRouting(HYBRID);
  clearRouteOverrides();
  applyScenario('estudio-norte');
  rememberLoginEmail(null);
  backendCalls.length = 0;
  backend = () => new Response('{}', { status: 404 });
  signInReal();
});
afterEach(() => {
  forceRouting(null);
  server.resetHandlers();
});

const fail = async (promise: Promise<unknown>): Promise<ApiError> => {
  const error = await promise.catch((reason: unknown) => reason);
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
};

describe('puente del JWT', () => {
  it('lee user_id, email y exp del payload de un token de simplejwt', () => {
    expect(readJwt(jwt())).toMatchObject({ user_id: REAL_USER, email: null });
    expect(readJwt(jwt({ email: 'ana@real.test', exp: 42 }))).toEqual({ user_id: REAL_USER, email: 'ana@real.test', exp: 42 });
    // An empty user, or an expiry that is not a number (it would never expire), is not a session.
    expect(readJwt(jwt({ user_id: '' }))).toBeNull();
    expect(readJwt(jwt({ user_id: '  ' }))).toBeNull();
    expect(readJwt(jwt({ exp: 'mañana' }))).toBeNull();
    expect(readJwt(jwt({ exp: null }))).toBeNull();
    expect(readJwt(jwt({ exp: undefined }))).toBeNull();
    // Numeric ids (a backend with AutoField) are read as text.
    expect(readJwt(jwt({ user_id: 7 }))?.user_id).toBe('7');
    // Accents travel as UTF-8 inside base64url.
    expect(readJwt(jwt({ email: 'josé@real.test' }))?.email).toBe('josé@real.test');
  });

  it('no confunde con un JWT lo que no lo es', () => {
    expect(readJwt(null)).toBeNull();
    expect(readJwt('')).toBeNull();
    expect(readJwt(mockTokens(USER_IDS.lucia).access)).toBeNull();
    expect(readJwt('a.b.c')).toBeNull();
    expect(readJwt(`x.${base64url({ sub: 'sin user_id', exp: 99 })}.y`)).toBeNull();
    expect(readJwt(`x.${base64url('texto')}.y`)).toBeNull();
  });

  it('fuera de híbrido un JWT real no crea ningún usuario', () => {
    forceRouting({ mode: 'mock' });
    const db = loadDb();
    const before = db.users.length;
    expect(adoptUserFromToken(db, jwt(), Date.now())).toBeNull();
    expect(db.users).toHaveLength(before);
  });
});

describe('modo híbrido: ruteo de los pedidos', () => {
  it('lo real sale al backend sin tocar y lo simulado lo responde el mock, marcado', async () => {
    backend = (request) =>
      new URL(request.url).pathname === '/api/v1/brand-kits/'
        ? Response.json([
            { id: '93c89170-c470-406f-8a8f-600ffb6a9371', workspace: REAL_WORKSPACE, user: null, name: 'Kit del backend', description: '', primary_font: null, secondary_font: null, primary_color: '#FFFFFF', secondary_color: '#000000', accent_color: '#FFFF00', watermark_logo: null, intro_video: null, outro_video: null, is_default: false, created_at: '2026-11-26T12:22:04.754098Z' },
          ])
        : Response.json([]);
    const kits = await apiCall(brandKits.listBrandKits, {});
    expect(kits.map((kit) => kit.name)).toEqual(['Kit del backend']);
    const list = await apiCall(projects.listProjects, {});
    expect(list.results).toEqual([]);
    const wallet = await apiCall(billing.getWallet, {});
    expect(wallet.available_balance).toBe(4860);
    // Only the real endpoints reached the backend, with the real token.
    expect(backendCalls.map((call) => `${call.method} ${call.path}`)).toEqual(['GET /api/v1/brand-kits/', 'GET /api/v1/projects/']);
    expect(backendCalls.every((call) => call.authorization === `Bearer ${getTokens()?.access}`)).toBe(true);
  });

  it('las respuestas de un mock llevan X-OC-Mock; las del backend, no', async () => {
    backend = () => Response.json([]);
    const access = getTokens()?.access;
    // Plain fetch: the header is not visible through apiCall.
    const mocked = await fetch(`${API_URL}/api/v1/wallet/`, { headers: { Authorization: `Bearer ${access}` } });
    expect(mocked.headers.get(MOCK_HEADER)).toBe('billing.wallet');
    const real = await fetch(`${API_URL}/api/v1/brand-kits/`, { headers: { Authorization: `Bearer ${access}` } });
    expect(real.headers.get(MOCK_HEADER)).toBeNull();
  });

  it('el login siempre va al backend, y su email queda para el /me simulado', async () => {
    const tokens = { access: jwt(), refresh: jwt({}, 'refresh') };
    backend = () => Response.json(tokens);
    setTokens(null);
    await expect(apiCall(auth.login, { body: { email: 'hibrido@onecreator.test', password: 'x' } })).resolves.toEqual(tokens);
    expect(backendCalls).toEqual([{ method: 'POST', path: '/api/token/', authorization: null, workspace: null }]);
    setTokens(tokens);
    const me = await apiCall(users.getMe, {});
    expect(me).toMatchObject({ id: REAL_USER, email: 'hibrido@onecreator.test', name: 'hibrido' });
  });

  it('el cambio a mano de un dominio se aplica al pedido siguiente', async () => {
    backend = () => Response.json([]);
    setRouteOverride('brandKits', 'mock');
    await expect(apiCall(brandKits.listBrandKits, {})).resolves.toEqual([]);
    expect(backendCalls).toEqual([]);
    setRouteOverride('brandKits', null);
    await apiCall(brandKits.listBrandKits, {});
    expect(backendCalls.map((call) => call.path)).toEqual(['/api/v1/brand-kits/']);
  });

  it('con el backend caído, un pedido real falla como error de red (los estados de «No pudimos conectar»), no como error de MSW', async () => {
    backend = () => {
      throw new TypeError('fetch failed');
    };
    const error = await apiCall(projects.listProjects, {}).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(NetworkError);
    expect((error as NetworkError).endpointId).toBe('projects.list');
    // What is mocked keeps answering.
    await expect(apiCall(billing.getWallet, {})).resolves.toMatchObject({ available_balance: 4860 });
  });

  it('un 500 del backend real llega como ApiError 500', async () => {
    backend = () => Response.json({ detail: 'Error interno del servidor.' }, { status: 500 });
    expect((await fail(apiCall(projects.listProjects, {}))).status).toBe(500);
  });
});

describe('modo híbrido: identidad coherente con /me simulado', () => {
  it('el /me simulado toma el id del JWT real y muestra un único workspace de reemplazo', async () => {
    const me = await apiCall(users.getMe, {});
    expect(me.id).toBe(REAL_USER);
    expect(me.active_workspace_id).toBe(BACKEND_DEFAULT_WORKSPACE_ID);
    expect(me.workspaces).toHaveLength(1);
    expect(me.workspaces[0]).toMatchObject({ id: BACKEND_DEFAULT_WORKSPACE_ID, role: 'owner', available_balance: 4860 });
    // Without a remembered login the email is a placeholder that still passes the contract.
    expect(me.email).toBe('usuario-4544e2e0@backend.invalid');
    expect(me.tour_completed).toBe(true);
  });

  it('el email sale del token cuando el backend lo incluye', async () => {
    signInReal({ email: 'ana@real.test' });
    await expect(apiCall(users.getMe, {})).resolves.toMatchObject({ email: 'ana@real.test', name: 'ana' });
  });

  it('el mismo usuario no se crea dos veces, y dos usuarios reales no comparten datos', async () => {
    await apiCall(users.getMe, {});
    await apiCall(users.updateMe, { body: { name: 'Híbrida Real' } });
    await expect(apiCall(users.getMe, {})).resolves.toMatchObject({ name: 'Híbrida Real' });
    expect(loadDb().users.filter((user) => user.id === REAL_USER)).toHaveLength(1);
    signInReal({ user_id: '11111111-2222-4333-8444-555555555555' });
    const other = await apiCall(users.getMe, {});
    expect(other.id).toBe('11111111-2222-4333-8444-555555555555');
    expect(other.name).not.toBe('Híbrida Real');
    // Each real user has a placeholder workspace of their own: the mocked wallet of one is not the other's.
    expect(other.active_workspace_id).not.toBe(BACKEND_DEFAULT_WORKSPACE_ID);
    expect(hasBackendDefaultPrefix(other.active_workspace_id)).toBe(true);
    expect(other.workspaces.map((item) => item.id)).toEqual([other.active_workspace_id]);
  });

  it('con el workspace de reemplazo activo el cliente no manda X-Workspace-Id: el backend usa el workspace por defecto', async () => {
    backend = () => Response.json([]);
    setActiveWorkspaceId(BACKEND_DEFAULT_WORKSPACE_ID);
    await apiCall(projects.listProjects, {});
    await apiCall(brandKits.listBrandKits, {});
    expect(backendCalls.map((call) => call.workspace)).toEqual([null, null]);
    expect(BACKEND_DEFAULT_WORKSPACE_ID).toBe('00000000-0000-4000-8000-3cfe1c5e357a');
    // A real id is sent as always.
    setActiveWorkspaceId(REAL_WORKSPACE);
    await apiCall(projects.listProjects, {});
    expect(backendCalls.at(-1)?.workspace).toBe(REAL_WORKSPACE);
  });

  it('con el workspace de reemplazo, las filas del backend cuentan como del workspace activo', () => {
    expect(isOfWorkspace(REAL_WORKSPACE, BACKEND_DEFAULT_WORKSPACE_ID)).toBe(true);
    expect(isOfWorkspace(REAL_WORKSPACE, REAL_WORKSPACE)).toBe(true);
    expect(isOfWorkspace(REAL_WORKSPACE, WORKSPACE_IDS.estudio)).toBe(false);
  });

  it('fuera de híbrido el marcador no es nada: se manda como cualquier id y no afloja el filtro por workspace', async () => {
    for (const mode of ['real', 'mock'] as const) {
      forceRouting({ mode });
      expect(isBackendDefaultWorkspace(BACKEND_DEFAULT_WORKSPACE_ID)).toBe(false);
      expect(isOfWorkspace(REAL_WORKSPACE, BACKEND_DEFAULT_WORKSPACE_ID)).toBe(false);
      expect(isMeBridged()).toBe(false);
    }
    // Real mode: MSW is not there; the request goes out with the header, like any other workspace id.
    forceRouting({ mode: 'real' });
    server.use(http.get(`${API_URL}/api/v1/projects/`, () => passthrough()));
    backend = () => Response.json([]);
    setActiveWorkspaceId(BACKEND_DEFAULT_WORKSPACE_ID);
    await apiCall(projects.listProjects, {});
    expect(backendCalls.at(-1)?.workspace).toBe(BACKEND_DEFAULT_WORKSPACE_ID);
  });

  it('un marcador que quedó guardado se olvida cuando el puente ya no se usa', () => {
    // Hybrid with /me mocked: the bridge is in use, the placeholder stays.
    setActiveWorkspaceId(BACKEND_DEFAULT_WORKSPACE_ID);
    expect(isMeBridged()).toBe(true);
    dropStaleBridgeWorkspace();
    expect(getActiveWorkspaceId()).toBe(BACKEND_DEFAULT_WORKSPACE_ID);
    // /me became real (the env list, the contract or a dev override): gone before anything is asked.
    forceRouting({ mode: 'hybrid', real: 'existing,users.me' });
    expect(isMeBridged()).toBe(false);
    dropStaleBridgeWorkspace();
    expect(getActiveWorkspaceId()).toBeNull();
    // A build that is not hybrid forgets it too, and leaves a real id alone.
    for (const mode of ['real', 'mock'] as const) {
      forceRouting({ mode });
      setActiveWorkspaceId(BACKEND_DEFAULT_WORKSPACE_ID);
      dropStaleBridgeWorkspace();
      expect(getActiveWorkspaceId()).toBeNull();
      setActiveWorkspaceId(REAL_WORKSPACE);
      dropStaleBridgeWorkspace();
      expect(getActiveWorkspaceId()).toBe(REAL_WORKSPACE);
    }
  });

  it('un JWT vencido recibe 401 del mock, y el cliente lo renueva contra el backend real', async () => {
    const fresh = { access: jwt(), refresh: jwt({}, 'refresh') };
    setTokens({ access: jwt({ exp: Math.floor(Date.now() / 1000) - 10 }), refresh: jwt({}, 'refresh') });
    backend = () => Response.json(fresh);
    await expect(apiCall(billing.getWallet, {})).resolves.toMatchObject({ available_balance: 4860 });
    expect(backendCalls.map((call) => `${call.method} ${call.path}`)).toEqual(['POST /api/token/refresh/']);
    expect(getTokens()).toEqual(fresh);
  });

  it('un token simulado no sirve en híbrido (y uno real no sirve en mock): la sesión ajena se descarta al arrancar', async () => {
    setTokens(mockTokens(USER_IDS.lucia));
    dropForeignSession();
    expect(getTokens()).toBeNull();
    signInReal();
    dropForeignSession();
    expect(getTokens()).not.toBeNull();
    forceRouting({ mode: 'mock' });
    dropForeignSession();
    expect(getTokens()).toBeNull();
    setTokens(mockTokens(USER_IDS.lucia));
    dropForeignSession();
    expect(getTokens()).not.toBeNull();
  });

  it('en modo mock un JWT real no es una sesión', async () => {
    forceRouting({ mode: 'mock' });
    backend = () => Response.json({});
    await expect(apiCall(users.getMe, {})).rejects.toBeInstanceOf(SessionExpiredError);
    expect(backendCalls).toEqual([]);
    expect(loadDb().users.some((user) => user.id === REAL_USER)).toBe(false);
  });
});

describe('modo híbrido: adopción de ids del backend', () => {
  it('la billetera simulada de un workspace real desconocido se crea con el saldo del escenario', async () => {
    setActiveWorkspaceId(REAL_WORKSPACE);
    const wallet = await apiCall(billing.getWallet, {});
    expect(wallet).toMatchObject({ workspace: REAL_WORKSPACE, available_balance: 4860, reserved_balance: 0 });
    const subscription = await apiCall(billing.getSubscription, {});
    expect(subscription.plan.id).toBe(PLAN_IDS.pro);
    const db = loadDb();
    expect(db.workspaces.filter((item) => item.id === REAL_WORKSPACE)).toHaveLength(1);
    expect(db.wallets.filter((item) => item.workspace === REAL_WORKSPACE)).toHaveLength(1);
    expect(db.members.filter((item) => item.workspace === REAL_WORKSPACE && item.user === REAL_USER)).toHaveLength(1);
    // What the mocks of the workspace build on keeps working: the purchase lands in that wallet.
    const purchase = await apiCall(billing.createTokenPurchase, { body: { custom_tokens: 1000, return_url: 'http://localhost:3000/uso/comprar/resultado' } });
    expect(purchase.status).toBe('pending');
    expect(loadDb().purchases.at(-1)?.workspace).toBe(REAL_WORKSPACE);
  });

  it('el plan y el saldo del workspace adoptado siguen al escenario elegido', async () => {
    applyScenario('saldo-bajo');
    setActiveWorkspaceId(REAL_WORKSPACE);
    await expect(apiCall(billing.getWallet, {})).resolves.toMatchObject({ available_balance: 120 });
    applyScenario('plan-gratis');
    await expect(apiCall(billing.getWallet, {})).resolves.toMatchObject({ available_balance: 300 });
    await expect(apiCall(billing.getSubscription, {})).resolves.toMatchObject({ plan: { id: PLAN_IDS.free } });
  });

  it('redes y notificaciones simuladas funcionan para un workspace real', async () => {
    setActiveWorkspaceId(REAL_WORKSPACE);
    const accounts = await apiCall(social.listSocialAccounts, {});
    expect(accounts.map((account) => account.status)).toEqual(['disconnected', 'disconnected', 'disconnected']);
    await expect(apiCall(notifications.listNotifications, {})).resolves.toEqual({ unread_count: 0, results: [] });
    await expect(apiCall(social.listPosts, {})).resolves.toMatchObject({ count: 0 });
  });

  it('otro usuario real en el mismo workspace real comparte la billetera adoptada', async () => {
    setActiveWorkspaceId(REAL_WORKSPACE);
    await apiCall(billing.getWallet, {});
    const first = loadDb().wallets.find((item) => item.workspace === REAL_WORKSPACE)?.id;
    signInReal({ user_id: '11111111-2222-4333-8444-555555555555' });
    setActiveWorkspaceId(REAL_WORKSPACE);
    const wallet = await apiCall(billing.getWallet, {});
    expect(wallet.id).toBe(first);
    expect(loadDb().workspaces.filter((item) => item.id === REAL_WORKSPACE)).toHaveLength(1);
  });

  it('un proyecto real desconocido tiene un doble para los endpoints simulados (actividad)', async () => {
    const activity = await apiCall(projects.getProjectActivity, { params: { id: REAL_PROJECT } });
    expect(activity).toEqual([]);
    const db = loadDb();
    const standIn = db.projects.find((item) => item.id === REAL_PROJECT);
    expect(standIn).toMatchObject({ workspace: BACKEND_DEFAULT_WORKSPACE_ID, title: 'Proyecto del backend', cost: null });
    await apiCall(projects.getProjectActivity, { params: { id: REAL_PROJECT } });
    expect(loadDb().projects.filter((item) => item.id === REAL_PROJECT)).toHaveLength(1);
    // Nothing was charged to the mocked wallet for it.
    expect(db.transactions.some((item) => item.project?.id === REAL_PROJECT)).toBe(false);
  });

  it('un clip real desconocido tiene un doble: se puede leer y publicar desde los mocks', async () => {
    const clip = await apiCall(clips.getClip, { params: { id: REAL_CLIP } });
    expect(clip).toMatchObject({ id: REAL_CLIP, status: 'completed', title: 'Clip del backend' });
    // Publishing asks for a connected network: the mocked one of the adopted workspace.
    const db = loadDb();
    const account = db.socialAccounts.find((item) => item.workspace === BACKEND_DEFAULT_WORKSPACE_ID && item.platform === 'YOUTUBE_SHORTS');
    expect(account).toBeDefined();
    if (account) Object.assign(account, { status: 'connected', username: '@real' });
    saveDb(db);
    const post = await apiCall(social.createPost, { body: { video_clip: REAL_CLIP, platform: 'YOUTUBE_SHORTS', caption: 'Hola' } });
    expect(post.video_clip).toBe(REAL_CLIP);
  });

  it('publicar un clip real que ningún mock vio todavía también lo adopta', async () => {
    const db = loadDb();
    adoptUserFromToken(db, getTokens()?.access ?? '', Date.now());
    const account = db.socialAccounts.find((item) => item.workspace === BACKEND_DEFAULT_WORKSPACE_ID && item.platform === 'YOUTUBE_SHORTS');
    if (account) Object.assign(account, { status: 'connected', username: '@real' });
    saveDb(db);
    const post = await apiCall(social.createPost, { body: { video_clip: REAL_CLIP, platform: 'YOUTUBE_SHORTS', caption: 'Hola' } });
    expect(post.clip.project_id).toBe(loadDb().projects.find((item) => item.clips.some((clip) => clip.id === REAL_CLIP))?.id);
  });

  it('los pedidos simultáneos del primer render adoptan lo mismo: los ids salen del id real, no del azar', async () => {
    setActiveWorkspaceId(REAL_WORKSPACE);
    // Two copies of the database, as two requests that arrive together have them.
    const base = JSON.stringify(loadDb());
    const adopt = () => {
      const db = JSON.parse(base) as ReturnType<typeof loadDb>;
      const user = adoptUserFromToken(db, getTokens()?.access ?? '', Date.now());
      if (!user) throw new Error('sin usuario');
      adoptWorkspace(db, user, REAL_WORKSPACE, Date.now());
      const clip = adoptClip(db, REAL_WORKSPACE, REAL_CLIP);
      return {
        wallet: db.wallets.find((item) => item.workspace === REAL_WORKSPACE)?.id,
        placeholderWallet: db.wallets.find((item) => item.workspace === BACKEND_DEFAULT_WORKSPACE_ID)?.id,
        members: db.members.filter((item) => item.user === REAL_USER).map((item) => item.id).sort(),
        clipProject: clip?.project.id,
        clipTimes: [clip?.clip.start_time, clip?.clip.end_time],
      };
    };
    const first = adopt();
    const second = adopt();
    expect(second).toEqual(first);
    expect(first.wallet).toMatch(/^0c0a0001-/);
    expect(first.wallet).not.toBe(first.placeholderWallet);
    expect(first.members).toHaveLength(2);
    expect(first.clipProject).toMatch(/^0c0a0003-/);
    // And through the API, in parallel: one wallet, the same in every answer.
    const wallets = await Promise.all([apiCall(billing.getWallet, {}), apiCall(billing.getWallet, {}), apiCall(billing.getSubscription, {}).then(() => apiCall(billing.getWallet, {}))]);
    expect(new Set(wallets.map((wallet) => wallet.id))).toEqual(new Set([first.wallet]));
  });

  it('cerrar sesión olvida el email del login', async () => {
    backend = () => new Response(null, { status: 204 });
    rememberLoginEmail('hibrido@onecreator.test');
    await apiCall(auth.logout, { body: { refresh: getTokens()?.refresh ?? '', all_devices: false } });
    expect(backendCalls.at(-1)?.path).toBe('/api/v1/auth/logout/');
    const db = loadDb();
    db.users = db.users.filter((user) => user.id !== REAL_USER);
    saveDb(db);
    await expect(apiCall(users.getMe, {})).resolves.toMatchObject({ email: 'usuario-4544e2e0@backend.invalid' });
  });

  it('lo que no es un id del backend sigue siendo 404', async () => {
    expect((await fail(apiCall(projects.getProjectActivity, { params: { id: 'no-es-un-uuid' } }))).status).toBe(404);
    expect((await fail(apiCall(clips.getClip, { params: { id: 'tampoco' } }))).status).toBe(404);
  });

  it('en modo mock no se adopta nada: un id desconocido es 404 y un workspace desconocido no se crea', async () => {
    forceRouting({ mode: 'mock' });
    applyScenario('estudio-norte');
    setTokens(mockTokens(USER_IDS.lucia));
    setActiveWorkspaceId(REAL_WORKSPACE);
    expect((await fail(apiCall(projects.getProjectActivity, { params: { id: REAL_PROJECT } }))).status).toBe(404);
    expect((await fail(apiCall(clips.getClip, { params: { id: REAL_CLIP } }))).status).toBe(404);
    // An unknown X-Workspace-Id falls back to the user's own workspace, as before.
    await expect(apiCall(billing.getWallet, {})).resolves.toMatchObject({ workspace: WORKSPACE_IDS.estudio });
    const db = loadDb();
    expect(db.workspaces.some((item) => item.id === REAL_WORKSPACE)).toBe(false);
    expect(db.projects.some((item) => item.id === REAL_PROJECT)).toBe(false);
  });

  it('un usuario real no entra a un workspace del escenario aunque mande su id', async () => {
    setActiveWorkspaceId(WORKSPACE_IDS.estudio);
    await expect(apiCall(billing.getWallet, {})).resolves.toMatchObject({ workspace: BACKEND_DEFAULT_WORKSPACE_ID });
    expect(loadDb().members.some((item) => item.workspace === WORKSPACE_IDS.estudio && item.user === REAL_USER)).toBe(false);
  });
});

describe('modo híbrido: escenarios', () => {
  it('cambiar de escenario no toca la sesión real', () => {
    const before = getTokens();
    applyScenario('saldo-bajo');
    expect(getTokens()).toEqual(before);
    expect(getScenarioId()).toBe('saldo-bajo');
  });

  it('un escenario de sesión o de datos simulados elegido en modo mock no se usa en híbrido', async () => {
    forceRouting({ mode: 'mock' });
    applyScenario('sesion-vencida');
    forceRouting(HYBRID);
    signInReal();
    expect(getScenarioId()).toBe('estudio-norte');
    // The flag of that scenario (every request answers 401) is not in force.
    await expect(apiCall(users.getMe, {})).resolves.toMatchObject({ id: REAL_USER });
  });
});
