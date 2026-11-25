import type { Metadata } from 'next';
import { SummaryScreen } from '@/features/proyecto/SummaryScreen';

export const metadata: Metadata = { title: 'Proyecto' };

// D1 · Proyecto › Resumen. The header and the tabs come from the layout.
export default function Page() {
  return <SummaryScreen />;
}
