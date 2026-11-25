import type { Metadata } from 'next';
import { NetworksScreen } from '@/features/ajustes/NetworksScreen';

export const metadata: Metadata = { title: 'Redes sociales' };

/** H3 · Ajustes › Redes sociales. */
export default function Page() {
  return <NetworksScreen />;
}
