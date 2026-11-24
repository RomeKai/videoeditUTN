import type { ReactNode } from 'react';
import { PublicShell } from '@/shell/PublicShell';

/** Public screens (A1–A6): no session needed, no app bar. */
export default function PublicLayout({ children }: { children: ReactNode }) {
  return <PublicShell>{children}</PublicShell>;
}
