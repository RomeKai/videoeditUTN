import type { ReactNode } from 'react';
import { AppShell } from '@/shell/AppShell';
import { RequireSession } from '@/shell/RequireSession';

/** Everything behind the session: route protection and the app shell (top bar of two rows). */
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <RequireSession>
      <AppShell>{children}</AppShell>
    </RequireSession>
  );
}
