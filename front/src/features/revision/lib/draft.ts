import type { Clip } from '@/api/contracts/projects';
import { scoreBand } from '@/ui/Score';
import { clamp, MIN_CLIP_SECONDS } from './time';
import { textOf, wordsInRange, type Word } from './transcript';

/** A clip of the review as the person left it. Nothing is sent until "Renderizar". */
export interface DraftClip {
  /** Stable key in the screen: the clip id, or a local one for clips made from the transcript. */
  key: string;
  /** VideoClip id of an AI suggestion; null for clips made by the person. */
  clipId: string | null;
  /** Number shown in the list and on the timeline. */
  n: number;
  title: string;
  start: number;
  end: number;
  /** Null for clips made by the person: they have no score. */
  score: number | null;
  reasoning: string;
  included: boolean;
  /** What the suggestion was, to know what changed. */
  original: { title: string; start: number; end: number; included: boolean } | null;
}

export interface Draft {
  clips: DraftClip[];
  selectedKey: string | null;
}

export type DraftAction =
  | { type: 'reset'; draft: Draft }
  | { type: 'sync'; suggestions: ReadonlyArray<Clip> }
  | { type: 'select'; key: string | null }
  | { type: 'toggle'; key: string; included?: boolean }
  | { type: 'rename'; key: string; title: string }
  | { type: 'edge'; key: string; edge: 'start' | 'end'; value: number; total: number }
  | { type: 'add'; key: string; start: number; end: number; title: string; total: number }
  | { type: 'remove'; key: string };

/**
 * Starting point of the review: the suggestions sorted by score, with the ones
 * in the "Flojo" band left out (the artboard's starting state). If every
 * suggestion is weak they are all included, so there is something to render.
 */
export function initialDraft(suggestions: ReadonlyArray<Clip>): Draft {
  const sorted = [...suggestions].sort((a, b) => b.virality_score - a.virality_score || a.start_time - b.start_time);
  const anyStrong = sorted.some((clip) => scoreBand(clip.virality_score) !== 'Flojo');
  const clips = sorted.map((clip, index): DraftClip => {
    const included = anyStrong ? scoreBand(clip.virality_score) !== 'Flojo' : true;
    return {
      key: clip.id,
      clipId: clip.id,
      n: index + 1,
      title: clip.title,
      start: clip.start_time,
      end: clip.end_time,
      score: clip.virality_score,
      reasoning: clip.ai_reasoning,
      included,
      original: { title: clip.title, start: clip.start_time, end: clip.end_time, included },
    };
  });
  return { clips, selectedKey: clips[0]?.key ?? null };
}

let localCounter = 0;

/** A key for a clip the person made: unique in the session, also after removing and adding at the same spot. */
export function newLocalKey(): string {
  localCounter += 1;
  const random = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : Math.random().toString(36).slice(2);
  return `local-${localCounter}-${random}`;
}

export function isClipChanged(clip: DraftClip): boolean {
  if (!clip.original) return true;
  return (
    clip.title !== clip.original.title ||
    clip.start !== clip.original.start ||
    clip.end !== clip.original.end ||
    clip.included !== clip.original.included
  );
}

export function isDirty(draft: Draft): boolean {
  return draft.clips.some(isClipChanged);
}

export function draftReducer(draft: Draft, action: DraftAction): Draft {
  const update = (key: string, change: (clip: DraftClip) => DraftClip): Draft => ({
    ...draft,
    clips: draft.clips.map((clip) => (clip.key === key ? change(clip) : clip)),
  });
  switch (action.type) {
    case 'reset':
      return action.draft;
    case 'sync':
      return mergeSuggestions(draft, action.suggestions);
    case 'select':
      return draft.selectedKey === action.key ? draft : { ...draft, selectedKey: action.key };
    case 'toggle':
      return update(action.key, (clip) => ({ ...clip, included: action.included ?? !clip.included }));
    case 'rename':
      return update(action.key, (clip) => ({ ...clip, title: action.title }));
    case 'edge':
      return update(action.key, (clip) =>
        action.edge === 'start'
          ? { ...clip, start: clamp(action.value, 0, clip.end - MIN_CLIP_SECONDS) }
          : { ...clip, end: clamp(action.value, clip.start + MIN_CLIP_SECONDS, Math.max(action.total, clip.end)) },
      );
    case 'add': {
      const start = clamp(action.start, 0, Math.max(0, action.total - MIN_CLIP_SECONDS));
      const end = clamp(action.end, start + MIN_CLIP_SECONDS, Math.max(action.total, start + MIN_CLIP_SECONDS));
      const n = draft.clips.reduce((max, clip) => Math.max(max, clip.n), 0) + 1;
      // The caller makes the key (newLocalKey): two clips made at the same spot must not share one.
      const key = draft.clips.some((clip) => clip.key === action.key) ? newLocalKey() : action.key;
      const clip: DraftClip = { key, clipId: null, n, title: action.title, start, end, score: null, reasoning: '', included: true, original: null };
      return { clips: [...draft.clips, clip], selectedKey: key };
    }
    case 'remove': {
      const clips = draft.clips.filter((clip) => clip.key !== action.key);
      return { clips, selectedKey: draft.selectedKey === action.key ? (clips[0]?.key ?? null) : draft.selectedKey };
    }
    default:
      return draft;
  }
}

export type ClipSort = 'score' | 'time';

/** "Puntaje": best first, the person's own clips after the suggestions. "Orden del video": by start time. */
export function sortClips(clips: ReadonlyArray<DraftClip>, sort: ClipSort): DraftClip[] {
  return [...clips].sort((a, b) =>
    sort === 'time' ? a.start - b.start : (b.score ?? -1) - (a.score ?? -1) || a.start - b.start,
  );
}

export function includedTotals(clips: ReadonlyArray<DraftClip>): { count: number; seconds: number } {
  const included = clips.filter((clip) => clip.included);
  return { count: included.length, seconds: included.reduce((sum, clip) => sum + (clip.end - clip.start), 0) };
}

export interface ApprovedSegmentInput {
  start: number;
  end: number;
  /** What is said in the segment: the render builds the subtitles from it and from nothing else. */
  text?: string;
  title: string;
  clip_id?: string;
}

/**
 * Body of `approve-paper-edit`: the included clips in video order. Every segment
 * carries the words inside its range, because the backend burns the subtitles
 * from each segment's `text` (render_video_segments); without it an approved
 * project with subtitles on comes back without them.
 */
export function buildApprovedSegments(clips: ReadonlyArray<DraftClip>, words: ReadonlyArray<Word>): ApprovedSegmentInput[] {
  return clips
    .filter((clip) => clip.included)
    .sort((a, b) => a.start - b.start)
    .map((clip) => {
      const range = wordsInRange(words, clip.start, clip.end);
      const text = range ? textOf(words, range.from, range.to) : '';
      return {
        start: clip.start,
        end: clip.end,
        ...(text ? { text } : {}),
        title: clip.title.trim() || `Clip ${clip.n}`,
        ...(clip.clipId ? { clip_id: clip.clipId } : {}),
      };
    });
}

/* ── Following the server ───────────────────────────────────────────── */

/**
 * Lays a draft over the suggestions the API returns now. For each suggestion a
 * field keeps the person's value only when they changed it (it differs from
 * the suggestion the draft started from) and that starting point is still what
 * the server has. Everything else follows the server: a trim saved in the clip
 * editor, or by a teammate, is never put back to its old value. A suggestion
 * that no longer exists is dropped and a new one is added as it comes.
 */
export function mergeSuggestions(current: Draft, suggestions: ReadonlyArray<Clip>): Draft {
  const fresh = initialDraft(suggestions);
  const byKey = new Map(current.clips.map((clip) => [clip.key, clip]));
  const merged = fresh.clips.map((clip): DraftClip => {
    const old = byKey.get(clip.key);
    const base = old?.original;
    if (!old || !base) return clip;
    const keep = <K extends 'title' | 'start' | 'end'>(field: K): DraftClip[K] =>
      old[field] !== base[field] && base[field] === clip[field] ? old[field] : clip[field];
    let start = keep('start');
    let end = keep('end');
    // An edit of one border against a new value of the other can cross: then the server wins both.
    if (end - start < MIN_CLIP_SECONDS) {
      start = clip.start;
      end = clip.end;
    }
    return { ...clip, title: keep('title'), start, end, included: old.included !== base.included ? old.included : clip.included };
  });
  const own = current.clips.filter((clip) => clip.clipId === null && Number.isFinite(clip.start) && clip.end > clip.start);
  let n = merged.length;
  const seen = new Set(merged.map((clip) => clip.key));
  const clips = [
    ...merged,
    ...own.map((clip) => {
      // Drafts stored by an older build could repeat a key.
      const key = seen.has(clip.key) ? newLocalKey() : clip.key;
      seen.add(key);
      return { ...clip, key, n: (n += 1), original: null };
    }),
  ];
  const selectedKey = clips.some((clip) => clip.key === current.selectedKey) ? current.selectedKey : fresh.selectedKey;
  return { clips, selectedKey };
}

/* ── Kept across a reload and the round trip to "Comprar tokens" ───────── */

/** `scope` is "<user id>.<project id>": a draft never shows up for another person on the same tab. */
const storageKey = (scope: string) => `oc.revision.${scope}`;

export function draftScope(userId: string, projectId: string): string {
  return `${userId}.${projectId}`;
}

export function saveDraft(scope: string, draft: Draft): void {
  try {
    if (isDirty(draft)) window.sessionStorage.setItem(storageKey(scope), JSON.stringify(draft));
    else window.sessionStorage.removeItem(storageKey(scope));
  } catch {
    // Storage blocked: the draft lives for this page only.
  }
}

export function clearDraft(scope: string): void {
  try {
    window.sessionStorage.removeItem(storageKey(scope));
  } catch {
    // Nothing to clear.
  }
}

/** The stored draft merged over the suggestions the API returns now (see mergeSuggestions). */
export function restoreDraft(scope: string, suggestions: ReadonlyArray<Clip>): Draft {
  let stored: Draft | null = null;
  try {
    const raw = window.sessionStorage.getItem(storageKey(scope));
    stored = raw ? (JSON.parse(raw) as Draft) : null;
  } catch {
    stored = null;
  }
  if (!stored || !Array.isArray(stored.clips)) return initialDraft(suggestions);
  return mergeSuggestions(stored, suggestions);
}
