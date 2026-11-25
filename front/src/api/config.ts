/** Base URL of the Django backend, without a trailing slash. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/+$/, '');

/** True: MSW answers every request in the browser. False: the app talks to the real backend. */
export const API_MOCKS = process.env.NEXT_PUBLIC_API_MOCKS === 'true';

/** Header that carries the active workspace on every request (proposed, map J3 / gap 2). */
export const WORKSPACE_HEADER = 'X-Workspace-Id';

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
