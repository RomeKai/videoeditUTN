import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { server } from '@/api/mocks/server';
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

const table = () => screen.findByRole('table', { name: 'Proyectos del workspace' });
const rowOf = async (title: string) => {
  const link = await screen.findByRole('link', { name: new RegExp(title) });
  return link.closest('tr, li') as HTMLElement;
};

describe('B2 · Proyectos', () => {
  it('lista los proyectos con su estado de la sección 3 y su acción principal', async () => {
    startScenario('estudio-norte');
    renderScreen(<ProjectsScreen />);
    expect(screen.getByRole('heading', { level: 1, name: 'Proyectos' })).toBeInTheDocument();
    await table();

    const review = await rowOf('Podcast Ep. 42');
    expect(within(review).getByText('Listo para revisar')).toBeInTheDocument();
    expect(within(review).getByText('Encontramos 9 clips')).toBeInTheDocument();
    expect(within(review).getByRole('link', { name: 'Revisar clips' })).toHaveAttribute('href', expect.stringMatching(/\/proyectos\/.+\/revision$/));

    const transcribing = await rowOf('Clase 3');
    expect(within(transcribing).getByText('Transcribiendo')).toBeInTheDocument();
    // 52:02 of video: the mock estimates 390 s.
    expect(within(transcribing).getByText(/^Paso 2 de 4/)).toHaveTextContent('Paso 2 de 4 · ≈ 7 min');

    const waiting = await rowOf('Entrevista a Paula');
    expect(within(waiting).getByText('En espera')).toBeInTheDocument();
    expect(within(waiting).getByText('Hay mucha demanda. Arranca solo.')).toHaveAttribute('title', expect.stringContaining('no tenés que hacer nada'));
    // Waiting for capacity has no estimate, whatever `eta_seconds` says.
    expect(waiting).not.toHaveTextContent('≈');

    const failed = await rowOf('Webinar de lanzamiento');
    expect(within(failed).getByText('Error')).toBeInTheDocument();
    expect(within(failed).getByText(/Te devolvimos 1\.755 tokens/)).toBeInTheDocument();
    expect(within(failed).getByRole('link', { name: 'Crear de nuevo' })).toHaveAttribute('href', expect.stringMatching(/\/proyectos\/nuevo\?desde=/));

    const link = await rowOf('Stream Valorant');
    expect(within(link).getByText('Link de YouTube:')).toBeInTheDocument();
    expect(within(link).getByText('4 de 8 clips listos')).toBeInTheDocument();

    expect(screen.getByText('Los proyectos en proceso se actualizan solos cada 5 s.')).toBeInTheDocument();
    expect(screen.getByText(/1–8 de 8 proyectos/)).toBeInTheDocument();
  });

  it('filtra por estado y busca por título', async () => {
    startScenario('estudio-norte');
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await table();
    const filters = screen.getByRole('group', { name: 'Filtrar por estado' });
    expect(within(filters).getByRole('button', { name: /Todos/ })).toHaveAttribute('aria-pressed', 'true');

    await user.click(within(filters).getByRole('button', { name: /Con error/ }));
    await waitFor(() => expect(screen.queryByRole('link', { name: /Podcast Ep. 42/ })).not.toBeInTheDocument());
    expect(screen.getByRole('link', { name: /Webinar de lanzamiento/ })).toBeInTheDocument();

    await user.click(within(filters).getByRole('button', { name: /Todos/ }));
    await user.type(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' }), 'vlog');
    await waitFor(() => expect(screen.queryByRole('link', { name: /Webinar/ })).not.toBeInTheDocument());
    expect(screen.getByRole('link', { name: /Vlog Mendoza/ })).toBeInTheDocument();

    await user.clear(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' }));
    await user.type(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' }), 'zzz');
    expect(await screen.findByText('Ningún proyecto coincide con «zzz»')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Ver todos los proyectos' }));
    expect(await screen.findByRole('link', { name: /Webinar/ })).toBeInTheDocument();
  });

  it('pagina de a 25 y ordena', async () => {
    startScenario('muchos-proyectos');
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await table();
    expect(screen.getByText(/1–25 de 64 proyectos/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    expect(await screen.findByText(/26–50 de 64 proyectos/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Orden: más recientes' }));
    await user.click(screen.getByRole('menuitemradio', { name: 'Título (A–Z)' }));
    expect(await screen.findByText(/1–25 de 64 proyectos/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Orden: título (a–z)' })).toBeInTheDocument();
  });

  it('el filtro, el orden, la página y la búsqueda van a la URL, sin los valores por defecto', async () => {
    startScenario('muchos-proyectos');
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await table();
    expect(nav.get()).toBe('');

    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    await waitFor(() => expect(nav.get()).toBe('pagina=2'));
    await user.click(screen.getByRole('button', { name: 'Orden: más recientes' }));
    await user.click(screen.getByRole('menuitemradio', { name: 'Título (A–Z)' }));
    // Another order starts on the first page: the page leaves the URL with it.
    await waitFor(() => expect(nav.get()).toBe('orden=title'));
    const filters = screen.getByRole('group', { name: 'Filtrar por estado' });
    await user.click(within(filters).getByRole('button', { name: /Listos/ }));
    await waitFor(() => expect(nav.get()).toBe('orden=title&estado=ready'));
    await user.type(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' }), 'proyecto 1');
    await waitFor(() => expect(new URLSearchParams(nav.get()).get('buscar')).toBe('proyecto 1'));

    // Back to the defaults: nothing of the list is left in the URL.
    await user.clear(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' }));
    await user.click(within(filters).getByRole('button', { name: /Todos/ }));
    await user.click(screen.getByRole('button', { name: 'Orden: título (a–z)' }));
    await user.click(screen.getByRole('menuitemradio', { name: 'Más recientes' }));
    await waitFor(() => expect(nav.get()).toBe(''));
  });

  it('al cargar, la URL devuelve la página, el filtro, el orden y la búsqueda (una recarga no los pierde)', async () => {
    startScenario('muchos-proyectos');
    const user = userEvent.setup();
    const first = renderScreen(<ProjectsScreen />);
    await table();
    await user.click(screen.getByRole('button', { name: 'Orden: más recientes' }));
    await user.click(screen.getByRole('menuitemradio', { name: 'Más antiguos' }));
    await waitFor(() => expect(nav.get()).toBe('orden=created_at'));
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    expect(await screen.findByText(/26–50 de 64 proyectos/)).toBeInTheDocument();
    const shown = (await screen.findAllByRole('row')).map((row) => row.textContent);
    expect(nav.get()).toBe('orden=created_at&pagina=2');
    first.unmount();

    // The reload: a new screen, the same URL.
    renderScreen(<ProjectsScreen />);
    expect(await screen.findByText(/26–50 de 64 proyectos/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Orden: más antiguos' })).toBeInTheDocument();
    expect((await screen.findAllByRole('row')).map((row) => row.textContent)).toEqual(shown);
    expect(nav.get()).toBe('orden=created_at&pagina=2');
  });

  it('abre con el filtro y la búsqueda de un link, y deja como están los parámetros que no son suyos', async () => {
    startScenario('estudio-norte');
    nav.set('espacio=abc&estado=failed&buscar=webinar&orden=cualquiera&pagina=-3');
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await table();
    expect(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' })).toHaveValue('webinar');
    const filters = screen.getByRole('group', { name: 'Filtrar por estado' });
    expect(within(filters).getByRole('button', { name: /Con error/ })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByRole('link', { name: /Webinar de lanzamiento/ })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Podcast Ep. 42/ })).not.toBeInTheDocument();
    // What the screen does not know (an order that does not exist, page -3) is the default.
    expect(screen.getByRole('button', { name: 'Orden: más recientes' })).toBeInTheDocument();

    await user.click(within(filters).getByRole('button', { name: /Todos/ }));
    await waitFor(() => expect(new URLSearchParams(nav.get()).get('estado')).toBeNull());
    expect(new URLSearchParams(nav.get()).get('espacio')).toBe('abc');
    expect(new URLSearchParams(nav.get()).get('buscar')).toBe('webinar');
  });

  it('una página de la URL que ya no existe vuelve a la última que hay', async () => {
    startScenario('estudio-norte');
    nav.set('pagina=9');
    renderScreen(<ProjectsScreen />);
    expect(await screen.findByText(/1–8 de 8 proyectos/, {}, { timeout: 5000 })).toBeInTheDocument();
    await waitFor(() => expect(nav.get()).toBe(''));
  });

  it('un link a Proyectos sin parámetros, estando en la lista filtrada, la deja como al entrar', async () => {
    startScenario('estudio-norte');
    nav.set('estado=failed&buscar=webinar');
    renderScreen(<ProjectsScreen />);
    await table();
    expect(screen.queryByRole('link', { name: /Podcast Ep. 42/ })).not.toBeInTheDocument();

    // What the sidebar link does: the same path, no query.
    nav.set('');

    expect(await screen.findByRole('link', { name: /Podcast Ep. 42/ })).toBeInTheDocument();
    expect(screen.getByRole('searchbox', { name: 'Buscar proyectos por título' })).toHaveValue('');
    // And the search that was typed is not written back.
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(nav.get()).toBe('');
  });

  it('recuerda la vista de grilla en el navegador', async () => {
    startScenario('estudio-norte');
    const user = userEvent.setup();
    const first = renderScreen(<ProjectsScreen />);
    await table();
    await user.click(screen.getByRole('button', { name: 'Vista de grilla' }));
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Proyectos del workspace' })).toBeInTheDocument();
    expect(window.localStorage.getItem('oc.proyectos.vista')).toBe('"grid"');
    first.unmount();

    renderScreen(<ProjectsScreen />);
    expect(await screen.findByRole('list', { name: 'Proyectos del workspace' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Vista de grilla' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('renombra y borra desde el menú de la fila', async () => {
    startScenario('estudio-norte');
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await table();

    await user.click(screen.getByRole('button', { name: /Más acciones de Vlog Mendoza/ }));
    const menu = screen.getByRole('menu', { name: 'Más acciones de Vlog Mendoza' });
    expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Abrir', 'Renombrar', 'Duplicar configuración', 'Borrar']);
    await user.click(within(menu).getByRole('menuitem', { name: 'Renombrar' }));
    const dialog = await screen.findByRole('dialog', { name: 'Renombrar proyecto' });
    const input = within(dialog).getByRole('textbox', { name: 'Nombre' });
    await user.clear(input);
    await user.click(within(dialog).getByRole('button', { name: 'Guardar' }));
    expect(within(dialog).getByText('Ponele un nombre al proyecto.')).toBeInTheDocument();
    await user.type(input, 'Vlog Córdoba');
    await user.click(within(dialog).getByRole('button', { name: 'Guardar' }));
    expect(await screen.findByRole('link', { name: /Vlog Córdoba/ })).toBeInTheDocument();
    expect(screen.getByText('Proyecto renombrado')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Más acciones de Vlog Córdoba/ }));
    await user.click(screen.getByRole('menuitem', { name: 'Borrar' }));
    const confirm = await screen.findByRole('alertdialog');
    expect(within(confirm).getByText('Se borran el video, los clips y las publicaciones programadas. No se puede deshacer.')).toBeInTheDocument();
    await user.click(within(confirm).getByRole('button', { name: 'Borrar proyecto' }));
    await waitFor(() => expect(screen.queryByRole('link', { name: /Vlog Córdoba/ })).not.toBeInTheDocument());
    expect(screen.getByText(/1–7 de 7 proyectos/)).toBeInTheDocument();
  });

  it('rol Lector: las acciones que no puede hacer quedan deshabilitadas, no ocultas', async () => {
    startScenario('rol-lector');
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await table();
    const failed = await rowOf('Webinar de lanzamiento');
    expect(within(failed).getByRole('button', { name: 'Crear de nuevo' })).toHaveAttribute('aria-disabled', 'true');
    await user.click(screen.getByRole('button', { name: /Más acciones de Vlog Mendoza/ }));
    expect(screen.getByRole('menuitem', { name: 'Abrir' })).not.toHaveAttribute('aria-disabled');
    for (const name of ['Renombrar', 'Duplicar configuración', 'Borrar']) {
      const item = screen.getByRole('menuitem', { name });
      expect(item).toHaveAttribute('aria-disabled', 'true');
      expect(item).toHaveAttribute('title', 'Pedile acceso a un admin');
    }
  });

  it('rol Editor: borra solo sus proyectos', async () => {
    startScenario('rol-editor');
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    await table();
    await user.click(screen.getByRole('button', { name: /Más acciones de Vlog Mendoza/ }));
    expect(screen.getByRole('menuitem', { name: 'Borrar' })).not.toHaveAttribute('aria-disabled');
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: /Más acciones de Podcast Ep. 42/ }));
    expect(screen.getByRole('menuitem', { name: 'Borrar' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('menuitem', { name: 'Renombrar' })).not.toHaveAttribute('aria-disabled');
  });

  it('workspace vacío: una frase, una acción, tres pedidos de ejemplo y los primeros pasos', async () => {
    startScenario('workspace-vacio');
    const user = userEvent.setup();
    renderScreen(<ProjectsScreen />);
    expect(await screen.findByRole('heading', { name: 'Todavía no hay proyectos' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Subí tu primer video' })).toHaveAttribute('href', '/proyectos/nuevo');
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument();
    const example = screen.getByRole('link', { name: /Lo más gracioso/ });
    expect(example).toHaveAttribute('href', expect.stringContaining('/proyectos/nuevo?origen=ia&prompt=Sac%C3%A1%205%20clips'));
    expect(screen.getAllByRole('link', { name: /«/ })).toHaveLength(3);

    const steps = screen.getByRole('complementary', { name: 'Primeros pasos' });
    expect(within(steps).getAllByRole('listitem')).toHaveLength(4);
    await user.click(within(steps).getByRole('button', { name: 'Cerrar la lista de primeros pasos' }));
    expect(screen.queryByRole('complementary', { name: 'Primeros pasos' })).not.toBeInTheDocument();
  });

  it('plan Gratis: «Invitar a alguien» lleva a Miembros, porque el plan tiene 2 lugares', async () => {
    startScenario('usuario-nuevo');
    const { queryClient } = renderScreen(<ProjectsScreen />);
    const steps = await screen.findByRole('complementary', { name: 'Primeros pasos' });
    // The step reads the plan and the plan list: only once both arrived does it say where it goes.
    await waitFor(() => expect(queryClient.isFetching()).toBe(0));
    const invite = within(steps).getByRole('link', { name: /Invitar a alguien/ });
    expect(invite).toHaveAttribute('href', '/ajustes/miembros');
    expect(within(invite).queryByText(/Disponible en el plan/)).not.toBeInTheDocument();
  });

  it('servidor caído: banner con Reintentar', async () => {
    startScenario('servidor-caido');
    renderScreen(<ProjectsScreen />);
    const alert = await screen.findByRole('alert', {}, { timeout: 5000 });
    expect(within(alert).getByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Proyectos' })).toBeInTheDocument();
  });
});
