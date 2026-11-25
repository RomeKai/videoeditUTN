import type { ReactNode } from 'react';
import { SettingsShell } from '@/features/ajustes/SectionShell';
import { Page } from '@/ui/Page';

/** Ajustes del workspace (H1–H3): sections at the left; a list on phones (M10). Dominio dueño: `ajustes`. */
export default function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <Page>
      <SettingsShell>{children}</SettingsShell>
    </Page>
  );
}
