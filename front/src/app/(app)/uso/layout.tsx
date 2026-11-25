import type { ReactNode } from 'react';
import { UsageHeader } from '@/features/uso/UsageHeader';
import { Page } from '@/ui/Page';

/** Uso y facturación (G1–G5): title and sub-tabs; each tab is a route. Dominio dueño: `uso`. */
export default function UsageLayout({ children }: { children: ReactNode }) {
  return (
    <Page>
      <UsageHeader />
      {children}
    </Page>
  );
}
