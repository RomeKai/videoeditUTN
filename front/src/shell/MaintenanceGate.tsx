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
 * maintenance: the status endpoint says so, or the API answers a maintenance 503
 * (also when the status endpoint itself is the one that is down). Dev routes stay reachable.
 *
 * A 503 somewhere else is checked against the status endpoint at once, and then every
 * `MAINTENANCE_RECHECK_MS` while the page is up and the tab is visible: whatever that endpoint answers decides
 * (the client tells this gate about every answer, `onMaintenance`).
 *
 * When the maintenance ends, everything that was asked meanwhile is asked again: what failed with the
 * platform down (the session first of all, which lives above this gate and is not mounted again) would
 * otherwise come back as an error the person has to retry by hand.
 * Only after a maintenance the status endpoint confirmed. A lone 503 the status denies is that request's
 * own failure: asking it again would bring the 503 back, the page with it, and so on without end.
 */
export function MaintenanceGate({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? '';
  const status = useSystemStatus();
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => onMaintenance(setUnavailable), []);
  const down = status.error instanceof ApiError && status.error.status === 503;
  const closed = Boolean(status.data?.maintenance || unavailable || down);
  const { refetch } = status;
  useEffect(() => {
    if (!closed) return;
    // Only a tab somebody is looking at keeps asking; one that comes back to the front asks at once.
    const ask = () => {
      if (document.visibilityState === 'visible') void refetch();
    };
    ask();
    const timer = window.setInterval(ask, MAINTENANCE_RECHECK_MS);
    document.addEventListener('visibilitychange', ask);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', ask);
    };
  }, [closed, refetch]);
  const queryClient = useQueryClient();
  // Whether the status endpoint itself said so while the page was up: only then was it a maintenance.
  const confirmed = Boolean(status.data?.maintenance || down);
  const wasMaintenance = useRef(false);
  useEffect(() => {
    if (closed) {
      if (confirmed) wasMaintenance.current = true;
      return;
    }
    if (!wasMaintenance.current) return;
    wasMaintenance.current = false;
    // The status itself is the answer that just opened the gate: not asked twice.
    void queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== keys.system[0] });
  }, [closed, confirmed, queryClient]);
  if (closed && !pathname.startsWith('/dev')) {
    return <MaintenanceScreen startsAt={status.data?.maintenance_window?.starts_at} endsAt={status.data?.maintenance_window?.ends_at} />;
  }
  return <>{children}</>;
}
