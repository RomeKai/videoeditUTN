import type { Metadata } from 'next';
import { InvitationScreen } from '@/features/acceso/InvitationScreen';

export const metadata: Metadata = { title: 'Invitación' };

// A5 · Aceptar invitación.
export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <InvitationScreen token={decodeURIComponent(token)} />;
}
