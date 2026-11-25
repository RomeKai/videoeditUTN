import type { Metadata } from 'next';
import { ProfileScreen } from '@/features/ajustes/ProfileScreen';

export const metadata: Metadata = { title: 'Tu cuenta' };

/** I1 · Perfil. */
export default function Page() {
  return <ProfileScreen />;
}
