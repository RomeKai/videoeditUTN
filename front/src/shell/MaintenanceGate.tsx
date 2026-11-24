'use client';

import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { useSystemStatus } from '@/api/hooks';
import { MaintenanceScreen } from './SystemPage';

/** Shows the maintenance page (K5) instead of the app while the platform says it is in maintenance. Dev routes stay reachable. */
export function MaintenanceGate({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? '';
  const status = useSystemStatus();
  if (status.data?.maintenance && !pathname.startsWith('/dev')) {
    return <MaintenanceScreen startsAt={status.data.maintenance_window?.starts_at} endsAt={status.data.maintenance_window?.ends_at} />;
  }
  return <>{children}</>;
}
