import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { server } from '@/api/mocks/server';
import { loadDb, saveDb } from '@/api/mocks/state';
import { NewProjectScreen } from './NewProjectScreen';
import { allowSlowScreens, renderScreen, startScenario } from '@/testing';

allowSlowScreens();

const location = vi.hoisted(() => ({ search: '' }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/proyectos/nuevo',
  useSearchParams: () => new URLSearchParams(location.search),
  useParams: () => ({}),
}));

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  location.search = '';
});
afterAll(() => server.close());

/** Gives a seeded project a configuration that no starting point has, and returns its id. */
function prepareProject(title: string): string {
  const db = loadDb();
  const project = db.projects.find((item) => item.title === title);
  if (!project) throw new Error(`No hay proyecto «${title}» en el escenario`);
  Object.assign(project, {
    aspect_ratio: '1:1',
    editing_style: 'hormozi',
    render_layout: 'split',
    gameplay_position: 'left',
    camera_selection_position: 'right',
    manual_camera_x: 0.3,
    manual_camera_y: 0.6,
    manual_camera_zoom: 2,
    add_subtitles: true,
    subtitle_size: 'small',
    subtitle_position: 'top',
    subtitle_color: '#FFFFFF',
    subtitle_words_per_segment: 5,
    remove_silences: true,
    auto_render_bypass: true,
    intelligence_level: 'smart',
  });
  project.metadata = { ...project.metadata, max_clips: 14, selection_criteria: 'Solo los consejos', render_resolution: '1080p' };
  saveDb(db);
  return project.id;
}

describe('C1 · ?desde=<proyecto>', () => {
  it('copia la configuración del proyecto, pero no su origen', async () => {
    startScenario('estudio-norte');
    location.search = `desde=${prepareProject('Webinar de lanzamiento')}`;
    const user = userEvent.setup();
    renderScreen(<NewProjectScreen />);

    const notice = await screen.findByText(/Configuración copiada de/);
    expect(notice).toHaveTextContent('Configuración copiada de Webinar de lanzamiento.');
    // The source is not copied: the user picks the video again.
    expect(screen.getByLabelText('Elegir video')).toBeInTheDocument();
    expect(screen.queryByTestId('file-row')).not.toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Crear proyecto' })).toBeDisabled();

    expect(screen.getByRole('radio', { name: /Como «Webinar de lanzamiento»/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: /1:1/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: /1080p/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('textbox', { name: 'Cantidad máxima' })).toHaveValue('14');
    expect(screen.getByRole('textbox', { name: /Qué buscás/ })).toHaveValue('Solo los consejos');
    expect(screen.getByRole('button', { name: 'Inteligente' })).toHaveAttribute('aria-pressed', 'true');

    expect(screen.getByRole('button', { name: /^Estilo/ })).toHaveTextContent('Hormozi · Dividido');
    expect(screen.getByRole('button', { name: /^Subtítulos/ })).toHaveTextContent('Activados · chicos · arriba · blanco · 5 palabras por línea');
    expect(screen.getByRole('button', { name: /^Inteligencia/ })).toHaveTextContent(/quitar silencios · renderiza sin revisar/i);

    await user.click(screen.getByRole('button', { name: /^Estilo/ }));
    await user.click(screen.getByRole('button', { name: /Avanzado/ }));
    expect(screen.getByRole('group', { name: 'Posición del gameplay' })).toContainElement(screen.getAllByRole('button', { name: 'Izquierda', pressed: true })[0] ?? null);
    expect(screen.getByRole('switch', { name: 'Cámara manual' })).toBeChecked();
    expect(screen.getByRole('slider', { name: 'Zoom' })).toHaveValue('2');
    expect(screen.getByRole('slider', { name: 'Centro horizontal' })).toHaveValue('0.3');

    await user.click(screen.getByRole('button', { name: /^Inteligencia/ }));
    expect(screen.getByRole('switch', { name: 'Renderizar sin revisar' })).toBeChecked();
  });

  it('un proyecto que no existe se ignora sin avisos', async () => {
    startScenario('estudio-norte');
    location.search = 'desde=00000000-0000-4000-8000-00000000dead';
    renderScreen(<NewProjectScreen />);
    await screen.findByText('Costo estimado');
    // Give the failed lookup time to settle.
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(screen.queryByText(/Configuración copiada/)).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /^Podcast/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('heading', { level: 1, name: 'Nuevo proyecto' })).toBeInTheDocument();
  });
});
