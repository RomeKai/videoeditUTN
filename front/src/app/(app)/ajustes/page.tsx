import type { Metadata } from 'next';
import { GeneralSettings } from '@/features/ajustes/GeneralSettings';

export const metadata: Metadata = { title: 'Ajustes' };

/** H1 · Ajustes › General. */
export default function Page() {
  return <GeneralSettings />;
}
