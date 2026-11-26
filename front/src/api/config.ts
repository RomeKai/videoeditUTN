/** Base URL of the Django backend, without a trailing slash. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/+$/, '');

export type ApiMode = 'mock' | 'hybrid' | 'real';

/**
 * Who answers the API:
 * - `mock`: MSW answers every request in the browser;
 * - `real`: the app talks to the Django backend for everything (no MSW worker);
 * - `hybrid`: MSW runs, but lets through to the backend the endpoints routed as real
 *   (`src/api/routing.ts`) and mocks the rest.
 * `NEXT_PUBLIC_API_MODE` decides; without it, the older `NEXT_PUBLIC_API_MOCKS` does (`true` → mock,
 * anything else → real). Both are fixed when the app is built (`env` in next.config.ts): what the
 * environment says when the server starts changes nothing.
 */
export function resolveApiMode(mode: string | undefined, mocks: string | undefined): ApiMode {
  const value = mode?.trim().toLowerCase();
  if (value === 'mock' || value === 'hybrid' || value === 'real') return value;
  return mocks === 'true' ? 'mock' : 'real';
}

export const API_MODE: ApiMode = resolveApiMode(process.env.NEXT_PUBLIC_API_MODE, process.env.NEXT_PUBLIC_API_MOCKS);

/** True: the MSW worker runs in the browser (it answers everything in `mock`, only the mocked part in `hybrid`). */
export const API_MOCKS = API_MODE !== 'real';

/**
 * Hybrid mode: what goes to the real backend. Comma-separated contract domains (`projects`, `brandkits`…),
 * endpoint ids (`billing.plans`) or the word `existing` (what the contract marks `exists` or `partial`).
 * Empty: `existing`.
 */
export const API_REAL_LIST = process.env.NEXT_PUBLIC_API_REAL ?? '';

/** Header that carries the active workspace on every request (proposed, map J3 / gap 2). */
export const WORKSPACE_HEADER = 'X-Workspace-Id';

/**
 * DEV BRIDGE (hybrid mode only). While `/me` is still mocked, the mocks cannot know the ids of the user's
 * real workspaces, so the mocked `/me` shows one placeholder workspace. Its id starts with this prefix
 * (the rest comes from the user's id, so two real users in one browser do not share mocked data). The
 * HTTP client sends no `X-Workspace-Id` for it, and the backend falls back to the user's default
 * workspace (`back/apps/core/workspace.py · _default_workspace`). It goes away when `/me` is real.
 */
export const BACKEND_DEFAULT_WORKSPACE_PREFIX = '00000000-0000-4000-8000-';

/** The placeholder workspace of a real user: a valid UUID that no backend and no seeded mock row has. */
export function backendDefaultWorkspaceId(userId: string): string {
  const hex = userId.toLowerCase().replace(/[^0-9a-f]/g, '');
  return `${BACKEND_DEFAULT_WORKSPACE_PREFIX}${hex.slice(-12).padStart(12, '0')}`;
}

/** Whether the id has the shape of a placeholder. What to do about it depends on the mode: see `src/api/routing.ts`. */
export function hasBackendDefaultPrefix(workspaceId: string | null | undefined): boolean {
  return Boolean(workspaceId?.startsWith(BACKEND_DEFAULT_WORKSPACE_PREFIX));
}

/**
 * How long a request may go without an answer before it counts as a network error. Without it, a
 * request that hangs keeps whatever waits on it (a disabled button, a spinner) waiting forever.
 * An endpoint can set its own with `timeoutMs`.
 */
export const REQUEST_TIMEOUT_MS = 30_000;

/**
 * For the endpoints where the server keeps working after the request is in: creating a project
 * (it checks the video, moves it to storage, prices it and reserves the tokens), saving a brand
 * kit's files, building a zip. Cutting those short would report a failure for something that was
 * done, and a retry would do it (and charge it) twice.
 */
export const SLOW_REQUEST_TIMEOUT_MS = 180_000;

/** An upload that sends nothing for this long is stalled: it is cut, as a network error. */
export const UPLOAD_STALL_TIMEOUT_MS = 60_000;

/**
 * Header of a write that must not be done twice (proposed, for publishing): the client sends one
 * key per submit and repeats it when it retries; the server answers a known key with the first result.
 */
export const IDEMPOTENCY_HEADER = 'Idempotency-Key';

/** Screens with something in progress poll every 5 s (map 1.4). */
export const POLL_INTERVAL_MS = 5000;
