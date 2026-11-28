'use client';

import { ApiError, NetworkError } from '@/api/errors';
import { retryAfterSeconds } from '@/lib/cooldown';

// The wait between two emails lives in `@/lib/cooldown`: the dialogs of the app use it too.
export { formatCountdown, retryAfterSeconds, startCooldown, useCooldown } from '@/lib/cooldown';

/** Where the app sends a signed-in user that asked for nothing in particular. */
export const HOME = '/proyectos';

/**
 * The page the user was going to before being sent to sign in (`?siguiente=`).
 * Only paths inside the app are accepted: an absolute or protocol-relative URL
 * would let a link send people to another site after signing in.
 */
export function safeNext(next: string | null | undefined, { verifyLink = false }: { verifyLink?: boolean } = {}): string | null {
  if (!next || !next.startsWith('/') || next.startsWith('//')) return null;
  // The URL parser drops tabs and line breaks and reads "\" as "/": "/<TAB>/otro.sitio" would become "//otro.sitio".
  if (/[\u0000-\u001F\u007F\s\\]/.test(next)) return null;
  let url: URL;
  try {
    url = new URL(next, PARSE_ORIGIN);
  } catch {
    return null;
  }
  if (url.origin !== PARSE_ORIGIN) return null;
  // "/.//otro.sitio" normalises to "//otro.sitio", which a browser reads as another host.
  if (url.pathname.startsWith('//')) return null;
  // `verifyLink`: the login may go back to the verification link it interrupted (A3 answers
  // `login_required`, or the link of a new account was opened outside the browser that signed up); a link
  // of another account only gets a 403 there, never its session. The token is not in the path: A3 keeps
  // it for this tab (`rememberVerifyLink`). A path that still carries it (`?token=`) comes from a page
  // loaded before the token moved to the fragment.
  const backToLink = verifyLink && url.pathname === '/verificar' && (url.search === '' || Boolean(url.searchParams.get('token'))) && url.hash === '';
  if (!backToLink && /^\/(login|registro|verificar|recuperar)(\/|$)/.test(url.pathname)) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}

const PARSE_ORIGIN = 'http://app.invalid';
const NEXT_KEY = 'oc.acceso.siguiente';

/** How long a remembered destination is worth following: the verification email is opened soon or not at all. */
export const NEXT_MAX_AGE_MS = 24 * 60 * 60 * 1000;

interface StoredNext {
  path: string;
  email: string;
  at: number;
}

const normalEmail = (email: string | null | undefined) => (email ?? '').trim().toLowerCase();

function forgetNext(): void {
  try {
    window.localStorage.removeItem(NEXT_KEY);
  } catch {
    // Nothing stored, nothing to forget.
  }
}

/**
 * Remembers where the user was going while the email gets verified (A1 → A3 → link of the email).
 * The link usually opens in another tab, hence localStorage. It belongs to one account: opening A3
 * without a destination (or without knowing whose it is) forgets whatever an earlier visit left.
 */
export function rememberNext(next: string | null | undefined, email: string | null | undefined): void {
  const path = safeNext(next);
  const owner = normalEmail(email);
  if (!path || !owner) {
    forgetNext();
    return;
  }
  try {
    const value: StoredNext = { path, email: owner, at: Date.now() };
    window.localStorage.setItem(NEXT_KEY, JSON.stringify(value));
  } catch {
    // Private mode: the verification ends in the welcome, as usual.
  }
}

/**
 * Reads the page remembered by `rememberNext` and always forgets it. It is only returned to the
 * account it was stored for, and only while it is recent: someone else verifying later in the same
 * browser goes through the welcome, not to a page another person asked for.
 */
export function takeNext(email: string | null | undefined): string | null {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(NEXT_KEY);
  } catch {
    return null;
  }
  forgetNext();
  if (!raw) return null;
  try {
    const stored = JSON.parse(raw) as Partial<StoredNext> | null;
    if (!stored || typeof stored.path !== 'string' || typeof stored.email !== 'string' || typeof stored.at !== 'number') return null;
    const age = Date.now() - stored.at;
    if (age < 0 || age > NEXT_MAX_AGE_MS) return null;
    if (!normalEmail(email) || stored.email !== normalEmail(email)) return null;
    return safeNext(stored.path);
  } catch {
    return null;
  }
}

const SIGNUP_KEY = 'oc.acceso.registro';

/**
 * The `signup_nonce` of the sign-up (A2): the link of the email signs in only in the browser that
 * has it (A3); anywhere else it only verifies. localStorage, not sessionStorage: the link usually
 * opens in another tab of this same browser. Kept as long as the link lasts (24 h), and for the
 * latest sign-up only.
 */
export function rememberSignupNonce(nonce: string): void {
  try {
    window.localStorage.setItem(SIGNUP_KEY, JSON.stringify({ nonce, at: Date.now() }));
  } catch {
    // Private mode: the link verifies and the person signs in with the password.
  }
}

/** The nonce of the latest sign-up while it lasts. An expired or broken entry is deleted, not just ignored. */
export function readSignupNonce(): string | undefined {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(SIGNUP_KEY);
  } catch {
    return undefined;
  }
  if (raw === null) return undefined;
  try {
    const stored = JSON.parse(raw) as { nonce?: unknown; at?: unknown } | null;
    if (stored && typeof stored.nonce === 'string' && stored.nonce && typeof stored.at === 'number') {
      const age = Date.now() - stored.at;
      if (age >= 0 && age <= NEXT_MAX_AGE_MS) return stored.nonce;
    }
  } catch {
    // Not JSON: nothing to keep.
  }
  forgetSignupNonce();
  return undefined;
}

export function forgetSignupNonce(): void {
  try {
    window.localStorage.removeItem(SIGNUP_KEY);
  } catch {
    // Nothing stored.
  }
}

/**
 * The password reset link (A4) carries its token and email in the fragment (`/recuperar#token=…&email=…`):
 * a browser never sends it, so it stays out of the web host's logs. Reads it and drops it from the
 * address bar (and from the history), so it is not left behind either.
 */
export function takeResetLink(): { token: string; email: string } | null {
  const hash = window.location.hash.replace(/^#/, '');
  if (!hash) return null;
  const params = new URLSearchParams(hash);
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
  const token = params.get('token');
  return token ? { token, email: params.get('email') ?? '' } : null;
}

const VERIFY_KEY = 'oc.acceso.verificar';

/** The link of a verification email: its token and, in an email change, where to go after (`siguiente`). */
export interface VerifyLink {
  token: string;
  next: string | null;
}

/** Where the login sends the person back to finish a verification. The token is not in it: it waits in sessionStorage. */
export const VERIFY_ROUTE = '/verificar';

/**
 * The verification link (A3) carries its token in the fragment (`/verificar#token=…&siguiente=…`), like the
 * reset and invitation links: a browser never sends it, so it stays out of the web host's logs, and the API
 * gets it in a POST body. Reads it and drops it from the address bar (and from the history).
 */
export function takeVerifyLink(): VerifyLink | null {
  const hash = window.location.hash.replace(/^#/, '');
  if (!hash) return null;
  const params = new URLSearchParams(hash);
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
  const token = params.get('token');
  return token ? { token, next: params.get('siguiente') } : null;
}

/**
 * A link sent before the token moved to the fragment has it in the query (`/verificar?token=…`): it works
 * until it expires (24 h). Drops it from the address bar (and from the history) once the page read it; the
 * rest of the query stays.
 */
export function dropQueryToken(): void {
  const params = new URLSearchParams(window.location.search);
  if (!params.has('token')) return;
  params.delete('token');
  const query = params.toString();
  window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`);
}

/**
 * Keeps the link while the person signs in and comes back to `/verificar` (which no longer has it in its
 * URL). sessionStorage: this tab only, gone when it closes, and never in `?siguiente=`, which would put the
 * token in the login's URL and in the web host's logs.
 */
export function rememberVerifyLink(link: VerifyLink): void {
  try {
    window.sessionStorage.setItem(VERIFY_KEY, JSON.stringify({ token: link.token, next: safeNext(link.next) }));
  } catch {
    // Private mode: after signing in, the person opens the link of the email again.
  }
}

export function readVerifyLink(): VerifyLink | null {
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(VERIFY_KEY) ?? 'null') as Partial<VerifyLink> | null;
    if (!stored || typeof stored.token !== 'string' || !stored.token) return null;
    return { token: stored.token, next: typeof stored.next === 'string' ? stored.next : null };
  } catch {
    return null;
  }
}

/** Once the link did its job or turned out to be dead. */
export function forgetVerifyLink(): void {
  try {
    window.sessionStorage.removeItem(VERIFY_KEY);
  } catch {
    // Nothing stored.
  }
}

const INVITATION_KEY = 'oc.acceso.invitacion';

/**
 * The invitation link (A5) carries its token in the fragment (`/invitacion#token=…`), like the reset link:
 * a browser never sends it, so it stays out of the web host's logs, and the API gets it in a POST body.
 * Reads it and drops it from the address bar (and from the history).
 */
export function takeInvitationLink(): string | null {
  const hash = window.location.hash.replace(/^#/, '');
  if (!hash) return null;
  const token = new URLSearchParams(hash).get('token');
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
  return token || null;
}

/**
 * Keeps the token while the person signs in and comes back to `/invitacion` (which no longer has it in
 * its URL), and across a reload. sessionStorage: this tab only, gone when it closes, and never in
 * `?siguiente=`, which would put it in the login's URL and in the web host's logs.
 */
export function rememberInvitationToken(token: string): void {
  try {
    window.sessionStorage.setItem(INVITATION_KEY, token);
  } catch {
    // Private mode: after signing in, the person opens the link of the email again.
  }
}

export function readInvitationToken(): string | null {
  try {
    return window.sessionStorage.getItem(INVITATION_KEY) || null;
  } catch {
    return null;
  }
}

/** Once the link is spent or turned out to be dead. */
export function forgetInvitationToken(): void {
  try {
    window.sessionStorage.removeItem(INVITATION_KEY);
  } catch {
    // Nothing stored.
  }
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function emailError(value: string): string | undefined {
  const email = value.trim();
  if (!email) return 'Escribí tu email.';
  if (!EMAIL.test(email)) return 'Revisá el email: no parece completo.';
  return undefined;
}

export const MIN_PASSWORD = 8;

export function newPasswordError(value: string): string | undefined {
  if (!value) return 'Elegí una contraseña.';
  if (value.length < MIN_PASSWORD) return `Usá ${MIN_PASSWORD} caracteres o más.`;
  return undefined;
}

export function nameError(value: string): string | undefined {
  return value.trim() ? undefined : 'Escribí tu nombre.';
}

export const TERMS_ERROR = 'Para crear la cuenta tenés que aceptar los términos.';

/** What to say when a form's request fails for a reason that is not one of its fields. */
export function requestErrorMessage(error: unknown): string {
  if (error instanceof NetworkError) return 'No pudimos conectar. Revisá tu conexión y probá de nuevo.';
  if (error instanceof ApiError && error.status === 429) {
    const seconds = retryAfterSeconds(error, 0);
    return seconds > 0
      ? `Probaste demasiadas veces. Esperá ${formatWait(seconds)} y volvé a intentar.`
      : 'Probaste demasiadas veces. Esperá un momento y volvé a intentar.';
  }
  return 'Algo falló de nuestro lado. Probá de nuevo en un momento.';
}

function formatWait(seconds: number): string {
  if (seconds < 60) return `${seconds} segundos`;
  const minutes = Math.ceil(seconds / 60);
  return minutes === 1 ? '1 minuto' : `${minutes} minutos`;
}
