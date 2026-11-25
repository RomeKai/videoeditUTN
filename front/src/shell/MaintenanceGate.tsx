'use client';

import { usePathname } from 'next/navigation';
import { useEffect, useState, type ReactNode } from 'react';
import { ApiError } from '@/api/errors';
import { useSystemStatus } from '@/api/hooks';
import { onMaintenance } from '@/api/session';
import { MaintenanceScreen } from './SystemPage';

/**
 * Shows the maintenance page (K5) instead of the app while the platform is in
 * maintenance: the status endpoint says so, or the API answers 503 (also when
 * the status endpoint itself is the one that is down). Dev routes stay reachable.
 */
export function MaintenanceGate({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? '';
  const status = useSystemStatus();
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => onMaintenance(setUnavailable), []);
  const down = status.error instanceof ApiError && status.error.status === 503;
  if ((status.data?.maintenance || unavailable || down) && !pathname.startsWith('/dev')) {
    return <MaintenanceScreen startsAt={status.data?.maintenance_window?.starts_at} endsAt={status.data?.maintenance_window?.ends_at} />;
  }
  return <>{children}</>;
}
