/**
 * Formats of the product (map 1.3), Spanish (Argentina):
 * - thousands with a dot, decimals with a comma: 1.404,90
 * - tokens: up to 2 decimals, none when the value is whole: "120 tokens", "12,50 tokens"
 * - dates: "24 nov 2026, 14:30" in the browser's time zone (the backend stores UTC)
 * - durations: "1:05:32" or "0:41"
 * Months are spelled here (not by Intl) so the output does not depend on the ICU data of the runtime.
 */

const MONTHS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'] as const;
const WEEKDAYS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'] as const;
const MINUS = '−';

export type DateInput = string | number | Date;

/** Numbers arrive from DRF as decimal strings ("1404.9000000000"); accept both. */
export function toNumber(value: number | string | null | undefined): number {
  if (value === null || value === undefined || value === '') return 0;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function group(integer: string): string {
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
}

/** 1404.9 → "1.404,90"; 120 → "120". Whole values never show decimals. */
export function formatNumber(value: number | string, decimals = 2): string {
  const number = toNumber(value);
  const rounded = Math.round(Math.abs(number) * 10 ** decimals) / 10 ** decimals;
  const whole = Number.isInteger(rounded);
  const [integer = '0', fraction] = rounded.toFixed(whole ? 0 : decimals).split('.');
  const sign = number < 0 && rounded !== 0 ? MINUS : '';
  return `${sign}${group(integer)}${fraction ? `,${fraction}` : ''}`;
}

export interface TokenFormatOptions {
  /** Appends "token"/"tokens". Default true. */
  unit?: boolean;
  /** Always shows the sign: "+1.755" or "−763,20" (movements table). */
  signed?: boolean;
}

/** "120 tokens", "12,50 tokens", "1 token". */
export function formatTokens(value: number | string, { unit = true, signed = false }: TokenFormatOptions = {}): string {
  const number = toNumber(value);
  const text = formatNumber(number);
  const withSign = signed && number > 0 ? `+${text}` : text;
  if (!unit) return withSign;
  return `${withSign} ${Math.abs(number) === 1 ? 'token' : 'tokens'}`;
}

/** 3932 → "1:05:32"; 41 → "0:41". */
export function formatDuration(totalSeconds: number | string | null | undefined): string {
  const seconds = Math.max(0, Math.round(toNumber(totalSeconds)));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

/** Timecode of the editing surfaces (Geist Mono): 211 → "03:31"; 3700.5 → "1:01:40". With `tenths`: "03:31.5". */
export function formatTimecode(totalSeconds: number, { tenths = false }: { tenths?: boolean } = {}): string {
  const safe = Math.max(0, Number.isFinite(totalSeconds) ? totalSeconds : 0);
  const whole = Math.floor(tenths ? safe : Math.round(safe));
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  const base =
    h > 0
      ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
      : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return tenths ? `${base}.${Math.floor((safe - whole) * 10)}` : base;
}

/** 1800 → "30 min"; 11100 → "3 h 5 min"; 45 → "45 s". For plan limits and estimates. */
export function formatMinutes(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

function asDate(value: DateInput): Date {
  return value instanceof Date ? value : new Date(value);
}

/** "24 nov 2026" */
export function formatDate(value: DateInput): string {
  const date = asDate(value);
  if (Number.isNaN(date.getTime())) return '—';
  return `${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
}

/** "14:30" (24 h) */
export function formatTime(value: DateInput, { seconds = false }: { seconds?: boolean } = {}): string {
  const date = asDate(value);
  if (Number.isNaN(date.getTime())) return '—';
  const base = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  return seconds ? `${base}:${String(date.getSeconds()).padStart(2, '0')}` : base;
}

/** "24 nov 2026, 14:30" */
export function formatDateTime(value: DateInput): string {
  const date = asDate(value);
  if (Number.isNaN(date.getTime())) return '—';
  return `${formatDate(date)}, ${formatTime(date)}`;
}

/** "lunes 24 nov" (calendar and lists grouped by day). */
export function formatDayLabel(value: DateInput): string {
  const date = asDate(value);
  if (Number.isNaN(date.getTime())) return '—';
  return `${WEEKDAYS[date.getDay()]} ${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

/** "noviembre de 2026" style label for month pickers: "nov 2026". */
export function formatMonth(value: DateInput): string {
  const date = asDate(value);
  if (Number.isNaN(date.getTime())) return '—';
  return `${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
}

/**
 * "recién", "hace 12 min", "hace 3 h", "ayer", "hace 3 días"; older than a week falls back to the date.
 * Future dates: "en 12 min", "en 3 h", "mañana", "en 3 días".
 */
export function formatRelative(value: DateInput, now: DateInput = new Date()): string {
  const date = asDate(value);
  if (Number.isNaN(date.getTime())) return '—';
  const diffMs = asDate(now).getTime() - date.getTime();
  const future = diffMs < 0;
  const minutes = Math.floor(Math.abs(diffMs) / 60_000);
  const prefix = future ? 'en' : 'hace';
  if (minutes < 1) return future ? 'en un momento' : 'recién';
  if (minutes < 60) return `${prefix} ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${prefix} ${hours} h`;
  const days = Math.floor(hours / 24);
  if (days === 1) return future ? 'mañana' : 'ayer';
  if (days < 7) return `${prefix} ${days} días`;
  return formatDate(date);
}

/** "(GMT−3)" for the browser's zone at that date. */
export function formatTimeZone(value: DateInput = new Date()): string {
  const offset = -asDate(value).getTimezoneOffset();
  const sign = offset < 0 ? MINUS : '+';
  const h = Math.floor(Math.abs(offset) / 60);
  const m = Math.abs(offset) % 60;
  return `GMT${sign}${h}${m ? `:${String(m).padStart(2, '0')}` : ''}`;
}

/** 0.1 → "10 %" */
export function formatPercent(rate: number | string, decimals = 0): string {
  return `${formatNumber(toNumber(rate) * 100, decimals)} %`;
}

/** 49 → "USD 49"; 0.01 → "USD 0,01" */
export function formatUsd(value: number | string): string {
  return `USD ${formatNumber(value)}`;
}

/** 65250 → "ARS 65.250" */
export function formatMoney(value: number | string, currency: string): string {
  return `${currency} ${formatNumber(value)}`;
}

/** 1536 → "1,5 KB"; for upload sizes and speeds. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${formatNumber(value, 1)} ${units[unit]}`;
}

/** "LF" from "Lucía Fernández"; "EN" from "Estudio Norte". */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  const first = words[0]?.[0] ?? '';
  const second = words.length > 1 ? (words[words.length - 1]?.[0] ?? '') : (words[0]?.[1] ?? '');
  return `${first}${second}`.toUpperCase();
}

/** 1 → "1 clip", 3 → "3 clips". */
export function plural(count: number, singular: string, pluralForm: string): string {
  return `${formatNumber(count, 0)} ${count === 1 ? singular : pluralForm}`;
}

/**
 * "#fff" → "#FFFFFF". Colors come back from the backend as free text; a color
 * input needs #RRGGBB. Anything that is not a hex color gives the fallback.
 */
export function normalizeHex(value: string | null | undefined, fallback = '#FFFFFF'): string {
  const text = (value ?? '').trim();
  const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(text);
  if (short) return `#${short[1]}${short[1]}${short[2]}${short[2]}${short[3]}${short[3]}`.toUpperCase();
  return /^#[0-9a-f]{6}$/i.test(text) ? text.toUpperCase() : fallback;
}
