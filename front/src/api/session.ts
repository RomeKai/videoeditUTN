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

export function setTokens(tokens: TokenPair | null): void {
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

export function getActiveWorkspaceId(): string | null {
  const store = storage();
  return store ? store.getItem(WORKSPACE_STORAGE_KEY) : memoryWorkspace;
}

export function setActiveWorkspaceId(id: string | null): void {
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

/** Subscribes to token and workspace changes (also from other tabs). */
export function subscribeSession(listener: Listener): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === AUTH_STORAGE_KEY || event.key === WORKSPACE_STORAGE_KEY) listener();
  };
  if (typeof window !== 'undefined') window.addEventListener('storage', onStorage);
  return () => {
    listeners.delete(listener);
    if (typeof window !== 'undefined') window.removeEventListener('storage', onStorage);
  };
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

/** Seconds until a JWT expires, or null when the token is not a readable JWT (the mock tokens are not). */
export function secondsUntilExpiry(token: string, now: number = Date.now()): number | null {
  const payload = token.split('.')[1];
  if (!payload) return null;
  try {
    const json = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/'))) as { exp?: unknown };
    return typeof json.exp === 'number' ? json.exp - now / 1000 : null;
  } catch {
    return null;
  }
}

/** Stable snapshot for useSyncExternalStore: the access token, or null without a session. */
export function getAccessSnapshot(): string | null {
  return getTokens()?.access ?? null;
}

/** Stable snapshot of the active workspace id for useSyncExternalStore. */
export function getWorkspaceSnapshot(): string | null {
  return getActiveWorkspaceId();
}
