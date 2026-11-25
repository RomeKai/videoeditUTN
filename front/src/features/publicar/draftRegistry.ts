/**
 * Helper drafts the publish panel created to get the AI text, remembered in the
 * browser until they are discarded or published. The panel discards them when
 * it closes; this list is for the exits it cannot clean up after (a reload, a
 * closed tab, a workspace switch): the next time the app is open in that
 * workspace, `orphanDrafts()` says which ones nobody is using and they are discarded.
 */
export interface DraftEntry {
  id: string;
  clip: string;
  workspace: string;
  /** The browser tab that created it. */
  tab: string;
  at: number;
}

const KEY = 'oc.publicar.borradores';
const TAB_KEY = 'oc.publicar.pestana';
/** Another tab may still have its panel open: its drafts are left alone for this long. */
export const FOREIGN_DRAFT_TTL_MS = 60 * 60_000;

let memoryTab: string | null = null;

/** Identity of this browser tab: survives a reload, differs between tabs. */
export function tabId(): string {
  try {
    const stored = window.sessionStorage.getItem(TAB_KEY);
    if (stored) return stored;
    const fresh = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    window.sessionStorage.setItem(TAB_KEY, fresh);
    return fresh;
  } catch {
    memoryTab ??= `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    return memoryTab;
  }
}

function read(): DraftEntry[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(KEY) ?? '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is DraftEntry => Boolean(item) && typeof item.id === 'string' && typeof item.workspace === 'string');
  } catch {
    return [];
  }
}

function write(entries: DraftEntry[]): void {
  try {
    if (entries.length === 0) window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, JSON.stringify(entries));
  } catch {
    // Without storage the panel still discards its drafts when it closes.
  }
}

export function rememberDraft(entry: Omit<DraftEntry, 'tab' | 'at'>, now = Date.now()): void {
  write([...read().filter((item) => item.id !== entry.id), { ...entry, tab: tabId(), at: now }]);
}

export function forgetDraft(id: string): void {
  const entries = read();
  if (entries.some((item) => item.id === id)) write(entries.filter((item) => item.id !== id));
}

export const rememberedDrafts = (): DraftEntry[] => read();

/** True when this tab created the draft (and has not discarded or published it yet). */
export function isOwnDraft(id: string): boolean {
  const tab = tabId();
  return read().some((item) => item.id === id && item.tab === tab);
}

/**
 * Drafts of the workspace that nobody is using: the ones of this tab whose clip
 * is not in the open panel, and the ones of other tabs that are over an hour old.
 */
export function orphanDrafts(workspaceId: string, openClipIds: string[], now = Date.now()): DraftEntry[] {
  const tab = tabId();
  return read().filter((item) => {
    if (item.workspace !== workspaceId) return false;
    if (item.tab === tab) return !openClipIds.includes(item.clip);
    return now - item.at > FOREIGN_DRAFT_TTL_MS;
  });
}
