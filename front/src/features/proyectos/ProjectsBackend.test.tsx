import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { API_URL } from '@/api/config';
import type { Project } from '@/api/contracts/projects';
import { serializeProject } from '@/api/mocks/serialize';
import { server } from '@/api/mocks/server';
import { loadDb, saveDb } from '@/api/mocks/state';
import { ProjectsScreen } from './ProjectsScreen';
import { applyListQuery } from './project-view';
import { allowSlowScreens, renderScreen, startScenario } from './test-utils';

allowSlowScreens();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const rows = () => within(screen.getByRole('table', { name: 'Proyectos del workspace' })).getAllByRole('row').slice(1);
const titles = () => rows().map((row) => row.querySelector('a')?.textContent?.replace(/^(Archivo|Link[^:]*): /, '') ?? '');

/** The list endpoint as the backend answers today: every project in one array, whatever the query says. */
function answerBareArray(limit = 30): { queries: string[]; projects: Project[] } {
  const db = loadDb();
  const workspace = db.projects[0]?.workspace;
  const projects = db.projects
    .filter((project) => project.workspace === workspace)
    .slice(0, limit)
    .map((project) => serializeProject(db, project, { full: false })) as Project[];
  const queries: string[] = [];
  server.use(
    http.get(`${API_URL}/api/v1/projects/`, ({ request }) => {
      queries.push(new URL(request.url).search);
      return HttpResponse.json(projects);
    }),
  );
  return { queries, projects };
}

describe('B2 contra el backend de hoy (un array sin paginar, que ignora la consulta)', () => {
  it('pagina, filtra, busca y ordena en el navegador, y cuenta los filtros', async () => {
    startScenario('muchos-proyectos');
    const { projects } = answerBareArray(30);
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await screen.findByText(/1–25 de 30 proyectos/);
    expect(rows()).toHaveLength(25);

    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await screen.findByText(/26–30 de 30 proyectos/);
    expect(rows()).toHaveLength(5);

    // The chips count what the browser has, since the answer brings no counts.
    const expected = applyListQuery(projects, { group: 'all', term: '', ordering: '-created_at', page: 1, pageSize: 25 }).counts;
    const filters = screen.getByRole('group', { name: 'Filtrar por estado' });
    expect(within(filters).getByRole('button', { name: /Todos/ })).toHaveTextContent(`Todos${expected.all}`);
    expect(within(filters).getByRole('button', { name: /Con error/ })).toHaveTextContent(`Con error${expected.failed}`);
    expect(expected.all).toBe(30);
    expect(expected.in_progress + expected.to_review + expected.ready + expected.failed).toBe(30);

    const group = expected.ready > 0 ? ({ name: /Listos/, key: 'ready' } as const) : ({ name: /En proceso/, key: 'in_progress' } as const);
    await user.click(within(filters).getByRole('button', { name: group.name }));
    await waitFor(() => expect(rows()).toHaveLength(Math.min(25, expected[group.key])));
    expect(screen.getByText(new RegExp(`de ${expected[group.key]} proyectos`))).toBeInTheDocument();
    await user.click(within(filters).getByRole('button', { name: /Todos/ }));

    const sample = projects[7]?.title ?? '';
    await user.type(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' }), sample);
    const matching = projects.filter((project) => project.title.toLowerCase().includes(sample.toLowerCase())).length;
    await waitFor(() => expect(rows()).toHaveLength(matching));
    expect(titles()).toContain(sample);

    await user.clear(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' }));
    await user.type(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' }), 'zzzz');
    expect(await screen.findByText('Ningún proyecto coincide con «zzzz»')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Ver todos los proyectos' }));
    await screen.findByText(/1–25 de 30 proyectos/);

    await user.click(screen.getByRole('button', { name: 'Orden: más recientes' }));
    await user.click(screen.getByRole('menuitemradio', { name: 'Título (A–Z)' }));
    const sorted = [...projects].sort((a, b) => a.title.localeCompare(b.title, 'es')).map((project) => project.title);
    await waitFor(() => expect(titles()).toEqual(sorted.slice(0, 25)));
  });

  it('sin proyectos muestra el estado vacío', async () => {
    startScenario('muchos-proyectos');
    server.use(http.get(`${API_URL}/api/v1/projects/`, () => HttpResponse.json([])));
    renderScreen(<ProjectsScreen />);
    expect(await screen.findByRole('heading', { name: 'Todavía no hay proyectos' })).toBeInTheDocument();
  });
});

describe('applyListQuery', () => {
  it('acota la página pedida a la última que existe', () => {
    startScenario('muchos-proyectos');
    const { projects } = answerBareArray(30);
    const page = applyListQuery(projects, { group: 'all', term: '', ordering: 'created_at', page: 9, pageSize: 25 });
    expect(page.page).toBe(2);
    expect(page.results).toHaveLength(5);
    expect(page.results[0]?.created_at.localeCompare(page.results[4]?.created_at ?? '')).toBeLessThanOrEqual(0);
    expect(applyListQuery([], { group: 'failed', term: 'x', ordering: 'title', page: 3, pageSize: 25 })).toMatchObject({ page: 1, count: 0, results: [] });
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
    const asked: string[] = [];
    server.events.on('request:start', ({ request }) => {
      const url = new URL(request.url);
      if (request.method === 'GET' && url.pathname === '/api/v1/projects/') asked.push(url.search);
    });
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await screen.findByText(/1–25 de 64 proyectos/);
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await screen.findByText(/26–50 de 64 proyectos/);
    await user.type(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' }), '1');
    await waitFor(() => expect(asked.some((search) => search.includes('search=1'))).toBe(true));
    await screen.findByText(/^1–/);
    expect(asked.filter((search) => search.includes('search=1')).every((search) => search.includes('page=1&'))).toBe(true);
    server.events.removeAllListeners();
  });
});

describe('B2 · vacío', () => {
  it('cerrar «Primeros pasos» termina el recorrido de la cuenta (tour_completed)', async () => {
    startScenario('workspace-vacio');
    const db = loadDb();
    const user = db.users.find((item) => item.id === db.sessionUserId);
    if (!user) throw new Error('El escenario no tiene usuario');
    user.tour_completed = false;
    saveDb(db);
    const pointer = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    const steps = await screen.findByRole('complementary', { name: 'Primeros pasos' });
    expect(loadDb().users.find((item) => item.id === user.id)?.tour_completed).toBe(false);
    await pointer.click(within(steps).getByRole('button', { name: 'Cerrar la lista de primeros pasos' }));
    await waitFor(() => expect(loadDb().users.find((item) => item.id === user.id)?.tour_completed).toBe(true));
    expect(screen.queryByRole('complementary', { name: 'Primeros pasos' })).not.toBeInTheDocument();
  });

  it('rol Lector: los pedidos de ejemplo se ven, deshabilitados', async () => {
    startScenario('rol-lector');
    server.use(http.get(`${API_URL}/api/v1/projects/`, () => HttpResponse.json({ count: 0, next: null, previous: null, results: [], status_counts: { all: 0, in_progress: 0, to_review: 0, ready: 0, failed: 0 } })));
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
