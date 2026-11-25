import { configure, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiCall } from '@/api/client';
import * as projects from '@/api/contracts/projects';
import * as clips from '@/api/contracts/clips';
import { ApiError } from '@/api/errors';
import { mid, KIND } from '@/api/mocks/db';
import { server } from '@/api/mocks/server';
import { PLAN_IDS } from '@/api/mocks/seed';
import { keys } from '@/api/query';
import { loadDb } from '@/api/mocks/state';
import { ClipEditorScreen, PROJECT_RENDERING_MESSAGE } from './ClipEditorScreen';
import { ReviewScreen } from './ReviewScreen';
import { toggleWord } from './components/TranscriptPanel';
import { formatEdge } from './lib/time';
import { appQueryClient, editDb, renderScreen, signIn } from './test-utils';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  usePathname: () => '/proyectos',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const REVIEW = mid(KIND.project, 3); // Podcast Ep. 42: awaiting approval, 9 suggestions
const RENDERING = mid(KIND.project, 4); // Stream Valorant: rendering
const READY = mid(KIND.project, 5); // Vlog Mendoza: completed, 1080p
const WHOLE = mid(KIND.project, 7); // Tutorial de Notion: whole-video project
const NO_SUGGESTIONS = mid(KIND.project, 27); // scenario todos-los-estados
const clipOf = (project: number, n: number) => mid(KIND.clip, project * 100 + n);

// The review renders an hour of transcript word by word: jsdom needs more than the default 5 s when the whole suite runs.
vi.setConfig({ testTimeout: 40_000 });
configure({ asyncUtilTimeout: 10_000 });

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  vi.clearAllMocks();
});
afterAll(() => server.close());

const project = (id: string) => loadDb().projects.find((item) => item.id === id);
const bar = () => screen.getByRole('region', { name: 'Acciones' });

/** Selects words `from`..`to` of the transcript the way the browser does, and lets the panel read it. */
function selectWords(from: number, to: number) {
  const spans = document.querySelectorAll('[data-w]');
  const range = document.createRange();
  range.setStart(spans[from]?.firstChild as Node, 0);
  range.setEnd(spans[to]?.firstChild as Node, 1);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  fireEvent.mouseUp(spans[to] as Element);
}

describe('D2 · Revisión de clips', () => {
  beforeEach(() => signIn('estudio-norte'));

  it('muestra las sugerencias con puntaje y el costo de lo incluido', async () => {
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Podcast Ep. 42 — Cómo vender sin vender' })).toBeInTheDocument();
    const list = screen.getByRole('region', { name: 'Clips sugeridos' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(9);
    // The weak ones start excluded.
    expect(within(list).getAllByRole('checkbox', { checked: true })).toHaveLength(5);
    await waitFor(() => expect(bar()).toHaveTextContent('5 clips seleccionados'));
    await waitFor(() => expect(bar()).toHaveTextContent(/− 10 % del plan Pro/));
    expect(screen.getByRole('button', { name: 'Renderizar 5 clips' })).toBeEnabled();
  });

  it('incluir y excluir cambia el resumen, también con el teclado', async () => {
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    const list = await screen.findByRole('region', { name: 'Clips sugeridos' });
    await user.click(within(list).getAllByRole('checkbox', { checked: false })[0] as HTMLElement);
    await waitFor(() => expect(bar()).toHaveTextContent('6 clips seleccionados'));
    // X excludes the selected clip, ↓ moves to the next one, A includes that one back.
    fireEvent.keyDown(window, { key: 'x' });
    await waitFor(() => expect(bar()).toHaveTextContent('5 clips seleccionados'));
    fireEvent.keyDown(window, { key: 'ArrowDown' });
    fireEvent.keyDown(window, { key: 'x' });
    await waitFor(() => expect(bar()).toHaveTextContent('4 clips seleccionados'));
    fireEvent.keyDown(window, { key: 'a' });
    await waitFor(() => expect(bar()).toHaveTextContent('5 clips seleccionados'));
    expect(within(list).getAllByRole('listitem')[1]).toHaveAttribute('aria-current', 'true');
  });

  it('el campo de inicio acepta un tiempo escrito y lo lleva a una palabra', async () => {
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    const start = (await screen.findByRole('textbox', { name: /Inicio/ })) as HTMLInputElement;
    const before = start.value;
    await user.clear(start);
    await user.type(start, 'no es un tiempo{Enter}');
    expect(start.value).toBe(before);
    const end = screen.getByRole('textbox', { name: /Fin/ }) as HTMLInputElement;
    const duration = () => screen.getByText('Duración').parentElement?.textContent;
    const was = duration();
    await user.clear(end);
    await user.type(end, '9:59:59{Enter}');
    // Past the end of the video: it stops at the last word.
    await waitFor(() => expect(duration()).not.toBe(was));
    expect(end.value).not.toBe('9:59:59');
  });

  it('aprobar muestra el costo, reserva y lleva a Clips', async () => {
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    await user.click(await screen.findByRole('button', { name: 'Renderizar 5 clips' }));
    const dialog = await screen.findByRole('dialog', { name: 'Renderizar 5 clips' });
    expect(dialog).toHaveTextContent('Saldo después');
    await user.click(within(dialog).getByRole('button', { name: 'Renderizar' }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith(`/proyectos/${REVIEW}/clips`));
    expect(project(REVIEW)?.status).toBe('rendering');
    expect(project(REVIEW)?.clips).toHaveLength(5);
  });

  it('manda cada segmento aprobado con su texto, en orden y con el id del clip', async () => {
    let body: projects.ApprovePaperEditRequest | null = null;
    server.use(
      http.post('*/api/v1/projects/:id/approve-paper-edit/', async ({ request }) => {
        body = (await request.json()) as projects.ApprovePaperEditRequest;
        return HttpResponse.json({ message: 'ok', project_id: REVIEW, final_cost: '92.70', transaction_id: mid(KIND.transaction, 999) });
      }),
    );
    const data = await apiCall(projects.getPaperEditData, { params: { id: REVIEW } });
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    await user.click(await screen.findByRole('button', { name: 'Renderizar 5 clips' }));
    await user.click(within(await screen.findByRole('dialog', { name: 'Renderizar 5 clips' })).getByRole('button', { name: 'Renderizar' }));
    await waitFor(() => expect(body).not.toBeNull());
    const segments = (body as unknown as projects.ApprovePaperEditRequest).approved_segments;
    expect(segments).toHaveLength(5);
    expect(segments.map((segment) => segment.start)).toEqual([...segments.map((segment) => segment.start)].sort((a, b) => a - b));
    const suggestions = new Map(data.ai_suggestions.map((item) => [item.id, item]));
    for (const segment of segments) {
      const suggestion = suggestions.get(segment.clip_id ?? '');
      expect(suggestion).toBeDefined();
      expect(segment).toMatchObject({ start: suggestion?.start_time, end: suggestion?.end_time, title: suggestion?.title });
      // Subtitles are built from this text and nothing else: it is what is said inside the range.
      const said = (data.transcript ?? []).filter((word) => word.start >= segment.start - 0.01 && word.start < segment.end - 0.01).map((word) => word.text);
      expect(said.length).toBeGreaterThan(0);
      expect(segment.text).toBe(said.join(' '));
    }
  });

  it('más clips de lo que el plan renderiza abre «Límite del plan» antes de confirmar', async () => {
    signIn('plan-gratis');
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    const end = (await screen.findByRole('textbox', { name: /Fin/ })) as HTMLInputElement;
    await user.clear(end);
    // The free plan renders up to 10 minutes: one clip of 20 is over it.
    await user.type(end, '25:00{Enter}');
    await user.click(await screen.findByRole('button', { name: 'Renderizar 5 clips' }));
    const limit = await screen.findByRole('alertdialog', { name: 'Este video supera el límite de tu plan' });
    expect(limit).toHaveTextContent(/renderiza hasta 10 min/);
    expect(screen.queryByRole('dialog', { name: 'Renderizar 5 clips' })).not.toBeInTheDocument();
  });

  it('el texto se recorre y se selecciona con el teclado para crear un clip', async () => {
    signIn('todos-los-estados');
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={NO_SUGGESTIONS} />);
    const text = await screen.findByRole('group', { name: /^Texto de la transcripción/ });
    // Exactly one word takes the Tab stop; the arrows move it.
    expect(text.querySelectorAll('[tabindex="0"]')).toHaveLength(1);
    const first = text.querySelector('[data-w="0"]') as HTMLElement;
    first.focus();
    await user.keyboard('{ArrowRight}{ArrowRight}');
    expect(document.activeElement).toHaveAttribute('data-w', '2');
    expect(text.querySelectorAll('[tabindex="0"]')).toHaveLength(1);
    await user.keyboard('{Shift>}{ArrowRight}{ArrowRight}{ArrowRight}{ArrowDown}{/Shift}');
    const actions = await screen.findByRole('group', { name: 'Acciones para la selección' });
    const create = within(actions).getByRole('button', { name: 'Crear clip con la selección' });
    create.focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(bar()).toHaveTextContent('1 clip seleccionado'));
    // Escape drops a keyboard selection.
    // With a clip in the list the layout changes and the transcript moves to the side: look it up again.
    const side = await screen.findByRole('group', { name: /^Texto de la transcripción/ });
    (side.querySelector('[tabindex="0"]') as HTMLElement).focus();
    await user.keyboard('{Shift>}{ArrowRight}{/Shift}');
    expect(await screen.findByRole('group', { name: 'Acciones para la selección' })).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('group', { name: 'Acciones para la selección' })).not.toBeInTheDocument();
  });

  describe('con un recorte guardado en el editor de clip', () => {
    /** The clip the review opens on: the best suggestion. */
    const best = () => [...(project(REVIEW)?.clips ?? [])].sort((a, b) => b.virality_score - a.virality_score)[0];
    const endField = () => screen.getByRole('textbox', { name: /Fin/ }) as HTMLInputElement;

    it('volver del editor muestra el recorte nuevo y es el que se aprueba', async () => {
      const user = userEvent.setup();
      const client = appQueryClient();
      const clip = best();
      if (!clip) throw new Error('falta el clip');
      const newEnd = clip.end_time + 0.5;

      // 1. The review is open and cached.
      const review = renderScreen(<ReviewScreen projectId={REVIEW} />, client);
      await screen.findByRole('region', { name: 'Clips sugeridos' });
      expect(endField().value).toBe(formatEdge(clip.end_time));
      review.unmount();

      // 2. The clip editor saves a longer trim (free).
      const editor = renderScreen(<ClipEditorScreen projectId={REVIEW} clipId={clip.id} />, client);
      await user.click(await screen.findByRole('button', { name: 'Mover el fin 0,5 segundos después' }));
      await user.click(screen.getByRole('button', { name: 'Guardar cambios' }));
      expect(await screen.findByText('Cambios guardados')).toBeInTheDocument();
      editor.unmount();

      // 3. "Ir a la revisión": the saved trim, not the cached one.
      let body: projects.ApprovePaperEditRequest | null = null;
      server.use(
        http.post('*/api/v1/projects/:id/approve-paper-edit/', async ({ request }) => {
          body = (await request.json()) as projects.ApprovePaperEditRequest;
          return HttpResponse.json({ message: 'ok', project_id: REVIEW, final_cost: '92.70', transaction_id: mid(KIND.transaction, 999) });
        }),
      );
      renderScreen(<ReviewScreen projectId={REVIEW} />, client);
      await screen.findByRole('region', { name: 'Clips sugeridos' });
      await waitFor(() => expect(endField().value).toBe(formatEdge(newEnd)));
      const renderButton = await screen.findByRole('button', { name: 'Renderizar 5 clips' });
      await waitFor(() => expect(renderButton).toBeEnabled());
      await user.click(renderButton);
      await user.click(within(await screen.findByRole('dialog', { name: 'Renderizar 5 clips' })).getByRole('button', { name: 'Renderizar' }));
      await waitFor(() => expect(body).not.toBeNull());
      const sent = (body as unknown as projects.ApprovePaperEditRequest).approved_segments.find((segment) => segment.clip_id === clip.id);
      expect(sent).toMatchObject({ start: clip.start_time, end: newEnd });
    });

    it('recargar con un borrador que solo excluyó otro clip no devuelve el recorte viejo', async () => {
      const user = userEvent.setup();
      const clip = best();
      if (!clip) throw new Error('falta el clip');
      const first = renderScreen(<ReviewScreen projectId={REVIEW} />);
      const list = await screen.findByRole('region', { name: 'Clips sugeridos' });
      await user.click(within(list).getAllByRole('checkbox', { checked: true })[1] as HTMLElement);
      await waitFor(() => expect(bar()).toHaveTextContent('4 clips seleccionados'));
      first.unmount();
      // The trim of the first clip changes on the server (saved in the editor, or by a teammate).
      editDb((db) => {
        const target = db.projects.find((item) => item.id === REVIEW)?.clips.find((item) => item.id === clip.id);
        if (target) target.end_time += 0.5;
      });
      renderScreen(<ReviewScreen projectId={REVIEW} />);
      await screen.findByRole('region', { name: 'Clips sugeridos' });
      // The session draft comes back (4 clips), and the untouched clip follows the server.
      await waitFor(() => expect(bar()).toHaveTextContent('4 clips seleccionados'));
      expect(endField().value).toBe(formatEdge(clip.end_time + 0.5));
    });

    it('el borrador de una persona no aparece para otra en la misma pestaña', async () => {
      const user = userEvent.setup();
      const first = renderScreen(<ReviewScreen projectId={REVIEW} />);
      const list = await screen.findByRole('region', { name: 'Clips sugeridos' });
      await user.click(within(list).getAllByRole('checkbox', { checked: true })[1] as HTMLElement);
      await waitFor(() => expect(bar()).toHaveTextContent('4 clips seleccionados'));
      first.unmount();
      const draftKeys = Object.keys(window.sessionStorage);
      // Lucía logs out and Martín signs in on the same tab (the session storage is still there).
      signIn('rol-admin', 'martin');
      for (const key of draftKeys) expect(window.sessionStorage.getItem(key)).not.toBeNull();
      renderScreen(<ReviewScreen projectId={REVIEW} />);
      await screen.findByRole('region', { name: 'Clips sugeridos' });
      await waitFor(() => expect(bar()).toHaveTextContent('5 clips seleccionados'));
    });
  });

  it('en el texto, Espacio marca o desmarca la palabra y Enter va a esa palabra en el video (como en una lista)', async () => {
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    const text = await screen.findByRole('group', { name: /^Texto de la transcripción/ });
    expect(text).toHaveAccessibleName(/con Espacio marcás o desmarcás la palabra y con Enter vas a esa palabra en el video/);
    const selected = () => Array.from(text.querySelectorAll('[role="option"][aria-selected="true"]')).map((word) => Number(word.getAttribute('data-w')));
    /** Where the player is, as its seek bar says it. */
    const video = { get currentTime() { return screen.getAllByRole('slider').map((slider) => slider.getAttribute('aria-valuetext')).join('|'); } };
    const word = (index: number) => text.querySelector(`[data-w="${index}"]`) as HTMLElement;

    word(10).focus();
    const before = video.currentTime;
    // Space selects the word and leaves the video where it was.
    await user.keyboard(' ');
    expect(selected()).toEqual([10]);
    expect(video.currentTime).toBe(before);
    expect(await screen.findByRole('group', { name: 'Acciones para la selección' })).toBeInTheDocument();
    // The next word joins the run; Space on it again takes it out.
    await user.keyboard('{ArrowRight}');
    expect(selected()).toEqual([]);
    await user.keyboard(' ');
    expect(selected()).toEqual([11]);
    await user.keyboard('{Shift>}{ArrowRight}{ArrowRight}{/Shift}');
    expect(selected()).toEqual([11, 12, 13]);
    expect(document.activeElement).toHaveAttribute('data-w', '13');
    await user.keyboard(' ');
    expect(selected()).toEqual([11, 12]);
    await user.keyboard(' ');
    expect(selected()).toEqual([11, 12, 13]);

    // Enter goes to the word in the video and does not touch the selection.
    await user.keyboard('{Enter}');
    expect(selected()).toEqual([11, 12, 13]);
    await waitFor(() => expect(video.currentTime).not.toBe(before));

    // Unselecting down to nothing closes the actions.
    word(11).focus();
    await user.keyboard(' ');
    expect(selected()).toEqual([12, 13]);
    word(12).focus();
    await user.keyboard(' ');
    word(13).focus();
    await user.keyboard(' ');
    expect(selected()).toEqual([]);
    expect(screen.queryByRole('group', { name: 'Acciones para la selección' })).not.toBeInTheDocument();
  });

  it('toggleWord mantiene la selección en un solo tramo', () => {
    expect(toggleWord(null, 4)).toEqual({ from: 4, to: 4 });
    expect(toggleWord({ from: 4, to: 4 }, 4)).toBeNull();
    // Next to the run: it joins. Away from it: a new run.
    expect(toggleWord({ from: 4, to: 6 }, 7)).toEqual({ from: 4, to: 7 });
    expect(toggleWord({ from: 4, to: 6 }, 3)).toEqual({ from: 3, to: 6 });
    expect(toggleWord({ from: 4, to: 6 }, 20)).toEqual({ from: 20, to: 20 });
    // At an edge: it leaves. In the middle: the run ends before it.
    expect(toggleWord({ from: 4, to: 6 }, 4)).toEqual({ from: 5, to: 6 });
    expect(toggleWord({ from: 4, to: 6 }, 6)).toEqual({ from: 4, to: 5 });
    expect(toggleWord({ from: 4, to: 8 }, 6)).toEqual({ from: 4, to: 5 });
  });

  it('el panel de atajos cuenta qué hacen Espacio y Enter en el texto', async () => {
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    await user.click(await screen.findByRole('button', { name: 'Atajos de teclado' }));
    const keys = (label: string) => Array.from(screen.getByText(label).parentElement?.querySelectorAll('kbd') ?? []).map((key) => key.textContent);
    expect(keys('En el texto: marcar o desmarcar la palabra')).toEqual(['espacio']);
    expect(keys('En el texto: ir a esa palabra en el video')).toEqual(['↵']);
    expect(keys('Reproducir o pausar')).toEqual(['espacio']);
  });

  it('mientras la revisión se actualiza, «Renderizar» espera y dice por qué', async () => {
    const user = userEvent.setup();
    const view = renderScreen(<ReviewScreen projectId={REVIEW} />, appQueryClient());
    const button = await screen.findByRole('button', { name: 'Renderizar 5 clips' });
    await waitFor(() => expect(button).toBeEnabled());
    expect(button).not.toHaveAttribute('aria-disabled');

    // The refetch takes its time (a slow network; the client gives up on it after 30 s).
    let answer: () => void = () => undefined;
    const held = new Promise<void>((resolve) => (answer = resolve));
    server.use(
      http.get('http://localhost:8000/api/v1/projects/:id/paper-edit-data/', async () => {
        await held;
      }),
    );
    void view.queryClient.invalidateQueries({ queryKey: keys.projects.paperEdit(REVIEW) });
    await waitFor(() => expect(button).toHaveAttribute('aria-disabled', 'true'));
    // The reason is one hover (or one Tab) away, and clicking does nothing meanwhile.
    await user.hover(button);
    expect(await screen.findByRole('tooltip')).toHaveTextContent('Actualizando la revisión…');
    expect(button).toHaveAccessibleDescription('Actualizando la revisión…');
    await user.click(button);
    expect(screen.queryByRole('dialog', { name: 'Renderizar 5 clips' })).not.toBeInTheDocument();

    answer();
    await waitFor(() => expect(button).not.toHaveAttribute('aria-disabled'));
    await user.click(button);
    expect(await screen.findByRole('dialog', { name: 'Renderizar 5 clips' })).toBeInTheDocument();
  });

  it('la selección se anuncia y las palabras dicen si están seleccionadas', async () => {
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    const text = await screen.findByRole('group', { name: /^Texto de la transcripción/ });
    expect(within(text).getAllByRole('listbox').length).toBeGreaterThan(1);
    const live = text.parentElement?.querySelector('[aria-live="polite"]') as HTMLElement;
    expect(live).toHaveTextContent('');
    expect(text.querySelectorAll('[role="option"][aria-selected="true"]')).toHaveLength(0);
    selectWords(3, 6);
    await waitFor(() => expect(live).toHaveTextContent(/^Selección \d\d:\d\d–\d\d:\d\d, \d:\d\d$/));
    expect(text.querySelectorAll('[role="option"][aria-selected="true"]')).toHaveLength(4);
    expect((text.querySelector('[data-w="3"]') as HTMLElement).textContent?.trim()).not.toBe('');
  });

  it('si otra persona ya lo aprobó (hoy un 400) avisa y lleva a Clips', async () => {
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    await user.click(await screen.findByRole('button', { name: 'Renderizar 5 clips' }));
    const dialog = await screen.findByRole('dialog', { name: 'Renderizar 5 clips' });
    editDb((db) => {
      const target = db.projects.find((item) => item.id === REVIEW);
      if (target) target.status = 'rendering';
    });
    await user.click(within(dialog).getByRole('button', { name: 'Renderizar' }));
    expect(await screen.findByText('Este proyecto ya se aprobó. Te llevamos a los clips.')).toBeInTheDocument();
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith(`/proyectos/${REVIEW}/clips`));
    // The cached review is refreshed at once: the screen itself now knows the project left the review.
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith(`/proyectos/${REVIEW}/clips`));
  });

  it('también con el texto mal codificado que mandaba el backend antes del arreglo', async () => {
    server.use(
      http.post('*/api/v1/projects/:id/approve-paper-edit/', () =>
        HttpResponse.json({ error: 'El proyecto no estÃ¡ en espera de aprobaciÃ³n. Estado actual: rendering' }, { status: 400 }),
      ),
    );
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    await user.click(await screen.findByRole('button', { name: 'Renderizar 5 clips' }));
    await user.click(within(await screen.findByRole('dialog', { name: 'Renderizar 5 clips' })).getByRole('button', { name: 'Renderizar' }));
    expect(await screen.findByText('Este proyecto ya se aprobó. Te llevamos a los clips.')).toBeInTheDocument();
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith(`/proyectos/${REVIEW}/clips`));
  });

  it('la carrera de dos aprobaciones (409) va por el mismo camino', async () => {
    server.use(http.post('*/api/v1/projects/:id/approve-paper-edit/', () => HttpResponse.json({ error: 'El proyecto ya se está renderizando.' }, { status: 409 })));
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    await user.click(await screen.findByRole('button', { name: 'Renderizar 5 clips' }));
    await user.click(within(await screen.findByRole('dialog', { name: 'Renderizar 5 clips' })).getByRole('button', { name: 'Renderizar' }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith(`/proyectos/${REVIEW}/clips`));
  });

  it('un límite del plan al aprobar (400 del motor de precios) abre «Límite del plan» en español', async () => {
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    await user.click(await screen.findByRole('button', { name: 'Renderizar 5 clips' }));
    const dialog = await screen.findByRole('dialog', { name: 'Renderizar 5 clips' });
    // The plan changed after the cost was shown: the backend refuses with its English pricing message.
    editDb((db) => {
      const target = db.projects.find((item) => item.id === REVIEW);
      if (target) target.metadata = { ...target.metadata, render_resolution: '1080p' };
      for (const workspace of db.workspaces) workspace.subscription_plan = PLAN_IDS.free;
    });
    await user.click(within(dialog).getByRole('button', { name: 'Renderizar' }));
    const limit = await screen.findByRole('alertdialog', { name: 'Tu plan no incluye esta resolución' });
    expect(limit).toHaveTextContent('Tu plan no permite renderizar estos clips. Sacá alguno o cambiá de plan.');
    expect(limit).not.toHaveTextContent(/motor de precios|does not support/);
    expect(router.push).not.toHaveBeenCalled();
    expect(project(REVIEW)?.status).toBe('awaiting_approval');
  });

  it.each([
    [30, 'No alcanzó el saldo para renderizar sin revisar'],
    [31, 'Tu plan no permite renderizar estos clips sin revisar'],
  ])('avisa cuando «Renderizar sin revisar» no se pudo pagar (proyecto %i)', async (index, title) => {
    signIn('todos-los-estados');
    renderScreen(<ReviewScreen projectId={mid(KIND.project, index)} />);
    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.getByText('Revisá y aprobá los clips que quieras.')).toBeInTheDocument();
  });

  it('con saldo bajo abre «No te alcanza el saldo» antes de confirmar', async () => {
    signIn('saldo-bajo');
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    const list = await screen.findByRole('region', { name: 'Clips sugeridos' });
    for (const box of within(list).getAllByRole('checkbox', { checked: false })) await user.click(box);
    await waitFor(() => expect(bar()).toHaveTextContent(/Te faltan/));
    await user.click(screen.getByRole('button', { name: 'Renderizar 9 clips' }));
    expect(await screen.findByRole('alertdialog', { name: 'No te alcanza el saldo' })).toBeInTheDocument();
    expect(project(REVIEW)?.status).toBe('awaiting_approval');
  });

  it('un Lector ve la revisión sin poder elegir ni renderizar', async () => {
    signIn('rol-lector', 'diego');
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    expect(await screen.findByText('Estás viendo la revisión en modo lectura')).toBeInTheDocument();
    const list = screen.getByRole('region', { name: 'Clips sugeridos' });
    for (const box of within(list).getAllByRole('checkbox')) expect(box).toBeDisabled();
    const renderButton = await screen.findByRole('button', { name: 'Renderizar 5 clips' });
    expect(renderButton).toHaveAttribute('aria-disabled', 'true');
    // Disabled, not hidden, and each one says why.
    expect(screen.getByRole('button', { name: 'Nuevo clip desde el cursor' })).toHaveAttribute('aria-disabled', 'true');
    expect(await screen.findByRole('button', { name: 'Ver detalle' })).toHaveAttribute('title', 'Pedile acceso a un admin');
    expect(screen.getByRole('textbox', { name: /Inicio/ })).toHaveAttribute('title', 'Pedile acceso a un admin');
    await user.click(renderButton);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('sin sugerencias se arman clips desde la transcripción', async () => {
    signIn('todos-los-estados');
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={NO_SUGGESTIONS} />);
    expect(await screen.findByText('No encontramos momentos que funcionen solos.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Renderizar' })).toBeDisabled();
    expect(bar()).toHaveTextContent('Creá al menos un clip para renderizar');
    selectWords(12, 40);
    const actions = await screen.findByRole('group', { name: 'Acciones para la selección' });
    await user.click(within(actions).getByRole('button', { name: 'Crear clip con la selección' }));
    await waitFor(() => expect(bar()).toHaveTextContent('1 clip seleccionado'));
    expect(within(screen.getByRole('region', { name: 'Tus clips' })).getAllByRole('listitem')).toHaveLength(1);
    expect(await screen.findByRole('button', { name: 'Renderizar 1 clip' })).toBeEnabled();
  });

  it('la selección también mueve el fin del clip actual', async () => {
    const user = userEvent.setup();
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    await screen.findByRole('region', { name: 'Clips sugeridos' });
    const end = screen.getByRole('textbox', { name: /Fin/ }) as HTMLInputElement;
    const before = end.value;
    const spans = [...document.querySelectorAll('[data-w]')];
    const last = spans.reduce((found, span, index) => (span.className.includes('in') ? index : found), -1);
    selectWords(last + 2, last + 8);
    await user.click(await screen.findByRole('button', { name: /Llevar el fin del clip 1 acá/ }));
    await waitFor(() => expect(end.value).not.toBe(before));
  });

  it('un proyecto que ya no está en revisión lleva a sus clips', async () => {
    renderScreen(<ReviewScreen projectId={READY} />);
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith(`/proyectos/${READY}/clips`));
  });

  it('si la API no responde ofrece reintentar', async () => {
    signIn('servidor-caido');
    renderScreen(<ReviewScreen projectId={REVIEW} />);
    expect(await screen.findByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
  });
});

describe('D4 · Editor de clip', () => {
  beforeEach(() => signIn('estudio-norte'));
  const CLIP = clipOf(5, 1);
  const stored = () => project(READY)?.clips.find((item) => item.id === CLIP);

  it('recortar marca cambios sin guardar y muestra el costo del render', async () => {
    const user = userEvent.setup();
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    await screen.findByRole('region', { name: 'Recorte del clip' });
    expect(bar()).toHaveTextContent('Sin cambios');
    await user.click(screen.getByRole('button', { name: 'Mover el inicio 0,5 segundos antes' }));
    expect(await screen.findByText('Cambios sin guardar')).toBeInTheDocument();
    expect(bar()).toHaveTextContent('Cambios sin guardar: recorte');
    await waitFor(() => expect(bar()).toHaveTextContent(/en 1080p con 10 % de descuento del plan Pro/));
    expect(screen.getByRole('group', { name: 'Inicio del clip' })).toHaveTextContent('Antes:');
  });

  it('guardar y renderizar confirma el costo, guarda el recorte y encola el render', async () => {
    const user = userEvent.setup();
    const before = stored();
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    await user.click(await screen.findByRole('button', { name: 'Mover el fin 0,5 segundos después' }));
    await user.click(await screen.findByRole('button', { name: /Guardar y renderizar/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Guardar y renderizar' });
    await user.click(within(dialog).getByRole('button', { name: 'Renderizar' }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith(`/proyectos/${READY}/clips`));
    expect(stored()?.end_time).toBe((before?.end_time ?? 0) + 0.5);
    expect(stored()?.status).toBe('rendering');
  });

  it('cambiar solo el título se guarda sin renderizar ni cobrar', async () => {
    const user = userEvent.setup();
    const balance = loadDb().wallets[0]?.available_balance;
    const status = stored()?.status;
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    const title = await screen.findByRole('textbox', { name: 'Título' });
    await user.clear(title);
    await user.type(title, 'Atardecer en Potrerillos');
    expect(bar()).toHaveTextContent('Cambiar el título no vuelve a renderizar ni gasta tokens.');
    await user.click(screen.getByRole('button', { name: 'Guardar título' }));
    expect(await screen.findByText('Título guardado')).toBeInTheDocument();
    expect(stored()?.title).toBe('Atardecer en Potrerillos');
    expect(stored()?.status).toBe(status);
    expect(loadDb().wallets[0]?.available_balance).toBe(balance);
  });

  it('un título vacío no se guarda', async () => {
    const user = userEvent.setup();
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    await user.clear(await screen.findByRole('textbox', { name: 'Título' }));
    await user.click(screen.getByRole('button', { name: 'Guardar título' }));
    expect(await screen.findByText('Poné un título para el clip.')).toBeInTheDocument();
  });

  it('con el proyecto renderizando avisa y no deja recortar', async () => {
    renderScreen(<ClipEditorScreen projectId={RENDERING} clipId={clipOf(4, 1)} />);
    expect(await screen.findByText(PROJECT_RENDERING_MESSAGE)).toBeInTheDocument();
    // The reason is the render, not the role.
    expect(screen.getByRole('button', { name: 'Mover el inicio 0,5 segundos antes' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('textbox', { name: /Inicio/ })).toHaveAttribute('title', PROJECT_RENDERING_MESSAGE);
    expect(screen.getByRole('button', { name: /Volver a renderizar/ })).toBeDisabled();
  });

  it('si el render arranca mientras se edita, el 409 deja el borrador y avisa', async () => {
    const user = userEvent.setup();
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    await user.click(await screen.findByRole('button', { name: 'Mover el inicio 0,5 segundos antes' }));
    await user.click(await screen.findByRole('button', { name: /Guardar y renderizar/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Guardar y renderizar' });
    const original = stored()?.start_time;
    editDb((db) => {
      const target = db.projects.find((item) => item.id === READY);
      if (target) target.status = 'rendering';
    });
    await user.click(within(dialog).getByRole('button', { name: 'Renderizar' }));
    expect((await screen.findAllByText(PROJECT_RENDERING_MESSAGE)).length).toBeGreaterThan(0);
    expect(stored()?.start_time).toBe(original);
    expect(router.push).not.toHaveBeenCalled();
    expect(screen.getByText('Cambios sin guardar')).toBeInTheDocument();
  });

  it('el recorte sin guardar sobrevive a ir a comprar tokens y volver', async () => {
    signIn('saldo-bajo');
    editDb((db) => {
      for (const wallet of db.wallets) wallet.available_balance = 5;
    });
    const user = userEvent.setup();
    const first = renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    await user.click(await screen.findByRole('button', { name: 'Mover el inicio 0,5 segundos antes' }));
    const typed = (screen.getByRole('textbox', { name: /Inicio/ }) as HTMLInputElement).value;
    await user.click(await screen.findByRole('button', { name: /Guardar y renderizar/ }));
    // Not enough balance: the dialog says the configuration stays saved, and it does.
    expect(await screen.findByRole('alertdialog', { name: 'No te alcanza el saldo' })).toHaveTextContent('Tu configuración queda guardada');
    first.unmount();
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    expect(await screen.findByText('Cambios sin guardar')).toBeInTheDocument();
    expect((screen.getByRole('textbox', { name: /Inicio/ }) as HTMLInputElement).value).toBe(typed);
    expect(screen.getByRole('group', { name: 'Inicio del clip' })).toHaveTextContent('Antes:');
    // Nothing was saved behind the person's back.
    expect(stored()?.start_time).not.toBe(undefined);
    expect(bar()).toHaveTextContent('Cambios sin guardar: recorte');
  });

  it('descartar tira el borrador guardado', async () => {
    const user = userEvent.setup();
    const first = renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    await user.click(await screen.findByRole('button', { name: 'Mover el inicio 0,5 segundos antes' }));
    await user.click(await screen.findByRole('button', { name: 'Descartar cambios' }));
    expect(router.push).toHaveBeenCalledWith(`/proyectos/${READY}/clips`);
    first.unmount();
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    await screen.findByRole('region', { name: 'Recorte del clip' });
    expect(bar()).toHaveTextContent('Sin cambios');
  });

  it('un clip en borrador de un proyecto en revisión no se renderiza solo', async () => {
    const user = userEvent.setup();
    const draftClip = clipOf(3, 1);
    const saved = () => project(REVIEW)?.clips.find((item) => item.id === draftClip);
    const balance = loadDb().wallets[0]?.available_balance;
    const start = saved()?.start_time ?? 0;
    renderScreen(<ClipEditorScreen projectId={REVIEW} clipId={draftClip} />);
    await screen.findByRole('region', { name: 'Recorte del clip' });
    // Never rendered: it is not "Volver a renderizar", and paying for it here would charge it twice.
    expect(screen.queryByRole('button', { name: /Volver a renderizar/ })).not.toBeInTheDocument();
    const renderButton = screen.getByRole('button', { name: 'Renderizar' });
    expect(renderButton).toHaveAttribute('aria-disabled', 'true');
    await user.hover(renderButton);
    expect(await screen.findByText('Renderizalo desde la revisión')).toBeInTheDocument();
    await user.click(renderButton);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    // Saving the trim stays, and it is free.
    await user.click(screen.getByRole('button', { name: 'Mover el inicio 0,5 segundos antes' }));
    expect(screen.queryByRole('button', { name: /Guardar y renderizar/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Guardar cambios' }));
    expect(await screen.findByText('Cambios guardados')).toBeInTheDocument();
    expect(saved()?.start_time).toBe(start - 0.5);
    expect(saved()?.status).toBe('draft');
    expect(loadDb().wallets[0]?.available_balance).toBe(balance);
  });

  it('un refetch del servidor no pisa lo que se está editando', async () => {
    const user = userEvent.setup();
    const { queryClient } = renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    const title = (await screen.findByRole('textbox', { name: 'Título' })) as HTMLInputElement;
    await user.clear(title);
    await user.type(title, 'Mi título a medio escribir');
    const startBefore = (screen.getByRole('textbox', { name: /Inicio/ }) as HTMLInputElement).value;
    // Someone else moves the start and renames the clip; the screen refetches.
    editDb((db) => {
      const target = db.projects.find((item) => item.id === READY)?.clips.find((item) => item.id === CLIP);
      if (target) {
        target.start_time -= 2;
        target.title = 'Título de otra persona';
      }
    });
    await queryClient.invalidateQueries({ queryKey: keys.clips.detail(CLIP) });
    // The untouched trim follows the server; the title being typed stays.
    await waitFor(() => expect((screen.getByRole('textbox', { name: /Inicio/ }) as HTMLInputElement).value).not.toBe(startBefore));
    expect(title.value).toBe('Mi título a medio escribir');
    expect(bar()).toHaveTextContent('Cambios sin guardar: título');
  });

  it('un 409 sin código muestra la frase del servidor y el bloqueo se levanta cuando termina el render', async () => {
    server.use(http.patch('*/api/v1/clips/:id/', () => HttpResponse.json({ detail: 'El proyecto se está renderizando: esperá a que termine para cambiarlo.' }, { status: 409 })));
    const user = userEvent.setup();
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    await user.click(await screen.findByRole('button', { name: 'Mover el inicio 0,5 segundos antes' }));
    editDb((db) => {
      const target = db.projects.find((item) => item.id === READY);
      if (target) target.status = 'rendering';
    });
    await user.click(await screen.findByRole('button', { name: 'Guardar y ver vista previa' }));
    expect((await screen.findAllByText(PROJECT_RENDERING_MESSAGE)).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Mover el inicio 0,5 segundos antes' })).toHaveAttribute('aria-disabled', 'true'));
    // The render ends: the project is polled every 5 s and the trim opens again.
    editDb((db) => {
      const target = db.projects.find((item) => item.id === READY);
      if (target) target.status = 'completed';
    });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Mover el inicio 0,5 segundos antes' })).not.toHaveAttribute('aria-disabled'), { timeout: 15_000 });
    expect(screen.getByText('Cambios sin guardar')).toBeInTheDocument();
  });

  it('un borrador de solo título no empuja un recorte viejo si otra persona lo cambió', async () => {
    const user = userEvent.setup();
    const first = renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    const title = await screen.findByRole('textbox', { name: 'Título' });
    await user.clear(title);
    await user.type(title, 'Solo cambié el título');
    const startBefore = stored()?.start_time ?? 0;
    first.unmount();
    // A teammate moves the trim while this person is away (buying tokens, say).
    editDb((db) => {
      const target = db.projects.find((item) => item.id === READY)?.clips.find((item) => item.id === CLIP);
      if (target) target.start_time -= 2;
    });
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    expect(((await screen.findByRole('textbox', { name: 'Título' })) as HTMLInputElement).value).toBe('Solo cambié el título');
    expect((screen.getByRole('textbox', { name: /Inicio/ }) as HTMLInputElement).value).toBe(formatEdge(startBefore - 2));
    // Only the title is pending: saving it does not touch the teammate's trim.
    expect(bar()).toHaveTextContent('Cambios sin guardar: título');
    await user.click(screen.getByRole('button', { name: 'Guardar título' }));
    expect(await screen.findByText('Título guardado')).toBeInTheDocument();
    expect(stored()).toMatchObject({ title: 'Solo cambié el título', start_time: startBefore - 2 });
  });

  it('el borrador del editor tampoco pasa de una persona a otra', async () => {
    const user = userEvent.setup();
    const first = renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    await user.click(await screen.findByRole('button', { name: 'Mover el inicio 0,5 segundos antes' }));
    expect(await screen.findByText('Cambios sin guardar')).toBeInTheDocument();
    first.unmount();
    signIn('rol-admin', 'martin');
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    await screen.findByRole('region', { name: 'Recorte del clip' });
    expect(bar()).toHaveTextContent('Sin cambios');
  });

  it('con la sesión y el clip llegando tarde no hay nada que editar antes de tiempo, y lo editado no se pierde', async () => {
    // A slow runner: the session answers late and the clip later still (the handlers fall through to the mocks).
    server.use(
      http.get('*/api/v1/me/', async () => {
        await delay(700);
      }),
      http.get('*/api/v1/clips/:id/', async () => {
        await delay(1200);
      }),
    );
    const user = userEvent.setup();
    const first = renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    // Until the session and the data are there the editor shows its skeleton: there is no control
    // whose edit could be dropped or stored under a key without the person.
    expect(screen.queryByRole('button', { name: 'Mover el inicio 0,5 segundos antes' })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: /Inicio/ })).not.toBeInTheDocument();
    const nudge = await screen.findByRole('button', { name: 'Mover el inicio 0,5 segundos antes' });
    const start = screen.getByRole('textbox', { name: /Inicio/ }) as HTMLInputElement;
    const before = start.value;
    // The very first interaction once it is ready counts, and the field shows it at once.
    await user.click(nudge);
    expect(start.value).not.toBe(before);
    const edited = start.value;
    expect(Object.keys(window.sessionStorage).filter((key) => key.startsWith('oc.editor.'))).toHaveLength(1);
    // A refetch of the clip that lands after the edit does not undo it.
    await first.queryClient.invalidateQueries({ queryKey: keys.clips.detail(CLIP) });
    expect(start.value).toBe(edited);
    first.unmount();
    // Reload on the same slow runner: the unsaved trim is back when the editor is ready.
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    expect(((await screen.findByRole('textbox', { name: /Inicio/ })) as HTMLInputElement).value).toBe(edited);
    expect(screen.getByText('Cambios sin guardar')).toBeInTheDocument();
  });

  it('pide la vista previa gratis y espera a que esté', async () => {
    const user = userEvent.setup();
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    await user.click(await screen.findByRole('button', { name: 'Vista previa gratis' }));
    expect(await screen.findByText(/Preparando la vista previa/)).toBeInTheDocument();
    expect(loadDb().previews.some((item) => item.clip_id === CLIP)).toBe(true);
  });

  it('un proyecto de video completo no ofrece vista previa', async () => {
    renderScreen(<ClipEditorScreen projectId={WHOLE} clipId={clipOf(7, 1)} />);
    expect(await screen.findByText('El video completo se renderiza entero: no tiene vista previa.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Vista previa gratis' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Volver a Resultado' })).toHaveAttribute('href', `/proyectos/${WHOLE}/resultado`);
  });

  it('si el plan ya no permite la resolución abre «Límite del plan»', async () => {
    signIn('plan-gratis');
    const user = userEvent.setup();
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    await user.click(await screen.findByRole('button', { name: /Volver a renderizar/ }));
    expect(await screen.findByRole('alertdialog', { name: 'Tu plan no incluye esta resolución' })).toBeInTheDocument();
  });

  it('un Lector puede mirar y descargar, no editar', async () => {
    signIn('rol-lector', 'diego');
    renderScreen(<ClipEditorScreen projectId={READY} clipId={CLIP} />);
    expect(await screen.findByText('Estás viendo el clip en modo lectura')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Título' })).toHaveAttribute('readonly');
    expect(screen.getByRole('button', { name: /Volver a renderizar/ })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('button', { name: 'Descargar' })).not.toHaveAttribute('aria-disabled');
  });

  it('un clip que no es de ese proyecto no existe', async () => {
    renderScreen(<ClipEditorScreen projectId={REVIEW} clipId={CLIP} />);
    expect(await screen.findByText('Esta página no existe')).toBeInTheDocument();
  });
});

describe('mocks de clips: reglas mientras se renderiza y por plan', () => {
  const fail = async (promise: Promise<unknown>) => {
    const error = await promise.catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  };

  it('PATCH con el proyecto renderizando: 409 project_rendering salvo que cambie solo el título', async () => {
    signIn('estudio-norte');
    const id = clipOf(4, 1);
    const renamed = await apiCall(clips.updateClip, { params: { id }, body: { title: 'Otro título' } });
    expect(renamed.title).toBe('Otro título');
    const error = await fail(apiCall(clips.updateClip, { params: { id }, body: { start_time: renamed.start_time + 1 } }));
    expect(error.status).toBe(409);
    expect(error.code).toBe('project_rendering');
    expect((await fail(apiCall(clips.renderClip, { params: { id } }))).code).toBe('project_rendering');
  });

  it('render de un clip en una resolución que el plan no permite: 403 de precios', async () => {
    signIn('plan-gratis');
    const error = await fail(apiCall(clips.renderClip, { params: { id: clipOf(5, 1) } }));
    expect(error.status).toBe(403);
    expect(error.isPlanLimit).toBe(true);
    expect(error.code).toBe('resolution');
  });
});
