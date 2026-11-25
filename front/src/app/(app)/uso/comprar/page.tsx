import type { Metadata } from 'next';
import { Suspense } from 'react';
import { BuyTokensScreen } from '@/features/uso/BuyTokensScreen';

export const metadata: Metadata = { title: 'Comprar tokens' };

/** G4 · Uso › Comprar tokens. */
export default function Page() {
  return (
    <Suspense>
      <BuyTokensScreen />
    </Suspense>
  );
}
