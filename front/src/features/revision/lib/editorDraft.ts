/** Unsaved work of the clip editor (D4), kept per clip for the tab's session. */
export interface EditorDraft {
  title: string;
  start: number;
  end: number;
}

const storageKey = (clipId: string) => `oc.editor.${clipId}`;

/**
 * Keeps the unsaved trim and title across a reload, the Back button and the
 * round trip to buy tokens or change plan ("Tu configuración queda guardada").
 * Pass null to drop it (saved, or discarded on purpose).
 */
export function storeEditorDraft(clipId: string, draft: EditorDraft | null): void {
  try {
    if (draft) window.sessionStorage.setItem(storageKey(clipId), JSON.stringify(draft));
    else window.sessionStorage.removeItem(storageKey(clipId));
  } catch {
    // Storage blocked: the draft lives for this page only.
  }
}

export function readEditorDraft(clipId: string): EditorDraft | null {
  try {
    const raw = window.sessionStorage.getItem(storageKey(clipId));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<EditorDraft>;
    if (typeof value.title !== 'string' || typeof value.start !== 'number' || typeof value.end !== 'number') return null;
    if (!Number.isFinite(value.start) || !Number.isFinite(value.end) || value.end <= value.start || value.start < 0) return null;
    return { title: value.title, start: value.start, end: value.end };
  } catch {
    return null;
  }
}
