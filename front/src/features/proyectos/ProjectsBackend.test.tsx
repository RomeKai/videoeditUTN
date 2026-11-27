import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { API_URL } from '@/api/config';
import real from '@/api/contracts/drf/responses.json';
import { server } from '@/api/mocks/server';
import { loadDb, saveDb } from '@/api/mocks/state';
import { ProjectsScreen } from './ProjectsScreen';
import { nav } from './testing-nav';
import { allowSlowScreens, renderScreen, startScenario } from '@/testing';

allowSlowScreens();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// The list keeps its filter, order, page and search in the URL: a URL that changes, empty before each test.
vi.mock('next/navigation', async () => (await import('./testing-nav')).navigationMock());
afterEach(() => nav.set(''));

const rows = () => within(screen.getByRole('table', { name: 'Proyectos del workspace' })).getAllByRole('row').slice(1);
const titles = () => rows().map((row) => row.querySelector('a')?.textContent?.replace(/^(Archivo|Link[^:]*): /, '') ?? '');

/** Every GET of the list the screen makes, as its query string. */
function watchListRequests(): string[] {
  const asked: string[] = [];
  server.events.on('request:start', ({ request }) => {
    const url = new URL(request.url);
    if (request.method === 'GET' && url.pathname === '/api/v1/projects/') asked.push(url.search);
  });
  return asked;
}
afterEach(() => server.events.removeAllListeners());

const EMPTY_PAGE = { count: 0, next: null, previous: null, results: [], status_counts: { all: 0, in_progress: 0, to_review: 0, ready: 0, failed: 0 } };

const lastQuery = (asked: string[]) => new URLSearchParams(asked[asked.length - 1] ?? '');

describe('B2 con las páginas reales del backend (drf/responses.json)', () => {
  /** The page the backend answered to the search «clase» (test_projects_list_contract_fixture.py). */
  function answerRealPages() {
    server.use(http.get(`${API_URL}/api/v1/projects/`, () => HttpResponse.json(real.project_list)));
  }

  it('muestra las filas, el total y los contadores que manda el servidor', async () => {
    startScenario('estudio-norte');
    answerRealPages();
    renderScreen(<ProjectsScreen />);
    await screen.findByText(/1–3 de 3 proyectos/);
    expect(titles()).toEqual(['CLASE 3: Finanzas personales', 'Clase 2: Ahorro', 'Clase 1: Presupuesto']);
    const filters = screen.getByRole('group', { name: 'Filtrar por estado' });
    expect(within(filters).getByRole('button', { name: /Todos/ })).toHaveTextContent('Todos3');
    expect(within(filters).getByRole('button', { name: /En proceso/ })).toHaveTextContent('En proceso1');
    expect(within(filters).getByRole('button', { name: /Para revisar/ })).toHaveTextContent('Para revisar1');
    expect(within(filters).getByRole('button', { name: /Listos/ })).toHaveTextContent('Listos1');
    expect(within(filters).getByRole('button', { name: /Con error/ })).toHaveTextContent('Con error0');
  });

  it('el proyecto en proceso dice cuánto falta para revisar (eta_seconds)', async () => {
    startScenario('estudio-norte');
    answerRealPages();
    renderScreen(<ProjectsScreen />);
    await screen.findByText(/1–3 de 3 proyectos/);
    const [processing, review, ready] = rows() as [HTMLElement, HTMLElement, HTMLElement];
    // 542 s.
    expect(within(processing).getByText('≈ 9 min')).toHaveAttribute('title', 'Faltan ≈ 9 min para revisar');
    expect(processing).toHaveTextContent('Paso 2 de 4 · ≈ 9 min');
    expect(review).not.toHaveTextContent('≈');
    expect(ready).not.toHaveTextContent('≈');
  });
});

describe('B2 · el servidor pagina, filtra, busca y ordena', () => {
  it('cada cambio de la pantalla viaja en la consulta', async () => {
    startScenario('muchos-proyectos');
    const asked = watchListRequests();
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await screen.findByText(/1–25 de 64 proyectos/);
    expect(Object.fromEntries(lastQuery(asked))).toEqual({ page: '1', page_size: '25' });

    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await screen.findByText(/26–50 de 64 proyectos/);
    expect(lastQuery(asked).get('page')).toBe('2');

    const filters = screen.getByRole('group', { name: 'Filtrar por estado' });
    const failed = Number(within(filters).getByRole('button', { name: /Con error/ }).textContent?.replace(/\D/g, ''));
    expect(failed).toBeGreaterThan(0);
    await user.click(within(filters).getByRole('button', { name: /Con error/ }));
    await waitFor(() => expect(Object.fromEntries(lastQuery(asked))).toEqual({ page: '1', page_size: '25', status_group: 'failed' }));
    await screen.findByText(new RegExp(`de ${failed} proyectos`));
    await waitFor(() => expect(rows().every((row) => within(row).queryByText('Error') !== null)).toBe(true));
    // The chips keep counting every filter, whatever the one chosen.
    expect(within(filters).getByRole('button', { name: /Todos/ })).toHaveTextContent('Todos64');

    await user.click(within(filters).getByRole('button', { name: /Todos/ }));
    await screen.findByText(/1–25 de 64 proyectos/);
    await user.click(screen.getByRole('button', { name: 'Orden: más recientes' }));
    await user.click(screen.getByRole('menuitemradio', { name: 'Título (A–Z)' }));
    await waitFor(() => expect(lastQuery(asked).get('ordering')).toBe('title'));
    await waitFor(() => expect(titles()).toEqual([...titles()].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase(), 'es'))));

    const sample = titles()[3] ?? '';
    await user.type(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' }), `  ${sample.toUpperCase()} `);
    await waitFor(() => expect(lastQuery(asked).get('search')).toBe(sample.toUpperCase()));
    // The page before stays on screen until the search answers: wait for the shorter list.
    await waitFor(() => expect(rows().length).toBeLessThan(25));
    expect(titles()).toContain(sample);
    // The counts follow the search.
    expect(within(filters).getByRole('button', { name: /Todos/ })).toHaveTextContent(`Todos${rows().length}`);
  });

  it('una respuesta que no es una página (el array de antes) es un error, no una lista armada en el navegador', async () => {
    startScenario('muchos-proyectos');
    server.use(http.get(`${API_URL}/api/v1/projects/`, () => HttpResponse.json(real.project_list.results)));
    renderScreen(<ProjectsScreen />);
    expect(await screen.findByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('sin proyectos muestra el estado vacío', async () => {
    startScenario('muchos-proyectos');
    server.use(http.get(`${API_URL}/api/v1/projects/`, () => HttpResponse.json(EMPTY_PAGE)));
    renderScreen(<ProjectsScreen />);
    expect(await screen.findByRole('heading', { name: 'Todavía no hay proyectos' })).toBeInTheDocument();
  });

  it('al borrar una búsqueda sin resultados, el buscador sigue en su lugar mientras llega la lista', async () => {
    startScenario('muchos-proyectos');
    const user = userEvent.setup();
    const view = renderScreen(<ProjectsScreen />);
    await screen.findByText(/1–25 de 64 proyectos/);
    const box = screen.getByRole('searchbox', { name: 'Buscar proyectos por título' });
    await user.type(box, 'zzzz');
    expect(await screen.findByText('Ningún proyecto coincide con «zzzz»')).toBeInTheDocument();

    // The first page left the cache (as it does 5 minutes after the search started), and asking for it again
    // takes a while: meanwhile the screen still holds the page of «zzzz» (0 of 0).
    view.queryClient.removeQueries({ type: 'inactive' });
    let waiting = false;
    server.use(
      http.get(`${API_URL}/api/v1/projects/`, async () => {
        waiting = true;
        await delay(400);
        return undefined; // the mock answers
      }),
    );
    await user.clear(box);
    await waitFor(() => expect(waiting).toBe(true));
    expect(screen.queryByRole('heading', { name: 'Todavía no hay proyectos' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Ningún proyecto coincide/)).not.toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Cargando proyectos' })).toBeInTheDocument();
    expect(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' })).toBe(box);
    expect(box).toHaveFocus();
    await screen.findByText(/1–25 de 64 proyectos/);
  });
});

describe('B2 · una página que ya no existe (404)', () => {
  function keepProjects(howMany: number) {
    const db = loadDb();
    const workspace = db.projects[0]?.workspace;
    const keep = new Set(db.projects.filter((project) => project.workspace === workspace).slice(0, howMany).map((project) => project.id));
    db.projects = db.projects.filter((project) => project.workspace !== workspace || keep.has(project.id));
    saveDb(db);
  }

  it('una URL con una página muy pasada del final no pide las páginas de a una', async () => {
    startScenario('estudio-norte');
    const asked = watchListRequests();
    nav.set('pagina=60');
    renderScreen(<ProjectsScreen />);
    expect(await screen.findByText(/1–8 de 8 proyectos/, {}, { timeout: 5000 })).toBeInTheDocument();
    await waitFor(() => expect(nav.get()).toBe(''));
    // The page of the URL (404) and the first one; not the 59 in between.
    const pages = asked.map((query) => new URLSearchParams(query).get('page'));
    expect([...new Set(pages)]).toEqual(['60', '1']);
    expect(pages.length).toBeLessThanOrEqual(3);
  });

  it('con una página en pantalla, una muy pasada del final va a la última que esa página conoce', async () => {
    startScenario('muchos-proyectos');
    const asked = watchListRequests();
    renderScreen(<ProjectsScreen />);
    await screen.findByText(/1–25 de 64 proyectos/);
    // Someone pastes a page number in the address bar (or follows an old link) without leaving the screen.
    nav.set('pagina=60');
    expect(await screen.findByText(/51–64 de 64 proyectos/, {}, { timeout: 5000 })).toBeInTheDocument();
    expect(nav.get()).toBe('pagina=3');
    const pages = asked.map((query) => new URLSearchParams(query).get('page'));
    expect(pages.filter((page) => page !== '1' && page !== '60' && page !== '3')).toEqual([]);
    expect(pages.length).toBeLessThanOrEqual(4);
  });

  it('borrar la única fila de la última página vuelve a la anterior, sin error', async () => {
    startScenario('muchos-proyectos');
    keepProjects(26);
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await screen.findByText(/1–25 de 26 proyectos/);
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await screen.findByText(/26–26 de 26 proyectos/);
    const title = titles()[0] ?? '';
    await user.click(within(rows()[0] as HTMLElement).getByRole('button', { name: /Más acciones/ }));
    await user.click(screen.getByRole('menuitem', { name: 'Borrar' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Borrar proyecto' }));

    expect(await screen.findByText(/1–25 de 25 proyectos/)).toBeInTheDocument();
    expect(rows()).toHaveLength(25);
    expect(titles()).not.toContain(title);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reintentar' })).not.toBeInTheDocument();
  });

  it('si otra persona borra proyectos mientras estás en la página 3, retrocede hasta una que exista', async () => {
    startScenario('muchos-proyectos');
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await screen.findByText(/1–25 de 64 proyectos/);
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await screen.findByText(/26–50 de 64 proyectos/);
    keepProjects(10);
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    expect(await screen.findByText(/1–10 de 10 proyectos/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('una búsqueda nueva arranca en la primera página sin pedir la página vieja', async () => {
    startScenario('muchos-proyectos');
    const asked = watchListRequests();
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await screen.findByText(/1–25 de 64 proyectos/);
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await screen.findByText(/26–50 de 64 proyectos/);
    await user.type(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' }), '1');
    await waitFor(() => expect(asked.some((search) => search.includes('search=1'))).toBe(true));
    await screen.findByText(/^1–/);
    expect(asked.filter((search) => search.includes('search=1')).every((search) => search.includes('page=1&'))).toBe(true);
  });
});

describe('B2 · vacío', () => {
  it('cerrar «Primeros pasos» termina el recorrido de la cuenta (tour_completed)', async () => {
    startScenario('workspace-vacio');
    const db = loadDb();
    const user = db.users.find((item) => item.id === db.sessionUserId);
    if (!user) throw new Error('El escenario no tiene usuario');
    const pointer = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    const steps = await screen.findByRole('complementary', { name: 'Primeros pasos' });
    expect(loadDb().users.find((item) => item.id === user.id)?.tour_completed).toBe(false);
    await pointer.click(within(steps).getByRole('button', { name: 'Cerrar la lista de primeros pasos' }));
    await waitFor(() => expect(loadDb().users.find((item) => item.id === user.id)?.tour_completed).toBe(true));
    expect(screen.queryByRole('complementary', { name: 'Primeros pasos' })).not.toBeInTheDocument();
  });

  it('con el recorrido ya terminado en el servidor, «Primeros pasos» no aparece en ningún navegador', async () => {
    // Martín (rol Admin) terminó el recorrido; nothing is stored in this browser.
    startScenario('rol-admin');
    server.use(http.get(`${API_URL}/api/v1/projects/`, () => HttpResponse.json(EMPTY_PAGE)));
    renderScreen(<ProjectsScreen />);
    await screen.findByRole('heading', { name: 'Todavía no hay proyectos' });
    expect(screen.getAllByRole('link', { name: /«/ })).toHaveLength(3);
    expect(screen.queryByRole('complementary', { name: 'Primeros pasos' })).not.toBeInTheDocument();
    expect(window.localStorage.getItem('oc.proyectos.primeros-pasos.oculto')).toBeNull();
  });

  it('rol Lector: los pedidos de ejemplo se ven, deshabilitados', async () => {
    startScenario('rol-lector');
    server.use(http.get(`${API_URL}/api/v1/projects/`, () => HttpResponse.json(EMPTY_PAGE)));
    renderScreen(<ProjectsScreen />);
    await screen.findByRole('heading', { name: 'Todavía no hay proyectos' });
    const examples = screen.getAllByRole('link', { name: /«/ });
    expect(examples).toHaveLength(3);
    for (const example of examples) {
      expect(example).toHaveAttribute('aria-disabled', 'true');
      expect(example).not.toHaveAttribute('href');
      expect(example).toHaveAttribute('title', 'Pedile acceso a un admin');
    }
    expect(screen.getByRole('button', { name: 'Subí tu primer video' })).toHaveAttribute('aria-disabled', 'true');
  });
});
