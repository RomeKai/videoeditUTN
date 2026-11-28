import type { ZodTypeAny } from 'zod';
import { API_URL, IDEMPOTENCY_HEADER, REQUEST_TIMEOUT_MS, UPLOAD_STALL_TIMEOUT_MS, WORKSPACE_HEADER } from './config';
import { buildPath, buildQuery, type AnyEndpoint, type ParamsOf, type QueryOf, type RequestBody, type ResponseOf } from './endpoint';
import { isBackendDefaultWorkspace } from './routing';
import { ApiError, ContractError, NetworkError, RequestTimeoutError, SESSION_REVOKED_CODE, SessionExpiredError, UploadStalledError, type SessionEndReason } from './errors';
import {
  getActiveWorkspaceId,
  getTokenGeneration,
  getTokens,
  isSessionExpired,
  noteRefreshRejected,
  notifyMaintenance,
  notifySessionExpired,
  setTokens,
} from './session';

export interface CallOptions<E extends AnyEndpoint> {
  params?: ParamsOf<E>;
  query?: QueryOf<E>;
  /** JSON body, or the fields of a multipart form (Files and Blobs are sent as files). */
  body?: RequestBody<E>;
  signal?: AbortSignal;
  /** Upload progress (multipart only): bytes sent and total. Uses XMLHttpRequest under the hood. */
  onUploadProgress?: (loaded: number, total: number) => void;
  /** Overrides the endpoint's timeout for this call (ms, or `false` for none). */
  timeoutMs?: number | false;
  /**
   * The workspace this call is about, when it is not (or may no longer be) the active one: cleaning
   * up after a screen that is being left while the workspace changes. Default: the active workspace.
   */
  workspaceId?: string;
  /**
   * `Idempotency-Key` for a write that must not happen twice (publishing): one per submit, the same
   * on every retry of it. The server answers a repeated key with what the first request did.
   */
  idempotencyKey?: string;
}

type Args<E extends AnyEndpoint> =
  ParamsOf<E> extends undefined
    ? RequestBody<E> extends undefined
      ? [options?: CallOptions<E>]
      : [options: CallOptions<E> & { body: RequestBody<E> }]
    : RequestBody<E> extends undefined
      ? [options: CallOptions<E> & { params: ParamsOf<E> }]
      : [options: CallOptions<E> & { params: ParamsOf<E>; body: RequestBody<E> }];

const REFRESH_PATH = '/api/token/refresh/';

/** Object → FormData the way DRF's MultiPartParser expects it. */
export function toFormData(fields: Record<string, unknown>): FormData {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined || value === null) continue;
    if (typeof Blob !== 'undefined' && value instanceof Blob) form.append(key, value);
    else if (typeof value === 'boolean') form.append(key, value ? 'true' : 'false');
    else if (Array.isArray(value)) for (const item of value) form.append(key, String(item));
    else form.append(key, String(value));
  }
  return form;
}

let refreshing: Promise<string> | null = null;

/** Name of the Web Lock every tab of the app takes to renew the pair: one renewal at a time per browser. */
export const REFRESH_LOCK_NAME = 'oc.auth.refresh';
/**
 * How long a renewal that was refused waits for the pair another tab may be about to store: its
 * request reached the server first and its answer is on the way (a renewal without Web Locks, or
 * the new pair of a password change, which takes no lock).
 */
export const REFRESH_FALLBACK_WAIT_MS = 500;
/** How long a tab waits for the renewal lock: what the holder's request can take, plus a margin. */
export const REFRESH_LOCK_WAIT_MS = REQUEST_TIMEOUT_MS + 5_000;
const REFRESH_FALLBACK_POLL_MS = 50;

function webLocks(): LockManager | undefined {
  const locks = typeof navigator === 'undefined' ? undefined : (navigator as Navigator & { locks?: LockManager }).locks;
  return locks && typeof locks.request === 'function' ? locks : undefined;
}

/**
 * Runs `run` while holding the renewal lock, so two tabs never send the same refresh token (the
 * backend rotates and blacklists it: the second would get a 401 and its tab the "Sesión vencida"
 * dialog for nothing). Where Web Locks do not exist, cannot be asked for, or the lock does not
 * come in `REFRESH_LOCK_WAIT_MS`, it runs without one.
 */
async function withRefreshLock<T>(run: () => Promise<T>): Promise<T> {
  const locks = webLocks();
  if (!locks) return run();
  let entered = false;
  // A tab that was suspended while holding the lock must not hang the others: past the longest a
  // renewal can take, the wait is given up and this tab renews without the lock.
  const signal = typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(REFRESH_LOCK_WAIT_MS) : undefined;
  try {
    return await locks.request(REFRESH_LOCK_NAME, signal ? { signal } : {}, () => {
      entered = true;
      return run();
    });
  } catch (error) {
    if (entered) throw error;
    // The lock itself failed (blocked by the browser, or the wait timed out): renew without it.
    return run();
  }
}

/** The pair another tab stored instead of `mine`, waiting up to `waitMs` for it. Null: nobody replaced it. */
async function pairFromElsewhere(mine: string, waitMs: number): Promise<{ access: string } | null> {
  const deadline = Date.now() + waitMs;
  for (;;) {
    const current = getTokens();
    if (current && current.refresh !== mine) return current;
    if (!current || Date.now() >= deadline) return null;
    await new Promise((resolve) => setTimeout(resolve, REFRESH_FALLBACK_POLL_MS));
  }
}

/**
 * Renews the access token with the refresh token. Concurrent 401s share one
 * request, and so do the tabs of the browser (`withRefreshLock`). The backend
 * rotates refresh tokens (ROTATE_REFRESH_TOKENS) and blacklists the one it
 * replaced (BLACKLIST_AFTER_ROTATION), so the new pair replaces the stored one.
 *
 * The stored pair is read again once the lock is held: if another tab renewed
 * while this one waited, its pair is used and no request goes out.
 *
 * A pair stored while the request is in flight (a sign in, the new pair of a
 * password change, a renewal of another tab) wins: the answer of this renewal
 * is dropped, never written over it (`getTokenGeneration`). The server already
 * revoked what this renewal would bring.
 *
 * Only a 400 or 401 from the refresh endpoint means the session is over
 * (SessionExpiredError). A 5xx, a 429 or no answer is a passing problem: it is
 * thrown as ApiError / NetworkError and the session stays.
 */
export function refreshAccessToken(): Promise<string> {
  if (refreshing) return refreshing;
  const initial = getTokens();
  refreshing = withRefreshLock(async () => {
    const tokens = getTokens();
    if (!tokens || !initial) throw new SessionExpiredError();
    // Another tab renewed while this one waited for the lock: its pair is the current one.
    if (tokens.refresh !== initial.refresh) return tokens.access;
    const generation = getTokenGeneration();
    /**
     * The pair that replaced the one this renewal started with, if any: this renewal's answer is
     * void. Replaced means this tab stored a pair meanwhile (the generation moved) or the stored
     * refresh token is another one (another tab did).
     */
    const replacedBy = () => {
      const current = getTokens();
      if (getTokenGeneration() === generation && current && current.refresh === tokens.refresh) return null;
      return { current };
    };
    let response: Response;
    try {
      response = await send(
        {
          url: `${API_URL}${REFRESH_PATH}`,
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ refresh: tokens.refresh }),
        },
        { timeoutMs: REQUEST_TIMEOUT_MS },
      );
    } catch (cause) {
      const replaced = replacedBy();
      if (replaced?.current) return replaced.current.access;
      throw new NetworkError('auth.refresh', cause, 'POST');
    }
    if (await isMaintenance(response)) notifyMaintenance(true);
    const replaced = replacedBy();
    if (replaced) {
      // Signed out meanwhile: nothing to renew, and nothing to bring back.
      if (!replaced.current) throw new SessionExpiredError();
      return replaced.current.access;
    }
    if (!response.ok) {
      if (response.status === 400 || response.status === 401) {
        // Another tab may have got to the server first (the same refresh token without the lock, or a
        // password change that revoked it): its pair is about to be stored.
        const other = await pairFromElsewhere(tokens.refresh, REFRESH_FALLBACK_WAIT_MS);
        if (other) return other.access;
        // This pair, and no other, is the dead one (a public tab must not give up one another tab just renewed).
        noteRefreshRejected(tokens.refresh);
        throw new SessionExpiredError(await endReason(response));
      }
      throw new ApiError(response.status, 'auth.refresh', await readBody(response, 'json'));
    }
    const data = (await response.json()) as { access?: unknown; refresh?: unknown };
    if (typeof data.access !== 'string') throw new SessionExpiredError();
    setTokens({ access: data.access, refresh: typeof data.refresh === 'string' ? data.refresh : tokens.refresh });
    return data.access;
  }).finally(() => {
    refreshing = null;
  });
  return refreshing;
}

/** Why a 401 ended the session, from its `code`. The body is read from a copy: the answer stays whole. */
async function endReason(response: Response): Promise<SessionEndReason> {
  try {
    const data: unknown = await response.clone().json();
    return data && typeof data === 'object' && (data as { code?: unknown }).code === SESSION_REVOKED_CODE ? 'revoked' : 'expired';
  } catch {
    return 'expired';
  }
}

/**
 * Whether a 503 means the platform is down (page K5) and not that this one request failed.
 *
 * The maintenance middleware answers `{ detail }` and nothing else (back/apps/core/maintenance.py), and
 * a proxy in front of a backend that is down answers a 503 that is not even JSON. An endpoint that
 * fails on its own says so the way every error of the API does, with `error` and usually a `code`
 * (a provider that did not answer, storage that is down, a queue that did not take the job): that is
 * an error of that request, shown where it was made, and the rest of the app keeps working.
 * The body is read from a copy: the answer stays whole.
 */
async function isMaintenance(response: Response): Promise<boolean> {
  if (response.status !== 503) return false;
  try {
    const data: unknown = await response.clone().json();
    if (!data || typeof data !== 'object' || Array.isArray(data)) return true;
    const body = data as { code?: unknown; error?: unknown };
    return typeof body.code !== 'string' && typeof body.error !== 'string';
  } catch {
    return true;
  }
}

async function readBody(response: Response, type: 'json' | 'text' | 'blob'): Promise<unknown> {
  if (response.status === 204 || response.status === 205) return undefined;
  if (type === 'blob') return response.blob();
  const text = await response.text();
  if (type === 'text') return text;
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

interface Prepared {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: BodyInit | undefined;
}

interface SendOptions {
  signal?: AbortSignal;
  onUploadProgress?: CallOptions<AnyEndpoint>['onUploadProgress'];
  timeoutMs: number | false;
}

const NO_BODY_STATUS = [204, 205, 304];

/** Rejects, as fetch does, when the signal aborts. */
function aborted(signal: AbortSignal): Promise<never> {
  return new Promise((_resolve, reject) => {
    const fail = () => reject(new DOMException('Aborted', 'AbortError'));
    if (signal.aborted) fail();
    else signal.addEventListener('abort', fail, { once: true });
  });
}

/**
 * Sends the request and waits for the whole answer, body included, for at most `timeoutMs`.
 * Past that it is aborted and fails with RequestTimeoutError (a network error for the caller).
 *
 * A multipart body goes by XMLHttpRequest, which says when the upload ends: the clock only starts
 * there, so a big file on a slow connection is never cut short. (fetch cannot tell, nor report
 * progress: the creation form needs percentage and speed, map C1.) While it uploads, what is
 * watched is that it keeps moving: `UPLOAD_STALL_TIMEOUT_MS` without sending anything cuts it
 * (UploadStalledError).
 */
async function send(prepared: Prepared, { signal, onUploadProgress, timeoutMs }: SendOptions): Promise<Response> {
  const controller = new AbortController();
  let timedOut = false;
  let stalled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stallTimer: ReturnType<typeof setTimeout> | undefined;
  /** Called when the upload starts and every time it moves: the next sign of life is due within the stall limit. */
  const stillUploading = () => {
    clearTimeout(stallTimer);
    stallTimer = setTimeout(() => {
      stalled = true;
      controller.abort();
    }, UPLOAD_STALL_TIMEOUT_MS);
  };
  const startClock = () => {
    if (timeoutMs === false || timer !== undefined) return;
    timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
  };
  const onAbort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  else signal?.addEventListener('abort', onAbort);
  const isUpload = onUploadProgress !== undefined || (typeof FormData !== 'undefined' && prepared.body instanceof FormData);
  try {
    if (isUpload && typeof XMLHttpRequest !== 'undefined') {
      return await sendXhr(prepared, controller.signal, onUploadProgress, stillUploading, () => {
        clearTimeout(stallTimer);
        startClock();
      });
    }
    startClock();
    const response = await fetch(prepared.url, { method: prepared.method, headers: prepared.headers, body: prepared.body, signal: controller.signal });
    // Headers are not the whole answer: a body that stalls halfway is a request that hangs too.
    // (Raced with the abort: not every body reader gives up when its request is aborted.)
    const body = NO_BODY_STATUS.includes(response.status) ? null : await Promise.race([response.arrayBuffer(), aborted(controller.signal)]);
    return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
  } catch (cause) {
    if (timedOut && timeoutMs !== false) throw new RequestTimeoutError(timeoutMs);
    if (stalled) throw new UploadStalledError(UPLOAD_STALL_TIMEOUT_MS);
    throw cause;
  } finally {
    clearTimeout(timer);
    clearTimeout(stallTimer);
    signal?.removeEventListener('abort', onAbort);
  }
}

function sendXhr(
  prepared: Prepared,
  signal: AbortSignal,
  onUploadProgress: SendOptions['onUploadProgress'],
  onUploading: () => void,
  onUploaded: () => void,
): Promise<Response> {
  return new Promise<Response>((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    const xhr = new XMLHttpRequest();
    xhr.open(prepared.method, prepared.url);
    for (const [name, value] of Object.entries(prepared.headers)) xhr.setRequestHeader(name, value);
    xhr.responseType = 'text';
    xhr.upload.onloadstart = onUploading;
    xhr.upload.onprogress = (event) => {
      onUploading();
      if (event.lengthComputable) onUploadProgress?.(event.loaded, event.total);
    };
    // The body is out: from here on it is a request waiting for its answer, like any other.
    xhr.upload.onload = onUploaded;
    xhr.onload = () =>
      resolve(
        new Response(NO_BODY_STATUS.includes(xhr.status) ? null : (xhr.responseText as string), {
          status: xhr.status,
          headers: { 'Content-Type': xhr.getResponseHeader('Content-Type') ?? 'application/json' },
        }),
      );
    xhr.onerror = () => reject(new TypeError('Network request failed'));
    xhr.onabort = () => reject(new DOMException('Aborted', 'AbortError'));
    signal.addEventListener('abort', () => xhr.abort());
    xhr.send(prepared.body as XMLHttpRequestBodyInit | undefined);
  });
}

/**
 * The single HTTP entry point. Given an endpoint of the contract it:
 * - validates query and body against their zod schemas,
 * - adds `Authorization: Bearer <access>` and the active workspace header,
 * - on 401 refreshes the token once and repeats the request,
 * - throws ApiError for non-2xx, NetworkError when there is no answer (or none within the timeout:
 *   `REQUEST_TIMEOUT_MS`, or the endpoint's `timeoutMs`),
 * - parses the answer with the endpoint's schema (ContractError if it does not match).
 * Screens never call it directly: they use the hooks in `src/api/hooks`.
 */
export async function apiCall<E extends AnyEndpoint>(endpoint: E, ...args: Args<E>): Promise<ResponseOf<E>> {
  const options = (args[0] ?? {}) as CallOptions<AnyEndpoint>;
  const needsAuth = endpoint.auth === undefined || endpoint.auth === true;
  const sendsToken = endpoint.auth !== false;

  let query: Record<string, unknown> | undefined = options.query as Record<string, unknown> | undefined;
  if (endpoint.query && query !== undefined) {
    const parsed = (endpoint.query as ZodTypeAny).safeParse(query);
    if (!parsed.success) throw new ContractError(endpoint.id, 'query', parsed.error.issues);
    query = parsed.data as Record<string, unknown>;
  }

  let body: BodyInit | undefined;
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (endpoint.request && options.body !== undefined) {
    const parsed = (endpoint.request as ZodTypeAny).safeParse(options.body);
    if (!parsed.success) throw new ContractError(endpoint.id, 'request', parsed.error.issues);
    if (endpoint.bodyType === 'multipart') {
      // The browser sets the multipart boundary itself.
      body = toFormData(parsed.data as Record<string, unknown>);
    } else {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(parsed.data);
    }
  }

  const workspaceId = endpoint.workspaceScoped === false ? null : (options.workspaceId ?? getActiveWorkspaceId());
  // The placeholder of hybrid mode names no workspace: without the header the backend uses the user's default one.
  if (sendsToken && workspaceId && !isBackendDefaultWorkspace(workspaceId)) headers[WORKSPACE_HEADER] = workspaceId;
  if (options.idempotencyKey) headers[IDEMPOTENCY_HEADER] = options.idempotencyKey;

  const url = `${API_URL}${buildPath(endpoint.path, options.params as Record<string, string> | undefined)}${buildQuery(query)}`;

  const timeoutMs = options.timeoutMs ?? endpoint.timeoutMs ?? REQUEST_TIMEOUT_MS;

  const attempt = async (accessToken: string | undefined): Promise<Response> => {
    const requestHeaders = { ...headers };
    if (accessToken) requestHeaders.Authorization = `Bearer ${accessToken}`;
    try {
      return await send({ url, method: endpoint.method, headers: requestHeaders, body }, { signal: options.signal, onUploadProgress: options.onUploadProgress, timeoutMs });
    } catch (cause) {
      // Cancelled by the caller: not an error of the network. (A timeout is, and comes as RequestTimeoutError.)
      if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
      throw new NetworkError(endpoint.id, cause, endpoint.method);
    }
  };

  // After the session expired nothing authenticated goes out until the user signs in again (dialog D9).
  if (needsAuth && isSessionExpired()) throw new SessionExpiredError();

  // «Cerrar sesión en todos los dispositivos» from another device: the access token answers 401
  // `session_revoked`, and so does the refresh (blacklisted by then), which is the first to answer
  // when the access token had already expired. A refresh that is simply not valid says
  // `token_not_valid`. Whichever answer names `session_revoked` decides what dialog D9 says.
  let reason: SessionEndReason = 'expired';
  const expire = (): never => {
    notifySessionExpired(reason);
    throw new SessionExpiredError(reason);
  };
  const renew = async (): Promise<string | undefined> => {
    try {
      return await refreshAccessToken();
    } catch (error) {
      if (!(error instanceof SessionExpiredError)) throw error;
      // A public endpoint that only sends the token when there is one goes on without it.
      if (!needsAuth) return undefined;
      if (error.reason === 'revoked') reason = 'revoked';
      return expire();
    }
  };

  let sent = sendsToken ? getTokens()?.access : undefined;
  if (sent && (endpoint.bodyType === 'multipart' || options.onUploadProgress)) {
    // An upload can take longer than the token lives and must not die halfway with a 401:
    // it always starts with a fresh one. If the renewal fails for a passing reason, it goes with what there is.
    try {
      sent = await renew();
    } catch (error) {
      if (error instanceof SessionExpiredError) throw error;
    }
  }
  let response = await attempt(sent);

  if (response.status === 401 && sendsToken && (needsAuth || sent)) {
    reason = await endReason(response);
    // Another tab may already have renewed the pair: try its token before spending a refresh.
    const stored = getTokens()?.access;
    const next = stored && stored !== sent ? stored : await renew();
    response = await attempt(next);
    if (response.status === 401 && needsAuth) {
      if ((await endReason(response)) === 'revoked') reason = 'revoked';
      expire();
    }
  }

  // A 503 that is not an error of the endpoint itself: the platform is down for maintenance (page K5).
  // Any other answer means it is back.
  notifyMaintenance(await isMaintenance(response));

  if (!response.ok) {
    throw new ApiError(response.status, endpoint.id, await readBody(response, 'json'));
  }

  const data = await readBody(response, endpoint.responseType ?? 'json');
  const parsed = endpoint.response.safeParse(data);
  if (!parsed.success) throw new ContractError(endpoint.id, 'response', parsed.error.issues);
  return parsed.data as ResponseOf<E>;
}
