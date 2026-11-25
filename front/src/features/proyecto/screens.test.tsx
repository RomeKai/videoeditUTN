import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { server } from '@/api/mocks/server';
import { ActivityScreen } from './ActivityScreen';
import { ClipsScreen } from './ClipsScreen';
import { ProjectShell } from './ProjectShell';
import { ResultScreen } from './ResultScreen';
import { SummaryScreen } from './SummaryScreen';
import { projectId, renderScreen, signIn } from './test-utils';
import { TranscriptScreen } from './TranscriptScreen';

const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), pathname: '/proyectos' }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace, back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));
// jsdom cannot follow a download link; the screens only need to hand it the URL.
const downloads = vi.hoisted(() => ({ url: vi.fn(), text: vi.fn() }));
vi.mock('@/lib/download', async (original) => ({ ...(await original<typeof import('@/lib/download')>()), downloadUrl: downloads.url, downloadText: downloads.text }));

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  vi.clearAllMocks();
});
afterAll(() => server.close());

function open(title: string, tab: '' | 'clips' | 'resultado' | 'transcripcion' | 'actividad', scenario = 'todos-los-estados', user: Parameters<typeof signIn>[1] = 'lucia') {
  signIn(scenario, user);
  const id = projectId(title);
  nav.pathname = `/proyectos/${id}${tab ? `/${tab}` : ''}`;
  const content =
    tab === 'clips' ? <ClipsScreen projectId={id} /> : tab === 'resultado' ? <ResultScreen projectId={id} /> : tab === 'transcripcion' ? <TranscriptScreen projectId={id} /> : tab === 'actividad' ? <ActivityScreen projectId={id} /> : <SummaryScreen projectId={id} />;
  renderScreen(<ProjectShell projectId={id}>{content}</ProjectShell>);
  return { id, user: userEvent.setup() };
}

describe('D1 · Proyecto', () => {
  it('procesando: cabecera, pasos y pestañas que todavía no se pueden abrir', async () => {
    open('Clase 3', '');
    expect(await screen.findByRole('heading', { level: 1, name: 'Clase 3: Finanzas personales' })).toBeInTheDocument();
    expect(screen.getAllByText('Transcribiendo').length).toBeGreaterThan(0);
    expect(screen.getByText(/Paso 2 de 4 · faltan ≈/)).toBeInTheDocument();
    expect(screen.getByText('1.404,90 tokens reservados')).toBeInTheDocument();
    const steps = screen.getByRole('list', { name: 'Etapas del proceso' });
    expect(within(steps).getByText('Transcribir').closest('[aria-current="step"]')).not.toBeNull();
    expect(within(steps).getByText('Necesita tu revisión')).toBeInTheDocument();
    const tabs = screen.getByRole('navigation', { name: 'Secciones del proyecto' });
    expect(within(tabs).getByText('Clips').closest('[aria-disabled="true"]')).not.toBeNull();
    expect(within(tabs).getByRole('link', { name: 'Actividad' })).toBeInTheDocument();
    // Nothing to do yet: the header has no primary action.
    expect(screen.queryByRole('link', { name: 'Revisar clips' })).toBeNull();
  });

  it('en espera por demanda: lo dice en ámbar y aclara que la reserva se devuelve si falla', async () => {
    open('En espera en la transcripción', '');
    expect(await screen.findByText(/En espera hace \d+ min\./)).toBeInTheDocument();
    expect(screen.getByText('Hay mucha demanda. Tu video sigue en la fila y arranca solo; no tenés que hacer nada.')).toBeInTheDocument();
    expect(screen.getByText(/se devuelven si falla/)).toBeInTheDocument();
  });

  it('listo para revisar: la acción principal lleva a la Revisión', async () => {
    const { id } = open('Podcast Ep. 42', '');
    const cta = await screen.findByRole('link', { name: 'Revisar clips' });
    expect(cta).toHaveAttribute('href', `/proyectos/${id}/revision`);
    expect(screen.getByText(/Falta tu revisión\./)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Clips sugeridos' })).toBeInTheDocument();
  });

  it.each([
    ['Sin revisar: no alcanzó', 'No alcanzó el saldo para renderizar sin revisar', 'Comprar tokens'],
    ['Sin revisar: el plan', 'Tu plan no permite renderizar estos clips sin revisar', 'Ver planes'],
  ])('«Renderizar sin revisar» detenido (%s)', async (title, notice, link) => {
    open(title, '');
    expect(await screen.findByText(notice)).toBeInTheDocument();
    expect(screen.getByText(/Revisá y aprobá los clips que quieras\./)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: link })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Revisar clips' })).toBeInTheDocument();
  });

  it('«Renderizar sin revisar» en curso: avisa que el render quedó reservado', async () => {
    open('Sin revisar: render reservado', '');
    expect(await screen.findByText('Reservamos el render de los clips')).toBeInTheDocument();
  });

  it('error: mensaje de la sección 4, devolución, contacto y «Crear de nuevo»', async () => {
    const { id } = open('Falló: AINoSpeechDetectedError', '');
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('No encontramos voz en el video. OneCreator elige clips a partir de lo que se dice.')).toBeInTheDocument();
    expect(within(alert).getByText(/Te devolvimos/)).toBeInTheDocument();
    expect(within(alert).getByRole('link', { name: /Ver en Movimientos/ })).toHaveAttribute('href', '/uso/movimientos');
    expect(within(alert).getByRole('link', { name: /Escribir a soporte/ })).toHaveAttribute('href', expect.stringContaining('mailto:'));
    expect(within(alert).getByRole('link', { name: 'Crear de nuevo' })).toHaveAttribute('href', `/proyectos/nuevo?desde=${id}`);
    expect(screen.getByText(/Se detuvo en el paso 2/)).toBeInTheDocument();
    expect(screen.getByText('3 pasos no llegaron a empezar')).toBeInTheDocument();
  });

  it('error de moderación: no dice la categoría y ofrece escribirnos', async () => {
    open('Falló: UnsafeContentError', '');
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText(/no cumple nuestras políticas/)).toBeInTheDocument();
    expect(within(alert).getByRole('link', { name: /Escribinos/ })).toBeInTheDocument();
  });

  it('listo: tira de clips, proceso plegado y lo que aplicó el prompt', async () => {
    const { id } = open('Charla en la UTN', '');
    expect(await screen.findByRole('link', { name: 'Ver clips' })).toHaveAttribute('href', `/proyectos/${id}/clips`);
    expect(screen.getByText('Listo con errores')).toBeInTheDocument();
    expect(screen.getByText('Creado con IA')).toBeInTheDocument();
    expect(screen.getByText('Lo que aplicamos')).toBeInTheDocument();
    expect(screen.getByText('Layout Fondo desenfocado')).toBeInTheDocument();
    expect(screen.getByText('Tu plan no lo incluye.')).toBeInTheDocument();
    expect(screen.getByText('Ver pasos')).toBeInTheDocument();
  });

  it('renombrar desde el lápiz guarda el título nuevo', async () => {
    const { user } = open('Vlog Mendoza', '');
    await user.click(await screen.findByRole('button', { name: 'Renombrar el proyecto' }));
    const dialog = screen.getByRole('dialog', { name: 'Renombrar el proyecto' });
    const input = within(dialog).getByLabelText('Nombre');
    await user.clear(input);
    await user.type(input, 'Vlog Mendoza, día 2');
    await user.click(within(dialog).getByRole('button', { name: 'Guardar' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Vlog Mendoza, día 2' })).toBeInTheDocument();
    expect(screen.getByText('Proyecto renombrado')).toBeInTheDocument();
  });

  it('borrar: el diálogo avisa que no se devuelve nada y vuelve a Proyectos', async () => {
    const { user } = open('Vlog Mendoza', '');
    await user.click(await screen.findByRole('button', { name: /Más acciones: renombrar/ }));
    await user.click(screen.getByRole('menuitem', { name: 'Borrar' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText(/Se borran el video, los clips y las publicaciones programadas/)).toBeInTheDocument();
    expect(within(dialog).getByText(/No se devuelve nada de lo ya cobrado/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Borrar proyecto' }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith('/proyectos'));
  });

  it('Lector: el menú y renombrar quedan deshabilitados, no ocultos', async () => {
    open('Vlog Mendoza', '', 'rol-lector', 'diego');
    expect(await screen.findByRole('button', { name: /Más acciones: renombrar/ })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('button', { name: 'Renombrar el proyecto' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('Editor: puede borrar sus proyectos y no los de otros', async () => {
    const mine = open('Vlog Mendoza', '', 'rol-editor', 'carla');
    await mine.user.click(await screen.findByRole('button', { name: /Más acciones: renombrar/ }));
    expect(screen.getByRole('menuitem', { name: 'Borrar' })).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('Editor: «Borrar» de un proyecto ajeno queda deshabilitado', async () => {
    const other = open('Podcast Ep. 42', '', 'rol-editor', 'carla');
    await other.user.click(await screen.findByRole('button', { name: /Más acciones: renombrar/ }));
    expect(screen.getByRole('menuitem', { name: 'Borrar' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('menuitem', { name: 'Renombrar' })).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('un proyecto que no existe muestra el 404', async () => {
    signIn('estudio-norte');
    renderScreen(
      <ProjectShell projectId="00000007-0000-4000-8000-00000000ffff">
        <p>contenido</p>
      </ProjectShell>,
    );
    expect(await screen.findByRole('heading', { name: /Esta página no existe/ })).toBeInTheDocument();
    expect(screen.queryByText('contenido')).toBeNull();
  });

  it('servidor caído: banner con Reintentar y un título', async () => {
    signIn('servidor-caido');
    renderScreen(
      <ProjectShell projectId="00000007-0000-4000-8000-000000000005">
        <p>contenido</p>
      </ProjectShell>,
    );
    expect(await screen.findByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Proyecto' })).toBeInTheDocument();
  });
});

describe('D3 · Clips', () => {
  it('muestra las tarjetas con su estado y dónde están publicadas; en esta pestaña no hay acción principal', async () => {
    open('Vlog Mendoza', 'clips', 'estudio-norte');
    const list = await screen.findByRole('list');
    expect(within(list).getAllByRole('listitem')).toHaveLength(6);
    expect(screen.queryByRole('link', { name: 'Ver clips' })).toBeNull();
    expect(screen.getByRole('group', { name: 'Filtrar clips por estado' })).toBeInTheDocument();
    expect(within(list).getAllByText('Programado').length).toBeGreaterThan(0);
  });

  it('filtra por estado', async () => {
    const { user } = open('Vlog Mendoza', 'clips', 'estudio-norte');
    const chips = await screen.findByRole('group', { name: 'Filtrar clips por estado' });
    await user.click(within(chips).getByRole('button', { name: /^Publicados/ }));
    const items = within(screen.getByRole('list')).getAllByRole('listitem');
    expect(items.length).toBeGreaterThan(0);
    for (const item of items) expect(within(item).getByText('Publicado')).toBeInTheDocument();
  });

  it('seleccionar varios y publicar abre el panel con ?publicar=<ids>', async () => {
    const { user, id } = open('Vlog Mendoza', 'clips', 'estudio-norte');
    await user.click(await screen.findByRole('button', { name: 'Seleccionar' }));
    const bar = screen.getByRole('toolbar', { name: 'Acciones para los clips seleccionados' });
    expect(within(bar).getByText(/seleccionados/)).toHaveTextContent('0 seleccionados');
    expect(within(bar).getByRole('button', { name: 'Publicar' })).toBeDisabled();
    const boxes = screen.getAllByRole('checkbox');
    await user.click(boxes[0] as HTMLElement);
    await user.click(boxes[1] as HTMLElement);
    expect(within(bar).getByText(/seleccionados/)).toHaveTextContent('2 seleccionados');
    await user.click(within(bar).getByRole('button', { name: 'Publicar' }));
    expect(nav.push).toHaveBeenCalledTimes(1);
    expect(nav.push.mock.calls[0]?.[0]).toMatch(new RegExp(`^/proyectos/${id}/clips\\?publicar=[0-9a-f-]{36},[0-9a-f-]{36}$`));
    await user.click(within(bar).getByRole('button', { name: 'Cancelar' }));
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
  });

  it('descargar un clip pide el link firmado y lo baja', async () => {
    const { user } = open('Vlog Mendoza', 'clips', 'estudio-norte');
    const buttons = await screen.findAllByRole('button', { name: /^Descargar «/ });
    await user.click(buttons[0] as HTMLElement);
    await waitFor(() => expect(downloads.url).toHaveBeenCalledWith(expect.stringMatching(/^data:video\/mp4/), expect.stringMatching(/\.mp4$/)));
    expect(await screen.findByText('Clip descargado')).toBeInTheDocument();
  });

  it('«Descargar todo» baja el .zip', async () => {
    const { user } = open('Vlog Mendoza', 'clips', 'estudio-norte');
    await user.click(await screen.findByRole('button', { name: /Descargar todo/ }));
    await waitFor(() => expect(downloads.url).toHaveBeenCalledWith(expect.stringMatching(/^data:application\/zip/), 'vlog-mendoza.zip'));
  });

  it('cada clip listo se puede publicar y editar', async () => {
    const { user, id } = open('Vlog Mendoza', 'clips', 'estudio-norte');
    const first = (await screen.findAllByRole('listitem'))[0] as HTMLElement;
    expect(within(first).getByRole('link', { name: /^Editar «/ })).toHaveAttribute('href', expect.stringMatching(new RegExp(`^/proyectos/${id}/clips/[0-9a-f-]{36}$`)));
    await user.click(within(first).getByRole('button', { name: 'Publicar' }));
    expect(nav.push.mock.calls[0]?.[0]).toMatch(/\?publicar=[0-9a-f-]{36}$/);
  });

  it('un clip con error se reintenta sin gastar tokens', async () => {
    const { user } = open('Charla en la UTN', 'clips', 'estudio-norte');
    expect(await screen.findByText('No se pudo generar. Reintentar no gasta tokens.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText('Reintentando el clip. No gasta tokens.')).toBeInTheDocument();
    expect(await screen.findByText('Renderizando…')).toBeInTheDocument();
  });

  it('borrar un clip pide confirmación', async () => {
    const { user } = open('Vlog Mendoza', 'clips', 'estudio-norte');
    const first = (await screen.findAllByRole('listitem'))[0] as HTMLElement;
    await user.click(within(first).getByRole('button', { name: /^Más acciones de/ }));
    await user.click(screen.getByRole('menuitem', { name: 'Borrar' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText(/se cancelan sus publicaciones programadas/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Borrar clip' }));
    expect(await screen.findByText('Clip borrado')).toBeInTheDocument();
    await waitFor(() => expect(within(screen.getByRole('list')).getAllByRole('listitem')).toHaveLength(5));
  });

  it('clips sugeridos: se puede pedir la vista previa gratis (PTE-03)', async () => {
    const { user } = open('Podcast Ep. 42', 'clips', 'estudio-norte');
    expect(await screen.findByText('Estos clips todavía no se renderizaron')).toBeInTheDocument();
    const first = screen.getAllByRole('listitem')[0] as HTMLElement;
    expect(within(first).getByText('Sugerido')).toBeInTheDocument();
    await user.click(within(first).getByRole('button', { name: 'Vista previa' }));
    const dialog = await screen.findByRole('dialog', { name: 'Vista previa' });
    expect(await within(dialog).findByText(/En la fila…|Armando la vista previa…/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Es gratis y no reserva tokens/)).toBeInTheDocument();
  });

  it('Lector (R2): descarga, pero Seleccionar, Publicar y Editar quedan deshabilitados con el motivo', async () => {
    open('Vlog Mendoza', 'clips', 'rol-lector', 'diego');
    expect(await screen.findByRole('button', { name: 'Seleccionar' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('button', { name: /Descargar todo/ })).not.toHaveAttribute('aria-disabled');
    const first = screen.getAllByRole('listitem')[0] as HTMLElement;
    expect(within(first).getByRole('button', { name: 'Publicar' })).toHaveAttribute('aria-disabled', 'true');
    expect(within(first).getByRole('button', { name: /^Editar «/ })).toHaveAttribute('aria-disabled', 'true');
    expect(within(first).getByRole('button', { name: /^Descargar «/ })).toBeEnabled();
  });

  it('sin clips todavía: estado vacío con el camino al proceso', async () => {
    open('Paso 1', 'clips');
    expect(await screen.findByText('Todavía no hay clips')).toBeInTheDocument();
  });

  it('revisión sin sugerencias: invita a elegir los momentos', async () => {
    const { id } = open('Revisión sin sugerencias', 'clips');
    expect(await screen.findByText('No encontramos momentos para sugerir')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Elegir momentos' })).toHaveAttribute('href', `/proyectos/${id}/revision`);
  });

  it('un video completo no tiene Clips: va a Resultado', async () => {
    const { id } = open('Tutorial de Notion', 'clips', 'estudio-norte');
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith(`/proyectos/${id}/resultado`));
  });
});

describe('D1e · Transcripción', () => {
  it('busca, cuenta las coincidencias y avanza con Enter', async () => {
    const { user } = open('Vlog Mendoza', 'transcripcion', 'estudio-norte');
    const input = await screen.findByRole('searchbox', { name: 'Buscar en la transcripción' });
    await user.type(input, 'clientes');
    const status = await screen.findByText(/^1 de \d+$/);
    const total = Number(status.textContent?.split(' de ')[1]);
    expect(total).toBeGreaterThan(1);
    expect(document.querySelectorAll('mark').length).toBeGreaterThan(0);
    expect(document.querySelectorAll('mark[data-current]')).toHaveLength(1);
    await user.keyboard('{Enter}');
    expect(await screen.findByText(`2 de ${total}`)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Resultado anterior' }));
    expect(await screen.findByText(`1 de ${total}`)).toBeInTheDocument();
  });

  it('sin resultados: lo dice y deja el texto como estaba', async () => {
    const { user } = open('Vlog Mendoza', 'transcripcion', 'estudio-norte');
    await user.type(await screen.findByRole('searchbox'), 'aconcagua');
    expect(await screen.findByText('No encontramos «aconcagua» en este video.')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Transcripción completa' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Borrar la búsqueda' }));
    await waitFor(() => expect(screen.queryByText(/No encontramos «/)).toBeNull());
  });

  it('marca los tramos que se volvieron clip y exporta .txt y .srt', async () => {
    const { user } = open('Vlog Mendoza', 'transcripcion', 'estudio-norte');
    expect((await screen.findAllByRole('group', { name: /^Tramo del clip «/ })).length).toBeGreaterThan(0);
    expect(screen.getByText('Dónde están los clips')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Descargar .txt' }));
    expect(downloads.text).toHaveBeenCalledWith(expect.any(String), 'vlog-mendoza.txt');
    await user.click(screen.getByRole('button', { name: 'Descargar .srt' }));
    expect(downloads.text).toHaveBeenLastCalledWith(expect.stringMatching(/^1\n00:00:0\d,\d{3} --> /), 'vlog-mendoza.srt', expect.any(String));
  });

  it('todavía sin transcribir, o fallido antes: estado vacío', async () => {
    open('Paso 1', 'transcripcion');
    expect(await screen.findByText('Todavía no hay transcripción')).toBeInTheDocument();
  });
});

describe('D1f · Actividad', () => {
  it('lista quién hizo qué por día, los movimientos y cómo se calculó el costo', async () => {
    open('Vlog Mendoza', 'actividad', 'estudio-norte');
    expect(await screen.findByText('Quién hizo qué, de lo más nuevo a lo más viejo')).toBeInTheDocument();
    expect(screen.getAllByText(/Creó el proyecto/).length).toBe(1);
    const table = screen.getByRole('table', { name: 'Movimientos de tokens del proyecto' });
    expect(within(table).getByText('Cobrado')).toBeInTheDocument();
    expect(within(table).getByText('Reservado')).toBeInTheDocument();
    expect(await screen.findByText('Total cobrado')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Ver todos los movimientos del workspace/ })).toHaveAttribute('href', '/uso/movimientos');
  });

  it('un proyecto fallido muestra la devolución', async () => {
    open('Webinar de lanzamiento', 'actividad', 'estudio-norte');
    const table = await screen.findByRole('table', { name: 'Movimientos de tokens del proyecto' });
    expect(within(table).getByText('Devuelto')).toBeInTheDocument();
    expect(await screen.findByText('Total devuelto')).toBeInTheDocument();
    expect(screen.getByText('1 devolución en este proyecto.')).toBeInTheDocument();
  });

  it('Editor: ve la actividad, pero no entra a los movimientos del workspace', async () => {
    open('Vlog Mendoza', 'actividad', 'rol-editor', 'carla');
    expect(await screen.findByRole('button', { name: /Ver todos los movimientos del workspace/ })).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('D5 · Resultado del video completo', () => {
  it('un reproductor, los detalles y Descargar como acción principal', async () => {
    const { user } = open('Tutorial de Notion', 'resultado', 'estudio-norte');
    expect(await screen.findByRole('group', { name: /^Reproductor: Tutorial de Notion/ })).toBeInTheDocument();
    expect(screen.getByText('Este proyecto no tiene revisión: editamos el video completo.')).toBeInTheDocument();
    expect(screen.getByText('9:16 vertical')).toBeInTheDocument();
    expect(screen.getByText('1080 × 1920')).toBeInTheDocument();
    expect(screen.getByText('Minimalista')).toBeInTheDocument();
    expect(within(screen.getByRole('navigation', { name: 'Secciones del proyecto' })).getByRole('link', { name: 'Resultado' })).toHaveAttribute('aria-current', 'page');
    await user.click(screen.getByRole('button', { name: 'Descargar' }));
    await waitFor(() => expect(downloads.url).toHaveBeenCalled());
    expect(await screen.findByText('Video descargado')).toBeInTheDocument();
  });

  it('Publicar abre el panel con el único clip', async () => {
    const { user, id } = open('Tutorial de Notion', 'resultado', 'estudio-norte');
    await user.click(await screen.findByRole('button', { name: /^Publicar/ }));
    expect(nav.push.mock.calls[0]?.[0]).toMatch(new RegExp(`^/proyectos/${id}/resultado\\?publicar=[0-9a-f-]{36}$`));
  });

  it('volver a renderizar con otro estilo muestra el costo antes de confirmar', async () => {
    const { user } = open('Tutorial de Notion', 'resultado', 'estudio-norte');
    await user.click(await screen.findByRole('button', { name: /Volver a renderizar con otro estilo/ }));
    const style = await screen.findByRole('dialog', { name: 'Volver a renderizar con otro estilo' });
    await user.selectOptions(within(style).getByLabelText('Estilo'), 'hormozi');
    await waitFor(() => expect(within(style).getByRole('button', { name: 'Ver el costo' })).toBeEnabled());
    await user.click(within(style).getByRole('button', { name: 'Ver el costo' }));
    const cost = await screen.findByRole('dialog', { name: 'Volver a renderizar' });
    expect(within(cost).getByText('Total')).toBeInTheDocument();
    expect(within(cost).getByText('Saldo después')).toBeInTheDocument();
    await user.click(within(cost).getByRole('button', { name: 'Renderizar' }));
    expect(await screen.findByText('Render en marcha. Te avisamos cuando termine.')).toBeInTheDocument();
    expect(await screen.findByText('Estamos editando tu video…')).toBeInTheDocument();
  });

  it('con saldo bajo, volver a renderizar abre «No te alcanza el saldo»', async () => {
    signIn('saldo-bajo');
    const { loadDb, saveDb } = await import('@/api/mocks/state');
    const db = loadDb();
    const wallet = db.wallets.find((item) => item.available_balance > 0);
    if (wallet) wallet.available_balance = 1;
    saveDb(db);
    const id = (await import('./test-utils')).projectId('Tutorial de Notion');
    nav.pathname = `/proyectos/${id}/resultado`;
    renderScreen(
      <ProjectShell projectId={id}>
        <ResultScreen projectId={id} />
      </ProjectShell>,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Volver a renderizar con otro estilo/ }));
    const style = await screen.findByRole('dialog', { name: 'Volver a renderizar con otro estilo' });
    await waitFor(() => expect(within(style).getByRole('button', { name: 'Ver el costo' })).toBeEnabled());
    await user.click(within(style).getByRole('button', { name: 'Ver el costo' }));
    await user.click(within(await screen.findByRole('dialog', { name: 'Volver a renderizar' })).getByRole('button', { name: 'Renderizar' }));
    expect(await screen.findByText('No te alcanza el saldo')).toBeInTheDocument();
  });

  it('un video sin voz se edita igual y avisa que salió sin subtítulos', async () => {
    open('Video sin voz', 'resultado');
    expect(await screen.findByText('No encontramos voz en el video: se editó igual, sin subtítulos.')).toBeInTheDocument();
  });

  it('mientras renderiza: sin vista previa y sin acciones todavía', async () => {
    open('Video completo renderizando', 'resultado');
    expect(await screen.findByText('Estamos editando tu video…')).toBeInTheDocument();
    expect(screen.getByText(/No hay vista previa: el video final sale enseguida/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Publicar/ })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.queryByRole('button', { name: 'Descargar' })).toBeNull();
  });

  it('si el render falló: la devolución y Reintentar', async () => {
    const { user } = open('Video completo que no se pudo generar', 'resultado');
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('El video no se pudo generar')).toBeInTheDocument();
    expect(within(alert).getByText(/Te devolvimos/)).toBeInTheDocument();
    await user.click(within(alert).getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText('Reintentando el video. No gasta tokens.')).toBeInTheDocument();
  });

  it('un proyecto de clips no tiene Resultado: va a Clips', async () => {
    const { id } = open('Vlog Mendoza', 'resultado', 'estudio-norte');
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith(`/proyectos/${id}/clips`));
  });
});
