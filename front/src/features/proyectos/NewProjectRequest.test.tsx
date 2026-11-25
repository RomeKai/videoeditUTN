import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { toFormData } from '@/api/client';
import { API_URL } from '@/api/config';
import * as contract from '@/api/contracts/projects';
import { ApiError, NetworkError } from '@/api/errors';
import { server } from '@/api/mocks/server';
import { NewProjectScreen } from './NewProjectScreen';
import { allowSlowScreens, renderScreen, startScenario } from './test-utils';

interface Call {
  body: Record<string, unknown>;
  prompt?: string;
  onUploadProgress?: (loaded: number, total: number) => void;
}
interface MutateOptions {
  onError?: (error: unknown) => void;
}

/** The creation is replaced by a recorder: the tests read what C1 would send, and decide how the request ends. */
const fake = vi.hoisted(() => ({ calls: [] as Call[], options: [] as MutateOptions[], pending: false }));
vi.mock('@/api/hooks', async (original) => ({
  ...((await original()) as Record<string, unknown>),
  useCreateProject: () => ({
    mutate: (variables: Call, options: MutateOptions = {}) => {
      fake.calls.push(variables);
      fake.options.push(options);
    },
    isPending: fake.pending,
    isSuccess: false,
  }),
}));

allowSlowScreens();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  fake.calls.length = 0;
  fake.options.length = 0;
  fake.pending = false;
});
afterAll(() => server.close());

const video = (name: string) => new File(['x'.repeat(64)], name, { type: 'video/mp4' });
const LINK = 'https://www.youtube.com/watch?v=k3Vn8sQx2Lw';

/** The multipart fields exactly as the client puts them on the wire, after the contract's schema. */
function wire(call: Call | undefined): Record<string, string> {
  if (!call) throw new Error('C1 no mandó nada');
  const parsed = (call.prompt !== undefined ? contract.CreateFromPromptRequest.parse({ ...call.body, prompt: call.prompt }) : contract.CreateProjectRequest.parse(call.body)) as Record<string, unknown>;
  const fields: Record<string, string> = {};
  for (const [key, value] of toFormData(parsed).entries()) fields[key] = typeof value === 'string' ? value : `<archivo ${(value as File).name}>`;
  return fields;
}

async function open(scenario = 'estudio-norte') {
  startScenario(scenario);
  const user = userEvent.setup({ applyAccept: false });
  renderScreen(<NewProjectScreen />);
  await screen.findByText('Costo estimado');
  return user;
}

async function pickFile(user: ReturnType<typeof userEvent.setup>, name = 'corto-8min.mp4') {
  await user.upload(screen.getByLabelText('Elegir video'), video(name));
  await screen.findByText(/Entra en tu plan/);
}

async function pasteLink(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole('textbox', { name: 'Link del video' }), LINK);
  await screen.findByTestId('link-row');
}

const create = () => screen.getByRole('button', { name: 'Crear proyecto' });
const form = () => screen.getByRole('form', { name: 'Configuración del proyecto' });

describe('C1 · el pedido que se manda', () => {
  it('archivo con el punto de partida por defecto', async () => {
    const user = await open();
    await pickFile(user);
    await user.click(create());
    expect(fake.calls).toHaveLength(1);
    expect(fake.calls[0]?.prompt).toBeUndefined();
    expect(wire(fake.calls[0])).toEqual({
      source_file: '<archivo corto-8min.mp4>',
      title: 'corto-8min',
      project_type: 'repurpose',
      aspect_ratio: '9:16',
      render_resolution: '720p',
      editing_style: 'dynamic',
      render_layout: 'active',
      add_subtitles: 'true',
      subtitle_size: 'large',
      subtitle_position: 'bottom',
      subtitle_color: '#FFFF00',
      subtitle_words_per_segment: '3',
      use_facetracking: 'true',
      remove_silences: 'true',
      use_seo: 'false',
      max_clips: '8',
      intelligence_level: 'fast',
      auto_render_bypass: 'false',
      brand_kit: expect.stringMatching(/^[0-9a-f-]{36}$/),
    });
  });

  it('video completo con Dividido: sin opciones de clips, con el bloque Avanzado', async () => {
    const user = await open();
    await pickFile(user);
    await user.click(screen.getByRole('radio', { name: /^Gaming/ }));
    await user.click(screen.getByRole('radio', { name: /Editar el video completo/ }));
    await user.click(screen.getByRole('button', { name: /^Subtítulos/ }));
    await user.click(screen.getByRole('switch', { name: 'Mostrar subtítulos' }));
    await user.click(create());
    const sent = wire(fake.calls[0]);
    expect(sent).toMatchObject({
      project_type: 'single_edit',
      render_layout: 'split',
      editing_style: 'hormozi',
      gameplay_position: 'center',
      camera_selection_position: 'center',
      manual_camera_x: '0.5',
      manual_camera_y: '0.2',
      manual_camera_zoom: '1',
      // Turned off by the user: it travels as false, never left to the default.
      add_subtitles: 'false',
    });
    for (const key of ['max_clips', 'intelligence_level', 'auto_render_bypass', 'selection_criteria', 'video_url']) expect(sent).not.toHaveProperty(key);
  });

  it('link: siempre en 720p, aunque la configuración diga otra cosa', async () => {
    const user = await open();
    await user.click(screen.getByRole('radio', { name: /1080p/ }));
    await user.click(screen.getByRole('tab', { name: /Pegar link/ }));
    await pasteLink(user);
    await user.click(create());
    const sent = wire(fake.calls[0]);
    expect(sent).toMatchObject({ video_url: LINK, title: 'Video de YouTube', render_resolution: '720p', project_type: 'repurpose', add_subtitles: 'true' });
    expect(sent).not.toHaveProperty('source_file');
  });

  it('describir con IA: el video, el texto y el SEO; add_subtitles no viaja y el texto puede apagar los subtítulos', async () => {
    const user = await open();
    await user.click(screen.getByRole('tab', { name: /Describir con IA/ }));
    await user.type(screen.getByRole('textbox', { name: 'Contanos qué querés' }), 'Sacá 5 clips sin subtítulos');
    await user.click(screen.getByRole('button', { name: 'Link' }));
    await pasteLink(user);
    await user.click(create());
    expect(fake.calls[0]?.prompt).toBe('Sacá 5 clips sin subtítulos');
    expect(wire(fake.calls[0])).toEqual({ video_url: LINK, title: 'Video de YouTube', use_seo: 'false', prompt: 'Sacá 5 clips sin subtítulos' });
  });

  it('el contrato: add_subtitles tiene valor por defecto al crear, no en from-prompt', () => {
    expect(contract.CreateProjectRequest.parse({ video_url: LINK })).toHaveProperty('add_subtitles', true);
    expect(contract.CreateFromPromptRequest.parse({ video_url: LINK, prompt: 'sin subtítulos' })).not.toHaveProperty('add_subtitles');
    expect(contract.CreateFromPromptRequest.parse({ video_url: LINK, prompt: 'x', add_subtitles: false })).toHaveProperty('add_subtitles', false);
    expect(contract.CreateFromPromptRequest.safeParse({ prompt: 'x' }).success).toBe(false);
  });
});

describe('C1 · Enter nunca crea el proyecto', () => {
  it('rol Lector: enviar el formulario no manda nada', async () => {
    const user = await open('rol-lector');
    await pickFile(user);
    expect(create()).toHaveAttribute('aria-disabled', 'true');
    await user.type(screen.getByRole('textbox', { name: 'Cantidad máxima' }), '{Enter}');
    // What the browser does on Enter in a form with one text field (implicit submission).
    expect(fireEvent.submit(form())).toBe(false);
    await user.click(create());
    expect(fake.calls).toHaveLength(0);
  });

  it('saldo insuficiente: enviar el formulario no saltea «Comprar tokens»', async () => {
    const user = await open('saldo-bajo');
    await user.upload(screen.getByLabelText('Elegir video'), video('podcast-ep42-72min.mp4'));
    await within(await screen.findByRole('complementary', { name: 'Resumen del proyecto' })).findByRole('link', { name: 'Comprar tokens' });
    fireEvent.submit(form());
    await user.type(screen.getByRole('textbox', { name: 'Cantidad máxima' }), '{Enter}');
    expect(fake.calls).toHaveLength(0);
  });

  it('dueña con todo listo: Enter en un campo tampoco arranca la subida; el botón sí', async () => {
    const user = await open();
    await pickFile(user);
    await user.type(screen.getByRole('textbox', { name: 'Cantidad máxima' }), '{Enter}');
    fireEvent.submit(form());
    expect(fake.calls).toHaveLength(0);
    await user.click(create());
    expect(fake.calls).toHaveLength(1);
  });

  it('describir con IA + link: Enter en el campo del link no crea', async () => {
    const user = await open();
    await user.click(screen.getByRole('tab', { name: /Describir con IA/ }));
    await user.type(screen.getByRole('textbox', { name: 'Contanos qué querés' }), 'hola');
    await user.click(screen.getByRole('button', { name: 'Link' }));
    await pasteLink(user);
    await user.type(screen.getByRole('textbox', { name: 'Link del video' }), '{Enter}');
    expect(fake.calls).toHaveLength(0);
  });

  it('sin video, el botón tampoco manda nada', async () => {
    const user = await open();
    expect(create()).toBeDisabled();
    fireEvent.click(create());
    await user.click(create());
    expect(fake.calls).toHaveLength(0);
  });
});

describe('C1 · un solo pedido por creación', () => {
  it('dos clics seguidos mandan uno solo, y después de un error se puede reintentar', async () => {
    const user = await open();
    await pickFile(user);
    // Same task: `isPending` has not changed yet when the second click arrives.
    fireEvent.click(create());
    fireEvent.click(create());
    await user.dblClick(create());
    expect(fake.calls).toHaveLength(1);

    act(() => fake.options[0]?.onError?.(new ApiError(500, 'projects.create', {})));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    await user.click(create());
    expect(fake.calls).toHaveLength(2);
  });
});

describe('C1 · errores al crear', () => {
  it('un 400 en «¿Qué buscás?» queda junto al campo', async () => {
    const user = await open();
    await pickFile(user);
    const criteria = screen.getByRole('textbox', { name: /Qué buscás/ });
    expect(criteria).toHaveAttribute('maxlength', '300');
    await user.type(criteria, 'consejos');
    await user.click(create());
    act(() => fake.options[0]?.onError?.(new ApiError(400, 'projects.create', { selection_criteria: ['Asegúrese de que este campo no tenga más de 300 caracteres.'] })));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Usá hasta 300 caracteres.');
    expect(criteria).toHaveAttribute('aria-invalid', 'true');
    expect(criteria.closest('div')).toContainElement(alert);
    await user.type(criteria, ' concretos');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('403 de SEO con IA: «Cambiar opciones» marca el interruptor de SEO', async () => {
    const user = await open();
    await user.click(screen.getByRole('tab', { name: /Describir con IA/ }));
    await user.type(screen.getByRole('textbox', { name: 'Contanos qué querés' }), 'hola');
    await pickFile(user);
    await user.click(screen.getByRole('switch', { name: 'SEO, suma 5 tokens' }));
    await user.click(create());
    expect(wire(fake.calls[0])).toMatchObject({ use_seo: 'true' });
    act(() => fake.options[0]?.onError?.(new ApiError(403, 'projects.createFromPrompt', { error: 'SEO Optimization is not allowed in your current plan.', code: 'seo' })));
    const dialog = await screen.findByRole('alertdialog', { name: 'Tu plan no incluye SEO' });
    await user.click(within(dialog).getByRole('button', { name: 'Cambiar opciones' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Tu plan no incluye SEO. Apagalo o mirá los planes.');
    await user.click(screen.getByRole('switch', { name: 'SEO, suma 5 tokens' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('la conexión se corta con el video ya enviado: no dice que no se creó nada', async () => {
    const user = await open();
    await pickFile(user);
    await user.click(create());
    act(() => fake.calls[0]?.onUploadProgress?.(64, 64));
    act(() => fake.options[0]?.onError?.(new NetworkError('projects.create')));
    const row = await screen.findByTestId('file-row');
    await waitFor(() => expect(within(row).getByRole('alert')).toHaveTextContent('Se cortó la conexión cuando el video ya estaba subido.'));
    expect(within(row).getByText(/Puede que el proyecto se haya creado/)).toBeInTheDocument();
  });

  it('la conexión se corta a mitad de la subida: dice el porcentaje y ofrece reintentar', async () => {
    const user = await open();
    await pickFile(user);
    await user.click(create());
    act(() => fake.calls[0]?.onUploadProgress?.(32, 64));
    act(() => fake.options[0]?.onError?.(new NetworkError('projects.create')));
    const row = await screen.findByTestId('file-row');
    await waitFor(() => expect(within(row).getByRole('alert')).toHaveTextContent('Se cortó la subida al 50 %.'));
    await user.click(within(row).getByRole('button', { name: 'Reintentar' }));
    expect(fake.calls).toHaveLength(2);
  });
});

describe('C1 · salir durante la subida', () => {
  it('un link de la app pide confirmación antes de cancelar la subida', async () => {
    const user = await open();
    await pickFile(user);
    fireEvent.click(create());
    // From here the request is on its way; the first progress event re-renders the screen with the upload in flight.
    fake.pending = true;
    act(() => fake.calls[0]?.onUploadProgress?.(16, 64));
    await screen.findByRole('status', { name: 'Subiendo el video' });

    await user.click(screen.getByRole('link', { name: 'Proyectos' }));
    const dialog = await screen.findByRole('alertdialog', { name: '¿Salir y cancelar la subida?' });
    await user.click(within(dialog).getByRole('button', { name: 'Seguir subiendo' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Subiendo el video' })).toBeInTheDocument();

    await user.click(screen.getByRole('link', { name: 'Proyectos' }));
    expect(await screen.findByRole('alertdialog', { name: '¿Salir y cancelar la subida?' })).toBeInTheDocument();
  });
});

describe('C1 · links sin `probe-link` en el backend', () => {
  it.each([404, 405])('con un %i acepta el link al precio fijo, sin datos del video', async (status) => {
    startScenario('estudio-norte');
    server.use(http.post(`${API_URL}/api/v1/projects/probe-link/`, () => HttpResponse.json({ detail: 'No encontrado.' }, { status })));
    const user = userEvent.setup();
    renderScreen(<NewProjectScreen />);
    await screen.findByText('Costo estimado');
    await user.click(screen.getByRole('tab', { name: /Pegar link/ }));
    await user.type(screen.getByRole('textbox', { name: 'Link del video' }), LINK);
    expect(await screen.findByText(/Lo revisamos al crear el proyecto/)).toBeInTheDocument();
    expect(screen.queryByTestId('link-row')).not.toBeInTheDocument();
    const side = screen.getByRole('complementary', { name: 'Resumen del proyecto' });
    await waitFor(() => expect(within(side).getByTestId('cost-total')).toHaveTextContent('10'));
    await user.click(create());
    const sent = wire(fake.calls[0]);
    expect(sent).toMatchObject({ video_url: LINK, render_resolution: '720p' });
    expect(sent).not.toHaveProperty('title');
  });
});
