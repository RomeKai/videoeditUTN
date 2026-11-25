import type { ZodTypeAny } from 'zod';
import { API_URL, REQUEST_TIMEOUT_MS, WORKSPACE_HEADER } from './config';
import { buildPath, buildQuery, type AnyEndpoint, type ParamsOf, type QueryOf, type RequestBody, type ResponseOf } from './endpoint';
import { ApiError, ContractError, NetworkError, RequestTimeoutError, SessionExpiredError } from './errors';
import {
  getActiveWorkspaceId,
  getTokens,
  isSessionExpired,
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

/**
 * Renews the access token with the refresh token. Concurrent 401s share one
 * request. The backend rotates refresh tokens (ROTATE_REFRESH_TOKENS), so the
 * new pair replaces the stored one.
 *
 * Only a 400 or 401 from the refresh endpoint means the session is over
 * (SessionExpiredError). A 5xx, a 429 or no answer is a passing problem: it is
 * thrown as ApiError / NetworkError and the session stays.
 */
export function refreshAccessToken(): Promise<string> {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    const tokens = getTokens();
    if (!tokens) throw new SessionExpiredError();
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
      throw new NetworkError('auth.refresh', cause);
    }
    if (response.status === 503) notifyMaintenance(true);
    if (!response.ok) {
      // Another tab may have renewed (and rotated) the pair while this one waited: use what it stored.
      const current = getTokens();
      if (current && current.refresh !== tokens.refresh) return current.access;
      if (response.status === 400 || response.status === 401) throw new SessionExpiredError();
      throw new ApiError(response.status, 'auth.refresh', await readBody(response, 'json'));
    }
    const data = (await response.json()) as { access?: unknown; refresh?: unknown };
    if (typeof data.access !== 'string') throw new SessionExpiredError();
    setTokens({ access: data.access, refresh: typeof data.refresh === 'string' ? data.refresh : tokens.refresh });
    return data.access;
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
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
 * progress: the creation form needs percentage and speed, map C1.)
 */
async function send(prepared: Prepared, { signal, onUploadProgress, timeoutMs }: SendOptions): Promise<Response> {
  const controller = new AbortController();
  let timedOut = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
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
    if (isUpload && typeof XMLHttpRequest !== 'undefined') return await sendXhr(prepared, controller.signal, onUploadProgress, startClock);
    startClock();
    const response = await fetch(prepared.url, { method: prepared.method, headers: prepared.headers, body: prepared.body, signal: controller.signal });
    // Headers are not the whole answer: a body that stalls halfway is a request that hangs too.
    // (Raced with the abort: not every body reader gives up when its request is aborted.)
    const body = NO_BODY_STATUS.includes(response.status) ? null : await Promise.race([response.arrayBuffer(), aborted(controller.signal)]);
    return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
  } catch (cause) {
    if (timedOut && timeoutMs !== false) throw new RequestTimeoutError(timeoutMs);
    throw cause;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }
}

function sendXhr(prepared: Prepared, signal: AbortSignal, onUploadProgress: SendOptions['onUploadProgress'], onUploaded: () => void): Promise<Response> {
  return new Promise<Response>((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    const xhr = new XMLHttpRequest();
    xhr.open(prepared.method, prepared.url);
    for (const [name, value] of Object.entries(prepared.headers)) xhr.setRequestHeader(name, value);
    xhr.responseType = 'text';
    xhr.upload.onprogress = (event) => {
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

  const workspaceId = endpoint.workspaceScoped === false ? null : getActiveWorkspaceId();
  if (sendsToken && workspaceId) headers[WORKSPACE_HEADER] = workspaceId;

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
      throw new NetworkError(endpoint.id, cause);
    }
  };

  // After the session expired nothing authenticated goes out until the user signs in again (dialog D9).
  if (needsAuth && isSessionExpired()) throw new SessionExpiredError();

  const expire = (): never => {
    notifySessionExpired();
    throw new SessionExpiredError();
  };
  const renew = async (): Promise<string | undefined> => {
    try {
      return await refreshAccessToken();
    } catch (error) {
      if (!(error instanceof SessionExpiredError)) throw error;
      // A public endpoint that only sends the token when there is one goes on without it.
      if (!needsAuth) return undefined;
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
    // Another tab may already have renewed the pair: try its token before spending a refresh.
    const stored = getTokens()?.access;
    const next = stored && stored !== sent ? stored : await renew();
    response = await attempt(next);
    if (response.status === 401 && needsAuth) expire();
  }

  // 503: the platform is down for maintenance (page K5). Any other answer means it is back.
  notifyMaintenance(response.status === 503);

  if (!response.ok) {
    throw new ApiError(response.status, endpoint.id, await readBody(response, 'json'));
  }

  const data = await readBody(response, endpoint.responseType ?? 'json');
  const parsed = endpoint.response.safeParse(data);
  if (!parsed.success) throw new ContractError(endpoint.id, 'response', parsed.error.issues);
  return parsed.data as ResponseOf<E>;
}
