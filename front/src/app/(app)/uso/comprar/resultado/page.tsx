import type { Metadata } from 'next';
import { Suspense } from 'react';
import { PurchaseResultScreen } from '@/features/uso/PurchaseResultScreen';

export const metadata: Metadata = { title: 'Resultado de la compra' };

/** G5 · Uso › Resultado de la compra (`?compra=<id>`). */
export default function Page() {
  return (
    <Suspense>
      <PurchaseResultScreen />
    </Suspense>
  );
}
