import { expect, test, type Page } from '@playwright/test';
import { REAL_API_URL } from './helpers';

/**
 * Build with the mocks off (`NEXT_PUBLIC_API_MOCKS=false`): the app talks to the
 * backend. Nothing listens there; each test answers for it with `page.route`,
 * with bodies shaped like the real ones.
 */
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
const wallet = {
  id: WS,
  workspace: WS,
  available_balance: '100.0000000000',
  reserved_balance: '0.0000000000',
  updated_at: '2026-01-01T00:00:00Z',
  spent_this_month: '0.00',
  low_balance_threshold: '10.00',
  reservations: [],
};

interface Call {
  method: string;
  path: string;
  workspace: string | undefined;
}

/** Fakes the backend. `overrides` answers a path with a status; everything unknown is DRF's 404. */
async function fakeApi(page: Page, overrides: Record<string, number> = {}): Promise<Call[]> {
  const calls: Call[] = [];
  await page.route(`${REAL_API_URL}/**`, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    // The preflight of a cross-origin request with Authorization and X-Workspace-Id.
    const cors = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    calls.push({ method: request.method(), path, workspace: request.headers()['x-workspace-id'] });
    const status = overrides[path];
    // A 401 as simplejwt answers it; anything else as a server that is down does (no `code`: it is not an error of the endpoint).
    if (status) return route.fulfill({ status, headers: cors, json: status === 401 ? { detail: 'Token is invalid or expired', code: 'token_not_valid' } : { detail: 'Service Unavailable' } });
    if (path === '/api/v1/me/') return route.fulfill({ headers: cors, json: me });
    if (path === '/api/v1/wallet/') return route.fulfill({ headers: cors, json: wallet });
    if (path === '/api/v1/notifications/') return route.fulfill({ headers: cors, json: { unread_count: 0, results: [] } });
    if (path === '/api/v1/status/') return route.fulfill({ headers: cors, json: { maintenance: false, maintenance_window: null } });
    return route.fulfill({ status: 404, headers: cors, json: { detail: 'Not found.' } });
  });
  return calls;
}

async function signedIn(page: Page): Promise<void> {
  await page.addInitScript(() => window.localStorage.setItem('oc.auth', JSON.stringify({ access: 'a', refresh: 'r' })));
}

/** Every URL the tab went through, to prove it never touched the login. */
function navigations(page: Page): string[] {
  const urls: string[] = [];
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) urls.push(new URL(frame.url()).pathname);
  });
  return urls;
}

test.describe('Sin mocks: la sesión contra el backend', () => {
  test('con sesión, un link directo y una recarga se quedan en la página (nunca pasan por Iniciar sesión)', async ({ page }) => {
    const calls = await fakeApi(page);
    await signedIn(page);
    const urls = navigations(page);
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));

    await page.goto('/uso/movimientos');
    await expect(page.getByRole('navigation', { name: 'Secciones', exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: /Saldo: 100 tokens disponibles/ })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('navigation', { name: 'Secciones', exact: true })).toBeVisible();
    await expect(page).toHaveURL(/\/uso\/movimientos$/);

    expect(urls.filter((url) => url !== '/uso/movimientos')).toEqual([]);
    expect(errors).toEqual([]);
    // What belongs to a workspace is only asked once the workspace is known: the header is always there.
    const scoped = calls.filter((call) => call.path === '/api/v1/wallet/' || call.path === '/api/v1/notifications/');
    expect(scoped.length).toBeGreaterThan(0);
    expect(scoped.filter((call) => call.workspace !== WS)).toEqual([]);
  });

  test('sin sesión, la app lleva a Iniciar sesión y recuerda a dónde iba', async ({ page }) => {
    const calls = await fakeApi(page);
    await page.goto('/uso/movimientos');
    await expect(page).toHaveURL(/\/login\?siguiente=%2Fuso%2Fmovimientos$/);
    expect(calls.filter((call) => call.path === '/api/v1/me/')).toEqual([]);
  });

  test('sesión que no se puede renovar: aparece «Sesión vencida» y no se sigue pidiendo con el token muerto', async ({ page }) => {
    const calls = await fakeApi(page, { '/api/v1/me/': 401, '/api/token/refresh/': 401 });
    await signedIn(page);
    await page.goto('/proyectos');
    await expect(page.getByRole('dialog', { name: /sesión venció/i })).toBeVisible();
    expect(calls.filter((call) => call.path === '/api/token/refresh/')).toHaveLength(1);
  });

  test('dos pestañas con el token vencido: renuevan una sola vez y ninguna ve «Sesión vencida»', async ({ context }) => {
    // The backend rotates the refresh token and blacklists the old one: sending it twice is a 401.
    const used: string[] = [];
    const cors = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' };
    await context.route(`${REAL_API_URL}/**`, async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
      if (path === '/api/token/refresh/') {
        const refresh = (request.postDataJSON() as { refresh: string }).refresh;
        used.push(refresh);
        // Slow enough for the other tab to get its own 401 meanwhile.
        await new Promise((resolve) => setTimeout(resolve, 400));
        if (refresh === 'r' && used.filter((item) => item === 'r').length === 1) return route.fulfill({ headers: cors, json: { access: 'a2', refresh: 'r2' } });
        return route.fulfill({ status: 401, headers: cors, json: { detail: 'Token is blacklisted', code: 'token_not_valid' } });
      }
      if (request.headers().authorization !== 'Bearer a2') return route.fulfill({ status: 401, headers: cors, json: { detail: 'Token is invalid or expired', code: 'token_not_valid' } });
      if (path === '/api/v1/me/') return route.fulfill({ headers: cors, json: me });
      if (path === '/api/v1/wallet/') return route.fulfill({ headers: cors, json: wallet });
      if (path === '/api/v1/notifications/') return route.fulfill({ headers: cors, json: { unread_count: 0, results: [] } });
      if (path === '/api/v1/status/') return route.fulfill({ headers: cors, json: { maintenance: false, maintenance_window: null } });
      return route.fulfill({ status: 404, headers: cors, json: { detail: 'Not found.' } });
    });
    await context.addInitScript(() => {
      if (!window.localStorage.getItem('oc.auth')) window.localStorage.setItem('oc.auth', JSON.stringify({ access: 'a', refresh: 'r' }));
    });
    const [first, second] = await Promise.all([context.newPage(), context.newPage()]);
    await Promise.all([first.goto('/uso/movimientos'), second.goto('/uso/movimientos')]);
    for (const tab of [first, second]) {
      await expect(tab.getByRole('navigation', { name: 'Secciones', exact: true })).toBeVisible();
      await expect(tab.getByRole('dialog', { name: /sesión venció/i })).toHaveCount(0);
      expect(await tab.evaluate(() => window.localStorage.getItem('oc.auth'))).toBe(JSON.stringify({ access: 'a2', refresh: 'r2' }));
    }
    expect(used).toEqual(['r']);
  });

  test('el backend caído al renovar no cierra la sesión', async ({ page }) => {
    // The whole backend is down, the status endpoint included.
    await fakeApi(page, { '/api/v1/me/': 401, '/api/token/refresh/': 503, '/api/v1/status/': 503 });
    await signedIn(page);
    await page.goto('/proyectos');
    // A 503 is the maintenance page, not the "session expired" dialog; the tokens stay.
    await expect(page.getByRole('heading', { name: /mantenimiento/i })).toBeVisible();
    await expect(page.getByRole('dialog', { name: /sesión venció/i })).toHaveCount(0);
    expect(await page.evaluate(() => window.localStorage.getItem('oc.auth'))).toContain('"refresh":"r"');
  });

  test('un 503 al renovar con la plataforma en pie: es un error para reintentar, no mantenimiento ni «Sesión vencida»', async ({ page }) => {
    // Only the renewal fails; the status endpoint says all is well.
    await fakeApi(page, { '/api/v1/me/': 401, '/api/token/refresh/': 503 });
    await signedIn(page);
    await page.goto('/proyectos');
    await expect(page.getByRole('button', { name: 'Reintentar' })).toBeVisible();
    await expect(page.getByRole('heading', { name: /mantenimiento/i })).toHaveCount(0);
    await expect(page.getByRole('dialog', { name: /sesión venció/i })).toHaveCount(0);
    expect(await page.evaluate(() => window.localStorage.getItem('oc.auth'))).toContain('"refresh":"r"');
  });

  test('el modo queda fijo en el build: las variables del entorno al arrancar el servidor no lo cambian', async ({ page }) => {
    // This server was started with NEXT_PUBLIC_API_MODE=hybrid and NEXT_PUBLIC_API_MOCKS=true (playwright.config.ts).
    // If the server read them, it would render the mock gate and the browser would not: React #418.
    await fakeApi(page);
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    const html = await (await page.request.get('/login')).text();
    // The server render already has the form: with the mocks on it would be the loading outline of the mock gate.
    expect(html).toContain('Iniciar sesión');
    await page.goto('/login');
    await expect(page.getByRole('button', { name: 'Iniciar sesión' })).toBeVisible();
    await page.waitForLoadState('networkidle');
    expect(errors).toEqual([]);
    // No mock worker and no dev panel.
    expect(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length)).toBe(0);
    await expect(page.getByRole('button', { name: /Mocks|Híbrido/ })).toHaveCount(0);
  });

  test('un workspace marcador que dejó el modo híbrido se olvida: nada se pide sin X-Workspace-Id ni con ese id', async ({ page }) => {
    const calls = await fakeApi(page);
    await signedIn(page);
    await page.addInitScript(() => {
      if (!window.sessionStorage.getItem('sembrado')) window.localStorage.setItem('oc.workspace', '00000000-0000-4000-8000-3cfe1c5e357a');
      window.sessionStorage.setItem('sembrado', '1');
    });
    await page.goto('/uso/movimientos');
    await expect(page.getByRole('link', { name: /Saldo: 100 tokens disponibles/ })).toBeVisible();
    const scoped = calls.filter((call) => call.path !== '/api/v1/me/' && call.path !== '/api/v1/status/' && call.path !== '/api/v1/plans/' && call.path !== '/api/v1/pricing/');
    expect(scoped.length).toBeGreaterThan(0);
    expect(scoped.filter((call) => call.workspace !== WS)).toEqual([]);
    expect(await page.evaluate(() => window.localStorage.getItem('oc.workspace'))).toBe(WS);
  });

  test('las rutas de desarrollo no existen', async ({ page }) => {
    await fakeApi(page);
    for (const path of ['/dev/kit', '/dev/checkout']) {
      await page.goto(path);
      // `notFound()` of the layout: the 404 page instead of the tool (the status stays 200 because the root streams a loading state).
      await expect(page.getByRole('heading', { name: /Esta página no existe/ }), path).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Kit de componentes' })).toHaveCount(0);
    }
  });
});
