import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import type { ReactNode } from 'react';
import { API_MOCKS } from '@/api/config';

export const metadata: Metadata = { title: 'Herramientas de desarrollo', robots: { index: false } };

/**
 * Dev-only routes: the component kit and the stand-ins for third parties in mock mode.
 * With the mocks off (mode `real`: a build that talks to the backend for everything) they do not exist.
 */
export default function DevLayout({ children }: { children: ReactNode }) {
  if (!API_MOCKS) notFound();
  return <>{children}</>;
}
