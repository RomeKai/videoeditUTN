import type { Metadata } from 'next';
import { one, type SearchParams } from '@/features/acceso/params';
import { RecoverScreen } from '@/features/acceso/RecoverScreen';

export const metadata: Metadata = { title: 'Recuperar contraseña' };

// A4 · Recuperar contraseña. Without `?token=` it asks for the link (step 1); with it, for the new password (step 2).
export default async function Page({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const token = one(params.token);
  // The key gives each step its own state when the link is opened in the tab that asked for it.
  return <RecoverScreen key={token ?? 'pedir'} token={token} email={one(params.email)} />;
}
