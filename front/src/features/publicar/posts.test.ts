import { describe, expect, it } from 'vitest';
import { goingOut } from '@/api/hooks';
import type { ScheduledPost, SocialAccount } from '@/api/contracts/social';
import {
  dayKey,
  failureMessage,
  fromInputs,
  groupByDay,
  groupKind,
  groupPosts,
  isDraggable,
  isStuck,
  monthGrid,
  monthLabel,
  needsReconnect,
  parseDayKey,
  parseHashtags,
  shortDay,
  startOfWeek,
  textLength,
  textOrigin,
  weekRangeLabel,
} from './posts';

const at = (year: number, month: number, day: number, hour = 0, minute = 0) => new Date(year, month - 1, day, hour, minute);

function post(overrides: Partial<ScheduledPost> & { id: string }): ScheduledPost {
  return {
    video_clip: 'clip-1',
    platform: 'TIKTOK',
    publish_at: at(2026, 11, 26, 18).toISOString(),
    status: 'SCHEDULED',
    error_log: null,
    retry_count: 0,
    generated_caption: null,
    generated_hashtags: null,
    created_at: at(2026, 11, 20).toISOString(),
    updated_at: at(2026, 11, 20).toISOString(),
    caption: '',
    hashtags: [],
    seo_status: 'none',
    clip: { id: 'clip-1', title: 'Atardecer en Potrerillos', project_id: 'p', project_title: 'Vlog Mendoza', duration_seconds: 31, thumbnail_url: null },
    post_url: null,
    error_message: null,
    error_code: null,
    tiktok: null,
    ...overrides,
  };
}

const account = (platform: SocialAccount['platform'], status: SocialAccount['status']): SocialAccount => ({
  platform,
  status,
  username: status === 'disconnected' ? null : '@estudionorte',
  connected_at: null,
  limits: { caption_max_chars: 2200, hashtags_max: 30, max_duration_seconds: 600, aspect_ratios: ['9:16'] },
  tiktok: null,
});

describe('fechas del calendario', () => {
  it('la semana empieza el lunes', () => {
    expect(dayKey(startOfWeek(at(2026, 11, 24)))).toBe('2026-11-23');
    expect(dayKey(startOfWeek(at(2026, 11, 29, 23, 59)))).toBe('2026-11-23');
    expect(dayKey(startOfWeek(at(2026, 11, 23)))).toBe('2026-11-23');
  });

  it('el título de la semana abrevia lo que se repite', () => {
    expect(weekRangeLabel(at(2026, 11, 23))).toBe('23 – 29 nov 2026');
    expect(weekRangeLabel(at(2026, 11, 30))).toBe('30 nov – 6 dic 2026');
    expect(weekRangeLabel(at(2026, 12, 28))).toBe('28 dic 2026 – 3 ene 2027');
  });

  it('el mes se dibuja en semanas completas, de lunes a domingo', () => {
    const days = monthGrid(at(2026, 11, 15));
    expect(days.length % 7).toBe(0);
    expect(dayKey(days[0] as Date)).toBe('2026-10-26');
    expect(dayKey(days[days.length - 1] as Date)).toBe('2026-12-06');
    expect(monthLabel(at(2026, 11, 15))).toBe('Noviembre 2026');
  });

  it('fecha y hora de los campos son hora local; lo inválido no devuelve fecha', () => {
    expect(fromInputs('2026-11-26', '18:30')).toEqual(at(2026, 11, 26, 18, 30));
    expect(fromInputs('', '18:30')).toBeNull();
    expect(fromInputs('2026-11-26', '25:00')).toBeNull();
    expect(parseDayKey('26/11/2026')).toBeNull();
    expect(shortDay(at(2026, 11, 25))).toBe('mié 25 nov');
  });
});

describe('texto', () => {
  it('cuenta lo que publica el backend: el caption, una línea en blanco y los hashtags con su #', () => {
    expect(textLength('hola', [])).toBe(4);
    // compose_caption(): "hola\n\n#uno #dos"
    expect(textLength('hola', ['uno', 'dos'])).toBe('hola\n\n#uno #dos'.length);
    expect(textLength('', ['uno'])).toBe('\n\n#uno'.length);
    expect(textLength('hola', ['', 'uno'])).toBe('hola\n\n#uno'.length);
  });

  it('los hashtags se limpian: sin #, sin símbolos y sin repetidos', () => {
    expect(parseHashtags('#Mendoza, vlog  #mendoza #viajes-argentina ##mate')).toEqual(['Mendoza', 'vlog', 'viajesargentina', 'mate']);
    expect(parseHashtags('   ')).toEqual([]);
  });

  it('distingue el texto de la IA del editado a mano', () => {
    expect(textOrigin({ caption: 'a', generated_caption: 'a', seo_status: 'ready' })).toBe('ai');
    expect(textOrigin({ caption: 'b', generated_caption: 'a', seo_status: 'ready' })).toBe('edited');
    expect(textOrigin({ caption: 'b', generated_caption: null, seo_status: 'none' })).toBe('manual');
  });
});

describe('fichas del calendario', () => {
  const same = at(2026, 11, 24, 14, 30).toISOString();
  const posts = [
    post({ id: '1', platform: 'TIKTOK', status: 'PROCESSING', publish_at: same }),
    post({ id: '2', platform: 'INSTAGRAM_REELS', status: 'QUEUED', publish_at: same }),
    post({ id: '3', status: 'SCHEDULED', publish_at: at(2026, 11, 26, 18).toISOString() }),
    post({ id: '4', video_clip: 'clip-2', status: 'FAILED', publish_at: at(2026, 11, 24, 9).toISOString() }),
    post({ id: '5', video_clip: 'clip-3', status: 'PUBLISHED', publish_at: at(2026, 11, 23, 10).toISOString() }),
  ];

  it('el mismo clip a la misma hora es una sola ficha, con un estado por red', () => {
    const groups = groupPosts(posts);
    expect(groups).toHaveLength(4);
    const sending = groups.find((group) => group.posts.length === 2);
    expect(sending?.posts.map((item) => item.platform)).toEqual(['TIKTOK', 'INSTAGRAM_REELS']);
    expect(groups.map((group) => group.at.getTime())).toEqual([...groups.map((group) => group.at.getTime())].sort((a, b) => a - b));
  });

  it('la ficha se pinta por su estado más urgente y solo se arrastra lo programado', () => {
    const kinds = Object.fromEntries(groupPosts(posts).map((group) => [group.posts[0]?.id, groupKind(group)]));
    expect(kinds).toEqual({ '1': 'send', '3': 'sched', '4': 'fail', '5': 'done' });
    const draggable = groupPosts(posts).filter(isDraggable);
    expect(draggable.map((group) => group.posts[0]?.id)).toEqual(['3']);
  });

  it('la lista agrupa por día y deja los borradores al final, sin fecha', () => {
    const days = groupByDay([...posts, post({ id: '6', status: 'DRAFT', publish_at: at(2026, 11, 20).toISOString() })]);
    expect(days.map((day) => (day.day ? dayKey(day.day) : 'sin fecha'))).toEqual(['2026-11-23', '2026-11-24', '2026-11-26', 'sin fecha']);
    expect(days[1]?.posts).toHaveLength(3);
  });
});

describe('publicaciones con error', () => {
  it('si la cuenta no está conectada, la salida es reconectar', () => {
    const failed = post({ id: '1', platform: 'YOUTUBE_SHORTS', status: 'FAILED', error_message: 'La red rechazó el video.', error_code: 'media_rejected' });
    expect(needsReconnect(failed, [account('YOUTUBE_SHORTS', 'expired')])).toBe(true);
    expect(failureMessage(failed, [account('YOUTUBE_SHORTS', 'expired')])).toBe('La cuenta de YouTube está vencida. Reconectala y reintentá.');
    expect(needsReconnect(failed, [account('YOUTUBE_SHORTS', 'connected')])).toBe(false);
    expect(failureMessage(failed, [account('YOUTUBE_SHORTS', 'connected')])).toBe('La red rechazó el video.');
    expect(needsReconnect(post({ id: '2', status: 'SCHEDULED' }), [account('TIKTOK', 'expired')])).toBe(false);
  });

  it('nunca muestra el texto de la excepción', () => {
    const failed = post({ id: '1', status: 'FAILED', error_log: 'AyrshareError: code 137', error_message: null });
    expect(failureMessage(failed)).not.toContain('Ayrshare');
  });

  it('una publicación que lleva mucho «Publicando» se puede reintentar', () => {
    const now = at(2026, 11, 24, 15).getTime();
    expect(isStuck(post({ id: '1', status: 'PROCESSING', updated_at: at(2026, 11, 24, 14).toISOString() }), now)).toBe(true);
    expect(isStuck(post({ id: '1', status: 'PROCESSING', updated_at: at(2026, 11, 24, 14, 58).toISOString() }), now)).toBe(false);
    expect(isStuck(post({ id: '1', status: 'QUEUED', updated_at: at(2026, 11, 24, 10).toISOString() }), now)).toBe(false);
    // The backend's POST_STUCK_AFTER: before 15 minutes a retry is a 409, so the button does not show yet.
    expect(isStuck(post({ id: '1', status: 'PROCESSING', updated_at: at(2026, 11, 24, 14, 48).toISOString() }), now)).toBe(false);
    expect(isStuck(post({ id: '1', status: 'PROCESSING', updated_at: at(2026, 11, 24, 14, 44).toISOString() }), now)).toBe(true);
  });

  it('«publicar ahora» queda programada para ya: se sigue consultando hasta que el beat la toma', () => {
    const now = at(2026, 11, 24, 15).getTime();
    expect(goingOut(post({ id: '1', status: 'SCHEDULED', publish_at: at(2026, 11, 24, 14, 59).toISOString() }), now)).toBe(true);
    expect(goingOut(post({ id: '1', status: 'SCHEDULED', publish_at: at(2026, 11, 25, 9).toISOString() }), now)).toBe(false);
    expect(goingOut(post({ id: '1', status: 'QUEUED' }), now)).toBe(true);
    expect(goingOut(post({ id: '1', status: 'DRAFT', publish_at: at(2026, 11, 24, 14).toISOString() }), now)).toBe(false);
  });
});
