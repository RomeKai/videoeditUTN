/** What the clip editor (D4) edits. */
export interface EditorValues {
  title: string;
  start: number;
  end: number;
}

/** Unsaved work of the editor, kept per person and clip for the tab's session. */
export interface EditorDraft extends EditorValues {
  /** The server's values the edit started from: only what differs from them is the person's work. */
  base: EditorValues;
}

/** `scope` is "<user id>.<clip id>": a draft never shows up for another person on the same tab. */
const storageKey = (scope: string) => `oc.editor.${scope}`;

export function editorScope(userId: string, clipId: string): string {
  return `${userId}.${clipId}`;
}

/**
 * Keeps the unsaved trim and title across a reload, the Back button and the
 * round trip to buy tokens or change plan ("Tu configuración queda guardada").
 * Pass null to drop it (saved, or discarded on purpose).
 */
export function storeEditorDraft(scope: string, draft: EditorDraft | null): void {
  try {
    if (draft) window.sessionStorage.setItem(storageKey(scope), JSON.stringify(draft));
    else window.sessionStorage.removeItem(storageKey(scope));
  } catch {
    // Storage blocked: the draft lives for this page only.
  }
}

function isValues(value: unknown): value is EditorValues {
  if (!value || typeof value !== 'object') return false;
  const { title, start, end } = value as Partial<EditorValues>;
  return typeof title === 'string' && typeof start === 'number' && typeof end === 'number' && Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start;
}

export function readEditorDraft(scope: string): EditorDraft | null {
  try {
    const raw = window.sessionStorage.getItem(storageKey(scope));
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    const base: unknown = value && typeof value === 'object' ? (value as { base?: unknown }).base : null;
    // A draft without its starting point cannot tell the person's edits from stale values: drop it.
    if (!isValues(value) || !isValues(base)) return null;
    return { title: value.title, start: value.start, end: value.end, base: { title: base.title, start: base.start, end: base.end } };
  } catch {
    return null;
  }
}

/**
 * What the editor opens with: the server's clip, with only the fields the
 * person really changed (they differ from the values the draft started from)
 * laid over it. A title-only draft never pushes an old trim back when a
 * teammate moved it meanwhile, and the other way round.
 */
export function applyEditorDraft(server: EditorValues, draft: EditorDraft | null): EditorValues {
  if (!draft) return server;
  const title = draft.title !== draft.base.title ? draft.title : server.title;
  let start = draft.start !== draft.base.start ? draft.start : server.start;
  let end = draft.end !== draft.base.end ? draft.end : server.end;
  if (end <= start) {
    start = server.start;
    end = server.end;
  }
  return { title, start, end };
}
