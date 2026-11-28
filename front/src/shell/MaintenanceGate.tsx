'use client';

import { useQueryClient } from '@tanstack/react-query';
import { usePathname } from 'next/navigation';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiError } from '@/api/errors';
import { useSystemStatus } from '@/api/hooks';
import { keys } from '@/api/query';
import { onMaintenance } from '@/api/session';
import { MaintenanceScreen } from './SystemPage';

/** How often the status is asked again while the maintenance page is up: coming back must not wait for the minute of the regular poll. */
export const MAINTENANCE_RECHECK_MS = 10_000;

/**
 * Shows the maintenance page (K5) instead of the app while the platform is in
 * maintenance. Only the status endpoint decides that: it says so, or it is itself down with a 503.
 * Dev routes stay reachable.
 *
 * A maintenance 503 somewhere else (the client tells this gate about every answer, `onMaintenance`) makes
 * the gate ask the status at once instead of waiting for the minute of the regular poll, and that is all it
 * does. Until the status confirms it the app stays as it is, with the error of that request where it was
 * made: taking the app away for a 503 the status then denies (a proxy in front of one endpoint) would
 * mount it again a moment later, its screens would ask again, get the same 503, and so on without end.
 * While the page is up the status is asked again every `MAINTENANCE_RECHECK_MS`, if the tab is visible.
 *
 * When the maintenance ends, everything that was asked meanwhile is asked again: what failed with the
 * platform down (the session first of all, which lives above this gate and is not mounted again) would
 * otherwise come back as an error the person has to retry by hand.
 */
export function MaintenanceGate({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? '';
  const status = useSystemStatus();
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => onMaintenance(setUnavailable), []);
  const down = status.error instanceof ApiError && status.error.status === 503;
  const closed = Boolean(status.data?.maintenance || down);
  const { refetch } = status;
  useEffect(() => {
    // One question for however many requests got the 503 together.
    if (unavailable && !closed) void refetch({ cancelRefetch: false });
  }, [closed, refetch, unavailable]);
  useEffect(() => {
    if (!closed) return;
    // Only a tab somebody is looking at keeps asking; one that comes back to the front asks at once.
    // (Not now: the page is up because the status just answered.)
    const ask = () => {
      if (document.visibilityState === 'visible') void refetch();
    };
    const timer = window.setInterval(ask, MAINTENANCE_RECHECK_MS);
    document.addEventListener('visibilitychange', ask);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', ask);
    };
  }, [closed, refetch]);
  const queryClient = useQueryClient();
  const wasClosed = useRef(false);
  useEffect(() => {
    if (wasClosed.current && !closed) {
      // The status itself is the answer that just opened the gate: not asked twice. Nor is what the screens
      // that just came back are already asking (`cancelRefetch: false` leaves a request in flight alone).
      void queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== keys.system[0] }, { cancelRefetch: false });
    }
    wasClosed.current = closed;
  }, [closed, queryClient]);
  if (closed && !pathname.startsWith('/dev')) {
    return <MaintenanceScreen startsAt={status.data?.maintenance_window?.starts_at} endsAt={status.data?.maintenance_window?.ends_at} />;
  }
  return <>{children}</>;
}
