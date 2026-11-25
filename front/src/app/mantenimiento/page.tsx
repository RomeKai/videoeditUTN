'use client';

import { useSystemStatus } from '@/api/hooks';
import { MaintenanceScreen } from '@/shell/SystemPage';

/** K5 · Mantenimiento. The app shows it by itself while the platform reports maintenance; this route previews it. */
export default function MaintenancePage() {
  const status = useSystemStatus();
  const window = status.data?.maintenance_window;
  return <MaintenanceScreen startsAt={window?.starts_at} endsAt={window?.ends_at} />;
}
