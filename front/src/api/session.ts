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

export function clearSession(): void {
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

/** Fired when the refresh token is rejected: opens the "Sesión vencida" dialog (D9). */
export function onSessionExpired(listener: ExpiredListener): () => void {
  expiredListeners.add(listener);
  return () => expiredListeners.delete(listener);
}

export function notifySessionExpired(): void {
  for (const listener of expiredListeners) listener();
}

/** Stable snapshot for useSyncExternalStore: the access token, or null without a session. */
export function getAccessSnapshot(): string | null {
  return getTokens()?.access ?? null;
}
