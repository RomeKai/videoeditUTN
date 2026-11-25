import type { Platform } from '@/api/contracts/common';
import type { Clip } from '@/api/contracts/projects';
import type { CreatePostInput, ScheduledPost, SocialAccount, TikTokOptions, TikTokPrivacy, UpdatePostRequest } from '@/api/contracts/social';
import { formatDuration, formatNumber, formatTime } from '@/lib/format';
import { NETWORK_NOUN, addDays, shortDay, textLength } from './posts';

/** Clip id + network: one publication. */
export type PairKey = `${string}|${Platform}`;
export const pairKey = (clipId: string, platform: Platform): PairKey => `${clipId}|${platform}`;

/** What the panel needs to know about each clip. */
export interface PanelClip {
  id: string;
  title: string;
  duration: number;
  /** "9:16": comes from the project. */
  aspectRatio: string;
  projectId: string;
  projectTitle: string;
  resolution?: string;
  /** Rendered and ready to go out. */
  ready: boolean;
  /** The project paid for the AI text. */
  seo: boolean;
}

export const clipDuration = (clip: Pick<Clip, 'start_time' | 'end_time'>): number => Math.max(0, clip.end_time - clip.start_time);

export interface TextValue {
  caption: string;
  hashtags: string[];
}

export interface TikTokForm {
  /** Empty until the user chooses: TikTok does not allow a default. */
  privacy: TikTokPrivacy | '';
  comment: boolean;
  duet: boolean;
  stitch: boolean;
  /** "Divulgar contenido comercial". */
  disclose: boolean;
  brandOrganic: boolean;
  brandedContent: boolean;
}

export const EMPTY_TIKTOK: TikTokForm = { privacy: '', comment: false, duet: false, stitch: false, disclose: false, brandOrganic: false, brandedContent: false };

export function tiktokOptions(form: TikTokForm, account: SocialAccount | undefined): TikTokOptions | undefined {
  if (!form.privacy) return undefined;
  const limits = account?.tiktok;
  return {
    privacy_level: form.privacy,
    allow_comment: form.comment && !limits?.comment_disabled,
    allow_duet: form.duet && !limits?.duet_disabled,
    allow_stitch: form.stitch && !limits?.stitch_disabled,
    brand_organic: form.disclose && form.brandOrganic,
    branded_content: form.disclose && form.brandedContent,
  };
}

/* ── When ────────────────────────────────────────────────────────────────── */

export type WhenMode = 'now' | 'later';
export type SpreadMode = 'together' | 'daily';

/** Date of each clip, in order. Null: now. "Uno por día" puts each clip a day after the previous one, at the same time. */
export function scheduleFor(order: string[], when: WhenMode, start: Date | null, spread: SpreadMode): Map<string, Date | null> {
  const dates = new Map<string, Date | null>();
  order.forEach((clipId, index) => {
    if (when === 'now' || !start) dates.set(clipId, null);
    else dates.set(clipId, spread === 'daily' ? addDays(start, index) : new Date(start));
  });
  return dates;
}

/* ── Requirements of each network ────────────────────────────────────────── */

export type RequirementKey = 'format' | 'duration' | 'text' | 'hashtags' | 'privacy' | 'disclosure';

export interface Requirement {
  key: RequirementKey;
  ok: boolean;
  /** Short fact for "Ya cumple: …". */
  met: string;
  /** What is missing, to complete "N de M requisitos · …". */
  missing: string;
  /** For the footer: "Elegí la privacidad de TikTok". */
  action: string;
  /** A broken limit (red) rather than something still to choose (amber). */
  severe: boolean;
}

export interface NetworkCheck {
  platform: Platform;
  requirements: Requirement[];
  passed: number;
  total: number;
  ok: boolean;
  /** First requirement that fails. */
  failing?: Requirement;
  /** Characters over the limit, in the worst clip. */
  overBy: number;
}

export interface CheckInput {
  platform: Platform;
  account: SocialAccount | undefined;
  clips: PanelClip[];
  /** Text of each clip for this network. */
  textOf: (clipId: string) => TextValue;
  tiktok: TikTokForm;
}

/** The short list of what the clip must meet for one network (map E1.5). TikTok's privacy counts as a requirement. */
export function checkNetwork({ platform, account, clips, textOf, tiktok }: CheckInput): NetworkCheck {
  const noun = NETWORK_NOUN[platform];
  const many = clips.length > 1;
  const limits = account?.limits ?? { caption_max_chars: 2200, hashtags_max: 30, max_duration_seconds: 600, aspect_ratios: ['9:16'] };
  const requirements: Requirement[] = [];

  const offFormat = clips.find((clip) => !limits.aspect_ratios.includes(clip.aspectRatio));
  requirements.push({
    key: 'format',
    ok: !offFormat,
    met: `formato ${clips[0]?.aspectRatio ?? '9:16'}`,
    missing: offFormat ? `${many ? `«${offFormat.title}» está` : 'el clip está'} en ${offFormat.aspectRatio} y ${noun} pide ${limits.aspect_ratios.join(' o ')}` : '',
    action: `${noun} pide el formato ${limits.aspect_ratios.join(' o ')}`,
    severe: true,
  });

  const durations = clips.map((clip) => clip.duration);
  const longest = clips.reduce<PanelClip | undefined>((worst, clip) => (!worst || clip.duration > worst.duration ? clip : worst), undefined);
  const tooLong = longest && longest.duration > limits.max_duration_seconds ? longest : undefined;
  const max = formatDuration(limits.max_duration_seconds);
  requirements.push({
    key: 'duration',
    ok: !tooLong,
    met: many ? `de ${formatDuration(Math.min(...durations))} a ${formatDuration(Math.max(...durations))}, máximo ${max}` : `${formatDuration(durations[0] ?? 0)} de ${max}`,
    missing: tooLong ? `${many ? `«${tooLong.title}» dura` : 'el clip dura'} ${formatDuration(tooLong.duration)} y ${noun} acepta hasta ${max}` : '',
    action: `${many && tooLong ? `«${tooLong.title}» dura` : 'El clip dura'} más de lo que acepta ${noun} (${max})`,
    severe: true,
  });

  const lengths = clips.map((clip) => {
    const text = textOf(clip.id);
    return { clip, length: textLength(text.caption, text.hashtags), tags: text.hashtags.length };
  });
  const longestText = lengths.reduce<(typeof lengths)[number] | undefined>((worst, item) => (!worst || item.length > worst.length ? item : worst), undefined);
  const overBy = Math.max(0, (longestText?.length ?? 0) - limits.caption_max_chars);
  requirements.push({
    key: 'text',
    ok: overBy === 0,
    met: many ? `textos dentro de ${formatNumber(limits.caption_max_chars)} caracteres` : `${formatNumber(longestText?.length ?? 0)} de ${formatNumber(limits.caption_max_chars)} caracteres`,
    missing: many && longestText ? `el texto de «${longestText.clip.title}» se pasa del límite` : 'el texto se pasa del límite',
    action: `Acortá el texto de ${noun}: ${overBy === 1 ? 'sobra' : 'sobran'} ${formatNumber(overBy)}`,
    severe: true,
  });

  const mostTags = lengths.reduce((most, item) => Math.max(most, item.tags), 0);
  // TikTok's fourth requirement is the privacy: its hashtag limit only shows up when it is broken.
  if (platform !== 'TIKTOK' || mostTags > limits.hashtags_max) {
    requirements.push({
      key: 'hashtags',
      ok: mostTags <= limits.hashtags_max,
      met: `${formatNumber(mostTags)} de ${formatNumber(limits.hashtags_max)} hashtags`,
      missing: `hay más de ${formatNumber(limits.hashtags_max)} hashtags`,
      action: `Quitá hashtags de ${noun}: acepta hasta ${formatNumber(limits.hashtags_max)}`,
      severe: true,
    });
  }

  if (platform === 'TIKTOK') {
    requirements.push({
      key: 'privacy',
      ok: Boolean(tiktok.privacy),
      met: 'privacidad elegida',
      missing: `falta elegir quién puede ver ${many ? 'los videos' : 'el video'}`,
      action: 'Elegí la privacidad de TikTok',
      severe: false,
    });
    if (tiktok.disclose) {
      requirements.push({
        key: 'disclosure',
        ok: tiktok.brandOrganic || tiktok.brandedContent,
        met: 'contenido comercial declarado',
        missing: 'falta elegir «Tu marca» o «Contenido de marca»',
        action: 'Elegí «Tu marca» o «Contenido de marca» en TikTok',
        severe: false,
      });
    }
  }

  const passed = requirements.filter((item) => item.ok).length;
  return { platform, requirements, passed, total: requirements.length, ok: passed === requirements.length, failing: requirements.find((item) => !item.ok), overBy };
}

/* ── What gets sent ──────────────────────────────────────────────────────── */

export interface Submission {
  /** Publications with no draft: created (in bulk when there are several). */
  creates: CreatePostInput[];
  /** Drafts that already exist: published or scheduled with PATCH. */
  updates: Array<UpdatePostRequest & { id: string }>;
}

export interface SubmissionInput {
  order: string[];
  platforms: Platform[];
  dates: Map<string, Date | null>;
  textOf: (clipId: string, platform: Platform) => TextValue;
  /** True while the AI text of the pair has not arrived and the user wrote nothing: the text is left to the backend. */
  waitingText: (clipId: string, platform: Platform) => boolean;
  draftOf: (clipId: string, platform: Platform) => ScheduledPost | undefined;
  tiktok: TikTokOptions | undefined;
}

export function buildSubmission({ order, platforms, dates, textOf, waitingText, draftOf, tiktok }: SubmissionInput): Submission {
  const creates: CreatePostInput[] = [];
  const updates: Submission['updates'] = [];
  for (const clipId of order) {
    const date = dates.get(clipId) ?? null;
    const publishAt = date ? date.toISOString() : null;
    for (const platform of platforms) {
      const text = waitingText(clipId, platform) ? {} : { caption: textOf(clipId, platform).caption, hashtags: textOf(clipId, platform).hashtags };
      const extra = platform === 'TIKTOK' && tiktok ? { tiktok } : {};
      const draft = draftOf(clipId, platform);
      if (draft) updates.push({ id: draft.id, publish_at: publishAt, ...text, ...extra });
      else creates.push({ video_clip: clipId, platform, publish_at: publishAt, ...text, ...extra });
    }
  }
  return { creates, updates };
}

/* ── Calendar clashes ────────────────────────────────────────────────────── */

export interface Clash {
  at: Date;
  platform: Platform;
  /** Title of the publication that is already scheduled at that time. */
  title: string;
}

/** Scheduled publications of the workspace that go out in the same network at the same minute as one of the new ones. */
export function findClashes(dates: Map<string, Date | null>, platforms: Platform[], scheduled: ScheduledPost[], clipIds: string[]): Clash[] {
  const clashes: Clash[] = [];
  for (const [, date] of dates) {
    if (!date) continue;
    const minute = Math.floor(date.getTime() / 60_000);
    for (const post of scheduled) {
      if (post.status !== 'SCHEDULED' || !platforms.includes(post.platform) || clipIds.includes(post.video_clip)) continue;
      if (Math.floor(new Date(post.publish_at).getTime() / 60_000) !== minute) continue;
      if (clashes.some((clash) => clash.platform === post.platform && clash.at.getTime() === date.getTime())) continue;
      clashes.push({ at: date, platform: post.platform, title: post.clip.title });
    }
  }
  return clashes;
}

/** "25 – 27 nov 2026, 18:00" for the footer of several clips; one date when they all go together. */
export function scheduleSummary(dates: Array<Date | null>): string {
  const real = dates.filter((date): date is Date => Boolean(date)).sort((a, b) => a.getTime() - b.getTime());
  const first = real[0];
  const last = real[real.length - 1];
  if (!first || !last) return 'ahora';
  const time = formatTime(first);
  const day = (date: Date) => shortDay(date).slice(4);
  if (first.getTime() === last.getTime() || real.length === 1) return `${day(first)} ${first.getFullYear()}, ${time}`;
  if (first.getMonth() === last.getMonth()) return `${first.getDate()} – ${day(last)} ${last.getFullYear()}, ${time}`;
  return `${day(first)} – ${day(last)} ${last.getFullYear()}, ${time}`;
}

/** "TikTok e Instagram", "TikTok, Instagram y YouTube" */
export function joinNetworks(platforms: Platform[]): string {
  const names = platforms.map((platform) => NETWORK_NOUN[platform]);
  if (names.length <= 1) return names[0] ?? '';
  const last = names[names.length - 1] as string;
  const joiner = /^(i|hi)/i.test(last) ? 'e' : 'y';
  return `${names.slice(0, -1).join(', ')} ${joiner} ${last}`;
}
