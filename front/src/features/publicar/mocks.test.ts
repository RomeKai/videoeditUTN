// @vitest-environment node
// Node environment: the brand kit endpoints are multipart, and jsdom's FormData does not travel through Node's fetch.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { apiCall } from '@/api/client';
import * as brandKits from '@/api/contracts/brandkits';
import * as social from '@/api/contracts/social';
import { ApiError } from '@/api/errors';
import { server } from '@/api/mocks/server';
import { tick } from '@/api/mocks/sim';
import { USER_IDS, WORKSPACE_IDS } from '@/api/mocks/seed';
import { applyScenario, loadDb, mockTokens, saveDb } from '@/api/mocks/state';
import { setActiveWorkspaceId, setTokens } from '@/api/session';
import { EXAMPLE_IDS } from '@/dev/screens';
import { CLIPS } from './testing';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function signIn(scenario: string, user: keyof typeof USER_IDS = 'lucia') {
  applyScenario(scenario);
  setTokens(mockTokens(USER_IDS[user]));
  setActiveWorkspaceId(WORKSPACE_IDS.estudio);
}

/** Moves the simulated generation and publication forward without waiting. */
function advance(ms: number) {
  const db = loadDb();
  for (const post of db.posts) if (post._sim) post._sim.startedAt -= ms;
  tick(db, Date.now());
  saveDb(db);
}

const fail = async (promise: Promise<unknown>): Promise<ApiError> => {
  const error = await promise.catch((reason: unknown) => reason);
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
};

describe('contrato de publicar: borradores y texto con IA', () => {
  beforeEach(() => signIn('estudio-norte'));

  it('crear un borrador no publica nada y dispara la generación del texto', async () => {
    const draft = await apiCall(social.createPost, { body: { video_clip: CLIPS.first, platform: 'TIKTOK', draft: true } });
    expect(draft.status).toBe('DRAFT');
    expect(draft.seo_status).toBe('pending');
    expect(draft.caption).toBe('');

    advance(3000);
    const ready = await apiCall(social.getPost, { params: { id: draft.id } });
    expect(ready.status).toBe('DRAFT');
    expect(ready.seo_status).toBe('ready');
    expect(ready.caption).toBe(ready.generated_caption);
    expect(ready.hashtags.length).toBeGreaterThan(0);
  });

  it('un borrador se publica con publish_at null y se programa con una fecha', async () => {
    const tiktok = { privacy_level: 'SELF_ONLY', allow_comment: false, allow_duet: false, allow_stitch: false, brand_organic: false, branded_content: false } as const;
    const first = await apiCall(social.createPost, { body: { video_clip: CLIPS.first, platform: 'TIKTOK', draft: true } });
    // TikTok's privacy is asked for when it goes out, not for the draft.
    const error = await fail(apiCall(social.updatePost, { params: { id: first.id }, body: { publish_at: null } }));
    expect(error.status).toBe(400);
    expect(error.fieldErrors.tiktok?.[0]).toBe('Elegí quién puede ver el video.');
    const now = await apiCall(social.updatePost, { params: { id: first.id }, body: { publish_at: null, caption: 'Hola', tiktok } });
    // Like the backend: SCHEDULED for now, for the beat.
    expect(now.status).toBe('SCHEDULED');
    expect(Date.parse(now.publish_at)).toBeLessThanOrEqual(Date.now());
    expect(now.tiktok?.privacy_level).toBe('SELF_ONLY');

    const second = await apiCall(social.createPost, { body: { video_clip: CLIPS.first, platform: 'INSTAGRAM_REELS', draft: true } });
    const later = new Date(Date.now() + 3 * 86_400_000).toISOString();
    const scheduled = await apiCall(social.updatePost, { params: { id: second.id }, body: { publish_at: later } });
    expect(scheduled.status).toBe('SCHEDULED');
    expect(scheduled.publish_at).toBe(later);

    // Editing the text of a draft keeps it a draft.
    const third = await apiCall(social.createPost, { body: { video_clip: CLIPS.third, platform: 'INSTAGRAM_REELS', draft: true } });
    expect((await apiCall(social.updatePost, { params: { id: third.id }, body: { caption: 'Solo texto' } })).status).toBe('DRAFT');
  });

  it('regenerar trae un texto distinto y pisa el que estaba', async () => {
    const draft = await apiCall(social.createPost, { body: { video_clip: CLIPS.first, platform: 'INSTAGRAM_REELS', draft: true } });
    advance(3000);
    const first = await apiCall(social.getPost, { params: { id: draft.id } });
    await apiCall(social.updatePost, { params: { id: draft.id }, body: { caption: 'Lo edité a mano' } });

    const pending = await apiCall(social.regeneratePostSeo, { params: { id: draft.id } });
    expect(pending.seo_status).toBe('pending');
    // The previous text stays until the new one arrives.
    expect(pending.caption).toBe('Lo edité a mano');
    advance(3000);
    const second = await apiCall(social.getPost, { params: { id: draft.id } });
    expect(second.seo_status).toBe('ready');
    expect(second.caption).toBe(second.generated_caption);
    expect(second.generated_caption).not.toBe(first.generated_caption);
  });

  it('no regenera lo que ya salió', async () => {
    const published = loadDb().posts.find((post) => post.status === 'PUBLISHED');
    expect((await fail(apiCall(social.regeneratePostSeo, { params: { id: published?.id ?? '' } }))).status).toBe(409);
  });
});

describe('contrato de publicar: plan y rol', () => {
  it('plan Gratis: un borrador no se puede programar, pero sí publicar ahora', async () => {
    signIn('plan-gratis');
    // A clip with no other network taken: without crossposting, it may go out on one.
    const draft = await apiCall(social.createPost, { body: { video_clip: CLIPS.fourth, platform: 'INSTAGRAM_REELS', draft: true } });
    // The plan has no SEO: nothing is generated.
    expect(draft.seo_status).toBe('none');
    const error = await fail(apiCall(social.updatePost, { params: { id: draft.id }, body: { publish_at: new Date(Date.now() + 86_400_000).toISOString() } }));
    expect(error.status).toBe(403);
    expect(error.code).toBe('scheduling');
    expect((await fail(apiCall(social.regeneratePostSeo, { params: { id: draft.id } }))).code).toBe('seo');
    expect((await apiCall(social.updatePost, { params: { id: draft.id }, body: { publish_at: null } })).status).toBe('SCHEDULED');
  });

  it('rol Lector: no crea borradores ni regenera', async () => {
    signIn('rol-lector', 'diego');
    expect((await fail(apiCall(social.createPost, { body: { video_clip: CLIPS.first, platform: 'TIKTOK', draft: true } }))).status).toBe(403);
    const draft = loadDb().posts.find((post) => post.status === 'DRAFT');
    expect((await fail(apiCall(social.regeneratePostSeo, { params: { id: draft?.id ?? '' } }))).status).toBe(403);
  });
});

describe('contrato de publicar: como el backend', () => {
  beforeEach(() => signIn('estudio-norte'));
  const tiktok = { privacy_level: 'SELF_ONLY', allow_comment: false, allow_duet: false, allow_stitch: false, brand_organic: false, branded_content: false } as const;

  it('cada 409 dice por qué con su código', async () => {
    const db = loadDb();
    const of = (status: string) => db.posts.find((post) => post.workspace === WORKSPACE_IDS.estudio && post.status === status)?.id ?? '';
    const [queued, processing, published, scheduled] = [of('QUEUED'), of('PROCESSING'), of('PUBLISHED'), of('SCHEDULED')];
    expect((await fail(apiCall(social.updatePost, { params: { id: queued }, body: { caption: 'x' } }))).code).toBe('post_in_flight');
    expect((await fail(apiCall(social.updatePost, { params: { id: published }, body: { caption: 'x' } }))).code).toBe('post_published');
    expect((await fail(apiCall(social.cancelPost, { params: { id: published } }))).code).toBe('post_published');
    expect((await fail(apiCall(social.retryPost, { params: { id: scheduled } }))).code).toBe('post_not_failed');
    expect((await fail(apiCall(social.regeneratePostSeo, { params: { id: queued } }))).code).toBe('post_in_flight');
    // The PROCESSING post of the seed started an hour ago: it is stuck, so it can be retried; a fresh one cannot.
    const fresh = loadDb();
    const post = fresh.posts.find((item) => item.id === processing);
    if (post) post.updated_at = new Date().toISOString();
    saveDb(fresh);
    expect((await fail(apiCall(social.retryPost, { params: { id: processing } }))).code).toBe('post_in_flight');
    expect((await fail(apiCall(social.cancelPost, { params: { id: processing } }))).code).toBe('post_in_flight');
  });

  it('reintentar vuelve a la cola una vez; el segundo reintento es un 409', async () => {
    const failed = loadDb().posts.find((post) => post.workspace === WORKSPACE_IDS.estudio && post.status === 'FAILED');
    const retried = await apiCall(social.retryPost, { params: { id: failed?.id ?? '' } });
    expect([retried.status, retried.error_code]).toEqual(['SCHEDULED', null]);
    expect((await fail(apiCall(social.retryPost, { params: { id: retried.id } }))).code).toBe('post_not_failed');
  });

  it('una publicación cuyo texto con IA falló vuelve a borrador al reintentar', async () => {
    const db = loadDb();
    const failed = db.posts.find((post) => post.workspace === WORKSPACE_IDS.estudio && post.status === 'FAILED');
    if (!failed) throw new Error('falta una publicación fallida');
    Object.assign(failed, { error_log: 'seo_no_clip_transcript', error_code: 'seo_no_clip_transcript', seo_status: 'failed' });
    saveDb(db);
    const draft = await apiCall(social.retryPost, { params: { id: failed.id } });
    expect([draft.status, draft.seo_status]).toEqual(['DRAFT', 'failed']);
  });

  it('el lote dice cuál publicación tiene el error y no crea ninguna', async () => {
    const before = loadDb().posts.length;
    const error = await fail(
      apiCall(social.createPostsBulk, {
        body: { posts: [{ video_clip: CLIPS.first, platform: 'INSTAGRAM_REELS' }, { video_clip: CLIPS.first, platform: 'TIKTOK', caption: 'x'.repeat(2201), tiktok }] },
      }),
    );
    expect([error.status, error.data.index]).toEqual([400, 1]);
    expect(error.fieldErrors.caption?.[0]).toMatch(/supera el límite de TikTok/);
    const twice = await fail(apiCall(social.createPostsBulk, { body: { posts: [{ video_clip: CLIPS.first, platform: 'INSTAGRAM_REELS' }, { video_clip: CLIPS.first, platform: 'INSTAGRAM_REELS' }] } }));
    expect(twice.fieldErrors.posts?.[0]).toBe('La misma publicación (clip y red) está dos veces.');
    expect(loadDb().posts.length).toBe(before);
  });

  it('los hashtags llegan sin «#» ni repetidos, y uno inválido es un 400', async () => {
    const post = await apiCall(social.createPost, { body: { video_clip: CLIPS.first, platform: 'INSTAGRAM_REELS', draft: true, hashtags: ['#Mendoza', 'mendoza', 'vlog'] } });
    expect(post.hashtags).toEqual(['Mendoza', 'vlog']);
    expect((await fail(apiCall(social.createPost, { body: { video_clip: CLIPS.first, platform: 'TIKTOK', draft: true, hashtags: ['dos palabras'] } }))).status).toBe(400);
  });

  it('un borrador no pide la cuenta conectada; mandarlo sí', async () => {
    const draft = await apiCall(social.createPost, { body: { video_clip: CLIPS.first, platform: 'YOUTUBE_SHORTS', draft: true } });
    expect(draft.status).toBe('DRAFT');
    expect((await fail(apiCall(social.updatePost, { params: { id: draft.id }, body: { publish_at: null } }))).code).toBe('account_not_connected');
  });

  it('regenerar mientras otro texto está en camino no pide otro', async () => {
    const draft = await apiCall(social.createPost, { body: { video_clip: CLIPS.first, platform: 'INSTAGRAM_REELS', draft: true } });
    expect(draft.seo_status).toBe('pending');
    const again = await apiCall(social.regeneratePostSeo, { params: { id: draft.id } });
    expect(again.seo_status).toBe('pending');
    expect(loadDb().posts.find((post) => post.id === draft.id)?._sim?.regen).toBeUndefined();
  });
});

describe('contrato de publicar: plan sin varias redes', () => {
  it('un clip ya programado en una red no se programa en otra, aunque sea en otro pedido', async () => {
    signIn('plan-gratis');
    // Its failed TikTok post does not hold the network: it went nowhere.
    await apiCall(social.createPost, { body: { video_clip: CLIPS.fourth, platform: 'INSTAGRAM_REELS' } });
    const tiktok = { privacy_level: 'SELF_ONLY', allow_comment: false, allow_duet: false, allow_stitch: false, brand_organic: false, branded_content: false } as const;
    expect((await fail(apiCall(social.createPost, { body: { video_clip: CLIPS.fourth, platform: 'TIKTOK', tiktok } }))).code).toBe('crossposting');
    // A draft publishes nothing.
    expect((await apiCall(social.createPost, { body: { video_clip: CLIPS.fourth, platform: 'TIKTOK', draft: true } })).status).toBe('DRAFT');
  });
});

describe('contrato de brand kits: posición del logo', () => {
  beforeEach(() => signIn('estudio-norte'));

  it('la posición se guarda con el resto del kit y la del backend de hoy (ausente) es válida', async () => {
    const before = await apiCall(brandKits.getBrandKit, { params: { id: EXAMPLE_IDS.brandKit } });
    // The mock answers as the proposed backend would; today's backend leaves the field out, and the schema takes that too.
    expect(before.watermark_position).toBe('bottom_right');
    const { watermark_position: _absent, ...today } = before;
    void _absent;
    expect(brandKits.BrandKit.safeParse(today).success).toBe(true);
    const saved = await apiCall(brandKits.updateBrandKit, { params: { id: EXAMPLE_IDS.brandKit }, body: { watermark_position: 'top_left', accent_color: '#3dd6c4' } });
    expect(saved.watermark_position).toBe('top_left');
    expect(saved.accent_color).toBe('#3DD6C4');
    const created = await apiCall(brandKits.createBrandKit, { body: { name: 'Podcast Vender', watermark_position: 'bottom_left', watermark_logo: new File(['x'], 'logo.png', { type: 'image/png' }) } });
    expect(created.watermark_position).toBe('bottom_left');
    expect(created.watermark_logo).toMatch(/logo\.png$/);
  });
});
