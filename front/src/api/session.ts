/**
 * Session state kept in the browser: the JWT pair of `/api/token/` and the
 * active workspace. Everything goes through here so the HTTP client, the auth
 * provider and the mocks agree on one source.
 */
export interface TokenPair {
  access: string;
  refresh: string;
}

export const AUTH_STORAGE_KEY = 'oc.auth';
export const WORKSPACE_STORAGE_KEY = 'oc.workspace';

type Listener = () => void;
const listeners = new Set<Listener>();
let memoryTokens: TokenPair | null = null;
let memoryWorkspace: string | null = null;

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

function emit() {
  for (const listener of listeners) listener();
}

export function getTokens(): TokenPair | null {
  const store = storage();
  if (!store) return memoryTokens;
  try {
    const raw = store.getItem(AUTH_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<TokenPair>;
    return typeof parsed.access === 'string' && typeof parsed.refresh === 'string'
      ? { access: parsed.access, refresh: parsed.refresh }
      : null;
  } catch {
    return null;
  }
}

/**
 * Counts every time this tab's pair is replaced (sign in, sign out, a new pair after changing the
 * password, a renewal) or another tab replaces it. A renewal that started under an older
 * generation must not store its result: see `refreshAccessToken`.
 */
let tokenGeneration = 0;

export function getTokenGeneration(): number {
  return tokenGeneration;
}

export function setTokens(tokens: TokenPair | null): void {
  tokenGeneration += 1;
  memoryTokens = tokens;
  // New tokens end the "expired" state; dropping them ends it too (there is nothing left to expire).
  expired = false;
  const store = storage();
  if (store) {
    if (tokens) store.setItem(AUTH_STORAGE_KEY, JSON.stringify(tokens));
    else store.removeItem(AUTH_STORAGE_KEY);
  }
  emit();
}

/**
 * The active workspace is one per browser: a switch in one tab reaches the others through the
 * `storage` event. A tab with unsaved changes must not follow it (its screen would reload in the
 * other workspace and the changes would be gone): while it holds, it keeps the workspace it had.
 */
let holding = false;
/** The workspace this tab stayed on when another tab switched. Null while it follows the browser's. */
let heldWorkspace: string | null = null;

/** The leave guard says whether this tab has unsaved changes on screen. */
export function setWorkspaceHold(hold: boolean): void {
  holding = hold;
}

const storedWorkspace = (): string | null => {
  const store = storage();
  return store ? store.getItem(WORKSPACE_STORAGE_KEY) : memoryWorkspace;
};

export function getActiveWorkspaceId(): string | null {
  return heldWorkspace ?? storedWorkspace();
}

/**
 * The workspace another tab switched to while this one stayed on its own, or null when this tab
 * follows the browser's. The tab moves to it with `setActiveWorkspaceId` (the shell offers it).
 */
export function getDeferredWorkspaceId(): string | null {
  if (heldWorkspace === null) return null;
  const stored = storedWorkspace();
  return stored !== null && stored !== heldWorkspace ? stored : null;
}

export function setActiveWorkspaceId(id: string | null): void {
  heldWorkspace = null;
  memoryWorkspace = id;
  const store = storage();
  if (store) {
    if (id) store.setItem(WORKSPACE_STORAGE_KEY, id);
    else store.removeItem(WORKSPACE_STORAGE_KEY);
  }
  emit();
}

/** Signs out locally: tokens and the active workspace (it belongs to that user). */
export function clearSession(): void {
  setActiveWorkspaceId(null);
  setTokens(null);
}

/**
 * What another tab did to the session. One listener for the whole page, however many subscribers
 * there are: it updates this tab's state once and then tells them. (One listener per subscriber
 * would run the hold logic once each, each on what the one before left: the second would undo the first.)
 */
function onStorage(event: StorageEvent): void {
  if (event.key !== AUTH_STORAGE_KEY && event.key !== WORKSPACE_STORAGE_KEY) return;
  // Another tab signed in again (or out): this tab's "expired" state is over too.
  if (event.key === AUTH_STORAGE_KEY) {
    expired = false;
    tokenGeneration += 1;
  }
  if (event.key === WORKSPACE_STORAGE_KEY) {
    // The other tab came back to this one's workspace: nothing is deferred any more.
    if (heldWorkspace !== null && event.newValue === heldWorkspace) heldWorkspace = null;
    // Another tab switched. With unsaved changes here, this tab stays where it was.
    else if (holding && heldWorkspace === null && event.oldValue && event.newValue && event.newValue !== event.oldValue) heldWorkspace = event.oldValue;
  }
  emit();
}
let listening = false;

/** Subscribes to token and workspace changes (also from other tabs). */
export function subscribeSession(listener: Listener): () => void {
  listeners.add(listener);
  if (!listening && typeof window !== 'undefined') {
    window.addEventListener('storage', onStorage);
    listening = true;
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && listening) {
      window.removeEventListener('storage', onStorage);
      listening = false;
    }
  };
}

/** Whether this tab is staying on a workspace of its own (see `setWorkspaceHold`). */
export function isWorkspaceHeld(): boolean {
  return heldWorkspace !== null;
}

type ExpiredListener = () => void;
const expiredListeners = new Set<ExpiredListener>();
let expired = false;

/** Fired when the refresh token is rejected: opens the "Sesión vencida" dialog (D9). */
export function onSessionExpired(listener: ExpiredListener): () => void {
  expiredListeners.add(listener);
  return () => expiredListeners.delete(listener);
}

/**
 * The session could not be renewed. From here on authenticated calls fail at
 * once (no more requests with a dead token) until new tokens are stored.
 */
export function notifySessionExpired(): void {
  expired = true;
  for (const listener of expiredListeners) listener();
}

export function isSessionExpired(): boolean {
  return expired;
}

type MaintenanceListener = (active: boolean) => void;
const maintenanceListeners = new Set<MaintenanceListener>();

/** Fired when the API answers 503 (true) and when it answers normally again (false): page K5. */
export function onMaintenance(listener: MaintenanceListener): () => void {
  maintenanceListeners.add(listener);
  return () => maintenanceListeners.delete(listener);
}

export function notifyMaintenance(active: boolean): void {
  for (const listener of maintenanceListeners) listener(active);
}

/** Stable snapshot for useSyncExternalStore: the access token, or null without a session. */
export function getAccessSnapshot(): string | null {
  return getTokens()?.access ?? null;
}

/** Stable snapshot of `getDeferredWorkspaceId` for useSyncExternalStore. */
export function getDeferredWorkspaceSnapshot(): string | null {
  return getDeferredWorkspaceId();
}

/** Stable snapshot of the active workspace id for useSyncExternalStore. */
export function getWorkspaceSnapshot(): string | null {
  return getActiveWorkspaceId();
}
