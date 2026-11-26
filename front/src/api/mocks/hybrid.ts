import { backendDefaultWorkspaceId, hasBackendDefaultPrefix } from '../config';
import { isHybrid } from '../routing';
import { newId, type MockClip, type MockDb, type MockProject, type MockUser, type MockWorkspace } from './db';
import { findScenario } from './scenarios';
import { buildProject, PLAN_IDS, socialAccounts, USER_IDS } from './seed';

/**
 * DEV BRIDGE of hybrid mode: how the mocks keep up with a session and with data that live in the real
 * backend. None of this runs in mock mode, and none of it is part of the contract.
 *
 * - The session is real (`auth` is never mocked in hybrid), so a mocked endpoint gets a real JWT. It reads
 *   the user id from the token's payload (simplejwt access tokens carry `user_id`) and creates that user in
 *   the mock database the first time it sees it ("adoption"). The signature cannot be checked here: the
 *   mocks trust the token the way they trust their own.
 * - While `/me` is mocked, the adopted user has one placeholder workspace (`backendDefaultWorkspaceId`):
 *   the client sends no `X-Workspace-Id` for it and the backend uses the user's default workspace.
 * - When a mocked endpoint gets the id of a workspace, a project or a clip that only the backend knows, it
 *   adopts it too: a stand-in row with that id, so the mocked part of the app works for it.
 */

export interface JwtClaims {
  user_id: string;
  email: string | null;
  /** Epoch seconds. */
  exp: number;
}

function decodeBase64Url(value: string): string {
  const base64 = value.replace(/-/g, '+').replace(/_/g, '/');
  const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  return new TextDecoder().decode(Uint8Array.from(binary, (char) => char.charCodeAt(0)));
}

/** Payload of a simplejwt token, unverified. Null for anything that is not a JWT with a `user_id` and a numeric `exp`. */
export function readJwt(token: string | null | undefined): JwtClaims | null {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 3 || !parts[1]) return null;
  try {
    const payload = JSON.parse(decodeBase64Url(parts[1])) as Record<string, unknown>;
    const id = payload.user_id;
    if ((typeof id !== 'string' && typeof id !== 'number') || String(id).trim() === '') return null;
    // simplejwt always sets a numeric `exp`. Anything else would be a token that never expires here.
    if (typeof payload.exp !== 'number' || !Number.isFinite(payload.exp)) return null;
    return { user_id: String(id), email: typeof payload.email === 'string' ? payload.email : null, exp: payload.exp };
  } catch {
    return null;
  }
}

export const HYBRID_LOGIN_EMAIL_KEY = 'oc.hybrid.loginEmail';
let memoryEmail: string | null = null;

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

/**
 * simplejwt does not put the email in the token, so the email typed at the (real) login is kept for the
 * mocked `/me`. It is the address the backend just accepted for that user.
 */
export function rememberLoginEmail(email: string | null): void {
  memoryEmail = email;
  try {
    const store = storage();
    if (email) store?.setItem(HYBRID_LOGIN_EMAIL_KEY, email);
    else store?.removeItem(HYBRID_LOGIN_EMAIL_KEY);
  } catch {
    // Kept in memory for this page.
  }
}

function lastLoginEmail(): string | null {
  try {
    return storage()?.getItem(HYBRID_LOGIN_EMAIL_KEY) ?? memoryEmail;
  } catch {
    return memoryEmail;
  }
}

const iso = (ms: number) => new Date(ms).toISOString();

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const isUuid = (value: string | undefined): value is string => Boolean(value && UUID.test(value));

/** What an adopted row is, in the first group of its id. */
const ADOPTED = { wallet: '0c0a0001', member: '0c0a0002', clipProject: '0c0a0003' } as const;

/**
 * Id of a row made for a real one, derived from the real id (and, for a membership, from the user's too).
 * Several requests reach the mocks at once on the first load, each with its own copy of the database: with
 * a random id each would create a different wallet or stand-in and the last one saved would win. Derived
 * ids make every copy create the same row.
 */
function adoptedId(kind: keyof typeof ADOPTED, realId: string, otherId?: string): string {
  if (!isUuid(realId)) return newId();
  const tail = otherId ? otherId.toLowerCase().replace(/[^0-9a-f]/g, '').slice(-12).padStart(12, '0') : realId.slice(24);
  return `${ADOPTED[kind]}${realId.slice(8, 24)}${tail}`.toLowerCase();
}

const isEmail = (value: string | null): value is string => Boolean(value && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value));

/** Plan and balance an adopted workspace starts with: the ones of the scenario in use (Plan Gratis, Saldo bajo…). */
function scenarioDefaults(db: MockDb): { plan: string; balance: number } {
  const options = findScenario(db.scenario).options;
  const plan = options.plan ?? 'pro';
  return { plan: PLAN_IDS[plan], balance: options.balance ?? (plan === 'free' ? 300 : 4860) };
}

/**
 * The user of a real token, created on first sight with the placeholder workspace. Null outside hybrid
 * mode, for a token that is not a JWT and for one that already expired (the mocks answer 401 and the
 * client renews it against the real backend, exactly as a real endpoint would make it do).
 */
export function adoptUserFromToken(db: MockDb, token: string, now: number): MockUser | null {
  if (!isHybrid()) return null;
  const claims = readJwt(token);
  if (!claims) return null;
  if (claims.exp * 1000 <= now) return null;
  const known = db.users.find((item) => item.id === claims.user_id);
  if (known) return known;
  const remembered = lastLoginEmail();
  const email = isEmail(claims.email) ? claims.email : isEmail(remembered) ? remembered : `usuario-${claims.user_id.slice(0, 8)}@backend.invalid`;
  const user: MockUser = {
    id: claims.user_id,
    name: email.split('@')[0] ?? 'Usuario del backend',
    email,
    is_email_verified: true,
    // The first-time tour is a mocked flow: it would run for every real user on every reset of the mock data.
    tour_completed: true,
    terms_accepted_at: iso(now),
    pending_email: null,
    onboarding_use_case: null,
    active_workspace_id: backendDefaultWorkspaceId(claims.user_id),
    notification_preferences: { project_ready: true, render_finished: true, project_failed: true, post_failed: true, low_balance: true },
  };
  db.users.push(user);
  adoptWorkspace(db, user, backendDefaultWorkspaceId(claims.user_id), now);
  return user;
}

/**
 * The workspace of an id the mocks did not know, with what the mocked domains need: an active membership
 * for the user, a wallet with the scenario's balance, the scenario's plan and the three networks, not
 * connected. A workspace another real user already brought in gets the membership only.
 *
 * The mocks cannot know the user's real role: they treat them as admin (owner, when they bring the
 * workspace in). The backend still enforces the real role on everything it answers.
 */
export function adoptWorkspace(db: MockDb, user: MockUser, id: string, now: number): MockWorkspace | null {
  if (!isHybrid()) return null;
  let workspace = db.workspaces.find((item) => item.id === id) ?? null;
  if (!workspace) {
    const defaults = scenarioDefaults(db);
    workspace = {
      id,
      name: hasBackendDefaultPrefix(id) ? 'Workspace por defecto (backend)' : 'Workspace del backend',
      owner: user.id,
      subscription_plan: defaults.plan,
      created_at: iso(now),
      renews_at: defaults.plan === PLAN_IDS.free ? null : iso(now + 30 * 24 * 3600 * 1000),
      pending_plan: null,
    };
    db.workspaces.push(workspace);
    db.wallets.push({ id: adoptedId('wallet', id), workspace: id, available_balance: defaults.balance, reserved_balance: 0, updated_at: iso(now) });
    db.socialAccounts.push(...socialAccounts(id, {}, now));
  }
  // A workspace of the scenario (Estudio Norte…) is not the backend's: a real user never joins it.
  else if (SEEDED_USERS.has(workspace.owner)) return null;
  const member = db.members.some((item) => item.workspace === id && item.user === user.id && item.status === 'active');
  if (!member) {
    db.members.push({ id: adoptedId('member', id, user.id), workspace: id, user: user.id, email: user.email, role: 'admin', status: 'active', joined_at: iso(now), invited_by: null, token: null });
  }
  return workspace;
}

const SEEDED_USERS = new Set<string>(Object.values(USER_IDS));

function standIn(db: MockDb, workspaceId: string, id: string, withClip: string | null): MockProject {
  const now = Date.now();
  const workspace = db.workspaces.find((item) => item.id === workspaceId);
  const owner = db.users.find((item) => item.id === workspace?.owner);
  const built = buildProject(
    {
      // Fixed: every copy of the database builds the same stand-in.
      index: 9000,
      title: 'Proyecto del backend',
      status: 'completed',
      stage: 'completed',
      durationSeconds: 600,
      uploadedBy: owner?.id ?? '',
      ageMs: 0,
      clips: withClip ? { count: 1, statuses: ['completed'] } : undefined,
      workspace: workspaceId,
    },
    now,
    null,
  );
  const clips: MockClip[] = withClip ? built.clips.slice(0, 1).map((clip) => ({ ...clip, id: withClip, project: id, title: 'Clip del backend', s3_object_key: `clips/${id}/${withClip}.mp4` })) : [];
  const { reservation_id: _reservation, ...metadata } = built.metadata;
  const project: MockProject = {
    ...built,
    id,
    clips,
    uploaded_by: owner?.id ?? null,
    uploaded_by_detail: owner ? { id: owner.id, name: owner.name } : null,
    // Nothing was reserved in the mocked wallet for a project the backend created.
    cost: null,
    metadata,
    _reservations: [],
  };
  db.projects.push(project);
  return project;
}

/**
 * A project only the backend knows, asked from a mocked endpoint (its activity, a re-render…): a stand-in
 * with that id in the workspace. It is not a copy: the mocks never read the real project.
 */
export function adoptProject(db: MockDb, workspaceId: string, id: string | undefined): MockProject | null {
  if (!isHybrid() || !isUuid(id)) return null;
  // The same id in another mocked workspace is someone else's project: not adopted twice.
  if (db.projects.some((item) => item.id === id)) return null;
  return standIn(db, workspaceId, id, null);
}

/** A clip only the backend knows: a rendered stand-in with that id, inside a stand-in project of its own. */
export function adoptClip(db: MockDb, workspaceId: string, clipId: string | undefined): { project: MockProject; clip: MockClip } | null {
  if (!isHybrid() || !isUuid(clipId)) return null;
  if (db.projects.some((item) => item.clips.some((clip) => clip.id === clipId))) return null;
  const project = standIn(db, workspaceId, adoptedId('clipProject', clipId), clipId);
  const clip = project.clips[0];
  return clip ? { project, clip } : null;
}
