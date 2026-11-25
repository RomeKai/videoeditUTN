import { cleanup, configure, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '@/api/mocks/server';
import { tick } from '@/api/mocks/sim';
import { applyScenario, saveDb } from '@/api/mocks/state';
import { PublishPanel } from './PublishPanel';
import { PublishPanelMount, parseNetworkParam, parsePublishParam, publishHref } from './PublishPanelMount';
import { FOREIGN_DRAFT_TTL_MS, orphanDrafts, rememberDraft, rememberedDrafts, tabId } from './draftRegistry';
import { dayKey } from './posts';
import { CLIPS, db, idle, renderScreen } from './testing';

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
  const view = renderScreen(<PublishPanel clipIds={clipIds} platforms={platforms} onClose={onClose} />, { scenario });
  const panel = await screen.findByRole('dialog', { name: clipIds.length > 1 ? `Publicar ${clipIds.length} clips` : 'Publicar clip' });
  await within(panel).findByRole('heading', { name: 'Redes' });
  return { onClose, panel, ...view };
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
    fireEvent.click(publishButton('Programar'));
    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 20_000 });

    const created = [CLIPS.first, CLIPS.second, CLIPS.third].map((clipId) => db().posts.filter((post) => post.video_clip === clipId && post.status === 'SCHEDULED' && new Date(post.publish_at).getHours() === 18 && new Date(post.publish_at).getTime() > start.getTime() - 86_400_000));
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

  it('lo que quedó de una recarga o de otro workspace se descarta la próxima vez que se abre la app', async () => {
    applyDefault();
    const data = db();
    const seedDraft = data.posts.find((post) => post.status === 'DRAFT');
    const mine = data.posts.find((post) => post.status === 'SCHEDULED');
    if (!seedDraft || !mine) throw new Error('faltan publicaciones de ejemplo');
    // A helper draft that a reload left behind: remembered by this tab, with no panel open.
    mine.status = 'DRAFT';
    saveDb(data);
    rememberDraft({ id: mine.id, clip: mine.video_clip, workspace: mine.workspace });
    expect(orphanDrafts(mine.workspace, []).map((entry) => entry.id)).toEqual([mine.id]);
    // The panel of that clip open again keeps it; another tab's draft waits an hour.
    expect(orphanDrafts(mine.workspace, [mine.video_clip])).toEqual([]);
    expect(orphanDrafts('otro-workspace', [])).toEqual([]);
    window.sessionStorage.setItem('oc.publicar.pestana', 'otra-pestana');
    expect(orphanDrafts(mine.workspace, [])).toEqual([]);
    expect(orphanDrafts(mine.workspace, [], Date.now() + FOREIGN_DRAFT_TTL_MS + 1).map((entry) => entry.id)).toEqual([mine.id]);
    window.sessionStorage.removeItem('oc.publicar.pestana');
    rememberDraft({ id: mine.id, clip: mine.video_clip, workspace: mine.workspace });
    expect(tabId()).not.toBe('otra-pestana');

    renderScreen(<PublishPanelMount />);
    await waitFor(() => expect(db().posts.some((post) => post.id === mine.id)).toBe(false));
    await waitFor(() => expect(rememberedDrafts()).toEqual([]));
    // A draft the user made on purpose is nobody's helper: it stays.
    expect(db().posts.some((post) => post.id === seedDraft.id)).toBe(true);
  });
});

describe('E1 · Publicar · envío parcial', () => {
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
