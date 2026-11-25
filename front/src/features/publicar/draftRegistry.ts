/**
 * Helper drafts the publish panel created to get the AI text, remembered in the
 * browser until they are discarded or published. The panel discards them when
 * it closes; this list is for the exits it cannot clean up after (a reload, a
 * closed tab, a workspace switch): the next time the app is open in that
 * workspace, the ones nobody is using are discarded.
 *
 * "Nobody is using" is checked three ways, because discarding a draft a panel
 * still shows would lose the person's text:
 * - panels of this page hold their drafts in `held`;
 * - panels of other tabs answer for theirs over a BroadcastChannel;
 * - an open panel keeps the entry's time fresh. A tab that is slow or in the background may not
 *   answer in time, so an entry refreshed in the last two minutes is in use whatever the answers
 *   say; and where there is no BroadcastChannel, only entries nobody refreshed for an hour go.
 *   A panel that closes (or whose page goes away) clears the time of what it could not discard:
 *   those are nobody's from that moment.
 */
export interface DraftEntry {
  id: string;
  clip: string;
  workspace: string;
  /** Last time a panel said it was using it. 0: its panel let go of it. */
  at: number;
}

const KEY = 'oc.publicar.borradores';
const CHANNEL = 'oc.publicar.borradores';
/** Without a channel to ask: an entry no panel refreshed for this long is nobody's. */
export const STALE_DRAFT_MS = 60 * 60_000;
/** How often an open panel refreshes its entries. */
export const TOUCH_DRAFTS_MS = 60_000;
/** How long other tabs get to answer. */
const ANSWER_MS = 300;
/**
 * An entry refreshed this recently is in use even when no tab answered: a background tab runs its
 * timers late (browsers hold them to one a minute) and may take longer than `ANSWER_MS` to reply.
 * Two refresh periods, so one late refresh is not enough to lose a draft.
 */
export const RECENT_DRAFT_MS = 2 * TOUCH_DRAFTS_MS;

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

/** Drafts that a panel of this page is using right now. */
const held = new Set<string>();

type Message = { type: 'ask'; nonce: string; ids: string[] } | { type: 'held'; nonce: string; ids: string[] };
let answering: BroadcastChannel | null | undefined;

/** Starts answering other tabs that ask whether a draft is in use here. */
function answer(): void {
  if (answering !== undefined) return;
  if (typeof BroadcastChannel === 'undefined') {
    answering = null;
    return;
  }
  answering = new BroadcastChannel(CHANNEL);
  answering.onmessage = (event: MessageEvent<Message>) => {
    const message = event.data;
    if (message?.type !== 'ask') return;
    const mine = message.ids.filter((id) => held.has(id));
    if (mine.length > 0) answering?.postMessage({ type: 'held', nonce: message.nonce, ids: mine } satisfies Message);
  };
}

/** The panel created (or took back) the draft: it is in use until `releaseDraft`. */
export function holdDraft(entry: Omit<DraftEntry, 'at'>, now = Date.now()): void {
  answer();
  held.add(entry.id);
  write([...read().filter((item) => item.id !== entry.id), { ...entry, at: now }]);
}

/**
 * The panel no longer uses it, but it may still exist on the server (the discard failed): it stays
 * remembered, with no "last used" time. Its panel said it is done, so the next sweep can take it
 * without waiting the two minutes a draft that may still be on screen is given.
 */
export function releaseDraft(id: string): void {
  held.delete(id);
  const entries = read();
  if (entries.some((item) => item.id === id && item.at !== 0)) write(entries.map((item) => (item.id === id ? { ...item, at: 0 } : item)));
}

/** The draft is gone, went out, or became the person's own: nothing to clean up. */
export function forgetDraft(id: string): void {
  held.delete(id);
  const entries = read();
  if (entries.some((item) => item.id === id)) write(entries.filter((item) => item.id !== id));
}

/** An open panel says its drafts are still in use. */
export function touchDrafts(ids: string[], now = Date.now()): void {
  if (ids.length === 0) return;
  const entries = read();
  if (entries.some((item) => ids.includes(item.id))) write(entries.map((item) => (ids.includes(item.id) ? { ...item, at: now } : item)));
}

export const rememberedDrafts = (): DraftEntry[] => read();
export const isRemembered = (id: string): boolean => read().some((item) => item.id === id);
export const isHeld = (id: string): boolean => held.has(id);

/**
 * Whether the draft is still nobody's, asked again right before discarding it: a panel of this
 * page did not take it, no panel of its clip opened here, it is still remembered, and no tab
 * refreshed its entry since it was listed as an orphan (taking a draft back writes the entry again).
 */
export function isStillOrphan(entry: DraftEntry, openClipIds: string[]): boolean {
  if (held.has(entry.id) || openClipIds.includes(entry.clip)) return false;
  const current = read().find((item) => item.id === entry.id);
  return current !== undefined && current.at <= entry.at;
}

/** Ids that a panel in another tab says it is using. Null when other tabs cannot be asked. */
function heldElsewhere(ids: string[]): Promise<Set<string> | null> {
  if (typeof BroadcastChannel === 'undefined') return Promise.resolve(null);
  return new Promise((resolve) => {
    const nonce = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    const found = new Set<string>();
    const channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = (event: MessageEvent<Message>) => {
      const message = event.data;
      if (message?.type === 'held' && message.nonce === nonce) for (const id of message.ids) found.add(id);
    };
    channel.postMessage({ type: 'ask', nonce, ids } satisfies Message);
    setTimeout(() => {
      channel.close();
      resolve(found);
    }, ANSWER_MS);
  });
}

export interface OrphanSearch {
  /** Drafts nobody is using: they can be discarded. */
  orphans: DraftEntry[];
  /** When to look again: some entries were only kept for being recent. Null when there is nothing to wait for. */
  retryAt: number | null;
}

/**
 * Remembered drafts of the workspace that no panel is using: not held in this
 * page, not of a clip whose panel is open here, not claimed by another tab and
 * not refreshed in the last two minutes (or, when other tabs cannot be asked,
 * not refreshed for an hour).
 */
export async function findOrphanDrafts(workspaceId: string, openClipIds: string[], now = Date.now()): Promise<OrphanSearch> {
  const candidates = read().filter((item) => item.workspace === workspaceId && !held.has(item.id) && !openClipIds.includes(item.clip));
  if (candidates.length === 0) return { orphans: [], retryAt: null };
  const elsewhere = await heldElsewhere(candidates.map((item) => item.id));
  // A panel of this page may have taken one back while the others answered; another tab's may have refreshed its own.
  const current = new Map(read().map((item) => [item.id, item]));
  const free = candidates.flatMap((item) => {
    const entry = current.get(item.id);
    return entry && !held.has(item.id) ? [entry] : [];
  });
  if (!elsewhere) return { orphans: free.filter((item) => now - item.at > STALE_DRAFT_MS), retryAt: null };
  const unclaimed = free.filter((item) => !elsewhere.has(item.id));
  const recent = unclaimed.filter((item) => now - item.at <= RECENT_DRAFT_MS);
  return {
    orphans: unclaimed.filter((item) => now - item.at > RECENT_DRAFT_MS),
    retryAt: recent.length > 0 ? Math.min(...recent.map((item) => item.at)) + RECENT_DRAFT_MS + 1 : null,
  };
}

/** The orphans of `findOrphanDrafts`. */
export async function orphanDrafts(workspaceId: string, openClipIds: string[], now = Date.now()): Promise<DraftEntry[]> {
  return (await findOrphanDrafts(workspaceId, openClipIds, now)).orphans;
}
