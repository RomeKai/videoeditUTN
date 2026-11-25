import type { ZodIssue } from 'zod';

/**
 * Error body shapes of the backend today:
 * - DRF validation: `{ "field": ["mensaje"], "non_field_errors": ["mensaje"] }`
 * - DRF auth/permission/not found: `{ "detail": "mensaje", "code"?: "token_not_valid" }`
 * - Hand-written views: `{ "error": "mensaje", "required"?: "117.00" }`
 * Proposed additions (all optional): `code` (machine-readable), `limit`, and
 * validation items as `{ "message", "code" }` so the UI does not match on texts.
 */
export interface ApiErrorData {
  detail?: unknown;
  error?: unknown;
  code?: unknown;
  required?: unknown;
  [key: string]: unknown;
}

function firstString(value: unknown): string | undefined {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    for (const item of value) {
      const text = firstString(item);
      if (text) return text;
    }
  }
  if (value && typeof value === 'object' && 'message' in value) return firstString((value as { message: unknown }).message);
  return undefined;
}

function firstCode(value: unknown): string | undefined {
  if (Array.isArray(value)) {
    for (const item of value) {
      const code = firstCode(item);
      if (code) return code;
    }
  }
  if (value && typeof value === 'object' && 'code' in value) {
    const code = (value as { code: unknown }).code;
    return typeof code === 'string' ? code : undefined;
  }
  return undefined;
}

/** Proposed machine-readable codes of a plan 403. */
export const PLAN_LIMIT_CODES = new Set(['duration', 'resolution', 'seo', 'scheduling', 'crossposting', 'seats_exceeded', 'plan_limit']);
/** What PricingEngine raises today (English, no code). */
export const PLAN_LIMIT_MESSAGE = /exceeds your plan limit|does not support .+ rendering|not allowed in your current plan/i;

const NON_FIELD_KEYS = new Set(['detail', 'error', 'code', 'required', 'limit', 'messages', 'non_field_errors']);

/** A non-2xx answer from the API. */
export class ApiError extends Error {
  readonly name = 'ApiError';
  readonly status: number;
  readonly endpointId: string;
  readonly data: ApiErrorData;
  /** Machine-readable code when the backend sends one (`code`, or the code of a field error). */
  readonly code: string | undefined;
  /** Messages per field, from a DRF validation error. */
  readonly fieldErrors: Record<string, string[]>;
  /** Codes per field (proposed `{message, code}` items), e.g. `{ source_file: 'too_long' }`. */
  readonly fieldCodes: Record<string, string>;

  constructor(status: number, endpointId: string, data: unknown) {
    const body: ApiErrorData = data && typeof data === 'object' && !Array.isArray(data) ? (data as ApiErrorData) : {};
    const fieldErrors: Record<string, string[]> = {};
    const fieldCodes: Record<string, string> = {};
    for (const [key, value] of Object.entries(body)) {
      if (NON_FIELD_KEYS.has(key)) continue;
      const list = Array.isArray(value) ? value : [value];
      const messages = list.map((item) => firstString(item)).filter((item): item is string => Boolean(item));
      if (messages.length > 0) fieldErrors[key] = messages;
      const code = firstCode(value);
      if (code) fieldCodes[key] = code;
    }
    const message =
      firstString(body.error) ??
      firstString(body.detail) ??
      firstString(body.non_field_errors) ??
      Object.values(fieldErrors)[0]?.[0] ??
      (typeof data === 'string' && data.length < 200 ? data : undefined) ??
      `Error ${status}`;
    super(message);
    this.status = status;
    this.endpointId = endpointId;
    this.data = body;
    this.code = typeof body.code === 'string' ? body.code : Object.values(fieldCodes)[0];
    this.fieldErrors = fieldErrors;
    this.fieldCodes = fieldCodes;
  }

  /** 402: not enough tokens. `required` is what the action costs. Opens dialog D2. */
  get isInsufficientFunds(): boolean {
    return this.status === 402;
  }

  /** Tokens the action needs, from a 402 body (`required` arrives as a decimal string). */
  get requiredTokens(): number | undefined {
    const raw = this.data.required;
    const value = typeof raw === 'number' ? raw : typeof raw === 'string' ? Number(raw) : NaN;
    return Number.isFinite(value) ? value : undefined;
  }

  /**
   * 403 because the plan does not allow it (opens dialog D3). Detected
   * positively: a plan `code`, or one of the messages PricingEngine raises
   * today. Any other 403 (DRF's PermissionDenied has no code) is a role 403.
   */
  get isPlanLimit(): boolean {
    if (this.status !== 403) return false;
    if (this.code && PLAN_LIMIT_CODES.has(this.code)) return true;
    return PLAN_LIMIT_MESSAGE.test(this.message);
  }

  get isConflict(): boolean {
    return this.status === 409;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

/** `cause` of the NetworkError of a request that waited longer than its timeout. */
export class RequestTimeoutError extends Error {
  readonly name = 'RequestTimeoutError';
  readonly timeoutMs: number;
  constructor(timeoutMs: number) {
    super(`Sin respuesta en ${timeoutMs} ms`);
    this.timeoutMs = timeoutMs;
  }
}

/** The request never got an answer (offline, DNS, CORS, aborted by the network, or it timed out). */
export class NetworkError extends Error {
  readonly name = 'NetworkError';
  readonly endpointId: string;
  /** HTTP method of the request, when it is known. */
  readonly method: string | undefined;
  constructor(endpointId: string, cause?: unknown, method?: string) {
    super('No pudimos conectar');
    this.endpointId = endpointId;
    this.cause = cause;
    this.method = method;
  }

  /** The request went out whole and the answer did not come in time. */
  get timedOut(): boolean {
    return this.cause instanceof RequestTimeoutError;
  }

  /**
   * A write that timed out: the server may have done it (created the project, reserved the
   * tokens) and only the answer was lost. The UI must not say "it failed, try again": it says it
   * may have been done, and reads again what it could have changed. A GET changes nothing, and a
   * request that never got out (offline, connection refused, stalled upload) did not arrive whole.
   */
  get outcomeUnknown(): boolean {
    return this.timedOut && this.method !== undefined && this.method !== 'GET';
  }
}

/** `cause` of the NetworkError of an upload that stopped sending. */
export class UploadStalledError extends Error {
  readonly name = 'UploadStalledError';
  constructor(stallMs: number) {
    super(`La subida no avanzó en ${stallMs} ms`);
  }
}

/** Whether the error is a write whose answer never came: it may have been done (see `NetworkError.outcomeUnknown`). */
export function isOutcomeUnknown(error: unknown): error is NetworkError {
  return error instanceof NetworkError && error.outcomeUnknown;
}

/** The answer (or the request) does not match the contract's zod schema. */
export class ContractError extends Error {
  readonly name = 'ContractError';
  readonly endpointId: string;
  readonly direction: 'request' | 'response' | 'query';
  readonly issues: ZodIssue[];
  constructor(endpointId: string, direction: 'request' | 'response' | 'query', issues: ZodIssue[]) {
    const first = issues[0];
    super(
      `Contrato roto en ${endpointId} (${direction}): ${first ? `${first.path.join('.') || '(raíz)'}: ${first.message}` : 'sin detalle'}`,
    );
    this.endpointId = endpointId;
    this.direction = direction;
    this.issues = issues;
  }
}

/** The refresh token is no longer valid: dialog D9 asks for the password again. */
export class SessionExpiredError extends Error {
  readonly name = 'SessionExpiredError';
  constructor() {
    super('Tu sesión venció');
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}
