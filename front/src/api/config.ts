/** Base URL of the Django backend, without a trailing slash. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/+$/, '');

/** True: MSW answers every request in the browser. False: the app talks to the real backend. */
export const API_MOCKS = process.env.NEXT_PUBLIC_API_MOCKS === 'true';

/** Header that carries the active workspace on every request (proposed, map J3 / gap 2). */
export const WORKSPACE_HEADER = 'X-Workspace-Id';

/** Screens with something in progress poll every 5 s (map 1.4). */
export const POLL_INTERVAL_MS = 5000;
