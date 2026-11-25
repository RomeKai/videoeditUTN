import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { server } from '@/api/mocks/server';
import { tick } from '@/api/mocks/sim';
import { saveDb } from '@/api/mocks/state';
import { PublishPanel } from './PublishPanel';
import { parsePublishParam, publishHref } from './PublishPanelMount';
import { dayKey } from './posts';
import { CLIPS, db, idle, renderScreen } from './testing';

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

async function openPanel(clipIds: string[] = [CLIPS.first], scenario?: string) {
  const onClose = vi.fn();
  const view = renderScreen(<PublishPanel clipIds={clipIds} onClose={onClose} />, { scenario });
  const panel = await screen.findByRole('dialog', { name: clipIds.length > 1 ? `Publicar ${clipIds.length} clips` : 'Publicar clip' });
  await within(panel).findByRole('heading', { name: 'Redes' });
  return { onClose, panel, ...view };
}

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

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(postsOf(CLIPS.first, 'QUEUED').map((post) => post.platform).sort()).toEqual(['INSTAGRAM_REELS', 'TIKTOK']);
    expect(postsOf(CLIPS.first, 'QUEUED').find((post) => post.platform === 'TIKTOK')?.tiktok?.privacy_level).toBe('PUBLIC_TO_EVERYONE');
    // The drafts created to get the AI text are the ones that went out: none is left behind.
    expect(postsOf(CLIPS.first, 'DRAFT')).toEqual([]);
    expect(await screen.findByText(/se está publicando en TikTok e Instagram/)).toBeInTheDocument();
  });

  it('el texto llega sugerido por la IA, se puede editar y regenerar', async () => {
    const { panel, client } = await openPanel();
    expect(await within(panel).findByText('Generando texto…')).toBeInTheDocument();
    await waitFor(() => expect(postsOf(CLIPS.first, 'DRAFT')).toHaveLength(2));
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
    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 5000 });

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
});
