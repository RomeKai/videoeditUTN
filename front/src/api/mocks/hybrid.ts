import { API_URL, backendDefaultWorkspaceId, hasBackendDefaultPrefix } from '../config';
import { isHybrid } from '../routing';
import { newId, type MockClip, type MockDb, type MockProject, type MockUser, type MockWorkspace } from './db';
import { findScenario } from './scenarios';
import { buildClips, buildProject, PLAN_IDS, socialAccounts, USER_IDS } from './seed';

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
 * - The stand-in looks like the real thing as far as the mocks can tell: MSW shows them every answer the
 *   backend gave to this page (`noteRealAnswer`), and what those say of a workspace, a project or a clip
 *   (name, title, times, thumbnail) is what the stand-in shows.
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

/* ── What the real backend said ─────────────────────────────────────────── */

/** What a mocked screen shows of a real clip (Publicaciones: title, project, duration, thumbnail). */
export interface RealClip {
  id: string;
  project: string;
  title: string;
  start_time: number;
  end_time: number;
  thumbnail_url: string | null;
}

/**
 * What the answers of the real backend said, by id. Kept for this page only: a stand-in copies from here
 * when it is made, and the database (shared by every tab, kept across reloads) remembers what it copied.
 */
const real = {
  workspaces: new Map<string, string>(),
  projects: new Map<string, string>(),
  clips: new Map<string, RealClip>(),
};

/** For tests: a page that has seen no answer of the backend yet. */
export function forgetRealAnswers(): void {
  real.workspaces.clear();
  real.projects.clear();
  real.clips.clear();
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown): string | null => (typeof value === 'string' && value.trim() !== '' ? value : null);

function noteClip(value: unknown, projectId?: string): void {
  if (!isRecord(value) || !isUuid(text(value.id) ?? undefined)) return;
  const project = text(value.project) ?? projectId;
  const title = text(value.title);
  if (!project || !title || typeof value.start_time !== 'number' || typeof value.end_time !== 'number') return;
  const key = String(value.id).toLowerCase();
  real.clips.set(key, {
    id: String(value.id),
    project,
    title,
    start_time: value.start_time,
    end_time: value.end_time,
    // An answer that does not bring the field says nothing about it: what an earlier one said still holds.
    thumbnail_url: 'thumbnail_url' in value ? text(value.thumbnail_url) : (real.clips.get(key)?.thumbnail_url ?? null),
  });
}

function noteProject(value: unknown): void {
  if (!isRecord(value) || !isUuid(text(value.id) ?? undefined)) return;
  const title = text(value.title);
  if (title) real.projects.set(String(value.id).toLowerCase(), title);
  if (Array.isArray(value.clips)) for (const clip of value.clips) noteClip(clip, String(value.id));
}

function noteWorkspace(value: unknown): void {
  if (!isRecord(value)) return;
  const id = text(value.id);
  const name = text(value.name);
  if (id && name) real.workspaces.set(id.toLowerCase(), name);
}

/** A page (`{ results }`), a list or one row: each row. */
const rows = (body: unknown): unknown[] => (Array.isArray(body) ? body : isRecord(body) && Array.isArray(body.results) ? body.results : [body]);

/**
 * An answer the real backend gave to this page (MSW let the request through and shows the answer to the
 * mocks: `watchRealAnswers`). Read loosely, by path and shape and never against the contract: it is only
 * what a stand-in will show, and an answer the mocks do not understand is simply not used.
 */
export function noteRealAnswer(url: string, body: unknown): void {
  if (!isHybrid()) return;
  let path: string;
  try {
    path = new URL(url).pathname;
  } catch {
    return;
  }
  if (path === '/api/v1/me/') {
    if (isRecord(body) && Array.isArray(body.workspaces)) body.workspaces.forEach(noteWorkspace);
  } else if (path.startsWith('/api/v1/workspaces/')) {
    // The list and the detail; not what hangs below a workspace (its members, its invitations).
    if (/^\/api\/v1\/workspaces\/([^/]+\/)?$/.test(path)) rows(body).forEach(noteWorkspace);
  } else if (path.startsWith('/api/v1/projects/')) {
    rows(body).forEach(noteProject);
  } else if (path.startsWith('/api/v1/clips/')) {
    rows(body).forEach((row) => noteClip(row));
  }
}

/** The same, from the answer itself: only a JSON answer that went well says anything. */
export async function noteRealResponse(request: Request, response: Response): Promise<void> {
  if (!isHybrid() || !response.ok || !request.url.startsWith(`${API_URL}/api/`)) return;
  if (!(response.headers.get('Content-Type') ?? '').includes('json')) return;
  try {
    noteRealAnswer(request.url, await response.clone().json());
  } catch {
    // Not JSON after all: nothing to learn.
  }
}

interface AnswerEvents {
  on(event: 'response:bypass', listener: (args: { request: Request; response: Response }) => void): unknown;
}

/** Shows the mocks every answer MSW did not give itself (the browser's worker and the test server both tell). */
export function watchRealAnswers(events: AnswerEvents): void {
  events.on('response:bypass', ({ request, response }) => void noteRealResponse(request, response));
}

const realClip = (id: string | undefined): RealClip | undefined => (id ? real.clips.get(id.toLowerCase()) : undefined);
const realProjectTitle = (id: string | undefined): string | undefined => (id ? real.projects.get(id.toLowerCase()) : undefined);

/** What the stand-ins are called until the backend says a name. */
const PLACEHOLDER = {
  workspace: 'Workspace del backend',
  defaultWorkspace: 'Workspace por defecto (backend)',
  project: 'Proyecto del backend',
  clip: 'Clip del backend',
} as const;

/** The account name the mocked networks give a workspace when it connects (`completeMockSocialConnect`). */
const handleOf = (name: string): string => `@${name.toLowerCase().replace(/[^a-z0-9]+/g, '')}`;
const round2 = (value: number): number => Math.round(value * 100) / 100;

/** The stand-in clip takes what the backend said of the real one. */
function fillClip(clip: MockClip, from: RealClip): void {
  clip.title = from.title;
  clip.start_time = from.start_time;
  clip.end_time = from.end_time;
  clip.thumbnail_url = from.thumbnail_url;
}

/**
 * Brings the stand-ins up to date with what the backend said since they were made, before a mocked
 * endpoint answers. A stand-in that still has its placeholder name takes the real one (a workspace, and with
 * it the account name of its mocked networks; a project; a clip, with its times). The thumbnail always
 * follows the latest answer: its URL is temporary. A stand-in that already has a real title is left as it
 * is: a mocked endpoint may have edited it since.
 */
export function syncStandIns(db: MockDb): void {
  if (!isHybrid()) return;
  if (real.workspaces.size === 0 && real.projects.size === 0 && real.clips.size === 0) return;
  for (const workspace of db.workspaces) {
    const name = real.workspaces.get(workspace.id.toLowerCase());
    if (!name || workspace.name === name || (workspace.name !== PLACEHOLDER.workspace && workspace.name !== PLACEHOLDER.defaultWorkspace)) continue;
    const before = handleOf(workspace.name);
    workspace.name = name;
    for (const account of db.socialAccounts) if (account.workspace === workspace.id && account.username === before) account.username = handleOf(name);
  }
  for (const project of db.projects) {
    const title = realProjectTitle(project.id);
    if (title && project.title === PLACEHOLDER.project) project.title = title;
    for (const clip of project.clips) {
      const from = realClip(clip.id);
      if (!from) continue;
      const filled = clip.title === PLACEHOLDER.clip;
      if (filled) fillClip(clip, from);
      else if (from.thumbnail_url) clip.thumbnail_url = from.thumbnail_url;
      // The project made for this clip alone: it is the real clip's project in all but the id.
      const ofClip = realProjectTitle(from.project);
      if (ofClip && project.title === PLACEHOLDER.project && project.id === adoptedId('clipProject', clip.id)) project.title = ofClip;
      for (const post of db.posts) {
        if (post.video_clip !== clip.id) continue;
        if (post.clip.title === PLACEHOLDER.clip) post.clip.title = clip.title;
        if (post.clip.project_title === PLACEHOLDER.project) post.clip.project_title = project.title;
        if (filled) post.clip.duration_seconds = round2(clip.end_time - clip.start_time);
        post.clip.thumbnail_url = clip.thumbnail_url ?? null;
      }
    }
  }
}


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
      // The real name when the backend already said it (its `/me`), so the mocked screens call it the same.
      name: real.workspaces.get(id.toLowerCase()) ?? (hasBackendDefaultPrefix(id) ? PLACEHOLDER.defaultWorkspace : PLACEHOLDER.workspace),
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

/** A built clip turned into the stand-in of a real one: its id, and what the backend said of it if anything. */
function asStandIn(clip: MockClip, projectId: string, clipId: string): MockClip {
  const standIn: MockClip = { ...clip, id: clipId, project: projectId, title: PLACEHOLDER.clip, s3_object_key: `clips/${projectId}/${clipId}.mp4` };
  const from = realClip(clipId);
  if (from) fillClip(standIn, from);
  return standIn;
}

/** One more stand-in clip for a stand-in project that already exists (another clip of the same real project). */
function standInClip(projectId: string, clipId: string): MockClip {
  const [built] = buildClips(projectId, 9000, 600, { count: 1, statuses: ['completed'] }, Date.now());
  if (!built) throw new Error('buildClips no devolvió ningún clip');
  return asStandIn(built, projectId, clipId);
}

function standIn(db: MockDb, workspaceId: string, id: string, withClip: string | null): MockProject {
  const now = Date.now();
  const workspace = db.workspaces.find((item) => item.id === workspaceId);
  const owner = db.users.find((item) => item.id === workspace?.owner);
  const from = realClip(withClip ?? undefined);
  const built = buildProject(
    {
      // Fixed: every copy of the database builds the same stand-in.
      index: 9000,
      title: realProjectTitle(id) ?? realProjectTitle(from?.project) ?? PLACEHOLDER.project,
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
  const clips: MockClip[] = withClip ? built.clips.slice(0, 1).map((clip) => asStandIn(clip, id, withClip)) : [];
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
 * with that id in the workspace. It is not a copy: the mocks never ask the backend for the real project.
 * It has its title when the page already got it from the backend, «Proyecto del backend» until then.
 */
export function adoptProject(db: MockDb, workspaceId: string, id: string | undefined): MockProject | null {
  if (!isHybrid() || !isUuid(id)) return null;
  // The same id in another mocked workspace is someone else's project: not adopted twice.
  if (db.projects.some((item) => item.id === id)) return null;
  return standIn(db, workspaceId, id, null);
}

/**
 * A clip only the backend knows: a rendered stand-in with that id, inside a stand-in project of its own.
 * With what the page already got from the backend about the real clip (its title, its times, its thumbnail
 * and its project's title); «Clip del backend» in «Proyecto del backend» when it got nothing yet.
 */
export function adoptClip(db: MockDb, workspaceId: string, clipId: string | undefined): { project: MockProject; clip: MockClip } | null {
  if (!isHybrid() || !isUuid(clipId)) return null;
  if (db.projects.some((item) => item.clips.some((clip) => clip.id === clipId))) return null;
  // The real clip's project, when the backend already said which: the stand-in lives under that id, so what
  // the mocked screens link to (the project of a publication) is a page the backend has.
  const home = realClip(clipId)?.project;
  const known = isUuid(home) ? db.projects.find((item) => item.id === home) : undefined;
  if (known) {
    // The same id in another mocked workspace is someone else's project.
    if (known.workspace !== workspaceId) return null;
    const clip = standInClip(known.id, clipId);
    known.clips.push(clip);
    return { project: known, clip };
  }
  const project = standIn(db, workspaceId, isUuid(home) ? home : adoptedId('clipProject', clipId), clipId);
  const clip = project.clips[0];
  return clip ? { project, clip } : null;
}
