import type { ReactNode } from 'react';
import { ProfileNav } from '@/shell/SectionNav';
import { Page, PageHead } from '@/ui/Page';
import { SideLayout } from '@/ui/Settings';

/** Cuenta (I1, I2). Dominio dueño: `ajustes`. */
export default function ProfileLayout({ children }: { children: ReactNode }) {
  return (
    <Page>
      <PageHead title="Tu cuenta" />
      <SideLayout nav={<ProfileNav />}>{children}</SideLayout>
    </Page>
  );
}
