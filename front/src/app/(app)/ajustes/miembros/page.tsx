import type { Metadata } from 'next';
import { MembersScreen } from '@/features/ajustes/MembersScreen';

export const metadata: Metadata = { title: 'Miembros' };

/** H2 · Ajustes › Miembros (con D7 · Invitar miembro). */
export default function Page() {
  return <MembersScreen />;
}
