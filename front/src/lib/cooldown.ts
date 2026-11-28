'use client';

import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '@/api/errors';

/**
 * The wait between two emails of the same kind (verification, password reset): 60 s, or what a 429
 * says. Shared by the access screens (A3, A4) and by whatever else offers to send one again (D6).
 */

/** Seconds the API asks to wait, from a 429 ("… in 42 seconds" / "… en 42 segundos" / "… en 12 minutos"). */
export function retryAfterSeconds(error: unknown, fallback = 60): number {
  if (!(error instanceof ApiError) || error.status !== 429) return fallback;
  const match = /(\d+)\s*(?:(seconds?|segundos?|s\b)|minutes?|minutos?)/i.exec(error.message);
  const seconds = match ? Number(match[1]) * (match[2] ? 1 : 60) : NaN;
  return Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, 3600) : fallback;
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
