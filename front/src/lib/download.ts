/**
 * Starts a browser download from a URL (signed link of a clip, .zip, data URL).
 * Uses a temporary <a download> so it also works with the mock's data URLs,
 * which browsers refuse to open as a page.
 */
export function downloadUrl(url: string, filename?: string): void {
  const anchor = document.createElement('a');
  anchor.href = url;
  if (filename) anchor.download = filename;
  anchor.rel = 'noopener';
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
 * Groups word-level transcript items into readable lines: a new line after a
 * sentence end, a pause longer than `maxGap` seconds or `maxWords` words.
 */
export function groupTranscript(items: ReadonlyArray<TranscriptLine>, { maxWords = 14, maxGap = 0.8 } = {}): TranscriptLine[] {
  const lines: TranscriptLine[] = [];
  let current: TranscriptLine | null = null;
  let words = 0;
  for (const item of items) {
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
