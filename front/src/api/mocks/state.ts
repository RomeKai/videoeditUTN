import { isHybrid } from '../routing';
import { clearSession, getTokens, setActiveWorkspaceId, setTokens, type TokenPair } from '../session';
import { DB_VERSION, type MockDb } from './db';
import { DEFAULT_SCENARIO, findScenario } from './scenarios';
import { seedDb } from './seed';

export const MOCK_DB_KEY = 'oc.mock.db';
export const MOCK_SCENARIO_KEY = 'oc.mock.scenario';
/** Set on logout so the scenario's auto-login does not sign the user back in. */
export const MOCK_LOGGED_OUT_KEY = 'oc.mock.loggedOut';

let memoryDb: MockDb | null = null;
let memoryScenario: string | null = null;

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function getScenarioId(): string {
  const stored = storage()?.getItem(MOCK_SCENARIO_KEY) ?? memoryScenario;
  const scenario = findScenario(stored ?? DEFAULT_SCENARIO);
  // A scenario about the mocked session or its data (chosen in mock mode) makes no sense with a real session.
  return isHybrid() && !scenario.hybrid ? DEFAULT_SCENARIO : scenario.id;
}

/**
 * The database lives in localStorage so every tab (and the popup windows of the
 * fake checkout and network authorization) see the same state, and a reload
 * keeps what was created during the session.
 */
export function loadDb(): MockDb {
  const scenario = getScenarioId();
  const store = storage();
  if (store) {
    try {
      const raw = store.getItem(MOCK_DB_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as MockDb;
        if (parsed.version === DB_VERSION && parsed.scenario === scenario) return parsed;
      }
    } catch {
      // Corrupt or too old: seed again.
    }
  } else if (memoryDb && memoryDb.scenario === scenario) {
    return memoryDb;
  }
  const fresh = seedDb({ scenario, ...findScenario(scenario).options });
  saveDb(fresh);
  return fresh;
}

export function saveDb(db: MockDb): void {
  memoryDb = db;
  const store = storage();
  if (!store) return;
  try {
    store.setItem(MOCK_DB_KEY, JSON.stringify(db));
  } catch {
    // Quota exceeded: keep working from memory for this page.
  }
}

/** Mock JWTs carry the user id in clear: `mock-access.<userId>.<nonce>`. */
export function mockTokens(userId: string): TokenPair {
  const nonce = Date.now().toString(36);
  return { access: `mock-access.${userId}.${nonce}`, refresh: `mock-refresh.${userId}.${nonce}` };
}

export function userIdFromToken(token: string | null | undefined, kind: 'access' | 'refresh'): string | null {
  if (!token) return null;
  const [prefix, userId] = token.split('.');
  return prefix === `mock-${kind}` && userId ? userId : null;
}

/** Throws the stored database away and seeds the scenario again. */
export function resetDb(scenarioId: string = getScenarioId()): MockDb {
  const scenario = findScenario(scenarioId);
  memoryScenario = scenario.id;
  storage()?.setItem(MOCK_SCENARIO_KEY, scenario.id);
  const fresh = seedDb({ scenario: scenario.id, ...scenario.options });
  saveDb(fresh);
  return fresh;
}

/** Switches scenario: seeds it and signs in (or out) the user the scenario starts with. The caller reloads the page. */
export function applyScenario(scenarioId: string): void {
  const db = resetDb(scenarioId);
  // Hybrid: the session is the real one and stays as it is; what was adopted from the backend comes back on its own.
  if (isHybrid()) return;
  storage()?.removeItem(MOCK_LOGGED_OUT_KEY);
  syncSession(db, true);
}

/**
 * Mocked session: when the scenario starts signed in and the browser has no
 * tokens, store mock tokens so the app opens on its screens. Not after an
 * explicit logout.
 */
export function syncSession(db: MockDb = loadDb(), force = false): void {
  const store = storage();
  if (!store) return;
  if (!db.sessionUserId) {
    if (force) setTokens(null);
    return;
  }
  if (!force && store.getItem(MOCK_LOGGED_OUT_KEY)) return;
  const hasTokens = Boolean(store.getItem('oc.auth'));
  if (hasTokens && !force) return;
  setTokens(mockTokens(db.sessionUserId));
  const user = db.users.find((item) => item.id === db.sessionUserId);
  setActiveWorkspaceId(user?.active_workspace_id ?? null);
}

/**
 * Tokens left by the other mode are of no use: the real backend rejects the mock ones (hybrid) and the
 * mocks do not know the user of a real one (mock). The session is dropped, so the app asks to sign in
 * (hybrid) or the scenario signs its user in again (mock).
 */
export function dropForeignSession(): void {
  const access = getTokens()?.access;
  if (!access) return;
  const isMock = userIdFromToken(access, 'access') !== null;
  if (isMock === isHybrid()) clearSession();
}

export function markLoggedOut(): void {
  storage()?.setItem(MOCK_LOGGED_OUT_KEY, '1');
}

export function clearLoggedOut(): void {
  storage()?.removeItem(MOCK_LOGGED_OUT_KEY);
}
