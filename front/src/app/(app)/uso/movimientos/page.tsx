import type { Metadata } from 'next';
import { MovementsScreen } from '@/features/uso/MovementsScreen';

export const metadata: Metadata = { title: 'Movimientos' };

/** G2 · Uso › Movimientos. */
export default function Page() {
  return <MovementsScreen />;
}
