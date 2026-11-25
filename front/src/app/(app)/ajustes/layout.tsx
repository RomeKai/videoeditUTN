import type { ReactNode } from 'react';
import { SettingsNav } from '@/shell/SectionNav';
import { Page, PageHead } from '@/ui/Page';
import { SideLayout } from '@/ui/Settings';

/** Ajustes del workspace (H1–H3): sections at the left, a horizontal list on phones. Dominio dueño: `ajustes`. */
export default function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <Page>
      <PageHead title="Ajustes" />
      <SideLayout nav={<SettingsNav />}>{children}</SideLayout>
    </Page>
  );
}
