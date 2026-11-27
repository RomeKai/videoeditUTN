import type { Platform } from '@/api/contracts/common';
import type { PostStatus } from '@/api/contracts/projects';
import type { ScheduledPost, SocialAccount, TikTokPrivacy } from '@/api/contracts/social';
import { formatDate, formatTime, formatTimeZone } from '@/lib/format';

export const PLATFORMS: Platform[] = ['TIKTOK', 'INSTAGRAM_REELS', 'YOUTUBE_SHORTS'];
export const STATUSES: PostStatus[] = ['DRAFT', 'SCHEDULED', 'QUEUED', 'PROCESSING', 'PUBLISHED', 'FAILED'];

/** Short network noun for sentences: "la cuenta de TikTok". */
export const NETWORK_NOUN: Record<Platform, string> = { TIKTOK: 'TikTok', INSTAGRAM_REELS: 'Instagram', YOUTUBE_SHORTS: 'YouTube' };

export const PRIVACY_LABEL: Record<TikTokPrivacy, string> = {
  PUBLIC_TO_EVERYONE: 'Todos',
  MUTUAL_FOLLOW_FRIENDS: 'Amigos (seguidores que seguís)',
  FOLLOWER_OF_CREATOR: 'Seguidores',
  SELF_ONLY: 'Solo yo',
};

const MONTHS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'] as const;
const MONTHS_LONG = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'] as const;
const DOW = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'] as const;
/** Column order of the calendars: the week starts on Monday. */
export const WEEK_DOW = ['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom'] as const;

const pad = (value: number) => String(value).padStart(2, '0');

/* ── Dates (always the browser's time zone; the API stores UTC) ──────────── */

export function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

export function addMonths(date: Date, months: number): Date {
  return new Date(date.getFullYear(), date.getMonth() + months, 1);
}

/** Monday of the week of `date`, at 00:00. */
export function startOfWeek(date: Date): Date {
  const day = startOfDay(date);
  return addDays(day, -((day.getDay() + 6) % 7));
}

export function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

export function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "2026-11-24": value of <input type="date"> and key of a calendar day. */
export function dayKey(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** "18:00": value of <input type="time">. */
export function timeKey(date: Date): string {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function parseDayKey(value: string | null | undefined): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? '');
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Local date and time of the two inputs → Date. Null when either is missing or invalid. */
export function fromInputs(day: string, time: string): Date | null {
  const date = parseDayKey(day);
  const match = /^(\d{1,2}):(\d{2})$/.exec(time);
  if (!date || !match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  date.setHours(hours, minutes, 0, 0);
  return date;
}

/** "mié 25 nov" */
export function shortDay(value: Date | string): string {
  const date = new Date(value);
  return `${DOW[date.getDay()]} ${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

/** "Mié 25 nov": the same, to start a heading. */
export function shortDayTitle(value: Date | string): string {
  const text = shortDay(value);
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** "mié 25 nov, 18:00" */
export function shortDayTime(value: Date | string): string {
  return `${shortDay(value)}, ${formatTime(value)}`;
}

/** "23 – 29 nov 2026", "30 nov – 6 dic 2026", "28 dic 2026 – 3 ene 2027" */
export function weekRangeLabel(monday: Date): string {
  const sunday = addDays(monday, 6);
  if (monday.getFullYear() !== sunday.getFullYear()) return `${formatDate(monday)} – ${formatDate(sunday)}`;
  if (monday.getMonth() !== sunday.getMonth()) return `${monday.getDate()} ${MONTHS[monday.getMonth()]} – ${formatDate(sunday)}`;
  return `${monday.getDate()} – ${formatDate(sunday)}`;
}

/** "Noviembre 2026" */
export function monthLabel(date: Date): string {
  return `${MONTHS_LONG[date.getMonth()]} ${date.getFullYear()}`;
}

/** The 6 (or fewer) weeks that cover the month, Monday first. */
export function monthGrid(month: Date): Date[] {
  const first = startOfWeek(startOfMonth(month));
  const last = addDays(startOfMonth(addMonths(month, 1)), -1);
  const days: Date[] = [];
  for (let day = first; day <= last || days.length % 7 !== 0; day = addDays(day, 1)) days.push(day);
  return days;
}

/** "Buenos Aires (GMT−3)": the browser's zone, with its city when the browser names one. */
export function timeZoneLabel(at: Date = new Date()): string {
  const offset = formatTimeZone(at);
  let city = '';
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone ?? '';
    city = zone.includes('/') ? (zone.split('/').pop() ?? '').replace(/_/g, ' ') : '';
  } catch {
    city = '';
  }
  return city ? `${city} (${offset})` : offset;
}

/* ── Text ────────────────────────────────────────────────────────────────── */

/**
 * Characters that count against the network's limit: what the backend publishes
 * (`compose_caption`), the caption, a blank line and the hashtags as "#a #b".
 */
export function textLength(caption: string, hashtags: string[]): number {
  const tags = hashtags.filter(Boolean);
  if (tags.length === 0) return caption.length;
  return caption.length + 2 + tags.reduce((sum, tag) => sum + tag.length + 1, 0) + (tags.length - 1);
}

/** "#Mendoza, vlog  #mendoza" → ["Mendoza", "vlog"]: no "#", only letters, digits and "_", no repeats. */
export function parseHashtags(text: string): string[] {
  const seen = new Set<string>();
  const tags: string[] = [];
  for (const raw of text.split(/[\s,]+/)) {
    const tag = raw.replace(/^#+/, '').replace(/[^\p{L}\p{N}_]/gu, '');
    const key = tag.toLowerCase();
    if (!tag || seen.has(key)) continue;
    seen.add(key);
    tags.push(tag);
  }
  return tags;
}

export const formatHashtags = (hashtags: string[]): string => hashtags.map((tag) => `#${tag}`).join(' ');

/** Where the visible text of a post came from. */
export function textOrigin(post: Pick<ScheduledPost, 'caption' | 'generated_caption' | 'seo_status'>): 'ai' | 'edited' | 'manual' {
  if (!post.generated_caption) return 'manual';
  return post.caption === post.generated_caption ? 'ai' : 'edited';
}

/* ── Publications ────────────────────────────────────────────────────────── */

const sameTags = (a: string[], b: string[]): boolean => {
  const clean = (tags: string[]) => tags.map((tag) => tag.replace(/^#/, '').trim()).filter(Boolean);
  const [left, right] = [clean(a), clean(b)];
  return left.length === right.length && left.every((tag, index) => tag === right[index]);
};

/**
 * A helper draft exactly as the panel (and the AI) left it: still a draft, and nothing a person can
 * edit was edited. Text and hashtags are the suggestion or empty, and no TikTok options were chosen.
 * Anything else was written by someone and is theirs: it is never thrown away as a leftover.
 */
export function isUntouchedDraft(post: Pick<ScheduledPost, 'status' | 'caption' | 'generated_caption' | 'hashtags' | 'generated_hashtags' | 'tiktok'>): boolean {
  if (post.status !== 'DRAFT') return false;
  if (post.caption && post.caption !== post.generated_caption) return false;
  if (post.hashtags.length > 0 && !sameTags(post.hashtags, post.generated_hashtags ?? [])) return false;
  return post.tiktok === null;
}

/** Text and date can change only before the post is queued. */
export const isEditable = (status: PostStatus): boolean => status === 'DRAFT' || status === 'SCHEDULED';
/** A post that has not gone out yet can be cancelled. */
export const isCancellable = (status: PostStatus): boolean => status === 'DRAFT' || status === 'SCHEDULED' || status === 'QUEUED' || status === 'FAILED';

/**
 * Minutes after which a post still "Publicando" is considered stuck and can be retried: the backend's
 * POST_STUCK_AFTER (services/scheduled_posts.py), which answers 409 `post_in_flight` before.
 */
export const STUCK_AFTER_MS = 15 * 60_000;
export function isStuck(post: Pick<ScheduledPost, 'status' | 'updated_at'>, now: number = Date.now()): boolean {
  return post.status === 'PROCESSING' && now - new Date(post.updated_at).getTime() > STUCK_AFTER_MS;
}

export function accountOf(accounts: SocialAccount[] | undefined, platform: Platform): SocialAccount | undefined {
  return accounts?.find((account) => account.platform === platform);
}

/** A failed post whose way out is reconnecting the account, not retrying. */
export function needsReconnect(post: Pick<ScheduledPost, 'status' | 'platform' | 'error_code'>, accounts: SocialAccount[] | undefined): boolean {
  if (post.status !== 'FAILED') return false;
  const account = accountOf(accounts, post.platform);
  if (account) return account.status !== 'connected';
  return post.error_code === 'account_not_connected';
}

/** Readable reason of a failure; never the raw `error_log`. */
export function failureMessage(post: Pick<ScheduledPost, 'platform' | 'error_message' | 'error_code'>, accounts?: SocialAccount[]): string {
  const account = accountOf(accounts, post.platform);
  if (account && account.status !== 'connected') {
    return `La cuenta de ${NETWORK_NOUN[post.platform]} está ${account.status === 'expired' ? 'vencida' : 'desconectada'}. Reconectala y reintentá.`;
  }
  return post.error_message ?? 'No pudimos publicar el clip. Probá de nuevo.';
}

/** One card of the calendars: the same clip at the same minute, with one state per network. */
export interface PostGroup {
  key: string;
  at: Date;
  title: string;
  clipId: string;
  posts: ScheduledPost[];
}

export function groupPosts(posts: ScheduledPost[]): PostGroup[] {
  const groups = new Map<string, PostGroup>();
  for (const post of posts) {
    const at = new Date(post.publish_at);
    at.setSeconds(0, 0);
    const key = `${post.video_clip}|${at.getTime()}`;
    const group = groups.get(key);
    if (group) group.posts.push(post);
    else groups.set(key, { key, at, title: post.clip.title, clipId: post.video_clip, posts: [post] });
  }
  return [...groups.values()].sort((a, b) => a.at.getTime() - b.at.getTime() || a.title.localeCompare(b.title));
}

export type GroupKind = 'fail' | 'done' | 'sched' | 'send' | 'draft';

/** How a calendar card is drawn: an error wins, then what is going out, then what is scheduled. */
export function groupKind(group: PostGroup): GroupKind {
  const statuses = group.posts.map((post) => post.status);
  if (statuses.includes('FAILED')) return 'fail';
  if (statuses.some((status) => status === 'QUEUED' || status === 'PROCESSING')) return 'send';
  if (statuses.includes('SCHEDULED')) return 'sched';
  if (statuses.includes('DRAFT')) return 'draft';
  return 'done';
}

/** Only fully scheduled cards move: dragging changes every post of the card. */
export const isDraggable = (group: PostGroup): boolean => group.posts.every((post) => post.status === 'SCHEDULED');

/** Posts by local day, for the list. Drafts have no date yet: they go under `null`, last. */
export function groupByDay(posts: ScheduledPost[]): Array<{ day: Date | null; posts: ScheduledPost[] }> {
  const days = new Map<string, { day: Date | null; posts: ScheduledPost[] }>();
  for (const post of posts) {
    const day = post.status === 'DRAFT' ? null : startOfDay(new Date(post.publish_at));
    const key = day ? dayKey(day) : 'draft';
    const entry = days.get(key);
    if (entry) entry.posts.push(post);
    else days.set(key, { day, posts: [post] });
  }
  return [...days.values()].sort((a, b) => (a.day ? a.day.getTime() : Infinity) - (b.day ? b.day.getTime() : Infinity));
}
