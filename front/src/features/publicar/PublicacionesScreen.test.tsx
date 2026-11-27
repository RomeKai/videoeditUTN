import { cleanup, configure, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '@/api/mocks/server';
import { applyScenario } from '@/api/mocks/state';
import { db, idle, renderScenario } from '@/testing';
import { PublicacionesScreen } from './PublicacionesScreen';
import { holdDraft, isRemembered, releaseDraft } from './draftRegistry';
import { dayKey } from './posts';

// These flows wait on the mocked API several times: on a busy CI runner the default 1 s / 5 s are not enough.
configure({ asyncUtilTimeout: 5000 });
vi.setConfig({ testTimeout: 30_000 });

/** The screen keeps its view, filters and open detail in the URL: this is a URL that changes. */
const nav = vi.hoisted(() => {
  let search = '';
  const listeners = new Set<() => void>();
  return {
    get: () => search,
    set: (value: string) => {
      search = value;
      for (const listener of listeners) listener();
    },
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
});

vi.mock('next/navigation', async () => {
  const React = await import('react');
  const go = (url: string) => nav.set(url.split('?')[1] ?? '');
  const router = { push: go, replace: go, back: () => undefined, refresh: () => undefined, prefetch: () => undefined };
  return {
    useRouter: () => router,
    usePathname: () => '/publicaciones',
    useSearchParams: () => {
      const search = React.useSyncExternalStore(nav.subscribe, nav.get, nav.get);
      return React.useMemo(() => new URLSearchParams(search), [search]);
    },
  };
});

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => nav.set(''));
afterEach(async () => {
  cleanup();
  await idle();
  server.resetHandlers();
});
afterAll(() => server.close());

const open = async (search = 'vista=lista', scenario?: string) => {
  nav.set(search);
  const view = renderScenario(<PublicacionesScreen />, { scenario });
  await screen.findByRole('heading', { level: 1, name: 'Publicaciones' });
  return view;
};
const table = () => screen.findByRole('table', { name: 'Publicaciones por día' });
const postWith = (status: string) => db().posts.find((post) => post.status === status);
const rowOf = async (title: string, network: string) => {
  const rows = within(await table()).getAllByRole('row');
  return rows.find((row) => row.textContent?.includes(title) && row.textContent.includes(network)) as HTMLElement;
};

describe('E2 · Publicaciones · lista', () => {
  it('agrupa por día, muestra cada estado con su palabra y deja el borrador sin fecha', async () => {
    await open();
    const list = await table();
    for (const label of ['Publicado', 'Programado', 'En cola', 'Publicando', 'Error', 'Borrador']) {
      expect(within(list).getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(within(list).getByRole('rowheader', { name: /Sin fecha/ })).toBeInTheDocument();
    expect(within(list).getByText('Hoy')).toBeInTheDocument();
    // Counters of the filters come from the API.
    expect(screen.getByRole('button', { name: /Programados/ })).toHaveTextContent('2');
    expect(screen.getByRole('button', { name: /Con error/ })).toHaveTextContent('1');
  });

  it('«Programar» en un borrador abre el panel con esa red sola', async () => {
    applyScenario('estudio-norte');
    const draft = postWith('DRAFT');
    await open();
    await table();
    fireEvent.click(await screen.findByRole('button', { name: `Programar «${draft?.clip.title}» en Instagram` }));
    await waitFor(() => expect(nav.get()).toContain(`publicar=${draft?.video_clip}`));
    expect(nav.get()).toContain('red=INSTAGRAM_REELS');
  });

  it('un borrador que dejó un panel y la persona elige o edita desde acá deja de ser descartable', async () => {
    applyScenario('estudio-norte');
    const draft = postWith('DRAFT');
    if (!draft) throw new Error('falta el borrador');
    await open();
    await table();
    const remember = () => {
      holdDraft({ id: draft.id, clip: draft.video_clip, workspace: draft.workspace });
      releaseDraft(draft.id);
    };
    remember();
    fireEvent.click(await screen.findByRole('button', { name: `Programar «${draft.clip.title}» en Instagram` }));
    expect(isRemembered(draft.id)).toBe(false);

    remember();
    nav.set(`vista=lista&detalle=${draft.id}`);
    const detail = await screen.findByRole('dialog', { name: 'Detalle de la publicación' });
    fireEvent.click(await within(detail).findByRole('button', { name: /Editar/ }));
    fireEvent.change(await within(detail).findByLabelText('Caption'), { target: { value: 'Lo escribí yo' } });
    fireEvent.click(within(detail).getByRole('button', { name: 'Guardar' }));
    expect(await screen.findByText('Cambios guardados')).toBeInTheDocument();
    expect(isRemembered(draft.id)).toBe(false);
  });

  it('avisa de la publicación que falló, con un motivo legible', async () => {
    await open();
    const failed = postWith('FAILED');
    const alert = (await screen.findByText(`«${failed?.clip.title}» no salió en TikTok`)).closest('[role]') as HTMLElement;
    // The backend's sentence for `provider_rejected`, never the `error_log` with the provider's detail.
    expect(alert).toHaveTextContent('TikTok rechazó la publicación.');
    expect(alert).not.toHaveTextContent('ayrshare');
    expect(within(alert).getByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
  });

  it('filtra por estado y por red', async () => {
    await open();
    await table();
    fireEvent.click(screen.getByRole('button', { name: /Con error/ }));
    await waitFor(async () => expect(within(await table()).getAllByRole('row')).toHaveLength(3));
    expect(within(await table()).getByText('Error')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Todas/ }));
    fireEvent.click(within(screen.getByRole('group', { name: 'Filtrar por red' })).getByRole('button', { name: /Instagram/ }));
    await waitFor(async () => expect(within(await table()).queryByText('TikTok')).not.toBeInTheDocument());

    // Nothing failed in Instagram: the "Con error" filter is not offered.
    await waitFor(() => expect(screen.queryByRole('button', { name: /Con error/ })).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Publicando/ }));
    expect(await screen.findByText('No hay publicaciones con estos filtros.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Quitar filtros' }));
    expect(within(await table()).getAllByText('TikTok').length).toBeGreaterThan(0);
  });

  it('reintentar una publicación con error la vuelve a poner en cola', async () => {
    await open();
    const failed = postWith('FAILED');
    const row = await rowOf(failed?.clip.title ?? '', 'TikTok');
    fireEvent.click(within(row).getByRole('button', { name: /^Reintentar/ }));
    expect(await screen.findByText(`Reintentando «${failed?.clip.title}» en TikTok`)).toBeInTheDocument();
    await waitFor(() => expect(db().posts.find((post) => post.id === failed?.id)?.status).toBe('QUEUED'));
    await waitFor(async () => expect(within(await rowOf(failed?.clip.title ?? '', 'TikTok')).getByText('En cola')).toBeInTheDocument());
  });

  it('cancelar pide confirmación y saca la publicación', async () => {
    await open();
    const queued = postWith('QUEUED');
    const row = await rowOf(queued?.clip.title ?? '', 'Instagram');
    fireEvent.click(within(row).getByRole('button', { name: /^Cancelar/ }));
    const dialog = await screen.findByRole('alertdialog', { name: '¿Cancelar esta publicación?' });
    expect(dialog).toHaveTextContent('no va a salir en Instagram');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancelar publicación' }));
    expect(await screen.findByText('Publicación cancelada')).toBeInTheDocument();
    expect(db().posts.find((post) => post.id === queued?.id)).toBeUndefined();
  });

  it('reprogramar abre el detalle con la fecha y el texto para editar', async () => {
    await open();
    const scheduled = postWith('SCHEDULED');
    const row = await rowOf(scheduled?.clip.title ?? '', 'TikTok');
    fireEvent.click(within(row).getByRole('button', { name: /^Reprogramar/ }));
    const detail = await screen.findByRole('dialog', { name: 'Detalle de la publicación' });
    const form = await within(detail).findByRole('form', { name: 'Editar la publicación' });

    fireEvent.change(within(form).getByLabelText('Fecha'), { target: { value: '2020-01-01' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Guardar' }));
    expect(await within(form).findByText('Elegí una fecha y una hora que todavía no pasaron.')).toBeInTheDocument();

    const next = new Date();
    next.setDate(next.getDate() + 5);
    fireEvent.change(within(form).getByLabelText('Fecha'), { target: { value: dayKey(next) } });
    fireEvent.change(within(form).getByLabelText('Hora'), { target: { value: '09:15' } });
    fireEvent.change(within(form).getByLabelText('Caption'), { target: { value: 'Texto nuevo' } });
    fireEvent.change(within(form).getByLabelText('Hashtags'), { target: { value: '#uno #dos' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Guardar' }));

    expect(await screen.findByText(/^Reprogramada para el/)).toBeInTheDocument();
    const saved = db().posts.find((post) => post.id === scheduled?.id);
    expect(dayKey(new Date(saved?.publish_at ?? 0))).toBe(dayKey(next));
    expect(new Date(saved?.publish_at ?? 0).getHours()).toBe(9);
    expect(saved?.caption).toBe('Texto nuevo');
    expect(saved?.hashtags).toEqual(['uno', 'dos']);
    expect(saved?.status).toBe('SCHEDULED');
  });

  it('el detalle de una publicada tiene el link al post y no deja editar', async () => {
    await open();
    const published = postWith('PUBLISHED');
    fireEvent.click(within(await rowOf(published?.clip.title ?? '', 'TikTok')).getByRole('button', { name: published?.clip.title }));
    const detail = await screen.findByRole('dialog', { name: 'Detalle de la publicación' });
    expect(await within(detail).findByRole('link', { name: 'Ver publicación' })).toHaveAttribute('href', published?.post_url);
    expect(within(detail).getByText('Sugerido por IA')).toBeInTheDocument();
    expect(within(detail).queryByRole('button', { name: /Editar/ })).not.toBeInTheDocument();
    expect(within(detail).queryByRole('button', { name: 'Cancelar publicación' })).not.toBeInTheDocument();
  });

  it('con la cuenta vencida, el error pide reconectar antes de reintentar', async () => {
    await open('vista=lista', 'redes-vencidas');
    const failed = postWith('FAILED');
    const row = await rowOf(failed?.clip.title ?? '', 'TikTok');
    fireEvent.click(within(row).getByRole('button', { name: failed?.clip.title }));
    const detail = await screen.findByRole('dialog', { name: 'Detalle de la publicación' });
    expect(await within(detail).findByText('La cuenta de TikTok está vencida. Reconectala y reintentá.')).toBeInTheDocument();
    expect(within(detail).getByRole('button', { name: 'Reintentar' })).toBeDisabled();
    expect(within(detail).getByText(/Reintentar se habilita cuando reconectes la cuenta de TikTok/)).toBeInTheDocument();
    fireEvent.click(within(detail).getByRole('button', { name: 'Reconectar' }));
    expect(await screen.findByRole('dialog', { name: 'Conectar TikTok' })).toBeInTheDocument();
  });

  it('rol Lector: ve todo, pero las acciones quedan deshabilitadas con su motivo', async () => {
    await open('vista=lista', 'rol-lector');
    const list = await table();
    for (const name of [/^Reintentar/, /^Cancelar/, /^Reprogramar/, /^Programar/]) {
      for (const button of within(list).getAllByRole('button', { name })) expect(button).toHaveAttribute('aria-disabled', 'true');
    }
    expect(within(list).getAllByRole('link', { name: /Ver la publicación/ }).length).toBeGreaterThan(0);
  });
});

describe('E2 · Publicaciones · calendario', () => {
  it('la semana es la vista por defecto: una ficha por clip y horario, con el estado de cada red en su nombre', async () => {
    // No `vista` in the URL: the week. The failed post is from six hours ago, which early on a Monday is last week.
    applyScenario('estudio-norte');
    const failed = postWith('FAILED');
    await open(`fecha=${dayKey(new Date(failed?.publish_at ?? 0))}`);
    const grid = await screen.findByRole('grid', { name: /Publicaciones de la semana/ });
    expect(within(grid).getAllByRole('columnheader')).toHaveLength(8);
    expect(screen.getByRole('button', { name: 'Semana' })).toHaveAttribute('aria-pressed', 'true');
    const card = await within(grid).findByRole('button', { name: new RegExp(`${failed?.clip.title}\\. TikTok: Error`) });
    fireEvent.click(card);
    const detail = await screen.findByRole('dialog', { name: 'Detalle de la publicación' });
    expect(await within(detail).findByText('TikTok rechazó la publicación. Revisá el video y el texto, y reintentá.')).toBeInTheDocument();
  });

  it('arrastrar una programada a otra celda la reprograma', async () => {
    // The seed is the same on every run: read where the scheduled post is, then open that week.
    applyScenario('estudio-norte');
    const scheduled = postWith('SCHEDULED');
    const at = new Date(scheduled?.publish_at ?? 0);
    await open(`fecha=${dayKey(at)}`);
    const grid = await screen.findByRole('grid', { name: /Publicaciones de la semana/ });
    const card = await within(grid).findByRole('button', { name: new RegExp(`${scheduled?.clip.title}\\. TikTok: Programado`) });
    expect(card).toHaveAttribute('draggable', 'true');
    const cells = within(grid).getAllByRole('gridcell');
    const from = cells.indexOf(card.closest('[role="gridcell"]') as HTMLElement);
    // One row below: same day, an hour later (or an hour earlier when the card is on the last row).
    const step = cells[from + 7] ? 1 : -1;
    const target = cells[from + 7 * step] as HTMLElement;
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };
    fireEvent.dragStart(card, { dataTransfer });
    fireEvent.dragOver(target, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });
    expect(await screen.findByText(new RegExp(`«${scheduled?.clip.title}» pasó al`))).toBeInTheDocument();
    const moved = new Date(db().posts.find((post) => post.id === scheduled?.id)?.publish_at ?? 0);
    const expected = new Date(at.getTime() + step * 3_600_000);
    expect([dayKey(moved), moved.getHours(), moved.getMinutes()]).toEqual([dayKey(expected), expected.getHours(), expected.getMinutes()]);
  });

  it('la ficha se mueve al soltarla, no se puede arrastrar otra mientras se guarda, y vuelve a su lugar si falla', async () => {
    applyScenario('estudio-norte');
    const scheduled = postWith('SCHEDULED');
    const at = new Date(scheduled?.publish_at ?? 0);
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => (release = resolve));
    server.use(
      http.patch('http://localhost:8000/api/v1/scheduled-posts/:id/', async () => {
        await held;
        return HttpResponse.json({ detail: 'boom' }, { status: 500 });
      }),
    );
    await open(`fecha=${dayKey(at)}`);
    const saved = db().posts.find((post) => post.id === scheduled?.id)?.publish_at;
    const grid = await screen.findByRole('grid', { name: /Publicaciones de la semana/ });
    const name = new RegExp(`${scheduled?.clip.title}\\. TikTok: Programado`);
    const card = await within(grid).findByRole('button', { name });
    const cells = within(grid).getAllByRole('gridcell');
    const from = cells.indexOf(card.closest('[role="gridcell"]') as HTMLElement);
    const step = cells[from + 7] ? 1 : -1;
    const target = cells[from + 7 * step] as HTMLElement;
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };
    fireEvent.dragStart(card, { dataTransfer });
    fireEvent.dragOver(target, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });

    // Before the server answers: the card is already in its new cell, and nothing can be dragged.
    await waitFor(() => expect(within(target).getByRole('button', { name })).toBeInTheDocument());
    expect(within(target).getByRole('button', { name })).toHaveAttribute('draggable', 'false');

    release();
    expect(await screen.findByText('No pudimos reprogramar la publicación.')).toBeInTheDocument();
    await waitFor(() => expect(within(cells[from] as HTMLElement).getByRole('button', { name })).toHaveAttribute('draggable', 'true'));
    expect(db().posts.find((post) => post.id === scheduled?.id)?.publish_at).toBe(saved);
  });

  it('los borradores no tienen fecha: no aparecen en el calendario', async () => {
    applyScenario('estudio-norte');
    const draft = postWith('DRAFT');
    await open(`vista=mes&fecha=${dayKey(new Date(draft?.publish_at ?? 0))}`);
    const grid = await screen.findByRole('grid', { name: /Publicaciones de / });
    await within(grid).findAllByRole('button', { name: /Programado|Publicado|Error/ });
    expect(within(grid).queryByRole('button', { name: /Borrador/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Borradores/ }));
    expect(await screen.findByText(/Los borradores todavía no tienen fecha/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Ver los borradores en la lista' }));
    await waitFor(() => expect(nav.get()).toMatch(/vista=lista/));
  });

  it('el mes muestra las fichas en su día y vuelve a la semana de ese día', async () => {
    await open('vista=mes');
    const grid = await screen.findByRole('grid', { name: /Publicaciones de / });
    expect(within(grid).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom']);
    expect(within(grid).getByText('Hoy')).toBeInTheDocument();
    expect(within(grid).getAllByRole('button', { name: /Programado|Publicado|Error/ }).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: 'Mes siguiente' }));
    await waitFor(() => expect(nav.get()).toMatch(/fecha=\d{4}-\d{2}-01/));
    fireEvent.click(screen.getByRole('button', { name: 'Hoy' }));
    await waitFor(() => expect(nav.get()).not.toMatch(/fecha=/));
  });

  it('sin publicaciones: una frase, una acción y nada más', async () => {
    await open('', 'workspace-vacio');
    expect(await screen.findByRole('heading', { name: 'Todavía no programaste nada' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Ir a mis clips' })).toHaveAttribute('href', '/proyectos');
    expect(screen.getByRole('link', { name: 'Ver redes' })).toHaveAttribute('href', '/ajustes/redes');
    expect(screen.queryByRole('group', { name: 'Filtrar por red' })).not.toBeInTheDocument();
  });
});
