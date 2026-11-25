import type { TranscriptItem } from '@/api/contracts/projects';

/** A word of the transcript (or a whole segment when the backend has no word times). */
export type Word = Pick<TranscriptItem, 'start' | 'end' | 'text'>;

/** A paragraph of the transcript: words `from`..`to`, both included. */
export interface Row {
  index: number;
  from: number;
  to: number;
  start: number;
  end: number;
}

const SENTENCE_END = /[.!?…]["»”']?$/;

/**
 * Groups the words into paragraphs like the artboards show them: a new one
 * after a long pause, or after a sentence once the paragraph is long enough.
 */
export function buildRows(words: ReadonlyArray<Word>, { softWords = 24, hardWords = 60, maxGap = 1 } = {}): Row[] {
  const rows: Row[] = [];
  let from = 0;
  const close = (to: number) => {
    const first = words[from];
    const last = words[to];
    if (first && last) rows.push({ index: rows.length, from, to, start: first.start, end: last.end });
    from = to + 1;
  };
  for (let index = 0; index < words.length; index += 1) {
    const word = words[index] as Word;
    const next = words[index + 1];
    const count = index - from + 1;
    const sentenceEnd = SENTENCE_END.test(word.text);
    if (!next) close(index);
    else if (count >= hardWords) close(index);
    else if (sentenceEnd && (count >= softWords || next.start - word.end > maxGap)) close(index);
  }
  return rows;
}

/** Index of the last word that starts at or before `time`; -1 before the first. */
export function lastWordStartingBefore(words: ReadonlyArray<Word>, time: number): number {
  let low = 0;
  let high = words.length - 1;
  let found = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if ((words[middle] as Word).start <= time) {
      found = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return found;
}

/** The word being said at `time`, or -1 in a silence. */
export function wordIndexAt(words: ReadonlyArray<Word>, time: number): number {
  const index = lastWordStartingBefore(words, time);
  if (index < 0) return -1;
  return time <= (words[index] as Word).end + 0.3 ? index : -1;
}

/** Clip borders snap to whole words: the start goes to the nearest word start. */
export function snapStart(words: ReadonlyArray<Word>, time: number): number {
  if (words.length === 0) return time;
  const before = lastWordStartingBefore(words, time);
  const a = words[Math.max(0, before)] as Word;
  const b = words[Math.min(words.length - 1, before + 1)] as Word;
  return Math.abs(a.start - time) <= Math.abs(b.start - time) ? a.start : b.start;
}

/** …and the end goes to the nearest word end. */
export function snapEnd(words: ReadonlyArray<Word>, time: number): number {
  if (words.length === 0) return time;
  const before = lastWordStartingBefore(words, time);
  // The word being said may end after `time` while the previous one ended closer to it.
  let best = (words[Math.max(0, before)] as Word).end;
  for (const index of [before - 1, before + 1]) {
    const word = words[index];
    if (word && Math.abs(word.end - time) < Math.abs(best - time)) best = word.end;
  }
  return best;
}

/** The word boundary before or after the current edge (keyboard on a trim handle). */
export function stepEdge(words: ReadonlyArray<Word>, edge: 'start' | 'end', value: number, direction: -1 | 1): number {
  if (words.length === 0) return value + direction * 0.5;
  const times = edge === 'start' ? words.map((word) => word.start) : words.map((word) => word.end);
  if (direction > 0) return times.find((time) => time > value + 0.01) ?? value;
  for (let index = times.length - 1; index >= 0; index -= 1) {
    if ((times[index] as number) < value - 0.01) return times[index] as number;
  }
  return value;
}

/** Word indexes inside a time range (both included), or null when no word falls in it. */
export function wordsInRange(words: ReadonlyArray<Word>, start: number, end: number): { from: number; to: number } | null {
  let from = lastWordStartingBefore(words, start - 0.01) + 1;
  // A word that started just before but is mostly inside still belongs to the range.
  while (from > 0 && (words[from - 1] as Word).start >= start - 0.01) from -= 1;
  const to = lastWordStartingBefore(words, end - 0.01);
  return to >= from && from < words.length ? { from, to } : null;
}

export function textOf(words: ReadonlyArray<Word>, from: number, to: number): string {
  return words
    .slice(from, to + 1)
    .map((word) => word.text)
    .join(' ');
}

/** Burnt-in subtitle sample: the group of `perSegment` words that holds the current one. */
export function captionAt(
  words: ReadonlyArray<Word>,
  time: number,
  clipStart: number,
  perSegment: number,
): { words: string[]; active: number } | null {
  const current = wordIndexAt(words, time);
  if (current < 0) return null;
  const first = Math.max(0, lastWordStartingBefore(words, clipStart - 0.01) + 1);
  const size = Math.max(1, perSegment);
  const offset = Math.max(0, current - first);
  const from = first + Math.floor(offset / size) * size;
  const group = words.slice(from, from + size).map((word) => word.text);
  return { words: group, active: current - from };
}

/** A default title for a clip made from a selection: its first words. */
export function titleFromText(text: string, max = 48): string {
  const clean = text.replace(/\s+/g, ' ').trim();
  if (clean.length <= max) return clean.replace(/[.,;:]+$/, '');
  const cut = clean.slice(0, max);
  const lastSpace = cut.lastIndexOf(' ');
  return `${(lastSpace > 12 ? cut.slice(0, lastSpace) : cut).replace(/[.,;:]+$/, '')}…`;
}

/** Lowercase without accents, for the search box. */
export function normalize(text: string): string {
  return text
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
}
