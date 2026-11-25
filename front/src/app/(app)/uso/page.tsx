import type { Metadata } from 'next';
import { UsageOverview } from '@/features/uso/UsageOverview';

export const metadata: Metadata = { title: 'Uso y facturación' };

/** G1 · Uso › Resumen (R1 for Editor and Lector, M9 on phones). */
export default function Page() {
  return <UsageOverview />;
}
