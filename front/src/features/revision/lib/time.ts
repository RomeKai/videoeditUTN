import { formatTimecode } from '@/lib/format';

/** Shortest clip the editing surfaces let you make, in seconds. */
export const MIN_CLIP_SECONDS = 1;

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

/**
 * Seconds the backend charges for: `ceil(round(seconds, 3))`. Rounding first keeps
 * float noise (60.00000000001) from costing one more second.
 */
export function billedSeconds(seconds: number): number {
  return Math.ceil(Math.round(seconds * 1000) / 1000);
}

export function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

/** Edge of a clip as the artboards write it: tenths after a comma ("04:12,0", "1:05:20,5"). */
export function formatEdge(seconds: number): string {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  const tenths = Math.round(safe * 10);
  return `${formatTimecode(Math.floor(tenths / 10))},${tenths % 10}`;
}

/** Reads what a person types in an edge field: "04:12,5", "4:12.5", "1:05:20" or plain seconds. Null when it is not a time. */
export function parseEdge(text: string): number | null {
  const clean = text.trim().replace(',', '.');
  if (!/^\d+(:\d{1,2}){0,2}(\.\d+)?$/.test(clean)) return null;
  const parts = clean.split(':').map(Number);
  if (parts.some((part) => !Number.isFinite(part))) return null;
  if (parts.slice(1).some((part) => part >= 60)) return null;
  return parts.reduce((total, part) => total * 60 + part, 0);
}

const NICE_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

/** Evenly spaced ruler ticks for a window of the video, at most `max` of them. */
export function rulerTicks(from: number, to: number, max = 9): number[] {
  const span = to - from;
  if (span <= 0) return [from];
  const step = NICE_STEPS.find((candidate) => span / candidate <= max) ?? 3600;
  const ticks: number[] = [];
  for (let tick = Math.ceil(from / step) * step; tick <= to + 1e-6; tick += step) ticks.push(tick);
  return ticks;
}
