import type { Metadata } from 'next';
import { PricingScreen } from '@/features/uso/PricingScreen';

export const metadata: Metadata = { title: 'Precios' };

/** A6 · Precios (pública). */
export default function Page() {
  return <PricingScreen />;
}
