import type { ReactNode } from 'react';
import { USAGE_TABS } from '@/shell/nav';
import { Page, PageHead } from '@/ui/Page';
import { NavTabs } from '@/ui/Tabs';

/** Uso y facturación (G1–G5): title and sub-tabs; each tab is a route. Dominio dueño: `uso`. */
export default function UsageLayout({ children }: { children: ReactNode }) {
  return (
    <Page>
      <PageHead title="Uso y facturación" />
      <NavTabs ariaLabel="Uso y facturación" items={USAGE_TABS} />
      {children}
    </Page>
  );
}
