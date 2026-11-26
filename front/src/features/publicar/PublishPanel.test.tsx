import { cleanup, configure, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '@/api/mocks/server';
import { tick } from '@/api/mocks/sim';
import { applyScenario, saveDb } from '@/api/mocks/state';
import { setActiveWorkspaceId } from '@/api/session';
import { db, idle, renderScenario } from '@/testing';
import { PublishPanel } from './PublishPanel';
import { PublishPanelMount, parseNetworkParam, parsePublishParam, publishHref } from './PublishPanelMount';
import { RECENT_DRAFT_MS, STALE_DRAFT_MS, findOrphanDrafts, holdDraft, isRemembered, orphanDrafts, releaseDraft, rememberedDrafts, touchDrafts } from './draftRegistry';
import { dayKey } from './posts';
import { CLIPS } from './testing';

// These flows wait on the mocked API several times: on a busy CI runner the default 1 s / 5 s are not enough.
configure({ asyncUtilTimeout: 5000 });
vi.setConfig({ testTimeout: 30_000 });

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(async () => {
  cleanup();
  await idle();
  server.resetHandlers();
});
afterAll(() => server.close());

const postsOf = (clipId: string, status: string) => db().posts.filter((post) => post.video_clip === clipId && post.status === status);
/** The primary button of the panel's footer. */
const publishButton = (name = 'Publicar') => within(screen.getByRole('region', { name: 'Acciones' })).getByRole('button', { name });
const whenButton = (name: string) => within(screen.getByRole('group', { name: 'Cuándo publicar' })).getByRole('button', { name });

async function openPanel(clipIds: string[] = [CLIPS.first], scenario?: string, platforms?: Array<'TIKTOK' | 'INSTAGRAM_REELS' | 'YOUTUBE_SHORTS'>) {
  const onClose = vi.fn();
  const view = renderScenario(<PublishPanel clipIds={clipIds} platforms={platforms} onClose={onClose} />, { scenario });
  const panel = await screen.findByRole('dialog', { name: clipIds.length > 1 ? `Publicar ${clipIds.length} clips` : 'Publicar clip' });
  await within(panel).findByRole('heading', { name: 'Redes' });
  return { onClose, panel, client: view.queryClient, ...view };
}

/** Seeds the default scenario before the panel renders, to read the database first. */
const applyDefault = () => applyScenario('estudio-norte');

/** The mocked SEO generation takes two seconds: this makes them pass. */
function finishSeo() {
  const data = db();
  for (const post of data.posts) if (post._sim) post._sim.startedAt -= 3000;
  tick(data, Date.now());
  saveDb(data);
}

describe('E1 · Publicar', () => {
  it('muestra cada red con su estado y ofrece conectar la que falta', async () => {
    const { panel } = await openPanel();
    expect(within(panel).getByRole('checkbox', { name: 'Publicar en TikTok' })).toBeChecked();
    expect(within(panel).getByRole('checkbox', { name: 'Publicar en Instagram Reels' })).toBeChecked();
    expect(within(panel).getByRole('checkbox', { name: /YouTube Shorts: conectá la cuenta/ })).toBeDisabled();
    expect(within(panel).getAllByText('Conectada')).toHaveLength(2);
    expect(within(panel).getByText('Sin conectar')).toBeInTheDocument();

    fireEvent.click(within(panel).getByRole('button', { name: /Conectar YouTube Shorts/ }));
    expect(await screen.findByRole('dialog', { name: 'Conectar YouTube' })).toBeInTheDocument();
  });

  it('no publica hasta elegir la privacidad de TikTok; después sale en las dos redes', async () => {
    const { panel, onClose } = await openPanel();
    expect(publishButton()).toBeDisabled();
    expect(within(panel).getByText('Elegí la privacidad de TikTok')).toBeInTheDocument();
    expect(within(panel).getByLabelText('Requisitos de TikTok')).toHaveTextContent('3 de 4 requisitos · falta elegir quién puede ver el video');
    expect(within(panel).getByRole('tab', { name: /TikTok/ })).toHaveTextContent('Falta 1');
    // TikTok asks for it on every publication: there is no default.
    const privacy = within(panel).getByLabelText('Quién puede ver este video') as HTMLSelectElement;
    expect(privacy.value).toBe('');
    for (const name of ['Comentarios', 'Stitch']) expect(within(panel).getByRole('checkbox', { name })).not.toBeChecked();
    // The account has duets off.
    expect(within(panel).getByRole('checkbox', { name: 'Dúos' })).toBeDisabled();

    fireEvent.change(privacy, { target: { value: 'PUBLIC_TO_EVERYONE' } });
    expect(within(panel).getByRole('tab', { name: /TikTok/ })).toHaveTextContent('Listo');
    expect(publishButton()).toBeEnabled();
    fireEvent.click(publishButton());

    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 20_000 });
    expect(postsOf(CLIPS.first, 'QUEUED').map((post) => post.platform).sort()).toEqual(['INSTAGRAM_REELS', 'TIKTOK']);
    expect(postsOf(CLIPS.first, 'QUEUED').find((post) => post.platform === 'TIKTOK')?.tiktok?.privacy_level).toBe('PUBLIC_TO_EVERYONE');
    // The drafts created to get the AI text are the ones that went out: none is left behind.
    expect(postsOf(CLIPS.first, 'DRAFT')).toEqual([]);
    expect(await screen.findByText(/se está publicando en TikTok e Instagram/)).toBeInTheDocument();
  });

  it('el texto llega sugerido por la IA, se puede editar y regenerar', async () => {
    const { panel, client } = await openPanel();
    expect(await within(panel).findByText('Generando texto…')).toBeInTheDocument();
    // Only the network on screen gets its helper draft; Instagram's is asked for when its tab opens.
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT').map((post) => post.platform)).toEqual(['TIKTOK']));
    finishSeo();
    await client.invalidateQueries();

    const caption = (await within(panel).findByLabelText('Caption')) as HTMLTextAreaElement;
    expect(caption.value).toMatch(/^Lo mejor de «/);
    expect(within(panel).getByText('Sugerido por IA')).toBeInTheDocument();
    expect(within(panel).getByText('#clips')).toBeInTheDocument();

    fireEvent.change(caption, { target: { value: 'Mi propio texto' } });
    expect(within(panel).getByText('Editado por vos')).toBeInTheDocument();

    fireEvent.click(within(panel).getByRole('button', { name: 'Regenerar' }));
    expect(await within(panel).findByText('Generando texto…')).toBeInTheDocument();
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT').some((post) => post.seo_status === 'pending')).toBe(true));
    finishSeo();
    await client.invalidateQueries();
    await waitFor(() => expect((within(panel).getByLabelText('Caption') as HTMLTextAreaElement).value).toMatch(/lo que nadie te cuenta/));
  });

  it('cerrar sin publicar no deja borradores', async () => {
    const { panel, onClose } = await openPanel();
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toHaveLength(1));
    fireEvent.click(within(panel).getByRole('tab', { name: /Instagram/ }));
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toHaveLength(2));
    fireEvent.click(within(panel).getByRole('button', { name: 'Cancelar' }));
    expect(onClose).toHaveBeenCalled();
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toEqual([]));
  });

  it('un texto por encima del límite se marca y bloquea el botón', async () => {
    // Plan Gratis: one network and no AI text, so the caption box is there from the start.
    const { panel } = await openPanel([CLIPS.first], 'plan-gratis');
    fireEvent.change(within(panel).getByLabelText('Quién puede ver este video'), { target: { value: 'SELF_ONLY' } });
    fireEvent.change(within(panel).getByLabelText('Caption'), { target: { value: 'x'.repeat(2341) } });
    expect(within(panel).getByRole('alert')).toHaveTextContent('Sobran 141 caracteres. TikTok no acepta textos de más de 2.200');
    expect(within(panel).getByRole('tab', { name: /TikTok/ })).toHaveTextContent('Sobran 141');
    expect(within(panel).getByText('Acortá el texto de TikTok: sobran 141')).toBeInTheDocument();
    expect(publishButton()).toBeDisabled();
  });

  it('plan Gratis: una sola red y «Programar» con candado', async () => {
    const { panel } = await openPanel([CLIPS.first], 'plan-gratis');
    const tiktok = within(panel).getByRole('checkbox', { name: 'Publicar en TikTok' });
    const instagram = within(panel).getByRole('checkbox', { name: 'Publicar en Instagram Reels' });
    expect(tiktok).toBeChecked();
    expect(instagram).not.toBeChecked();
    expect(within(panel).getByText('Tu plan permite una red por publicación')).toBeInTheDocument();
    fireEvent.click(instagram);
    expect(instagram).toBeChecked();
    expect(tiktok).not.toBeChecked();

    const schedule = whenButton('Programar');
    expect(schedule).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(schedule);
    expect(whenButton('Ahora')).toHaveAttribute('aria-pressed', 'true');
    expect(within(panel).getAllByText(/Disponible en el plan/).length).toBeGreaterThan(0);
    // The plan has no AI text: nothing is created by opening the panel.
    expect(postsOf(CLIPS.first, 'DRAFT')).toEqual([]);
  });

  it('rol Lector: ve el panel pero no publica ni conecta redes', async () => {
    const { panel } = await openPanel([CLIPS.first], 'rol-lector');
    expect(within(panel).getByText('Tu rol no publica')).toBeInTheDocument();
    fireEvent.change(within(panel).getByLabelText('Quién puede ver este video'), { target: { value: 'SELF_ONLY' } });
    expect(publishButton()).toHaveAttribute('aria-disabled', 'true');
    expect(within(panel).getByRole('button', { name: /Conectar YouTube Shorts/ })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(publishButton());
    expect(db().posts.filter((post) => post.video_clip === CLIPS.first && post.status !== 'PUBLISHED')).toEqual([]);
  });

  it('cuenta vencida: pide reconectar y no deja elegir la red', async () => {
    const { panel } = await openPanel([CLIPS.first], 'redes-vencidas');
    expect(within(panel).getByText('Vencida')).toBeInTheDocument();
    expect(within(panel).getByRole('checkbox', { name: /TikTok: reconectá la cuenta/ })).toBeDisabled();
    expect(within(panel).getByText('Conectá una red para publicar')).toBeInTheDocument();
    expect(publishButton()).toBeDisabled();
    fireEvent.click(within(panel).getByRole('button', { name: /Reconectar TikTok/ }));
    expect(await screen.findByRole('dialog', { name: 'Conectar TikTok' })).toBeInTheDocument();
  });

  it('varios clips: uno por día a la misma hora, y se programan todos', async () => {
    const { panel, onClose } = await openPanel([CLIPS.first, CLIPS.second, CLIPS.third]);
    expect(within(panel).getByText('3 clips seleccionados')).toBeInTheDocument();
    fireEvent.click(whenButton('Programar'));
    expect(within(panel).getByRole('button', { name: 'Uno por día' })).toHaveAttribute('aria-pressed', 'true');

    const start = new Date();
    start.setDate(start.getDate() + 3);
    fireEvent.change(within(panel).getByLabelText('Desde'), { target: { value: dayKey(start) } });
    fireEvent.change(within(panel).getByLabelText('Hora'), { target: { value: '18:00' } });
    const order = within(panel).getByRole('list', { name: 'Orden y fecha de cada clip' });
    expect(within(order).getAllByRole('listitem')).toHaveLength(3);
    expect(within(panel).getByText(/3 clips × 2 redes = 6 publicaciones/)).toBeInTheDocument();
    expect(within(panel).getAllByText('Falta la privacidad')).toHaveLength(3);

    // The second clip goes first.
    const secondTitle = db().projects.flatMap((project) => project.clips).find((clip) => clip.id === CLIPS.second)?.title as string;
    fireEvent.click(within(order).getByRole('button', { name: `Subir «${secondTitle}»` }));
    expect(within(order).getAllByRole('listitem')[0]).toHaveTextContent(secondTitle);

    fireEvent.change(within(panel).getByLabelText('Quién puede ver estos videos'), { target: { value: 'PUBLIC_TO_EVERYONE' } });
    // What was scheduled before: the seed has posts at "now + N days", which fall at 18:00 too when the test runs at that hour.
    const before = new Set(db().posts.filter((post) => post.status === 'SCHEDULED').map((post) => post.id));
    fireEvent.click(publishButton('Programar'));
    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 20_000 });

    const created = [CLIPS.first, CLIPS.second, CLIPS.third].map((clipId) => db().posts.filter((post) => post.video_clip === clipId && post.status === 'SCHEDULED' && !before.has(post.id) && new Date(post.publish_at).getHours() === 18 && new Date(post.publish_at).getTime() > start.getTime() - 86_400_000));
    expect(created.map((posts) => posts.length)).toEqual([2, 2, 2]);
    const days = created.map((posts) => dayKey(new Date(posts[0]?.publish_at ?? 0)));
    expect(new Set(days).size).toBe(3);
    // The second clip was moved to the first day.
    expect(days[1]).toBe(dayKey(start));
    expect(await screen.findByText(/6 publicaciones programadas/)).toBeInTheDocument();
  });

  it('una fecha que ya pasó no se puede programar', async () => {
    const { panel } = await openPanel([CLIPS.first], 'plan-creador');
    fireEvent.click(whenButton('Programar'));
    fireEvent.change(within(panel).getByLabelText('Fecha'), { target: { value: '2020-01-01' } });
    expect(within(panel).getByText('Esa fecha y hora ya pasaron.')).toBeInTheDocument();
    expect(within(panel).getByText('Elegí una fecha y una hora que todavía no pasaron')).toBeInTheDocument();
    expect(publishButton('Programar')).toBeDisabled();
    expect(within(panel).getByText(/Zona horaria:/)).toBeInTheDocument();
  });
});

describe('?publicar=', () => {
  it('toma ids válidos, sin repetidos, y arma el link que abre el panel', () => {
    expect(parsePublishParam(`${CLIPS.first},nada,${CLIPS.second},${CLIPS.first}`)).toEqual([CLIPS.first, CLIPS.second]);
    expect(parsePublishParam(null)).toEqual([]);
    expect(publishHref('/proyectos/1/clips', [CLIPS.first, CLIPS.second], 'vista=grilla')).toBe(`/proyectos/1/clips?vista=grilla&publicar=${CLIPS.first},${CLIPS.second}`);
  });

  it('`red=` elige con qué redes abre el panel', () => {
    expect(parseNetworkParam('INSTAGRAM_REELS,nada')).toEqual(['INSTAGRAM_REELS']);
    expect(parseNetworkParam(null)).toEqual([]);
    expect(publishHref('/publicaciones', [CLIPS.first], '', ['INSTAGRAM_REELS'])).toBe(`/publicaciones?publicar=${CLIPS.first}&red=INSTAGRAM_REELS`);
  });
});

const API = 'http://localhost:8000/api/v1';
const draftsOf = (clipIds: string[]) => db().posts.filter((post) => clipIds.includes(post.video_clip) && post.status === 'DRAFT');
const outOf = (clipId: string, platform?: string) =>
  db().posts.filter((post) => post.video_clip === clipId && post.status !== 'DRAFT' && post.created_at > new Date(Date.now() - 60_000).toISOString() && (!platform || post.platform === platform));
const expire = (platform: string) => {
  const data = db();
  for (const account of data.socialAccounts) if (account.platform === platform) account.status = 'expired';
  saveDb(data);
};
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe('E1 · Publicar · borradores de ayuda', () => {
  it('abre con una sola red cuando se la piden (desde un borrador de Publicaciones)', async () => {
    const { panel } = await openPanel([CLIPS.first], undefined, ['INSTAGRAM_REELS']);
    expect(within(panel).getByRole('checkbox', { name: 'Publicar en Instagram Reels' })).toBeChecked();
    expect(within(panel).getByRole('checkbox', { name: 'Publicar en TikTok' })).not.toBeChecked();
    expect(within(panel).getAllByRole('tab')).toHaveLength(1);
  });

  it('cerrar mientras todavía se están creando no deja ninguno', async () => {
    const ids = [CLIPS.first, CLIPS.second, CLIPS.third];
    applyDefault();
    const before = draftsOf(ids).length;
    const { panel } = await openPanel(ids);
    // Close as soon as the first one exists: the rest of the queue must not run, and the one in flight is cancelled on arrival.
    await waitFor(() => expect(draftsOf(ids).length).toBeGreaterThan(before));
    fireEvent.click(within(panel).getByRole('button', { name: 'Cancelar' }));
    await pause(1200);
    expect(draftsOf(ids)).toHaveLength(before);
    expect(rememberedDrafts()).toEqual([]);
  });

  it('irse sin cerrar (Atrás, otra pantalla, otra selección) también los descarta', async () => {
    const { unmount } = await openPanel();
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toHaveLength(1));
    expect(rememberedDrafts().map((entry) => entry.clip)).toEqual([CLIPS.first]);
    // What the layout does when `?publicar=` leaves the URL without the panel's own close().
    unmount();
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toEqual([]));
    await waitFor(() => expect(rememberedDrafts()).toEqual([]));
  });

  it('si el workspace activo cambia mientras el panel se cierra, el borrador se descarta en el suyo', async () => {
    const { unmount } = await openPanel();
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toHaveLength(1));
    const [draft] = postsOf(CLIPS.first, 'DRAFT');
    const asked: Array<string | null> = [];
    server.use(
      http.post(`${API}/scheduled-posts/:id/cancel/`, ({ request }) => {
        asked.push(request.headers.get('X-Workspace-Id'));
        return undefined;
      }),
    );
    // The switch: the id changes in the same frame the panel goes away, before its cleanup runs.
    const other = db().workspaces.find((workspace) => workspace.id !== draft?.workspace);
    setActiveWorkspaceId(other?.id ?? null);
    unmount();
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toEqual([]));
    // Not with the new workspace's header: there the API does not know the draft, and it would stay behind.
    expect(asked).toEqual([draft?.workspace]);
    await waitFor(() => expect(rememberedDrafts()).toEqual([]));
    setActiveWorkspaceId(draft?.workspace ?? null);
  });

  it('el que vuelve después de irse se cancela al llegar', async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => (release = resolve));
    let asked = false;
    server.use(
      http.post(`${API}/scheduled-posts/`, async () => {
        asked = true;
        await held;
        return undefined;
      }),
    );
    const { unmount } = await openPanel();
    await waitFor(() => expect(asked).toBe(true));
    unmount();
    release();
    await pause(600);
    expect(postsOf(CLIPS.first, 'DRAFT')).toEqual([]);
  });

  /** A helper draft that a reload left behind: remembered in the browser, with no panel using it. */
  const leftover = (change: (post: ReturnType<typeof db>['posts'][number]) => void = () => undefined) => {
    applyDefault();
    const data = db();
    const post = data.posts.find((item) => item.status === 'SCHEDULED');
    if (!post) throw new Error('falta una publicación de ejemplo');
    post.status = 'DRAFT';
    // As the panel creates them: no options chosen yet.
    post.tiktok = null;
    change(post);
    saveDb(data);
    holdDraft({ id: post.id, clip: post.video_clip, workspace: post.workspace });
    // Its panel let go of it (the page went away before the discard got through).
    releaseDraft(post.id);
    return post;
  };
  /**
   * The same draft as another tab's open panel has it: remembered, refreshed `age` ms ago, and not
   * held by this page. (That tab is slow, or in the background: it does not answer when asked.)
   */
  const inAnotherTab = (age: number) => {
    const post = leftover();
    const at = Date.now() - age;
    window.localStorage.setItem('oc.publicar.borradores', JSON.stringify(rememberedDrafts().map((entry) => (entry.id === post.id ? { ...entry, at } : entry))));
    return post;
  };
  const exists = (id: string) => db().posts.some((post) => post.id === id);

  it('lo que quedó de una recarga o de otro workspace se descarta la próxima vez que se abre la app', async () => {
    const mine = leftover();
    const seedDraft = db().posts.find((post) => post.status === 'DRAFT' && post.id !== mine.id);
    expect((await orphanDrafts(mine.workspace, [])).map((entry) => entry.id)).toEqual([mine.id]);
    // The panel of that clip open again keeps it; in another workspace it is not touched.
    expect(await orphanDrafts(mine.workspace, [mine.video_clip])).toEqual([]);
    expect(await orphanDrafts('otro-workspace', [])).toEqual([]);

    renderScenario(<PublishPanelMount />, { keep: true });
    await waitFor(() => expect(exists(mine.id)).toBe(false));
    await waitFor(() => expect(rememberedDrafts()).toEqual([]));
    // A draft the user made on purpose is nobody's helper: it stays.
    expect(exists(seedDraft?.id ?? '')).toBe(true);
  });

  it('un borrador que quedó y la persona después editó no se descarta: pasa a ser suyo', async () => {
    const edited = leftover((post) => {
      post.caption = 'Lo edité a mano en Publicaciones, me lo quedo';
    });
    renderScenario(<PublishPanelMount />, { keep: true });
    await waitFor(() => expect(isRemembered(edited.id)).toBe(false));
    await pause(300);
    expect(exists(edited.id)).toBe(true);
  });

  it.each([
    ['los hashtags', (post: ReturnType<typeof db>['posts'][number]) => void (post.hashtags = ['mendoza', 'vinos'])],
    ['las opciones de TikTok', (post: ReturnType<typeof db>['posts'][number]) => void (post.tiktok = { privacy_level: 'PUBLIC_TO_EVERYONE', allow_comment: true, allow_duet: false, allow_stitch: false, brand_organic: false, branded_content: false })],
  ])('si la persona solo tocó %s (el texto quedó igual), tampoco se descarta', async (_what, change) => {
    const edited = leftover((post) => {
      post.caption = post.generated_caption ?? '';
      change(post);
    });
    renderScenario(<PublishPanelMount />, { keep: true });
    await waitFor(() => expect(isRemembered(edited.id)).toBe(false));
    await pause(300);
    expect(exists(edited.id)).toBe(true);
  });

  it('si nadie responde pero el borrador se usó hace menos de dos minutos, se queda: una pestaña lenta o en segundo plano no lo pierde', async () => {
    // Its panel refreshed the entry a minute ago, and its tab does not answer in time.
    const theirs = inAnotherTab(60_000);
    const found = await findOrphanDrafts(theirs.workspace, []);
    expect(found.orphans).toEqual([]);
    // It is looked at again when the two minutes are up.
    const entry = rememberedDrafts().find((item) => item.id === theirs.id);
    expect(found.retryAt).toBe((entry?.at ?? 0) + RECENT_DRAFT_MS + 1);
    expect((await orphanDrafts(theirs.workspace, [], Date.now() + 61_000)).map((item) => item.id)).toEqual([theirs.id]);

    // The sweeper leaves it alone now…
    renderScenario(<PublishPanelMount />, { keep: true });
    await pause(700);
    expect(exists(theirs.id)).toBe(true);
    expect(isRemembered(theirs.id)).toBe(true);
  });

  it('el barrido vuelve a pasar cuando se cumplen los dos minutos y descarta lo que nadie reclamó', async () => {
    const theirs = inAnotherTab(RECENT_DRAFT_MS - 400);
    renderScenario(<PublishPanelMount />, { keep: true });
    await pause(350);
    expect(exists(theirs.id)).toBe(true);
    // The second pass comes at least a second later.
    await waitFor(() => expect(exists(theirs.id)).toBe(false), { timeout: 4000 });
    expect(isRemembered(theirs.id)).toBe(false);
  });

  it('si un panel toma el borrador mientras el barrido lo estaba leyendo, no se descarta', async () => {
    const theirs = leftover();
    let reading: () => void = () => undefined;
    const read = new Promise<void>((resolve) => (reading = resolve));
    let answer: () => void = () => undefined;
    const held = new Promise<void>((resolve) => (answer = resolve));
    server.use(
      // Holds the answer and then lets the mocked API give it (a handler that returns nothing passes the request on).
      http.get('http://localhost:8000/api/v1/scheduled-posts/:id/', async ({ params }) => {
        if (params.id !== theirs.id) return;
        reading();
        await held;
      }),
    );
    renderScenario(<PublishPanelMount />, { keep: true });
    // The sweeper already decided it was an orphan and is waiting for the server…
    await read;
    // …when the panel of that clip (this page's or another tab's) takes it back.
    holdDraft({ id: theirs.id, clip: theirs.video_clip, workspace: theirs.workspace });
    answer();
    await pause(400);
    expect(exists(theirs.id)).toBe(true);
    expect(isRemembered(theirs.id)).toBe(true);
    releaseDraft(theirs.id);
  });

  it('lo que un panel soltó al cerrarse no espera los dos minutos: ya es de nadie', async () => {
    const mine = leftover();
    expect(rememberedDrafts().find((entry) => entry.id === mine.id)?.at).toBe(0);
    expect((await findOrphanDrafts(mine.workspace, [])).orphans.map((entry) => entry.id)).toEqual([mine.id]);
    // Taken back (the page came back from the cache, the panel opened again): in use from now on.
    holdDraft({ id: mine.id, clip: mine.video_clip, workspace: mine.workspace });
    expect(rememberedDrafts().find((entry) => entry.id === mine.id)?.at).toBeGreaterThan(0);
    releaseDraft(mine.id);
  });

  it('si una pestaña duplicada también lo tiene abierto, soltarlo acá no le saca sus dos minutos', async () => {
    const shared = leftover();
    // Both tabs hold it: this page and a duplicate of it.
    holdDraft({ id: shared.id, clip: shared.video_clip, workspace: shared.workspace });
    const stored = rememberedDrafts();
    window.localStorage.setItem('oc.publicar.borradores', JSON.stringify(stored.map((entry) => (entry.id === shared.id ? { ...entry, holders: [...(entry.holders ?? []), 'otra-pestaña'] } : entry))));
    // This page's panel closes.
    releaseDraft(shared.id);
    const entry = rememberedDrafts().find((item) => item.id === shared.id);
    expect(entry?.at).toBeGreaterThan(0);
    expect(entry?.holders).toEqual(['otra-pestaña']);
    // The other tab does not answer in time: the draft is still kept, as any recently used one.
    expect((await findOrphanDrafts(shared.workspace, [])).orphans).toEqual([]);
  });

  it('el barrido no toca (ni olvida) un borrador si el workspace cambió mientras corría: el 404 sería del otro workspace', async () => {
    const theirs = leftover();
    let reading: () => void = () => undefined;
    const read = new Promise<void>((resolve) => (reading = resolve));
    let answer: () => void = () => undefined;
    const held = new Promise<void>((resolve) => (answer = resolve));
    const asked: Array<string | null> = [];
    server.use(
      http.get('http://localhost:8000/api/v1/scheduled-posts/:id/', async ({ params, request }) => {
        if (params.id !== theirs.id) return;
        asked.push(request.headers.get('X-Workspace-Id'));
        reading();
        await held;
      }),
    );
    renderScenario(<PublishPanelMount />, { keep: true });
    await read;
    expect(asked).toEqual([theirs.workspace]);
    // The person switches workspace while the sweeper waits for the server.
    const other = db().workspaces.find((workspace) => workspace.id !== theirs.workspace);
    setActiveWorkspaceId(other?.id ?? null);
    answer();
    await pause(500);
    // Not discarded with the wrong workspace, and still remembered for the next visit to its own.
    expect(exists(theirs.id)).toBe(true);
    expect(isRemembered(theirs.id)).toBe(true);
    expect(asked).toEqual([theirs.workspace]);
  });

  it('otra instancia de la app (una pestaña duplicada) no descarta el borrador de un panel abierto', async () => {
    await openPanel();
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toHaveLength(1));
    const [live] = postsOf(CLIPS.first, 'DRAFT');
    // A second app with the same storage and no panel of its own.
    renderScenario(<PublishPanelMount />, { keep: true });
    await pause(900);
    expect(exists(live?.id ?? '')).toBe(true);
    expect(isRemembered(live?.id ?? '')).toBe(true);
  });

  it('el panel de otra pestaña responde por sus borradores', async () => {
    const theirs = leftover();
    // The other tab: it holds the draft and answers when asked.
    const other = new BroadcastChannel('oc.publicar.borradores');
    other.onmessage = (event: MessageEvent<{ type: string; nonce: string; ids: string[] }>) => {
      if (event.data.type === 'ask') other.postMessage({ type: 'held', nonce: event.data.nonce, ids: event.data.ids });
    };
    expect(await orphanDrafts(theirs.workspace, [])).toEqual([]);
    other.close();
    expect((await orphanDrafts(theirs.workspace, [])).map((entry) => entry.id)).toEqual([theirs.id]);
  });

  it('sin forma de preguntarle a otras pestañas, solo se descarta lo que nadie usa hace una hora; un panel abierto lo mantiene al día', async () => {
    const theirs = inAnotherTab(0);
    vi.stubGlobal('BroadcastChannel', undefined);
    try {
      const later = Date.now() + STALE_DRAFT_MS + 1;
      expect(await orphanDrafts(theirs.workspace, [])).toEqual([]);
      expect((await orphanDrafts(theirs.workspace, [], later)).map((entry) => entry.id)).toEqual([theirs.id]);
      // The panel that has it open refreshes it every minute, however long it stays open.
      touchDrafts([theirs.id], later);
      expect(await orphanDrafts(theirs.workspace, [], later + 1000)).toEqual([]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('si la página vuelve de la caché de atrás/adelante, el panel pide de nuevo sus borradores', async () => {
    await openPanel();
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toHaveLength(1));
    const [first] = postsOf(CLIPS.first, 'DRAFT');
    // Leaving the page (also into the back/forward cache) discards them.
    fireEvent(window, new Event('pagehide'));
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toEqual([]));
    // Restored with the panel still on screen.
    fireEvent(window, Object.assign(new Event('pageshow'), { persisted: true }));
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toHaveLength(1));
    expect(postsOf(CLIPS.first, 'DRAFT')[0]?.id).not.toBe(first?.id);
  });
});

describe('E1 · Publicar · envío parcial', () => {
  it('si publicar queda sin respuesta y la persona aprieta de nuevo, sale con la misma clave: no se publica dos veces', async () => {
    const keys: Array<string | null> = [];
    let lose = true;
    server.use(
      http.post(`${API}/scheduled-posts/`, async ({ request }) => {
        // No helper draft for this pair: the publication is created by the submit itself.
        if (((await request.clone().json()) as { draft?: boolean }).draft) return HttpResponse.json({ detail: 'sin borradores' }, { status: 500 });
        keys.push(request.headers.get('Idempotency-Key'));
        if (!lose) return undefined;
        lose = false;
        // The server did it (the same request, answered by the mocked API); the answer never reached the browser.
        await fetch(request.clone());
        return HttpResponse.error();
      }),
    );
    const { panel, onClose } = await openPanel([CLIPS.first], undefined, ['INSTAGRAM_REELS']);
    fireEvent.change(await within(panel).findByLabelText('Caption'), { target: { value: 'Una sola vez' } });
    fireEvent.click(publishButton());
    await within(panel).findByRole('alert');
    expect(onClose).not.toHaveBeenCalled();
    expect(outOf(CLIPS.first, 'INSTAGRAM_REELS')).toHaveLength(1);
    // The primary button, whatever it is called after a failure.
    fireEvent.click(within(screen.getByRole('region', { name: 'Acciones' })).getAllByRole('button').at(-1) as HTMLElement);
    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 10_000 });
    // First try, the server's own copy of it, and the retry: one key for all.
    expect(keys.length).toBeGreaterThanOrEqual(2);
    expect(keys[0]).toBeTruthy();
    expect(new Set(keys).size).toBe(1);
    expect(outOf(CLIPS.first, 'INSTAGRAM_REELS')).toHaveLength(1);
  });

  it('una red sale y la otra falla: lo dice red por red, no reenvía la que salió y cerrar no la cancela', async () => {
    const { panel, onClose } = await openPanel();
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toHaveLength(1));
    // Instagram expires between opening the panel and publishing.
    expire('INSTAGRAM_REELS');
    fireEvent.change(within(panel).getByLabelText('Quién puede ver este video'), { target: { value: 'PUBLIC_TO_EVERYONE' } });
    fireEvent.click(publishButton());

    const alert = await within(panel).findByRole('alert');
    expect(alert).toHaveTextContent('Salió en TikTok; Instagram falló');
    expect(alert).toHaveTextContent(/vencida/);
    expect(onClose).not.toHaveBeenCalled();
    const tiktok = outOf(CLIPS.first, 'TIKTOK');
    expect(tiktok.map((post) => post.status)).toEqual(['QUEUED']);
    expect(outOf(CLIPS.first, 'INSTAGRAM_REELS')).toEqual([]);

    // Instagram is not usable any more: nothing is left to send, and TikTok is never sent again.
    const sentAgain: string[] = [];
    server.use(
      http.patch(`${API}/scheduled-posts/:id/`, ({ params }) => {
        sentAgain.push(String(params.id));
        return undefined;
      }),
    );
    expect(await within(panel).findByRole('checkbox', { name: /Instagram Reels: reconectá la cuenta/ })).toBeDisabled();
    expect(within(panel).getByRole('checkbox', { name: 'Publicar en TikTok' })).toBeDisabled();

    // Closing discards helper drafts only: what went out stays out.
    fireEvent.click(within(panel).getByRole('button', { name: 'Cancelar' }));
    expect(onClose).toHaveBeenCalled();
    await pause(600);
    expect(db().posts.some((post) => post.id === tiktok[0]?.id)).toBe(true);
    expect(sentAgain).toEqual([]);
    expect(postsOf(CLIPS.first, 'DRAFT')).toEqual([]);
    expect(await screen.findByText('Salió en TikTok')).toBeInTheDocument();
  });

  it('las nuevas salieron y un borrador falló: reintentar no las crea de nuevo y manda solo lo que faltaba', async () => {
    applyDefault();
    const plain = db().projects.find((project) => !project.metadata.use_seo && project.clips.some((clip) => clip.status === 'completed'));
    const other = plain?.clips.find((clip) => clip.status === 'completed');
    if (!other) throw new Error('falta un clip sin texto con IA');
    const { panel, onClose } = await openPanel([CLIPS.first, other.id]);
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toHaveLength(1));
    let failed = false;
    server.use(
      http.patch(`${API}/scheduled-posts/:id/`, () => {
        if (failed) return undefined;
        failed = true;
        return HttpResponse.json({ detail: 'boom' }, { status: 500 });
      }),
    );
    fireEvent.change(within(panel).getByLabelText('Quién puede ver estos videos'), { target: { value: 'PUBLIC_TO_EVERYONE' } });
    fireEvent.click(publishButton());
    const alert = await within(panel).findByRole('alert');
    expect(alert).toHaveTextContent(/TikTok \(1 de 2\), Instagram \(2 de 2\); TikTok falló/);
    expect(outOf(other.id)).toHaveLength(2);
    expect(outOf(CLIPS.first).map((post) => post.platform)).toEqual(['INSTAGRAM_REELS']);

    await waitFor(() => expect(publishButton()).toBeEnabled());
    fireEvent.click(publishButton());
    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 20_000 });
    // Still two of the other clip (not four), and the one that had failed went out.
    expect(outOf(other.id)).toHaveLength(2);
    expect(outOf(CLIPS.first).map((post) => post.platform).sort()).toEqual(['INSTAGRAM_REELS', 'TIKTOK']);
    expect(draftsOf([CLIPS.first, other.id]).filter((post) => post.video_clip === CLIPS.first)).toEqual([]);
  });

  it('un borrador que ya salió por otro lado no traba el envío: se da por publicado', async () => {
    const { panel, onClose } = await openPanel([CLIPS.first], undefined, ['TIKTOK']);
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toHaveLength(1));
    // Another tab published the same draft.
    const data = db();
    const draft = data.posts.find((post) => post.video_clip === CLIPS.first && post.status === 'DRAFT');
    if (!draft) throw new Error('falta el borrador');
    draft.status = 'PUBLISHED';
    saveDb(data);
    fireEvent.change(within(panel).getByLabelText('Quién puede ver este video'), { target: { value: 'SELF_ONLY' } });
    fireEvent.click(publishButton());
    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 20_000 });
    // Nothing was created to replace it, and it was not deleted.
    expect(db().posts.filter((post) => post.video_clip === CLIPS.first && post.platform === 'TIKTOK' && post.id === draft.id)).toHaveLength(1);
    expect(outOf(CLIPS.first, 'TIKTOK').filter((post) => post.id !== draft.id)).toEqual([]);
  });
});

describe('E1 · Publicar · borrador que dejó de serlo', () => {
  it('un borrador que el backend pasó a error (falló su texto con IA) no cuenta como publicado', async () => {
    const { panel, onClose } = await openPanel([CLIPS.first], undefined, ['TIKTOK']);
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toHaveLength(1));
    // What `_fail_seo_post` does today: the draft becomes FAILED.
    const data = db();
    const draft = data.posts.find((post) => post.video_clip === CLIPS.first && post.status === 'DRAFT');
    if (!draft) throw new Error('falta el borrador');
    draft.status = 'FAILED';
    saveDb(data);
    fireEvent.change(within(panel).getByLabelText('Quién puede ver este video'), { target: { value: 'SELF_ONLY' } });
    fireEvent.click(publishButton());
    expect(await within(panel).findByRole('alert')).toHaveTextContent('No pudimos publicar en TikTok');
    expect(onClose).not.toHaveBeenCalled();
    expect(outOf(CLIPS.first, 'TIKTOK').filter((post) => post.status !== 'FAILED')).toEqual([]);

    // The next attempt creates the publication instead of insisting on the draft.
    await waitFor(() => expect(publishButton()).toBeEnabled());
    fireEvent.click(publishButton());
    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 20_000 });
    expect(outOf(CLIPS.first, 'TIKTOK').filter((post) => post.status === 'QUEUED')).toHaveLength(1);
  });
});

describe('E1 · Publicar · TikTok', () => {
  it('el contenido de marca no puede ser «Solo yo», y avisa de la política de contenido de marca', async () => {
    const { panel } = await openPanel([CLIPS.first], 'plan-gratis');
    const privacy = within(panel).getByLabelText('Quién puede ver este video') as HTMLSelectElement;
    fireEvent.change(privacy, { target: { value: 'SELF_ONLY' } });
    fireEvent.click(within(panel).getByRole('switch', { name: 'Divulgar contenido comercial' }));
    // Private: branded content cannot be ticked.
    expect(within(panel).getByRole('checkbox', { name: /Contenido de marca/ })).toBeDisabled();
    expect(within(panel).getByText(/No se puede con la privacidad «Solo yo»/)).toBeInTheDocument();

    fireEvent.change(privacy, { target: { value: 'PUBLIC_TO_EVERYONE' } });
    fireEvent.click(within(panel).getByRole('checkbox', { name: /Contenido de marca/ }));
    // Ticked: «Solo yo» is not offered any more, and the legal line names both documents.
    expect(within(privacy).getByRole('option', { name: /Solo yo/ })).toBeDisabled();
    expect(within(panel).getByRole('link', { name: 'política de contenido de marca' })).toBeInTheDocument();
    expect(within(panel).getByRole('link', { name: 'confirmación de uso de música de TikTok' })).toBeInTheDocument();
    expect(publishButton()).toBeEnabled();
  });
});
