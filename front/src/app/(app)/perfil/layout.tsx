import type { ReactNode } from 'react';
import { AccountShell } from '@/features/ajustes/SectionShell';
import { Page } from '@/ui/Page';

/** Cuenta (I1, I2). Dominio dueño: `ajustes`. */
export default function ProfileLayout({ children }: { children: ReactNode }) {
  return (
    <Page>
      <AccountShell>{children}</AccountShell>
    </Page>
  );
}
