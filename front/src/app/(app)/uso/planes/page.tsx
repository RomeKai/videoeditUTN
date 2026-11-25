import type { Metadata } from 'next';
import { Suspense } from 'react';
import { PlansScreen } from '@/features/uso/PlansScreen';

export const metadata: Metadata = { title: 'Planes' };

/** G3 · Uso › Planes (`?compra=<id>` al volver del checkout de un plan). */
export default function Page() {
  return (
    <Suspense>
      <PlansScreen />
    </Suspense>
  );
}
