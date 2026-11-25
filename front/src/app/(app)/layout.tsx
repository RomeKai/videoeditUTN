import type { ReactNode } from 'react';
import { PublishPanelMount } from '@/features/publicar/PublishPanelMount';
import { AppShell } from '@/shell/AppShell';
import { RequireSession } from '@/shell/RequireSession';

/**
 * Everything behind the session: route protection and the app shell (top bar of two rows).
 * The publish panel (E1) has no route: it opens over any of these screens with `?publicar=<clipId>[,…]`.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <RequireSession>
      <AppShell>{children}</AppShell>
      <PublishPanelMount />
    </RequireSession>
  );
}
