/**
 * Starts a browser download from a URL (signed link of a clip, .zip, data URL).
 * Uses a temporary <a download> so it also works with the mock's data URLs,
 * which browsers refuse to open as a page.
 *
 * Browsers ignore `download` for another origin: whether the file is saved or
 * played depends on the Content-Disposition the storage answers with (the
 * signed URL has to ask for `attachment`; see CONTRACT.md). For those links it
 * opens a new tab, so a file served inline never replaces the app.
 */
export function downloadUrl(url: string, filename?: string): void {
  const anchor = document.createElement('a');
  anchor.href = url;
  if (filename) anchor.download = filename;
  anchor.rel = 'noopener noreferrer';
  if (/^https?:/i.test(url) && new URL(url, window.location.href).origin !== window.location.origin) anchor.target = '_blank';
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

/** Downloads text built in the browser: CSV of movements, transcript .txt / .srt. */
export function downloadText(text: string, filename: string, mime = 'text/plain;charset=utf-8'): void {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  downloadUrl(url, filename);
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function srtTime(seconds: number): string {
  const ms = Math.max(0, Math.round(seconds * 1000));
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const pad = (value: number, size = 2) => String(value).padStart(size, '0');
  return `${pad(h)}:${pad(m)}:${pad(s)},${pad(ms % 1000, 3)}`;
}

export interface TranscriptLine {
  start: number;
  end: number;
  text: string;
}

/**
 * The backend sends words, or whole segments when the transcription has no word
 * times. A segment is split into its words, with its time shared out by length,
 * so lines and subtitle cues keep a readable size either way.
 */
function splitIntoWords(items: ReadonlyArray<TranscriptLine>): TranscriptLine[] {
  const words: TranscriptLine[] = [];
  for (const item of items) {
    const parts = item.text.split(/\s+/).filter(Boolean);
    if (parts.length <= 1) {
      if (parts.length === 1) words.push({ start: item.start, end: item.end, text: parts[0] ?? '' });
      continue;
    }
    const total = parts.reduce((sum, part) => sum + part.length, 0);
    const span = Math.max(0, item.end - item.start);
    let cursor = item.start;
    for (const part of parts) {
      const length = total > 0 ? (span * part.length) / total : 0;
      words.push({ start: cursor, end: cursor + length, text: part });
      cursor += length;
    }
  }
  return words;
}

/**
 * Groups word-level transcript items into readable lines: a new line after a
 * sentence end, a pause longer than `maxGap` seconds or `maxWords` words.
 */
export function groupTranscript(items: ReadonlyArray<TranscriptLine>, { maxWords = 14, maxGap = 0.8 } = {}): TranscriptLine[] {
  const lines: TranscriptLine[] = [];
  let current: TranscriptLine | null = null;
  let words = 0;
  for (const item of splitIntoWords(items)) {
    if (current && (item.start - current.end > maxGap || words >= maxWords)) {
      lines.push(current);
      current = null;
    }
    if (!current) {
      current = { start: item.start, end: item.end, text: item.text };
      words = 1;
    } else {
      current.text += ` ${item.text}`;
      current.end = item.end;
      words += 1;
    }
    if (/[.!?…]$/.test(item.text)) {
      lines.push(current);
      current = null;
    }
  }
  if (current) lines.push(current);
  return lines;
}

/** Plain text of a transcript, one sentence per line. */
export function transcriptToTxt(items: ReadonlyArray<TranscriptLine>): string {
  return groupTranscript(items)
    .map((line) => line.text)
    .join('\n');
}

/** SubRip (.srt) of a transcript. */
export function transcriptToSrt(items: ReadonlyArray<TranscriptLine>): string {
  return groupTranscript(items, { maxWords: 8 })
    .map((line, index) => `${index + 1}\n${srtTime(line.start)} --> ${srtTime(line.end)}\n${line.text}\n`)
    .join('\n');
}
