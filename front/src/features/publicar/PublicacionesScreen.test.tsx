import { cleanup, configure, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '@/api/mocks/server';
import { applyScenario } from '@/api/mocks/state';
import { db, editDb, idle, renderScenario } from '@/testing';
import { notificationHref } from '@/shell/nav';
import { PublicacionesScreen } from './PublicacionesScreen';
import { holdDraft, isRemembered, releaseDraft } from './draftRegistry';
import { failPublish } from '@/api/mocks/publish-errors';
import { ACCOUNT_GONE, NETWORK_NOUN, NO_ACCOUNT, dayKey } from './posts';

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
/**
 * The seed is relative to "now", and so is the week grid: it shows 08–21 plus the hours of whatever falls outside.
 * The dragging tests pin the clock (local time, so the same wall clock in any time zone) to cover the three shapes.
 */
const CLOCKS: Array<[string, Date]> = [
  ['en horario central', new Date(2026, 10, 25, 10, 0, 0)], // card on Thursday 14:00, rows above and below
  ['en la última fila', new Date(2026, 10, 27, 19, 33, 0)], // card on Saturday 23:33: it can only go up
  ['sola en una fila de madrugada', new Date(2026, 10, 27, 20, 9, 0)], // card on Sunday 00:09: moving it removes that row
];
const pinClock = (date: Date) => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(date);
};
afterEach(() => vi.useRealTimers());

/**
 * The cell of a day and hour, read from the grid as it is now. The grid adds and removes hour rows as cards move,
 * and React may replace the cells when it does: a cell kept from before the move can be a node that is gone.
 */
const cellAt = (grid: HTMLElement, at: Date): HTMLElement => {
  const hour = `${String(at.getHours()).padStart(2, '0')}:00`;
  const row = within(grid)
    .getAllByRole('rowheader')
    .findIndex((header) => header.textContent?.startsWith(hour));
  expect(row, `la fila de las ${hour}`).toBeGreaterThanOrEqual(0);
  const column = (at.getDay() + 6) % 7; // Monday first
  expect(within(grid).getAllByRole('columnheader')[column + 1]).toHaveTextContent(String(at.getDate()));
  return within(grid).getAllByRole('gridcell')[row * 7 + column] as HTMLElement;
};

const postWith = (status: string) => db().posts.find((post) => post.status === status);
const rowOf = async (title: string, network: string) => {
  const rows = within(await table()).getAllByRole('row');
  return rows.find((row) => row.textContent?.includes(title) && row.textContent.includes(network)) as HTMLElement;
};

describe('E2 · Publicaciones · lista', () => {
  let network = '';
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

  it('muestra la miniatura del clip cuando la publicación la trae, y el dibujo de siempre cuando no o cuando su URL ya no carga', async () => {
    const thumbnail = 'https://r2.example.test/clips/x/1.jpg?X-Amz-Expires=3600';
    applyScenario('estudio-norte');
    const published = postWith('PUBLISHED');
    nav.set('vista=lista');
    renderScenario(<PublicacionesScreen />, {
      keep: true,
      prepare: () =>
        editDb((data) => {
          for (const post of data.posts) if (post.id === published?.id) post.clip.thumbnail_url = thumbnail;
        }),
    });
    const list = await table();
    const images = () => Array.from(list.querySelectorAll('img'));
    await waitFor(() => expect(images().map((image) => image.getAttribute('src'))).toEqual([thumbnail]));
    // The temporary URL expired: no broken image is left on the row.
    fireEvent.error(images()[0] as HTMLImageElement);
    await waitFor(() => expect(images()).toHaveLength(0));
  });

  it('el nombre de la cuenta va entero en el tooltip: en la columna se corta, no se encima con la hora', async () => {
    const long = '@unnombredecuentalarguisimoquenoentraenlacolumna';
    applyScenario('estudio-norte');
    nav.set('vista=lista');
    renderScenario(<PublicacionesScreen />, {
      keep: true,
      prepare: () =>
        editDb((data) => {
          for (const account of data.socialAccounts) if (account.username) account.username = long;
        }),
    });
    const list = await table();
    const names = await within(list).findAllByText(long);
    expect(names.length).toBeGreaterThan(0);
    for (const name of names) expect(name).toHaveAttribute('title', long);
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

  it('el link de «una publicación falló» (campana y email) abre el detalle de esa publicación, aunque no esté en la semana a la vista', async () => {
    const failed = postWith('FAILED');
    if (!failed) throw new Error('falta la publicación fallida');
    // Next month: the week on screen does not have it.
    editDb((data) => {
      const post = data.posts.find((item) => item.id === failed.id);
      if (post) post.publish_at = new Date(Date.now() + 30 * 86_400_000).toISOString();
    });
    const notification = db().notifications.find((item) => item.type === 'post_failed' && item.post_id === failed.id);
    if (!notification) throw new Error('falta la notificación de la publicación fallida');
    const [path, search] = notificationHref(notification).split('?');
    expect(path).toBe('/publicaciones');

    await open(search);
    const detail = await screen.findByRole('dialog', { name: 'Detalle de la publicación' });
    expect(await within(detail).findByText('TikTok rechazó la publicación. Revisá el video y el texto, y reintentá.')).toBeInTheDocument();
    expect(within(detail).getAllByText(failed.clip.title).length).toBeGreaterThan(0);
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
    // It failed at its time, for another reason: that is why it cannot be edited any more.
    expect(within(detail).getByText(/pasó la hora de salida/)).toBeInTheDocument();
    fireEvent.click(within(detail).getByRole('button', { name: 'Reconectar' }));
    expect(await screen.findByRole('dialog', { name: 'Conectar TikTok' })).toBeInTheDocument();
  });

  /** The list over the default scenario with something changed in the database first. */
  const openWith = async (change: Parameters<typeof editDb>[0]) => {
    applyScenario('estudio-norte');
    nav.set('vista=lista');
    renderScenario(<PublicacionesScreen />, { keep: true, prepare: () => editDb(change) });
    await screen.findByRole('heading', { level: 1, name: 'Publicaciones' });
  };
  const detailOf = async (title: string, network: string) => {
    fireEvent.click(within(await rowOf(title, network)).getByRole('button', { name: title }));
    return screen.findByRole('dialog', { name: 'Detalle de la publicación' });
  };

  it('una falla de resultado incierto no dice «El clip no se publicó»: puede haber salido', async () => {
    let title = '';
    await openWith((data) => {
      const failed = data.posts.find((post) => post.status === 'FAILED');
      if (!failed) throw new Error('el escenario no tiene una publicación con error');
      failPublish(failed, 'outcome_unknown');
      title = failed.clip.title;
    });
    const detail = await detailOf(title, 'TikTok');
    expect(await within(detail).findByText(/^No sabemos si se publicó/)).toBeInTheDocument();
    expect(within(detail).getByText(/Puede que el clip se haya publicado igual\./)).toBeInTheDocument();
    expect(within(detail).queryByText(/El clip no se publicó/)).not.toBeInTheDocument();
  });

  it('una falla común sigue diciendo que el clip no se publicó', async () => {
    let title = '';
    await openWith((data) => {
      const failed = data.posts.find((post) => post.status === 'FAILED');
      if (!failed) throw new Error('el escenario no tiene una publicación con error');
      failPublish(failed, 'provider_rejected');
      title = failed.clip.title;
    });
    const detail = await detailOf(title, 'TikTok');
    expect(await within(detail).findByText(/El clip no se publicó\./)).toBeInTheDocument();
  });

  it('una programada que falló porque desconectaron la red antes de su hora lo dice: no «pasó la hora de salida»', async () => {
    let title = '';
    await openWith((data) => {
      const scheduled = data.posts.find((post) => post.status === 'SCHEDULED' && new Date(post.publish_at).getTime() > Date.now());
      if (!scheduled) throw new Error('el escenario no tiene una publicación programada');
      title = scheduled.clip.title;
      // What disconnecting the network does (the mock's handler, like the backend).
      for (const account of data.socialAccounts) {
        if (account.platform !== scheduled.platform) continue;
        account.status = 'disconnected';
        account.username = null;
        account.connected_at = null;
      }
      for (const post of data.posts) {
        if (post.platform === scheduled.platform && post.status === 'SCHEDULED') failPublish(post, 'account_not_connected', 'disconnected');
      }
      // Only this row has that clip and network.
      data.posts = data.posts.filter((post) => post === scheduled || post.clip.title !== title || post.platform !== scheduled.platform);
      network = NETWORK_NOUN[scheduled.platform];
    });
    const detail = await detailOf(title, network);
    expect(await within(detail).findByText(`La cuenta de ${network} está desconectada. Reconectala y reintentá.`)).toBeInTheDocument();
    expect(within(detail).getByText(new RegExp(`la publicación falló porque la cuenta de ${network} se desconectó\\.`))).toBeInTheDocument();
    expect(within(detail).queryByText(/pasó la hora de salida/)).not.toBeInTheDocument();
    expect(within(detail).getByRole('button', { name: 'Reintentar' })).toBeDisabled();
  });

  it('una publicada en una red que después se desconectó no dice «Sin cuenta», ni en la fila ni en el detalle', async () => {
    let title = '';
    await openWith((data) => {
      const published = data.posts.find((post) => post.status === 'PUBLISHED');
      if (!published) throw new Error('el escenario no tiene una publicación publicada');
      title = published.clip.title;
      network = NETWORK_NOUN[published.platform];
      for (const account of data.socialAccounts) {
        if (account.platform !== published.platform) continue;
        account.status = 'disconnected';
        account.username = null;
        account.connected_at = null;
      }
      // Only that one and a draft on the same network: the draft has no account, the published one had.
      const draft = data.posts.find((post) => post.status === 'DRAFT');
      if (!draft) throw new Error('el escenario no tiene un borrador');
      draft.platform = published.platform;
      data.posts = [published, draft];
    });
    const row = await rowOf(title, 'Publicado');
    expect(within(row).getByText(ACCOUNT_GONE)).toBeInTheDocument();
    expect(within(row).queryByText(NO_ACCOUNT)).not.toBeInTheDocument();
    // What has not gone out and has no account to go out on still says so.
    expect(within(await table()).getAllByText(NO_ACCOUNT)).toHaveLength(1);

    fireEvent.click(within(row).getByRole('button', { name: title }));
    const detail = await screen.findByRole('dialog', { name: 'Detalle de la publicación' });
    expect(await within(detail).findByText(ACCOUNT_GONE)).toBeInTheDocument();
    expect(within(detail).queryByText(NO_ACCOUNT)).not.toBeInTheDocument();
    expect(within(detail).queryByText('Sin conectar')).not.toBeInTheDocument();
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

  it.each(CLOCKS)('arrastrar una programada a otra celda la reprograma (%s)', async (_, clock) => {
    pinClock(clock);
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
    expect(cells[from]).toBe(cellAt(grid, at));
    // One row below: same day, an hour later (or an hour earlier when the card is on the last row).
    const step = cells[from + 7] ? 1 : -1;
    const target = cells[from + 7 * step] as HTMLElement;
    const expected = new Date(at.getTime() + step * 3_600_000);
    expect(target).toBe(cellAt(grid, expected));
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };
    fireEvent.dragStart(card, { dataTransfer });
    fireEvent.dragOver(target, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });
    expect(await screen.findByText(new RegExp(`«${scheduled?.clip.title}» pasó al`))).toBeInTheDocument();
    // The card is in the cell of its new day and hour, wherever the grid has that row now.
    await waitFor(() => expect(within(cellAt(grid, expected)).getByRole('button', { name: new RegExp(`${scheduled?.clip.title}\\. TikTok: Programado`) })).toBeInTheDocument());
    const moved = new Date(db().posts.find((post) => post.id === scheduled?.id)?.publish_at ?? 0);
    expect([dayKey(moved), moved.getHours(), moved.getMinutes()]).toEqual([dayKey(expected), expected.getHours(), expected.getMinutes()]);
  });

  it.each(CLOCKS)('la ficha se mueve al soltarla, no se puede arrastrar otra mientras se guarda, y vuelve a su lugar si falla (%s)', async (_, clock) => {
    pinClock(clock);
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
    const expected = new Date(at.getTime() + step * 3_600_000);
    expect(cells[from]).toBe(cellAt(grid, at));
    expect(target).toBe(cellAt(grid, expected));
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };
    fireEvent.dragStart(card, { dataTransfer });
    fireEvent.dragOver(target, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });

    // Before the server answers: the card is already in its new cell, and nothing can be dragged.
    await waitFor(() => expect(within(cellAt(grid, expected)).getByRole('button', { name })).toBeInTheDocument());
    expect(within(grid).getAllByRole('button', { name })).toHaveLength(1);
    expect(within(cellAt(grid, expected)).getByRole('button', { name })).toHaveAttribute('draggable', 'false');

    release();
    expect(await screen.findByText('No pudimos reprogramar la publicación.')).toBeInTheDocument();
    await waitFor(() => expect(within(cellAt(grid, at)).getByRole('button', { name })).toHaveAttribute('draggable', 'true'));
    expect(within(grid).getAllByRole('button', { name })).toHaveLength(1);
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

  /** The cell of a day in the month grid (the grid also shows the last days of the month before and the first of the next). */
  const monthCell = (grid: HTMLElement, day: Date): HTMLElement => {
    const first = new Date(day.getFullYear(), day.getMonth(), 1);
    // Monday first: the grid starts on the Monday of the week of the 1st.
    const index = ((first.getDay() + 6) % 7) + day.getDate() - 1;
    const cell = within(grid).getAllByRole('gridcell')[index] as HTMLElement;
    expect(cell.textContent?.startsWith(day.getDate() === 1 ? String(1) : String(day.getDate()))).toBe(true);
    return cell;
  };

  it('en el mes, arrastrar una programada a otro día la reprograma a la misma hora; a un día que ya pasó no hace nada', async () => {
    pinClock(new Date(2026, 10, 25, 10, 0, 0));
    applyScenario('estudio-norte');
    const scheduled = postWith('SCHEDULED');
    const at = new Date(scheduled?.publish_at ?? 0);
    expect(at.getMonth()).toBe(10);
    let writes = 0;
    server.events.on('request:start', ({ request }) => {
      if (request.method === 'PATCH') writes += 1;
    });
    await open('vista=mes');
    const grid = await screen.findByRole('grid', { name: /Publicaciones de noviembre de 2026/ });
    expect(await screen.findByText('Arrastrá una publicación programada para cambiarle el día.')).toBeInTheDocument();
    const name = new RegExp(`${scheduled?.clip.title}\\. TikTok: Programado`);
    const card = await within(monthCell(grid, at)).findByRole('button', { name });
    expect(card).toHaveAttribute('draggable', 'true');
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };

    // A day that already went by (what the rehearsal's script aimed at: the 2nd of the month shown, not of the next).
    const past = monthCell(grid, new Date(2026, 10, 2));
    fireEvent.dragStart(card, { dataTransfer });
    fireEvent.dragOver(past, { dataTransfer });
    fireEvent.drop(past, { dataTransfer });
    fireEvent.dragEnd(card, { dataTransfer });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(writes).toBe(0);
    expect(within(monthCell(grid, at)).getByRole('button', { name })).toBeInTheDocument();

    // A day to come: same hour and minute, another day.
    const expected = new Date(2026, 10, 30, at.getHours(), at.getMinutes(), 0, 0);
    const target = monthCell(grid, expected);
    fireEvent.dragStart(card, { dataTransfer });
    fireEvent.dragOver(target, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });
    expect(await screen.findByText(new RegExp(`«${scheduled?.clip.title}» pasó al`))).toBeInTheDocument();
    await waitFor(() => expect(within(monthCell(grid, expected)).getByRole('button', { name: new RegExp(`${scheduled?.clip.title}\\. TikTok: Programado`) })).toBeInTheDocument());
    await waitFor(() => expect(new Date(db().posts.find((post) => post.id === scheduled?.id)?.publish_at ?? 0).getTime()).toBe(expected.getTime()));
    expect(writes).toBe(1);
    server.events.removeAllListeners();
  });

  it('en el mes, lo que no se puede mover no se ofrece para arrastrar (publicadas; todo, para un Lector)', async () => {
    await open('vista=mes');
    const grid = await screen.findByRole('grid', { name: /Publicaciones de / });
    const published = await within(grid).findAllByRole('button', { name: /Publicado/ });
    for (const card of published) expect(card).toHaveAttribute('draggable', 'false');
    cleanup();
    await idle();
    await open('vista=mes', 'rol-lector');
    const readerGrid = await screen.findByRole('grid', { name: /Publicaciones de / });
    const cards = await within(readerGrid).findAllByRole('button', { name: /Programado|Publicado|Error/ });
    for (const card of cards) expect(card).toHaveAttribute('draggable', 'false');
    expect(screen.queryByText(/Arrastrá una publicación programada/)).not.toBeInTheDocument();
  });

  it('sin publicaciones: una frase, una acción y nada más', async () => {
    await open('', 'workspace-vacio');
    expect(await screen.findByRole('heading', { name: 'Todavía no programaste nada' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Ir a mis clips' })).toHaveAttribute('href', '/proyectos');
    expect(screen.getByRole('link', { name: 'Ver redes' })).toHaveAttribute('href', '/ajustes/redes');
    expect(screen.queryByRole('group', { name: 'Filtrar por red' })).not.toBeInTheDocument();
  });
});
