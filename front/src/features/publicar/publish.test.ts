import { describe, expect, it } from 'vitest';
import type { ScheduledPost, SocialAccount } from '@/api/contracts/social';
import {
  EMPTY_TIKTOK,
  buildSubmission,
  checkNetwork,
  findClashes,
  joinNetworks,
  outcomeSummary,
  scheduleFor,
  scheduleSummary,
  tiktokOptions,
  type PanelClip,
  type TextValue,
} from './publish';

const clip = (id: string, duration = 31): PanelClip => ({
  id,
  title: `Clip ${id}`,
  duration,
  aspectRatio: '9:16',
  projectId: 'p',
  projectTitle: 'Vlog Mendoza',
  resolution: '1080p',
  ready: true,
  seo: true,
});

const account = (platform: SocialAccount['platform'], limits: Partial<SocialAccount['limits']> = {}): SocialAccount => ({
  platform,
  status: 'connected',
  username: '@estudionorte',
  connected_at: null,
  limits: { caption_max_chars: 2200, hashtags_max: 30, max_duration_seconds: 600, aspect_ratios: ['9:16'], ...limits },
  tiktok: platform === 'TIKTOK' ? { privacy_options: ['PUBLIC_TO_EVERYONE', 'SELF_ONLY'], comment_disabled: false, duet_disabled: true, stitch_disabled: false } : null,
});

const text = (caption: string, hashtags: string[] = []): TextValue => ({ caption, hashtags });
const short = () => text('El dique Potrerillos a las siete y media de la tarde.', ['mendoza', 'potrerillos']);

describe('requisitos por red', () => {
  it('TikTok: la privacidad cuenta como requisito y es lo único que falta', () => {
    const check = checkNetwork({ platform: 'TIKTOK', account: account('TIKTOK'), clips: [clip('a')], textOf: short, tiktok: EMPTY_TIKTOK });
    expect(`${check.passed} de ${check.total}`).toBe('3 de 4');
    expect(check.ok).toBe(false);
    expect(check.failing?.missing).toBe('falta elegir quién puede ver el video');
    expect(check.failing?.action).toBe('Elegí la privacidad de TikTok');
    expect(check.failing?.severe).toBe(false);

    const chosen = checkNetwork({ platform: 'TIKTOK', account: account('TIKTOK'), clips: [clip('a')], textOf: short, tiktok: { ...EMPTY_TIKTOK, privacy: 'SELF_ONLY' } });
    expect(chosen.ok).toBe(true);
  });

  it('TikTok con contenido comercial pide elegir de quién es la marca', () => {
    const tiktok = { ...EMPTY_TIKTOK, privacy: 'SELF_ONLY' as const, disclose: true };
    const check = checkNetwork({ platform: 'TIKTOK', account: account('TIKTOK'), clips: [clip('a')], textOf: short, tiktok });
    expect(check.failing?.key).toBe('disclosure');
    expect(checkNetwork({ platform: 'TIKTOK', account: account('TIKTOK'), clips: [clip('a')], textOf: short, tiktok: { ...tiktok, brandOrganic: true } }).ok).toBe(true);
  });

  it('Instagram: el texto pasado del límite dice cuánto sobra, contando los hashtags', () => {
    const long = text('x'.repeat(2300), ['mendoza']);
    const check = checkNetwork({ platform: 'INSTAGRAM_REELS', account: account('INSTAGRAM_REELS'), clips: [clip('a')], textOf: () => long, tiktok: EMPTY_TIKTOK });
    expect(`${check.passed} de ${check.total}`).toBe('3 de 4');
    expect(check.overBy).toBe(2300 + 'mendoza'.length + 3 - 2200);
    expect(check.failing?.missing).toBe('el texto se pasa del límite');
    expect(check.failing?.action).toBe('Acortá el texto de Instagram: sobran 110');
    expect(check.failing?.severe).toBe(true);
  });

  it('un clip más largo de lo que acepta la red no pasa', () => {
    const check = checkNetwork({ platform: 'YOUTUBE_SHORTS', account: account('YOUTUBE_SHORTS', { max_duration_seconds: 60 }), clips: [clip('a', 45), clip('b', 80)], textOf: short, tiktok: EMPTY_TIKTOK });
    expect(check.failing?.key).toBe('duration');
    expect(check.failing?.missing).toBe('«Clip b» dura 1:20 y YouTube acepta hasta 1:00');
  });

  it('con varios clips, el resumen habla de todos', () => {
    const check = checkNetwork({ platform: 'INSTAGRAM_REELS', account: account('INSTAGRAM_REELS'), clips: [clip('a', 27), clip('b', 45)], textOf: short, tiktok: EMPTY_TIKTOK });
    expect(check.ok).toBe(true);
    expect(check.requirements.map((item) => item.met)).toEqual(['formato 9:16', 'de 0:27 a 0:45, máximo 10:00', 'textos dentro de 2.200 caracteres', '2 de 30 hashtags']);
  });
});

describe('opciones de TikTok', () => {
  it('sin privacidad no hay opciones; lo que la cuenta tiene apagado va en falso', () => {
    expect(tiktokOptions(EMPTY_TIKTOK, account('TIKTOK'))).toBeUndefined();
    const options = tiktokOptions({ ...EMPTY_TIKTOK, privacy: 'PUBLIC_TO_EVERYONE', comment: true, duet: true, stitch: true, brandOrganic: true }, account('TIKTOK'));
    expect(options).toEqual({ privacy_level: 'PUBLIC_TO_EVERYONE', allow_comment: true, allow_duet: false, allow_stitch: true, brand_organic: false, branded_content: false });
  });
});

describe('cuándo', () => {
  const start = new Date(2026, 10, 25, 18, 0);

  it('«uno por día» pone cada clip un día después del anterior, a la misma hora', () => {
    const dates = scheduleFor(['a', 'b', 'c'], 'later', start, 'daily');
    expect([...dates.values()].map((date) => date?.getDate())).toEqual([25, 26, 27]);
    expect([...dates.values()].every((date) => date?.getHours() === 18)).toBe(true);
    expect(scheduleSummary([...dates.values()])).toBe('25 – 27 nov 2026, 18:00');
  });

  it('«todos juntos» y «ahora»', () => {
    expect([...scheduleFor(['a', 'b'], 'later', start, 'together').values()].map((date) => date?.getDate())).toEqual([25, 25]);
    expect([...scheduleFor(['a', 'b'], 'now', start, 'daily').values()]).toEqual([null, null]);
    expect(scheduleSummary([null, null])).toBe('ahora');
    expect(scheduleSummary([start])).toBe('25 nov 2026, 18:00');
  });

  it('avisa cuando ya hay otra publicación programada a esa hora en la misma red', () => {
    const other = { id: 'x', video_clip: 'otro', platform: 'INSTAGRAM_REELS', status: 'SCHEDULED', publish_at: new Date(2026, 10, 26, 18, 0).toISOString(), clip: { title: 'Subimos a 3.000 metros en una hora' } } as ScheduledPost;
    const dates = scheduleFor(['a', 'b'], 'later', start, 'daily');
    const clashes = findClashes(dates, ['TIKTOK', 'INSTAGRAM_REELS'], [other]);
    expect(clashes).toHaveLength(1);
    expect(clashes[0]).toMatchObject({ title: 'Subimos a 3.000 metros en una hora', sameClip: false });
    expect(findClashes(dates, ['TIKTOK'], [other])).toEqual([]);
  });

  it('el mismo clip ya programado en esa red y a esa hora también avisa: saldría dos veces', () => {
    const same = { id: 'x', video_clip: 'b', platform: 'INSTAGRAM_REELS', status: 'SCHEDULED', publish_at: new Date(2026, 10, 26, 18, 0, 40).toISOString(), clip: { title: 'Clip b' } } as ScheduledPost;
    const dates = scheduleFor(['a', 'b'], 'later', start, 'daily');
    expect(findClashes(dates, ['INSTAGRAM_REELS'], [same])).toEqual([expect.objectContaining({ platform: 'INSTAGRAM_REELS', sameClip: true })]);
    // Another minute, or a publication that is not scheduled any more, is not a clash.
    expect(findClashes(dates, ['INSTAGRAM_REELS'], [{ ...same, publish_at: new Date(2026, 10, 26, 18, 1).toISOString() }])).toEqual([]);
    expect(findClashes(dates, ['INSTAGRAM_REELS'], [{ ...same, status: 'PUBLISHED' }])).toEqual([]);
  });
});

describe('lo que se envía', () => {
  const draft = { id: 'draft-1', video_clip: 'a', platform: 'INSTAGRAM_REELS' } as ScheduledPost;
  const all = [
    { clipId: 'a', platform: 'TIKTOK' },
    { clipId: 'a', platform: 'INSTAGRAM_REELS' },
    { clipId: 'b', platform: 'TIKTOK' },
    { clipId: 'b', platform: 'INSTAGRAM_REELS' },
  ] as const;
  const base = {
    textOf: () => text('Hola', ['uno']),
    tiktok: tiktokOptions({ ...EMPTY_TIKTOK, privacy: 'SELF_ONLY' }, account('TIKTOK')),
  };

  it('los borradores se publican con PATCH y el resto se crea; ahora va con publish_at null', () => {
    const { creates, updates } = buildSubmission({
      ...base,
      pairs: [...all],
      dates: scheduleFor(['a', 'b'], 'now', null, 'together'),
      hasText: () => true,
      draftOf: (clipId, platform) => (clipId === 'a' && platform === 'INSTAGRAM_REELS' ? draft : undefined),
    });
    expect(updates).toEqual([{ clipId: 'a', platform: 'INSTAGRAM_REELS', draft, request: { id: 'draft-1', publish_at: null, caption: 'Hola', hashtags: ['uno'] } }]);
    expect(creates.map((item) => `${item.video_clip}/${item.platform}`)).toEqual(['a/TIKTOK', 'b/TIKTOK', 'b/INSTAGRAM_REELS']);
    expect(creates[0]?.tiktok?.privacy_level).toBe('SELF_ONLY');
    expect(creates[2]?.tiktok).toBeUndefined();
    expect(creates.every((item) => item.publish_at === null)).toBe(true);
  });

  it('solo se envía lo que falta: un par que ya salió no vuelve a aparecer', () => {
    const { creates, updates } = buildSubmission({
      ...base,
      // a/TikTok and b/* already went out in the first attempt.
      pairs: [{ clipId: 'a', platform: 'INSTAGRAM_REELS' }],
      dates: scheduleFor(['a', 'b'], 'now', null, 'together'),
      hasText: () => true,
      draftOf: () => draft,
    });
    expect(creates).toEqual([]);
    expect(updates.map((item) => item.request.id)).toEqual(['draft-1']);
  });

  it('si el texto de la IA todavía no llegó, no se manda texto: lo completa el backend', () => {
    const start = new Date(2026, 10, 25, 18, 0);
    const { updates, creates } = buildSubmission({
      ...base,
      pairs: [
        { clipId: 'a', platform: 'INSTAGRAM_REELS' },
        { clipId: 'b', platform: 'INSTAGRAM_REELS' },
      ],
      dates: scheduleFor(['a', 'b'], 'later', start, 'together'),
      hasText: () => false,
      draftOf: (clipId) => (clipId === 'a' ? draft : undefined),
    });
    expect(updates[0]?.request).toEqual({ id: 'draft-1', publish_at: start.toISOString() });
    expect(creates).toEqual([{ video_clip: 'b', platform: 'INSTAGRAM_REELS', publish_at: start.toISOString() }]);
  });
});

describe('qué pasó con cada red', () => {
  const one = [{ id: 'a' }];
  const two = [{ id: 'a' }, { id: 'b' }];
  const all = ['TIKTOK', 'INSTAGRAM_REELS', 'YOUTUBE_SHORTS'] as const;

  it('una salió y otra no: lo dice red por red y aclara que no se reenvía', () => {
    const result = outcomeSummary({
      platforms: [...all],
      clips: one,
      sent: ['a|TIKTOK'],
      failures: [{ clipId: 'a', platform: 'INSTAGRAM_REELS', message: 'La cuenta de Instagram Reels está vencida. Reconectala para publicar.' }],
      when: 'now',
    });
    expect(result.partial).toBe(true);
    expect(result.title).toBe('Salió en TikTok; Instagram falló');
    expect(result.detail).toMatch(/^Instagram: La cuenta de Instagram Reels está vencida/);
    expect(result.detail).toMatch(/no se vuelve a enviar/);
  });

  it('nada salió: es un error, con el motivo', () => {
    const result = outcomeSummary({ platforms: [...all], clips: one, sent: [], failures: [{ clipId: 'a', platform: 'TIKTOK', message: 'Probá de nuevo.' }], when: 'later' });
    expect(result).toEqual({ title: 'No pudimos programar en TikTok', detail: 'TikTok: Probá de nuevo.', partial: false });
  });

  it('varios clips: cuenta cuántos salieron y cuántos no en cada red', () => {
    const result = outcomeSummary({
      platforms: [...all],
      clips: two,
      sent: ['a|TIKTOK', 'b|TIKTOK', 'a|INSTAGRAM_REELS'],
      failures: [{ clipId: 'b', platform: 'INSTAGRAM_REELS', message: 'boom' }],
      when: 'later',
    });
    expect(result.title).toBe('Quedaron programados en TikTok (2 de 2), Instagram (1 de 2); Instagram falló');
    expect(result.detail).toMatch(/^Instagram \(1 de 2 clips\): boom/);
  });
});

describe('TikTok · contenido de marca', () => {
  it('no puede ser «Solo yo»', () => {
    const input = { platform: 'TIKTOK' as const, account: account('TIKTOK'), clips: [clip('a')], textOf: () => text('Hola') };
    const blocked = checkNetwork({ ...input, tiktok: { ...EMPTY_TIKTOK, privacy: 'SELF_ONLY', disclose: true, brandedContent: true } });
    expect(blocked.ok).toBe(false);
    expect(blocked.failing?.action).toMatch(/no puede ser «Solo yo»/);
    expect(checkNetwork({ ...input, tiktok: { ...EMPTY_TIKTOK, privacy: 'PUBLIC_TO_EVERYONE', disclose: true, brandedContent: true } }).ok).toBe(true);
    // "Tu marca" alone can be private.
    expect(checkNetwork({ ...input, tiktok: { ...EMPTY_TIKTOK, privacy: 'SELF_ONLY', disclose: true, brandOrganic: true } }).ok).toBe(true);
  });
});

describe('nombres de redes', () => {
  it('une con «y», o con «e» antes de Instagram', () => {
    expect(joinNetworks(['TIKTOK'])).toBe('TikTok');
    expect(joinNetworks(['TIKTOK', 'INSTAGRAM_REELS'])).toBe('TikTok e Instagram');
    expect(joinNetworks(['TIKTOK', 'INSTAGRAM_REELS', 'YOUTUBE_SHORTS'])).toBe('TikTok, Instagram y YouTube');
  });
});
