/**
 * Session state kept in the browser: the JWT pair of `/api/token/` and the
 * active workspace. Everything goes through here so the HTTP client, the auth
 * provider and the mocks agree on one source.
 */
import type { SessionEndReason } from './errors';

export interface TokenPair {
  access: string;
  refresh: string;
}

export const AUTH_STORAGE_KEY = 'oc.auth';
export const WORKSPACE_STORAGE_KEY = 'oc.workspace';
/** The email of the account the stored session belongs to: kept and dropped with the session. */
export const SESSION_EMAIL_STORAGE_KEY = 'oc.auth.email';

type Listener = () => void;
const listeners = new Set<Listener>();
let memoryTokens: TokenPair | null = null;
let memoryWorkspace: string | null = null;
let memoryEmail: string | null = null;

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

/** The pair in the browser's storage, whatever this tab thinks of it. */
function storedTokens(): TokenPair | null {
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
 * The session this tab works with: the stored pair, unless this tab gave it up for dead
 * (`dropExpiredSession`). Then there is none here until another pair is stored.
 */
export function getTokens(): TokenPair | null {
  const tokens = storedTokens();
  return tokens && ended && ended.refresh === tokens.refresh ? null : tokens;
}

/**
 * Counts every time this tab stores a pair (sign in, sign out, the new pair of a password change,
 * a renewal). A renewal that started under an older generation must not store its result: see
 * `refreshAccessToken`. What another tab stores does not count here: its `storage` event can
 * arrive late, after this tab already read that pair and started renewing with it, and the answer
 * of that renewal is the valid one. Another tab's pair is recognised by comparing the stored pair.
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
  // Signing in (or out) is the end of "your session expired" as well.
  ended = null;
  rejectedRefresh = null;
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

/** Signs out locally: tokens, the active workspace (it belongs to that user) and whose session it was. */
export function clearSession(): void {
  setActiveWorkspaceId(null);
  rememberSessionEmail(null);
  setTokens(null);
}

/**
 * Whose session is stored: the profile says it once it loads (the token only carries the user's id).
 * It is what the app knows of a session that turns out to be dead before the profile ever answered
 * (the browser was closed for longer than the session lasts): enough to ask for the password of
 * that account again without making the person type the email.
 */
export function rememberSessionEmail(email: string | null): void {
  if (email === getSessionEmail()) return;
  memoryEmail = email;
  try {
    const store = storage();
    if (email) store?.setItem(SESSION_EMAIL_STORAGE_KEY, email);
    else store?.removeItem(SESSION_EMAIL_STORAGE_KEY);
  } catch {
    // Kept in memory for this page.
  }
}

export function getSessionEmail(): string | null {
  try {
    const store = storage();
    return store ? store.getItem(SESSION_EMAIL_STORAGE_KEY) : memoryEmail;
  } catch {
    return memoryEmail;
  }
}

/** A stored session this tab gave up for dead: why, whose it was when that is known, and which pair it was. */
export interface EndedSession {
  reason: SessionEndReason;
  email: string | null;
  /** The refresh token of the dead pair: another pair in the storage is another session, and a live one. */
  refresh: string;
}
let ended: EndedSession | null = null;
/** The refresh token the server last refused to renew (`noteRefreshRejected`). */
let rejectedRefresh: string | null = null;

/** The client says which refresh token the server refused: that pair, and no other, is the dead one. */
export function noteRefreshRejected(refresh: string): void {
  rejectedRefresh = refresh;
}

/**
 * Gives up, in this tab only, a stored session that turned out to be dead on a screen that works without
 * one (the login, the public pages): there is nothing to keep on screen, so no "Sesión vencida" dialog. The
 * login says what happened and fills in the email (`getEndedSession`).
 *
 * The storage is left alone: another tab may have the "Sesión vencida" dialog open over a screen it
 * promised to keep, and taking the pair away would send it to the login. From here on this tab simply has
 * no session (`getTokens` is null) until another pair is stored, here or in another tab.
 *
 * False, and nothing is given up, when the stored pair is no longer the one the server refused: another tab
 * renewed it or signed in meanwhile, and that pair is good. The caller reads again with it.
 */
export function dropExpiredSession(reason: SessionEndReason = 'expired'): boolean {
  const stored = storedTokens();
  if (stored && rejectedRefresh !== null && stored.refresh !== rejectedRefresh) {
    expired = false;
    emit();
    return false;
  }
  expired = false;
  ended = stored ? { reason, email: getSessionEmail(), refresh: stored.refresh } : null;
  emit();
  return true;
}

/** The session `dropExpiredSession` gave up in this tab, while it is still the stored one; else null. Stable between changes (useSyncExternalStore). */
export function getEndedSession(): EndedSession | null {
  if (ended && storedTokens()?.refresh !== ended.refresh) ended = null;
  return ended;
}

let sessionNeeded = 0;

/**
 * A screen that cannot be shown without a session is up (`RequireSession`): if the session turns out
 * to be dead, the person is asked for the password on top of it (dialog D9). Returns how to undo it.
 */
export function holdSessionNeeded(): () => void {
  sessionNeeded += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    sessionNeeded -= 1;
  };
}

export function isSessionNeeded(): boolean {
  return sessionNeeded > 0;
}

/**
 * What another tab did to the session. One listener for the whole page, however many subscribers
 * there are: it updates this tab's state once and then tells them. (One listener per subscriber
 * would run the hold logic once each, each on what the one before left: the second would undo the first.)
 */
function onStorage(event: StorageEvent): void {
  if (event.key !== AUTH_STORAGE_KEY && event.key !== WORKSPACE_STORAGE_KEY) return;
  // Another tab signed in again (or out): this tab's "expired" state is over too.
  // (A session this tab gave up stays given up while the same dead pair is stored: `getEndedSession`.)
  if (event.key === AUTH_STORAGE_KEY) expired = false;
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

type ExpiredListener = (reason: SessionEndReason) => void;
const expiredListeners = new Set<ExpiredListener>();
let expired = false;
let expiredReason: SessionEndReason = 'expired';

/** Fired when the refresh token is rejected: opens the "Sesión vencida" dialog (D9). */
export function onSessionExpired(listener: ExpiredListener): () => void {
  expiredListeners.add(listener);
  return () => expiredListeners.delete(listener);
}

/**
 * The session could not be renewed. From here on authenticated calls fail at
 * once (no more requests with a dead token) until new tokens are stored.
 */
export function notifySessionExpired(reason: SessionEndReason = 'expired'): void {
  // The requests in flight find the same dead session one after another, and not all of them learn
  // why (only a 401 `session_revoked` says it): the one that knows is not overwritten by the rest.
  expiredReason = expired && expiredReason === 'revoked' ? 'revoked' : reason;
  expired = true;
  for (const listener of expiredListeners) listener(expiredReason);
}

export function isSessionExpired(): boolean {
  return expired;
}

type MaintenanceListener = (active: boolean) => void;
const maintenanceListeners = new Set<MaintenanceListener>();

/** Fired when the API answers a maintenance 503 (true; see `isMaintenance` in ./client) and when it answers anything else (false): page K5. */
export function onMaintenance(listener: MaintenanceListener): () => void {
  maintenanceListeners.add(listener);
  return () => maintenanceListeners.delete(listener);
}

export function notifyMaintenance(active: boolean): void {
  for (const listener of maintenanceListeners) listener(active);
}

/**
 * Whose token it is, read from the token itself and never verified: the `user_id` simplejwt puts in the
 * payload. A token whose middle part is not that (the mocks' carry the id in clear) gives that part as it is:
 * two tokens of the same user still give the same answer, and anything unreadable is nobody's.
 */
export function tokenUser(token: string | null | undefined): string | null {
  const parts = (token ?? '').split('.');
  const claims = parts.length === 3 ? parts[1] : undefined;
  if (!claims) return null;
  try {
    const base64 = claims.replace(/-/g, '+').replace(/_/g, '/');
    const payload: unknown = JSON.parse(atob(base64 + '='.repeat((4 - (base64.length % 4)) % 4)));
    const id = payload && typeof payload === 'object' ? (payload as { user_id?: unknown }).user_id : undefined;
    if (typeof id === 'string' || typeof id === 'number') return String(id);
  } catch {
    // Not a JWT payload.
  }
  return claims;
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
