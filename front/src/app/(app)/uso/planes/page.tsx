import type { Metadata } from 'next';
import { PlansScreen } from '@/features/uso/PlansScreen';

export const metadata: Metadata = { title: 'Planes' };

/** G3 · Uso › Planes. */
export default function Page() {
  return <PlansScreen />;
}
