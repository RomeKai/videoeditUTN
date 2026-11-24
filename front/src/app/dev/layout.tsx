import type { Metadata } from 'next';
import type { ReactNode } from 'react';

export const metadata: Metadata = { title: 'Herramientas de desarrollo', robots: { index: false } };

/** Dev-only routes: the component kit and the stand-ins for third parties in mock mode. */
export default function DevLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
