'use client';

import { useCallback, useEffect, useState } from 'react';
import { ApiError, NetworkError } from '@/api/errors';

/** Where the app sends a signed-in user that asked for nothing in particular. */
export const HOME = '/proyectos';

/**
 * The page the user was going to before being sent to sign in (`?siguiente=`).
 * Only paths inside the app are accepted: an absolute or protocol-relative URL
 * would let a link send people to another site after signing in.
 */
export function safeNext(next: string | null | undefined): string | null {
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
  if (/^\/(login|registro|verificar|recuperar)(\/|$)/.test(url.pathname)) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}

const PARSE_ORIGIN = 'http://app.invalid';
const NEXT_KEY = 'oc.acceso.siguiente';

/**
 * Remembers where the user was going while the email gets verified (A1 → A3 → link of the email).
 * The link usually opens in another tab, hence localStorage.
 */
export function rememberNext(next: string | null | undefined): void {
  const safe = safeNext(next);
  try {
    if (safe) window.localStorage.setItem(NEXT_KEY, safe);
  } catch {
    // Private mode: the verification ends in the welcome, as usual.
  }
}

/** Reads (and forgets) the page remembered by `rememberNext`. */
export function takeNext(): string | null {
  try {
    const stored = window.localStorage.getItem(NEXT_KEY);
    window.localStorage.removeItem(NEXT_KEY);
    return safeNext(stored);
  } catch {
    return null;
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

/** Seconds the API asks to wait, from a 429 ("… in 42 seconds" / "… en 42 segundos"). */
export function retryAfterSeconds(error: unknown, fallback = 60): number {
  if (!(error instanceof ApiError) || error.status !== 429) return fallback;
  const match = /(\d+)\s*(?:seconds?|segundos?|s\b)/i.exec(error.message);
  const seconds = match ? Number(match[1]) : NaN;
  return Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, 3600) : fallback;
}

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

/** 42 → "0:42". */
export function formatCountdown(seconds: number): string {
  const safe = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`;
}

const COOLDOWN_PREFIX = 'oc.acceso.espera.';

function readDeadline(key: string): number {
  try {
    const value = Number(window.sessionStorage.getItem(COOLDOWN_PREFIX + key));
    return Number.isFinite(value) ? value : 0;
  } catch {
    return 0;
  }
}

/** Starts the wait before asking for another email, from outside the screen that shows it (A2 → A3). */
export function startCooldown(key: string, seconds = 60): void {
  try {
    window.sessionStorage.setItem(COOLDOWN_PREFIX + key, String(Date.now() + seconds * 1000));
  } catch {
    // Private mode: the screen simply starts without a wait.
  }
}

/**
 * The 60 s wait between two emails (map A3): seconds left and how to start it.
 * The deadline lives in sessionStorage so a reload does not reset the wait.
 */
export function useCooldown(key: string): { secondsLeft: number; start: (seconds?: number) => void } {
  const [deadline, setDeadline] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    setDeadline(readDeadline(key));
    setNow(Date.now());
  }, [key]);

  const waiting = deadline > now;
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [waiting]);

  const start = useCallback(
    (seconds = 60) => {
      startCooldown(key, seconds);
      setDeadline(Date.now() + seconds * 1000);
      setNow(Date.now());
    },
    [key],
  );

  return { secondsLeft: waiting ? Math.ceil((deadline - now) / 1000) : 0, start };
}
