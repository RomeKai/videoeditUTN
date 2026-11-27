import { expect, test, type APIRequestContext, type Page, type Response } from '@playwright/test';
import { expectClean, HYBRID_API_URL, HYBRID_REAL, settle, watch } from './helpers';

/**
 * Hybrid build (`NEXT_PUBLIC_API_MODE=hybrid`) against a real Django backend: the login, `/me`, the
 * projects, the brand kits and billing come from it, while everything the contract marks as proposed
 * (today, most of social: the accounts and creating or changing posts) is mocked in the browser.
 *
 * It needs a backend, so it is skipped unless `E2E_HYBRID_API_URL` says where one is, with a user that
 * owns a workspace with at least one project (more than 25 to see a second page) and one brand kit: `E2E_HYBRID_EMAIL` and `E2E_HYBRID_PASSWORD`. See "Modo híbrido" in the README.
 * The assertions about which side answers assume the default routing (no `E2E_HYBRID_REAL`).
 */
const EMAIL = process.env.E2E_HYBRID_EMAIL ?? '';
const PASSWORD = process.env.E2E_HYBRID_PASSWORD ?? '';

test.skip(!HYBRID_API_URL, 'Sin E2E_HYBRID_API_URL no hay backend real contra el que probar el modo híbrido.');
test.skip(Boolean(HYBRID_API_URL) && (!EMAIL || !PASSWORD), 'Faltan E2E_HYBRID_EMAIL y E2E_HYBRID_PASSWORD.');
test.skip(Boolean(HYBRID_REAL), 'El smoke asume el ruteo por defecto (sin E2E_HYBRID_REAL).');

// One user and one browser-side mock database: the tests change the same backend rows.
test.describe.configure({ mode: 'serial' });

const hybrid = { apiUrl: HYBRID_API_URL, real: HYBRID_REAL };

interface Seen {
  method: string;
  path: string;
  status: number;
  /** Endpoint id when a mock answered; null when the backend did. */
  mock: string | null;
  workspaceHeader: string | undefined;
}

/** Every API answer the page got, with who gave it. */
function record(page: Page): Seen[] {
  const seen: Seen[] = [];
  page.on('response', (response: Response) => {
    const request = response.request();
    if (!response.url().startsWith(`${HYBRID_API_URL}/api/`) || request.method() === 'OPTIONS') return;
    seen.push({
      method: request.method(),
      path: new URL(response.url()).pathname,
      status: response.status(),
      mock: response.headers()['x-oc-mock'] ?? null,
      workspaceHeader: request.headers()['x-workspace-id'],
    });
  });
  return seen;
}

const answersOf = (seen: Seen[], method: string, path: string | RegExp) =>
  seen.filter((item) => item.method === method && (typeof path === 'string' ? item.path === path : path.test(item.path)));

/** The backend, asked directly from Node with the same user: what the screens must show. */
async function backend(request: APIRequestContext) {
  const login = await request.post(`${HYBRID_API_URL}/api/token/`, { data: { email: EMAIL, password: PASSWORD } });
  expect(login.ok(), 'el usuario de E2E_HYBRID_EMAIL puede iniciar sesión en el backend').toBe(true);
  const { access } = (await login.json()) as { access: string };
  const headers = { Authorization: `Bearer ${access}` };
  const get = async <T>(path: string): Promise<T> => {
    const response = await request.get(`${HYBRID_API_URL}${path}`, { headers });
    expect(response.ok(), `GET ${path}`).toBe(true);
    return (await response.json()) as T;
  };
  return {
    headers,
    userId: (JSON.parse(Buffer.from(access.split('.')[1] ?? '', 'base64url').toString()) as { user_id: string }).user_id,
    me: () => get<{ active_workspace_id: string | null; workspaces: Array<{ id: string; name: string }> }>('/api/v1/me/'),
    // A page (25, newest first), as the screen asks for it.
    projects: async () => (await get<{ results: Array<{ id: string; title: string; workspace: string; clips: Array<{ id: string }> }> }>('/api/v1/projects/')).results,
    /** The list as B2 asks for it, with a query string such as `?status_group=failed`. */
    projectsPage: (query = '') =>
      get<{
        count: number;
        next: string | null;
        results: Array<{ id: string; title: string; status: string; eta_seconds: number | null }>;
        status_counts: Record<'all' | 'in_progress' | 'to_review' | 'ready' | 'failed', number>;
      }>(`/api/v1/projects/${query}`),
    brandKits: () => get<Array<{ id: string; name: string; workspace: string }>>('/api/v1/brand-kits/'),
  };
}

async function signIn(page: Page) {
  await page.goto('/login');
  await settle(page);
  await page.getByLabel('Email').fill(EMAIL);
  await page.getByLabel('Contraseña', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Iniciar sesión' }).click();
  await expect(page).toHaveURL(/\/proyectos$/);
  await settle(page);
}

/**
 * B2 on screen against the backend's own answers: the pages, the filters, the search and the counts are the
 * server's. Part of the first test, not one of its own: every test signs in twice and the backend lets
 * 10 logins a minute through.
 */
async function checkProjectsList(page: Page, api: Awaited<ReturnType<typeof backend>>, seen: Seen[]) {
  const first = await api.projectsPage();

  const summary = (count: number) => page.getByText(new RegExp(`de ${count} proyectos`));
  const filters = page.getByRole('group', { name: 'Filtrar por estado' });
  const chip = (name: RegExp, count: number) => expect(filters.getByRole('button', { name })).toHaveText(new RegExp(`\\D${count}$`));
  await expect(summary(first.count)).toBeVisible();
  await chip(/Todos/, first.status_counts.all);
  await chip(/En proceso/, first.status_counts.in_progress);
  await chip(/Para revisar/, first.status_counts.to_review);
  await chip(/Listos/, first.status_counts.ready);
  await chip(/Con error/, first.status_counts.failed);
  await expect(page.getByRole('table', { name: 'Proyectos del workspace' }).getByRole('row')).toHaveCount(first.results.length + 1);

  // A project being processed with an estimate says how long is left.
  const estimated = first.results.find((project) => project.status === 'ingesting' && project.eta_seconds);
  if (estimated) await expect(page.getByRole('row').filter({ hasText: estimated.title }).getByText(/≈ \d+ (min|h)/)).toBeVisible();

  // The filter: the rows and the total change, the chips do not.
  const failed = await api.projectsPage('?status_group=failed');
  await filters.getByRole('button', { name: /Con error/ }).click();
  if (failed.count > 0) {
    await expect(summary(failed.count)).toBeVisible();
    await expect(page.getByText(failed.results[0]?.title ?? '').first()).toBeVisible();
  } else await expect(page.getByText('No hay proyectos en este estado')).toBeVisible();
  await chip(/Todos/, first.status_counts.all);
  expect(new URL(page.url()).pathname).toBe('/proyectos');
  await filters.getByRole('button', { name: /Todos/ }).click();
  await expect(summary(first.count)).toBeVisible();

  // The next page, when there is one.
  if (first.next) {
    await page.getByRole('button', { name: 'Siguiente' }).click();
    await expect(page.getByText(new RegExp(`^26–\\d+ de ${first.count} proyectos`))).toBeVisible();
    const second = await api.projectsPage('?page=2');
    await expect(page.getByText(second.results[0]?.title ?? '').first()).toBeVisible();
  }

  // The search, whatever the case: the total and the chips follow it.
  const term = (first.results[0]?.title ?? '').toUpperCase();
  const found = await api.projectsPage(`?search=${encodeURIComponent(term)}`);
  expect(found.count).toBeGreaterThan(0);
  await page.getByRole('searchbox', { name: 'Buscar proyectos por título' }).fill(term);
  await expect(summary(found.count)).toBeVisible();
  await chip(/Todos/, found.status_counts.all);

  const lists = answersOf(seen, 'GET', '/api/v1/projects/');
  expect(lists.length).toBeGreaterThan(3);
  expect(lists.filter((item) => item.mock !== null || item.status !== 200)).toEqual([]);
  // Back to the whole list, for what the test checks next.
  await page.getByRole('searchbox', { name: 'Buscar proyectos por título' }).fill('');
  await expect(summary(first.count)).toBeVisible();
}


test.describe('Modo híbrido contra el backend real', () => {
  test('login, /me, proyectos, brand kits y billetera vienen del backend; las redes son simuladas', async ({ page, request }) => {
    const api = await backend(request);
    const me = await api.me();
    const active = me.workspaces.find((item) => item.id === me.active_workspace_id);
    expect(active, 'el usuario de prueba tiene un workspace activo en el backend').toBeDefined();
    const problems = watch(page, { hybrid });
    const seen = record(page);

    await signIn(page);
    // The session is the backend's: a real JWT, and /me is the backend's too, with its workspaces.
    expect(answersOf(seen, 'POST', '/api/token/')).toMatchObject([{ status: 200, mock: null }]);
    const stored = await page.evaluate(() => JSON.parse(window.localStorage.getItem('oc.auth') ?? '{}') as { access?: string });
    expect(stored.access?.split('.')).toHaveLength(3);
    expect(answersOf(seen, 'GET', '/api/v1/me/').length).toBeGreaterThan(0);
    expect(answersOf(seen, 'GET', '/api/v1/me/').every((item) => item.mock === null)).toBe(true);
    await expect(page.getByRole('button', { name: `Cambiar de workspace. Actual: ${active?.name ?? ''}` })).toBeVisible();

    // Projects: the list the backend gives for the user's default workspace.
    const projects = await api.projects();
    expect(projects.length, 'el usuario de prueba tiene al menos un proyecto en el backend').toBeGreaterThan(0);
    expect(answersOf(seen, 'GET', '/api/v1/projects/').at(-1)).toMatchObject({ status: 200, mock: null });
    for (const project of projects.slice(0, 3)) await expect(page.getByText(project.title).first()).toBeVisible();
    await checkProjectsList(page, api, seen);

    // Brand kits: also the backend's.
    await page.goto('/brand-kits');
    await settle(page);
    const kits = await api.brandKits();
    expect(kits.length, 'el usuario de prueba tiene al menos un brand kit en el backend').toBeGreaterThan(0);
    expect(answersOf(seen, 'GET', '/api/v1/brand-kits/').at(-1)).toMatchObject({ status: 200, mock: null });
    for (const kit of kits.slice(0, 3)) await expect(page.getByRole('article', { name: `Brand kit ${kit.name}` })).toBeVisible();
    await expect(page.getByRole('article', { name: /^Brand kit / })).toHaveCount(kits.length);

    // Billing: the wallet and the subscription are the backend's too.
    await page.goto('/uso');
    await settle(page);
    expect(answersOf(seen, 'GET', '/api/v1/wallet/').at(-1)).toMatchObject({ status: 200, mock: null });
    expect(answersOf(seen, 'GET', '/api/v1/subscription/').at(-1)).toMatchObject({ status: 200, mock: null });
    await expect(page.getByText('Saldo disponible')).toBeVisible();

    // Social accounts are still proposed: Redes is answered by the mocks, for the same real workspace.
    await page.goto('/ajustes/redes');
    await settle(page);
    expect(answersOf(seen, 'GET', '/api/v1/social-accounts/').at(-1)).toMatchObject({
      status: 200, mock: 'social.accounts', workspaceHeader: active?.id,
    });

    // With /me real there is no bridge: a workspace can be created (it would be the backend's).
    await page.getByRole('button', { name: /Cambiar de workspace/ }).click();
    const create = page.getByRole('menuitem', { name: /Crear workspace/ });
    await expect(create).toBeEnabled();
    await expect(create).not.toContainText('No disponible en modo híbrido');
    await page.keyboard.press('Escape');

    // Every request that names a workspace names the real one, real or mocked.
    const real = seen.filter((item) => item.mock === null);
    expect(real.length).toBeGreaterThan(2);
    const named = seen.filter((item) => item.workspaceHeader !== undefined);
    expect(named.length).toBeGreaterThan(0);
    expect(named.filter((item) => item.workspaceHeader !== active?.id)).toEqual([]);
    expect(seen.filter((item) => item.status >= 400)).toEqual([]);
    expectClean(problems, 'el recorrido híbrido');
  });

  test('un brand kit creado en la app queda guardado en el backend real', async ({ page, request }) => {
    const api = await backend(request);
    const name = `Kit híbrido ${Date.now().toString(36)}`;
    const problems = watch(page, { hybrid });
    const seen = record(page);
    await signIn(page);
    await page.goto('/brand-kits/nuevo');
    await settle(page);
    await page.getByLabel('Nombre').fill(name);
    await page.getByRole('button', { name: 'Crear brand kit' }).click();
    await expect(page.getByText(`Se creó «${name}»`)).toBeVisible();
    await expect(page).toHaveURL(/\/brand-kits\/[0-9a-f-]{36}$/);
    expect(answersOf(seen, 'POST', '/api/v1/brand-kits/')).toMatchObject([{ status: 201, mock: null }]);

    const created = (await api.brandKits()).find((kit) => kit.name === name);
    expect(created, 'el kit existe en el backend').toBeDefined();
    expect(page.url()).toContain(created?.id ?? 'sin-id');
    // Left as it was found.
    const removed = await request.delete(`${HYBRID_API_URL}/api/v1/brand-kits/${created?.id}/`, { headers: api.headers });
    expect(removed.status()).toBe(204);
    expectClean(problems, 'crear un brand kit en híbrido');
  });

  test('un endpoint simulado acepta el id de un clip real', async ({ page, request }) => {
    const api = await backend(request);
    const workspace = (await api.me()).active_workspace_id ?? '';
    const clip = (await api.projects()).flatMap((project) => project.clips)[0];
    expect(clip, 'el usuario de prueba tiene al menos un proyecto con clips en el backend').toBeDefined();
    await signIn(page);
    // `social.createPost` is proposed (mocked); the clip only exists in the backend. The mock takes its id
    // (a stand-in of the clip) and gets to the next check: the mocked accounts of the workspace are not connected.
    const created = await page.evaluate(
      async ({ url, id, workspace }) => {
        const { access } = JSON.parse(window.localStorage.getItem('oc.auth') ?? '{}') as { access: string };
        const response = await fetch(`${url}/api/v1/scheduled-posts/`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${access}`, 'Content-Type': 'application/json', 'X-Workspace-Id': workspace },
          body: JSON.stringify({ video_clip: id, platform: 'YOUTUBE_SHORTS', draft: true }),
        });
        return { status: response.status, mock: response.headers.get('x-oc-mock'), body: (await response.json()) as { code?: string } };
      },
      { url: HYBRID_API_URL, id: clip?.id ?? '', workspace },
    );
    expect(created).toMatchObject({ status: 409, mock: 'social.createPost', body: { code: 'account_not_connected' } });
  });

  test('el panel muestra el origen de cada dominio y permite cambiarlo en este navegador', async ({ page, request }) => {
    const api = await backend(request);
    const kits = await api.brandKits();
    expect(kits.length, 'el usuario de prueba tiene al menos un brand kit en el backend').toBeGreaterThan(0);
    const seen = record(page);
    await signIn(page);
    await page.getByRole('button', { name: /Híbrido: \d+ de 10 dominios con backend real/ }).click();
    const panel = page.getByRole('region', { name: 'Datos de prueba' });
    const domain = (name: string) => panel.locator(`li[data-domain="${name}"]`);
    await expect(domain('auth')).toHaveAttribute('data-route', 'real');
    await expect(domain('auth').getByRole('button', { name: 'Mock' })).toBeDisabled();
    // Which endpoints exist is the contract's business: here only that nothing but auth is forced.
    // Social still has proposed endpoints (connecting an account, creating a post): never all real.
    await expect(domain('social')).not.toHaveAttribute('data-route', 'real');
    await expect(domain('projects')).toHaveAttribute('data-route', /real|mixed/);
    await expect(domain('brandKits')).toHaveAttribute('data-route', /real|mixed/);
    // Scenarios about the mocked session are off, with the reason.
    await expect(panel.getByRole('button', { name: /Rol Lector/ })).toBeDisabled();
    await expect(panel.getByText(/Solo en modo mock: en híbrido la sesión/).first()).toBeVisible();

    // Brand kits to the mocks: after the reload the list is the mocked one (empty for this workspace).
    await Promise.all([page.waitForEvent('load'), domain('brandKits').getByRole('button', { name: 'Mock' }).click()]);
    await page.goto('/brand-kits');
    await settle(page);
    expect(answersOf(seen, 'GET', '/api/v1/brand-kits/').at(-1)).toMatchObject({ status: 200, mock: 'brandKits.list' });
    await expect(page.getByRole('article', { name: /^Brand kit / })).toHaveCount(0);

    // Back to the env: the backend's kits again.
    await page.getByRole('button', { name: /Híbrido/ }).click();
    await expect(domain('brandKits')).toHaveAttribute('data-route', 'mock');
    await Promise.all([page.waitForEvent('load'), panel.getByRole('button', { name: 'Volver a lo que dice el env' }).click()]);
    await settle(page);
    expect(answersOf(seen, 'GET', '/api/v1/brand-kits/').at(-1)).toMatchObject({ status: 200, mock: null });
    await expect(page.getByRole('article', { name: /^Brand kit / })).toHaveCount(kits.length);
  });

  test('con el backend caído se ven los estados normales de error, y lo simulado sigue andando', async ({ page, context }) => {
    await signIn(page);
    const consoleErrors: string[] = [];
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    page.on('pageerror', (error) => consoleErrors.push(`pageerror: ${error.message}`));
    // The backend stops answering (the requests the worker lets through are cut at the network).
    await context.route(`${HYBRID_API_URL}/api/v1/projects/**`, (route) => route.abort('connectionrefused'));
    await page.goto('/proyectos');
    const alert = page.getByRole('alert').filter({ hasText: 'No pudimos conectar' });
    await expect(alert).toBeVisible();
    await expect(alert.getByRole('button', { name: 'Reintentar' })).toBeVisible();
    // Nothing of MSW in the page: only the browser's own note about the failed request.
    expect(consoleErrors.filter((text) => /MSW|msw/.test(text))).toEqual([]);
    // The mocked part of the shell still answers.
    await expect(page.getByRole('button', { name: /Cambiar de workspace/ })).toBeVisible();

    // The backend is back: Reintentar loads the list.
    await context.unroute(`${HYBRID_API_URL}/api/v1/projects/**`);
    await alert.getByRole('button', { name: 'Reintentar' }).click();
    await expect(alert).toBeHidden();
  });
});
