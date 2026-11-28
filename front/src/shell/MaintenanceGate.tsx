'use client';

import { usePathname } from 'next/navigation';
import { useEffect, useState, type ReactNode } from 'react';
import { ApiError } from '@/api/errors';
import { useSystemStatus } from '@/api/hooks';
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
  if (closed && !pathname.startsWith('/dev')) {
    return <MaintenanceScreen startsAt={status.data?.maintenance_window?.starts_at} endsAt={status.data?.maintenance_window?.ends_at} />;
  }
  return <>{children}</>;
}
