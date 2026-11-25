import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import type { ReactNode } from 'react';

export const metadata: Metadata = { title: 'Herramientas de desarrollo', robots: { index: false } };

/**
 * Dev-only routes: the component kit and the stand-ins for third parties in mock mode.
 * With the mocks off (a build that talks to the real backend) they do not exist.
 */
export default function DevLayout({ children }: { children: ReactNode }) {
  if (process.env.NEXT_PUBLIC_API_MOCKS !== 'true') notFound();
  return <>{children}</>;
}
