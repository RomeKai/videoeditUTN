import type { Metadata } from 'next';
import { InvitationPage } from '@/features/acceso/InvitationScreen';

export const metadata: Metadata = { title: 'Invitación' };

// A5 · Aceptar invitación. The link of the email brings its token in the fragment (`/invitacion#token=…`),
// which the browser never sends: the screen reads it and takes it out of the address bar. No token in the path,
// where the web host would log it.
export default function Page() {
  return <InvitationPage />;
}
